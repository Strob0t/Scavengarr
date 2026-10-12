"""VidHide segments behind the HLS proxy: which headers the CDN wants.

    probe vidhide_segments [--hoster vidhide] [--stream-id ID | --imdb ID]
                           [--no-fresh]

The newest stored HLS link of the hoster (the Redis backend: the probe scans
the stream link keys; ``--stream-id`` names one, ``--imdb`` asks the app
at ``http://127.0.0.1:$PORT`` for a title's streams first) is fetched the
way the HLS proxy fetches it: the stream URL, its first variant playlist,
that playlist's first segment, each built as the proxy builds them (the
stream URL's query on a URL without one; a segment on another host is not
proxied: the player fetches it from its own address). Unless
``--no-fresh``, the link's hoster URL is also resolved again with the
XFS resolver, and its stream goes through the same chain.

Every chain runs once per header variant: the stored headers with the
player's User-Agent (what the proxy sends), the Referer with the
resolver's User-Agent, Referer and Origin with the player's, the Referer
alone, the player's User-Agent alone, and no headers. Per variant the
probe prints status and content type of the stream URL, the variant
playlist and the segment (the segment also as the playlist lists it, when
that differs from the proxy's URL). Prints hosts only, no URLs or tokens;
it changes nothing but the app's caches with ``--imdb``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
import structlog

from scavengarr.domain.entities.stremio import CachedStreamLink
from scavengarr.infrastructure.cache.cache_factory import create_cache
from scavengarr.infrastructure.config import load_config
from scavengarr.infrastructure.hoster_resolvers.xfs import VIDHIDE, XFSResolver
from scavengarr.infrastructure.persistence.stream_link_cache import (
    CacheStreamLinkRepository,
)
from scavengarr.infrastructure.plugins.constants import DEFAULT_USER_AGENT
from scavengarr.infrastructure.stremio.hls_proxy import (
    _proxy_uri,  # pyright: ignore[reportPrivateUsage]
    build_cdn_url,
    cdn_base_from_url,
)

# The User-Agent the XFS resolver fetches the embed page with
RESOLVER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
# A stand-in proxy base: a rewritten URI starts with it
_MARK = "proxy:/"
_STREAM_ID_RE = re.compile(r"/(?:proxy|play)/([^/?#]+)")


def host(url: str) -> str:
    return urlsplit(url).hostname or "?"


def header_variants(stored: dict[str, str]) -> dict[str, dict[str, str]]:
    """The header sets to try, by name; ``stored`` holds the link's headers."""
    referer = stored.get("Referer", "")
    origin = ""
    if referer:
        parts = urlsplit(referer)
        origin = f"{parts.scheme}://{parts.netloc}"
    variants = {
        "proxy (stored + player UA)": {"User-Agent": DEFAULT_USER_AGENT, **stored},
        "referer + resolver UA": {"User-Agent": RESOLVER_UA, "Referer": referer},
        "referer + origin + player UA": {
            "User-Agent": DEFAULT_USER_AGENT,
            "Referer": referer,
            "Origin": origin,
        },
        "referer only": {"Referer": referer},
        "player UA only": {"User-Agent": DEFAULT_USER_AGENT},
        "no headers": {},
    }
    if not referer:
        variants = {k: v for k, v in variants.items() if "referer" not in k}
    return variants


def first_uri(playlist: str) -> str:
    """The first URI line of a playlist (variant or segment)."""
    for line in playlist.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return ""


def is_master(playlist: str) -> bool:
    return "#EXT-X-STREAM-INF" in playlist


def origin(url: str) -> str:
    """Scheme, host and port of *url*: what decides whether it is proxied."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.hostname}:{parts.port or '-'}"


def uri_shape(uri: str, stream_url: str) -> str:
    """How a playlist names *uri*, without the URI itself."""
    if uri.startswith("//"):
        shape = "scheme-relative"
    elif uri.startswith("/"):
        shape = "root-relative"
    elif urlsplit(uri).scheme:
        shape = f"absolute {urlsplit(uri).scheme}"
    else:
        shape = "relative"
    under = urljoin(stream_url, uri).startswith(cdn_base_from_url(stream_url))
    return f"{shape} URI, {'under' if under else 'outside'} the stream's directory"


def proxy_target(
    uri: str, playlist_dir: str, stream_url: str
) -> tuple[str, str | None]:
    """``(url, proxy path)``: where the proxy fetches *uri* of a playlist.

    The proxy's own rewrite and URL building: a URI it rewrites is fetched
    from the CDN with the stream URL's query when the URI carries none; the
    proxy path (``None`` for a URI it leaves alone, which the player
    fetches itself) gives the next playlist's directory.
    """
    cdn_base = cdn_base_from_url(stream_url)
    rewritten = _proxy_uri(uri, cdn_base, _MARK, playlist_dir)
    if not rewritten.startswith(_MARK):
        return urljoin(stream_url, uri), None
    path, _, query = rewritten[len(_MARK) :].partition("?")
    query = query or urlsplit(stream_url).query
    return build_cdn_url(cdn_base, path, query), path


async def _fetch(
    http: httpx.AsyncClient, url: str, headers: dict[str, str], *, body: bool
) -> tuple[str, str]:
    """``(status, body or "")`` of a GET; the status names the content type."""
    try:
        async with http.stream("GET", url, headers=headers) as resp:
            ctype = resp.headers.get("content-type", "-").split(";")[0]
            text = ""
            if body and resp.status_code == 200:
                text = (await resp.aread()).decode("utf-8", errors="replace")
            return f"{resp.status_code} {ctype}", text
    except httpx.HTTPError as exc:
        return type(exc).__name__, ""


async def run_chain(
    http: httpx.AsyncClient, stream_url: str, headers: dict[str, str]
) -> list[str]:
    """The proxy's fetch chain under *headers*; one ``part: status`` each."""
    out: list[str] = []
    status, text = await _fetch(http, stream_url, headers, body=True)
    out.append(f"stream {status}")
    playlist_url, playlist_dir = stream_url, ""
    if text and is_master(text):
        variant, path = proxy_target(first_uri(text), "", stream_url)
        playlist_url = variant
        if path is not None:
            playlist_dir = path[: path.rfind("/") + 1]
        status, text = await _fetch(http, variant, headers, body=True)
        out.append(f"variant {status}")
    uri = first_uri(text) if text else ""
    if not uri:
        return out
    segment, path = proxy_target(uri, playlist_dir, stream_url)
    where = f"proxied, {uri_shape(uri, stream_url)}"
    if path is None:
        where = (
            f"direct, {uri_shape(uri, stream_url)}: {origin(segment)}"
            f" vs stream {origin(stream_url)}"
        )
    status, _ = await _fetch(http, segment, headers, body=False)
    out.append(f"segment ({where}) {status}")
    listed = urljoin(playlist_url, uri)
    if listed != segment:
        status, _ = await _fetch(http, listed, headers, body=False)
        out.append(f"segment as listed {status}")
    return out


