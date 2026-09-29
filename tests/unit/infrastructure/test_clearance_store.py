"""Tests for ClearanceStore (bot-challenge clearance cookies across restarts)."""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import structlog.testing
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
