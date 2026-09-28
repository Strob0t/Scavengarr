# Plan: Anti-Bot Hardening (Patchright + Browser Fallback)

**Status:** Phase 0 done, gate failed for all headless variants; headful test pending (2026-09-28)
**Priority:** High (blocks 6 plugins and 3 hoster resolvers)
**Related:** `docs/plans/plugin-repair.md`, `CHANGELOG.md` → `KNOWN_ISSUES`,
`src/scavengarr/infrastructure/plugins/{playwright_base,shared_browser,httpx_base}.py`,
`src/scavengarr/infrastructure/hoster_resolvers/{stealth_pool,cloudflare,supervideo,probe,xfs}.py`,
`src/scavengarr/interfaces/composition.py`

## Problem

Bot protection currently blocks:

| Target | Engine | Symptom |
|---|---|---|
| filmfans, kinoger | httpx | Cloudflare JS challenge (403) on every page |
| serienfans | httpx | Search API works, detail pages 403 (Cloudflare) |
| ddlspot, ddlvalley, scnsrc | Playwright + playwright-stealth | 0 results, Cloudflare challenge not cleared headless |
| veev, vinovo, wolfstream (XFS resolvers) | httpx | `needs_captcha=True`, resolver returns `None` (Turnstile / anti-bot JS) |

Current browser stack:

- `playwright` 1.57.0 + `playwright-stealth` 2.0.1 (JS evasions injected per context).
- `PlaywrightPluginBase` applies `Stealth()` and a fixed `user_agent=DEFAULT_USER_AGENT`
  in `_ensure_context()` and `isolated_search()`; `headless=True`.
- Two independent Chromium processes: `SharedBrowserPool` (Playwright plugins) and
  `StealthPool` (SuperVideo fallback + stealth probes in `probe.py`).
- httpx plugins have no browser fallback at all.

Why the current stealth fails (see research notes in the 2026-09-28 session):
playwright-stealth only overrides JS properties after the fact. It leaves the
CDP `Runtime.enable` / `Console.enable` side effects and the automation launch
flags (`--enable-automation`, …) in place, which Cloudflare, DataDome etc.
detect independently of any JS patch. The fixed User-Agent also disagrees with
the real Chromium version exposed via client hints (`sec-ch-ua`).

## Goals / non-goals

Goals:
- Replace playwright + playwright-stealth with Patchright (drop-in fork that
  removes those leaks at the source).
- One browser capability that httpx plugins can use for individual
  Cloudflare-blocked pages, instead of rewriting them as Playwright plugins.
- Optional external challenge solver (FlareSolverr/Byparr) behind the same port.

Non-goals:
- Paid scraping APIs, residential proxies, CAPTCHA-solving services.
- Rewriting plugins to nodriver/SeleniumBase/Camoufox (only reconsidered if the
  Phase 0 gate fails).
- Fixing the non-Cloudflare plugin breakages (scnlog, hdfilme, fireani, nox,
  dataload, kinoking, streamcloud, streamkiste) — tracked in `plugin-repair.md`.

## Phase 0 — Measure first (spike, no production code)

A throwaway script (`tmp/antibot_bench.py`, not committed) loads each target
once per variant and records: HTTP status, whether the CF title marker cleared,
whether an expected content marker is present, time to clear, peak RSS.

Targets: filmfans, kinoger, serienfans detail page, ddlspot, ddlvalley, scnsrc,
byte search pages, one known-good embed URL each for veev, vinovo, wolfstream.

| Variant | Stack | Purpose |
|---|---|---|
| A | playwright + stealth, fixed UA, headless (today) | Baseline |
| B | Patchright, default UA, headless | Main candidate |
| C | Patchright, fixed UA, headless | Isolates the UA effect |
| D | Patchright, default UA, headful under `xvfb-run` | Is headless itself the blocker? |
| E | Patchright, `channel="chrome"` (Google Chrome, amd64 only) | Only if B/D fall short |

Gate:
- B (or D) clears ≥ 4 of the 7 site targets → continue with Phase 1.
- Nothing beats A → stop; evaluate nodriver/Camoufox or jump to Phase 3.
- Record the result table in this file before starting Phase 1.

### Phase 0 results (2026-09-28)

Setup: devcontainer, residential IP (AS3209 Vodafone), 2 rounds per variant,
30 s wait per page. playwright 1.57.0 (Chromium 143) + playwright-stealth 2.0.1,
Patchright 1.63.0 (Chromium 153), Camoufox 0.5.6 (Firefox 152), nodriver 0.50.3.

| Target | A today | A2 PW new headless | B Patchright | C Patchright + UA | D Patchright new headless |
|---|---|---|---|---|---|
| filmfans | 403, blocked | 403, blocked | 403, blocked | 403, blocked | 403, blocked |
| kinoger | 403, blocked | 403, blocked | 403, blocked | 403, blocked | 403, blocked |
| serienfans (detail) | 403, blocked | 403, blocked | 403, blocked | 403, blocked | 403, blocked |
| ddlspot | 403, blocked | 403, blocked | 403, blocked | 403, blocked | 403, blocked |
| ddlvalley | 403, blocked | 403, blocked | 403, blocked | 403, blocked | 403, blocked |
| scnsrc | 403, blocked | 403, blocked | 403, blocked | 403, blocked | 403, blocked |
| byte | 200, results | 200, results | 200, results | 200, results | 200, results |

