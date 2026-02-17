"""Tests for the movie4k plugin (movie4k.sx)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

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
        first, links = mod._collect_streams(streams)
        assert first == "https://voe.sx/e/a"
        assert len(links) == 2

    def test_skips_empty_stream(self, mod) -> None:
        streams = [
            {"stream": "", "release": "R1"},
            {"stream": "https://voe.sx/e/a", "release": "R2"},
        ]
        first, links = mod._collect_streams(streams)
        assert first == "https://voe.sx/e/a"
        assert len(links) == 1

    def test_skips_non_url_stream_values(self, mod) -> None:
        """API garbage like 'http-equiv=' is rejected."""
        streams = [
            {"stream": "http-equiv=", "release": "Garbage"},
            {"stream": "javascript:void(0)", "release": "XSS"},
            {"stream": "https://voe.sx/e/good", "release": "Good"},
        ]
        first, links = mod._collect_streams(streams)
        assert len(links) == 1
        assert first == "https://voe.sx/e/good"

    def test_normalizes_protocol_relative(self, mod) -> None:
        streams = [{"stream": "//voe.sx/e/a", "release": "R1"}]
        first, links = mod._collect_streams(streams)
        assert first == "https://voe.sx/e/a"
        assert len(links) == 1

    def test_empty_streams(self, mod) -> None:
        first, links = mod._collect_streams([])
        assert first == ""
        assert links == []