async def _stored_ids(redis_url: str) -> list[str]:
    import redis.asyncio as aioredis

    client = aioredis.from_url(redis_url)
    try:
        return sorted(
            [
                key.decode().rsplit("streamlink:", 1)[1]
                async for key in client.scan_iter(match="*streamlink:*", count=500)
            ]
        )
    finally:
        await client.aclose()


async def _request_streams(imdb: str) -> list[str]:
    """Ask the app for the title's streams; the stream ids of its answer."""
    kind = "series" if ":" in imdb else "movie"
    base = "http://127.0.0.1:" + os.environ.get("PORT", "7979")
    async with httpx.AsyncClient(timeout=120) as http:
        answer = await http.get(f"{base}/api/v1/stremio/stream/{kind}/{imdb}.json")
    answer.raise_for_status()
    ids = []
    for stream in answer.json().get("streams", []):
        match = _STREAM_ID_RE.search(str(stream.get("url", "")))
        if match:
            ids.append(match.group(1))
    print(f"the app answered {len(ids)} streams for {imdb}")
    return ids


async def _pick_link(args: argparse.Namespace) -> CachedStreamLink:
    env = os.getenv("SCAVENGARR_CONFIG")
    config = load_config(config_path=Path(env) if env else None)
    cache = create_cache(
        backend=config.cache.backend,
        directory=str(config.cache.directory),
        redis_url=config.cache.redis_url,
    )
    async with cache:
        repo = CacheStreamLinkRepository(cache)
        if args.stream_id:
            ids = [args.stream_id]
        elif args.imdb:
            ids = await _request_streams(args.imdb)
        elif config.cache.backend == "redis":
            ids = await _stored_ids(config.cache.redis_url)
        else:
            sys.exit("pass --stream-id or --imdb: only Redis can list the links")
        wanted = args.hoster.lower()
        links = [await repo.get(stream_id) for stream_id in ids]
    candidates = [
        link
        for link in links
        if link and link.is_hls and link.video_url and wanted in link.hoster.lower()
    ]
    if not candidates:
        sys.exit(f"no stored HLS link of {args.hoster} among {len(ids)} links")
    return max(candidates, key=lambda link: link.resolved_at)


async def _probe(
    http: httpx.AsyncClient, label: str, url: str, stored: dict[str, str]
) -> None:
    referer = host(stored.get("Referer", ""))
    print(f"\n## {label}: stream on {host(url)}, Referer {referer}")
    for name, headers in header_variants(stored).items():
        print(f"- {name}: " + " · ".join(await run_chain(http, url, headers)))


async def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="vidhide_segments")
    parser.add_argument("--hoster", default="vidhide")
    pick = parser.add_mutually_exclusive_group()
    pick.add_argument("--stream-id")
    pick.add_argument("--imdb")
    parser.add_argument("--no-fresh", action="store_true")
    args = parser.parse_args(argv)
    link = await _pick_link(args)
    stored = json.loads(link.video_headers) if link.video_headers else {}
    age = (time.time() - link.resolved_at) / 60 if link.resolved_at else 0
    print(
        f"# vidhide_segments: {link.hoster} · {link.title[:60]} · hoster"
        f" {host(link.hoster_url)} · stored {age:.0f} min ago"
    )
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as http:
        await _probe(http, "stored", link.video_url, stored)
        if args.no_fresh:
            return
        resolved = await XFSResolver(VIDHIDE, http).resolve(link.hoster_url)
        if resolved is None:
            print("\n## fresh: the resolver found no stream")
            return
        await _probe(http, "fresh", resolved.video_url, dict(resolved.headers))


if __name__ == "__main__":
    # the cache, repository and resolver log at debug and info level
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING)
    )
    asyncio.run(main(sys.argv[1:]))
