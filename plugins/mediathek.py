"""mediathekviewweb.de Python plugin for Scavengarr.

The public broadcasters' mediatheken (ARD with its regional broadcasters,
ZDF, arte, 3sat, KiKA, phoenix, ORF, SRF and more) through the search API
of MediathekViewWeb, which indexes MediathekView's Filmliste:
- GET /api/query?query={JSON}: title and topic, every word as a substring,
  newest first, pages of up to 1000 entries; there are no detail pages
- each entry links the broadcaster's own files (MP4 or HLS); a result
  carries the HD file, else the normal one, for the ``mediathek`` resolver

The Filmliste's title conventions carry the rest. Versions for
accessibility and the subtitled twins of the German version are dropped;
original versions, OmU and English versions are results of their own
language; arte's other languages are channels of their own and are
dropped. A stated number ("(S01/E02)", "(Staffel 1, Folge 2)") or a part
marker ("(3/6)") numbers an episode. Films and documentaries (an entry
without a number in a collection topic, a topic that is the title itself,
3sat's genre suffix, arte's titles without a number) are 2000, the other
entries are episodes of their topic (5000).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from scavengarr.domain.entities.stremio import EpisodeRef
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.categories import (
    filter_by_category,
    served_category,
    stream_category,
)
from scavengarr.infrastructure.plugins.episode_index import normalize_title
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase
from scavengarr.infrastructure.plugins.relevance import relevant_hits

_DOMAINS = ["mediathekviewweb.de"]
_API_PATH = "/api/query"
# The API's cap per page
_PAGE_SIZE = 1000
# Pages read at most: the dropped entries (versions, other titles) leave
# room on the first
_MAX_PAGES = 3
# Shorter entries are trailers and clips (the API's minimum is exclusive)
_MIN_DURATION_S = 299
_MOVIE = stream_category((), is_series=False)
_SERIES = stream_category((), is_series=True)
_CATEGORIES = (_MOVIE, _SERIES)
# The channels as the release name names them: arte's German channel is
# arte, and guessit reads "BR" as Brazilian Portuguese
_CHANNEL_NAMES = {"ARTE.DE": "ARTE", "BR": "Bayerischer Rundfunk"}
# The resolver of the broadcasters' files: every file is one hoster, so a
# request gets one stream per language
_HOSTER = "mediathek"

_GERMAN = "German"
_GERMAN_SUB = "German Sub"
_ENGLISH = "English"
# The original language, whichever it is ("The Whale (Originalversion)")
_ORIGINAL = "Original"

# Versions for accessibility, anywhere in the title or topic: audio
# description, sign language (DGS, ÖGS), plain language
_ACCESSIBLE_RE = re.compile(
    r"audiodeskription|hörfassung|gebärdensprache|klare[nrs]?\s+sprache"
    r"|\b(?:dgs|ögs)\b",
    re.IGNORECASE,
)
# A group of a title: "(Originalversion)", "(S01/E02)", "(OmU, 3/5)"
_GROUP_RE = re.compile(r"\(([^()]*)\)")
# Where a part marker stood in a title: arte names its series before it
_PART_MARK = "\x00"
_LANGUAGE_ITEMS = {
    "omu": _GERMAN_SUB,
    "originalversion mit untertitel": _GERMAN_SUB,
    "originalversion mit untertiteln": _GERMAN_SUB,
    "englisch": _ENGLISH,
    "eng": _ENGLISH,
    "engl.": _ENGLISH,
    "english": _ENGLISH,
    "originalversion": _ORIGINAL,
    "originalfassung": _ORIGINAL,
    "ov": _ORIGINAL,
    # An Austrian film in its own German
    "österreich. ov": _GERMAN,
}
# The German version with subtitles: its plain twin is the result
_TWIN_ITEMS = frozenset({"mit untertitel", "mit untertiteln", "untertitel"})
_STATED_RE = re.compile(r"s(\d{1,4})\s*/\s*e(\d{1,4})")
_SEASON_ITEM_RE = re.compile(r"staffel\s+(\d{1,4})")
_EPISODE_ITEM_RE = re.compile(r"folge\s+(\d{1,4})")
_PART_RE = re.compile(r"(\d{1,3})\s*/\s*\d{1,3}")
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
# "Staffel 5" in a topic or a title, its separator before it included
_STAFFEL_RE = re.compile(
    r"\s*(?:[:\-–—|·]\s*)?\(?\bStaffel\s+(\d{1,4})\b\)?", re.IGNORECASE
)
# "Folge 1208 - " before an episode's title or " | Folge 1610" after it
_FOLGE_RE = re.compile(
    r"^Folge\s+\d+\s*[:\-–—|·]*\s*|\s*[:\-–—|·]*\s*Folge\s+\d+$", re.IGNORECASE
)
_SEPARATORS = " :-–—|·"
# The topics that collect films and documentaries ("Filme im Ersten",
# "Spielfilm", "Das kleine Fernsehspiel", "Dokus & Reportagen"): an entry
# without a number there is a film of its own
_COLLECTION_RE = re.compile(
    r"\b(?:(?:spiel|fernseh|kino|kurz|dokumentar)?filme?|fernsehspiel|dokus?"
    r"|dokumentation(?:en)?|dokumentarfilmzeit)\b",
    re.IGNORECASE,
)
# 3sat names a film's genre after its title: "Nikita - Spielfilm,
# Frankreich/Italien 1990", "Sorry Genosse - Dokumentarfilm von Vera
# Brückner, Deutschland 2022"
_GENRE_SUFFIX_RE = re.compile(
    r"\s+[-–]\s+(?:\w*film|fernsehspiel|drama|komödie|tragikomödie|thriller|krimi)"
    r"(?:\s+von\s+.+?)?(?:,\s*.*?((?:19|20)\d{2}))?[\s,]*$",
    re.IGNORECASE,
)
# ARD's film descriptions start with genre, countries and year:
# "Spielfilm USA 2022 Für den ..."
_DESCRIPTION_YEAR_RE = re.compile(
    r"\s*[A-ZÄÖÜ][\w-]*(?:film|drama|komödie|thriller|krimi)\b[^.\d]{0,60}?"
    r"((?:19|20)\d{2})\b"
)

# The file's lines from its name: "1920x1080", "avc-720", "_540p.", ".1080.mp4"
_HEIGHT_RES = (
    re.compile(r"\d{3,4}x(\d{3,4})"),
    re.compile(r"avc-?(\d{3,4})", re.IGNORECASE),
    re.compile(r"[_.-](\d{3,4})[pi](?=[._-])"),
    re.compile(r"\.(\d{3,4})\.mp4$"),
)
# ZDF's profile code ("_6660k_p37v17.mp4") and arte's quality code
# ("-A_SQ_0_"), measured on the files themselves
_ZDF_RE = re.compile(r"_\d+k_p(\d+)v\d+\.mp4$")
_ZDF_HEIGHTS = {37: 1080, 36: 720, 15: 720, 35: 576, 34: 576, 13: 480, 11: 360, 9: 360}
_ARTE_RE = re.compile(r"-A_([A-Z]{2})_\d_")
_ARTE_HEIGHTS = {"SQ": 720, "EQ": 406, "HQ": 360}


def _last_sunday(year: int, month: int) -> dt.datetime:
    """01:00 UTC on the last Sunday of *month*: when summer time starts
    (March) or ends (October)."""
    last = dt.datetime(year, month + 1, 1, 1, tzinfo=dt.UTC) - dt.timedelta(days=1)
    return last - dt.timedelta(days=(last.weekday() + 1) % 7)


def _broadcast_day(timestamp: int) -> dt.date:
    """The day of a broadcast in German time: UTC+2 in summer time, else
    UTC+1 (written out, the image has no time zone data)."""
    utc = dt.datetime.fromtimestamp(timestamp, dt.UTC)
    summer = _last_sunday(utc.year, 3) <= utc < _last_sunday(utc.year, 10)
    return (utc + dt.timedelta(hours=2 if summer else 1)).date()


def _height(url: str) -> int | None:
    path = url.split("?", 1)[0]
    for pattern in _HEIGHT_RES:
        match = pattern.search(path)
        if match:
            return int(match.group(1))
    match = _ZDF_RE.search(path)
    if match:
        return _ZDF_HEIGHTS.get(int(match.group(1)))
    match = _ARTE_RE.search(path)
    if match:
        return _ARTE_HEIGHTS.get(match.group(1))
    return None


def _quality(url: str, field: str) -> str | None:
    """The quality of the file *url* of the entry's *field* from its name,
    ``None`` where the name tells none. The Filmliste's HD file without a
    marker is "HD": 720 lines or more, which no height names."""
    height = _height(url)
    if height is None:
        hd_file = field == "url_video_hd" and url.split("?", 1)[0].lower().endswith(
            ".mp4"
        )
        return "HD" if hd_file else None
    for lines, label in ((2160, "2160p"), (1080, "1080p"), (720, "720p")):
        if height >= lines:
            return label
    return "SD"


@dataclass
class _Title:
    """A title read: its text without the groups the plugin knows, and
    what they said."""

    text: str = ""
    # The text before and after a part marker
    before: str | None = None
    after: str | None = None
    language: str = _GERMAN
    twin: bool = False
    stated: tuple[int, int] | None = None
    season: int | None = None
    episode: int | None = None
    part: int | None = None
    year: int | None = None


def _read_item(title: _Title, item: str) -> bool:
    """Apply one item of a title's group ("omu", "s01/e02", "3/6") to
    *title*; whether the plugin knows it. A running number ("(22)") says
    nothing."""
    if item in _TWIN_ITEMS:
        title.twin = True
    elif item in _LANGUAGE_ITEMS:
        title.language = _LANGUAGE_ITEMS[item]
    elif match := _STATED_RE.fullmatch(item):
        title.stated = (int(match.group(1)), int(match.group(2)))
    elif match := _SEASON_ITEM_RE.fullmatch(item):
        title.season = int(match.group(1))
    elif match := _EPISODE_ITEM_RE.fullmatch(item):
        title.episode = int(match.group(1))
    elif match := _PART_RE.fullmatch(item):
        title.part = int(match.group(1))
    elif _YEAR_RE.fullmatch(item):
        title.year = int(item)
    elif not item.isdigit():
        return False
    return True


def _clean(text: str) -> str:
    return " ".join(text.split()).strip(_SEPARATORS)


def _read_title(raw: str) -> _Title:
    title = _Title()

    def _group(match: re.Match[str]) -> str:
        items = [
            " ".join(item.casefold().split()) for item in match.group(1).split(",")
        ]
        if not all([_read_item(title, item) for item in items]):
            return match.group(0)
        return _PART_MARK if any(_PART_RE.fullmatch(item) for item in items) else " "

    marked = _GROUP_RE.sub(_group, raw)
    before, mark, after = marked.partition(_PART_MARK)
    title.text = _clean(f"{before} {after}".replace(_PART_MARK, " "))
    if mark:
        title.before, title.after = _clean(before), _clean(after)
    return title


def _staffel(text: str) -> int | None:
    match = _STAFFEL_RE.search(text)
    return int(match.group(1)) if match else None


def _numbers(title: _Title, topic: str) -> tuple[int, int] | None:
    """The season and episode the title states. A part marker or a lone
    "Folge n" counts in the season "Staffel n" names, else in the first."""
    if title.stated is not None:
        return title.stated
    if title.season is not None and title.episode is not None:
        return title.season, title.episode
    episode = title.part if title.part is not None else title.episode
    if episode is None:
        return None
    season = title.season or _staffel(title.text) or _staffel(topic) or 1
    return season, episode


def _without_staffel(text: str) -> str:
    return _clean(_STAFFEL_RE.sub("", text))


def _episode_title(text: str, series: str) -> str | None:
    """*text* without the series' name at its start or end and without
    "Folge n"; ``None`` when nothing else is left."""
    name = re.escape(series)
    text = re.sub(rf"^{name}\s*[:\-–—|·]+\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(rf"\s*[:\-–—|·]+\s*{name}$", "", text, flags=re.IGNORECASE)
    text = _clean(_FOLGE_RE.sub("", text))
    if not text or normalize_title(text) == normalize_title(series):
        return None
    return text


def _video(entry: Mapping[str, Any]) -> tuple[str, str] | None:
    """The entry's HD file, else its normal one, with the field's name."""
    for field in ("url_video_hd", "url_video"):
        url = str(entry.get(field) or "").strip()
        if url.startswith(("https://", "http://")):
            return field, url
    return None


@dataclass(frozen=True)
class _Broadcast:
    """An entry of the Filmliste as the plugin reads it."""

    channel: str
    # The film's title or the series' name
    title: str
    category: int
    language: str
    url: str
    quality: str | None
    day: dt.date
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    episode_title: str | None = None
    description: str = ""
    website: str = ""

    @property
    def release_name(self) -> str:
        """Title, numbers and episode title, then channel, broadcast day
        and quality: "The Whale (2022) (ARD, 19.10.26, 1080p)". The day
        is written ``dd.mm.yy``, which guessit reads as a date, not as the
        film's year."""
        name = self.title
        if self.category == _MOVIE:
            if self.year is not None:
                name = f"{name} ({self.year})"
        elif self.season is not None and self.episode is not None:
            name = f"{name} S{self.season:02d}E{self.episode:02d}"
            if self.episode_title:
                name = f"{name} {self.episode_title}"
        elif self.episode_title:
            name = f"{name} - {self.episode_title}"
        details = [self.channel, f"{self.day:%d.%m.%y}"]
        if self.quality:
            details.append(self.quality)
        return f"{name} ({', '.join(details)})"

    @property
    def key(self) -> tuple[object, ...]:
        """What makes two entries one result: a repeat on the same channel
        in the same language."""
        return (
            self.channel,
            self.title.casefold(),
            self.year,
            self.season,
            self.episode,
            (self.episode_title or "").casefold(),
            self.language,
        )


