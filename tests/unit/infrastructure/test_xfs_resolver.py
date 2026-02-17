"""Tests for the consolidated XFS resolver (XFSConfig + XFSResolver).

Covers:
- XFSConfig uniqueness invariants
- extract_xfs_file_id for every config
- XFSResolver.resolve for DDL hosters (validate-only)
- XFSResolver.resolve for video hosters (extract video URL)
- XFSResolver.resolve for captcha-required hosters (returns None)
- Video URL extraction from packed JS, JWPlayer, and HLS patterns
- Factory function create_all_xfs_resolvers
"""

from __future__ import annotations

import httpx
import pytest
import respx

from scavengarr.domain.entities.stremio import StreamQuality
from scavengarr.infrastructure.hoster_resolvers.xfs import (
    ALL_XFS_CONFIGS,
    XFSConfig,
    XFSResolver,
    create_all_xfs_resolvers,
    extract_xfs_file_id,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# TLD map for building plausible URLs per hoster
_TLD_MAP: dict[str, str] = {
    "katfile": "online",
    "hexupload": "net",
    "clicknupload": "click",
    "filestore": "me",
    "uptobox": "com",
    "funxd": "site",
    "bigwarp": "io",
    "dropload": "io",
    "savefiles": "com",
    "streamwish": "com",
    "vidmoly": "me",
    "vidoza": "net",
    "vinovo": "to",
    "vidhide": "com",
    "streamruby": "com",
    "veev": "to",
    "lulustream": "com",
    "upstream": "to",
    "wolfstream": "tv",
    "vidnest": "io",
}

# Standard file ID (uppercase+lowercase+digits, 12 chars)
_FILE_ID = "aBc123DeF456"

# Lowercase-only file ID (for vidhide)
_FILE_ID_LOWER = "abc123def456"

# Categorised config lists
_DDL_CONFIGS = [c for c in ALL_XFS_CONFIGS if not c.is_video_hoster]
_VIDEO_CONFIGS = [
    c for c in ALL_XFS_CONFIGS if c.is_video_hoster and not c.needs_captcha
]
_CAPTCHA_CONFIGS = [c for c in ALL_XFS_CONFIGS if c.needs_captcha]


def _first_domain(config: XFSConfig) -> str:
    """Return a deterministic domain from the config."""
    return sorted(config.domains)[0]


def _file_id_for(config: XFSConfig) -> str:
    """Choose the right file ID based on regex case sensitivity."""
    return _FILE_ID_LOWER if "[a-z0-9]" in config.file_id_re.pattern else _FILE_ID


def _make_url(config: XFSConfig) -> str:
    """Build a plausible URL for the given XFS config."""
    domain = _first_domain(config)
    tld = _TLD_MAP.get(config.name, "com")
    file_id = _file_id_for(config)

    # Vinovo's regex requires /e/ or /d/ prefix (the group is NOT optional).
    if config.name == "vinovo":
        return f"https://{domain}.{tld}/e/{file_id}"
    return f"https://{domain}.{tld}/{file_id}"


def _make_embed_url(config: XFSConfig) -> str:
    """Build the embed URL that the video resolver will fetch."""
    domain = _first_domain(config)
    tld = _TLD_MAP.get(config.name, "com")
    file_id = _file_id_for(config)
    return f"https://{domain}.{tld}/e/{file_id}"


def _valid_html() -> str:
    return "<html><body><h4>Movie.2025.1080p.mkv</h4></body></html>"


_VIDEO_HLS_URL = "https://cdn.example.com/video/master.m3u8"
_VIDEO_MP4_URL = "https://cdn.example.com/video/movie.mp4"


def _video_html_jwplayer() -> str:
    """HTML with JWPlayer sources containing HLS URL."""
    return (
        '<html><body><script>var player = jwplayer("vplayer");'
        "player.setup({sources:[{file:"
        f'"{_VIDEO_HLS_URL}"'
        "}]});</script></body></html>"
    )


def _video_html_hls2() -> str:
    """HTML with Streamwish-style hls2 pattern."""
    return f'<html><body><script>"hls2":"{_VIDEO_HLS_URL}"</script></body></html>'


def _video_html_packed_js() -> str:
    """HTML with Dean Edwards packed JS containing HLS URL."""
    return (
        "<html><body><script>eval(function(p,a,c,k,e,d)"
        "{e=function(c){return c};if(!''.replace(/^/,String))"
        "{while(c--)d[c]=k[c]||c;k=[function(e)"
        "{return d[e]}];e=function(){return'\\\\w+'};c=1};"
        "while(c--)if(k[c])p=p.replace(new RegExp('\\\\b'+e(c)+'\\\\b','g'),k[c]);"
        "return p}("
        f"'0=[{{1:\"{_VIDEO_HLS_URL}\"}}]',2,2,'sources|file'.split('|'),0,{{}}))"
        "</script></body></html>"
    )


def _video_html_direct_hls() -> str:
    """HTML with direct HLS URL in page source."""
    return f'<html><body><video src="{_VIDEO_HLS_URL}"></video></body></html>'


# ---------------------------------------------------------------------------
# Config invariant tests
# ---------------------------------------------------------------------------


class TestXFSConfigInvariants:
    def test_all_names_unique(self) -> None:
        names = [cfg.name for cfg in ALL_XFS_CONFIGS]
        assert len(names) == len(set(names))

    def test_all_configs_have_domains(self) -> None:
        for cfg in ALL_XFS_CONFIGS:
            assert len(cfg.domains) > 0, f"{cfg.name} has no domains"

    def test_all_configs_have_markers(self) -> None:
        for cfg in ALL_XFS_CONFIGS:
            assert len(cfg.offline_markers) > 0, f"{cfg.name} has no markers"

    def test_config_count(self) -> None:
        assert len(ALL_XFS_CONFIGS) == 27

    def test_configs_are_frozen(self) -> None:
        for cfg in ALL_XFS_CONFIGS:
            with pytest.raises(AttributeError):
                cfg.name = "changed"  # type: ignore[misc]

    def test_video_hoster_count(self) -> None:
        video_count = sum(1 for c in ALL_XFS_CONFIGS if c.is_video_hoster)
        assert video_count == 21

    def test_captcha_count(self) -> None:
        captcha_count = sum(1 for c in ALL_XFS_CONFIGS if c.needs_captcha)
        assert captcha_count == 3

    def test_ddl_count(self) -> None:
        ddl_count = sum(1 for c in ALL_XFS_CONFIGS if not c.is_video_hoster)
        assert ddl_count == 6


# ---------------------------------------------------------------------------
# extract_xfs_file_id — parameterised over all configs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "config",
    ALL_XFS_CONFIGS,
    ids=[c.name for c in ALL_XFS_CONFIGS],
)
class TestExtractFileId:
    def test_valid_url(self, config: XFSConfig) -> None:
        url = _make_url(config)
        result = extract_xfs_file_id(url, config)
        assert result is not None
        assert len(result) >= 12

    def test_www_prefix(self, config: XFSConfig) -> None:
        url = _make_url(config).replace("://", "://www.")
        result = extract_xfs_file_id(url, config)
        assert result is not None

    def test_http_scheme(self, config: XFSConfig) -> None:
        url = _make_url(config).replace("https://", "http://")
        result = extract_xfs_file_id(url, config)
        assert result is not None

    def test_non_matching_domain(self, config: XFSConfig) -> None:
        assert extract_xfs_file_id("https://example.com/abc123def456", config) is None

    def test_short_id_rejected(self, config: XFSConfig) -> None:
        domain = _first_domain(config)
        tld = _TLD_MAP.get(config.name, "com")
        assert extract_xfs_file_id(f"https://{domain}.{tld}/abc12", config) is None

    def test_empty_url(self, config: XFSConfig) -> None:
        assert extract_xfs_file_id("", config) is None

    def test_invalid_url(self, config: XFSConfig) -> None:
        assert extract_xfs_file_id("not-a-url", config) is None


