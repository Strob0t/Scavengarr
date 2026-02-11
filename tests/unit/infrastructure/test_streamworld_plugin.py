"""Tests for the streamworld.ws Python plugin (httpx-based)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock

import httpx
import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "streamworld.py"


def _load_module() -> ModuleType:
    """Load streamworld.py plugin via importlib."""
    spec = importlib.util.spec_from_file_location(
        "streamworld_plugin", str(_PLUGIN_PATH)
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mod = _load_module()
_StreamworldPlugin = _mod.StreamworldPlugin
_SearchResultParser = _mod._SearchResultParser
_DetailPageParser = _mod._DetailPageParser


def _make_plugin() -> object:
    """Create StreamworldPlugin instance."""
    return _StreamworldPlugin()


# ---------------------------------------------------------------------------
# Sample HTML fragments
# ---------------------------------------------------------------------------

_SEARCH_HTML = """\
<html><body>
<div id="content">
<table width="100%">
  <tr>
    <th></th>
    <th class="thlink"><a href="/suche/sortieren/nach/titel.html">Titel</a></th>
    <th></th>
    <th class="thlink"><a href="/suche/sortieren/nach/jahr.html">Jahr</a></th>
    <th>Genre(s)</th>
    <th class="thlink"><a href="/suche/sortieren/nach/imdb.html">IMDB</a></th>
  </tr>
  <tr>
    <td>Film</td>
    <td><span class="otherLittles">
      <a href="/film/6354-batman.html">Batman</a>
    </span></td>
    <td><img src="/images/languages/1.gif" alt="Deutsch"></td>
    <td><span class="otherLittles">
      <a href="/jahr/1989.html">1989</a>
    </span></td>
    <td>
      <span class="otherLittles"><a href="/genre/action.html">Action</a></span>,
      <span class="otherLittles"><a href="/genre/abenteuer.html">Abenteuer</a></span>
    </td>
    <td><span class="otherLittles">7.6 / 10</span></td>
  </tr>
  <tr bgcolor="#DBDBEA">
    <td>Serie</td>
    <td><span class="otherLittles">
      <a href="/serie/123-test-serie.html">Test Serie</a>
    </span></td>
    <td><img src="/images/languages/1.gif" alt="Deutsch"></td>
    <td><span class="otherLittles">
      <a href="/jahr/2022.html">2022</a>
    </span></td>
    <td>
      <span class="otherLittles"><a href="/genre/drama.html">Drama</a></span>
    </td>
    <td><span class="otherLittles">8.0 / 10</span></td>
  </tr>
</table>
</div>
</body></html>
"""

_DETAIL_HTML = """\
<html><body>
<div id="content">
  <h1>Batman</h1>
  <div class="genre">
    <a href="/genre/action.html">Action</a>,
    <a href="/genre/abenteuer.html">Abenteuer</a>
  </div>
  <p></p>
  <div>In Gotham City...</div>
  <a href="https://www.imdb.com/title/tt0096895/">7.6</a>
  <p></p>
  <table>
    <tr>
      <th><a href="/film/6354-batman/streams-12345.html">
        \u25b6 Batman.1989.German.DL.720p.BluRay.x264
      </a></th>
    </tr>
    <tr>
      <td>
        Verfuegbare Streams
        <a href="/film/6354-batman/streams-12345.html">
          <img alt="streamtape.com"></a>
        <a href="/film/6354-batman/streams-12345.html">
          <img alt="voe.sx"></a>
      </td>
      <td></td>
    </tr>
    <tr><td></td><td></td></tr>
  </table>
</div>
</body></html>
"""

_SERIE_DETAIL_HTML = """\
<html><body>
<div id="content">
  <h1>Test Serie</h1>
  <div class="genre">
    <a href="/genre/drama.html">Drama</a>
  </div>
  <p></p>
  <a href="https://www.imdb.com/title/tt1234567/">8.0</a>
  <p></p>
  <table>
    <tr>
      <th><a href="/serie/123-test-serie/streams-55555.html">
        \u25b6 Test.Serie.S01E01.German.DL.720p.WEB.x264
      </a></th>
    </tr>
    <tr>
      <td>
        Verfuegbare Streams
        <a href="/serie/123-test-serie/streams-55555.html"><img alt="voe.sx"></a>
      </td>
      <td></td>
    </tr>
  </table>
