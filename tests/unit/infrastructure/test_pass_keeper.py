"""Tests for PassKeeper: Filemoon's captcha pass kept alive (row 106)."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import structlog.testing

from scavengarr.domain.entities.stremio import ResolvedStream
from scavengarr.domain.ports.browser_fetcher import PageClaim, page_claim
from scavengarr.infrastructure.browser.clearance_store import CreationState
from scavengarr.infrastructure.browser.page_gate import PageGate
from scavengarr.infrastructure.hoster_resolvers import pass_keeper
from scavengarr.infrastructure.hoster_resolvers.pass_keeper import PassKeeper
from scavengarr.infrastructure.hoster_resolvers.registry import (
    HosterResolverRegistry,
)
from scavengarr.infrastructure.telemetry import Telemetry

_NOW = 1_760_000_000.0
_LINK = "https://filemoon.to/e/abc"
_STREAM = ResolvedStream(video_url="https://cdn.example.com/master.m3u8", is_hls=True)


def _state(expires_at: float) -> CreationState:
    """A kept state whose Byse pass ends at *expires_at* (epoch seconds)."""
    item = {
        "name": "byse:captcha-clearance:embed",
        "value": json.dumps({"token": "t", "expiresAt": int(expires_at * 1000)}),
    }
    state: Any = {
        "cookies": [],
        "origins": [{"origin": "https://n1mwq.org", "localStorage": [item]}],
    }
    return CreationState(state, ["n1mwq.org"], [])


@dataclass
class _Rig:
    keeper: PassKeeper
    registry: HosterResolverRegistry
    resolver: MagicMock
    store: MagicMock
    pages: PageGate
    telemetry: Telemetry
    # What the Filemoon resolver answers, after how long, and the page
    # claim of each call
    answer: dict[str, ResolvedStream | None]
    took: dict[str, float]
    claims: list[PageClaim | None] = field(default_factory=list)

    def kept(self, expires_at: float | None) -> None:
        """The store holds a pass that ends at *expires_at*, or none."""
        self.store.creation_state.return_value = (
            None if expires_at is None else _state(expires_at)
        )

    def outcome(self, outcome: str) -> float | None:
        return self.telemetry.registry.get_sample_value(
            "scavengarr_hoster_pass_keep_total", {"outcome": outcome}
        )

    @property
    def captures(self) -> int:
        """The keeper's resolutions: the resolver's calls under a claim."""
        return sum(claim is not None for claim in self.claims)


async def _rig(
    *, limit: int = 2, cached: bool = True, resolve_timeout: float = 15.0
) -> _Rig:
    """A keeper over a registry with a Filemoon resolver; *cached*: a request
    resolved ``_LINK`` before (its stream is in the cache)."""
    answer: dict[str, ResolvedStream | None] = {"stream": _STREAM}
    took = {"s": 0.0}
    claims: list[PageClaim | None] = []

    async def _resolve(url: str) -> ResolvedStream | None:
        claims.append(page_claim.get())
        await asyncio.sleep(took["s"])
        return answer["stream"]

    resolver = MagicMock()
    resolver.name = "filemoon"
    resolver.resolve = AsyncMock(side_effect=_resolve)
    telemetry = Telemetry()
    registry = HosterResolverRegistry(
        resolvers=[resolver], resolve_timeout=resolve_timeout, telemetry=telemetry
    )
    store = MagicMock()
    store.creation_state = AsyncMock(return_value=None)
    pages = PageGate(limit=limit)
    keeper = PassKeeper(registry, store, pages, telemetry=telemetry)
    if cached:
        await registry.resolve(_LINK)
    return _Rig(
        keeper, registry, resolver, store, pages, telemetry, answer, took, claims
    )


class TestWaits:
    async def test_without_a_pass_it_looks_again_later(self) -> None:
        rig = await _rig()

        assert await rig.keeper.step(_NOW) == 300
        assert rig.captures == 0

    async def test_until_the_pass_has_gone(self) -> None:
        rig = await _rig()
        rig.kept(_NOW + 120)

        # The expiry and the margin after it
        assert await rig.keeper.step(_NOW) == 150
        assert rig.captures == 0

    async def test_a_late_expiry_is_looked_at_again_meanwhile(self) -> None:
        rig = await _rig()
        rig.kept(_NOW + 1200)

        # A new pass, a clock that NTP set
        assert await rig.keeper.step(_NOW) == 300

    async def test_within_the_margin_after_the_expiry(self) -> None:
        rig = await _rig()
        rig.kept(_NOW - 10)

        assert await rig.keeper.step(_NOW) == 20
        assert rig.captures == 0


class TestCapture:
    async def test_the_newest_link_once_the_pass_has_gone(self) -> None:
        rig = await _rig()
        rig.kept(_NOW - 60)

        assert await rig.keeper.step(_NOW) == 300

        # Past the cache: the cached stream would answer without a capture
        assert rig.captures == 1
        assert rig.resolver.resolve.await_args.args == (_LINK,)
        claim = rig.claims[-1]
        assert claim is not None and claim.kind == "background"
        assert page_claim.get() is None
        assert rig.outcome("stream") == 1

    async def test_once_per_pass(self) -> None:
        rig = await _rig()
        rig.kept(_NOW - 60)
        await rig.keeper.step(_NOW)

        # The capture brought no new pass: the keeping waits for one
        assert await rig.keeper.step(_NOW + 300) == 300
        assert rig.captures == 1

    async def test_a_new_pass_is_kept_in_turn(self) -> None:
        rig = await _rig()
        rig.kept(_NOW - 60)
        await rig.keeper.step(_NOW)
        rig.kept(_NOW + 1785)  # the capture's captcha gave a new pass

        assert await rig.keeper.step(_NOW + 60) == 300
        assert rig.captures == 1
        await rig.keeper.step(_NOW + 1900)
        assert rig.captures == 2

    async def test_its_capture_has_a_bound_of_its_own(self) -> None:
        """The cold path outlasts the requests' bound: the captcha comes
        after the first click, 3 s after the page has loaded (row 106's
        proofs: cut before it, no pass). No request waits for the keeper."""
        # The production host's cold Byse streams came 22.7 to 43.7 s after
        # the resolution's start (row 141): the keeper's capture ends inside
        # its bound, so its line says whether the pass came
        assert pass_keeper._BOUND_S == 45.0
        rig = await _rig(resolve_timeout=0.05)
        rig.took["s"] = 0.2
        rig.kept(_NOW - 60)

        await rig.keeper.step(_NOW)

        assert rig.outcome("stream") == 1
        # A request's lookup keeps the registry's bound
        assert await rig.registry.resolve(_LINK, refresh=True) is None

    async def test_no_stream_is_recorded(self) -> None:
        rig = await _rig()
        rig.answer["stream"] = None
        rig.kept(_NOW - 60)

        await rig.keeper.step(_NOW)
        await rig.keeper.step(_NOW + 300)

        assert rig.captures == 1
        assert rig.outcome("none") == 1

    async def test_none_without_a_cached_stream(self) -> None:
        rig = await _rig(cached=False)
        rig.answer["stream"] = None
        await rig.registry.resolve(_LINK)  # a request's link that was dead
        rig.kept(_NOW - 60)

        assert await rig.keeper.step(_NOW) == 300
        await rig.keeper.step(_NOW + 300)

        assert rig.captures == 0
        assert rig.outcome("no_link") == 1


class TestDemand:
    """Only while a request looked a Filemoon link up within 48 h."""

    async def test_idle_48_h_after_the_last_request(self) -> None:
        rig = await _rig()
        rig.registry._demand["filemoon"] -= 48 * 3600 + 60
        rig.kept(_NOW - 60)

        assert await rig.keeper.step(_NOW) == 300
        await rig.keeper.step(_NOW + 300)

        assert rig.captures == 0
        assert rig.outcome("idle") == 1

    async def test_a_request_starts_it_again(self) -> None:
        rig = await _rig()
        rig.registry._demand["filemoon"] -= 48 * 3600 + 60
        rig.kept(_NOW - 60)
        await rig.keeper.step(_NOW)

        await rig.registry.resolve(_LINK)  # a cache hit
        await rig.keeper.step(_NOW + 300)

        assert rig.captures == 1

    async def test_its_own_capture_is_no_demand(self) -> None:
        rig = await _rig()
        rig.registry._demand["filemoon"] -= 47 * 3600
        rig.kept(_NOW - 60)

        await rig.keeper.step(_NOW)

        assert rig.captures == 1
        age = rig.registry.demand_age("filemoon")
        assert age is not None and age >= 47 * 3600

    async def test_idle_without_a_request_since_the_start(self) -> None:
        rig = await _rig(cached=False)
        stream = {"video_url": _STREAM.video_url, "is_hls": True}
        result = {"url": _LINK, "stream": stream, "remaining": 1800.0}
        snapshot = {"results": [{**result, "resolver": "filemoon"}], "redirects": []}
        rig.registry.import_state(snapshot, age_s=0.0)  # of a version without it
        rig.kept(_NOW - 60)

        await rig.keeper.step(_NOW)

        assert rig.captures == 0
        assert rig.outcome("idle") == 1


class TestPages:
    async def test_none_unless_a_page_stays_free(self) -> None:
        rig = await _rig(limit=2)
        rig.kept(_NOW - 60)

        async with rig.pages.page("capture", timeout=5):
            assert await rig.keeper.step(_NOW) == 30
        assert rig.captures == 0
        assert rig.outcome("busy") == 1

        await rig.keeper.step(_NOW + 30)
        assert rig.captures == 1

    async def test_beside_a_page_in_use_when_another_stays_free(self) -> None:
        rig = await _rig(limit=3)
        rig.kept(_NOW - 60)

        async with rig.pages.page("capture", timeout=5):
            await rig.keeper.step(_NOW)

        assert rig.captures == 1

    async def test_never_on_a_single_page(self) -> None:
        rig = await _rig(limit=1)
        rig.kept(_NOW - 60)

        assert await rig.keeper.step(_NOW) == 30
        assert rig.captures == 0


class TestRun:
    async def test_an_error_is_logged_and_the_keeping_goes_on(self) -> None:
        rig = await _rig()
        rig.store.creation_state.side_effect = RuntimeError("cache down")

        with structlog.testing.capture_logs() as logs:
            task = asyncio.create_task(rig.keeper.run_forever())
            for _ in range(3):
                await asyncio.sleep(0)

        assert not task.done()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        assert [e["event"] for e in logs] == ["hoster_pass_keep_error"]
