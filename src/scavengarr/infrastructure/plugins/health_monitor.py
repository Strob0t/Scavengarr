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

import structlog

from scavengarr.domain.entities.scoring import ProbeResult
from scavengarr.domain.ports.plugin_registry import PluginRegistryPort
from scavengarr.infrastructure.scoring.health_prober import HealthProber

log = structlog.get_logger(__name__)

# Unreachable sites are checked again this often; the first check waits
# for the start's own load (first requests, browser warm-up)
_RETRY_S = 300.0
_FIRST_CHECK_S = 60.0
_CONCURRENCY = 5


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

    Every plugin is checked every *interval_s*, an unreachable one every 5
    minutes in between. One failed check marks a site unreachable, one
    answer brings it back. A check in which no site answers changes
    nothing: then the own network or DNS is down, not every site.
    """

    def __init__(
        self,
        *,
        prober: HealthProber,
        plugins: PluginRegistryPort,
        names: list[str],
        interval_s: float,
    ) -> None:
        self._prober = prober
        self._plugins = plugins
        self._names = names
        self._interval_s = interval_s
        self._tick_s = min(_RETRY_S, interval_s)
        self._next_full = 0.0
        self._unreachable: set[str] = set()

    def is_reachable(self, name: str) -> bool:
        return name not in self._unreachable

    async def run_forever(self) -> None:
        """Check the sites until cancelled (an app lifespan task)."""
        await asyncio.sleep(_FIRST_CHECK_S)
        while True:
            try:
                names = self._due(time.monotonic())
                if names:
                    await self.check(names)
            except Exception:
                log.error("plugin_health_check_failed", exc_info=True)
            await asyncio.sleep(self._tick_s)

    def _due(self, now: float) -> list[str]:
        """The plugins to check at *now*: all once per interval, else the
        unreachable ones."""
        if now >= self._next_full:
            self._next_full = now + self._interval_s
            return list(self._names)
        return sorted(self._unreachable)

    async def check(self, names: list[str]) -> None:
        """Check the sites of *names* and record which ones answer."""
        semaphore = asyncio.Semaphore(_CONCURRENCY)

        async def _check(name: str) -> bool:
            async with semaphore:
                return await self._site_answers(name)

        up = dict(zip(names, await asyncio.gather(*map(_check, names)), strict=True))
        if not any(up.values()) and any(map(self.is_reachable, names)):
            log.warning("plugin_health_no_answer", plugins=len(names))
            return
        for name, answers in up.items():
            if answers and name in self._unreachable:
                self._unreachable.discard(name)
                log.info("plugin_reachable", plugin=name)
            elif not answers and name not in self._unreachable:
                self._unreachable.add(name)
                log.warning("plugin_unreachable", plugin=name)

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
