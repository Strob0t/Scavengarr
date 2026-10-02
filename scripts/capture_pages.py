"""Capture the pages a plugin fetches during a live search, for parser tests.

Usage:
    poetry run python scripts/capture_pages.py kinoger "The Last of Us" \\
        --category 5000 --season 1 --episode 1
    poetry run python scripts/capture_pages.py kinoger --fixture \\
        .cache/pages/kinoger/the-last-of-us-s1e1/02-detail.html detail-the-last-of-us

The first form runs the plugin's ``search()`` against the live site and
writes every page it fetched (``_fetch_text`` / ``_safe_fetch``) to
``.cache/pages/<plugin>/<query>/NN-<context>.html`` plus ``index.json`` (URL,
method, form data). Cloudflare-challenged pages go through the stealth
browser, like in the server. The second form stores one of those pages as
``tests/fixtures/html/<plugin>/<name>.html.gz`` (gzipped, per-visitor values
scrubbed) for ``tests/unit/infrastructure/test_real_pages.py``.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import re
from pathlib import Path
from typing import Any

from scavengarr.infrastructure.browser.shared_browser import SharedBrowserPool
from scavengarr.infrastructure.browser.stealth_pool import StealthPool
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase
from scavengarr.infrastructure.plugins.registry import PluginRegistry

_ROOT = Path(__file__).resolve().parents[1]
_CAPTURE_DIR = _ROOT / ".cache" / "pages"
_FIXTURE_DIR = _ROOT / "tests" / "fixtures" / "html"

# Per-visitor values that must not land in the repository
_SCRUB = [
    # DataLife Engine session hash
    (re.compile(r"(dle_login_hash\s*=\s*')[0-9a-f]+(')"), r"\g<1>0\g<2>"),
    # Laravel session CSRF token (s.to)
    (re.compile(r'(<meta name="csrf-token" content=")[^"]+(")'), r"\g<1>0\g<2>"),
    # s.to's encrypted redirect tokens (/r?t=<laravel payload>)
    (re.compile(r"(/r\?t=)[A-Za-z0-9%=+/_-]{20,}"), r"\g<1>scrubbed"),
]


def scrub(html: str) -> str:
    """*html* without per-visitor values (session hashes)."""
    for pattern, replacement in _SCRUB:
        html = pattern.sub(replacement, html)
    return html


def store_fixture(plugin: str, page: Path, name: str) -> Path:
    """Write *page* scrubbed and gzipped as a fixture of *plugin*."""
    target = _FIXTURE_DIR / plugin / f"{name}.html.gz"
    target.parent.mkdir(parents=True, exist_ok=True)
    data = scrub(page.read_text()).encode()
    target.write_bytes(gzip.compress(data, compresslevel=9, mtime=0))
    return target


def _record(plugin: Any, pages: list[dict[str, Any]]) -> None:
    """Wrap the plugin's fetch helpers so every answer lands in *pages*."""
    fetch_text = plugin._fetch_text
    safe_fetch = plugin._safe_fetch

    async def recording_fetch_text(url: str, **kwargs: Any) -> str | None:
        text = await fetch_text(url, **kwargs)
        pages.append({"url": url, "method": "GET", **kwargs, "text": text})
        return text

    async def recording_safe_fetch(url: str, **kwargs: Any) -> Any:
        resp = await safe_fetch(url, **kwargs)
        text = resp.text if resp is not None else None
        sent = {
            k: kwargs[k] for k in ("method", "context", "params", "data") if k in kwargs
        }
        pages.append({"url": url, **sent, "text": text})
        return resp

    plugin._fetch_text = recording_fetch_text
    plugin._safe_fetch = recording_safe_fetch


async def capture(plugin_name: str, query: str, args: argparse.Namespace) -> Path:
    """Run one live search of *plugin_name* and write the fetched pages."""
    pool = SharedBrowserPool(headless=False)
    HttpxPluginBase.set_browser_fetcher(
        StealthPool(browser_pool=pool, timeout_ms=30_000)
    )
    plugin = PluginRegistry(_ROOT / "plugins").get(plugin_name)
    pages: list[dict[str, Any]] = []
    _record(plugin, pages)
    try:
        results = await plugin.search(
            query, args.category, season=args.season, episode=args.episode
        )
    finally:
        cleanup = getattr(plugin, "cleanup", None)
        if cleanup is not None:
            await cleanup()
        await pool.cleanup()
    run = query if args.season is None else f"{query} s{args.season}e{args.episode}"
    out = (
        _CAPTURE_DIR / plugin_name / re.sub(r"[^a-z0-9]+", "-", run.lower()).strip("-")
    )
    out.mkdir(parents=True, exist_ok=True)
    index = []
    for number, page in enumerate(pages):
        context = re.sub(r"[^a-z0-9]+", "-", str(page.get("context") or "page"))
        name = f"{number:02d}-{context.strip('-')}.html"
        text = page.pop("text")
        if text is not None:
            (out / name).write_text(text)
        index.append({**page, "file": name, "size": len(text or "")})
    (out / "index.json").write_text(json.dumps(index, indent=1))
    print(f"{len(results)} results, {len(pages)} pages in {out}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("plugin")
    parser.add_argument("query", nargs="?", default="")
    parser.add_argument("--category", type=int)
    parser.add_argument("--season", type=int)
    parser.add_argument("--episode", type=int)
    parser.add_argument(
        "--fixture", nargs=2, metavar=("PAGE", "NAME"), help="store PAGE as NAME"
    )
    args = parser.parse_args()
    if args.fixture:
        page, name = args.fixture
        print(store_fixture(args.plugin, Path(page), name))
        return
    if not args.query:
        parser.error("a query is needed to capture pages")
    asyncio.run(capture(args.plugin, args.query, args))


if __name__ == "__main__":
    main()
