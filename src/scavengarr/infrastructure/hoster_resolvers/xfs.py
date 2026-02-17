"""Generic XFileSharingPro (XFS) hoster resolver.

Consolidates 21 identical XFS-based hoster resolvers into a single
parameterised implementation.  Each hoster is described by an ``XFSConfig``
— name, domains, file-ID regex, and offline markers — while the resolution
logic (fetch page → check markers → check redirect) lives once in
``XFSResolver``.

Adding a new XFS hoster = adding a new ``XFSConfig`` constant + appending
it to ``ALL_XFS_CONFIGS``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality
from scavengarr.infrastructure.hoster_resolvers import extract_domain

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class XFSConfig:
    """Immutable configuration for one XFS-based hoster."""

    name: str
    domains: frozenset[str]
    file_id_re: re.Pattern[str]
    offline_markers: tuple[str, ...]


# ---------------------------------------------------------------------------
# File-ID extraction (stateless, testable)
# ---------------------------------------------------------------------------


def extract_xfs_file_id(url: str, config: XFSConfig) -> str | None:
    """Extract the XFS file ID from *url* using *config*.

    Returns ``None`` when the URL does not match the config's domains or
    file-ID regex.
    """
    try:
        domain = extract_domain(url)
        if domain not in config.domains:
            return None
        parsed = urlparse(url)
        match = config.file_id_re.search(parsed.path)
        return match.group(1) if match else None
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


class XFSResolver:
    """Generic resolver for XFileSharingPro-based hosters.

    Satisfies ``HosterResolverPort``.
    """

    def __init__(
        self,
        config: XFSConfig,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._config = config
        self._http = http_client

    @property
    def name(self) -> str:
        return self._config.name

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Validate an XFS link by fetching the file page.

        Checks for offline markers to determine if the file is still
        available.  Returns a ``ResolvedStream`` with the original URL on
        success.
        """
        hoster = self._config.name
        file_id = extract_xfs_file_id(url, self._config)
        if not file_id:
            log.warning(f"{hoster}_invalid_url", url=url)
            return None

        try:
            resp = await self._http.get(url, follow_redirects=True, timeout=15)
        except httpx.HTTPError:
            log.warning(f"{hoster}_request_failed", url=url)
            return None

        if resp.status_code != 200:
            log.warning(
                f"{hoster}_http_error",
                status=resp.status_code,
                url=url,
            )
            return None

        html = resp.text

        for marker in self._config.offline_markers:
            if marker in html:
                log.info(f"{hoster}_file_offline", file_id=file_id, marker=marker)
                return None

        final_url = str(resp.url)
        if "/404" in final_url or "error" in final_url:
            log.info(f"{hoster}_error_redirect", file_id=file_id, url=final_url)
            return None

        log.debug(f"{hoster}_resolved", file_id=file_id)
        return ResolvedStream(video_url=url, quality=StreamQuality.UNKNOWN)


# ---------------------------------------------------------------------------
# Shared regex patterns
# ---------------------------------------------------------------------------

_BASIC_RE = re.compile(r"^/([a-zA-Z0-9]{12})(?:/|$)")
_EMBED_RE = re.compile(r"^/(?:e/|d/|embed-)?([a-zA-Z0-9]{12})(?:/|$|\.html)")
_ED_RE = re.compile(r"^/(?:e/|d/)?([a-zA-Z0-9]{12})(?:/|$|\.html)")

_STANDARD_MARKERS = (
    "File Not Found",
    "file was removed",
    ">The file expired",
    ">The file was deleted",
)

_EXTENDED_MARKERS = (
    *_STANDARD_MARKERS,
    "File is gone",
    "File unavailable",
)


# ---------------------------------------------------------------------------
# Hoster configurations
# ---------------------------------------------------------------------------

KATFILE = XFSConfig(
    name="katfile",
    domains=frozenset({"katfile"}),
    file_id_re=_BASIC_RE,
    offline_markers=(
        "/404-remove",
        ">The file expired",
        ">The file was deleted by its owner",
        "File Not Found",
        "file was removed",
    ),
)

