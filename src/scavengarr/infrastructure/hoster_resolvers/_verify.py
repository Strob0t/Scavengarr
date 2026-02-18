"""Shared HEAD-check verification for hoster-resolved video URLs."""

from __future__ import annotations

import httpx
import structlog

log = structlog.get_logger(__name__)


async def verify_video_url(
    http_client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    hoster: str,
) -> bool:
    """HEAD-check a CDN URL to verify it is accessible.

    Returns ``True`` when the CDN responds with 200 or 206 (partial
    content — common for video byte-range servers).  Logs a warning
    and returns ``False`` for any other status or network error.

    Parameters
    ----------
    http_client:
        Shared async HTTP client.
    url:
        The video CDN URL to check.
    headers:
        Playback headers (e.g. ``Referer``) required by the CDN.
    hoster:
        Hoster name used as prefix in structured log events
        (e.g. ``"voe"`` → ``"voe_video_head_failed"``).
    """
    try:
        resp = await http_client.head(
            url,
            headers=headers,
            follow_redirects=True,
            timeout=8.0,
        )
        if resp.status_code in (200, 206):
            return True
        log.warning(
            f"{hoster}_video_head_failed",
            status=resp.status_code,
            url=url[:120],
        )
        return False
    except httpx.HTTPError:
        log.warning(f"{hoster}_video_verify_error", url=url[:120])
        return False
