"""Series episodes per plugin: what the episode filter keeps, and its leaks.

``poetry run python scripts/probes/series_episodes.py [--json FILE] [ID ...]``
(``xvfb-run -a`` without a display: the browser fallback runs headful)
starts the app's composition (plugins, browser fallback, title lookup)
and runs every stream plugin's search directly, with the queries, the
category, season and episode of a Stremio request, under the plugin
timeout and the runner's plugin concurrency. Every result that matches
the title is classified: season and episode from its metadata or the
release name (guessit), the episode filter's outcome (kept, narrowed,
dropped), whether it shows the requested episode, and leaks: kept results
whose title or link labels name another episode.

A request's episode reference (the Cinemeta entry's title and the
absolute number; ``series/kitsu:12:1089`` names the episode as Kitsu
counts and is translated first, an IMDb id's episode gets the Kitsu number
the app looks up, with the id list loaded first) goes to the plugins that
locate episodes on their own season pages; the placement they answer
(``site_season``, ``site_episode``, ``episode_located_by``) is listed per
result.

With sto and aniworld among the plugins, the links both give for the
episode (the kept results that show it) are compared: aniworld's
``/redirect/<n>`` links are followed the way the registry follows them at
play time (``_follow_redirects()``, a few at a time), every link becomes
its hoster and file id, and a table counts per request and hoster the files
both sites list, sto alone and aniworld alone (row 71: does sto add streams
where aniworld places the episode too?). The links stay in memory: the
output names hosters and counts only.

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
from urllib.parse import urlsplit

import structlog

from scavengarr.application.stremio.queries import (
    build_lang_group_queries,
    build_multi_lang_reference,
)
from scavengarr.application.stremio.title_resolution import TitleResolver
from scavengarr.domain.entities.stremio import EpisodeRef, StremioStreamRequest
from scavengarr.domain.plugins.base import (
    SearchResult,
    link_url,
    locates_episodes,
    result_key,
)
from scavengarr.infrastructure.anime.id_lists import AnimeIdLists
from scavengarr.infrastructure.anime.kitsu_addon import KitsuAddonClient
from scavengarr.infrastructure.anime.resolver import KitsuAnimeIdResolver
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

# Episode words in link labels, read independently of the filter (which reads
# them too since row 30 of the ideas backlog): an oracle for its regressions
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
    """``series/tt0903747:1:2`` -> the Stremio request; ``series/kitsu:12:1089``
    names the episode as Kitsu counts it (no season)."""
    raw = sid.removeprefix("series/")
    if raw.startswith("kitsu:"):
        _, kitsu_id, episode = raw.split(":")
        return StremioStreamRequest(
            imdb_id=f"kitsu:{kitsu_id}", content_type="series", episode=int(episode)
        )
    imdb_id, season, episode = raw.split(":")
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
    # the site's page a locating plugin answered: "staffel-2/episode-1 by number"
    placement: str = ""


def placement(result: SearchResult) -> str:
    """The page a locating plugin took the links from, and its evidence."""
    meta = result.metadata
    if "site_season" not in meta:
        return ""
    return (
        f"staffel-{meta.get('site_season')}/episode-{meta.get('site_episode')}"
        f" by {meta.get('episode_located_by')}"
    )


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
        placement=placement(result),
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
    "located",
)


# The two sites row 71 compares, and how many redirects are followed at once
OVERLAP = ("sto", "aniworld")
_REDIRECTS_AT_ONCE = 3
# Path segments that name a hoster page's kind, not its file
_PAGE_KINDS = {"e", "d", "v", "f", "w", "embed", "file", "play", "watch"}

Identity = tuple[str, str]  # hoster, file id


def file_identity(url: str, hoster: str) -> Identity:
    """*url*'s file on *hoster*: the first path segment past the page kind
    (``/e/<id>/<name>``, ``/embed-<id>.html``, ``/<id>.html``), else the
    fragment (``/#<id>``)."""
    parts = urlsplit(url)
    segments = [s for s in parts.path.split("/") if s]
    while segments and segments[0].lower() in _PAGE_KINDS:
        segments.pop(0)
    if not segments:
        return hoster, parts.fragment
    file_id = segments[0].removeprefix("embed-")
    return hoster, file_id.removesuffix(".html")


def overlap_rows(sto: set[Identity], aniworld: set[Identity]) -> list[tuple]:
    """Per hoster: the files both list, sto alone, aniworld alone."""
    hosters = sorted({h for h, _ in sto | aniworld})
    return [
        (
            hoster,
            sum(1 for i in sto & aniworld if i[0] == hoster),
            sum(1 for i in sto - aniworld if i[0] == hoster),
            sum(1 for i in aniworld - sto if i[0] == hoster),
        )
        for hoster in hosters
    ]


@dataclass
class Overlap:
    """One request's comparison: each site's placement and the files."""

    request: str
    sto: str
    aniworld: str
    rows: list[tuple] = field(default_factory=list)
    unresolved: dict[str, int] = field(default_factory=dict)


def render_overlap(entries: list[Overlap]) -> str:
    """The overlap table (counts only) and the links no redirect resolved."""
    lines = [
        "| request | sto | aniworld | hoster | both | sto only | aniworld only |",
        "|---|---|---|---|---:|---:|---:|",
    ]
    for entry in entries:
        rows = entry.rows or [("–", 0, 0, 0)]
        for n, (hoster, both, sto, ani) in enumerate(rows):
            head = f"| {entry.request} | {entry.sto} | {entry.aniworld} |"
            lines.append(
                f"{head if n == 0 else '| | | |'} {hoster} | {both} | {sto} | {ani} |"
            )
    notes = [
        f"{e.request}: {site} {count} links unresolved"
        for e in entries
        for site, count in sorted(e.unresolved.items())
        if count
    ]
    if notes:
        lines += ["", *notes]
    return "\n".join(lines)


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
        row["located"] += bool(rec.placement)
    return table


def render(
    records: list[Record],
    failures: dict[str, Counter[str]],
    plugins: list[str],
    references: dict[str, str] | None = None,
) -> str:
    """The per-plugin table, totals, every leak, the references and the
    placements the locating plugins answered."""
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
    if references:
        lines += ["", "References:", ""]
        lines += [f"- {sid}: {ref}" for sid, ref in references.items()]
    placed = [r for r in records if r.placement]
    if placed:
        lines += ["", "Placements:", ""]
        lines += [
            f"- {r.plugin}, {r.request}: {r.title!r} -> {r.placement}" for r in placed
        ]
    return "\n".join(lines)


async def _search(
    plugin: Any,
    query: str,
    req: StremioStreamRequest,
    timeout: float,
    episode_ref: EpisodeRef | None,
) -> list[SearchResult]:
    search = getattr(plugin, "isolated_search", None) or plugin.search
    extra: dict[str, Any] = {}
    if episode_ref is not None and locates_episodes(plugin):
        extra["episode_ref"] = episode_ref
    return await asyncio.wait_for(
        search(query, SERIES, season=req.season, episode=req.episode, **extra),
        timeout,
    )


def _reference(req: StremioStreamRequest, ref: EpisodeRef | None) -> str:
    """The request as IMDb counts it and what the catalog says of it."""
    line = f"S{req.season}E{req.episode}"
    if ref is None:
        return line + " (no reference)"
    return f"{line} absolute {ref.absolute} title {ref.title!r} aired {ref.aired}"


def _plugin_names(state: AppState, only: set[str] | None) -> list[str]:
    """The stream plugins the probe asks; *only* those when given."""
    names = sorted(
        set(state.plugins.get_by_provides("stream"))
        | set(state.plugins.get_by_provides("both"))
    )
    return [n for n in names if not only or n in only]


async def _numbering(state: AppState) -> KitsuAnimeIdResolver:
    """The app's Kitsu numbering of IMDb requests with its id list loaded:
    the app's own look-up never waits for the list, so its first requests
    after a start go without."""
    lists = AnimeIdLists(http_client=state.http_client, cache=state.cache)
    await lists.entry(0)
    addon = KitsuAddonClient(http_client=state.http_client, cache=state.cache)
    return KitsuAnimeIdResolver(addon=addon, lists=lists)


async def _request(
    state: AppState, sid: str, numbering: KitsuAnimeIdResolver
) -> tuple[StremioStreamRequest, int | None] | None:
    """The request *sid* means, as the use case handles it: a ``kitsu:`` id
    is translated first and its episode number kept for the reference, an
    IMDb id's episode gets the Kitsu number *numbering* finds; ``None``
    when it has no translation."""
    req = parse_id(sid)
    if not req.imdb_id.startswith("kitsu:"):
        return req, await numbering.kitsu_episode(req)
    translated = await state.anime_ids.translate(req)
    if translated is None:
        return None
    return translated, req.episode


def _record(
    sid: str,
    name: str,
    matched: list[SearchResult],
    season: int,
    episode: int,
    records: list[Record],
    kept_links: dict[tuple[str, str], list[str]],
) -> None:
    """Classify *matched*; keep the links of the results that show the
    episode for the overlap (sto and aniworld only)."""
    for result in matched:
        record = classify(sid, name, result, season, episode)
        records.append(record)
        if name in OVERLAP and record.right:
            _, kept = filter_outcome(result, season, episode)
            kept_links.setdefault((sid, name), []).extend(
                link_url(link) for link in (kept.download_links if kept else ())
            )


async def probe(
    state: AppState, sids: list[str], only: set[str] | None
) -> tuple[
    list[Record], dict[str, Counter[str]], list[str], dict[str, str], list[Overlap]
]:
    config = state.config.stremio
    assert state.tmdb_client is not None
    titles = TitleResolver(
        tmdb=state.tmdb_client,
        plugins=state.plugins,
        filter_fn=filter_by_title_match,
        config=config,
        series_meta=state.series_meta,
    )
    names = _plugin_names(state, only)
    slots = asyncio.Semaphore(config.max_concurrent_plugins)
    search_max_results.set(config.max_results_per_plugin)
    records: list[Record] = []
    failures: dict[str, Counter[str]] = {}
    references: dict[str, str] = {}
    # (request, plugin) -> the links of the kept results that show the episode
    kept_links: dict[tuple[str, str], list[str]] = {}

    async def one_plugin(
        sid: str,
        req: StremioStreamRequest,
        name: str,
        queries: list[str],
        ref: Any,
        episode_ref: EpisodeRef | None,
    ) -> None:
        plugin = state.plugins.get(name)
        found: dict[Any, SearchResult] = {}
        for query in queries:
            async with slots:
                try:
                    results = await _search(
                        plugin, query, req, config.plugin_timeout_seconds, episode_ref
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
        _record(sid, name, matched, req.season, req.episode, records, kept_links)

    numbering = await _numbering(state)
    for sid in sids:
        started = time.monotonic()
        request = await _request(state, sid, numbering)
        if request is None:
            references[sid] = "no translation"
            print(f"{sid}: no translation", file=sys.stderr)
            continue
        req, absolute = request
        infos, meta = await titles.title_infos(req, titles.languages(names))
        episode_ref = titles.episode_ref(req, meta, absolute=absolute)
        references[sid] = _reference(req, episode_ref)
        tasks = []
        for langs, group in titles.language_groups(names).items():
            ref = build_multi_lang_reference(infos, list(langs))
            queries = build_lang_group_queries(infos, list(langs))
            if ref is None or not queries:
                continue
            tasks += [one_plugin(sid, req, n, queries, ref, episode_ref) for n in group]
        await asyncio.gather(*tasks)
        print(
            f"{sid}: {sum(r.request == sid for r in records)} results,"
            f" {time.monotonic() - started:.0f} s",
            file=sys.stderr,
        )
    overlaps = (
        await compare(state, sids, records, kept_links)
        if all(n in names for n in OVERLAP)
        else []
    )
    return records, failures, names, references, overlaps


def _site_placement(records: list[Record], sid: str, plugin: str) -> str:
    """How *plugin* placed *sid*'s episode: its page and evidence, ``kept``
    for a result that shows it without a placement, else ``nothing``."""
    right = [r for r in records if r.request == sid and r.plugin == plugin and r.right]
    placed = next((r.placement for r in right if r.placement), "")
    return placed or ("kept" if right else "nothing")


async def compare(
    state: AppState,
    sids: list[str],
    records: list[Record],
    kept_links: dict[tuple[str, str], list[str]],
) -> list[Overlap]:
    """Per request: sto's and aniworld's files of the episode, as hoster and
    file id; a link on the site's own host is followed like the registry does."""
    registry = state.hoster_resolver_registry
    assert registry is not None
    slots = asyncio.Semaphore(_REDIRECTS_AT_ONCE)

    async def identity(url: str, site: str) -> Identity | None:
        if urlsplit(url).hostname == site:
            async with slots:
                url = await registry._follow_redirects(url) or ""
            if not url or urlsplit(url).hostname == site:
                return None
        host = (urlsplit(url).hostname or "").removeprefix("www.")
        hoster = registry.canonical_hoster(host) or host
        return file_identity(url, hoster)

    entries = []
    for sid in sids:
        files: dict[str, set[Identity]] = {}
        unresolved: dict[str, int] = {}
        for name in OVERLAP:
            plugin = state.plugins.get(name)
            site = urlsplit(getattr(plugin, "base_url", "")).hostname or ""
            links = list(dict.fromkeys(kept_links.get((sid, name), [])))
            found = await asyncio.gather(*(identity(u, site) for u in links))
            files[name] = {i for i in found if i is not None}
            unresolved[name] = sum(i is None for i in found)
        entries.append(
            Overlap(
                request=sid,
                sto=_site_placement(records, sid, "sto"),
                aniworld=_site_placement(records, sid, "aniworld"),
                rows=overlap_rows(files["sto"], files["aniworld"]),
                unresolved=unresolved,
            )
        )
    return entries


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
        records, failures, names, references, overlaps = asyncio.run(run(sids, only))
    if args.json:
        out = {
            "requests": sids,
            "references": references,
            "plugins": {n: dict(c) for n, c in tally(records).items()},
            "failures": {n: dict(c) for n, c in failures.items()},
            "records": [asdict(r) for r in records],
        }
        args.json.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(render(records, failures, names, references))
    if overlaps:
        print()
        print(render_overlap(overlaps))
    return 0


if __name__ == "__main__":
    sys.exit(main())
