"""devideosrc.co embed API, the stream source of several DLE streaming sites.

Sites like streamcloud and hdfilme embed a devideosrc player instead of
listing hoster links themselves: ``https://devideosrc.co/movie/<imdb>`` for
movies, ``https://devideosrc.co/serial/<imdb>`` for series. The player page
carries a signed token and loads the hoster embeds with::

    POST https://devideosrc.co/api/embed-links
    {"type": "movie" | "tv", "id": "<imdb>", "token": "<token>"}

Movies answer with ``sources`` (one entry per hoster), series with
``tv.seasons[].episodes[].sources``. No captcha is involved; only the separate
download embed (``/embed/download/<imdb>``) sits behind Turnstile.

Pacing and 429/503 backoff come from the shared HTTP client's
``RetryTransport``. The player page is always loaded past Cloudflare's
cache: cached copies live for hours, carry expired tokens (403 from
embed-links) and even cache 429 answers, which the transport would retry
in vain. A 429 on the player page is therefore retried here, each time
with a fresh URL.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx
import structlog

from scavengarr.infrastructure.plugins.episodes import episode_label

log = structlog.get_logger(__name__)

BASE_URL = "https://devideosrc.co"

PlayerKind = Literal["movie", "tv"]

_PAGE_ATTEMPTS = 3
_PAGE_RETRY_DELAY_S = 2.0  # x attempt number

_MOVIE_PLAYER_RE = re.compile(r"devideosrc\.co/movie/(tt\d+)")
_SERIAL_PLAYER_RE = re.compile(r"devideosrc\.co/serial/")
_IMDB_RE = re.compile(r"devideosrc\.co/(?:embed/download|serial)/(tt\d+)")
_IMDB_VAR_RE = re.compile(r"""var\s+imdb\s*=\s*['"](tt\d+)['"]""")
_TOKEN_RE = re.compile(r"""token:\s*["']([A-Za-z0-9_.=-]+)["']""")


@dataclass(frozen=True)
class DevideosrcPlayer:
    """A devideosrc player embedded on a site's detail page."""

    kind: PlayerKind
    imdb_id: str


def find_player(html: str) -> DevideosrcPlayer | None:
    """Detect the devideosrc player (movie or series) on a detail page."""
    movie = _MOVIE_PLAYER_RE.search(html)
    if movie:
        return DevideosrcPlayer(kind="movie", imdb_id=movie.group(1))
    if _SERIAL_PLAYER_RE.search(html):
        imdb = _IMDB_VAR_RE.search(html) or _IMDB_RE.search(html)
        if imdb:
            return DevideosrcPlayer(kind="tv", imdb_id=imdb.group(1))
    return None


def player_url(player: DevideosrcPlayer) -> str:
    path = "movie" if player.kind == "movie" else "serial"
    return f"{BASE_URL}/{path}/{player.imdb_id}"


def _hoster_name(source: dict[str, Any]) -> str:
    """``doodstream.com`` -> ``doodstream`` (falls back to the URL host)."""
    name = str(source.get("name") or "")
    if "." not in name:
        name = httpx.URL(str(source.get("url", ""))).host
    parts = name.removeprefix("www.").split(".")
    return parts[-2] if len(parts) >= 2 else name


def _source_rank(source: dict[str, Any]) -> int:
    """The site's rank of a source; unranked ones last."""
    rank = source.get("rank")
    return rank if isinstance(rank, int) else 99


def _source_links(sources: object, label_prefix: str = "") -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    if not isinstance(sources, list):
        return links
    ranked = sorted(
        (s for s in sources if isinstance(s, dict)),
        key=_source_rank,
    )
    for source in ranked:
        url = str(source.get("url") or "")
        if not url.startswith("http"):
            continue
        hoster = _hoster_name(source)
        links.append(
            {
                "hoster": hoster,
                "link": url,
                "label": f"{label_prefix}{hoster}",
            }
        )
    return links


