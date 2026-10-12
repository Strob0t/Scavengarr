"""Tests for ClearanceStore (bot-challenge clearance cookies across restarts)."""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog.testing

from scavengarr.infrastructure.browser import clearance_store
from scavengarr.infrastructure.browser.clearance_store import ClearanceStore

_KEY = "browser:clearance_cookies"


def _cookie(name: str, domain: str, expires: float, value: str = "v") -> dict[str, Any]:
    return {
        "name": name,
        "value": value,
        "domain": domain,
        "path": "/",
        "expires": expires,
        "httpOnly": True,
        "secure": True,
        "sameSite": "None",
        "partitionKey": "ignored-by-add-cookies",
    }


def _context(cookies: list[dict[str, Any]]) -> MagicMock:
    ctx = MagicMock()
    ctx.cookies = AsyncMock(return_value=cookies)
    ctx.add_cookies = AsyncMock()
    return ctx


def _store(stored: str | None = None) -> tuple[ClearanceStore, AsyncMock]:
    cache = AsyncMock()
    cache.get.return_value = stored
    return ClearanceStore(cache), cache


class TestRemember:
    async def test_saves_only_clearance_cookies(self) -> None:
        soon = time.time() + 1800
        store, cache = _store()
        ctx = _context(
            [
                _cookie("cf_clearance", ".filmfans.org", soon, "SECRET-CF"),
                _cookie("__ddg8_", ".anime-loads.org", soon, "SECRET-DDG"),
                _cookie("ha-waf-ticket", ".kinoger.com", soon, "SECRET-WAF"),
                _cookie("PHPSESSID", "filmfans.org", soon),
                _cookie("cf_clearance", ".gone.example", time.time() - 5),
                _cookie("cf_clearance", ".session.example", -1),
            ]
        )

        with structlog.testing.capture_logs() as logs:
            await store.remember(ctx)

        key, value = cache.set.await_args.args
        assert key == _KEY
        saved = json.loads(value)
        assert [(c["name"], c["domain"]) for c in saved] == [
            ("cf_clearance", ".filmfans.org"),
            ("__ddg8_", ".anime-loads.org"),
            ("ha-waf-ticket", ".kinoger.com"),
        ]
        assert "partitionKey" not in saved[0]
        assert 1700 < cache.set.await_args.kwargs["ttl"] <= 1800
        assert "SECRET" not in str(logs)  # cookie values are never logged

    async def test_merges_with_stored_cookies(self) -> None:
        soon = time.time() + 1800
        stored = json.dumps([_cookie("cf_clearance", ".kinoger.com", soon, "old")])
        store, cache = _store(stored)
        ctx = _context([_cookie("cf_clearance", ".filmfans.org", soon, "new")])

        await store.remember(ctx)

        saved = json.loads(cache.set.await_args.args[1])
        assert {c["domain"] for c in saved} == {".kinoger.com", ".filmfans.org"}

    async def test_newer_cookie_replaces_the_same_name_and_domain(self) -> None:
        soon = time.time() + 1800
        stored = json.dumps([_cookie("cf_clearance", ".filmfans.org", soon, "old")])
        store, cache = _store(stored)

        await store.remember(
            _context([_cookie("cf_clearance", ".filmfans.org", soon + 60, "new")])
        )

        saved = json.loads(cache.set.await_args.args[1])
        assert [c["value"] for c in saved] == ["new"]

    async def test_unchanged_cookies_are_not_written_again(self) -> None:
        soon = time.time() + 1800
        store, cache = _store()
        ctx = _context([_cookie("cf_clearance", ".filmfans.org", soon)])

        await store.remember(ctx)
        await store.remember(ctx)

        assert cache.set.await_count == 1

    async def test_nothing_to_save(self) -> None:
        store, cache = _store()

        await store.remember(_context([_cookie("PHPSESSID", "x.org", -1)]))

        cache.set.assert_not_awaited()

    async def test_browser_error_is_swallowed_with_log(self) -> None:
        store, cache = _store()
        ctx = _context([])
        ctx.cookies.side_effect = RuntimeError("context closed")

        with structlog.testing.capture_logs() as logs:
            await store.remember(ctx)

        cache.set.assert_not_awaited()
        assert any(e["event"] == "clearance_save_failed" for e in logs)