def _read_entry(entry: Mapping[str, Any]) -> _Broadcast | None:
    """An entry of the API's answer as a broadcast; ``None`` for one the
    plugin drops: arte in another language, a version for accessibility, a
    subtitled twin, an entry without a file."""
    channel = str(entry.get("channel") or "")
    if channel.startswith("ARTE.") and channel != "ARTE.DE":
        return None
    raw = " ".join(str(entry.get("title") or "").split())
    topic = " ".join(str(entry.get("topic") or "").split())
    video = _video(entry)
    if not raw or video is None or _ACCESSIBLE_RE.search(f"{raw} {topic}"):
        return None
    title = _read_title(raw)
    if title.twin or not title.text:
        return None

    numbers = _numbers(title, topic)
    text, year = title.text, title.year
    suffix = _GENRE_SUFFIX_RE.search(text)
    if suffix:
        text = text[: suffix.start()]
        if suffix.group(1):
            year = int(suffix.group(1))
    arte = channel == "ARTE.DE"
    collection = _COLLECTION_RE.search(topic) is not None
    film = numbers is None and (
        arte
        or collection
        or suffix is not None
        or normalize_title(topic) == normalize_title(text)
    )

    description = str(entry.get("description") or "").strip()
    field, url = video
    common: dict[str, Any] = {
        "channel": _CHANNEL_NAMES.get(channel, channel),
        "language": title.language,
        "url": url,
        "quality": _quality(url, field),
        "day": _broadcast_day(int(entry.get("timestamp") or 0)),
        "description": description,
        "website": str(entry.get("url_website") or ""),
    }
    if film:
        if year is None and (match := _DESCRIPTION_YEAR_RE.match(description)):
            year = int(match.group(1))
        return _Broadcast(title=text, category=_MOVIE, year=year, **common)

    if numbers is not None and title.before and (arte or collection):
        # arte's series and the collections name the series before the marker
        series, episode_title = _without_staffel(title.before), title.after or None
    else:
        series = _without_staffel(topic) or text
        episode_title = _episode_title(text, series)
    season, episode = numbers if numbers is not None else (None, None)
    return _Broadcast(
        title=series,
        category=_SERIES,
        season=season,
        episode=episode,
        episode_title=episode_title,
        **common,
    )


