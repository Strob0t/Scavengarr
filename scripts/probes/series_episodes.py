"""Series episodes per plugin: what the episode filter keeps, and its leaks.

``poetry run python -P scripts/probes/series_episodes.py [--json FILE] [ID ...]``
(``-P``: the sibling probe ``redis.py`` would shadow the package;
``xvfb-run -a`` without a display: the browser fallback runs headful)
starts the app's composition (plugins, browser fallback, title lookup)
and runs every stream plugin's search directly, with the queries, the
category, season and episode of a Stremio request, under the plugin
timeout and the runner's plugin concurrency. Every result that matches
the title is classified: season and episode from its metadata or the
release name (guessit), the episode filter's outcome (kept, narrowed,
dropped), whether it shows the requested episode, and leaks: kept results
whose title or link labels name another episode.

Without ids: the series of ``docs/plans/round-titles.txt`` plus Severance
S01E05 and S02E05. Mirror groups are not collapsed: every member is asked.
Read-only: it searches the sites and writes nothing but a temporary cache.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import re
import sys
import tempfile
import time
from collections import Counter
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
from scavengarr.domain.entities.stremio import StremioStreamRequest
from scavengarr.domain.plugins.base import SearchResult, result_key
from scavengarr.infrastructure.config.load import load_config
from scavengarr.infrastructure.plugins.constants import search_max_results
from scavengarr.infrastructure.stremio.episode_filter import (
    filter_by_episode,
    parse_episode_from_label,
)
from scavengarr.infrastructure.stremio.release_guess import guess_release
from scavengarr.infrastructure.stremio.title_matcher import filter_by_title_match
from scavengarr.interfaces.app import create_app
from scavengarr.interfaces.app_state import AppState

REPO = Path(__file__).resolve().parents[2]
ROUND_TITLES = REPO / "docs" / "plans" / "round-titles.txt"
EXTRA_IDS = ("series/tt11280740:1:5", "series/tt11280740:2:5")  # Severance
SERIES = 5000

# Episode words the filter's label pattern (1x5, S01E05) does not read
_LOOSE_EPISODE_RE = re.compile(
    r"\b(?:episode|folge|ep\.?)\s*(\d{1,4})\b|\bE(\d{1,4})\b", re.IGNORECASE
)


def series_ids(lines: Iterable[str]) -> list[str]:
    """The ``series/`` ids of a round-titles file, in file order."""
    ids: list[str] = []
    for line in lines:
        sid = line.split("#", 1)[0].strip()
        if sid.startswith("series/") and sid not in ids:
            ids.append(sid)
    return ids


def parse_id(sid: str) -> StremioStreamRequest:
    """``series/tt0903747:1:2`` -> the Stremio request."""
    imdb_id, season, episode = sid.removeprefix("series/").split(":")
    return StremioStreamRequest(
        imdb_id=imdb_id,
        content_type="series",
        season=int(season),
        episode=int(episode),
    )


def _numbers(value: object) -> set[int]:
    """An int, or a list for multi-season/-episode releases."""
    items = value if isinstance(value, list) else [value]
    return {v for v in items if isinstance(v, int)}


def result_numbers(result: SearchResult) -> tuple[set[int], set[int]]:
    """(seasons, episodes): each from the plugin's metadata ints, else from
    the release name (sto stores the season as a string)."""
    meta = result.metadata
    seasons, episodes = _numbers(meta.get("season")), _numbers(meta.get("episode"))
    if not (seasons and episodes):
        info = guess_release(result.title)
        seasons = seasons or _numbers(info.get("season"))
        episodes = episodes or _numbers(info.get("episode"))
    return seasons, episodes


def label_numbers(label: str) -> tuple[int | None, int | None]:
    """(season, episode) a link label names: the filter's pattern, then
    episode words such as ``Folge 5`` or ``E05``."""
    season, episode = parse_episode_from_label(label)
    if episode is not None:
        return season, episode
    m = _LOOSE_EPISODE_RE.search(label)
    if m is None:
        return None, None
    return None, int(m.group(1) or m.group(2))


def numbering(result: SearchResult) -> str:
    """``season_episode``, ``episode``, ``season`` or ``none``."""
    seasons, episodes = result_numbers(result)
    if episodes:
        return "season_episode" if seasons else "episode"
    return "season" if seasons else "none"


def filter_outcome(
    result: SearchResult, season: int, episode: int
) -> tuple[str, SearchResult | None]:
    """The episode filter's outcome for one result, and what it keeps."""
    kept = filter_by_episode([result], season, episode)
    if not kept:
        return "dropped", None
    return ("kept" if kept[0] is result else "narrowed"), kept[0]


