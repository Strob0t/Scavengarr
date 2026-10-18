"""Keep Filemoon's captcha pass: one capture once it has gone.

The Byse player (Filemoon) asks a cold browser context for its captcha
before the stream and keeps the pass it gives in its localStorage for
30 min; a capture with the pass leaves it alone, and once it has gone the
next capture meets the captcha again. In production that capture took 17 to
32 s, past the resolve bound, so a Filemoon link asked for after a quiet half
hour came without a stream (row 106, docs/plans/challenge-reactions.md, R5).

The keeper reads the pass's expiry from the kept capture state
(``ClearanceStore``: a capture past a challenge keeps its state at once) and,
once it has gone, resolves the link of the newest cached Filemoon stream
again in the background. That capture meets the captcha, and its new pass
is kept for the next request's capture. It has a bound of its own: the
captcha comes with the player's first click, 3 s after the page has loaded,
and within production's resolve bound (10 s) a slow load cut the capture
before it (no pass; row 106's dev proofs). Only while there is demand: a
request looked up a Filemoon link within 48 h (``demand_age()``: its cache
hits count, a stream the keeper's capture cached included; the keeper's
own lookups do not). Past that the keeper idles, and the next request's
lookup starts it again; production's quiet stretches lasted up to about
23 h, and 48 h leave at most 96 captures after the last request. One
capture per pass: a capture that brings no new pass ends the keeping until
a request's capture does, and with no cached stream there is no capture.
It starts only while one of the stealth browser's pages stays free beside
its own, and its page claim is background work, served last.
"""

from __future__ import annotations

import asyncio
import time

import structlog

from scavengarr.domain.ports.browser_fetcher import PageClaim, page_claim
from scavengarr.domain.ports.telemetry import NO_TELEMETRY, TelemetryPort
from scavengarr.infrastructure.browser.clearance_store import ClearanceStore
from scavengarr.infrastructure.browser.page_gate import PageGate
from scavengarr.infrastructure.hoster_resolvers.filemoon import pass_expiry
from scavengarr.infrastructure.hoster_resolvers.registry import (
    HosterResolverRegistry,
)

log = structlog.get_logger(__name__)

_RESOLVER = "filemoon"
# Past the expiry by this much the player asks for the captcha again (it
# compares the expiry with the clock the keeper reads)
_MARGIN_S = 30.0
# The longest wait between two looks at the kept pass: one a capture
# renewed meanwhile, a clock that NTP set after the start
_RECHECK_S = 300.0
# The wait for a free page while the stealth browser is busy
_BUSY_S = 30.0
# The capture's bound in place of the resolve bound, and its page claim's
# due (the page gate serves it last and refuses it 3 s before): a page load
# of 8.6 s put the captcha past production's 10 s. 30 s is what a challenged
# capture gets there in all, the bound and the 20 s completion
_BOUND_S = 30.0
# The keeping stops this long after a request's last Filemoon lookup
_DEMAND_S = 48 * 3600.0


class PassKeeper:
    """Resolves the newest cached Filemoon stream's link again once the kept
    captcha pass has gone, so the next request's capture finds a pass."""

    def __init__(
        self,
        registry: HosterResolverRegistry,
        store: ClearanceStore,
        pages: PageGate,
        *,
        telemetry: TelemetryPort = NO_TELEMETRY,
    ) -> None:
        self._registry = registry
        self._store = store
        self._pages = pages
        self._telemetry = telemetry
        # The expiry of the last pass the keeper acted on: one capture per pass
        self._done: float | None = None
        # The expiry of the pass it last idled at (counted once per pass)
        self._idle: float | None = None

    async def run_forever(self) -> None:
        """Look at the pass until cancelled."""
        while True:
            try:
                delay = await self.step(time.time())
            except Exception:
                log.exception("hoster_pass_keep_error")
                delay = _RECHECK_S
            await asyncio.sleep(delay)

    async def step(self, now: float) -> float:
        """Look at the kept pass at *now* (epoch seconds) and capture once it
        has gone; returns the seconds until the next look."""
        kept = await self._store.creation_state()
        expiry = None if kept is None else pass_expiry(kept.state)
        if expiry is None or (self._done is not None and expiry <= self._done):
            return _RECHECK_S
        left = expiry + _MARGIN_S - now
        if left > 0:
            return min(left, _RECHECK_S)
        demand = self._registry.demand_age(_RESOLVER)
        if demand is None or demand > _DEMAND_S:
            # The pass stays due: a request's lookup starts the keeping again
            if self._idle != expiry:
                self._idle = expiry
                log.info(
                    "hoster_pass_keep_skipped",
                    reason="idle",
                    demand_h=None if demand is None else round(demand / 3600),
                )
                self._telemetry.count("hoster_pass_keep", "idle")
            return _RECHECK_S
        # A free page goes to a waiting request at once: while one stays
        # free beside the keeper's, nobody waits
        if self._pages.in_use >= self._pages.limit - 1:
            self._telemetry.count("hoster_pass_keep", "busy")
            return _BUSY_S
        self._done = expiry
        link = self._registry.last_stream(_RESOLVER)
        if link is None:
            log.info(
                "hoster_pass_keep_skipped", reason="no_link", gone_s=round(now - expiry)
            )
            self._telemetry.count("hoster_pass_keep", "no_link")
            return _RECHECK_S
        await self._capture(link, gone_s=now - expiry)
        return _RECHECK_S

    async def _capture(self, link: str, *, gone_s: float) -> None:
        """Resolve *link* past the cache and the challenge cooldown within
        ``_BOUND_S``, as background work and no demand. A capture the bound
        cuts after the captcha runs on as the registry's challenge
        completion, which keeps the new pass when it finds the stream."""
        started = time.monotonic()
        claim = page_claim.set(PageClaim("background", started + _BOUND_S))
        try:
            with self._telemetry.stage("hoster_pass_keep") as stage:
                stream = await self._registry.resolve(
                    link, refresh=True, demand=False, bound=_BOUND_S
                )
                stage.outcome = "none" if stream is None else "stream"
        finally:
            page_claim.reset(claim)
        log.info(
            "hoster_pass_keep",
            stream=stream is not None,
            gone_s=round(gone_s),
            duration_ms=round((time.monotonic() - started) * 1000),
        )
