"""Generic XFileSharingPro (XFS) hoster resolver.

Consolidates XFS-based hoster resolvers into a single parameterised
implementation.  Each hoster is described by an ``XFSConfig`` — name,
domains, file-ID regex, offline markers, and whether it's a video hoster —
while the resolution logic lives once in ``XFSResolver``.

Video hosters (``is_video_hoster=True``) get their embed page fetched and
the actual video URL extracted (JWPlayer, packed JS, HLS patterns).
DDL hosters (``is_video_hoster=False``) only validate file availability.

Adding a new XFS hoster = adding a new ``XFSConfig`` constant + appending
it to ``ALL_XFS_CONFIGS``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality
from scavengarr.infrastructure.hoster_resolvers import extract_domain
from scavengarr.infrastructure.hoster_resolvers._verify import verify_video_url
from scavengarr.infrastructure.hoster_resolvers._video_extract import extract_video_url

log = structlog.get_logger(__name__)

_BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# Detects the XFS two-step form: GET returns a play-button splash with a
# hidden form that must be POSTed to /dl to obtain the actual player page.
_XFS_FORM_RE = re.compile(r'<form\s+id=["\']F1["\']\s+action=["\']\/dl["\']')


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
    is_video_hoster: bool = False
    needs_captcha: bool = False
    extra_domains: frozenset[str] = field(default_factory=frozenset)


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
        all_domains = config.domains | config.extra_domains
        if domain not in all_domains:
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

    For video hosters: fetches the ``/e/{file_id}`` embed page and extracts
    the actual video URL (HLS/MP4) from JWPlayer config or packed JS.

    For DDL hosters: validates file availability by checking offline markers.
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

    @property
    def supported_domains(self) -> frozenset[str]:
        """All domains this resolver can handle (primary + aliases)."""
        return self._config.domains | self._config.extra_domains

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Resolve an XFS link.

        Video hosters: extract playable video URL from embed page.
        DDL hosters: validate file availability and return original URL.
        """
        hoster = self._config.name
        file_id = extract_xfs_file_id(url, self._config)
        if not file_id:
            log.warning(f"{hoster}_invalid_url", url=url)
            return None

        if self._config.is_video_hoster:
            if self._config.needs_captcha:
                log.debug(f"{hoster}_needs_captcha", file_id=file_id)
                return None
            return await self._resolve_video(url, file_id, hoster)
        return await self._resolve_ddl(url, file_id, hoster)

    async def _resolve_video(
        self, url: str, file_id: str, hoster: str
    ) -> ResolvedStream | None:
        """Fetch embed page and extract actual video URL.

        Many XFS hosters serve a form-based splash page on GET that requires
        a POST to ``/dl`` with ``op=embed&file_code={id}&auto=1`` to return
        the actual JWPlayer / packed-JS player page.  When the initial GET
        response contains an XFS form (``<form id="F1" action="/dl"``), we
        automatically submit it before attempting video URL extraction.
        """
        embed_url = self._build_embed_url(url, file_id)
        headers = {"User-Agent": _BROWSER_UA}

        try:
            resp = await self._http.get(
                embed_url,
                follow_redirects=True,
                timeout=15,
                headers=headers,
            )
        except httpx.HTTPError:
            log.warning(f"{hoster}_request_failed", url=embed_url)
            return None

        if resp.status_code != 200:
            log.warning(
                f"{hoster}_http_error",
                status=resp.status_code,
                url=embed_url,
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

        # Many XFS hosters return a form-based splash on GET; POST to /dl
        # to get the actual player page.
        if _XFS_FORM_RE.search(html):
            html = await self._post_xfs_form(url, file_id, hoster, headers)
            if html is None:
                return None

        video_url = extract_video_url(html)
        if not video_url:
            log.info(f"{hoster}_extraction_failed", file_id=file_id)
            return None

        is_hls = ".m3u8" in video_url
        cdn_headers = {"Referer": str(resp.url)}

        # Verify CDN URL is actually reachable (filters IP-locked tokens).
        if not await self._verify_video_url(video_url, cdn_headers, hoster):
            return None

        log.debug(f"{hoster}_video_extracted", file_id=file_id, url=video_url[:80])
        return ResolvedStream(
            video_url=video_url,
            is_hls=is_hls,
            quality=StreamQuality.UNKNOWN,
            headers=cdn_headers,
        )

    async def _verify_video_url(
        self, url: str, headers: dict[str, str], hoster: str
    ) -> bool:
        """HEAD-check the CDN URL to verify it is accessible."""
        return await verify_video_url(self._http, url, headers, hoster)

    async def _post_xfs_form(
        self,
        url: str,
        file_id: str,
        hoster: str,
        headers: dict[str, str],
    ) -> str | None:
        """Submit the XFS ``/dl`` form to obtain the real player page."""
        parsed = urlparse(url)
        dl_url = f"{parsed.scheme}://{parsed.netloc}/dl"
        data = {
            "op": "embed",
            "file_code": file_id,
            "auto": "1",
            "referer": "",
        }
        try:
            resp = await self._http.post(
                dl_url,
                data=data,
                follow_redirects=True,
                timeout=15,
                headers=headers,
            )
        except httpx.HTTPError:
            log.warning(f"{hoster}_form_post_failed", url=dl_url)
            return None

        if resp.status_code != 200:
            log.warning(
                f"{hoster}_form_post_error",
                status=resp.status_code,
                url=dl_url,
            )
            return None

        html = resp.text
        for marker in self._config.offline_markers:
            if marker in html:
                log.info(f"{hoster}_file_offline", file_id=file_id, marker=marker)
                return None

        return html

    async def _resolve_ddl(
        self, url: str, file_id: str, hoster: str
    ) -> ResolvedStream | None:
        """Validate file availability (DDL hosters only)."""
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

    def _build_embed_url(self, url: str, file_id: str) -> str:
        """Build the /e/{file_id} embed URL from the original URL."""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}/e/{file_id}"


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

