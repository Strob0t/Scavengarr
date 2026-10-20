"""Unit tests for MiniSearchProber."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import respx

from scavengarr.domain.entities.scoring import ProbeResult
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.domain.plugins.exceptions import PluginNotFoundError
from scavengarr.infrastructure.hoster_resolvers.registry import (
    HosterResolverRegistry,
)
from scavengarr.infrastructure.plugins.constants import search_max_results
from scavengarr.infrastructure.scoring.search_prober import (
    MiniSearchProber,
    _extract_hoster,
)


def _lookup(*resolvers: SimpleNamespace) -> Callable[[str], str | None]:
    """The registry's dispatch lookup over *resolvers*."""
    return HosterResolverRegistry(resolvers=list(resolvers)).resolver_name


_SUPPORTED = _lookup(
    *(SimpleNamespace(name=name) for name in ("hoster", "voe", "streamtape"))
)


def _make_results(count: int = 5, domain: str = "hoster.com") -> list[SearchResult]:
    return [
        SearchResult(
            title=f"Result {i}",
            download_link=f"https://{domain}/file/{i}",
        )
        for i in range(count)
    ]


def _mock_registry(
    plugin_name: str = "sto",
    results: list[SearchResult] | None = None,
    side_effect: Exception | None = None,
) -> MagicMock:
    """Create a mock plugin registry with a fake plugin."""
    registry = MagicMock()
    plugin = MagicMock()
    if side_effect:
        plugin.search = AsyncMock(side_effect=side_effect)
    else:
        plugin.search = AsyncMock(return_value=results or [])
    registry.get.return_value = plugin
    return registry


class TestExtractHoster:
    def test_simple_domain(self) -> None:
        assert _extract_hoster("https://voe.sx/e/abc") == "voe"

    def test_subdomain(self) -> None:
        assert _extract_hoster("https://www.streamtape.com/v/abc") == "streamtape"

    def test_empty_url(self) -> None:
        assert _extract_hoster("") == ""

    def test_no_hostname(self) -> None:
        assert _extract_hoster("not-a-url") == ""


