"""Hoster embed URL liveness probe.

Performs lightweight GET requests to detect dead/deleted/removed files
before showing streams to the user. Used at /stream time to filter
out non-functional links.

Two-phase hybrid probe:
  Phase 1 — httpx (fast): classifies URLs as ALIVE, DEAD, or CLOUDFLARE.
  Phase 2 — Playwright Stealth (slow, only for CF-blocked URLs).

Offline markers compiled from JDownloader2 hoster plugins, our own
resolver implementations, and E2E testing across VOE, DoodStream,
Streamtape, SuperVideo, Filemoon, Katfile, DDownload, Rapidgator,
and Filer.net.
"""

from __future__ import annotations

import asyncio
import enum

import httpx
import structlog

from scavengarr.infrastructure.hoster_resolvers.cloudflare import (
    is_cloudflare_challenge,
)

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


class _ProbeOutcome(enum.Enum):
    """Three-state classification of a single probe request."""

    ALIVE = "alive"
    DEAD = "dead"
    CLOUDFLARE = "cloudflare"


def _is_error_redirect(final_url: str) -> bool:
    """Detect redirects to error/404 pages (Katfile, DDownload, Rapidgator)."""
    return "/404" in final_url or "/error" in final_url


async def _probe_url_classified(
    http: httpx.AsyncClient,
    url: str,
    *,
    timeout: float = 10,
) -> _ProbeOutcome:
    """Classify a URL as ALIVE, DEAD, or CLOUDFLARE.

    This is the internal classifier used by the hybrid probe.
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
        return _ProbeOutcome.DEAD

    # Check for Cloudflare challenge BEFORE other status checks
    if is_cloudflare_challenge(resp.status_code, resp.text):
        log.debug("probe_cloudflare_detected", url=url, status=resp.status_code)
        return _ProbeOutcome.CLOUDFLARE

    if resp.status_code in _OFFLINE_STATUS_CODES:
        log.debug("probe_offline_status", url=url, status=resp.status_code)
        return _ProbeOutcome.DEAD

    if resp.status_code != 200:
        log.debug("probe_unexpected_status", url=url, status=resp.status_code)
        return _ProbeOutcome.DEAD

    # Check for error redirect (final URL after following redirects)
    if _is_error_redirect(str(resp.url)):
        log.debug("probe_error_redirect", url=url, final_url=str(resp.url))
        return _ProbeOutcome.DEAD

    # Check HTML content for offline markers
    html = resp.text
    for marker in _OFFLINE_MARKERS:
        if marker in html:
            log.debug("probe_offline_marker", url=url, marker=marker)
            return _ProbeOutcome.DEAD

    return _ProbeOutcome.ALIVE


async def probe_url(
    http: httpx.AsyncClient,
    url: str,
    *,
    timeout: float = 10,
) -> bool:
    """Check if a hoster embed URL is likely alive (httpx-only).

    Thin wrapper around ``_probe_url_classified``; returns ``True``
    only for ALIVE, treating CLOUDFLARE as dead (conservative).
    """
    outcome = await _probe_url_classified(http, url, timeout=timeout)
    return outcome == _ProbeOutcome.ALIVE


async def probe_urls(
    http: httpx.AsyncClient,
    urls: list[tuple[int, str]],
    *,
    concurrency: int = 10,
    timeout: float = 10,
) -> set[int]:
    """Probe multiple URLs in parallel, return set of alive indices.

    httpx-only variant. For Cloudflare bypass use ``probe_urls_stealth``.
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


# ------------------------------------------------------------------
# Hybrid probe (httpx + Playwright Stealth)
# ------------------------------------------------------------------


async def probe_urls_stealth(
    http: httpx.AsyncClient,
    urls: list[tuple[int, str]],
    *,
    stealth_pool: object | None = None,
    concurrency: int = 10,
    stealth_concurrency: int = 3,
    timeout: float = 10,
    stealth_timeout: float = 15,
) -> set[int]:
    """Two-phase hybrid probe with Cloudflare bypass.

    Phase 1: httpx batch — fast classification (ALIVE / DEAD / CLOUDFLARE).
    Phase 2: Playwright Stealth — only for CLOUDFLARE URLs.

    If *stealth_pool* is ``None``, gracefully degrades to httpx-only
    (CLOUDFLARE URLs are treated as dead).

    Signature matches ``ProbeCallback`` so it can be used via
    ``functools.partial`` as a drop-in replacement for ``probe_urls``.
    """
    if not urls:
        return set()

    # Phase 1: httpx classification
    semaphore = asyncio.Semaphore(concurrency)
    alive_indices: set[int] = set()
    cf_urls: list[tuple[int, str]] = []

    async def _classify(idx: int, url: str) -> None:
        async with semaphore:
            outcome = await _probe_url_classified(http, url, timeout=timeout)
            if outcome == _ProbeOutcome.ALIVE:
                alive_indices.add(idx)
            elif outcome == _ProbeOutcome.CLOUDFLARE:
                cf_urls.append((idx, url))
            # DEAD: do nothing, idx stays out

    tasks = [_classify(idx, url) for idx, url in urls]
    await asyncio.gather(*tasks)

    if not cf_urls:
        return alive_indices

    # Phase 2: Playwright Stealth for CF-blocked URLs
    if stealth_pool is None:
        log.info(
            "probe_stealth_skipped",
            cf_count=len(cf_urls),
            reason="no stealth pool",
        )
        return alive_indices

    stealth_sem = asyncio.Semaphore(stealth_concurrency)

    async def _stealth_probe(idx: int, url: str) -> None:
        async with stealth_sem:
            alive = await stealth_pool.probe_url(  # type: ignore[union-attr]
                url, timeout=stealth_timeout
            )
            if alive:
                alive_indices.add(idx)

    log.info("probe_stealth_phase2", cf_count=len(cf_urls))
    stealth_tasks = [_stealth_probe(idx, url) for idx, url in cf_urls]
    await asyncio.gather(*stealth_tasks)

    return alive_indices