def movie_links(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Hoster links of a movie ``embed-links`` answer (best rank first)."""
    return _source_links(payload.get("sources"))


def tv_links(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Hoster links of all episodes of a series ``embed-links`` answer.

    Labels start with ``<season>x<episode>`` (e.g. ``1x5 doodstream``).
    """
    tv = payload.get("tv")
    seasons = tv.get("seasons") if isinstance(tv, dict) else None
    links: list[dict[str, str]] = []
    if not isinstance(seasons, list):
        return links
    for season in seasons:
        if not isinstance(season, dict):
            continue
        s_num = _number(season.get("season_number"))
        episodes = season.get("episodes")
        if s_num is None or not isinstance(episodes, list):
            continue
        for ep in episodes:
            e_num = _number(ep.get("episode_number")) if isinstance(ep, dict) else None
            if e_num is None:
                continue
            prefix = episode_label(s_num, e_num, "")
            links.extend(_source_links(ep.get("sources"), prefix))
    return links


def _number(value: object) -> int | None:
    """A season/episode number of the API (an int, possibly sent as text)."""
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _uncached(url: str) -> str:
    """*url* with a unique query string, past Cloudflare's page cache."""
    return f"{url}?r={time.monotonic_ns()}"


async def _get_page(
    client: httpx.AsyncClient, page_url: str, **request_kwargs: Any
) -> httpx.Response:
    """GET the player page past the cache, retrying 429 with a fresh URL."""
    resp = await client.get(_uncached(page_url), **request_kwargs)
    for attempt in range(1, _PAGE_ATTEMPTS):
        if resp.status_code != 429:
            break
        log.debug("devideosrc_page_429", url=page_url, attempt=attempt)
        await asyncio.sleep(_PAGE_RETRY_DELAY_S * attempt)
        resp = await client.get(_uncached(page_url), **request_kwargs)
    resp.raise_for_status()
    return resp


@dataclass(frozen=True)
class PlayerLinks:
    """Hoster links of a player; *kind* is the player that answered."""

    kind: PlayerKind
    links: list[dict[str, str]]


async def _embed_links(
    client: httpx.AsyncClient, player: DevideosrcPlayer, **request_kwargs: Any
) -> dict[str, Any] | None:
    """Read the player page's token and POST it to ``/api/embed-links``.

    ``None`` when the page carries no token (devideosrc has no such title).
    """
    page_url = player_url(player)
    page = await _get_page(client, page_url, **request_kwargs)
    token = _TOKEN_RE.search(page.text)
    if token is None:
        log.debug("devideosrc_no_token", url=page_url)
        return None
    resp = await client.post(
        f"{BASE_URL}/api/embed-links",
        json={"type": player.kind, "id": player.imdb_id, "token": token.group(1)},
        **request_kwargs,
    )
    resp.raise_for_status()
    payload = resp.json()
    return payload if isinstance(payload, dict) else None


# Fetches in flight per player: hdfilme, streamcloud and streamkiste front one
# database and ask for the same player within the same request
_inflight: dict[DevideosrcPlayer, asyncio.Task[PlayerLinks]] = {}


async def fetch_links(
    client: httpx.AsyncClient,
    player: DevideosrcPlayer,
    **request_kwargs: Any,
) -> PlayerLinks:
    """Load *player*'s hoster links (empty ``links`` when unavailable).

    Some sites (streamkiste) embed the series player for movies too; when the
    series page has no token, the movie player is tried instead.
    *request_kwargs* (timeout, headers) are passed to every request.
    Concurrent calls for one player share one fetch (the first caller's
    client and *request_kwargs*).
    """
    task = _inflight.get(player)
    if task is None:
        task = asyncio.ensure_future(_fetch_links(client, player, **request_kwargs))
        _inflight[player] = task
        task.add_done_callback(lambda _: _inflight.pop(player, None))
    # A caller cut by its deadline must not cancel the fetch the others await
    return await asyncio.shield(task)


async def _fetch_links(
    client: httpx.AsyncClient,
    player: DevideosrcPlayer,
    **request_kwargs: Any,
) -> PlayerLinks:
    try:
        payload = await _embed_links(client, player, **request_kwargs)
        if payload is None and player.kind == "tv":
            player = DevideosrcPlayer(kind="movie", imdb_id=player.imdb_id)
            payload = await _embed_links(client, player, **request_kwargs)
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("devideosrc_fetch_failed", url=player_url(player), error=str(exc))
        return PlayerLinks(kind=player.kind, links=[])

    if payload is None or not payload.get("ok"):
        return PlayerLinks(kind=player.kind, links=[])
    links = movie_links(payload) if player.kind == "movie" else tv_links(payload)
    return PlayerLinks(kind=player.kind, links=links)
