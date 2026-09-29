"""Tests for the data-load.me plugin.

Login, search, pagination and link parsing live in ``XenForoPluginBase``
(``test_xenforo_base.py``); these tests cover the plugin's identity and its
forum map.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from scavengarr.infrastructure.plugins.xenforo import XenForoPluginBase

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "dataload.py"


def _load_dataload_module() -> ModuleType:
    """Load dataload.py plugin via importlib (same as plugin loader)."""
    spec = importlib.util.spec_from_file_location("dataload_plugin", str(_PLUGIN_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_DataloadPlugin = _load_dataload_module().DataloadPlugin
_NODES = _DataloadPlugin._node_categories


class TestPluginAttributes:
    def test_identity(self) -> None:
        plugin = _DataloadPlugin()
        assert isinstance(plugin, XenForoPluginBase)
        assert plugin.name == "dataload"
        assert plugin.provides == "download"
        assert plugin.mode == "httpx"
        assert plugin._domains == ["www.data-load.me"]

    def test_credentials_from_env(self) -> None:
        env = {
            "SCAVENGARR_DATALOAD_USERNAME": "user",
            "SCAVENGARR_DATALOAD_PASSWORD": "pass",
        }
        with patch.dict(os.environ, env):
            assert _DataloadPlugin()._credentials() == ("user", "pass")


class TestForumMap:
    """Nodes and names of the live forum index (checked 2026-09-29)."""

    def test_films(self) -> None:
        # Filme and Animation / Zeichentrick with their SD ... Complete Bluray
        for node in (6, 7, 8, 9, 10, 11, 95, 34, 35, 36, 37, 38, 39, 99):
            assert _NODES[node] == 2000, node

    def test_foreign_films_are_mapped(self) -> None:
        # Fremdsprachige Filme and its subforums were missing
        for node in range(161, 168):
            assert _NODES[node] == 2010, node

    def test_series_anime_documentaries(self) -> None:
        assert {_NODES[n] for n in (12, 13, 14, 15, 16, 96, 116)} == {5000}
        assert {_NODES[n] for n in (27, 28, 29, 30, 31, 98)} == {5070}
        assert {_NODES[n] for n in (17, 18, 19, 147, 145, 110, 97)} == {5080}

    def test_consoles(self) -> None:
        # Sony, Microsoft, Nintendo were filed as PC games
        assert {_NODES[n] for n in (54, 55, 56)} == {1000}

    def test_games_and_software_are_pc(self) -> None:
        assert _NODES[51] == 4050  # Spiele / PC
        assert _NODES[61] == 4000  # Software / Windows
        assert _NODES[64] == 4030  # Software / Mac
        assert _NODES[58] == _NODES[65] == 4060  # iOS
        assert _NODES[57] == _NODES[66] == 4070  # Android

    def test_no_tv_foreign_label(self) -> None:
        # 5020 is TV/Foreign; the software forums used to carry it
        assert 5020 not in _NODES.values()

    def test_request_forums_are_not_searched(self) -> None:
        for node in (25, 26, 32, 33, 40, 49, 60, 77, 78):  # "... Suche"
            assert node not in _NODES

    def test_movies_request_searches_the_film_forums(self) -> None:
        nodes = _DataloadPlugin()._nodes_for(2000)
        assert {8, 99, 162} <= set(nodes)
        assert 12 not in nodes
        assert 109 not in nodes  # Musikvideos