# ---------------------------------------------------------------------------
# DDL hosters — validate file availability (echo URL back)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "config",
    _DDL_CONFIGS,
    ids=[c.name for c in _DDL_CONFIGS],
)
class TestXFSResolverDDL:
    def test_name(self, config: XFSConfig) -> None:
        resolver = XFSResolver(config=config, http_client=httpx.AsyncClient())
        assert resolver.name == config.name

    @respx.mock
    @pytest.mark.asyncio()
    async def test_resolves_valid_file(self, config: XFSConfig) -> None:
        url = _make_url(config)
        respx.get(url).respond(200, text=_valid_html())

        async with httpx.AsyncClient() as client:
            resolver = XFSResolver(config=config, http_client=client)
            result = await resolver.resolve(url)

        assert result is not None
        assert result.video_url == url
        assert result.quality == StreamQuality.UNKNOWN

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_each_offline_marker(
        self, config: XFSConfig
    ) -> None:
        url = _make_url(config)
        for marker in config.offline_markers:
            respx.reset()
            html = f"<html><body>{marker}</body></html>"
            respx.get(url).respond(200, text=html)

            async with httpx.AsyncClient() as client:
                resolver = XFSResolver(config=config, http_client=client)
                result = await resolver.resolve(url)
            assert result is None, f"Marker not detected: {marker!r}"

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_http_500(self, config: XFSConfig) -> None:
        url = _make_url(config)
        respx.get(url).respond(500)

        async with httpx.AsyncClient() as client:
            resolver = XFSResolver(config=config, http_client=client)
            result = await resolver.resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_network_error(self, config: XFSConfig) -> None:
        url = _make_url(config)
        respx.get(url).mock(side_effect=httpx.ConnectError("failed"))

        async with httpx.AsyncClient() as client:
            resolver = XFSResolver(config=config, http_client=client)
            result = await resolver.resolve(url)
        assert result is None

    @pytest.mark.asyncio()
    async def test_returns_none_for_invalid_url(self, config: XFSConfig) -> None:
        async with httpx.AsyncClient() as client:
            resolver = XFSResolver(config=config, http_client=client)
            result = await resolver.resolve("https://example.com/abc123def456")
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_error_redirect(self, config: XFSConfig) -> None:
        domain = _first_domain(config)
        tld = _TLD_MAP.get(config.name, "com")
        file_id = _file_id_for(config)
        error_url = f"https://{domain}.{tld}/404/{file_id}"
        respx.get(error_url).respond(200, text=_valid_html())

        async with httpx.AsyncClient() as client:
            resolver = XFSResolver(config=config, http_client=client)
            result = await resolver.resolve(error_url)
        assert result is None


