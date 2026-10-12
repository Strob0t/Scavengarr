"""Plugin domains from where the probe runs: DNS, connect, HEAD, GET.

    probe domains [--plugins megakino_to,movie4k] [--history]

For every ``_domains`` entry of each plugin (default: every stream plugin)
the probe resolves the name, opens a TCP connection to port 443, sends the
domain check's ``HEAD https://<domain>/`` (its timeout, the plugin's client
and headers) and a ``GET`` of the same page, and prints per step the
outcome and its time. A domain *answers* by the domain check's rule: below
400, or ``_site_answers`` (an error page or a challenge page is the site).
Run it in the dev container (the home connection) and on the Pi
(``prodctl.py probe domains``, the VPN exit) to compare the two.

``--history`` also prints the plugins' long-term record from the app on
this host (``GET http://127.0.0.1:$PORT/api/v1/stats/plugins``): per day
searches, results, timeouts and unreachable marks. Prints hosts only, no
addresses, paths or tokens; changes nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import structlog

from scavengarr.infrastructure.cache.cache_factory import create_cache
from scavengarr.infrastructure.captcha.detect import detect_challenge
from scavengarr.infrastructure.config import load_config
from scavengarr.infrastructure.plugins import httpx_base
from scavengarr.infrastructure.plugins.constants import (
    DEFAULT_DOMAIN_CHECK_TIMEOUT,
    DEFAULT_USER_AGENT,
)
from scavengarr.infrastructure.plugins.registry import PluginRegistry

# A build before step 21 lacks the rule (production until its next deploy):
# there the column stays open below 400
_SITE_ANSWERS = getattr(httpx_base, "_site_answers", None)
CONNECT_TIMEOUT = 5.0
GET_TIMEOUT = 15.0


@dataclass
class DomainCheck:
    domain: str
    steps: list[str] = field(default_factory=list)
    answers: bool | None = False


def _ms(started: float) -> str:
    return f"{(time.monotonic() - started) * 1000:.0f} ms"


def _error(exc: BaseException) -> str:
    return type(exc).__name__


def http_step(name: str, resp: httpx.Response, started: float) -> str:
    """``HEAD 200 → host`` with the challenge kind and the time; no path."""
    out = f"{name} {resp.status_code}"
    if resp.history:
        out += f" → {resp.url.host}"
    kind = detect_challenge(resp.status_code, "", resp.headers)
    if name == "GET":
        kind = detect_challenge(resp.status_code, resp.text[:65536], resp.headers)
    if kind:
        out += f" ({kind})"
    # The edge in front of the site (cloudflare's 522: its origin is gone)
    server = resp.headers.get("server")
    if server:
        out += f" [{server[:20]}]"
    return f"{out} {_ms(started)}"


def answers(resp: httpx.Response) -> bool | None:
    """The domain check's rule: below 400, else ``_site_answers``; ``None``
    on a build without that rule."""
    if resp.status_code < 400:
        return True
    return _SITE_ANSWERS(resp) if _SITE_ANSWERS is not None else None


async def _dns(domain: str) -> tuple[str, list[Any]]:
    started = time.monotonic()
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(domain, 443, type=socket.SOCK_STREAM), CONNECT_TIMEOUT
        )
    except (OSError, TimeoutError) as exc:
        return f"DNS {_error(exc)} {_ms(started)}", []
    v4 = sum(1 for info in infos if info[0] == socket.AF_INET)
    return f"DNS {v4} v4/{len(infos) - v4} v6 {_ms(started)}", infos


async def _connect(infos: list[Any]) -> str:
    started = time.monotonic()
    family, _, _, _, address = infos[0]
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(address[0], 443, family=family),
            CONNECT_TIMEOUT,
        )
    except (OSError, TimeoutError) as exc:
        return f"TCP {_error(exc)} {_ms(started)}"
    writer.close()
    await writer.wait_closed()
    return f"TCP ok {_ms(started)}"


async def check_domain(
    client: httpx.AsyncClient, domain: str, get_timeout: float = GET_TIMEOUT
) -> DomainCheck:
    check = DomainCheck(domain)
    dns, infos = await _dns(domain)
    check.steps.append(dns)
    if not infos:
        return check
    check.steps.append(await _connect(infos))
    url = f"https://{domain}/"
    for name, timeout in (
        ("HEAD", DEFAULT_DOMAIN_CHECK_TIMEOUT),
        ("GET", get_timeout),
    ):
        started = time.monotonic()
        try:
            resp = await client.request(name, url, timeout=timeout)
        except httpx.HTTPError as exc:
            check.steps.append(f"{name} {_error(exc)} {_ms(started)}")
            continue
        check.steps.append(http_step(name, resp, started))
        verdict = answers(resp)
        if verdict or check.answers is False:
            check.answers = verdict
    return check


async def check_plugin(
    plugin: Any, get_timeout: float = GET_TIMEOUT
) -> list[DomainCheck]:
    """Every domain of *plugin*, one after another, with its own client."""
    ensure = getattr(plugin, "_ensure_client", None)
    own = ensure is None
    if ensure is not None:
        client = await ensure()
    else:
        client = httpx.AsyncClient(
            follow_redirects=True, headers={"User-Agent": DEFAULT_USER_AGENT}
        )
    try:
        return [await check_domain(client, d, get_timeout) for d in plugin._domains]
    finally:
        if own:
            await client.aclose()
        else:
            await plugin.cleanup()


def history_lines(report: dict[str, Any], names: list[str]) -> list[str]:
    """Per plugin its last result day, unreachable shares and daily counts."""
    lines: list[str] = []
    plugins = report.get("plugins", {})
    for name in names:
        record = plugins.get(name)
        if record is None:
            lines.append(f"- {name}: no record")
            continue
        shares = ", ".join(
            f"{window} d {share:.0%}" if share is not None else f"{window} d -"
            for window, share in record.get("unreachable_share", {}).items()
        )
        lines.append(
            f"- {name}: last result day {record.get('last_result_day') or 'none'};"
            f" unreachable share {shares}"
        )
        for day, counts in record.get("days", {}).items():
            row = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
            lines.append(f"  - {day}: {row}")
    return lines


def report_of_snapshot(snapshot: Any) -> dict[str, Any]:
    """The endpoint's shape from the stored ``plugin_history:v1`` snapshot
    (per plugin its days; the last day with a result, no shares)."""
    plugins: dict[str, Any] = {}
    stored = snapshot.get("plugins", {}) if isinstance(snapshot, dict) else {}
    for name, days in stored.items():
        with_results = [d for d, c in days.items() if c.get("results", 0) > 0]
        plugins[name] = {
            "last_result_day": max(with_results, default=None),
            "days": {day: days[day] for day in sorted(days)},
        }
    return {"plugins": plugins}


async def _history(names: list[str], config: Any) -> None:
    """The record from the app's endpoint, else from the cache backend (a
    build before the endpoint that already keeps the record)."""
    base = "http://127.0.0.1:" + os.environ.get("PORT", "7979")
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.get(f"{base}/api/v1/stats/plugins")
    if resp.status_code == 200:
        report, source = resp.json(), "the app's endpoint"
    else:
        cache = create_cache(
            backend=config.cache.backend,
            directory=str(config.cache.directory),
            redis_url=config.cache.redis_url,
        )
        async with cache:
            snapshot = await cache.get("plugin_history:v1")
        if snapshot is None:
            print(f"\n## History: endpoint {resp.status_code}, no stored snapshot")
            return
        report, source = report_of_snapshot(snapshot), "the stored snapshot"
    print(f"\n## History ({source})")
    print("\n".join(history_lines(report, names)))


async def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="probe domains")
    parser.add_argument("--plugins", help="comma-separated plugin names")
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--get-timeout", type=float, default=GET_TIMEOUT)
    args = parser.parse_args(argv)
    env = os.getenv("SCAVENGARR_CONFIG")
    config = load_config(config_path=Path(env) if env else None)
    registry = PluginRegistry(config.plugin_dir)
    registry.discover()
    if args.plugins:
        names = [n.strip() for n in args.plugins.split(",") if n.strip()]
    else:
        names = sorted(
            set(registry.get_by_provides("stream"))  # type: ignore[arg-type]
        )
    print("| plugin | domain | DNS | TCP | HEAD | GET | answers |")
    print("|---|---|---|---|---|---|---|")
    for name in names:
        plugin = registry.get(name)
        for check in await check_plugin(plugin, args.get_timeout):
            steps = check.steps + ["–"] * (4 - len(check.steps))
            verdict = {True: "yes", False: "**no**", None: "? (no rule)"}[check.answers]
            print(f"| {name} | {check.domain} | {' | '.join(steps)} | {verdict} |")
    if args.history:
        await _history(names, config)


if __name__ == "__main__":
    # the plugins log every request at info level
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING)
    )
    asyncio.run(main(sys.argv[1:]))
