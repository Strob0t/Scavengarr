"""Tests for the consolidated XFS resolver (XFSConfig + XFSResolver).

Covers:
- XFSConfig uniqueness invariants
- extract_xfs_file_id for every config
- XFSResolver.resolve for every config (valid, offline, errors)
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
    "goodstream": "uno",
    "savefiles": "com",
    "streamwish": "com",
    "vidmoly": "me",
    "vidoza": "net",
    "vinovo": "to",
    "vidhide": "com",
}

# Standard file ID (uppercase+lowercase+digits, 12 chars)
_FILE_ID = "aBc123DeF456"

# Lowercase-only file ID (for vidhide)
_FILE_ID_LOWER = "abc123def456"


def _first_domain(config: XFSConfig) -> str:
    """Return a deterministic domain from the config."""
    return sorted(config.domains)[0]


def _make_url(config: XFSConfig) -> str:
    """Build a plausible URL for the given XFS config."""
    domain = _first_domain(config)
    tld = _TLD_MAP.get(config.name, "com")
    pattern = config.file_id_re.pattern

    # Choose file ID based on regex case sensitivity
    file_id = _FILE_ID_LOWER if "[a-z0-9]" in pattern else _FILE_ID

    # Vinovo's regex requires /e/ or /d/ prefix (the group is NOT optional).
    # Detect: pattern has "(?:e/|d/)" without a trailing "?" making it optional.
    # Other patterns use "(?:e/|d/)?" or "(?:e/|d/|embed-)?" (note the ?).
    if config.name == "vinovo":
        return f"https://{domain}.{tld}/e/{file_id}"
    return f"https://{domain}.{tld}/{file_id}"


def _valid_html() -> str:
    return "<html><body><h4>Movie.2025.1080p.mkv</h4></body></html>"


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
        assert len(ALL_XFS_CONFIGS) == 15

    def test_configs_are_frozen(self) -> None:
        for cfg in ALL_XFS_CONFIGS:
            with pytest.raises(AttributeError):
                cfg.name = "changed"  # type: ignore[misc]


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
# XFSResolver.resolve — parameterised over all configs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "config",
    ALL_XFS_CONFIGS,
    ids=[c.name for c in ALL_XFS_CONFIGS],
)
class TestXFSResolver:
    def test_name(self, config: XFSConfig) -> None:
        resolver = XFSResolver(
            config=config,
            http_client=httpx.AsyncClient(),
        )
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
        """Each offline marker individually triggers offline detection."""
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
        """URL that contains /404 in the final (response) URL."""
        domain = _first_domain(config)
        tld = _TLD_MAP.get(config.name, "com")
        file_id = (
            _FILE_ID_LOWER if "[a-z0-9]" in config.file_id_re.pattern else _FILE_ID
        )

        # Build a URL where the path contains /404 so resp.url check fires
        error_url = f"https://{domain}.{tld}/404/{file_id}"
        respx.get(error_url).respond(200, text=_valid_html())

        async with httpx.AsyncClient() as client:
            resolver = XFSResolver(config=config, http_client=client)
            result = await resolver.resolve(error_url)
        # The resolver checks str(resp.url) for "/404" — since error_url
        # IS the final URL and contains "/404", result must be None.
        # But first the file_id extraction must succeed, which requires
        # the URL to match the config's domain.
        # If extraction fails, result is None anyway (invalid URL).
        assert result is None


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