HEXUPLOAD = XFSConfig(
    name="hexupload",
    domains=frozenset({"hexupload"}),
    file_id_re=_BASIC_RE,
    offline_markers=_STANDARD_MARKERS,
)

CLICKNUPLOAD = XFSConfig(
    name="clicknupload",
    domains=frozenset({"clicknupload", "clickndownload"}),
    file_id_re=_BASIC_RE,
    offline_markers=_STANDARD_MARKERS,
)

FILESTORE = XFSConfig(
    name="filestore",
    domains=frozenset({"filestore"}),
    file_id_re=_BASIC_RE,
    offline_markers=_STANDARD_MARKERS,
)

UPTOBOX = XFSConfig(
    name="uptobox",
    domains=frozenset({"uptobox", "uptostream"}),
    file_id_re=_BASIC_RE,
    offline_markers=(
        "File Not Found",
        "File has been removed",
        "This file is deleted",
        "This page is not available",
    ),
)

FUNXD = XFSConfig(
    name="funxd",
    domains=frozenset({"funxd"}),
    file_id_re=_ED_RE,
    offline_markers=_STANDARD_MARKERS,
)

BIGWARP = XFSConfig(
    name="bigwarp",
    domains=frozenset({"bigwarp"}),
    file_id_re=_EMBED_RE,
    offline_markers=(
        *_EXTENDED_MARKERS,
        ">File is no longer available",
    ),
)

DROPLOAD = XFSConfig(
    name="dropload",
    domains=frozenset({"dropload"}),
    file_id_re=_EMBED_RE,
    offline_markers=_EXTENDED_MARKERS,
)

GOODSTREAM = XFSConfig(
    name="goodstream",
    domains=frozenset({"goodstream"}),
    file_id_re=_EMBED_RE,
    offline_markers=_EXTENDED_MARKERS,
)

SAVEFILES = XFSConfig(
    name="savefiles",
    domains=frozenset({"savefiles"}),
    file_id_re=_EMBED_RE,
    offline_markers=_EXTENDED_MARKERS,
)

STREAMWISH = XFSConfig(
    name="streamwish",
    domains=frozenset(
        {
            "streamwish",
            "dwish",
            "playerwish",
            "rapidplayers",
            "streamhg",
            "hlsflex",
            "swiftplayers",
            "davioad",
            "hglink",
            # JDownloader StreamwishCom.java + user list
            "obeywish",
            "awish",
            "embedwish",
            "wishembed",
            "wishonly",
            "cloudwish",
            "ultpreplayer",
            "recordplay",
            "hgplaycdn",
            "hailindihg",
            "auvexiug",
            "habetar",
            "kravaxxa",
            "zuvioeb",
            "tryzendm",
            "yuguaab",
            "xenolyzb",
            "guxhag",
            "dumbalag",
            "haxloppd",
        }
    ),
    file_id_re=_EMBED_RE,
    offline_markers=(
        *_EXTENDED_MARKERS,
        "This video has been locked watch or does not exist",
        "Video temporarily not available",
    ),
)

VIDMOLY = XFSConfig(
    name="vidmoly",
    domains=frozenset({"vidmoly"}),
    file_id_re=re.compile(r"^/(?:embed-|e/|d/|w/)?([a-zA-Z0-9]{12})(?:/|$|\.html)"),
    offline_markers=(
        *_EXTENDED_MARKERS,
        "/notice.php",
    ),
)

VIDOZA = XFSConfig(
    name="vidoza",
    domains=frozenset({"vidoza", "videzz"}),
    file_id_re=re.compile(r"^/(?:embed-)?([a-zA-Z0-9]{12})(?:/|$|\.html)"),
    offline_markers=(
        "File Not Found",
        "file was removed",
        "Reason for deletion:",
        "Conversion stage",
    ),
)

VINOVO = XFSConfig(
    name="vinovo",
    domains=frozenset({"vinovo"}),
    file_id_re=re.compile(r"^/(?:e/|d/)([a-zA-Z0-9]{12,})(?:/|$)"),
    offline_markers=(
        *_EXTENDED_MARKERS,
        "Video not found",
    ),
)