Follow-up diagnosis on filmfans:
- The challenge is Cloudflare **managed** (`cType: 'managed'`) and renders an
  **interactive Turnstile checkbox** ("Verify you are human"); it never
  auto-clears.
- A real mouse click on the checkbox (verified by screenshot) is rejected:
  Cloudflare re-issues the challenge with a new Ray ID. Tried with Patchright
  (Chromium new headless), Camoufox (headless, `humanize=True`) and nodriver
  (`tab.verify_cf()`), 3 attempts each. All rejected.
- Not the IP: residential ISP address.

Conclusions:
- Patchright alone gives **no improvement** over today's stack on these
  targets. Fingerprint, User-Agent and automation-protocol leaks are not the
  deciding factor; every headless browser is rejected at the Turnstile step.
- **byte is not Cloudflare-blocked**: every variant gets HTTP 200 with search
  hits. Its 0 results are a parser problem → moved to `plugin-repair.md`.
- Veev/vinovo/wolfstream were not measured (no known-good embed URLs).
- Remaining untested variable: **headful browser on a virtual display**
  (variant D as planned). A user-space Xvfb cannot start without root
  (`/usr/bin/xkbcomp` is hard-wired), so this needs `xvfb` installed in the
  container. The same applies to FlareSolverr/Byparr, whose images run a
  headful browser under Xvfb.
- Next step: headful run of variants B/Camoufox/nodriver under `xvfb-run`.
  If headful passes → Phase 1 + Phase 2 with a headful StealthPool (Xvfb in
  `Dockerfile.prod`). If headful also fails → the three httpx plugins and the
  three Playwright plugins stay broken without a CAPTCHA-solving service
  (non-goal); drop Phase 1/2 and document them as unsupported.

## Phase 1 — Patchright instead of playwright + playwright-stealth

Changes:
1. `pyproject.toml`: remove `playwright` and `playwright-stealth`, add `patchright`.
   First commit pins the matching upstream version (`patchright` 1.57.x) so the
   swap is the only behavioral change; a follow-up commit bumps to the current
   release (1.63.x at time of writing) for a current Chromium.
2. Imports `playwright.async_api` → `patchright.async_api` in
   `playwright_base.py`, `shared_browser.py`, `stealth_pool.py`, `context_vars.py`,
   plugins `animeloads`, `boerse`, `byte`, `moflix`, `mygully`, `streamworld`,
   and `tests/live/{conftest,test_plugin_smoke}.py`.
   Unit tests patch `scavengarr.…async_playwright`, so their patch targets stay valid.
3. Remove `Stealth().apply_stealth_async(...)` from `PlaywrightPluginBase._ensure_context()`,
   `PlaywrightPluginBase.isolated_search()` and `StealthPool._ensure_context()`.
   Patchright and playwright-stealth must not be combined.
   Keep the resource blocking routes. `_stealth` then only controls resource
   blocking → rename to `_block_resources` (overridden in ddlspot, ddlvalley, scnsrc).
4. Stop forcing a User-Agent in browser contexts: `new_context()` no longer passes
   `user_agent=` by default (Patchright recommendation). A plugin may still opt in
   via an explicit override. httpx plugins keep `DEFAULT_USER_AGENT`.
5. Headless stays driven by `playwright.headless`. Headful/xvfb only if Phase 0
   variant D was clearly better — then add `xvfb` to `Dockerfile.prod` and
   document the RAM cost.
6. `Dockerfile.prod`: `python -m patchright install chromium`; verify the browser
   cache path (`/root/.cache/ms-playwright` today) and adjust the `COPY` line.
   `.devcontainer/setup.sh`: install the Patchright Chromium for the Python venv
   (the existing `npx @playwright/mcp` install is for the MCP server only).
7. XFS captcha hosters: flip `needs_captcha` for veev/vinovo/wolfstream only if
   Phase 0 showed they pass. They are resolved via httpx today, so passing
   requires routing them through the browser port (Phase 2), one commit per hoster.

Known Patchright limitation: the Console domain is disabled
(`page.on("console")` never fires). No current code uses it; add a note to
`playwright_base.py` so future plugins don't rely on it.

Tests:
- `test_stealth_pool.py` (52 stealth references): drop assertions on
  `Stealth`, keep lifecycle/probe/resource-blocking tests.
- `test_playwright_base.py`: add tests that no stealth is applied, no
  `user_agent` is passed by default, and an explicit override is still honored.
- Live: `poetry run pytest -m live -k "ddlspot or ddlvalley or scnsrc or byte"` plus
  all 9 Playwright plugins — must not regress vs. baseline.

Acceptance: offline suite + pre-commit green; no Playwright plugin regresses
in live smoke; the four Cloudflare Playwright plugins match the Phase 0 result;
SuperVideo stealth fallback still resolves.

Rollback: revert the commit(s); dependency swap is self-contained.