def _located(
    broadcast: _Broadcast,
    season: int,
    episode: int | None,
    ref: EpisodeRef | None,
) -> dict[str, Any] | None:
    """The metadata that places *broadcast* as the requested episode,
    ``None`` when it is another one.

    With *ref* (a Stremio request) a stated number places it by the
    numbers alone; an entry without one by its episode title or its
    broadcast day (Tatort states no numbers). Without *ref* only the stated
    numbers count. Never the request's numbers by themselves.
    """
    if broadcast.category != _SERIES:
        return None
    stated = broadcast.season is not None and broadcast.episode is not None
    if ref is None:
        if broadcast.season != season or episode not in (None, broadcast.episode):
            return None
        return {}
    if stated:
        if (broadcast.season, broadcast.episode) != (ref.season, ref.episode):
            return None
        by = "number"
    elif (
        ref.title
        and broadcast.episode_title
        and normalize_title(ref.title) == normalize_title(broadcast.episode_title)
    ):
        by = "title"
    elif ref.aired and ref.aired[:10] == broadcast.day.isoformat():
        by = "date"
    else:
        return None
    located: dict[str, Any] = {
        "season": ref.season,
        "episode": ref.episode,
        "episode_located_by": by,
    }
    if stated:
        located["site_season"] = broadcast.season
        located["site_episode"] = broadcast.episode
    return located


