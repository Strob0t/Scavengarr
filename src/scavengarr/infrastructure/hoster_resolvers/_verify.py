"""Shared checks that hoster-resolved video URLs are reachable and playable."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream
from scavengarr.infrastructure.plugins.constants import DEFAULT_USER_AGENT

log = structlog.get_logger(__name__)


_ERROR_PAGE_NAMES = frozenset({"404", "error", "errors"})


def is_error_redirect(url: str) -> bool:
    """True when a hoster redirected to its error page (``/404``, ``/error``).

    Looks at whole path segments (without extension) and query keys only:
    a plain substring test flags files and titles such as
    "The.Terror.S01E01.mkv" as dead.
    """
    parsed = urlparse(url)
    segments = (s.rsplit(".", 1)[0].lower() for s in parsed.path.split("/") if s)
    if any(s in _ERROR_PAGE_NAMES for s in segments):
        return True
    return any(k.lower() in _ERROR_PAGE_NAMES for k in parse_qs(parsed.query))


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


# Bytes read from a resolved URL: enough for a playlist header or a
# container signature, small enough to be cheap on servers ignoring Range.
_SNIFF_BYTES = 1024
_PLAYBACK_CHECK_TIMEOUT_S = 6.0


async def check_playable(
    http_client: httpx.AsyncClient,
    stream: ResolvedStream,
) -> bool:
    """Check that a resolved stream answers like a player expects.

    Fetches the first bytes with the stream's playback headers and, like
    the player (Stremio's ``proxyHeaders``), a browser User-Agent: CDNs
    such as mixdrop's answer other agents with 403.  An error status, an
    HTML page, or (for HLS) a body that is no playlist means the player
    would fail, so the stream is not playable.
    """
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        **stream.headers,
        "Range": f"bytes=0-{_SNIFF_BYTES - 1}",
    }
    try:
        async with http_client.stream(
            "GET",
            stream.video_url,
            headers=headers,
            follow_redirects=True,
            timeout=_PLAYBACK_CHECK_TIMEOUT_S,
        ) as resp:
            head = b""
            if resp.status_code < 400:
                async for chunk in resp.aiter_bytes():
                    head += chunk
                    if len(head) >= _SNIFF_BYTES:
                        break
            content_type = resp.headers.get("content-type", "").lower()
            status = resp.status_code
    except httpx.HTTPError as exc:
        log.info("playback_check_error", url=stream.video_url[:120], error=str(exc))
        return False

    head = head.lstrip()
    if status >= 400:
        reason = f"status {status}"
    elif "text/html" in content_type or head[:1] == b"<":
        reason = "html"
    elif stream.is_hls and not head.startswith(b"#EXTM3U"):
        reason = "no playlist"
    else:
        return True
    log.info("playback_check_failed", url=stream.video_url[:120], reason=reason)
    return False