# --- DDL (file download) hosters ---

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

HOTLINK = XFSConfig(
    name="hotlink",
    domains=frozenset({"hotlink"}),
    file_id_re=_BASIC_RE,
    offline_markers=_STANDARD_MARKERS,
)

# --- Video hosters (embed page → JWPlayer/packed JS → HLS/MP4 URL) ---

FUNXD = XFSConfig(
    name="funxd",
    domains=frozenset({"funxd"}),
    file_id_re=_ED_RE,
    offline_markers=_STANDARD_MARKERS,
    is_video_hoster=True,
)

BIGWARP = XFSConfig(
    name="bigwarp",
    domains=frozenset({"bigwarp"}),
    file_id_re=_EMBED_RE,
    offline_markers=(
        *_EXTENDED_MARKERS,
        ">File is no longer available",
    ),
    is_video_hoster=True,
    extra_domains=frozenset({"bigwarp"}),
)

DROPLOAD = XFSConfig(
    name="dropload",
    domains=frozenset({"dropload"}),
    file_id_re=_EMBED_RE,
    offline_markers=_EXTENDED_MARKERS,
    is_video_hoster=True,
)

SAVEFILES = XFSConfig(
    name="savefiles",
    domains=frozenset({"savefiles"}),
    file_id_re=_EMBED_RE,
    offline_markers=_EXTENDED_MARKERS,
    is_video_hoster=True,
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
    is_video_hoster=True,
)

VIDMOLY = XFSConfig(
    name="vidmoly",
    domains=frozenset({"vidmoly"}),
    file_id_re=re.compile(r"^/(?:embed-|e/|d/|w/)?([a-zA-Z0-9]{12})(?:/|$|\.html)"),
    offline_markers=(
        *_EXTENDED_MARKERS,
        "/notice.php",
    ),
    is_video_hoster=True,
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
    is_video_hoster=True,
)

