"""Mini-search prober — runs limited searches against plugins."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.scoring import ProbeResult
from scavengarr.domain.ports.plugin_registry import PluginRegistryPort

log = structlog.get_logger(__name__)


def _extract_hoster(url: str) -> str:
    """Extract hoster name from URL domain (e.g. 'https://voe.sx/e/x' → 'voe')."""
    try:
        hostname = urlparse(url).hostname
        if not hostname:
            return ""
        parts = hostname.split(".")
        return parts[-2] if len(parts) >= 2 else parts[0]
    except Exception:  # noqa: BLE001
        return ""


class MiniSearchProber:
    """Runs a limited search query against a plugin and checks results.

    Optionally HEAD-checks a few result links to assess hoster
    reachability.  Only links pointing to *supported* hosters (those
    with registered resolvers) are HEAD-checked.
    """

    def __init__(
        self,
        plugins: PluginRegistryPort,
        http_client: httpx.AsyncClient,
        supported_hosters: frozenset[str] = frozenset(),
        hoster_check_count: int = 3,
        hoster_timeout: float = 5.0,
    ) -> None:
        self._plugins = plugins
        self._http = http_client
        self._supported_hosters = supported_hosters
        self._hoster_check_count = hoster_check_count
        self._hoster_timeout = hoster_timeout

    async def probe(
        self,
        plugin_name: str,
        query: str,
        category: int,
        *,
        max_items: int = 20,
        timeout: float = 10.0,
    ) -> ProbeResult:
        """Run a mini-search probe against one plugin.

        Args:
            plugin_name: Name of the plugin to probe.
            query: Search query string.
            category: Torznab category ID.
            max_items: Limit results to this many items.
            timeout: Overall timeout for the search.

        Returns:
            ProbeResult summarising the outcome.
        """
        started_at = datetime.now(timezone.utc)
        t0 = time.monotonic()

        try:
            plugin = self._plugins.get(plugin_name)
        except KeyError:
            return ProbeResult(
                started_at=started_at,
                duration_ms=0.0,
                ok=False,
                error_kind="plugin_not_found",
            )

        try:
            results = await asyncio.wait_for(
                plugin.search(query, category=category),
                timeout=timeout,
            )
        except TimeoutError:
            duration_ms = (time.monotonic() - t0) * 1000
            return ProbeResult(
                started_at=started_at,
                duration_ms=duration_ms,
                ok=False,
                error_kind="timeout",
            )
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            log.debug(
                "search_probe_error",
                plugin=plugin_name,
                error=str(exc),
            )
            return ProbeResult(
                started_at=started_at,
                duration_ms=duration_ms,
                ok=False,
                error_kind="search_error",
            )

        items_found = len(results)
        items_used = min(items_found, max_items)

        # Classify links by supported-hoster status.
        links = [r.download_link for r in results[:max_items] if r.download_link]
        supported_links: list[str] = []
        hoster_total = 0
        hoster_supported = 0
        for link in links:
            hoster = _extract_hoster(link)
            if not hoster:
                continue
            hoster_total += 1
            if hoster in self._supported_hosters:
                hoster_supported += 1
                supported_links.append(link)

        # HEAD-check only links to supported hosters.
        hoster_checked, hoster_reachable = await self._check_hosters(
            supported_links[: self._hoster_check_count]
        )

        duration_ms = (time.monotonic() - t0) * 1000
        return ProbeResult(
            started_at=started_at,
            duration_ms=duration_ms,
            ok=True,
            items_found=items_found,
            items_used=items_used,
            hoster_checked=hoster_checked,
            hoster_reachable=hoster_reachable,
            hoster_supported=hoster_supported,
            hoster_total=hoster_total,
        )

    async def _check_hosters(self, links: list[str]) -> tuple[int, int]:
        """HEAD-check a small sample of links.

        Returns:
            ``(checked, reachable)`` counts.
        """
        if not links:
            return 0, 0

        checked = len(links)

        async def _head(url: str) -> bool:
            try:
                resp = await self._http.head(
                    url,
                    timeout=self._hoster_timeout,
                    follow_redirects=True,
                )
                return resp.status_code < 400
            except httpx.HTTPError:
                return False

        results = await asyncio.gather(*[_head(u) for u in links])
        reachable = sum(1 for r in results if r)
        return checked, reachable
