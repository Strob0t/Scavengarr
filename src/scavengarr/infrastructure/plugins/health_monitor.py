"""Periodic reachability checks of the Stremio plugins' sites.

A plugin whose site is down ran in every search until the deadline: its
fetch errors end as empty answers, which the circuit breaker does not
count. megakino_to and movie4k (Cloudflare 522, fetch timeouts) held every
first answer of the fifth end-to-end round to the soft deadline
(2026-10-05). The Stremio search skips the plugins this monitor finds
unreachable (``PluginSearchRunner``).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

import structlog

from scavengarr.domain.entities.scoring import ProbeResult
from scavengarr.domain.ports.plugin_history import (
    NO_PLUGIN_HISTORY,
    PluginHistoryPort,
)
from scavengarr.domain.ports.plugin_registry import PluginRegistryPort
from scavengarr.infrastructure.scoring.health_prober import HealthProber

log = structlog.get_logger(__name__)

# An unreachable site is checked again this long after the check that
# found it down, then at doubling pauses up to the full interval (a dead
# site costs one check per interval, a site that comes back is reachable
# again within one pause); the first check waits for the start's own load
# (first requests, browser warm-up)
_RETRY_S = 300.0
_FIRST_CHECK_S = 60.0
_CONCURRENCY = 5

# A reachable site that fails a check is tried again after this pause
# before it counts as unreachable: one try without an answer marked it down
# until the next check 5 minutes later (movie2k in production: no answer
# within 5 s, an answer at the next check; 2026-10-06)
_CONFIRM_S = 30.0


def _answers(result: ProbeResult) -> bool:
    """Whether the site is up: an answer below 500 or a challenge page.

    A challenge means the site is up behind Cloudflare (kinoger). No answer
    (DNS, connect or read error, timeout) or a server error without a
    challenge (522: Cloudflare cannot reach the site) means it is down.
    """
    status = result.http_status
    return status is not None and (status < 500 or result.captcha_detected)


class PluginHealthMonitor:
    """Which plugins' sites answer.

    Every plugin is checked every *interval_s*, an unreachable one again 5
    minutes after the check that found it down, then after 10, 20 and so
    on up to *interval_s* (``_RETRY_S``, doubling). A site that fails a
    check and its retry 30 s later is marked unreachable, one answer brings
    it back and resets its pause. A check in
    which no site answers changes nothing: then the own network or DNS is
    down, not every site. Every other check's verdict goes into the
    plugins' long-term record (``checks``, ``unreachable``).
    """

    def __init__(
        self,
        *,
        prober: HealthProber,
        plugins: PluginRegistryPort,
        names: list[str],
        interval_s: float,
        history: PluginHistoryPort = NO_PLUGIN_HISTORY,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._prober = prober
        self._plugins = plugins
        self._names = names
        self._interval_s = interval_s
        # The plugins' long-term record: every check's verdict, per day
        self._history = history
        self._clock = clock
        self._tick_s = min(_RETRY_S, interval_s)
        self._next_full = 0.0
        self._unreachable: set[str] = set()
        # An unreachable plugin's current pause and the time of its next check
        self._pause_s: dict[str, float] = {}
        self._recheck_at: dict[str, float] = {}

    def is_reachable(self, name: str) -> bool:
        return name not in self._unreachable

    def mark_unreachable(self, name: str) -> None:
        """A search found none of the plugin's domains answering: skip the
        plugin until the recheck finds its site answering."""
        if name in self._unreachable:
            return
        self._unreachable.add(name)
        self._back_off(name, self._clock())
        log.warning("plugin_unreachable", plugin=name, source="search")

    async def run_forever(self) -> None:
        """Check the sites until cancelled (an app lifespan task)."""
        await asyncio.sleep(_FIRST_CHECK_S)
        while True:
            try:
                names = self._due(self._clock())
                if names:
                    await self.check(names)
            except Exception:
                log.error("plugin_health_check_failed", exc_info=True)
            await asyncio.sleep(self._tick_s)

    def _due(self, now: float) -> list[str]:
        """The plugins to check at *now*: all once per interval, else the
        unreachable ones whose pause is over.

        The loop ticks every ``_tick_s``; a check due within the next half
        tick runs at this one (a pause is a multiple of the tick, and a sleep
        that wakes a moment early would otherwise push it a whole tick).
        """
        horizon = now + self._tick_s / 2
        if self._next_full < horizon:
            self._next_full = now + self._interval_s
            return list(self._names)
        return sorted(
            name
            for name in self._unreachable
            if self._recheck_at.get(name, 0.0) < horizon
        )

    def _back_off(self, name: str, now: float) -> None:
        """Schedule the unreachable plugin's next check: ``_RETRY_S`` after
        the first failed check, twice the last pause after every further
        one, never more than the full interval."""
        last = self._pause_s.get(name)
        pause = _RETRY_S if last is None else last * 2
        self._pause_s[name] = min(pause, self._interval_s)
        self._recheck_at[name] = now + self._pause_s[name]

    async def check(self, names: list[str]) -> None:
        """Check the sites of *names* and record which ones answer.

        A reachable site without an answer is tried again after
        ``_CONFIRM_S``; an unreachable one stays so without a retry.
        """
        semaphore = asyncio.Semaphore(_CONCURRENCY)

        async def _check(name: str) -> bool:
            async with semaphore:
                return await self._site_answers(name)

        async def _check_all(names: list[str]) -> dict[str, bool]:
            answers = await asyncio.gather(*map(_check, names))
            return dict(zip(names, answers, strict=True))

        up = await _check_all(names)
        if not any(up.values()) and any(map(self.is_reachable, names)):
            log.warning("plugin_health_no_answer", plugins=len(names))
            return
        failed = [
            name
            for name, answers in up.items()
            if not answers and self.is_reachable(name)
        ]
        if failed:
            await asyncio.sleep(_CONFIRM_S)
            up |= await _check_all(failed)
        now = self._clock()
        for name, answers in up.items():
            self._history.count(name, "checks")
            if answers:
                if name in self._unreachable:
                    self._unreachable.discard(name)
                    self._pause_s.pop(name, None)
                    self._recheck_at.pop(name, None)
                    log.info("plugin_reachable", plugin=name)
                continue
            self._history.count(name, "unreachable")
            if name not in self._unreachable:
                self._unreachable.add(name)
                log.warning("plugin_unreachable", plugin=name)
            self._back_off(name, now)

    async def _site_answers(self, name: str) -> bool:
        """Whether one of the plugin's domains answers.

        The plugin picks a working domain itself (``_verify_domain``), so its
        other domains count too.
        """
        plugin = self._plugins.get(name)
        domains: list[str] = getattr(plugin, "_domains", [])
        urls = dict.fromkeys(
            [getattr(plugin, "base_url", ""), *(f"https://{d}" for d in domains)]
        )
        for url in urls:
            if url and _answers(await self._prober.probe(url)):
                return True
        return False
