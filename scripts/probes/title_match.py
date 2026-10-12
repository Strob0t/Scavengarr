"""Title matching per plugin: the queries sent, the raw titles, the verdicts.

``poetry run python -P scripts/probes/title_match.py [--json FILE]
[--plugins a,b] ID [ID ...]`` (ids as Stremio paths: ``movie/tt0133093``,
``series/tt5753856:1:1``; ``-P``: the sibling probe ``redis.py`` would shadow
the package; ``xvfb-run -a`` without a display: the browser fallback runs
headful) starts the app's composition (plugins, browser fallback, title
lookup) and runs every stream plugin's search directly, once per query its
language group gets (``application/stremio/queries.py``), with the request's
category, season and episode, under the plugin timeout and the runner's
plugin concurrency. Every raw result gets the title matcher's score against
the group's reference (``score_title``, the configured weights), its
verdict at the configured threshold and the rule that decided it (``year``,
``imdb``, ``category``, else the text score); the reference line names the
IMDb id and the kind (animation or not) the catalog gave it. The episode
filter is not applied.

Mirror groups are not collapsed: every member is asked. Read-only: it
searches the sites and writes nothing but a temporary cache.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, cast

import structlog

from scavengarr.application.stremio.queries import (
    build_lang_group_queries,
    build_multi_lang_reference,
)
from scavengarr.application.stremio.title_resolution import TitleResolver
from scavengarr.domain.entities.stremio import StremioStreamRequest, TitleMatchInfo
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.config.load import load_config
from scavengarr.infrastructure.plugins.constants import search_max_results
from scavengarr.infrastructure.stremio.title_matcher import (
    filter_by_title_match,
    score_title,
)
from scavengarr.interfaces.app import create_app
from scavengarr.interfaces.app_state import AppState

REPO = Path(__file__).resolve().parents[2]


def parse_id(sid: str) -> tuple[StremioStreamRequest, int]:
    """``series/tt5753856:1:1`` / ``movie/tt0133093`` -> request, category."""
    kind, _, rest = sid.partition("/")
    if kind == "movie":
        return StremioStreamRequest(imdb_id=rest, content_type="movie"), 2000
    if kind != "series":
        raise ValueError(f"not a Stremio id: {sid}")
    imdb_id, season, episode = rest.split(":")
    request = StremioStreamRequest(
        imdb_id=imdb_id,
        content_type="series",
        season=int(season),
        episode=int(episode),
    )
    return request, 5000


@dataclass
class Hit:
    """One raw result and the matcher's verdict."""

    title: str
    release_name: str | None
    score: float
    kept: bool
    reason: str  # the rule that decided: score, year, imdb, category


@dataclass
class PluginRun:
    """One plugin's searches for one request."""

    plugin: str
    languages: list[str]
    reference: str
    queries: dict[str, int] = field(default_factory=dict)  # query -> raw count
    failures: dict[str, str] = field(default_factory=dict)  # query -> error
    hits: list[Hit] = field(default_factory=list)


def describe(ref: TitleMatchInfo) -> str:
    """``Haus des Geldes (2017; alt: Money Heist; tt6468322; not animation)``."""
    extra = [str(ref.year)] if ref.year else []
    if ref.alt_titles:
        extra.append("alt: " + ", ".join(ref.alt_titles))
    if ref.imdb_id:
        extra.append(ref.imdb_id)
    kinds = {True: "animation", False: "not animation", None: "kind unknown"}
    extra.append(kinds[ref.animation])
    return f"{ref.title} ({'; '.join(extra)})" if extra else ref.title


def verdicts(
    results: Iterable[SearchResult],
    ref: TitleMatchInfo,
    threshold: float,
    weights: dict[str, Any],
) -> list[Hit]:
    """Each result's score, whether it reaches *threshold* and the rule that
    decided, best first."""
    hits = []
    for r in results:
        verdict = score_title(r, ref, **weights)
        hits.append(
            Hit(
                r.title,
                r.release_name,
                round(verdict.score, 3),
                verdict.score >= threshold,
                verdict.reason,
            )
        )
    return sorted(hits, key=lambda h: -h.score)


def render(sid: str, runs: list[PluginRun], threshold: float) -> str:
    """One request's table (plugins with results or failures) and its hits."""
    refs = sorted({run.reference for run in runs})
    lines = [
        f"### `{sid}`",
        "",
        "Reference: " + " / ".join(refs) + f"; threshold {threshold}.",
        "",
        "| plugin | languages | queries (raw results) | raw | kept | failed |",
        "|---|---|---|---:|---:|---|",
    ]
    quiet: list[str] = []
    for run in sorted(runs, key=lambda r: r.plugin):
        if not run.hits and not run.failures:
            quiet.append(run.plugin)
            continue
        queries = ", ".join(f"{q!r} ({n})" for q, n in run.queries.items())
        failed = ", ".join(f"{q!r}: {e}" for q, e in run.failures.items()) or "–"
        lines.append(
            f"| {run.plugin} | {', '.join(run.languages)} | {queries or '–'} "
            f"| {len(run.hits)} | {sum(h.kept for h in run.hits)} | {failed} |"
        )
    if quiet:
        lines += ["", "No results: " + ", ".join(quiet) + "."]
    found = [(run.plugin, hit) for run in runs for hit in run.hits]
    if found:
        lines += ["", "Results (score, verdict):", ""]
        for plugin, hit in sorted(found, key=lambda x: (x[0], -x[1].score)):
            release = (
                f" [{hit.release_name}]"
                if hit.release_name and hit.release_name != hit.title
                else ""
            )
            verdict = "kept" if hit.kept else "dropped"
            if hit.reason != "score":
                verdict += f" by {hit.reason}"
            lines.append(
                f"- {plugin}: {hit.title!r}{release} {hit.score:.2f} {verdict}"
            )
    return "\n".join(lines) + "\n"


