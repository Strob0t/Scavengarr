"""Hoster embed URL liveness probe.

Performs lightweight GET requests to detect dead/deleted/removed files
before showing streams to the user. Used at /stream time to filter
out non-functional links.

Offline markers compiled from JDownloader2 hoster plugins, our own
resolver implementations, and E2E testing across VOE, DoodStream,
Streamtape, SuperVideo, Filemoon, Katfile, DDownload, Rapidgator,
and Filer.net.
"""

from __future__ import annotations

import asyncio

import httpx
import structlog

log = structlog.get_logger(__name__)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.5",
}

# HTTP status codes that definitively indicate an offline file.
# 403 is excluded — Cloudflare challenges return 403 but the video may
# still be alive behind the challenge.
_OFFLINE_STATUS_CODES: frozenset[int] = frozenset({404, 410, 500})

# Strings in the GET response body that indicate a dead/deleted/removed file.
# Order: generic XFS patterns first, then hoster-specific markers.
_OFFLINE_MARKERS: tuple[str, ...] = (
    # --- Generic XFileSharingPro patterns ---
    "File Not Found",
    "file was deleted",
    "file was removed",
    "The file expired",
    "File is gone",
    "File unavailable",
    "This file is not available",
    "Video not found",
    "video you are looking for is not found",
    "Not Found</h1>",
    # --- DoodStream ---
    '<iframe src="/e/"/>',  # Empty embed iframe = deleted video
    "Oops! Sorry</h1>",  # DoodStream 404 page heading
    "File you are looking for is not found",
    # --- SuperVideo (XFS) ---
    'class="fake-signup"',  # Soft-404: signup wall on dead files
    # --- Katfile ---
    "/404-remove",  # Explicit removal redirect marker in page
    "The file was deleted by its owner",
    # --- DDownload ---
    "file was banned by copyright",  # DMCA takedown
    "This server is in maintenance mode",
    "The file was deleted",
    # --- Rapidgator ---
    ">404 File not found",  # Rapidgator-style 404 within 200 page
    # --- VOE ---
    "Server overloaded, download temporary disabled",
    "Access to this file has been temporarily restricted",
    # --- Streamtape ---
    ">Video not found",  # Streamtape offline marker
)


def _is_error_redirect(final_url: str) -> bool:
    """Detect redirects to error/404 pages (Katfile, DDownload, Rapidgator)."""
    return "/404" in final_url or "/error" in final_url


async def probe_url(
    http: httpx.AsyncClient,
    url: str,
    *,
    timeout: float = 10,
) -> bool:
    """Check if a hoster embed URL is likely alive.

    Performs a single GET request and inspects both the HTTP status code
    and the response body for known offline markers.

    Returns True if the URL appears to host a valid video.
    Returns False if the URL is dead, deleted, or redirects to an error page.
    """
    try:
        resp = await http.get(
            url,
            follow_redirects=True,
            timeout=timeout,
            headers={**_BROWSER_HEADERS, "Referer": url},
        )
    except httpx.HTTPError:
        log.debug("probe_http_error", url=url)
        return False

    if resp.status_code in _OFFLINE_STATUS_CODES:
        log.debug("probe_offline_status", url=url, status=resp.status_code)
        return False

    if resp.status_code != 200:
        log.debug("probe_unexpected_status", url=url, status=resp.status_code)
        return False

    # Check for error redirect (final URL after following redirects)
    if _is_error_redirect(str(resp.url)):
        log.debug("probe_error_redirect", url=url, final_url=str(resp.url))
        return False

    # Check HTML content for offline markers
    html = resp.text
    for marker in _OFFLINE_MARKERS:
        if marker in html:
            log.debug("probe_offline_marker", url=url, marker=marker)
            return False

    return True


async def probe_urls(
    http: httpx.AsyncClient,
    urls: list[tuple[int, str]],
    *,
    concurrency: int = 10,
    timeout: float = 10,
) -> set[int]:
    """Probe multiple URLs in parallel, return set of alive indices.

    Args:
        http: Shared httpx client.
        urls: List of (index, url) tuples to probe.
        concurrency: Max parallel probes (semaphore limit).
        timeout: Per-URL timeout in seconds.

    Returns:
        Set of indices whose URLs are alive (probe_url returned True).
    """
    if not urls:
        return set()

    semaphore = asyncio.Semaphore(concurrency)

    async def _probe_one(idx: int, url: str) -> tuple[int, bool]:
        async with semaphore:
            alive = await probe_url(http, url, timeout=timeout)
            return idx, alive

    tasks = [_probe_one(idx, url) for idx, url in urls]
    results = await asyncio.gather(*tasks)

    return {idx for idx, alive in results if alive}