def _result(broadcast: _Broadcast, located: dict[str, Any]) -> SearchResult:
    link = {"link": broadcast.url, "hoster": _HOSTER, "language": broadcast.language}
    if broadcast.quality:
        link["quality"] = broadcast.quality
    metadata: dict[str, Any] = {"channel": broadcast.channel}
    if broadcast.year is not None:
        metadata["year"] = broadcast.year
    if broadcast.season is not None and broadcast.episode is not None:
        metadata["season"] = broadcast.season
        metadata["episode"] = broadcast.episode
    metadata.update(located)
    return SearchResult(
        title=broadcast.title,
        download_link=broadcast.url,
        release_name=broadcast.release_name,
        description=broadcast.description or None,
        download_links=[link],
        source_url=broadcast.website or None,
        metadata=metadata,
        category=broadcast.category,
    )


def _answer(
    broadcasts: list[_Broadcast],
    query: str,
    wanted: int | None,
    season: int | None,
    episode: int | None,
    ref: EpisodeRef | None,
) -> list[SearchResult]:
    """The results of *broadcasts*: the films and series *query* names
    (the API also finds its words inside other titles), the requested
    episode with *season*, one result per broadcast and language."""
    seen: set[tuple[object, ...]] = set()
    results: list[SearchResult] = []
    for broadcast in relevant_hits(
        broadcasts, query, lambda hit: hit.title, fallback=0
    ):
        if broadcast.key in seen:
            continue
        located: dict[str, Any] | None = {}
        if season is not None:
            located = _located(broadcast, season, episode, ref)
            if located is None:
                continue
        seen.add(broadcast.key)
        results.append(_result(broadcast, located))
    return filter_by_category(results, wanted) if wanted is not None else results