</div>
</body></html>
"""

_STREAMS_HTML = """\
<html><body>
<div id="content">
  <table>
    <tr>
      <th><a href="/film/6354-batman.html">
        \u25bc Batman.1989.German.DL.720p.BluRay.x264
      </a></th>
    </tr>
    <tr>
      <td>
        <table>
          <tr>
            <td>Streams</td>
            <td>Mirror</td>
          </tr>
          <tr>
            <td><img alt="streamtape.com">
              <strong>streamtape.com</strong></td>
            <td></td>
            <td><a href="/film/12345/stream/99001-st.html">
              <span>1</span></a></td>
          </tr>
          <tr>
            <td><img alt="voe.sx">
              <strong>voe.sx</strong></td>
            <td></td>
            <td><a href="/film/12345/stream/99002-voe.html">
              <span>1</span></a></td>
          </tr>
          <tr>
            <td><img alt="usenet.nl"><strong>usenet.nl</strong></td>
            <td></td>
            <td><a href="https://streamworld.co/red/?fn=Premium"><span>Premium</span></a></td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</div>
</body></html>
"""

_SERIE_STREAMS_HTML = """\
<html><body>
<div id="content">
  <table>
    <tr>
      <th><a href="/serie/123-test-serie.html">
        \u25bc Test.Serie.S01E01.German.DL.720p.WEB.x264
      </a></th>
    </tr>
    <tr>
      <td>
        <table>
          <tr>
            <td>Streams</td>
            <td>Mirror</td>
          </tr>
          <tr>
            <td><img alt="voe.sx">
              <strong>voe.sx</strong></td>
            <td></td>
            <td><a href="/serie/55555/stream/88001-voe.html">
              <span>1</span></a></td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</div>
