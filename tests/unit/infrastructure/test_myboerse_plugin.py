"""Tests for the myboerse.bz plugin.

Login, search, pagination and link parsing live in ``XenForoPluginBase``
(``test_xenforo_base.py``); these tests cover the plugin's identity, its
domain fallback and its forum map.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from scavengarr.infrastructure.plugins.xenforo import XenForoPluginBase

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "myboerse.py"


def _load_myboerse_module() -> ModuleType:
    """Load myboerse.py plugin via importlib (same as plugin loader)."""
    spec = importlib.util.spec_from_file_location("myboerse_plugin", str(_PLUGIN_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MyboersePlugin = _load_myboerse_module().MyboersePlugin
_DOMAINS = _MyboersePlugin._domains
_NODES = _MyboersePlugin._node_categories


def _make_plugin() -> object:
    """Create MyboersePlugin instance."""
    return _MyboersePlugin()


class TestPluginAttributes:
    def test_identity(self) -> None:
        plugin = _MyboersePlugin()
        assert isinstance(plugin, XenForoPluginBase)
        assert plugin.name == "myboerse"
        assert plugin.provides == "download"
        assert plugin.mode == "httpx"

    def test_domains_list(self) -> None:
        assert _DOMAINS == ["myboerse.bz", "myboerse.ws", "myboerse.me"]

    def test_credentials_from_env(self) -> None:
        env = {
            "SCAVENGARR_MYBOERSE_USERNAME": "user",
            "SCAVENGARR_MYBOERSE_PASSWORD": "pass",
        }
        with patch.dict(os.environ, env):
            assert _MyboersePlugin()._credentials() == ("user", "pass")


class TestDomainVerification:
    async def test_first_domain_reachable(self) -> None:
        plugin = _make_plugin()

        head_resp = MagicMock()
        head_resp.status_code = 200
        head_resp.url = httpx.URL(f"https://{_DOMAINS[0]}/")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(return_value=head_resp)
        plugin._client = mock_client

        await plugin._verify_domain()

        assert plugin._domain_verified is True
        assert plugin.base_url == f"https://{_DOMAINS[0]}"
        mock_client.head.assert_awaited_once()

    async def test_fallback_to_second_domain(self) -> None:
        plugin = _make_plugin()

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.url = httpx.URL(f"https://{_DOMAINS[1]}/")
        fail_exc = httpx.ConnectError("Connection refused")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(side_effect=[fail_exc, ok_resp])
        plugin._client = mock_client

        await plugin._verify_domain()

        assert plugin._domain_verified is True
        assert plugin.base_url == f"https://{_DOMAINS[1]}"

    async def test_all_domains_fail_uses_first(self) -> None:
        plugin = _make_plugin()

        fail_exc = httpx.ConnectError("Connection refused")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(side_effect=fail_exc)
        plugin._client = mock_client

        await plugin._verify_domain()

        assert plugin._domain_verified is True
        assert plugin.base_url == f"https://{_DOMAINS[0]}"

    async def test_skips_if_already_verified(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client = mock_client

        await plugin._verify_domain()

        mock_client.head.assert_not_awaited()


class TestForumMap:
    """Nodes and names of the live forum index (checked 2026-09-29)."""

    def test_videos(self) -> None:
        for node in (60, 61, 62, 75, 72, 67, 68, 74):
            assert _NODES[node] == 2000, node
        assert _NODES[71] == 2010  # Englisch
        assert _NODES[70] == 3020  # Konzerte / Musik
        assert _NODES[63] == 5000
        assert _NODES[64] == 5070
        assert _NODES[65] == 5080

    def test_consoles(self) -> None:
        assert {_NODES[n] for n in (28, 29, 30)} == {1000}

    def test_mobile_games_are_no_consoles(self) -> None:
        assert _NODES[31] == 4070  # Spiele / Android
        assert _NODES[32] == 4060  # Spiele / iPad / iPhone

    def test_games_and_software_are_pc(self) -> None:
        assert _NODES[24] == 4050  # PC Spiele
        assert _NODES[9] == 4000  # Software / Windows
        assert _NODES[10] == 4030  # Software / Mac
        assert _NODES[11] == _NODES[12] == 4060  # iPad / iPhone, Cydia Apps
        assert _NODES[13] == 4070  # Software / Android

    def test_audio_and_books(self) -> None:
        assert {_NODES[n] for n in (51, 50, 57, 113)} == {3000}
        assert _NODES[56] == 3040  # HQ Audio / Lossless
        assert _NODES[52] == _NODES[53] == 3030  # (Englische) Hörbücher
        assert _NODES[38] == _NODES[111] == 7000  # were missing

    def test_xxx_section(self) -> None:
        # Its "Filme" forum used to be labelled 2000 by a forum-name guess
        assert {_NODES[n] for n in (83, 84, 85, 86, 87, 88, 89, 92)} == {6000}

    def test_no_tv_foreign_label(self) -> None:
        # 5020 is TV/Foreign; the software forums used to carry it
        assert 5020 not in _NODES.values()

    def test_request_and_talk_forums_are_not_searched(self) -> None:
        for node in (15, 27, 43, 54, 55, 66, 90, 19, 33, 45, 58, 73, 91):
            assert node not in _NODES