# ---------------------------------------------------------------------------
# Video hosters — extract actual video URL from embed page
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "config",
    _VIDEO_CONFIGS,
    ids=[c.name for c in _VIDEO_CONFIGS],
)
class TestXFSResolverVideo:
    def test_name(self, config: XFSConfig) -> None:
        resolver = XFSResolver(config=config, http_client=httpx.AsyncClient())
        assert resolver.name == config.name

    @respx.mock
    @pytest.mark.asyncio()
    async def test_extracts_jwplayer_hls(self, config: XFSConfig) -> None:
        """Video hoster with JWPlayer sources in embed page."""
        url = _make_url(config)
        embed_url = _make_embed_url(config)
        respx.get(embed_url).respond(200, text=_video_html_jwplayer())

        async with httpx.AsyncClient() as client:
            resolver = XFSResolver(config=config, http_client=client)
            result = await resolver.resolve(url)

        assert result is not None
        assert result.video_url == _VIDEO_HLS_URL
        assert result.is_hls is True
        assert result.quality == StreamQuality.UNKNOWN
        assert "Referer" in result.headers

    @respx.mock
    @pytest.mark.asyncio()
    async def test_extracts_hls2_pattern(self, config: XFSConfig) -> None:
        """Video hoster with Streamwish-style hls2 pattern."""
        url = _make_url(config)
        embed_url = _make_embed_url(config)
        respx.get(embed_url).respond(200, text=_video_html_hls2())

        async with httpx.AsyncClient() as client:
            resolver = XFSResolver(config=config, http_client=client)
            result = await resolver.resolve(url)

        assert result is not None
        assert result.video_url == _VIDEO_HLS_URL
        assert result.is_hls is True

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_when_no_video_found(self, config: XFSConfig) -> None:
        """Video hoster with no extractable video URL returns None."""
        url = _make_url(config)
        embed_url = _make_embed_url(config)
        respx.get(embed_url).respond(200, text=_valid_html())

        async with httpx.AsyncClient() as client:
            resolver = XFSResolver(config=config, http_client=client)
            result = await resolver.resolve(url)

        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_each_offline_marker(
        self, config: XFSConfig
    ) -> None:
        url = _make_url(config)
        embed_url = _make_embed_url(config)
        for marker in config.offline_markers:
            respx.reset()
            html = f"<html><body>{marker}</body></html>"
            respx.get(embed_url).respond(200, text=html)

            async with httpx.AsyncClient() as client:
                resolver = XFSResolver(config=config, http_client=client)
                result = await resolver.resolve(url)
            assert result is None, f"Marker not detected: {marker!r}"

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_http_500(self, config: XFSConfig) -> None:
        url = _make_url(config)
        embed_url = _make_embed_url(config)
        respx.get(embed_url).respond(500)

        async with httpx.AsyncClient() as client:
            resolver = XFSResolver(config=config, http_client=client)
            result = await resolver.resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_network_error(self, config: XFSConfig) -> None:
        url = _make_url(config)
        embed_url = _make_embed_url(config)
        respx.get(embed_url).mock(side_effect=httpx.ConnectError("failed"))

        async with httpx.AsyncClient() as client:
            resolver = XFSResolver(config=config, http_client=client)
            result = await resolver.resolve(url)
        assert result is None

    @pytest.mark.asyncio()
    async def test_returns_none_for_invalid_url(self, config: XFSConfig) -> None:
        async with httpx.AsyncClient() as client:
            resolver = XFSResolver(config=config, http_client=client)
            result = await resolver.resolve("https://example.com/abc123def456")
        assert result is None


