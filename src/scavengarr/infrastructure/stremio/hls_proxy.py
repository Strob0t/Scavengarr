"""HLS proxy helpers — manifest rewriting and CDN fetching.

When a CDN requires ``Referer`` (or other headers) on **all** HLS
sub-requests (variant playlists, ``.ts`` segments), Stremio's built-in
``proxyHeaders`` is insufficient because it only applies the header to
the initial manifest fetch.  The HLS proxy endpoint solves this by
fetching each resource server-side with the correct headers and
rewriting absolute CDN URLs in manifests so the HLS player fetches
subsequent resources through the proxy as well.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator
from urllib.parse import urljoin, urlparse, urlsplit

import httpx
import structlog

from scavengarr.infrastructure.plugins.constants import DEFAULT_USER_AGENT

log = structlog.get_logger(__name__)

# Short-TTL manifest cache (60 seconds) — prevents re-fetching the
# same master/variant playlist on rapid segment requests.
_MANIFEST_CACHE_TTL = 60
# Entries expire but are only removed when full: rotating manifest URLs
# (tokens in the query) would otherwise pile up forever
_MANIFEST_CACHE_MAX = 512
_manifest_cache: dict[str, tuple[bytes, str, float]] = {}

# Global semaphore for CDN proxy fetches (prevents stampede).
_CDN_SEMAPHORE = asyncio.Semaphore(50)

# URI attribute of an HLS tag (EXT-X-MEDIA, EXT-X-KEY, EXT-X-MAP, …)
_URI_ATTR_RE = re.compile(r'URI="([^"]*)"')


def cdn_base_from_url(video_url: str) -> str:
    """Extract the CDN base directory from a video URL.

    >>> cdn_base_from_url("https://ds7.dropcdn.io/hls2/01/00017/yw6c47u0v5nb_h/master.m3u8?t=abc")
    'https://ds7.dropcdn.io/hls2/01/00017/yw6c47u0v5nb_h/'
    """
    parsed = urlparse(video_url)
    # Everything up to and including the last '/' in the path
    path = parsed.path
    last_slash = path.rfind("/")
    if last_slash >= 0:
        base_path = path[: last_slash + 1]
    else:
        base_path = "/"
    return f"{parsed.scheme}://{parsed.netloc}{base_path}"


def _proxy_uri(uri: str, cdn_base: str, proxy_base: str) -> str:
    """*uri* as a proxy URL when it points at the stream's CDN, else as is.

    Relative URIs stay: they resolve against the proxy URL of the playlist
    that lists them, which mirrors the CDN path. A URI from the CDN's root
    (``/secure/…``, ``//host/…`` or an absolute URL outside *cdn_base*)
    becomes ``<proxy_base>/<path>``; the proxy joins that absolute path
    with the CDN origin (``build_cdn_url``). Other origins stay direct:
    the proxy only fetches from the stream's own CDN.
    """
    if uri.startswith(cdn_base):
        return proxy_base + uri[len(cdn_base) :]
    base = urlsplit(cdn_base)
    if uri.startswith("//"):
        target = urlsplit(f"{base.scheme}:{uri}")
    elif uri.startswith("/"):
        target = urlsplit(f"{base.scheme}://{base.netloc}{uri}")
    else:
        target = urlsplit(uri)
        if target.scheme not in ("http", "https"):
            return uri  # relative
    if (target.scheme, target.netloc) != (base.scheme, base.netloc):
        return uri
    query = f"?{target.query}" if target.query else ""
    return f"{proxy_base}{target.path}{query}"


def rewrite_manifest(content: str, cdn_base: str, proxy_base: str) -> str:
    """Point the CDN URIs of an HLS manifest at the proxy.

    Rewrites URI lines and the ``URI="…"`` attributes of tags (audio
    renditions, keys, init segments) with ``_proxy_uri``. Query parameters
    (auth tokens) and line endings are preserved.
    """
    lines: list[str] = []
    for line in content.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("#"):
            line = _URI_ATTR_RE.sub(
                lambda m: f'URI="{_proxy_uri(m.group(1), cdn_base, proxy_base)}"',
                line,
            )
        elif stripped:
            line = line.replace(stripped, _proxy_uri(stripped, cdn_base, proxy_base), 1)
        lines.append(line)
    return "".join(lines)


def _cache_manifest(url: str, body: bytes, content_type: str) -> None:
    """Cache a manifest; when full, drop expired entries, then the oldest."""
    now = time.monotonic()
    if len(_manifest_cache) >= _MANIFEST_CACHE_MAX:
        expired = [k for k, (_, _, exp) in _manifest_cache.items() if exp <= now]
        for key in expired:
            del _manifest_cache[key]
        while len(_manifest_cache) >= _MANIFEST_CACHE_MAX:
            del _manifest_cache[next(iter(_manifest_cache))]
    _manifest_cache[url] = (body, content_type, now + _MANIFEST_CACHE_TTL)


def _player_headers(headers: dict[str, str]) -> dict[str, str]:
    """The stream's headers with the player's (browser) User-Agent.

    The proxy fetches on the player's behalf; CDNs such as mixdrop's answer
    other agents (the app's own) with 403.
    """
    return {"User-Agent": DEFAULT_USER_AGENT, **headers}


async def fetch_hls_resource(
    http_client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
) -> tuple[bytes, str]:
    """Fetch an HLS resource (manifest or segment) with headers.

    Returns ``(body_bytes, content_type)``.
    Raises ``httpx.HTTPStatusError`` on non-2xx responses.

    Manifests are cached for 60 seconds to avoid re-fetching on
    rapid segment requests.  Acquires the global CDN semaphore to
    prevent connection stampedes from concurrent viewers.
    """
    # Check manifest cache for .m3u8 URLs
    cached = _manifest_cache.get(url)
    if cached is not None:
        body, ct, expires = cached
        if time.monotonic() < expires:
            return body, ct
        del _manifest_cache[url]

    async with _CDN_SEMAPHORE:
        resp = await http_client.get(
            url,
            headers=_player_headers(headers),
            follow_redirects=True,
            timeout=15.0,
        )
        resp.raise_for_status()

    ct = resp.headers.get("content-type", "application/octet-stream")
    body = resp.content

    # Cache manifests (small text files, not segments)
    if ".m3u8" in url or "mpegurl" in ct.lower():
        _cache_manifest(url, body, ct)

    return body, ct


async def stream_hls_segment(
    http_client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
) -> tuple[AsyncIterator[bytes], str]:
    """Stream an HLS segment from CDN without buffering full body.

    Returns ``(byte_iterator, content_type)``.
    Raises ``httpx.HTTPStatusError`` on non-2xx responses.

    Uses ``httpx.stream()`` so that segment bytes flow through the
    proxy without loading the entire 2-10 MB segment into memory.
    """
    async with _CDN_SEMAPHORE:
        resp = await http_client.send(
            http_client.build_request(
                "GET",
                url,
                headers=_player_headers(headers),
            ),
            stream=True,
            follow_redirects=True,
        )
        if resp.is_error:
            # Streamed responses hold their pooled connection until closed
            await resp.aclose()
        resp.raise_for_status()

    ct = resp.headers.get("content-type", "application/octet-stream")

    async def _iter() -> AsyncIterator[bytes]:
        try:
            async for chunk in resp.aiter_bytes(chunk_size=65536):
                yield chunk
        finally:
            await resp.aclose()

    return _iter(), ct


def build_cdn_url(cdn_base: str, path: str, query_string: str = "") -> str:
    """Build a full CDN URL from the base, a sub-path, and optional query.

    Uses ``urljoin`` so that absolute paths (``/foo/bar``) and relative
    paths (``seg-1-v1-a1.ts``) both resolve correctly.

    Raises ``ValueError`` when the result leaves the CDN (other scheme or
    host): *path* comes from the client, and ``urljoin`` lets an absolute
    or ``//host`` path replace the host, which would turn the proxy into
    an open proxy into the server's network.
    """
    url = urljoin(cdn_base, path)
    base, target = urlparse(cdn_base), urlparse(url)
    if (target.scheme, target.netloc) != (base.scheme, base.netloc):
        raise ValueError(f"path leaves the stream's CDN: {path[:80]}")
    if query_string:
        url = f"{url}?{query_string}"
    return url
