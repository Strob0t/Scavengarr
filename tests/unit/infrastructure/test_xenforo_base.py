"""Tests for XenForoPluginBase (shared base of the XenForo download forums)."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from scavengarr.infrastructure.plugins import xenforo
from scavengarr.infrastructure.plugins.xenforo import XenForoPluginBase

_DOMAINS = ["forum.example"]
_NODES = {
    8: 2000,  # Filme / HD
    9: 2000,  # Filme / UHD
    12: 5000,  # Serien
    27: 5070,  # Anime
    51: 4050,  # Spiele / PC
    54: 1000,  # Spiele / Sony
    61: 4000,  # Software / Windows
}
_CREDENTIALS = {
    "SCAVENGARR_TESTFORUM_USERNAME": "testuser",
    "SCAVENGARR_TESTFORUM_PASSWORD": "testpass",
}


class _Forum(XenForoPluginBase):
    name = "testforum"
    _domains = _DOMAINS
    _node_categories = _NODES


def _resp(text: str, status: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.text = text
    resp.raise_for_status = MagicMock()
    if status >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    return resp


def _cookie(name: str, domain: str) -> MagicMock:
    cookie = MagicMock()
    cookie.name = name
    cookie.domain = domain
    return cookie


def _row(thread: str, title: str, forum: str) -> str:
    return (
        f'<h3 class="contentRow-title"><a href="/threads/{thread}/">{title}</a></h3>'
        f'<a href="/forums/{forum}/">Forum</a>'
    )


_THREAD_HTML = """
<html><body><div class="bbWrapper">
  <a href="https://hide.cx/container/abc">Online rapidgator.net</a>
  <a href="https://hide.cx/container/def">Online ddownload.com</a>
</div></body></html>
"""


class _Site:
    """A mocked XenForo forum answering the plugin's requests by URL."""

    def __init__(
        self,
        pages: list[str],
        *,
        threads: dict[str, str] | None = None,
        guest_answers: int = 0,
        cookie_domain: str = ".forum.example",
    ) -> None:
        self.pages = pages  # search result pages; page N+1 at /search/1/?page=N+1
        self.threads = threads or {}
        self.guest_answers = guest_answers  # searches answered as for a guest
        self.searches: list[dict[str, object]] = []
        self.logins: list[dict[str, object]] = []
        self.client = AsyncMock(spec=httpx.AsyncClient)
        self.client.get = AsyncMock(side_effect=self._get)
        self.client.post = AsyncMock(side_effect=self._post)
        self.client.cookies = MagicMock()
        self.client.cookies.jar = [_cookie("xf_user", cookie_domain)]

    async def _post(self, url: str, **kwargs: object) -> MagicMock:
        data = kwargs.get("data")
        assert isinstance(data, dict)
        if str(url).endswith("/login/login"):
            self.logins.append(data)
            return _resp('<html data-csrf="new,token" data-logged-in="true"></html>')
        self.searches.append(data)
        if len(self.searches) <= self.guest_answers:
            return _resp('<html data-logged-in="false"><body></body></html>')
        return _resp(self.pages[0])

    async def _get(self, url: str, **kwargs: object) -> MagicMock:
        url = str(url)
        if url.endswith("/login/"):
            return _resp('<input type="hidden" name="_xfToken" value="login-token">')
        if "/search/1/?page=" in url:
            return _resp(self.pages[int(url.rsplit("=", 1)[1]) - 1])
        for thread, html in self.threads.items():
            if f"/threads/{thread}/" in url:
                return _resp(html)
        return _resp(_THREAD_HTML)


def _plugin(site: _Site, *, logged_in: bool = True) -> _Forum:
    plugin = _Forum()
    plugin._client = site.client
    if logged_in:
        plugin._logged_in = True
        plugin._csrf_token = "old,token"
    return plugin


# ---------------------------------------------------------------------------
# Parsers and helpers
# ---------------------------------------------------------------------------


