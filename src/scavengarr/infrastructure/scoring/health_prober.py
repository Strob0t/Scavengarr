"""Health prober — HEAD/GET check against plugin base URLs."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import httpx
import structlog

from scavengarr.domain.entities.scoring import ProbeResult
from scavengarr.infrastructure.captcha.detect import (
    ChallengeKind,
    detect_challenge,
    detect_challenge_headers,
)

log = structlog.get_logger(__name__)

_BLOCKING_KINDS: frozenset[ChallengeKind | None] = frozenset(
    {"cloudflare_page", "ddos_guard"}
)


class HealthProber:
    """Probes plugin base URLs to check reachability and latency.

    Strategy:
    1. HEAD request to the base URL origin.
    2. On 405/501 (method not allowed), fall back to GET with
       ``Range: bytes=0-0`` to minimise bandwidth.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        timeout: float = 5.0,
    ) -> None:
        self._http = http_client
        self._timeout = timeout

    async def probe(self, base_url: str) -> ProbeResult:
        """Probe a single base URL and return a ProbeResult."""
        started_at = datetime.now(timezone.utc)
        t0 = time.monotonic()

        try:
            resp = await self._http.head(
                base_url,
                timeout=self._timeout,
                follow_redirects=True,
            )

            kind: ChallengeKind | None
            if resp.status_code in (405, 501):
                resp = await self._http.get(
                    base_url,
                    timeout=self._timeout,
                    follow_redirects=True,
                    headers={"Range": "bytes=0-0"},
                )
                # GET has a body — use full body-based detection
                kind = detect_challenge(resp.status_code, resp.text, resp.headers)
            else:
                # HEAD has no body: the headers decide
                kind = detect_challenge_headers(resp.status_code, resp.headers)

            # Only page blocks count; a login captcha on a homepage does not.
            captcha = kind in _BLOCKING_KINDS
            if captcha:
                log.info("health_probe_challenge", url=base_url, challenge=kind)

            duration_ms = (time.monotonic() - t0) * 1000
            ok = resp.status_code < 400 and not captcha

            return ProbeResult(
                started_at=started_at,
                duration_ms=duration_ms,
                ok=ok,
                http_status=resp.status_code,
                captcha_detected=captcha,
                error_kind="captcha" if captcha else None,
            )
        except httpx.TimeoutException:
            duration_ms = (time.monotonic() - t0) * 1000
            return ProbeResult(
                started_at=started_at,
                duration_ms=duration_ms,
                ok=False,
                error_kind="timeout",
            )
        except httpx.HTTPError as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            log.debug(
                "health_probe_error",
                url=base_url,
                error=str(exc),
            )
            return ProbeResult(
                started_at=started_at,
                duration_ms=duration_ms,
                ok=False,
                error_kind="http_error",
            )

    async def probe_all(
        self,
        plugins: dict[str, str],
        concurrency: int = 5,
    ) -> dict[str, ProbeResult]:
        """Probe multiple plugins concurrently.

        Args:
            plugins: Mapping of plugin name → base URL.
            concurrency: Maximum parallel probes.

        Returns:
            Mapping of plugin name → ProbeResult.
        """
        sem = asyncio.Semaphore(concurrency)
        results: dict[str, ProbeResult] = {}

        async def _probe_one(name: str, url: str) -> None:
            async with sem:
                result = await self.probe(url)
                results[name] = result
                log.debug(
                    "health_probe_done",
                    plugin=name,
                    ok=result.ok,
                    duration_ms=round(result.duration_ms, 1),
                )

        tasks = [
            asyncio.create_task(_probe_one(name, url)) for name, url in plugins.items()
        ]
        await asyncio.gather(*tasks)
        return results