</body></html>
"""


# ---------------------------------------------------------------------------
# Parser unit tests
# ---------------------------------------------------------------------------


class TestSearchResultParser:
    """Tests for _SearchResultParser."""

    def test_parses_search_results(self) -> None:
        parser = _SearchResultParser("https://streamworld.ws")
        parser.feed(_SEARCH_HTML)

        assert len(parser.results) == 2

        film = parser.results[0]
        assert film["title"] == "Batman"
        assert film["type"] == "film"
        assert film["url"] == "https://streamworld.ws/film/6354-batman.html"
        assert film["year"] == "1989"
        assert "Action" in film["genres"]
        assert "Abenteuer" in film["genres"]

        serie = parser.results[1]
        assert serie["title"] == "Test Serie"
        assert serie["type"] == "serie"
        assert serie["url"] == "https://streamworld.ws/serie/123-test-serie.html"
        assert serie["year"] == "2022"

    def test_empty_table_returns_no_results(self) -> None:
        html = "<html><body><table><tr><th>Header</th></tr></table></body></html>"
        parser = _SearchResultParser("https://streamworld.ws")
        parser.feed(html)
        assert len(parser.results) == 0

    def test_ignores_non_film_serie_links(self) -> None:
        html = """\
        <table>
          <tr><th>Type</th><th>Title</th><th></th><th>Year</th><th>Genre</th><th>IMDB</th></tr>
          <tr>
            <td>Film</td>
            <td><a href="/genre/action.html">Not a film link</a></td>
            <td></td><td></td><td></td><td></td>
          </tr>
        </table>
        """
        parser = _SearchResultParser("https://streamworld.ws")
        parser.feed(html)
        assert len(parser.results) == 0


class TestDetailPageParser:
    """Tests for _DetailPageParser."""

    def test_parses_releases(self) -> None:
        parser = _DetailPageParser("https://streamworld.ws")
        parser.feed(_DETAIL_HTML)

        assert len(parser.releases) == 1
        release = parser.releases[0]
        assert release["name"] == "Batman.1989.German.DL.720p.BluRay.x264"
        assert "/streams-12345.html" in release["streams_url"]

    def test_parses_imdb_url(self) -> None:
        parser = _DetailPageParser("https://streamworld.ws")
        parser.feed(_DETAIL_HTML)
        assert "imdb.com/title/tt0096895" in parser.imdb_url

    def test_parses_stream_links(self) -> None:
        parser = _DetailPageParser("https://streamworld.ws")
        parser.feed(_STREAMS_HTML)

        # Should have 2 stream links (usenet.nl skipped because no /stream/ in URL)
        assert len(parser.stream_links) == 2
        hosters = [s["hoster"] for s in parser.stream_links]
        assert "streamtape" in hosters
        assert "voe" in hosters

    def test_stream_links_have_full_urls(self) -> None:
        parser = _DetailPageParser("https://streamworld.ws")
        parser.feed(_STREAMS_HTML)

        for link in parser.stream_links:
            assert link["link"].startswith("https://streamworld.ws/")

    def test_empty_page(self) -> None:
        parser = _DetailPageParser("https://streamworld.ws")
        parser.feed("<html><body></body></html>")
        assert len(parser.releases) == 0
        assert len(parser.stream_links) == 0


# ---------------------------------------------------------------------------
# Plugin integration tests (mocked HTTP)
# ---------------------------------------------------------------------------


def _mock_response(text: str, status_code: int = 200) -> httpx.Response:
    """Create a mock httpx.Response."""
    return httpx.Response(
        status_code=status_code,
        text=text,
        request=httpx.Request("GET", "https://streamworld.ws/"),
    )


class TestStreamworldPlugin:
    """Tests for StreamworldPlugin with mocked HTTP."""

    @pytest.mark.asyncio
    async def test_search_returns_results(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        # AsyncMock resolves immediately, so gather runs tasks sequentially:
        # Batman: detail → streams, then Serie: detail → streams
        mock_client.post = AsyncMock(return_value=_mock_response(_SEARCH_HTML))
        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_DETAIL_HTML),  # Batman detail
                _mock_response(_STREAMS_HTML),  # Batman streams
                _mock_response(_SERIE_DETAIL_HTML),  # Serie detail
                _mock_response(_SERIE_STREAMS_HTML),  # Serie streams
            ]
        )

        plug._client = mock_client
        results = await plug.search("Batman")

        assert len(results) == 2
        assert results[0].title == "Batman"
        assert results[0].category == 2000  # Film
        assert results[1].title == "Test Serie"
        assert results[1].category == 5000  # Serie

    @pytest.mark.asyncio
    async def test_search_filters_by_category(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.post = AsyncMock(return_value=_mock_response(_SEARCH_HTML))
        # category=5000 filters to Serie only → 1 detail + 1 streams fetch
        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_SERIE_DETAIL_HTML),  # Serie detail
                _mock_response(_SERIE_STREAMS_HTML),  # Serie streams
            ],
        )

        plug._client = mock_client
        results = await plug.search("Batman", category=5000)  # TV only

        # Should only get the Serie result
        assert len(results) == 1
        assert results[0].title == "Test Serie"

    @pytest.mark.asyncio
    async def test_search_empty_query(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        empty_html = (
            "<html><body><table><tr><th>Hinweis</th></tr></table></body></html>"
        )
        mock_client.post = AsyncMock(return_value=_mock_response(empty_html))

        plug._client = mock_client
        results = await plug.search("xy")

        assert results == []

    @pytest.mark.asyncio
    async def test_search_handles_http_error(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.ConnectError("connection failed")
        )

        plug._client = mock_client
        results = await plug.search("test")

        assert results == []

    @pytest.mark.asyncio
    async def test_detail_page_error_skips_result(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.post = AsyncMock(return_value=_mock_response(_SEARCH_HTML))
        mock_client.get = AsyncMock(
            side_effect=httpx.ConnectError("detail page failed")
        )

        plug._client = mock_client
        results = await plug.search("Batman")

        assert results == []

    @pytest.mark.asyncio
    async def test_cleanup_closes_client(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()
        plug._client = mock_client

        await plug.cleanup()

        mock_client.aclose.assert_called_once()
        assert plug._client is None

    @pytest.mark.asyncio
    async def test_result_has_download_links(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.post = AsyncMock(return_value=_mock_response(_SEARCH_HTML))
        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_DETAIL_HTML),  # Batman detail
                _mock_response(_STREAMS_HTML),  # Batman streams
                _mock_response(_SERIE_DETAIL_HTML),  # Serie detail
                _mock_response(_SERIE_STREAMS_HTML),  # Serie streams
            ]
        )

        plug._client = mock_client
        results = await plug.search("Batman")

        assert len(results) >= 1
        first = results[0]
        assert first.download_links is not None
        assert len(first.download_links) >= 1
        assert first.download_link.startswith("https://")
        assert first.release_name is not None
        assert "Batman" in first.release_name

    @pytest.mark.asyncio
    async def test_result_metadata(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.post = AsyncMock(return_value=_mock_response(_SEARCH_HTML))
        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_DETAIL_HTML),  # Batman detail
                _mock_response(_STREAMS_HTML),  # Batman streams
                _mock_response(_SERIE_DETAIL_HTML),  # Serie detail
                _mock_response(_SERIE_STREAMS_HTML),  # Serie streams
            ]
        )

        plug._client = mock_client
        results = await plug.search("Batman")

        first = results[0]
        assert first.metadata.get("year") == "1989"
        assert "Action" in first.metadata.get("genres", "")
        assert first.metadata.get("imdb_url") is not None
