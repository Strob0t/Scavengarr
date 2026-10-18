"""Hoster page URLs for the live resolver tests (tests/live/test_resolver_live.py).

Searches the titles of ``.cache/live/round-titles.txt`` (local) through the plugin
registry the way ``title_match.py`` does (the stream plugins, each title's
queries per language group, the plugin timeout, the app's concurrency), maps
every result link to its resolver with the hoster resolver registry
(``canonical_hoster``: a host claim, then the second-level domain) and keeps
the first link per resolver. Writes ``.cache/live/resolver-urls.json``
(``{resolver name: URL}``; ``.cache/`` is gitignored) and prints resolver
names and counts only: a hoster page URL is a link to a third-party page and
stays out of the output.

``--avoid <file>`` takes an earlier output: a link it names is not taken, so
each resolver keeps its next link (a second link for a live case that failed
on its first; write it to a file of its own with ``--out``, the live test
reads ``resolver-urls.json`` only).

Usage (dev container):
    PYTHONPATH=src xvfb-run -a poetry run python scripts/probes/resolver_urls.py
    ... --plugins kinoger,sto --titles .cache/live/round-titles.txt --out <file>
    ... --avoid .cache/live/resolver-urls.json --out .cache/live/resolver-urls-2.json
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import structlog

from scavengarr.application.stremio.queries import build_lang_group_queries
from scavengarr.application.stremio.title_resolution import TitleResolver
from scavengarr.domain.plugins.base import SearchResult, link_url
from scavengarr.infrastructure.config.load import load_config
from scavengarr.infrastructure.hoster_resolvers import extract_domain
from scavengarr.infrastructure.hoster_resolvers.registry import HosterResolverRegistry
from scavengarr.infrastructure.plugins.constants import search_max_results
from scavengarr.infrastructure.stremio.title_matcher import filter_by_title_match
from scavengarr.interfaces.app import create_app
from scavengarr.interfaces.app_state import AppState

# title_match.py is a sibling, not a package: appended, so that nothing in
# this directory shadows an installed package
sys.path.append(str(Path(__file__).resolve().parent))
from title_match import (  # noqa: E402  # pyright: ignore[reportMissingImports]
    _search,
    parse_id,
)

REPO = Path(__file__).resolve().parents[2]
TITLES = REPO / ".cache" / "live" / "round-titles.txt"
OUT = REPO / ".cache" / "live" / "resolver-urls.json"


@dataclass
class Found:
    """What the searches yielded: the first link per resolver and the counts.

    A link in *avoid* is counted but not taken.
    """

    avoid: set[str] = field(default_factory=set)
    urls: dict[str, str] = field(default_factory=dict)
    plugin: dict[str, str] = field(default_factory=dict)
    links: Counter[str] = field(default_factory=Counter)
    unclaimed: Counter[str] = field(default_factory=Counter)
    failures: Counter[str] = field(default_factory=Counter)
    results: int = 0

    def take(
        self, registry: HosterResolverRegistry, plugin: str, result: SearchResult
    ) -> None:
        self.results += 1
        links = [result.download_link, *map(link_url, result.download_links or ())]
        for url in dict.fromkeys(u.strip() for u in links if u and u.strip()):
            name = resolver_name(registry, url)
            if name is None:
                self.unclaimed[extract_domain(url) or "?"] += 1
                continue
            self.links[name] += 1
            if url in self.avoid:
                continue
            self.urls.setdefault(name, url)
            self.plugin.setdefault(name, plugin)


def resolver_name(registry: HosterResolverRegistry, url: str) -> str | None:
    """The resolver the registry dispatches *url* to, ``None`` for no claim."""
    host = (urlsplit(url).hostname or "").removeprefix("www.")
    return registry.canonical_hoster(host) or registry.canonical_hoster(
        extract_domain(url)
    )


def read_avoid(path: Path | None) -> set[str]:
    """The links an earlier output names (``--avoid``); none without one."""
    if path is None:
        return set()
    return {str(url) for url in json.loads(path.read_text()).values()}


def read_titles(path: Path) -> list[str]:
    """One Stremio id per line, a comment after ``#``."""
    ids = []
    for line in path.read_text().splitlines():
        sid = line.split("#", 1)[0].strip()
        if sid:
            ids.append(sid)
    return ids


async def collect(
    state: AppState, sids: list[str], only: set[str] | None, avoid: set[str]
) -> Found:
    config = state.config.stremio
    assert state.tmdb_client is not None
    assert state.hoster_resolver_registry is not None
    registry = state.hoster_resolver_registry
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
    found = Found(avoid=avoid)

    async def one(name: str, query: str, category: int, req: Any) -> None:
        plugin = state.plugins.get(name)
        async with slots:
            try:
                results = await _search(
                    plugin, query, category, req, config.plugin_timeout_seconds
                )
            except TimeoutError:
                found.failures[f"{name}: timeout"] += 1
                return
            except Exception as exc:  # a site's failure is a finding
                found.failures[f"{name}: {type(exc).__name__}"] += 1
                return
        for result in results:
            found.take(registry, name, result)

    for sid in sids:
        req, category = parse_id(sid)
        infos, _meta = await titles.title_infos(req, titles.languages(names))
        tasks = []
        for lang_key, group in titles.language_groups(names).items():
            queries = build_lang_group_queries(infos, list(lang_key))
            if not queries:
                print(f"{sid}: no title for {list(lang_key)}", file=sys.stderr)
                continue
            tasks += [one(n, q, category, req) for n in group for q in queries]
        await asyncio.gather(*tasks)
        print(
            f"{sid}: {len(found.urls)} resolvers so far, {found.results} results",
            file=sys.stderr,
        )
    return found


async def run(sids: list[str], only: set[str] | None, avoid: set[str]) -> Found:
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
            return await collect(cast(AppState, app.state), sids, only, avoid)


def render(found: Found, titles: int, out: Path) -> str:
    lines = ["| Resolver | Links | First link from |", "|---|---|---|"]
    lines += [
        f"| {name} | {found.links[name]} | {found.plugin[name]} |"
        for name in sorted(found.urls)
    ]
    lines.append("")
    lines.append(
        f"{len(found.urls)} resolvers, {sum(found.links.values())} claimed links of"
        f" {found.results} results over {titles} titles; written to {out}"
    )
    if found.unclaimed:
        top = ", ".join(f"{d} {n}" for d, n in found.unclaimed.most_common(12))
        lines.append(f"Links no resolver claims (second-level domain, count): {top}")
    if found.failures:
        top = ", ".join(f"{f} {n}" for f, n in found.failures.most_common(12))
        lines.append(f"Searches that failed: {top}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    # the plugins log every request at info level
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING)
    )
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--titles", type=Path, default=TITLES)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--plugins", help="comma-separated plugin names")
    parser.add_argument(
        "--avoid", type=Path, help="an earlier output whose links are not taken"
    )
    args = parser.parse_args(argv)
    only = set(args.plugins.split(",")) if args.plugins else None
    sids = read_titles(args.titles)
    # the app logs to stdout; the table is the probe's only output
    with contextlib.redirect_stdout(sys.stderr):
        found = asyncio.run(run(sids, only, read_avoid(args.avoid)))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(dict(sorted(found.urls.items())), indent=1))
    print(render(found, len(sids), args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
