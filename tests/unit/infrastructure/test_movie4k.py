"""Tests for the movie4k plugin (movie4k.sx)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from scavengarr.infrastructure.plugins import data_api

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "movie4k.py"


@pytest.fixture()
def mod():
    """Import movie4k plugin module."""
    spec = importlib.util.spec_from_file_location("movie4k", _PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["movie4k"] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop("movie4k", None)


class TestCollectStreams:
    def test_basic_collection(self, mod) -> None:
        streams = [
            {"stream": "https://voe.sx/e/a", "release": "R1", "source": "voe"},
            {"stream": "https://dood.to/d/b", "release": "R1", "source": "dood"},
        ]
        first, links = data_api.collect_streams(streams)
        assert first == "https://voe.sx/e/a"
        assert len(links) == 2

    def test_skips_empty_stream(self, mod) -> None:
        streams = [
            {"stream": "", "release": "R1"},
            {"stream": "https://voe.sx/e/a", "release": "R2"},
        ]
        first, links = data_api.collect_streams(streams)
        assert first == "https://voe.sx/e/a"
        assert len(links) == 1

    def test_skips_non_url_stream_values(self, mod) -> None:
        """API garbage like 'http-equiv=' is rejected."""
        streams = [
            {"stream": "http-equiv=", "release": "Garbage"},
            {"stream": "javascript:void(0)", "release": "XSS"},
            {"stream": "https://voe.sx/e/good", "release": "Good"},
        ]
        first, links = data_api.collect_streams(streams)
        assert len(links) == 1
        assert first == "https://voe.sx/e/good"

    def test_normalizes_protocol_relative(self, mod) -> None:
        streams = [{"stream": "//voe.sx/e/a", "release": "R1"}]
        first, links = data_api.collect_streams(streams)
        assert first == "https://voe.sx/e/a"
        assert len(links) == 1

    def test_empty_streams(self, mod) -> None:
        first, links = data_api.collect_streams([])
        assert first == ""
        assert links == []


class TestSharedApiBehaviour:
    """movie4k used to be a stale copy of megakino_to; these broke there."""

    _ENTRY = {"_id": "abc", "title": "Show", "year": 2020}

    def _plugin(self, mod):
        p = mod.Movie4kPlugin()
        p.base_url = "https://movie4k.sx"
        return p

    def test_episode_request_keeps_only_that_episode(self, mod) -> None:
        detail = {
            "tv": 1,
            "s": 2,
            "streams": [
                {"stream": "https://voe.sx/e/e4", "e": 4},
                {"stream": "https://voe.sx/e/e5", "e": 5},
            ],
        }

        sr = self._plugin(mod)._build_search_result(
            self._ENTRY, detail, season=2, episode=5
        )

        assert [lnk["link"] for lnk in sr.download_links] == ["https://voe.sx/e/e5"]

    def test_deleted_streams_are_skipped(self, mod) -> None:
        detail = {
            "streams": [
                {"stream": "https://voe.sx/e/gone", "deleted": 1},
                {"stream": "https://dood.to/e/ok"},
            ]
        }

        sr = self._plugin(mod)._build_search_result(self._ENTRY, detail)

        assert sr.download_link == "https://dood.to/e/ok"

    def test_list_shaped_tmdb_movie_does_not_drop_the_title(self, mod) -> None:
        detail = {
            "streams": [{"stream": "https://voe.sx/e/a"}],
            "tmdb": {"movie": [{"movie_details": {"imdb_id": "tt0371746"}}]},
        }

        sr = self._plugin(mod)._build_search_result(self._ENTRY, detail)

        assert sr is not None
        assert sr.metadata["imdb_id"] == "tt0371746"
        assert sr.metadata["movie4k_id"] == "abc"