class TestLoginTokenParser:
    def test_extracts_token(self) -> None:
        parser = xenforo._LoginTokenParser()
        parser.feed(
            '<form action="/login/login" method="post">'
            '<input type="hidden" name="_xfToken" value="abc123,1234567890">'
            '<input type="text" name="login"></form>'
        )
        assert parser.token == "abc123,1234567890"

    def test_no_token(self) -> None:
        parser = xenforo._LoginTokenParser()
        parser.feed("<html><body><form></form></body></html>")
        assert parser.token == ""

    def test_first_token_wins(self) -> None:
        parser = xenforo._LoginTokenParser()
        parser.feed(
            '<input type="hidden" name="_xfToken" value="first_token">'
            '<input type="hidden" name="_xfToken" value="second_token">'
        )
        assert parser.token == "first_token"


class TestSearchResultParser:
    def _parse(self, html: str) -> xenforo._SearchResultParser:
        parser = xenforo._SearchResultParser("https://forum.example")
        parser.feed(html)
        parser.flush_pending()
        return parser

    def test_parses_results_with_forum(self) -> None:
        parser = self._parse(
            _row("batman-forever-uhd.12345", "Batman Forever UHD", "hd.8")
            + _row("inception-4k.67890", "Inception 4K", "uhd-4k.9")
        )

        assert [r["title"] for r in parser.results] == [
            "Batman Forever UHD",
            "Inception 4K",
        ]
        assert parser.results[0]["url"] == (
            "https://forum.example/threads/batman-forever-uhd.12345/"
        )
        assert parser.results[1]["forum_href"] == "/forums/uhd-4k.9/"

    def test_next_page_from_page_nav_jump(self) -> None:
        # Search result pages use "?page=N" (live data-load.me, myboerse.bz)
        parser = self._parse(
            '<a href="/search/67317750/?page=2&amp;q=Iron+Man" '
            'class="pageNav-jump pageNav-jump--next">Weiter</a>'
        )
        assert parser.next_page_url == "/search/67317750/?page=2&q=Iron+Man"

    def test_next_page_from_link_text(self) -> None:
        parser = self._parse('<a href="/search/12345/?page-2">Nächste</a>')
        assert parser.next_page_url == "/search/12345/?page-2"

    def test_numbered_page_link_is_not_next(self) -> None:
        parser = self._parse('<a href="/search/12345/?page=32">32</a>')
        assert parser.next_page_url == ""

    def test_empty_results(self) -> None:
        parser = self._parse("<html><body><p>No results found.</p></body></html>")
        assert parser.results == []
        assert parser.next_page_url == ""

    def test_result_without_forum(self) -> None:
        parser = self._parse(
            '<h3 class="contentRow-title">'
            '<a href="/threads/orphan.999/">Orphan Result</a></h3>'
        )
        assert parser.results == [
            {
                "title": "Orphan Result",
                "url": "https://forum.example/threads/orphan.999/",
                "forum": "",
                "forum_href": "",
            }
        ]

    def test_flush_pending_emits_last_result(self) -> None:
        parser = self._parse(
            _row("first.100", "First", "hd.8")
            + '<h3 class="contentRow-title"><a href="/threads/last.200/">Last</a></h3>'
        )
        assert [r["title"] for r in parser.results] == ["First", "Last"]