def _names(seasons: set[int], episodes: set[int], season: int, episode: int) -> str:
    """``right``, ``wrong`` or ``unknown`` for the requested episode."""
    if not episodes:
        return "unknown"
    if episode in episodes and (not seasons or season in seasons):
        return "right"
    return "wrong"


def label_verdicts(result: SearchResult, season: int, episode: int) -> list[str]:
    """``right``/``wrong``/``unknown`` per link label."""
    verdicts: list[str] = []
    for link in result.download_links or ():
        l_season, l_episode = label_numbers(link.get("label", ""))
        seasons = set() if l_season is None else {l_season}
        episodes = set() if l_episode is None else {l_episode}
        verdicts.append(_names(seasons, episodes, season, episode))
    return verdicts


def shows_episode(result: SearchResult, season: int, episode: int) -> bool:
    """The title or a link label names the requested episode."""
    seasons, episodes = result_numbers(result)
    if _names(seasons, episodes, season, episode) == "right":
        return True
    return "right" in label_verdicts(result, season, episode)


def leak_reasons(result: SearchResult, season: int, episode: int) -> list[str]:
    """Why a kept result shows another episode: its title, its labels."""
    reasons: list[str] = []
    seasons, episodes = result_numbers(result)
    if _names(seasons, episodes, season, episode) == "wrong":
        reasons.append(f"title {result.title!r}")
    for link in result.download_links or ():
        label = link.get("label", "")
        l_season, l_episode = label_numbers(label)
        if l_episode is None:
            continue
        l_seasons = set() if l_season is None else {l_season}
        if _names(l_seasons, {l_episode}, season, episode) == "wrong":
            reasons.append(f"label {label!r}")
    return reasons


@dataclass
class Record:
    """One title-matching result of one plugin for one request."""

    request: str
    plugin: str
    title: str
    numbering: str
    outcome: str
    links: int
    kept_links: int
    right: bool
    leaks: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)


def classify(
    sid: str, plugin: str, result: SearchResult, season: int, episode: int
) -> Record:
    outcome, kept = filter_outcome(result, season, episode)
    return Record(
        request=sid,
        plugin=plugin,
        title=result.title,
        numbering=numbering(result),
        outcome=outcome,
        links=len(result.download_links or ()),
        kept_links=len(kept.download_links or ()) if kept else 0,
        right=kept is not None and shows_episode(kept, season, episode),
        leaks=leak_reasons(kept, season, episode) if kept else [],
        labels=[link.get("label", "") for link in result.download_links or ()],
    )


COLUMNS = (
    "results",
    "season_episode",
    "episode",
    "season",
    "none",
    "kept",
    "narrowed",
    "dropped",
    "right",
    "leaks",
)


def tally(records: Iterable[Record]) -> dict[str, Counter[str]]:
    """Per plugin: the counts of the table's columns."""
    table: dict[str, Counter[str]] = {}
    for rec in records:
        row = table.setdefault(rec.plugin, Counter())
        row["results"] += 1
        row[rec.numbering] += 1
        row[rec.outcome] += 1
        row["right"] += rec.right
        row["leaks"] += bool(rec.leaks)
    return table


def render(
    records: list[Record], failures: dict[str, Counter[str]], plugins: list[str]
) -> str:
    """The per-plugin table, totals, and every leak."""
    table = tally(records)
    head = ("plugin", *COLUMNS, "failed")
    lines = [
        "| " + " | ".join(head) + " |",
        "|" + "|".join("---" if c == "plugin" else "---:" for c in head) + "|",
    ]
    total: Counter[str] = Counter()
    for name in plugins:
        row = table.get(name, Counter())
        failed = failures.get(name, Counter())
        total.update(row)
        total["failed"] += sum(failed.values())
        cells = [str(row[c]) for c in COLUMNS]
        fail = ", ".join(f"{k} {v}" for k, v in sorted(failed.items())) or "0"
        lines.append(f"| {name} | " + " | ".join(cells) + f" | {fail} |")
    cells = [str(total[c]) for c in COLUMNS]
    lines.append("| **total** | " + " | ".join(cells) + f" | {total['failed']} |")
    leaks = [r for r in records if r.leaks]
    if leaks:
        lines += ["", "Leaks:", ""]
        lines += [
            f"- {r.plugin}, {r.request}: {r.title!r} ({'; '.join(r.leaks[:3])})"
            for r in leaks
        ]
    return "\n".join(lines)