class TestRestore:
    async def test_adds_unexpired_cookies(self) -> None:
        soon = time.time() + 1800
        stored = json.dumps(
            [
                _cookie("cf_clearance", ".filmfans.org", soon),
                _cookie("cf_clearance", ".gone.example", time.time() - 5),
            ]
        )
        store, _ = _store(stored)
        ctx = _context([])

        await store.restore(ctx)

        added = ctx.add_cookies.await_args.args[0]
        assert [c["domain"] for c in added] == [".filmfans.org"]

    async def test_empty_cache(self) -> None:
        store, _ = _store(None)
        ctx = _context([])

        await store.restore(ctx)

        ctx.add_cookies.assert_not_awaited()

    async def test_corrupt_entry_is_ignored(self) -> None:
        store, _ = _store("not json")
        ctx = _context([])

        await store.restore(ctx)

        ctx.add_cookies.assert_not_awaited()


# ------------------------------------------------------------------
# Capture states (keep-capture-state, row 91's R4)
# ------------------------------------------------------------------

_STATE_KEY = "browser:capture_state:v1"
_DAY = 24 * 3600


def _origin(origin: str, keys: int = 1, databases: int = 0) -> dict[str, Any]:
    return {
        "origin": origin,
        "localStorage": [{"name": f"k{i}", "value": "SECRET-LS"} for i in range(keys)],
        "indexedDB": [
            {"name": f"db{i}", "version": 1, "stores": []} for i in range(databases)
        ],
    }


def _keyed_store(
    stored: dict[str, str | None],
) -> tuple[ClearanceStore, AsyncMock]:
    """A store whose cache answers each key from *stored*."""
    cache = AsyncMock()

    async def _get(key: str) -> str | None:
        return stored.get(key)

    cache.get.side_effect = _get
    return ClearanceStore(cache), cache


def _saved(cache: AsyncMock) -> dict[str, Any]:
    key, value = cache.set.await_args.args
    assert key == _STATE_KEY
    return json.loads(value)


# Dropload's capture: the hoster's page, the Turnstile frame, an ad domain
# that set a cookie without a frame on the page (the Pi's GL_UI4)
_DROPLOAD_STATE: dict[str, Any] = {
    "cookies": [
        _cookie("file_id", "dr0pstream.com", -1, "SECRET-1"),
        _cookie("aff", ".dr0pstream.com", time.time() + 3600, "SECRET-2"),
        _cookie("lang", "www.dr0pstream.com", -1, "SECRET-3"),
        _cookie("__cf_bm", ".cloudflare.com", time.time() + 1800, "SECRET-4"),
        _cookie("GL_UI4", ".hubeamily.com", time.time() + 3600, "SECRET-5"),
    ],
    "origins": [
        _origin("https://dr0pstream.com", keys=2),
        _origin("https://challenges.cloudflare.com", keys=1, databases=1),
        _origin("https://hubeamily.com"),
    ],
}
_DROPLOAD_HOSTS = ["challenges.cloudflare.com", "dr0pstream.com"]


class TestKeepCaptureState:
    async def test_a_state_is_kept_per_host_with_its_own_cookies_and_origins(
        self,
    ) -> None:
        store, cache = _keyed_store({})

        await store.keep_capture_state(_DROPLOAD_HOSTS, _DROPLOAD_STATE)

        saved = _saved(cache)
        assert sorted(saved) == _DROPLOAD_HOSTS
        hoster, widget = saved["dr0pstream.com"], saved["challenges.cloudflare.com"]
        # The host's own cookies and its parent domain's, no subdomain's
        assert [c["name"] for c in hoster["cookies"]] == ["file_id", "aff"]
        assert [o["origin"] for o in hoster["origins"]] == ["https://dr0pstream.com"]
        assert [c["name"] for c in widget["cookies"]] == ["__cf_bm"]
        assert [o["origin"] for o in widget["origins"]] == [
            "https://challenges.cloudflare.com"
        ]
        assert widget["origins"][0]["indexedDB"]
        assert abs(hoster["saved_at"] - time.time()) < 5
        assert cache.set.await_args.kwargs["ttl"] == _DAY

    async def test_other_hosts_stay_out(self) -> None:
        store, cache = _keyed_store({})

        await store.keep_capture_state(_DROPLOAD_HOSTS, _DROPLOAD_STATE)

        dumped = json.dumps(_saved(cache))
        assert "hubeamily" not in dumped
        assert "GL_UI4" not in dumped

    async def test_kept_states_are_merged_and_old_ones_dropped(self) -> None:
        now = time.time()
        stored = {
            "byse.example": {"saved_at": now - 600, "cookies": [], "origins": []},
            "old.example": {"saved_at": now - _DAY - 60, "cookies": [], "origins": []},
            "dr0pstream.com": {"saved_at": now - 900, "cookies": [], "origins": []},
        }
        store, cache = _keyed_store({_STATE_KEY: json.dumps(stored)})

        await store.keep_capture_state(["dr0pstream.com"], _DROPLOAD_STATE)

        saved = _saved(cache)
        assert sorted(saved) == ["byse.example", "dr0pstream.com"]
        assert saved["dr0pstream.com"]["saved_at"] > now - 5
        assert len(saved["dr0pstream.com"]["cookies"]) == 2

    async def test_a_state_above_512_kib_is_not_kept(self) -> None:
        big = {
            "origin": "https://dr0pstream.com",
            "localStorage": [{"name": "blob", "value": "x" * 600 * 1024}],
        }
        state = {"cookies": _DROPLOAD_STATE["cookies"], "origins": [big]}
        store, cache = _keyed_store({})

        with structlog.testing.capture_logs() as logs:
            await store.keep_capture_state(_DROPLOAD_HOSTS, state)

        assert sorted(_saved(cache)) == ["challenges.cloudflare.com"]
        too_large = [e for e in logs if e["event"] == "capture_state_too_large"]
        assert [e["host"] for e in too_large] == ["dr0pstream.com"]
        assert too_large[0]["bytes"] > 512 * 1024

    async def test_nothing_of_the_hosts_writes_nothing(self) -> None:
        store, cache = _keyed_store({})

        await store.keep_capture_state(
            ["elsewhere.example"], {"cookies": [], "origins": []}
        )

        cache.set.assert_not_awaited()

    async def test_the_saved_log_names_hosts_and_counts(self) -> None:
        store, _ = _keyed_store({})

        with structlog.testing.capture_logs() as logs:
            await store.keep_capture_state(_DROPLOAD_HOSTS, _DROPLOAD_STATE)

        (saved,) = [e for e in logs if e["event"] == "capture_state_saved"]
        assert saved["hosts"] == _DROPLOAD_HOSTS
        assert (saved["cookies"], saved["origins"]) == (3, 2)
        assert saved["bytes"] > 0