@dataclass(frozen=True)
class _Page:
    broadcasts: list[_Broadcast]
    # Whether the answer ends with this page
    last: bool


def _query_body(term: str, offset: int) -> dict[str, Any]:
    return {
        "queries": [{"fields": ["title", "topic"], "query": term}],
        "sortBy": "timestamp",
        "sortOrder": "desc",
        "future": False,
        "offset": offset,
        "size": _PAGE_SIZE,
        "duration_min": _MIN_DURATION_S,
    }


class MediathekPlugin(HttpxPluginBase):
    """Python plugin for the public broadcasters' mediatheken (the API of
    MediathekViewWeb)."""

    name = "mediathek"
    provides = "stream"
    _domains = _DOMAINS

    # A series episode is placed by the entry's stated number, its title or
    # its broadcast day: the request's numbers are IMDb's
    locates_episodes = True

    async def _page(self, query: str, page: int) -> _Page | None:
        """Page *page* of the API's answer to *query*, ``None`` when it
        fails. The first goes in the raising mode (a failed site is a
        failed search in the plugin record), the later ones leniently."""
        params = {"query": json.dumps(_query_body(query, page * _PAGE_SIZE))}
        if page == 0:
            body = await self._fetch_search_page(_API_PATH, params=params)
        else:
            body = await self._fetch_text(
                f"{self.base_url}{_API_PATH}", params=params, context="search"
            )
        if body is None:
            return None
        # Decoding and reading a page of 1000 entries is CPU work
        return await asyncio.to_thread(self._read_page, body, page)

    def _read_page(self, body: str, page: int) -> _Page | None:
        data = self._parse_json_text(body, context="search")
        if data is None:
            return None
        result = data.get("result")
        if data.get("err") or not isinstance(result, dict):
            self._log.warning(
                f"{self.name}_api_error", error=str(data.get("err"))[:200]
            )
            return None
        entries = [e for e in result.get("results") or () if isinstance(e, dict)]
        info = result.get("queryInfo")
        total = info.get("totalResults") if isinstance(info, dict) else None
        broadcasts = [b for b in map(_read_entry, entries) if b is not None]
        last = len(entries) < _PAGE_SIZE or (
            isinstance(total, int) and (page + 1) * _PAGE_SIZE >= total
        )
        return _Page(broadcasts, last)

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
        *,
        episode_ref: EpisodeRef | None = None,
    ) -> list[SearchResult]:
        """Search the mediatheken: films and documentaries, a series'
        episodes. With *season* only the requested episodes answer
        (``_located()``)."""
        if not query:
            return []
        wanted = None
        if category is not None:
            wanted = served_category(category, _CATEGORIES)
            if wanted is None:
                return []

        await self._ensure_client()
        limit = self.effective_max_results
        broadcasts: list[_Broadcast] = []
        results: list[SearchResult] = []
        for page in range(_MAX_PAGES):
            answer = await self._page(query, page)
            if answer is None:
                break
            broadcasts.extend(answer.broadcasts)
            results = _answer(broadcasts, query, wanted, season, episode, episode_ref)
            if answer.last or len(results) >= limit:
                break
        return results[:limit]


plugin = MediathekPlugin()