async def _search(
    plugin: Any,
    query: str,
    req: StremioStreamRequest,
    timeout: float,
) -> list[SearchResult]:
    search = getattr(plugin, "isolated_search", None) or plugin.search
    return await asyncio.wait_for(
        search(query, SERIES, season=req.season, episode=req.episode), timeout
    )


async def probe(
    state: AppState, sids: list[str], only: set[str] | None
) -> tuple[list[Record], dict[str, Counter[str]], list[str]]:
    config = state.config.stremio
    assert state.tmdb_client is not None
    titles = TitleResolver(
        tmdb=state.tmdb_client,
        plugins=state.plugins,
        filter_fn=filter_by_title_match,
        config=config,
        series_meta=state.series_meta,
    )
    names = sorted(
        set(state.plugins.get_by_provides("stream"))
        | set(state.plugins.get_by_provides("both"))
    )
    if only:
        names = [n for n in names if n in only]
    slots = asyncio.Semaphore(config.max_concurrent_plugins)
    search_max_results.set(config.max_results_per_plugin)
    records: list[Record] = []
    failures: dict[str, Counter[str]] = {}

    async def one_plugin(
        sid: str, req: StremioStreamRequest, name: str, queries: list[str], ref: Any
    ) -> None:
        plugin = state.plugins.get(name)
        found: dict[Any, SearchResult] = {}
        for query in queries:
            async with slots:
                try:
                    results = await _search(
                        plugin, query, req, config.plugin_timeout_seconds
                    )
                except TimeoutError:
                    failures.setdefault(name, Counter())["timeout"] += 1
                    continue
                except Exception as exc:  # a site's failure is a finding
                    failures.setdefault(name, Counter())[type(exc).__name__] += 1
                    continue
            for result in results:
                found.setdefault(result_key(result), result)
        matched = await titles.matching(list(found.values()), ref)
        assert req.season is not None and req.episode is not None
        records.extend(classify(sid, name, r, req.season, req.episode) for r in matched)

    for sid in sids:
        req = parse_id(sid)
        started = time.monotonic()
        infos, _meta = await titles.title_infos(req, titles.languages(names))
        tasks = []
        for langs, group in titles.language_groups(names).items():
            ref = build_multi_lang_reference(infos, list(langs))
            queries = build_lang_group_queries(infos, list(langs))
            if ref is None or not queries:
                continue
            tasks += [one_plugin(sid, req, n, queries, ref) for n in group]
        await asyncio.gather(*tasks)
        print(
            f"{sid}: {sum(r.request == sid for r in records)} results,"
            f" {time.monotonic() - started:.0f} s",
            file=sys.stderr,
        )
    return records, failures, names


async def run(sids: list[str], only: set[str] | None) -> tuple[Any, ...]:
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
            return await probe(cast(AppState, app.state), sids, only)


def main(argv: list[str] | None = None) -> int:
    # the plugins log every request at info level
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING)
    )
    parser = argparse.ArgumentParser(description="Series episodes per plugin")
    parser.add_argument("ids", nargs="*", help="series/ttID:S:E (default: see doc)")
    parser.add_argument("--json", type=Path, help="also write the records here")
    parser.add_argument("--plugins", help="comma-separated plugin names")
    args = parser.parse_args(argv)
    sids = args.ids or [
        *series_ids(ROUND_TITLES.read_text().splitlines()),
        *EXTRA_IDS,
    ]
    only = set(args.plugins.split(",")) if args.plugins else None
    # the app logs to stdout; the table or JSON is the probe's only output
    with contextlib.redirect_stdout(sys.stderr):
        records, failures, names = asyncio.run(run(sids, only))
    if args.json:
        out = {
            "requests": sids,
            "plugins": {n: dict(c) for n, c in tally(records).items()},
            "failures": {n: dict(c) for n, c in failures.items()},
            "records": [asdict(r) for r in records],
        }
        args.json.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(render(records, failures, names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