class TestCreationState:
    async def test_nothing_stored_gives_no_state(self) -> None:
        store, _ = _keyed_store({})

        assert await store.creation_state() is None

    async def test_clearance_cookies_and_kept_states_merge_newest_winning(
        self,
    ) -> None:
        now = time.time()
        soon = now + 1800
        clearance = [
            _cookie("cf_clearance", ".filmfans.org", soon, "CF"),
            _cookie("cf_clearance", ".dr0pstream.com", soon, "OLD-CF"),
            _cookie("cf_clearance", ".gone.example", now - 5, "GONE"),
        ]
        states = {
            "dr0pstream.com": {
                "saved_at": now - 60,
                "cookies": [
                    _cookie("cf_clearance", ".dr0pstream.com", soon, "NEW-CF"),
                    _cookie("file_id", "dr0pstream.com", -1, "NEWER"),
                    _cookie("aff", ".dr0pstream.com", now - 5, "EXPIRED"),
                ],
                "origins": [_origin("https://dr0pstream.com", keys=2)],
            },
            "www.dr0pstream.com": {
                "saved_at": now - 3600,
                "cookies": [_cookie("file_id", "dr0pstream.com", -1, "OLDER")],
                "origins": [_origin("https://dr0pstream.com", keys=5)],
            },
            "old.example": {
                "saved_at": now - _DAY - 60,
                "cookies": [_cookie("sid", "old.example", -1, "STALE")],
                "origins": [_origin("https://old.example")],
            },
        }
        store, _ = _keyed_store(
            {_KEY: json.dumps(clearance), _STATE_KEY: json.dumps(states)}
        )

        with structlog.testing.capture_logs() as logs:
            state = await store.creation_state()

        assert state is not None
        values = {(c["name"], c["domain"]): c["value"] for c in state["cookies"]}
        assert values == {
            ("cf_clearance", ".filmfans.org"): "CF",
            ("cf_clearance", ".dr0pstream.com"): "NEW-CF",
            ("file_id", "dr0pstream.com"): "NEWER",
        }
        assert [len(o["localStorage"]) for o in state["origins"]] == [2]
        (restored,) = [e for e in logs if e["event"] == "capture_state_restored"]
        assert restored["hosts"] == ["dr0pstream.com", "www.dr0pstream.com"]

    async def test_clearance_cookies_alone(self) -> None:
        soon = time.time() + 1800
        clearance = [_cookie("cf_clearance", ".filmfans.org", soon)]
        store, _ = _keyed_store({_KEY: json.dumps(clearance)})

        state = await store.creation_state()

        assert state is not None
        assert [c["domain"] for c in state["cookies"]] == [".filmfans.org"]
        assert state["origins"] == []

    async def test_a_corrupt_key_is_dropped_and_the_clearances_stay(self) -> None:
        soon = time.time() + 1800
        clearance = [_cookie("cf_clearance", ".filmfans.org", soon)]
        store, cache = _keyed_store(
            {_KEY: json.dumps(clearance), _STATE_KEY: "not json"}
        )

        with structlog.testing.capture_logs() as logs:
            state = await store.creation_state()

        assert state is not None
        assert [c["domain"] for c in state["cookies"]] == [".filmfans.org"]
        cache.delete.assert_awaited_once_with(_STATE_KEY)
        assert any(e["event"] == "capture_state_corrupt" for e in logs)

    async def test_a_cache_error_gives_no_state(self) -> None:
        cache = AsyncMock()
        cache.get.side_effect = RuntimeError("redis down")

        with structlog.testing.capture_logs() as logs:
            state = await ClearanceStore(cache).creation_state()

        assert state is None
        assert any(e["event"] == "capture_state_restore_failed" for e in logs)