VIDHIDE = XFSConfig(
    name="vidhide",
    domains=frozenset(
        {
            "vidhide",
            "vidhidepro",
            "vidhidehub",
            "filelions",
            "vidhideplus",
            "vidhidefast",
        }
    ),
    file_id_re=re.compile(
        r"^/(?:embed-|embed/|e/|f/|v/|d/|file/)?([a-z0-9]{12})(?:/|$|\.html)"
    ),
    offline_markers=(
        "File Not Found",
        "file was removed",
        "Video embed restricted",
        "Downloads disabled",
    ),
)


STREAMRUBY = XFSConfig(
    name="streamruby",
    domains=frozenset({"streamruby"}),
    file_id_re=_EMBED_RE,
    offline_markers=_STANDARD_MARKERS,
)

VEEV = XFSConfig(
    name="veev",
    domains=frozenset({"veev"}),
    file_id_re=_ED_RE,
    offline_markers=_STANDARD_MARKERS,
)

LULUSTREAM = XFSConfig(
    name="lulustream",
    domains=frozenset({"lulustream", "luluvdo", "luluvid", "lulu", "luluvdoo", "cdn1"}),
    file_id_re=_EMBED_RE,
    offline_markers=_STANDARD_MARKERS,
)

UPSTREAM = XFSConfig(
    name="upstream",
    domains=frozenset({"upstream"}),
    file_id_re=_EMBED_RE,
    offline_markers=_STANDARD_MARKERS,
)

WOLFSTREAM = XFSConfig(
    name="wolfstream",
    domains=frozenset({"wolfstream"}),
    file_id_re=_EMBED_RE,
    offline_markers=_STANDARD_MARKERS,
)

VIDNEST = XFSConfig(
    name="vidnest",
    domains=frozenset({"vidnest"}),
    file_id_re=_EMBED_RE,
    offline_markers=(
        *_STANDARD_MARKERS,
        ">Download video</",
    ),
)

MP4UPLOAD = XFSConfig(
    name="mp4upload",
    domains=frozenset({"mp4upload"}),
    file_id_re=_EMBED_RE,
    offline_markers=_STANDARD_MARKERS,
)

UQLOAD = XFSConfig(
    name="uqload",
    domains=frozenset({"uqload"}),
    file_id_re=_EMBED_RE,
    offline_markers=_STANDARD_MARKERS,
)

VIDSHAR = XFSConfig(
    name="vidshar",
    domains=frozenset({"vidshar", "vedshare"}),
    file_id_re=_EMBED_RE,
    offline_markers=_STANDARD_MARKERS,
)

VIDROBA = XFSConfig(
    name="vidroba",
    domains=frozenset({"vidoba", "vidroba"}),
    file_id_re=_EMBED_RE,
    offline_markers=_STANDARD_MARKERS,
)

HOTLINK = XFSConfig(
    name="hotlink",
    domains=frozenset({"hotlink"}),
    file_id_re=_BASIC_RE,
    offline_markers=_STANDARD_MARKERS,
)

VIDSPEED = XFSConfig(
    name="vidspeed",
    domains=frozenset({"vidspeed", "vidspeeds", "xvideosharing"}),
    file_id_re=_EMBED_RE,
    offline_markers=_STANDARD_MARKERS,
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

ALL_XFS_CONFIGS: tuple[XFSConfig, ...] = (
    KATFILE,
    HEXUPLOAD,
    CLICKNUPLOAD,
    FILESTORE,
    UPTOBOX,
    FUNXD,
    BIGWARP,
    DROPLOAD,
    GOODSTREAM,
    SAVEFILES,
    STREAMWISH,
    VIDMOLY,
    VIDOZA,
    VINOVO,
    VIDHIDE,
    STREAMRUBY,
    VEEV,
    LULUSTREAM,
    UPSTREAM,
    WOLFSTREAM,
    VIDNEST,
    MP4UPLOAD,
    UQLOAD,
    VIDSHAR,
    VIDROBA,
    HOTLINK,
    VIDSPEED,
)


def create_all_xfs_resolvers(
    http_client: httpx.AsyncClient,
) -> list[XFSResolver]:
    """Create ``XFSResolver`` instances for all known XFS hosters."""
    return [XFSResolver(config=cfg, http_client=http_client) for cfg in ALL_XFS_CONFIGS]