VINOVO = XFSConfig(
    name="vinovo",
    domains=frozenset({"vinovo"}),
    file_id_re=re.compile(r"^/(?:e/|d/)([a-zA-Z0-9]{12,})(?:/|$)"),
    offline_markers=(
        *_EXTENDED_MARKERS,
        "Video not found",
    ),
    is_video_hoster=True,
    needs_captcha=True,
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
    is_video_hoster=True,
    extra_domains=frozenset(
        {
            # JDownloader VidhideCom.java domain aliases
            "moflix-stream",
            "javplaya",
            "alions",
            "azipcdn",
            "vidhidepre",
            "nikaplayer",
            "niikaplayerr",
            "seraphinapl",
            "taylorplayer",
            "dinisglows",
            "dingtezuni",
            "dintezuvio",
            "dhtpre",
            "callistanise",
            "mivalyo",
            "minochinos",
            "dlions",
            "playrecord",
            "mycloudz",
            # E2E-discovered vidhide aliases (parklogic.com anti-adblock)
            "streamhide",
            "louishide",
            "streamvid",
            "availedsmallest",
            "tummulerviolableness",
            "tubelessceliolymph",
        }
    ),
)


GOODSTREAM = XFSConfig(
    name="goodstream",
    domains=frozenset({"goodstream"}),
    file_id_re=_EMBED_RE,
    offline_markers=_STANDARD_MARKERS,
    is_video_hoster=True,
)

STREAMRUBY = XFSConfig(
    name="streamruby",
    domains=frozenset({"streamruby"}),
    file_id_re=_EMBED_RE,
    offline_markers=_STANDARD_MARKERS,
    is_video_hoster=True,
)

VEEV = XFSConfig(
    name="veev",
    domains=frozenset({"veev"}),
    file_id_re=re.compile(r"^/(?:e/|d/)?([a-zA-Z0-9]{12,})(?:/|$|\.html)"),
    offline_markers=_STANDARD_MARKERS,
    is_video_hoster=True,
    needs_captcha=True,
)

LULUSTREAM = XFSConfig(
    name="lulustream",
    domains=frozenset({"lulustream", "luluvdo", "luluvid", "lulu", "luluvdoo", "cdn1"}),
    file_id_re=_EMBED_RE,
    offline_markers=_STANDARD_MARKERS,
    is_video_hoster=True,
)

UPSTREAM = XFSConfig(
    name="upstream",
    domains=frozenset({"upstream"}),
    file_id_re=_EMBED_RE,
    offline_markers=_STANDARD_MARKERS,
    is_video_hoster=True,
)

WOLFSTREAM = XFSConfig(
    name="wolfstream",
    domains=frozenset({"wolfstream"}),
    file_id_re=_EMBED_RE,
    offline_markers=_STANDARD_MARKERS,
    is_video_hoster=True,
    needs_captcha=True,  # anti-bot JS redirect on embed pages
)

VIDNEST = XFSConfig(
    name="vidnest",
    domains=frozenset({"vidnest"}),
    file_id_re=_EMBED_RE,
    offline_markers=(
        *_STANDARD_MARKERS,
        ">Download video</",
    ),
    is_video_hoster=True,
)

MP4UPLOAD = XFSConfig(
    name="mp4upload",
    domains=frozenset({"mp4upload"}),
    file_id_re=_EMBED_RE,
    offline_markers=_STANDARD_MARKERS,
    is_video_hoster=True,
)

UQLOAD = XFSConfig(
    name="uqload",
    domains=frozenset({"uqload"}),
    file_id_re=_EMBED_RE,
    offline_markers=_STANDARD_MARKERS,
    is_video_hoster=True,
)

VIDSHAR = XFSConfig(
    name="vidshar",
    domains=frozenset({"vidshar", "vedshare"}),
    file_id_re=_EMBED_RE,
    offline_markers=_STANDARD_MARKERS,
    is_video_hoster=True,
)

VIDROBA = XFSConfig(
    name="vidroba",
    domains=frozenset({"vidoba", "vidroba"}),
    file_id_re=_EMBED_RE,
    offline_markers=_STANDARD_MARKERS,
    is_video_hoster=True,
)

VIDSPEED = XFSConfig(
    name="vidspeed",
    domains=frozenset({"vidspeed", "vidspeeds", "xvideosharing"}),
    file_id_re=_EMBED_RE,
    offline_markers=_STANDARD_MARKERS,
    is_video_hoster=True,
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
