"""Tests for the reading of a site's official domain list (row 69)."""

from __future__ import annotations

import gzip
from pathlib import Path

import httpx
import pytest
import respx

from scavengarr.infrastructure.plugins.domain_list import (
    MAX_LISTED_HOSTS,
    listed_hosts,
    read_domain_list,
    serves_site,
)
from scavengarr.infrastructure.plugins.registry import PluginRegistry

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "domain_lists"


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text()


class TestListedHosts:
    def test_filmo_names_its_alternative_urls_only(self) -> None:
        """filmo.domains names filmo.to; its ``siteUrl`` repeats it, and the
        censorship probe script on serienstream.to and the page's canonical
        ``example.com`` are not addresses of the site."""
        hosts = listed_hosts(
            _fixture("filmo_index.html"), [_fixture("filmo_bundle.js")]
        )

        assert hosts == ["filmo.to"]

    def test_aniworld_names_two_hosts_and_no_ip_literal(self) -> None:
        hosts = listed_hosts(
            _fixture("aniworld_index.html"), [_fixture("aniworld_bundle.js")]
        )

        assert hosts == ["aniworld.to", "aniworld.cc"]

    def test_serien_names_two_hosts_and_no_ip_literal(self) -> None:
        hosts = listed_hosts(
            _fixture("serien_index.html"), [_fixture("serien_bundle.js")]
        )

        assert hosts == ["serienstream.to", "serienstream.cx"]

    def test_anchors_of_a_plain_page_count(self) -> None:
        page = (
            '<ul><li><a href="https://Mirror-One.example/">one</a></li>'
            '<li><a class="x" href="https://mirror-two.example/path">two</a></li>'
            '<li><a href="/relative">no host</a></li>'
            '<li><a href="https://10.0.0.1/">an address</a></li></ul>'
        )

        assert listed_hosts(page, []) == ["mirror-one.example", "mirror-two.example"]

    def test_at_most_twenty_hosts(self) -> None:
        script = ",".join(f'{{url:"https://m{n}.example"}}' for n in range(30))

        hosts = listed_hosts("", [script])

        assert len(hosts) == MAX_LISTED_HOSTS == 20
        assert hosts[0] == "m0.example"

    @pytest.mark.parametrize(
        "url",
        ["http://186.2.175.5", "https://[2001:db8::1]/", "https://localhost/"],
    )
    def test_ip_literals_and_bare_names_are_refused(self, url: str) -> None:
        assert listed_hosts("", [f'{{url:"{url}"}}']) == []


class TestReadDomainList:
    @pytest.mark.asyncio
    @respx.mock
    async def test_reads_the_page_and_its_bundle(self) -> None:
        respx.get("https://filmo.domains/").respond(
            200, text=_fixture("filmo_index.html")
        )
        respx.get("https://filmo.domains/assets/index-fixture.js").respond(
            200, text=_fixture("filmo_bundle.js")
        )
        async with httpx.AsyncClient() as client:
            hosts = await read_domain_list(client, "https://filmo.domains/", "UA")

        assert hosts == ["filmo.to"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_a_script_of_another_host_is_not_read(self) -> None:
        page = (
            '<script type="module" src="https://cdn.example/app.js"></script>'
            '<a href="https://site.example/">site</a>'
        )
        respx.get("https://list.example/").respond(200, text=page)
        cdn = respx.get("https://cdn.example/app.js")
        async with httpx.AsyncClient() as client:
            hosts = await read_domain_list(client, "https://list.example/", "UA")

        assert hosts == ["site.example"]
        assert not cdn.called

    @pytest.mark.asyncio
    @respx.mock
    async def test_the_list_host_itself_is_not_an_address(self) -> None:
        page = '<a href="https://list.example/faq">faq</a><a href="https://s.example">s</a>'
        respx.get("https://list.example/").respond(200, text=page)
        async with httpx.AsyncClient() as client:
            hosts = await read_domain_list(client, "https://list.example/", "UA")

        assert hosts == ["s.example"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_a_list_that_is_down_raises(self) -> None:
        respx.get("https://list.example/").respond(503)
        async with httpx.AsyncClient() as client:
            with pytest.raises(httpx.HTTPStatusError):
                await read_domain_list(client, "https://list.example/", "UA")

    @pytest.mark.asyncio
    @respx.mock
    async def test_a_list_that_names_no_host_raises(self) -> None:
        respx.get("https://list.example/").respond(200, text="<p>moved</p>")
        async with httpx.AsyncClient() as client:
            with pytest.raises(ValueError, match="no host"):
                await read_domain_list(client, "https://list.example/", "UA")


class TestServesSite:
    @pytest.mark.asyncio
    async def test_the_marker_decides(self) -> None:
        marker = 'a[href*="filmo.domains"]'
        site = '<footer><a href="https://filmo.domains/">Domains</a></footer>'
        parked = "<h1>This domain is for sale</h1>"

        assert await serves_site(site, marker)
        assert not await serves_site(parked, marker)


_ROOT = Path(__file__).resolve().parents[3]


def _listing_plugins() -> list[tuple[str, str]]:
    """(plugin, captured page) for every plugin that names a domain list."""
    registry = PluginRegistry(_ROOT / "plugins")
    pairs = []
    for name in registry.list_names():
        if getattr(registry.get(name), "domain_list_url", None):
            pages = sorted((_ROOT / "tests/fixtures/html" / name).glob("*.html.gz"))
            pairs += [(name, page.name) for page in pages]
    return pairs


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "page"), _listing_plugins())
async def test_the_marker_is_on_the_sites_own_pages(name: str, page: str) -> None:
    """A plugin naming a domain list declares a marker its site shows: on
    every captured page but a redirect interstitial."""
    plugin = PluginRegistry(_ROOT / "plugins").get(name)
    marker = getattr(plugin, "domain_marker", None)
    assert marker
    html = gzip.decompress((_ROOT / "tests/fixtures/html" / name / page).read_bytes())

    assert await serves_site(html.decode(), marker) == (page != "interstitial.html.gz")