class TestThreadPostParser:
    def _links(self, html: str) -> list[dict[str, str]]:
        parser = xenforo._ThreadPostParser()
        parser.feed(html)
        return parser.links

    def test_extracts_container_links(self) -> None:
        links = self._links(
            '<div class="bbWrapper">'
            '<a href="https://hide.cx/container/abc123">Online rapidgator.net</a>'
            '<a href="https://filecrypt.cc/Container/xyz.html">Online ddownload.com</a>'
            "</div>"
        )
        assert links == [
            {"hoster": "rapidgator", "link": "https://hide.cx/container/abc123"},
            {"hoster": "ddownload", "link": "https://filecrypt.cc/Container/xyz.html"},
        ]

    def test_other_links_skipped(self) -> None:
        links = self._links(
            '<div class="bbWrapper">'
            '<a href="https://www.imdb.com/title/tt123">IMDB</a>'
            '<a href="https://youtube.com/watch?v=abc">Trailer</a>'
            '<a href="https://hide.cx/container/abc123">Online rapidgator.net</a>'
            "</div>"
        )
        assert [link["link"] for link in links] == ["https://hide.cx/container/abc123"]

    def test_affiliate_xtra_link_is_no_download(self) -> None:
        """myboerse's /xtra/?x=<title> always redirects to one Rapidgator file,
        ``www.MyBoerse.bz_Premium_Download.txt`` (checked live)."""
        links = self._links(
            '<div class="bbWrapper">'
            '<a href="https://myboerse.bz/xtra/?x=Batman">Batman Download</a>'
            "</div>"
        )
        assert links == []

    def test_links_outside_bbwrapper_ignored(self) -> None:
        links = self._links(
            '<a href="https://hide.cx/container/outside">Outside</a>'
            '<div class="bbWrapper">'
            '<a href="https://hide.cx/container/inside">Inside</a></div>'
        )
        assert [link["link"] for link in links] == ["https://hide.cx/container/inside"]

    def test_duplicate_links_deduplicated(self) -> None:
        links = self._links(
            '<div class="bbWrapper">'
            '<a href="https://hide.cx/container/abc123">rapidgator.net</a>'
            '<a href="https://hide.cx/container/abc123">rapidgator.net</a></div>'
        )
        assert len(links) == 1

    def test_nested_divs_do_not_exit_early(self) -> None:
        links = self._links(
            '<div class="bbWrapper"><div class="bbCodeBlock">'
            '<div class="bbCodeBlock-content">NFO</div></div>'
            '<a href="https://hide.cx/container/abc123">Online rapidgator.net</a>'
            "</div>"
        )
        assert links == [
            {"hoster": "rapidgator", "link": "https://hide.cx/container/abc123"}
        ]

    def test_multiple_posts(self) -> None:
        links = self._links(
            '<div class="bbWrapper">'
            '<a href="https://hide.cx/container/aaa">Online rapidgator.net</a></div>'
            '<div class="bbWrapper">'
            '<a href="https://hide.cx/container/bbb">Online ddownload.com</a></div>'
        )
        assert len(links) == 2

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.keeplinks.org/p53/abc123",
            "https://tolink.to/abc123",
            "https://share-links.biz/_abc",
        ],
    )
    def test_other_containers_accepted(self, url: str) -> None:
        links = self._links(
            f'<div class="bbWrapper"><a href="{url}">RapidGator</a></div>'
        )
        assert [link["link"] for link in links] == [url]


class TestHelpers:
    def test_is_container_host(self) -> None:
        assert xenforo._is_container_host("hide.cx")
        assert xenforo._is_container_host("www.hide.cx")
        assert xenforo._is_container_host("keeplinks.org")
        assert not xenforo._is_container_host("nothide.cx")
        assert not xenforo._is_container_host("imdb.com")

    def test_hoster_from_text(self) -> None:
        assert xenforo._hoster_from_text("Online rapidgator.net") == "rapidgator"
        assert xenforo._hoster_from_text("DDownload") == "ddownload"
        assert xenforo._hoster_from_text("") == ""

    def test_hoster_from_url(self) -> None:
        assert xenforo._hoster_from_url("https://hide.cx/container/abc") == "hide"
        assert (
            xenforo._hoster_from_url("https://www.keeplinks.org/p53/a") == "keeplinks"
        )

    def test_node_id_from_url(self) -> None:
        assert xenforo._node_id_from_url("/forums/hd.8/") == 8
        assert xenforo._node_id_from_url("/forums/uhd-4k.75/") == 75
        assert xenforo._node_id_from_url("/other/path") is None