# ---------------------------------------------------------------------------
# Captcha-required hosters — always return None
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "config",
    _CAPTCHA_CONFIGS,
    ids=[c.name for c in _CAPTCHA_CONFIGS],
)
class TestXFSResolverCaptcha:
    @pytest.mark.asyncio()
    async def test_returns_none_due_to_captcha(self, config: XFSConfig) -> None:
        """Hosters requiring captcha return None without making HTTP requests."""
        url = _make_url(config)
        async with httpx.AsyncClient() as client:
            resolver = XFSResolver(config=config, http_client=client)
            result = await resolver.resolve(url)
        assert result is None


# ---------------------------------------------------------------------------
# Veev long file ID (43-char alphanumeric)
# ---------------------------------------------------------------------------


class TestVeevLongId:
    """Veev.to now uses 43-char IDs like /e/2EwYsJS8frxAbWIzEhmWIJlqeGylzY9utsaUISu."""

    def test_extract_long_id(self) -> None:
        from scavengarr.infrastructure.hoster_resolvers.xfs import VEEV

        long_id = "2EwYsJS8frxAbWIzEhmWIJlqeGylzY9utsaUISuAB"
        url = f"https://veev.to/e/{long_id}"
        result = extract_xfs_file_id(url, VEEV)
        assert result == long_id

    def test_extract_long_id_without_prefix(self) -> None:
        from scavengarr.infrastructure.hoster_resolvers.xfs import VEEV

        long_id = "2EwYsJS8frxAbWIzEhmWIJlqeGylzY9utsaUISuAB"
        url = f"https://veev.to/{long_id}"
        result = extract_xfs_file_id(url, VEEV)
        assert result == long_id

    @pytest.mark.asyncio()
    async def test_veev_returns_none_needs_captcha(self) -> None:
        """Veev requires Cloudflare Turnstile — always returns None."""
        from scavengarr.infrastructure.hoster_resolvers.xfs import VEEV

        url = "https://veev.to/e/aBc123DeF456"
        async with httpx.AsyncClient() as client:
            resolver = XFSResolver(config=VEEV, http_client=client)
            result = await resolver.resolve(url)
        assert result is None

    def test_short_12_char_id_still_works(self) -> None:
        from scavengarr.infrastructure.hoster_resolvers.xfs import VEEV

        url = "https://veev.to/e/aBc123DeF456"
        result = extract_xfs_file_id(url, VEEV)
        assert result == "aBc123DeF456"