class TestProbe:
    @respx.mock
    async def test_successful_probe(self) -> None:
        results = _make_results(10)
        registry = _mock_registry(results=results)
        for i in range(3):
            respx.head(f"https://hoster.com/file/{i}").respond(200)

        async with httpx.AsyncClient() as client:
            prober = MiniSearchProber(
                plugins=registry,
                http_client=client,
                resolver_name=_SUPPORTED,
            )
            probe = await prober.probe("sto", "Iron Man", 2000)

        assert probe.ok is True
        assert probe.items_found == 10
        assert probe.items_used == 10
        assert probe.hoster_checked == 3
        assert probe.hoster_reachable == 3
        assert probe.hoster_supported == 10
        assert probe.hoster_total == 10
        assert probe.duration_ms > 0

    @respx.mock
    async def test_no_results(self) -> None:
        registry = _mock_registry(results=[])
        async with httpx.AsyncClient() as client:
            prober = MiniSearchProber(
                plugins=registry,
                http_client=client,
                resolver_name=_SUPPORTED,
            )
            probe = await prober.probe("sto", "Nonexistent", 2000)

        assert probe.ok is True
        assert probe.items_found == 0
        assert probe.hoster_checked == 0
        assert probe.hoster_supported == 0
        assert probe.hoster_total == 0

    @respx.mock
    async def test_plugin_not_found(self) -> None:
        registry = MagicMock()
        registry.get.side_effect = PluginNotFoundError("unknown")
        async with httpx.AsyncClient() as client:
            prober = MiniSearchProber(
                plugins=registry,
                http_client=client,
                resolver_name=_SUPPORTED,
            )
            probe = await prober.probe("unknown", "query", 2000)

        assert probe.ok is False
        assert probe.error_kind == "plugin_not_found"

    @respx.mock
    async def test_search_error(self) -> None:
        registry = _mock_registry(side_effect=RuntimeError("boom"))
        async with httpx.AsyncClient() as client:
            prober = MiniSearchProber(
                plugins=registry,
                http_client=client,
                resolver_name=_SUPPORTED,
            )
            probe = await prober.probe("sto", "query", 2000)

        assert probe.ok is False
        assert probe.error_kind == "search_error"

    @respx.mock
    async def test_max_items_cap(self) -> None:
        results = _make_results(30)
        registry = _mock_registry(results=results)
        for i in range(3):
            respx.head(f"https://hoster.com/file/{i}").respond(200)

        async with httpx.AsyncClient() as client:
            prober = MiniSearchProber(
                plugins=registry,
                http_client=client,
                resolver_name=_SUPPORTED,
            )
            probe = await prober.probe("sto", "query", 2000, max_items=20)

        assert probe.items_found == 30
        assert probe.items_used == 20

    async def test_plugin_pages_only_to_max_items(self) -> None:
        """Plugins paged up to their 1000-result limit for a probe that
        counts 20 results, and long lists ran into the probe's timeout."""
        seen: list[int | None] = []

        async def _search(query: str, category: int | None = None) -> list:
            seen.append(search_max_results.get())
            return _make_results(3)

        registry = _mock_registry()
        registry.get.return_value.search = _search
        async with httpx.AsyncClient() as client:
            prober = MiniSearchProber(plugins=registry, http_client=client)
            probe = await prober.probe("sto", "query", 2000, max_items=20)

        assert probe.ok is True
        assert seen == [20]
        assert search_max_results.get() is None

    @respx.mock
    async def test_hoster_partial_reachable(self) -> None:
        results = _make_results(5)
        registry = _mock_registry(results=results)
        respx.head("https://hoster.com/file/0").respond(200)
        respx.head("https://hoster.com/file/1").respond(404)
        respx.head("https://hoster.com/file/2").respond(200)

        async with httpx.AsyncClient() as client:
            prober = MiniSearchProber(
                plugins=registry,
                http_client=client,
                resolver_name=_SUPPORTED,
            )
            probe = await prober.probe("sto", "query", 2000)

        assert probe.hoster_checked == 3
        assert probe.hoster_reachable == 2

    @respx.mock
    async def test_hoster_network_error(self) -> None:
        results = _make_results(5)
        registry = _mock_registry(results=results)
        respx.head("https://hoster.com/file/0").mock(
            side_effect=httpx.ConnectError("refused")
        )
        respx.head("https://hoster.com/file/1").respond(200)
        respx.head("https://hoster.com/file/2").respond(200)

        async with httpx.AsyncClient() as client:
            prober = MiniSearchProber(
                plugins=registry,
                http_client=client,
                resolver_name=_SUPPORTED,
            )
            probe = await prober.probe("sto", "query", 2000)

        assert probe.hoster_checked == 3
        assert probe.hoster_reachable == 2

    @respx.mock
    async def test_started_at_is_set(self) -> None:
        registry = _mock_registry(results=[])
        async with httpx.AsyncClient() as client:
            prober = MiniSearchProber(
                plugins=registry,
                http_client=client,
                resolver_name=_SUPPORTED,
            )
            probe = await prober.probe("sto", "query", 2000)

        assert probe.started_at is not None
        assert probe.started_at.tzinfo is not None

    @respx.mock
    async def test_unsupported_hosters_not_head_checked(self) -> None:
        """Links to unsupported hosters should NOT be HEAD-checked."""
        results = _make_results(5, domain="unknown-hoster.org")
        registry = _mock_registry(results=results)
        # No respx routes needed — nothing should be fetched.

        async with httpx.AsyncClient() as client:
            prober = MiniSearchProber(
                plugins=registry,
                http_client=client,
                resolver_name=_SUPPORTED,
            )
            probe = await prober.probe("sto", "query", 2000)

        assert probe.ok is True
        assert probe.items_found == 5
        assert probe.hoster_total == 5
        assert probe.hoster_supported == 0
        assert probe.hoster_checked == 0
        assert probe.hoster_reachable == 0

    @respx.mock
    async def test_mixed_supported_and_unsupported(self) -> None:
        """Mixed results: supported hosters are checked, unsupported are not."""
        results = [
            SearchResult(
                title="VOE link",
                download_link="https://voe.sx/e/abc",
            ),
            SearchResult(
                title="Unknown link",
                download_link="https://unknown.org/file/1",
            ),
            SearchResult(
                title="Streamtape link",
                download_link="https://streamtape.com/v/xyz",
            ),
            SearchResult(
                title="Another unknown",
                download_link="https://badhost.net/x",
            ),
        ]
        registry = _mock_registry(results=results)
        respx.head("https://voe.sx/e/abc").respond(200)
        respx.head("https://streamtape.com/v/xyz").respond(200)

        async with httpx.AsyncClient() as client:
            prober = MiniSearchProber(
                plugins=registry,
                http_client=client,
                resolver_name=_SUPPORTED,
            )
            probe = await prober.probe("sto", "query", 2000)

        assert probe.hoster_total == 4
        assert probe.hoster_supported == 2
        # Only supported links are HEAD-checked (2 supported, < check_count of 3).
        assert probe.hoster_checked == 2
        assert probe.hoster_reachable == 2

    @respx.mock
    async def test_without_a_lookup_no_link_is_checked(self) -> None:
        """Without the registry's lookup no link counts as supported, and
        none is HEAD-checked."""
        results = _make_results(5)
        registry = _mock_registry(results=results)

        async with httpx.AsyncClient() as client:
            prober = MiniSearchProber(
                plugins=registry,
                http_client=client,
            )
            probe = await prober.probe("sto", "query", 2000)

        assert probe.hoster_total == 5
        assert probe.hoster_supported == 0
        assert probe.hoster_checked == 0