# ---------------------------------------------------------------------------
# Category → forum nodes
# ---------------------------------------------------------------------------


class TestNodeSelection:
    def test_parent_covers_its_children(self) -> None:
        assert sorted(_Forum()._nodes_for(5000)) == [12, 27]
        assert sorted(_Forum()._nodes_for(4000)) == [51, 61]

    def test_child_matches_itself(self) -> None:
        assert _Forum()._nodes_for(5070) == [27]
        assert _Forum()._nodes_for(1000) == [54]

    def test_child_the_forum_does_not_tell_apart_uses_its_parent(self) -> None:
        # All films are 2000: a Movies/HD request searches the film forums
        assert sorted(_Forum()._nodes_for(2040)) == [8, 9]

    def test_category_without_section(self) -> None:
        assert _Forum()._nodes_for(8000) == []
        assert _Forum()._nodes_for(3030) == []


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


class TestLogin:
    async def test_login_stores_the_session_token(self) -> None:
        site = _Site([""])
        plugin = _plugin(site, logged_in=False)

        with patch.dict(os.environ, _CREDENTIALS):
            await plugin._login()

        assert plugin._logged_in is True
        assert plugin._csrf_token == "new,token"
        assert site.logins == [
            {
                "login": "testuser",
                "password": "testpass",
                "_xfToken": "login-token",
                "remember": "1",
            }
        ]

    async def test_missing_credentials(self) -> None:
        plugin = _plugin(_Site([""]), logged_in=False)

        with (
            patch.dict(os.environ, {}, clear=True),
            pytest.raises(RuntimeError, match="SCAVENGARR_TESTFORUM_USERNAME"),
        ):
            await plugin._login()

    async def test_login_page_without_token(self) -> None:
        site = _Site([""])
        site.client.get = AsyncMock(return_value=_resp("<html>No token</html>"))
        plugin = _plugin(site, logged_in=False)

        with (
            patch.dict(os.environ, _CREDENTIALS),
            pytest.raises(RuntimeError, match="_xfToken"),
        ):
            await plugin._login()

    async def test_login_page_unreachable(self) -> None:
        site = _Site([""])
        site.client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        plugin = _plugin(site, logged_in=False)

        with (
            patch.dict(os.environ, _CREDENTIALS),
            pytest.raises(RuntimeError, match="_xfToken"),
        ):
            await plugin._login()

    async def test_no_session_cookie(self) -> None:
        site = _Site([""])
        site.client.cookies.jar = []
        plugin = _plugin(site, logged_in=False)

        with (
            patch.dict(os.environ, _CREDENTIALS),
            pytest.raises(RuntimeError, match="session cookie"),
        ):
            await plugin._login()

    async def test_other_forums_cookie_is_no_session(self) -> None:
        """The shared client may hold another XenForo forum's cookie."""
        site = _Site([""], cookie_domain="other-forum.example")
        plugin = _plugin(site, logged_in=False)

        with (
            patch.dict(os.environ, _CREDENTIALS),
            pytest.raises(RuntimeError, match="session cookie"),
        ):
            await plugin._login()

    async def test_page_without_csrf_token(self) -> None:
        site = _Site([""])
        site.client.post = AsyncMock(return_value=_resp("<html></html>"))
        plugin = _plugin(site, logged_in=False)

        with (
            patch.dict(os.environ, _CREDENTIALS),
            pytest.raises(RuntimeError, match="no CSRF token"),
        ):
            await plugin._login()

    async def test_session_is_reused(self) -> None:
        site = _Site([""])
        plugin = _plugin(site)

        await plugin._login()

        site.client.get.assert_not_awaited()


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class TestSearch:
    async def test_search_returns_thread_links(self) -> None:
        site = _Site([_row("batman-4k.123", "Batman 4K", "uhd-4k.9")])
        plugin = _plugin(site)

        results = await plugin.search("batman")

        assert [r.title for r in results] == ["Batman 4K"]
        assert results[0].category == 2000
        assert results[0].source_url == "https://forum.example/threads/batman-4k.123/"
        assert results[0].download_link == "https://hide.cx/container/abc"
        assert [dl["hoster"] for dl in results[0].download_links] == [
            "rapidgator",
            "ddownload",
        ]

    async def test_search_posts_the_session_token_as_post_search(self) -> None:
        """XenForo rejects POSTs without the session's _xfToken (HTTP 400) and
        ignores c[nodes][] unless search_type=post (both checked live)."""
        site = _Site([""])
        plugin = _plugin(site)

        await plugin.search("batman")

        sent = site.searches[0]
        assert sent["_xfToken"] == "old,token"
        assert sent["search_type"] == "post"
        assert sent["keywords"] == "batman"
        assert "c[nodes][]" not in sent  # no category: every forum

    async def test_category_searches_its_forums(self) -> None:
        site = _Site([_row("show.1", "Show S01", "serien.12")])
        plugin = _plugin(site)

        results = await plugin.search("show", category=5000)

        assert sorted(site.searches[0]["c[nodes][]"]) == ["12", "27"]
        assert [r.category for r in results] == [5000]

    async def test_category_without_section_sends_no_request(self) -> None:
        site = _Site([""])
        plugin = _plugin(site)

        assert await plugin.search("batman", category=8000) == []
        site.client.post.assert_not_awaited()
        site.client.get.assert_not_awaited()

    async def test_result_of_an_unknown_forum_is_other(self) -> None:
        site = _Site([_row("new.1", "New Thing", "neu.999")])
        plugin = _plugin(site)

        results = await plugin.search("new")

        assert [r.category for r in results] == [8000]

    async def test_follows_the_next_page_link(self) -> None:
        next_link = (
            '<a href="/search/1/?page=2" class="pageNav-jump pageNav-jump--next">'
            "Weiter</a>"
        )
        site = _Site(
            [
                _row("a.1", "Page 1 Result", "hd.8") + next_link,
                _row("b.2", "Page 2 Result", "hd.8"),
            ]
        )
        plugin = _plugin(site)

        results = await plugin.search("result")

        assert sorted(r.title for r in results) == ["Page 1 Result", "Page 2 Result"]

    async def test_thread_without_links_is_skipped(self) -> None:
        site = _Site(
            [_row("a.1", "No Links", "hd.8")],
            threads={"a.1": '<div class="bbWrapper"><p>Just text.</p></div>'},
        )
        plugin = _plugin(site)

        assert await plugin.search("x") == []

    async def test_guest_answer_logs_in_again(self) -> None:
        """The XenForo session and its CSRF token expire."""
        site = _Site([_row("batman-4k.123", "Batman 4K", "uhd-4k.9")], guest_answers=1)
        plugin = _plugin(site)

        with patch.dict(os.environ, _CREDENTIALS):
            results = await plugin.search("batman")

        assert [r.title for r in results] == ["Batman 4K"]
        assert plugin._csrf_token == "new,token"
        assert site.searches[1]["_xfToken"] == "new,token"

    async def test_rejected_after_a_new_login_returns_nothing(self) -> None:
        site = _Site([""], guest_answers=2)
        plugin = _plugin(site)

        with patch.dict(os.environ, _CREDENTIALS):
            assert await plugin.search("batman") == []

        assert len(site.searches) == 2


class TestCleanup:
    async def test_cleanup_resets_the_session(self) -> None:
        site = _Site([""])
        plugin = _plugin(site)

        await plugin.cleanup()

        site.client.aclose.assert_awaited_once()
        assert plugin._client is None
        assert plugin._logged_in is False
        assert plugin._csrf_token == ""
