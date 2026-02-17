"""Tests for the consolidated generic DDL resolver (GenericDDLConfig + GenericDDLResolver).

Covers:
- GenericDDLConfig uniqueness invariants
- extract_ddl_file_id for every config
- GenericDDLResolver.resolve for every config (valid, offline, errors)
- Factory function create_all_ddl_resolvers
- Min-file-ID-length enforcement (alphaddl, turbobit)
- Query-based extraction (onefichier)
"""

from __future__ import annotations

import httpx
import pytest
import respx

from scavengarr.domain.entities.stremio import StreamQuality
from scavengarr.infrastructure.hoster_resolvers.generic_ddl import (
    ALL_DDL_CONFIGS,
    GenericDDLConfig,
    GenericDDLResolver,
    create_all_ddl_resolvers,
    extract_ddl_file_id,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Sample valid URLs per hoster (must match their respective file_id_re).
_VALID_URLS: dict[str, str] = {
    "alfafile": "https://alfafile.net/file/abc123def",
    "alphaddl": "https://alphaddl.com/some-movie-2025",
    "fastpic": "https://fastpic.org/view/123/abcdef01234567890123456789abcdef.jpg",
    "filecrypt": "https://filecrypt.cc/Container/ABC123def",
    "filefactory": "https://filefactory.com/file/abc123",
    "fsst": "https://fsst.online/abc123def",
    "go4up": "https://go4up.com/dl/abc123def",
    "mixdrop": "https://mixdrop.ag/f/abc123def",
    "nitroflare": "https://nitroflare.com/view/ABCDEF123",
    "1fichier": "https://1fichier.com/?abc12345",
    "turbobit": "https://turbobit.net/abc123def456.html",
    "uploaded": "https://uploaded.net/file/abc123",
}


def _valid_url(config: GenericDDLConfig) -> str:
    """Return the sample valid URL for a config."""
    return _VALID_URLS[config.name]


def _valid_html() -> str:
    return "<html><body><h4>Movie.2025.1080p.mkv</h4></body></html>"


# ---------------------------------------------------------------------------
# Config invariant tests
# ---------------------------------------------------------------------------


class TestGenericDDLConfigInvariants:
    def test_all_names_unique(self) -> None:
        names = [cfg.name for cfg in ALL_DDL_CONFIGS]
        assert len(names) == len(set(names))

    def test_all_configs_have_domains(self) -> None:
        for cfg in ALL_DDL_CONFIGS:
            assert len(cfg.domains) > 0, f"{cfg.name} has no domains"

    def test_all_configs_have_markers(self) -> None:
        for cfg in ALL_DDL_CONFIGS:
            assert len(cfg.offline_markers) > 0, f"{cfg.name} has no markers"

    def test_config_count(self) -> None:
        assert len(ALL_DDL_CONFIGS) == 12

    def test_configs_are_frozen(self) -> None:
        for cfg in ALL_DDL_CONFIGS:
            with pytest.raises(AttributeError):
                cfg.name = "changed"  # type: ignore[misc]

    def test_all_configs_have_valid_urls(self) -> None:
        """Every config in the registry has a matching test URL."""
        for cfg in ALL_DDL_CONFIGS:
            assert cfg.name in _VALID_URLS, f"No test URL for {cfg.name}"


# ---------------------------------------------------------------------------
# extract_ddl_file_id — parameterised over all configs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "config",
    ALL_DDL_CONFIGS,
    ids=[c.name for c in ALL_DDL_CONFIGS],
)
class TestExtractFileId:
    def test_valid_url(self, config: GenericDDLConfig) -> None:
        url = _valid_url(config)
        result = extract_ddl_file_id(url, config)
        assert result is not None

    def test_www_prefix(self, config: GenericDDLConfig) -> None:
        url = _valid_url(config).replace("://", "://www.")
        result = extract_ddl_file_id(url, config)
        assert result is not None

    def test_http_scheme(self, config: GenericDDLConfig) -> None:
        url = _valid_url(config).replace("https://", "http://")
        result = extract_ddl_file_id(url, config)
        assert result is not None

    def test_non_matching_domain(self, config: GenericDDLConfig) -> None:
        assert extract_ddl_file_id("https://example.com/file/abc123", config) is None

    def test_empty_url(self, config: GenericDDLConfig) -> None:
        assert extract_ddl_file_id("", config) is None

    def test_invalid_url(self, config: GenericDDLConfig) -> None:
        assert extract_ddl_file_id("not-a-url", config) is None


# ---------------------------------------------------------------------------
# min_file_id_len enforcement
# ---------------------------------------------------------------------------


class TestMinFileIdLen:
    def test_alphaddl_rejects_short_slug(self) -> None:
        """alphaddl requires min 3 characters."""
        from scavengarr.infrastructure.hoster_resolvers.generic_ddl import ALPHADDL

        assert extract_ddl_file_id("https://alphaddl.com/ab", ALPHADDL) is None

    def test_alphaddl_accepts_long_slug(self) -> None:
        from scavengarr.infrastructure.hoster_resolvers.generic_ddl import ALPHADDL

        result = extract_ddl_file_id("https://alphaddl.com/abc", ALPHADDL)
        assert result == "abc"

    def test_turbobit_rejects_short_id(self) -> None:
        """turbobit requires min 6 characters."""
        from scavengarr.infrastructure.hoster_resolvers.generic_ddl import TURBOBIT

        assert extract_ddl_file_id("https://turbobit.net/abcde.html", TURBOBIT) is None

    def test_turbobit_accepts_long_id(self) -> None:
        from scavengarr.infrastructure.hoster_resolvers.generic_ddl import TURBOBIT

        result = extract_ddl_file_id("https://turbobit.net/abcdef.html", TURBOBIT)
        assert result == "abcdef"


