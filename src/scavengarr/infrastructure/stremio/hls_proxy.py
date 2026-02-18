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
import time
from collections.abc import AsyncIterator
from urllib.parse import urljoin, urlparse

import httpx
import structlog

log = structlog.get_logger(__name__)

# Short-TTL manifest cache (60 seconds) — prevents re-fetching the
# same master/variant playlist on rapid segment requests.
_MANIFEST_CACHE_TTL = 60
_manifest_cache: dict[str, tuple[bytes, str, float]] = {}

# Global semaphore for CDN proxy fetches (prevents stampede).
_CDN_SEMAPHORE = asyncio.Semaphore(50)


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


def rewrite_manifest(content: str, cdn_base: str, proxy_base: str) -> str:
    """Replace absolute CDN URLs with proxy URLs in an HLS manifest.

    Only rewrites lines that start with the *cdn_base* (absolute CDN
    URLs).  Relative URLs (``index-v1-a1.m3u8?t=...``) are left as-is
    because they resolve against the proxy URL naturally.

    Query parameters (auth tokens) are preserved.
    """
    lines: list[str] = []
    for line in content.splitlines(keepends=True):
        stripped = line.strip()
        # Skip comment/tag lines — only rewrite URI lines
        if stripped and not stripped.startswith("#") and stripped.startswith(cdn_base):
            line = line.replace(cdn_base, proxy_base, 1)
        lines.append(line)
    return "".join(lines)


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
            headers=headers,
            follow_redirects=True,
            timeout=15.0,
        )
        resp.raise_for_status()

    ct = resp.headers.get("content-type", "application/octet-stream")
    body = resp.content

    # Cache manifests (small text files, not segments)
    if ".m3u8" in url or "mpegurl" in ct.lower():
        _manifest_cache[url] = (
            body,
            ct,
            time.monotonic() + _MANIFEST_CACHE_TTL,
        )

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
                headers=headers,
            ),
            stream=True,
            follow_redirects=True,
        )
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
    """
    url = urljoin(cdn_base, path)
    if query_string:
        url = f"{url}?{query_string}"
    return url