async def _search(
    plugin: Any,
    query: str,
    category: int,
    req: StremioStreamRequest,
    timeout: float,
) -> list[SearchResult]:
    search = getattr(plugin, "isolated_search", None) or plugin.search
    return await asyncio.wait_for(
        search(query, category, season=req.season, episode=req.episode), timeout
    )


async def probe(state: AppState, sid: str, only: set[str] | None) -> list[PluginRun]:
    config = state.config.stremio
    assert state.tmdb_client is not None
    titles = TitleResolver(
        tmdb=state.tmdb_client,
        plugins=state.plugins,
        filter_fn=filter_by_title_match,
        config=config,
        series_meta=state.series_meta,
    )
    weights: dict[str, Any] = {
        "year_bonus": config.title_year_bonus,
        "sequel_penalty": config.title_sequel_penalty,
        "extra_words_penalty": config.title_extra_words_penalty,
        "year_tolerance_movie": config.title_year_tolerance_movie,
        "year_tolerance_series": config.title_year_tolerance_series,
    }
    names = sorted(
        set(state.plugins.get_by_provides("stream"))
        | set(state.plugins.get_by_provides("both"))
    )
    if only:
        names = [n for n in names if n in only]
    slots = asyncio.Semaphore(config.max_concurrent_plugins)
    search_max_results.set(config.max_results_per_plugin)
    req, category = parse_id(sid)
    infos, _meta = await titles.title_infos(req, titles.languages(names))

    async def one_plugin(
        name: str, langs: list[str], queries: list[str], ref: TitleMatchInfo
    ) -> PluginRun:
        run = PluginRun(plugin=name, languages=langs, reference=describe(ref))
        plugin = state.plugins.get(name)
        for query in queries:
            async with slots:
                try:
                    results = await _search(
                        plugin, query, category, req, config.plugin_timeout_seconds
                    )
                except TimeoutError:
                    run.failures[query] = "timeout"
                    continue
                except Exception as exc:  # a site's failure is a finding
                    run.failures[query] = type(exc).__name__
                    continue
            run.queries[query] = len(results)
            run.hits += verdicts(results, ref, config.title_match_threshold, weights)
        return run

    tasks = []
    for lang_key, group in titles.language_groups(names).items():
        langs = list(lang_key)
        ref = build_multi_lang_reference(infos, langs)
        queries = build_lang_group_queries(infos, langs)
        if ref is None or not queries:
            print(f"{sid}: no title for {langs}", file=sys.stderr)
            continue
        tasks += [one_plugin(n, langs, queries, ref) for n in group]
    return list(await asyncio.gather(*tasks))


async def run(sids: list[str], only: set[str] | None) -> tuple[list[Any], float]:
    with tempfile.TemporaryDirectory() as tmp:
        config = load_config(
            cli_overrides={
                "plugin_dir": REPO / "plugins",
                "cache_dir": Path(tmp) / "cache",
                # no background plugin searches next to the probe
                "scoring": {"enabled": False},
            }
        )
        app = create_app(config)
        async with app.router.lifespan_context(app):
            state = cast(AppState, app.state)
            runs = [await probe(state, sid, only) for sid in sids]
            return runs, state.config.stremio.title_match_threshold


def main(argv: list[str] | None = None) -> int:
    # the plugins log every request at info level
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING)
    )
    parser = argparse.ArgumentParser(description="Title matching per plugin")
    parser.add_argument("ids", nargs="+", help="movie/ttID or series/ttID:S:E")
    parser.add_argument("--json", type=Path, help="also write the runs here")
    parser.add_argument("--plugins", help="comma-separated plugin names")
    args = parser.parse_args(argv)
    only = set(args.plugins.split(",")) if args.plugins else None
    # the app logs to stdout; the tables are the probe's only output
    with contextlib.redirect_stdout(sys.stderr):
        runs, threshold = asyncio.run(run(args.ids, only))
    if args.json:
        out = {
            sid: [asdict(r) for r in sid_runs] for sid, sid_runs in zip(args.ids, runs)
        }
        args.json.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print("\n".join(render(s, r, threshold) for s, r in zip(args.ids, runs)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