class _Clock:
    """The store's wall clock, set by the test."""

    def __init__(self, now: float) -> None:
        self.now = now

    def time(self) -> float:
        return self.now


def _clock(monkeypatch: pytest.MonkeyPatch, now: float) -> _Clock:
    clock = _Clock(now)
    monkeypatch.setattr(clearance_store, "time", clock)
    return clock


def _memory_store() -> tuple[ClearanceStore, dict[str, str]]:
    """A store on a cache that keeps what it is given."""
    data: dict[str, str] = {}
    cache = AsyncMock()

    async def _get(key: str) -> str | None:
        return data.get(key)

    async def _set(key: str, value: str, ttl: int | None = None) -> None:
        data[key] = value

    async def _delete(key: str) -> None:
        data.pop(key, None)

    cache.get.side_effect = _get
    cache.set.side_effect = _set
    cache.delete.side_effect = _delete
    return ClearanceStore(cache), data


class TestNewestCookieWins:
    """Per cookie the newest of the stored clearance cookies and the kept
    capture states' starts a new context, whichever store holds it (code
    review 2026-10-12, finding 7)."""

    async def test_a_newer_clearance_beats_an_older_kept_cookie(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The review's case: a capture kept Dropload's cf_clearance A,
        Cloudflare challenged again within the keep interval, and
        ``remember()`` stored B. A context created before the host's next
        keep starts with B, not with A."""
        clock = _clock(monkeypatch, 1_800_000_000.0)
        store, _ = _memory_store()
        expires = clock.now + 365 * _DAY
        await store.keep_capture_state(
            ["dr0pstream.com"],
            {"cookies": [_cookie("cf_clearance", ".dr0pstream.com", expires, "A")]},
        )
        clock.now += 120
        await store.remember(
            _context([_cookie("cf_clearance", ".dr0pstream.com", expires + 120, "B")])
        )

        state = await store.creation_state()

        assert state is not None
        assert [c["value"] for c in state["cookies"]] == ["B"]

    async def test_a_newer_kept_cookie_beats_an_older_clearance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = _clock(monkeypatch, 1_800_000_000.0)
        store, _ = _memory_store()
        expires = clock.now + 365 * _DAY
        await store.remember(
            _context([_cookie("cf_clearance", ".dr0pstream.com", expires, "A")])
        )
        clock.now += 120
        await store.keep_capture_state(
            ["dr0pstream.com"],
            {"cookies": [_cookie("cf_clearance", ".dr0pstream.com", expires, "B")]},
        )

        state = await store.creation_state()

        assert state is not None
        assert [c["value"] for c in state["cookies"]] == ["B"]

    async def test_no_cookie_carries_its_time_into_a_context(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = _clock(monkeypatch, 1_800_000_000.0)
        store, data = _memory_store()
        await store.remember(
            _context([_cookie("cf_clearance", ".filmfans.org", clock.now + 365 * _DAY)])
        )
        context = _context([])

        state = await store.creation_state()
        await store.restore(context)

        assert "saved_at" in json.loads(data[_KEY])[0]
        assert state is not None
        assert all("saved_at" not in c for c in state["cookies"])
        (added,) = context.add_cookies.await_args.args
        assert all("saved_at" not in c for c in added)


async def test_no_log_line_carries_a_value_or_a_url() -> None:
    store, cache = _keyed_store({})

    with structlog.testing.capture_logs() as logs:
        await store.keep_capture_state(_DROPLOAD_HOSTS, _DROPLOAD_STATE)
        stored = {_STATE_KEY: cache.set.await_args.args[1]}
        await _keyed_store(stored)[0].creation_state()

    assert logs
    assert "SECRET" not in str(logs)
    assert "https://" not in str(logs)