# ---------------------------------------------------------------------------
# Video extraction — specific patterns
# ---------------------------------------------------------------------------


class TestVideoExtraction:
    """Test video URL extraction from various embed page formats."""

    @respx.mock
    @pytest.mark.asyncio()
    async def test_jwplayer_sources_extraction(self) -> None:
        """JWPlayer sources:[{file:"..."}] pattern."""
        from scavengarr.infrastructure.hoster_resolvers.xfs import STREAMWISH

        url = "https://streamwish.com/aBc123DeF456"
        embed_url = "https://streamwish.com/e/aBc123DeF456"
        respx.get(embed_url).respond(200, text=_video_html_jwplayer())

        async with httpx.AsyncClient() as client:
            resolver = XFSResolver(config=STREAMWISH, http_client=client)
            result = await resolver.resolve(url)

        assert result is not None
        assert result.video_url == _VIDEO_HLS_URL
        assert result.is_hls is True
        assert result.headers == {"Referer": embed_url}

    @respx.mock
    @pytest.mark.asyncio()
    async def test_hls2_extraction(self) -> None:
        """Streamwish-specific "hls2":"http..." pattern."""
        from scavengarr.infrastructure.hoster_resolvers.xfs import STREAMWISH

        url = "https://streamwish.com/aBc123DeF456"
        embed_url = "https://streamwish.com/e/aBc123DeF456"
        respx.get(embed_url).respond(200, text=_video_html_hls2())

        async with httpx.AsyncClient() as client:
            resolver = XFSResolver(config=STREAMWISH, http_client=client)
            result = await resolver.resolve(url)

        assert result is not None
        assert result.video_url == _VIDEO_HLS_URL

    @respx.mock
    @pytest.mark.asyncio()
    async def test_packed_js_extraction(self) -> None:
        """Dean Edwards packed JS with JWPlayer sources."""
        from scavengarr.infrastructure.hoster_resolvers.xfs import VIDMOLY

        url = "https://vidmoly.me/aBc123DeF456"
        embed_url = "https://vidmoly.me/e/aBc123DeF456"
        respx.get(embed_url).respond(200, text=_video_html_packed_js())

        async with httpx.AsyncClient() as client:
            resolver = XFSResolver(config=VIDMOLY, http_client=client)
            result = await resolver.resolve(url)

        assert result is not None
        assert result.video_url == _VIDEO_HLS_URL
        assert result.is_hls is True

    @respx.mock
    @pytest.mark.asyncio()
    async def test_direct_hls_extraction(self) -> None:
        """Direct HLS URL in page source."""
        from scavengarr.infrastructure.hoster_resolvers.xfs import VIDOZA

        url = "https://vidoza.net/aBc123DeF456"
        embed_url = "https://vidoza.net/e/aBc123DeF456"
        respx.get(embed_url).respond(200, text=_video_html_direct_hls())

        async with httpx.AsyncClient() as client:
            resolver = XFSResolver(config=VIDOZA, http_client=client)
            result = await resolver.resolve(url)

        assert result is not None
        assert result.video_url == _VIDEO_HLS_URL

    @respx.mock
    @pytest.mark.asyncio()
    async def test_mp4_url_extraction(self) -> None:
        """Direct MP4 URL in JWPlayer sources."""
        from scavengarr.infrastructure.hoster_resolvers.xfs import MP4UPLOAD

        url = "https://mp4upload.com/aBc123DeF456"
        embed_url = "https://mp4upload.com/e/aBc123DeF456"
        html = (
            "<html><body><script>"
            f'sources:[{{file:"{_VIDEO_MP4_URL}"}}]'
            "</script></body></html>"
        )
        respx.get(embed_url).respond(200, text=html)

        async with httpx.AsyncClient() as client:
            resolver = XFSResolver(config=MP4UPLOAD, http_client=client)
            result = await resolver.resolve(url)

        assert result is not None
        assert result.video_url == _VIDEO_MP4_URL
        assert result.is_hls is False

    @respx.mock
    @pytest.mark.asyncio()
    async def test_embed_url_construction(self) -> None:
        """Resolver builds /e/{file_id} URL from any input URL format."""
        from scavengarr.infrastructure.hoster_resolvers.xfs import VIDHIDE

        # Input URL uses /f/ path, resolver should convert to /e/
        url = "https://vidhide.com/f/abc123def456"
        embed_url = "https://vidhide.com/e/abc123def456"
        respx.get(embed_url).respond(200, text=_video_html_hls2())

        async with httpx.AsyncClient() as client:
            resolver = XFSResolver(config=VIDHIDE, http_client=client)
            result = await resolver.resolve(url)

        assert result is not None
        assert result.video_url == _VIDEO_HLS_URL


