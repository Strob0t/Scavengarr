[← Back to Index](../features/README.md)

# Plan: Repair Broken Plugins

**Status:** Triaged, not started (2026-09-28)
**Priority:** High (11 of 42 plugins return 0 results)
**Related:** `plugins/`, `tests/live/test_plugin_smoke.py`, `CHANGELOG.md` → `KNOWN_ISSUES`

## Context

`poetry run pytest -m live` (live smoke tests against the real sites) fails for 11 httpx plugins. Every failure is `returned 0 results`. The offline suite is green, so the parsers still match their fixtures; the sites changed.

Triage method (2026-09-28, from inside the devcontainer, no proxy):

1. `curl` each domain in `_DOMAINS` with a browser User-Agent: status code, redirect target, Cloudflare/DDoS-Guard markers.
2. Run the plugin's `search()` directly with DEBUG logging and read the warning events (script below).
3. For "0 results without an error" cases: fetch the search URL and check whether the query term appears in the HTML (parser vs. site problem).

## Findings

| Plugin | Root cause | Evidence | Suggested fix |
|---|---|---|---|
| filmfans | Cloudflare blocks httpx | `https://filmfans.org/` → 403, CF challenge page (~5.4 KB); every movie page 403 | Move to Playwright/StealthPool, or CF clearance cookie |
| kinoger | Cloudflare blocks httpx | `kinoger.com` + `kinoger.to` → 403 CF challenge; search URL 403 | Same as filmfans |
| serienfans | Cloudflare on detail pages | Search API still works (`serienfans_search_api count=1`), `https://serienfans.org/breaking-bad` → 403 | Same as filmfans (only detail stage needs a browser) |
| fireani | API endpoint removed | `GET https://fireani.me/api/anime/search` → 404; homepage 200 | Find the new search endpoint (inspect site XHR) |
| nox | API endpoint removed | `GET https://nox.to/api/frontend/search/Iron%20Man` → 404; `nox.tv` → 301 `nox.to` | Find the new search endpoint |
| dataload | Search form changed | Login OK (`dataload_login_success`), `POST https://www.data-load.me/search/search` → 400 | Inspect the vBulletin/XenForo search form (new token/field names) |
| scnlog | Selectors broken | `https://scnlog.me/?s=Iron+Man` → 200, HTML contains "iron man" 119×, parser finds 0 | Update search result selectors — **Fixed 2026-09-28:** new layout (li.row search results, h1.single-title, plain links in div.download) |
| kinoking | Search yields nothing | `https://kinoking.cc/?s=Iron+Man` → 200 (250 KB), query term absent, plugin logs `count=0` | Check whether search moved (JS/API) or needs another URL |
| byte (Playwright) | Parser, not Cloudflare | Anti-bot benchmark 2026-09-28: `https://byte.to/?q=Iron+Man&t=1` → 200 with hits in every browser variant, yet the plugin returns 0 | Re-check result selectors / iframe link extraction — **Fixed 2026-09-28:** moved to httpx; links from /widgets/button.php, go.php resolved |
| streamworld (Playwright) | Unknown, 0 results | Live smoke 2026-09-28 fails with playwright + stealth and with Patchright alike (not Cloudflare-related) | Triage search/detail selectors |
| boerse (Playwright) | Network error in plugin | Live smoke 2026-09-28 skips boerse (and byte, fixed above) with "Network error reaching …" on both browser stacks | Check the plugin's navigation/domain handling |
| hdfilme | Domain moved | `hdfilme.legal` → 301 `hdfilme.press` → `hdfilme.cafe`; DLE search there returns only 749 bytes | Update `_DOMAINS`, re-check search URL on the new domain — **Status 2026-09-28:** domain updated to hdfilme.cafe; blocked upstream: search returns a PHP fatal error (also in a real browser), film links moved to devideosrc.co behind Turnstile |
| streamcloud | Detail page structure changed | Search works (domain now `streamcloud.download`, `.plus` → 301 `.uno`), every detail page logs `streamcloud_no_streams` | Update detail/stream selectors; update `_DOMAINS` |
| streamkiste | Stream source changed | Search works (`streamkiste.taxi` → 301 `.bid`), detail pages `no_streams`, `https://meinecloud.click/ddl/tt…` → 404 | Update stream extraction (MeineCloud endpoint gone); update `_DOMAINS` |

Quick wins first: scnlog, byte, hdfilme, fireani, nox (selector/domain/endpoint). The three Cloudflare plugins need a design decision (Playwright plugin vs. shared StealthPool for httpx plugins). Decided and done (2026-09-28): browser fallback port for httpx plugins; filmfans, kinoger, serienfans (and ddlspot, ddlvalley, scnsrc) return results again, see `docs/plans/antibot-patchright.md`.

## Repro script

```python
# save as tmp/triage.py, run: poetry run python tmp/triage.py <plugin> "<query>"
from __future__ import annotations
import asyncio, importlib.util, logging, sys
import structlog

structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG))
spec = importlib.util.spec_from_file_location(sys.argv[1], f"plugins/{sys.argv[1]}.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

async def main() -> None:
    p = mod.plugin
    try:
        res = await asyncio.wait_for(p.search(sys.argv[2]), 90)
        print("RESULT", len(res), "base_url", p.base_url)
    except Exception as e:
        print("EXC", type(e).__name__, str(e)[:300])
    finally:
        await p.cleanup()

asyncio.run(main())
```

Live test for a single plugin: `poetry run pytest -m live -k "<plugin>" -v`.

Until 2026-09-28 the Playwright smoke tests were always skipped ("Chromium not installed"): the Chromium check used the sync API inside the running event loop. The check is async now, so Playwright plugins are really exercised.

## Prerequisites

- Site analysis per `docs/features/python-plugins.md` ("Adding a New Plugin") uses `playwright-mcp`. It runs over stdio from the committed `.mcp.json` (no Docker); `.devcontainer/setup.sh` installs Chromium with its system libraries. Verify after the next container recreate that the `playwright-mcp` tools are available in Claude Code.