## Phase 2 — Browser fallback port for httpx plugins

Design:

- **Port** `src/scavengarr/domain/ports/browser_fetcher.py`:

  ```python
  class BrowserFetcherPort(Protocol):
      async def fetch_text(self, url: str, *, timeout: float) -> str | None: ...
  ```

  Returns the rendered page body after the challenge cleared, `None` on failure.
  Two implementations are planned (StealthPool now, FlareSolverr in Phase 3),
  so the port is justified.

- **Browser package** `src/scavengarr/infrastructure/browser/`:
  move `SharedBrowserPool` (from `plugins/`), `StealthPool` (from
  `hoster_resolvers/`) and `cloudflare.py` here, keeping re-exports at the old
  paths only if needed for a transition commit.
  `StealthPool` obtains its browser from `SharedBrowserPool.warmup()` instead of
  launching a second Chromium → one browser process instead of two.
  `StealthPool.fetch_text()` reuses the `goto` + `wait_for_cloudflare` logic of
  `probe_url()` and is bounded by a semaphore sized from the existing
  `max_concurrent_playwright` setting (no new knob).

- **HttpxPluginBase**:
  - `set_browser_fetcher(fetcher: BrowserFetcherPort | None)` classmethod,
    wired in `composition.py` next to `set_shared_http_client()`.
  - `_fetch_text(url, *, context="") -> str | None`: httpx GET; if
    `is_cloudflare_challenge(status, body)` and a fetcher is set, retry via the
    fetcher; otherwise same logging/`None` semantics as `_safe_fetch()`.
  - Without a fetcher the behavior is exactly today's (graceful degradation).

- **Plugins**: filmfans, kinoger, serienfans switch their HTML page fetches from
  `client.get(...)` to `self._fetch_text(...)`. JSON API calls that still work
  over httpx (serienfans search API) stay on httpx.

- **Config**: `playwright.browser_fallback` (bool, default `true`) so operators on
  small hosts can disable the extra Chromium load. When `false`, no fetcher is
  injected.

Out of scope for Phase 2: reusing `cf_clearance` cookies in httpx — the cookie
is bound to the browser's TLS fingerprint and fails with httpx (see Phase 3).

Risks:
- Latency: a browser fetch costs seconds; detail-page fan-out (semaphore 3) may
  exceed the plugin search timeout in `PluginSearchRunner`. Measure with live
  smoke; if needed cap browser-fetched detail pages per search or remember
  CF-blocked domains per plugin (skip the doomed httpx request).
- RAM: bounded by the shared semaphore and a single Chromium process.

Tests:
- `HttpxPluginBase._fetch_text`: respx 403 + CF HTML → fetcher called;
  200 → fetcher not called; 403 non-CF → no fallback; fetcher `None` → `None`.
  Fetcher is an `AsyncMock` of the port.
- `StealthPool.fetch_text`: patched `async_playwright`, success/timeout/closed page.
- One CF-fallback test per migrated plugin.
- Composition: fetcher injected when enabled, not injected when disabled.

Acceptance: filmfans, kinoger, serienfans return results in live smoke;
no httpx plugin without Cloudflare changes behavior.

## Phase 3 — Optional: FlareSolverr/Byparr adapter

Only if plugins remain blocked after Phase 2.

- `FlareSolverrFetcher` implementing `BrowserFetcherPort` via the FlareSolverr
  v1 API (`POST /v1`, `cmd: request.get`); Byparr is API-compatible.
- Config `playwright.solver_url` (unset = disabled). Composition builds a
  chained fetcher: StealthPool first, solver as fallback.
- `curl_cffi` (TLS impersonation) only if solver latency becomes the bottleneck:
  reuse the solver's `cf_clearance` cookie + User-Agent for follow-up requests.
  This is a new dependency with its own client stack (no respx, no
  `RetryTransport`); needs a separate justification at that point.

## Documentation per phase

- `CHANGELOG.md` (+ `KNOWN_ISSUES` updates as plugins recover).
- `docs/features/configuration.md` (new config keys), `docs/features/hoster-resolvers.md`
  (StealthPool location, captcha hosters), `docs/features/FEATURES.md`,
  `docs/architecture/codeplan.md` (browser package).
- `docs/features/python-plugins.md`: base class notes (`_fetch_text`, no forced
  browser UA, no console events under Patchright).
- `CLAUDE.md`: overview line (Patchright instead of Playwright) and the dev
  container note on the Chromium install.
- `docs/plans/plugin-repair.md`: mark the three Cloudflare plugins as covered here.

## Commit sequence

1. Phase 0 results recorded in this file (docs only).
2. Swap to Patchright 1.57.x, remove playwright-stealth, rename `_stealth`.
3. Drop forced browser User-Agent.
4. Bump Patchright to current release (+ Dockerfile/devcontainer install).
5. Move browser pools into `infrastructure/browser/`, StealthPool on shared browser.
6. `BrowserFetcherPort` + `StealthPool.fetch_text` + `HttpxPluginBase._fetch_text` + wiring + config.
7. One commit per plugin: filmfans, kinoger, serienfans.
8. Optional: captcha hosters, Phase 3.