# ---------------------------------------------------------------------------
# XFS form POST flow (two-step embed: GET splash → POST /dl → player page)
# ---------------------------------------------------------------------------


_XFS_FORM_HTML = (
    '<html><body><form id="F1" action="/dl" method="POST">'
    '<input type="hidden" name="op" value="embed">'
    '<input type="hidden" name="file_code" value="">'
    '<input type="hidden" name="auto" value="1">'
    '<input type="hidden" name="referer" value="">'
    "</form></body></html>"
)


class TestXFSFormPost:
    """XFS hosters that serve a form splash on GET need a POST to /dl."""

    @respx.mock
    @pytest.mark.asyncio()
    async def test_form_page_triggers_post(self) -> None:
        """When GET returns an XFS form, resolver POSTs to /dl for the player."""
        from scavengarr.infrastructure.hoster_resolvers.xfs import BIGWARP

        url = "https://bigwarp.io/aBc123DeF456"
        embed_url = "https://bigwarp.io/e/aBc123DeF456"
        dl_url = "https://bigwarp.io/dl"

        # GET returns the form splash
        respx.get(embed_url).respond(200, text=_XFS_FORM_HTML)
        # POST returns the actual player page
        respx.post(dl_url).respond(200, text=_video_html_jwplayer())

        async with httpx.AsyncClient() as client:
            resolver = XFSResolver(config=BIGWARP, http_client=client)
            result = await resolver.resolve(url)

        assert result is not None
        assert result.video_url == _VIDEO_HLS_URL
        assert result.is_hls is True

    @respx.mock
    @pytest.mark.asyncio()
    async def test_form_post_network_error(self) -> None:
        """POST failure returns None."""
        from scavengarr.infrastructure.hoster_resolvers.xfs import SAVEFILES

        url = "https://savefiles.com/aBc123DeF456"
        embed_url = "https://savefiles.com/e/aBc123DeF456"
        dl_url = "https://savefiles.com/dl"

        respx.get(embed_url).respond(200, text=_XFS_FORM_HTML)
        respx.post(dl_url).mock(side_effect=httpx.ConnectError("failed"))

        async with httpx.AsyncClient() as client:
            resolver = XFSResolver(config=SAVEFILES, http_client=client)
            result = await resolver.resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_form_post_offline_marker(self) -> None:
        """POST page with offline marker returns None."""
        from scavengarr.infrastructure.hoster_resolvers.xfs import BIGWARP

        url = "https://bigwarp.io/aBc123DeF456"
        embed_url = "https://bigwarp.io/e/aBc123DeF456"
        dl_url = "https://bigwarp.io/dl"

        respx.get(embed_url).respond(200, text=_XFS_FORM_HTML)
        respx.post(dl_url).respond(200, text="<html>File is no longer available</html>")

        async with httpx.AsyncClient() as client:
            resolver = XFSResolver(config=BIGWARP, http_client=client)
            result = await resolver.resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_direct_embed_page_skips_form_post(self) -> None:
        """When GET returns actual player HTML (no form), no POST is made."""
        from scavengarr.infrastructure.hoster_resolvers.xfs import STREAMWISH

        url = "https://streamwish.com/aBc123DeF456"
        embed_url = "https://streamwish.com/e/aBc123DeF456"
        # GET returns player directly (no form)
        respx.get(embed_url).respond(200, text=_video_html_hls2())

        async with httpx.AsyncClient() as client:
            resolver = XFSResolver(config=STREAMWISH, http_client=client)
            result = await resolver.resolve(url)

        assert result is not None
        assert result.video_url == _VIDEO_HLS_URL
        # No POST route was defined — if it tried to POST, it would fail


