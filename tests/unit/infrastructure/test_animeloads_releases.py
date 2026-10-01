"""anime-loads.org: one Torznab result per release, links resolved on grab."""

from __future__ import annotations

import base64
import importlib.util
import json
import sys
from binascii import unhexlify
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog.testing
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from scavengarr.domain.plugins import GrabResolvingPlugin

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "animeloads.py"
_BASE_URL = "https://www.anime-loads.org"
_MEDIA = f"{_BASE_URL}/media/one-punch-man"
_GRAB_URL = f"{_MEDIA}?release=2"


@pytest.fixture()
def mod() -> Iterator[ModuleType]:
    spec = importlib.util.spec_from_file_location("animeloads", _PLUGIN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["animeloads"] = module
    spec.loader.exec_module(module)
    module._STEP_PAUSE_S = 0
    module._EPISODE_PAUSE_S = 0
    yield module
    sys.modules.pop("animeloads", None)


@pytest.fixture()
def plugin(mod: ModuleType) -> Any:
    p = mod.AnimeLoadsPlugin()
    p._domain_verified = True
    p.base_url = _BASE_URL
    return p


def _page() -> AsyncMock:
    page = AsyncMock()
    page.is_closed = MagicMock(return_value=False)
    return page


def _release(release_id: int = 2, **overrides: Any) -> dict[str, Any]:
    """Shape of _EXTRACT_RELEASES_JS output (live one-punch-man, 2026-09-29)."""
    release: dict[str, Any] = {
        "id": release_id,
        "label": f"Release {release_id}: 1080p |",
        "group": "Riitz-chan",
        "resolution": "1920x1080",
        "format": "MKV",
        "size": "Ø 742 MB (Package: ~8.70 GB)",
        "languages": ["Japanese"],
        "subtitles": ["German"],
        "password": "www.anime-loads.org",
        "notes": "WebRip",
        "episodes": 12,
    }
    release.update(overrides)
    return release


def _series(plugin: Any) -> Any:
    return plugin._build_search_result(
        {
            "title": "One Punch Man",
            "slug": "one-punch-man",
            "mediaUrl": _MEDIA,
            "type": "Anime Series",
            "year": "2015",
            "episodes": "12/12",
            "embedUrl": "https://voe.sx/e/abc",
        }
    )


# ---------------------------------------------------------------------------
# Release results
# ---------------------------------------------------------------------------


class TestReleaseResult:
    def test_release_fields(self, plugin: Any) -> None:
        series = _series(plugin)

        result = plugin._release_result(series, _release())

        assert result.title == (
            "One Punch Man (2015) [12/12] [1080p WebRip] [Japanese | Sub: German]"
            " [Riitz-chan]"
        )
        assert result.download_link == _GRAB_URL
        assert result.validated_links == [_GRAB_URL]
        assert result.source_url == _MEDIA
        assert result.size == "8.70 GB"
        assert result.download_links is None
        assert result.category == series.category
        assert result.metadata["archive_password"] == "www.anime-loads.org"
        assert result.metadata["release_group"] == "Riitz-chan"
        assert result.metadata["release_episodes"] == "12"

    @pytest.mark.parametrize(
        ("overrides", "fragment"),
        [
            ({"resolution": "", "label": "Release 6: SD    |"}, "[SD WebRip]"),
            ({"notes": "", "subtitles": []}, "[1080p] [Japanese] [Riitz-chan]"),
            ({"group": ""}, "[Japanese | Sub: German]"),
        ],
    )
    def test_optional_fields(
        self, plugin: Any, overrides: dict[str, Any], fragment: str
    ) -> None:
        result = plugin._release_result(_series(plugin), _release(**overrides))
        assert fragment in result.title
        assert "[]" not in result.title

    @pytest.mark.parametrize(
        ("resolution", "quality"),
        [
            ("3840x2160", "2160p"),
            ("1920x800", "1080p"),  # cinemascope crop
            ("1280x720", "720p"),
            ("720x408", "480p"),
        ],
    )
    def test_quality_from_width(
        self, plugin: Any, resolution: str, quality: str
    ) -> None:
        result = plugin._release_result(
            _series(plugin), _release(resolution=resolution, notes="")
        )
        assert f"[{quality}]" in result.title

    def test_size_without_package(self, plugin: Any) -> None:
        result = plugin._release_result(_series(plugin), _release(size="Ø 1.2 GB"))
        assert result.size == "1.2 GB"

    def test_no_password(self, plugin: Any) -> None:
        result = plugin._release_result(_series(plugin), _release(password=""))
        assert "archive_password" not in result.metadata


class TestReleaseExpansion:
    def _media_page(self, releases: list[dict[str, Any]] | Exception) -> AsyncMock:
        page = _page()
        if isinstance(releases, Exception):
            page.evaluate = AsyncMock(side_effect=releases)
        else:
            page.evaluate = AsyncMock(return_value=releases)
        return page

    async def test_one_result_per_release(self, plugin: Any) -> None:
        page = self._media_page([_release(1), _release(2, resolution="1280x720")])
        plugin._new_page = AsyncMock(return_value=page)

        results = await plugin._expand_releases([_series(plugin)])

        assert [r.download_link for r in results] == [
            f"{_MEDIA}?release=1",
            f"{_MEDIA}?release=2",
        ]
        assert page.goto.await_args.args[0] == _MEDIA
        page.close.assert_awaited_once()

    async def test_page_failure_keeps_the_series_result(self, plugin: Any) -> None:
        plugin._new_page = AsyncMock(
            return_value=self._media_page(RuntimeError("closed"))
        )
        series = _series(plugin)

        assert await plugin._expand_releases([series]) == [series]

    async def test_no_releases_keeps_the_series_result(self, plugin: Any) -> None:
        plugin._new_page = AsyncMock(return_value=self._media_page([]))
        series = _series(plugin)

        assert await plugin._expand_releases([series]) == [series]

    async def test_only_the_first_series_are_expanded(
        self, mod: ModuleType, plugin: Any
    ) -> None:
        mod._MAX_EXPANDED_SERIES = 1
        plugin._new_page = AsyncMock(return_value=self._media_page([_release()]))
        first, second = _series(plugin), _series(plugin)

        results = await plugin._expand_releases([first, second])

        assert results[-1] is second
        assert plugin._new_page.await_count == 1

    async def test_search_expands_releases(self, plugin: Any) -> None:
        series = _series(plugin)
        plugin._verify_domain = AsyncMock()
        plugin._new_page = AsyncMock(return_value=_page())
        plugin._search_all_pages = AsyncMock(return_value=[series])
        plugin._expand_releases = AsyncMock(return_value=["release"])

        assert await plugin.search("one punch man") == ["release"]
        plugin._expand_releases.assert_awaited_once_with([series])


# ---------------------------------------------------------------------------
# Grab-time links
# ---------------------------------------------------------------------------


def _cnl(links: list[str]) -> dict[str, str]:
    """Encrypt *links* the way anime-loads hands them out (Click'n'Load)."""
    jk = "00112233445566778899aabbccddeeff"
    data = "\r\n".join(links).encode()
    data += b"\x00" * (-len(data) % 16)
    key = unhexlify(jk)
    enc = Cipher(algorithms.AES(key), modes.CBC(key)).encryptor()
    crypted = base64.b64encode(enc.update(data) + enc.finalize()).decode()
    return {"jk": jk, "crypted": crypted}


class _Site:
    """Fake anime-loads page answering the plugin's in-page requests."""

    def __init__(
        self,
        mod: ModuleType,
        *,
        episodes: int = 3,
        verify: list[str] | None = None,
        refuse: str | None = None,
    ) -> None:
        self.mod = mod
        self.logged_in = False
        self.episodes = episodes
        self.verify = list(verify or [])
        self.refuse = refuse
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.page = _page()
        self.page.evaluate = AsyncMock(side_effect=self._evaluate)
        self.page.context.cookies = AsyncMock(side_effect=self._cookies)

    def urls(self) -> list[str]:
        return [url for url, _ in self.calls]

    async def _cookies(self) -> list[dict[str, Any]]:
        if not self.logged_in:
            return []
        return [{"name": "al_user", "value": "%7B%22username%22%3A%22u%22%7D"}]

    def _links(self, enc: str) -> str:
        request = json.loads(base64.b64decode(enc))
        episode = request[4]
        if episode == "cnl" and not self.logged_in:
            return json.dumps({"code": "error", "message": "cnl_login"})
        names = (
            [f"e{i:02d}" for i in range(1, self.episodes + 1)]
            if episode == "cnl"
            else [f"e{episode + 1:02d}"]
        )
        content = {
            "1": {
                "hoster": "rapidgator",
                "cnl": _cnl(
                    [f"https://rapidgator.net/file/{n}.rar.html" for n in names]
                ),
            },
            "2": {
                "hoster": "ddownload",
                "cnl": _cnl([f"https://ddownload.com/{n}" for n in names]),
            },
        }
        return json.dumps({"code": "success", "content": content})

    async def _evaluate(self, js: str, arg: Any = None, **_: Any) -> Any:
        if js == self.mod._EXTRACT_RELEASES_JS:
            return [_release(2, episodes=self.episodes)]
        if js == self.mod._CAPTCHA_PICK_JS:
            return "hash-3"
        if js == self.mod._AJAX_JS:
            url, data = arg
            self.calls.append((url, data))
            if url == "/auth/signin":
                self.logged_in = data["password"] == "secret"
                return ""
            if url == "/files/captcha":
                return self.verify.pop(0) if self.verify else "1"
            if url == "/ajax/captcha":
                if self.refuse:
                    return json.dumps({"code": "error", "message": self.refuse})
                if data["response"] == "nocaptcha":
                    return json.dumps({"code": "error", "message": "noadblock"})
                return self._links(data["enc"])
        raise AssertionError(f"unexpected evaluate: {js[:40]}")


class TestResolveDownload:
    def test_is_grab_resolving(self, plugin: Any) -> None:
        assert isinstance(plugin, GrabResolvingPlugin)

    async def test_anonymous_solves_one_captcha_per_episode(
        self, mod: ModuleType, plugin: Any
    ) -> None:
        site = _Site(mod, episodes=3)
        plugin._new_page = AsyncMock(return_value=site.page)

        links = await plugin.resolve_download(_GRAB_URL)

        assert links == [
            "https://rapidgator.net/file/e01.rar.html",
            "https://ddownload.com/e01",
            "https://rapidgator.net/file/e02.rar.html",
            "https://ddownload.com/e02",
            "https://rapidgator.net/file/e03.rar.html",
            "https://ddownload.com/e03",
        ]
        verifies = [d for u, d in site.calls if u == "/files/captcha"]
        assert verifies == [{"cID": 0, "pC": "hash-3", "rT": 2}] * 3
        assert "/auth/signin" not in site.urls()
        site.page.close.assert_awaited_once()

    async def test_login_fetches_the_whole_release_with_one_captcha(
        self, mod: ModuleType, plugin: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SCAVENGARR_ANIMELOADS_USERNAME", "user")
        monkeypatch.setenv("SCAVENGARR_ANIMELOADS_PASSWORD", "secret")
        site = _Site(mod, episodes=24)
        plugin._new_page = AsyncMock(return_value=site.page)

        links = await plugin.resolve_download(_GRAB_URL)

        assert len(links) == 48
        assert site.urls().count("/files/captcha") == 1
        signin = [d for u, d in site.calls if u == "/auth/signin"]
        assert signin == [{"identity": "user", "password": "secret", "remember": 1}]

    async def test_failed_login_falls_back_to_episodes(
        self, mod: ModuleType, plugin: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SCAVENGARR_ANIMELOADS_USERNAME", "user")
        monkeypatch.setenv("SCAVENGARR_ANIMELOADS_PASSWORD", "wrong")
        site = _Site(mod, episodes=2)
        plugin._new_page = AsyncMock(return_value=site.page)

        with structlog.testing.capture_logs() as logs:
            links = await plugin.resolve_download(_GRAB_URL)

        assert len(links) == 4
        assert any(e["event"] == "animeloads_login_failed" for e in logs)
        assert "wrong" not in str(logs)  # never log the password

    async def test_rejected_captcha_is_retried(
        self, mod: ModuleType, plugin: Any
    ) -> None:
        site = _Site(mod, episodes=1, verify=["", "1"])
        plugin._new_page = AsyncMock(return_value=site.page)

        assert len(await plugin.resolve_download(_GRAB_URL)) == 2
        assert site.urls().count("/files/captcha") == 2

    async def test_captcha_gives_up_after_attempts(
        self, mod: ModuleType, plugin: Any
    ) -> None:
        site = _Site(mod, episodes=1, verify=[""] * 10)
        plugin._new_page = AsyncMock(return_value=site.page)

        assert await plugin.resolve_download(_GRAB_URL) == []
        assert site.urls().count("/files/captcha") == mod._CAPTCHA_ATTEMPTS

    @pytest.mark.parametrize("message", ["slowdown", "wrong_captcha"])
    async def test_rate_limit_stops(
        self, mod: ModuleType, plugin: Any, message: str
    ) -> None:
        site = _Site(mod, refuse=message)
        plugin._new_page = AsyncMock(return_value=site.page)

        with structlog.testing.capture_logs() as logs:
            assert await plugin.resolve_download(_GRAB_URL) == []

        refused = [e for e in logs if e["event"] == "animeloads_links_refused"]
        assert refused[0]["message"] == message

    async def test_long_release_without_login_is_refused(
        self, mod: ModuleType, plugin: Any
    ) -> None:
        site = _Site(mod, episodes=mod._MAX_ANON_EPISODES + 1)
        plugin._new_page = AsyncMock(return_value=site.page)

        assert await plugin.resolve_download(_GRAB_URL) == []
        assert "/files/captcha" not in site.urls()

    async def test_series_level_link_is_kept(self, plugin: Any) -> None:
        plugin._new_page = AsyncMock()

        assert await plugin.resolve_download(_MEDIA) == [_MEDIA]
        plugin._new_page.assert_not_awaited()

    async def test_ddos_guard_unresolved(self, mod: ModuleType, plugin: Any) -> None:
        site = _Site(mod)
        site.page.wait_for_selector = AsyncMock(side_effect=TimeoutError())
        site.page.content = AsyncMock(return_value="ddos-guard")
        plugin._new_page = AsyncMock(return_value=site.page)

        assert await plugin.resolve_download(_GRAB_URL) == []
        site.page.close.assert_awaited_once()