async def _probe(links: list[str], lookup: Callable[[str], str | None]) -> ProbeResult:
    results = [
        SearchResult(title=f"Result {i}", download_link=link)
        for i, link in enumerate(links)
    ]
    async with httpx.AsyncClient() as client:
        prober = MiniSearchProber(
            plugins=_mock_registry(results=results),
            http_client=client,
            resolver_name=lookup,
        )
        return await prober.probe("sto", "query", 2000)


class TestSupportedLinks:
    """A link is supported where the registry's dispatch finds its resolver."""

    @respx.mock
    async def test_a_claimed_host_counts_as_supported(self) -> None:
        # The mediathek resolver claims the broadcasters' Akamai hosts by their
        # full names; their second-level name "akamaihd" names no resolver
        mediathek = SimpleNamespace(
            name="mediathek",
            supported_domains=frozenset({"ard-mcdn"}),
            supported_hosts=frozenset({"nrodlzdf-a.akamaihd.net"}),
        )
        link = "https://nrodlzdf-a.akamaihd.net/de/zdf/film_3360k_p36v17.mp4"
        respx.head(link).respond(200)

        probe = await _probe([link], _lookup(mediathek))

        assert (probe.hoster_total, probe.hoster_supported) == (1, 1)
        assert (probe.hoster_checked, probe.hoster_reachable) == (1, 1)

    @respx.mock
    async def test_a_second_level_name_or_an_alias_still_counts(self) -> None:
        voe = SimpleNamespace(name="voe")
        vidhide = SimpleNamespace(
            name="vidhide", supported_domains=frozenset({"filelions"})
        )
        links = ["https://voe.sx/e/abc", "https://filelions.to/v/xyz"]
        for link in links:
            respx.head(link).respond(200)

        probe = await _probe(links, _lookup(voe, vidhide))

        assert (probe.hoster_total, probe.hoster_supported) == (2, 2)
        assert probe.hoster_checked == 2

    @respx.mock
    async def test_an_unknown_host_does_not_count(self) -> None:
        # Another host of the claimed zone, and a host no resolver names
        mediathek = SimpleNamespace(
            name="mediathek", supported_hosts=frozenset({"nrodlzdf-a.akamaihd.net"})
        )
        links = ["https://other-a.akamaihd.net/video.mp4", "https://unknown.org/file/1"]

        probe = await _probe(links, _lookup(mediathek))

        assert (probe.hoster_total, probe.hoster_supported) == (2, 0)
        assert probe.hoster_checked == 0