# ---------------------------------------------------------------------------
# Extra domains (JDownloader aliases)
# ---------------------------------------------------------------------------


class TestExtraDomains:
    def test_vidhide_extra_domain_accepted(self) -> None:
        from scavengarr.infrastructure.hoster_resolvers.xfs import VIDHIDE

        url = "https://nikaplayer.com/e/abc123def456"
        result = extract_xfs_file_id(url, VIDHIDE)
        assert result == "abc123def456"

    def test_vidhide_main_domain_accepted(self) -> None:
        from scavengarr.infrastructure.hoster_resolvers.xfs import VIDHIDE

        url = "https://vidhide.com/abc123def456"
        result = extract_xfs_file_id(url, VIDHIDE)
        assert result == "abc123def456"


# ---------------------------------------------------------------------------
# Error redirect with real redirect chain
# ---------------------------------------------------------------------------


class TestErrorRedirectChain:
    @respx.mock
    @pytest.mark.asyncio()
    async def test_redirect_to_404_page(self) -> None:
        """XFSResolver detects redirect to /404 URL via resp.url check."""
        from scavengarr.infrastructure.hoster_resolvers.xfs import KATFILE

        url = "https://katfile.online/aBc123DeF456"
        redirect_target = "https://katfile.online/404"

        respx.get(url).respond(
            301,
            headers={"Location": redirect_target},
        )
        respx.get(redirect_target).respond(
            200,
            text="<html><body>Not found</body></html>",
        )

        async with httpx.AsyncClient(follow_redirects=True) as client:
            resolver = XFSResolver(config=KATFILE, http_client=client)
            result = await resolver.resolve(url)
        assert result is None


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------


class TestCreateAllXfsResolvers:
    def test_returns_correct_count(self) -> None:
        resolvers = create_all_xfs_resolvers(httpx.AsyncClient())
        assert len(resolvers) == len(ALL_XFS_CONFIGS)

    def test_all_names_unique(self) -> None:
        resolvers = create_all_xfs_resolvers(httpx.AsyncClient())
        names = [r.name for r in resolvers]
        assert len(names) == len(set(names))

    def test_all_names_match_configs(self) -> None:
        resolvers = create_all_xfs_resolvers(httpx.AsyncClient())
        resolver_names = {r.name for r in resolvers}
        config_names = {c.name for c in ALL_XFS_CONFIGS}
        assert resolver_names == config_names