# ---------------------------------------------------------------------------
# Query-based extraction (onefichier)
# ---------------------------------------------------------------------------


class TestQueryExtraction:
    def test_onefichier_main_domain(self) -> None:
        from scavengarr.infrastructure.hoster_resolvers.generic_ddl import ONEFICHIER

        result = extract_ddl_file_id("https://1fichier.com/?abc12345", ONEFICHIER)
        assert result == "abc12345"

    def test_onefichier_alias_domain(self) -> None:
        from scavengarr.infrastructure.hoster_resolvers.generic_ddl import ONEFICHIER

        result = extract_ddl_file_id("https://alterupload.com/?xyz98765", ONEFICHIER)
        assert result == "xyz98765"

    def test_onefichier_rejects_short_query(self) -> None:
        from scavengarr.infrastructure.hoster_resolvers.generic_ddl import ONEFICHIER

        assert extract_ddl_file_id("https://1fichier.com/?abcd", ONEFICHIER) is None

    def test_onefichier_rejects_empty_query(self) -> None:
        from scavengarr.infrastructure.hoster_resolvers.generic_ddl import ONEFICHIER

        assert extract_ddl_file_id("https://1fichier.com/", ONEFICHIER) is None


# ---------------------------------------------------------------------------
# GenericDDLResolver.resolve — parameterised over all configs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "config",
    ALL_DDL_CONFIGS,
    ids=[c.name for c in ALL_DDL_CONFIGS],
)
class TestGenericDDLResolver:
    def test_name(self, config: GenericDDLConfig) -> None:
        resolver = GenericDDLResolver(
            config=config,
            http_client=httpx.AsyncClient(),
        )
        assert resolver.name == config.name

    @respx.mock
    @pytest.mark.asyncio()
    async def test_resolves_valid_file(self, config: GenericDDLConfig) -> None:
        url = _valid_url(config)
        respx.get(url).respond(200, text=_valid_html())

        async with httpx.AsyncClient() as client:
            resolver = GenericDDLResolver(config=config, http_client=client)
            result = await resolver.resolve(url)

        assert result is not None
        assert result.video_url == url
        assert result.quality == StreamQuality.UNKNOWN

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_each_offline_marker(
        self, config: GenericDDLConfig
    ) -> None:
        """Each offline marker individually triggers offline detection."""
        url = _valid_url(config)
        for marker in config.offline_markers:
            respx.reset()
            html = f"<html><body>{marker}</body></html>"
            respx.get(url).respond(200, text=html)

            async with httpx.AsyncClient() as client:
                resolver = GenericDDLResolver(config=config, http_client=client)
                result = await resolver.resolve(url)
            assert result is None, f"Marker not detected: {marker!r}"

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_http_500(self, config: GenericDDLConfig) -> None:
        url = _valid_url(config)
        respx.get(url).respond(500)

        async with httpx.AsyncClient() as client:
            resolver = GenericDDLResolver(config=config, http_client=client)
            result = await resolver.resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_network_error(
        self, config: GenericDDLConfig
    ) -> None:
        url = _valid_url(config)
        respx.get(url).mock(side_effect=httpx.ConnectError("failed"))

        async with httpx.AsyncClient() as client:
            resolver = GenericDDLResolver(config=config, http_client=client)
            result = await resolver.resolve(url)
        assert result is None

    @pytest.mark.asyncio()
    async def test_returns_none_for_invalid_url(
        self, config: GenericDDLConfig
    ) -> None:
        async with httpx.AsyncClient() as client:
            resolver = GenericDDLResolver(config=config, http_client=client)
            result = await resolver.resolve("https://example.com/file/abc123def")
        assert result is None


# ---------------------------------------------------------------------------
# Error redirect with real redirect chain
# ---------------------------------------------------------------------------


class TestErrorRedirectChain:
    @respx.mock
    @pytest.mark.asyncio()
    async def test_redirect_to_404_page(self) -> None:
        """GenericDDLResolver detects redirect to /404 URL via resp.url check."""
        from scavengarr.infrastructure.hoster_resolvers.generic_ddl import ALFAFILE

        url = "https://alfafile.net/file/abc123def"
        redirect_target = "https://alfafile.net/404"

        respx.get(url).respond(
            301,
            headers={"Location": redirect_target},
        )
        respx.get(redirect_target).respond(
            200,
            text="<html><body>Not found</body></html>",
        )

        async with httpx.AsyncClient(follow_redirects=True) as client:
            resolver = GenericDDLResolver(config=ALFAFILE, http_client=client)
            result = await resolver.resolve(url)
        assert result is None


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------


class TestCreateAllDdlResolvers:
    def test_returns_correct_count(self) -> None:
        resolvers = create_all_ddl_resolvers(httpx.AsyncClient())
        assert len(resolvers) == len(ALL_DDL_CONFIGS)

    def test_all_names_unique(self) -> None:
        resolvers = create_all_ddl_resolvers(httpx.AsyncClient())
        names = [r.name for r in resolvers]
        assert len(names) == len(set(names))

    def test_all_names_match_configs(self) -> None:
        resolvers = create_all_ddl_resolvers(httpx.AsyncClient())
        resolver_names = {r.name for r in resolvers}
        config_names = {c.name for c in ALL_DDL_CONFIGS}
        assert resolver_names == config_names
