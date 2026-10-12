"""sto's hoster link-outs (``/r?t=<token>``): do they leave the site?

``probe sto_linkout [QUERY] [SEASON] [EPISODE]`` (default: Haus des Geldes
S01E01) creates the sto plugin from the app's registry, finds the series with
the plugin's own search, reads the episode page's hoster buttons without
resolving them and follows the first two link-outs with the plugin's client,
headers and the episode page as ``Referer``, one hop at a time, as the plugin
does. Per link: the status of each hop, the hops, the final host, whether the
last body looks like a gate (Turnstile, Cloudflare challenge) and the time;
then the plugin's own resolution of the same link (``_resolve_redirect``, no
browser). Runs in the production container (``prodctl.py probe``) and in the
dev container (``poetry run python scripts/probes/sto_linkout.py``).

Read-only: no cache writes, no browser. Prints hosts only, never a path or a
token.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import structlog

from scavengarr.infrastructure.config import load_config
from scavengarr.infrastructure.plugins.registry import PluginRegistry

LINKS = 2
MAX_HOPS = 5
# What a gate page carries instead of a redirect
GATE_MARKERS = {
    "turnstile": ("cf-turnstile", "challenges.cloudflare.com/turnstile"),
    "cloudflare": ("Just a moment", "cf_chl_", "challenge-platform"),
    "captcha": ("g-recaptcha", "hcaptcha"),
}
# Words that tell what a page without a redirect is
KEYWORDS = (
    "turnstile",
    "captcha",
    "challenge",
    "iframe",
    "window.top",
    "top.location",
    "location.href",
    "cookie",
    "<form",
)


def summary(body: str) -> str:
    """A body without its tokens: title, the hosts it loads, keywords."""
    title = re.search(r"<title[^>]*>([^<]{0,80})", body, re.IGNORECASE)
    hosts = sorted(
        {
            urlparse(src).hostname or "(relative)"
            for src in re.findall(r"""src=["']([^"']+)""", body)
        }
    )
    words = [w for w in KEYWORDS if w in body.lower()]
    return (
        f"title {title.group(1).strip() if title else '–'!r}"
        f" | loads {', '.join(hosts) or 'nothing'}"
        f" | words {', '.join(words) or 'none'}"
    )


def gates(body: str) -> list[str]:
    """The gate kinds whose markers *body* carries."""
    return [
        kind for kind, marks in GATE_MARKERS.items() if any(m in body for m in marks)
    ]


async def trace(plugin: object, url: str, referer: str) -> str:
    """Follow *url* hop by hop as the plugin does; one masked line."""
    client = await plugin._ensure_client()  # type: ignore[attr-defined]
    kwargs = plugin._request_kwargs(client, url)  # type: ignore[attr-defined]
    headers = dict(kwargs.get("headers") or {})
    kwargs["headers"] = {**headers, "Referer": referer}
    site = urlparse(url).hostname
    statuses: list[str] = []
    current = url
    started = time.monotonic()
    body = ""
    for _ in range(MAX_HOPS):
        try:
            resp = await client.get(current, follow_redirects=False, **kwargs)
        except Exception as exc:  # noqa: BLE001 - the error is the finding
            statuses.append(type(exc).__name__)
            break
        statuses.append(str(resp.status_code))
        location = resp.headers.get("location") if resp.is_redirect else None
        if not location:
            body = resp.text
            break
        current = urljoin(current, location)
        if urlparse(current).hostname != site:
            break
    final = urlparse(current).hostname or "?"
    left = "left the site" if final != site else "stayed on the site"
    found = ", ".join(gates(body)) or "none"
    line = (
        f"hops {' > '.join(statuses)} | final host {final} ({left})"
        f" | gate markers: {found} | body {len(body)} B"
        f" | {time.monotonic() - started:.1f} s"
    )
    return f"{line}\n  page: {summary(body)}" if body else line


async def main(query: str, season: int, episode: int) -> None:
    env = os.getenv("SCAVENGARR_CONFIG")
    config = load_config(config_path=Path(env) if env else None)
    registry = PluginRegistry(config.plugin_dir)
    registry.discover()
    plugin = registry.get("sto")
    try:
        await plugin._ensure_client()  # type: ignore[attr-defined]
        await plugin._verify_domain()  # type: ignore[attr-defined]
        base = plugin.base_url  # type: ignore[attr-defined]
        print(f"site {urlparse(base).hostname}")
        series = await plugin._paginate_search(query)  # type: ignore[attr-defined]
        wanted = query.casefold()
        hit = next(
            (s for s in series if s.get("title", "").casefold() == wanted),
            series[0] if series else None,
        )
        if hit is None:
            print(f"no series for {query!r}")
            return
        print(f"series {hit.get('title')!r} ({len(series)} search hits)")
        episode_url = f"{hit['url'].rstrip('/')}/staffel-{season}/episode-{episode}"
        hosters = await plugin._scrape_episode_hosters(episode_url)  # type: ignore[attr-defined]
        print(
            f"episode S{season:02d}E{episode:02d}: {len(hosters)} hoster buttons"
            f" ({', '.join(h['provider'] for h in hosters)})"
        )
        for h in hosters[:LINKS]:
            link = urljoin(base, h["play_url"])
            print(f"- {h['provider']}: {await trace(plugin, link, episode_url)}")
            started = time.monotonic()
            target = await plugin._resolve_redirect(  # type: ignore[attr-defined]
                link, context="probe", referer=episode_url
            )
            host = urlparse(target).hostname if target else None
            print(
                f"  plugin resolution: {host or 'unresolved'}"
                f" ({time.monotonic() - started:.1f} s)"
            )
    finally:
        await plugin.cleanup()  # type: ignore[attr-defined]


if __name__ == "__main__":
    # the plugins log every request at info level
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING)
    )
    argv = sys.argv[1:]
    asyncio.run(
        main(
            argv[0] if argv else "Haus des Geldes",
            int(argv[1]) if len(argv) > 1 else 1,
            int(argv[2]) if len(argv) > 2 else 1,
        )
    )
