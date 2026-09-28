[← Back to Index](../features/README.md)

# Plan: Anti-Bot Hardening (Patchright + Browser Fallback)

**Status:** Phase 0–2 done (2026-09-28): all six Cloudflare-blocked plugins return results again. Phase 3 (FlareSolverr/Byparr) not needed. Open: captcha hosters (veev, vinovo, wolfstream)
**Priority:** High (blocks 6 plugins and 3 hoster resolvers)
**Related:** `docs/plans/plugin-repair.md`, `CHANGELOG.md` → `KNOWN_ISSUES`, `src/scavengarr/infrastructure/plugins/{playwright_base,shared_browser,httpx_base}.py`, `src/scavengarr/infrastructure/hoster_resolvers/{stealth_pool,cloudflare,supervideo,probe,xfs}.py`, `src/scavengarr/interfaces/composition.py`

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
- `PlaywrightPluginBase` applies `Stealth()` and a fixed `user_agent=DEFAULT_USER_AGENT` in `_ensure_context()` and `isolated_search()`; `headless=True`.
- Two independent Chromium processes: `SharedBrowserPool` (Playwright plugins) and `StealthPool` (SuperVideo fallback + stealth probes in `probe.py`).
- httpx plugins have no browser fallback at all.

Why the current stealth fails (see research notes in the 2026-09-28 session): playwright-stealth only overrides JS properties after the fact. It leaves the CDP `Runtime.enable` / `Console.enable` side effects and the automation launch flags (`--enable-automation`, …) in place, which Cloudflare, DataDome etc. detect independently of any JS patch. The fixed User-Agent also disagrees with the real Chromium version exposed via client hints (`sec-ch-ua`).

## Goals / non-goals

Goals:
- Replace playwright + playwright-stealth with Patchright (drop-in fork that removes those leaks at the source).
- One browser capability that httpx plugins can use for individual Cloudflare-blocked pages, instead of rewriting them as Playwright plugins.
- Optional external challenge solver (FlareSolverr/Byparr) behind the same port.

Non-goals:
- Paid scraping APIs, residential proxies, CAPTCHA-solving services.
- Rewriting plugins to nodriver/SeleniumBase/Camoufox (only reconsidered if the Phase 0 gate fails).
- Fixing the non-Cloudflare plugin breakages (scnlog, hdfilme, fireani, nox, dataload, kinoking, streamcloud, streamkiste) — tracked in `docs/plans/plugin-repair.md`.

## Phase 0 — Measure first (spike, no production code)

A throwaway script (`tmp/antibot_bench.py`, not committed) loads each target once per variant and records: HTTP status, whether the CF title marker cleared, whether an expected content marker is present, time to clear, peak RSS.

Targets: filmfans, kinoger, serienfans detail page, ddlspot, ddlvalley, scnsrc, byte search pages, one known-good embed URL each for veev, vinovo, wolfstream.

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

Setup: devcontainer, residential IP (AS3209 Vodafone), 2 rounds per variant, 30 s wait per page. playwright 1.57.0 (Chromium 143) + playwright-stealth 2.0.1, Patchright 1.63.0 (Chromium 153), Camoufox 0.5.6 (Firefox 152), nodriver 0.50.3.

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
- The challenge is Cloudflare **managed** (`cType: 'managed'`) and renders an **interactive Turnstile checkbox** ("Verify you are human"); it never auto-clears.
- A real mouse click on the checkbox (verified by screenshot) is rejected: Cloudflare re-issues the challenge with a new Ray ID. Tried with Patchright (Chromium new headless), Camoufox (headless, `humanize=True`) and nodriver (`tab.verify_cf()`), 3 attempts each. All rejected.
- Not the IP: residential ISP address.

Conclusions:
- Patchright alone gives **no improvement** over today's stack on these targets. Fingerprint, User-Agent and automation-protocol leaks are not the deciding factor; every headless browser is rejected at the Turnstile step.
- **byte is not Cloudflare-blocked**: every variant gets HTTP 200 with search hits. Its 0 results are a parser problem → moved to `plugin-repair.md`.
- Veev/vinovo/wolfstream were not measured (no known-good embed URLs).
- Headful follow-up (Xvfb installed via `.devcontainer/setup.sh`), one run per target, Turnstile checkbox clicked once:

| Target | Patchright headful (Xvfb) | Playwright + stealth headful (Xvfb) |
|---|---|---|
| filmfans | cleared after 1 click ("FilmFans") | blocked after 3 clicks |
| kinoger | cleared after 1 click ("Website-Suche") | blocked after 3 clicks |
| serienfans (detail) | cleared after 1 click ("Breaking Bad \| SerienFans") | blocked after 3 clicks |
| ddlspot | cleared after 1 click ("Iron Man (30 Downloads)") | blocked after 3 clicks |
| ddlvalley | cleared after 1 click (search results) | blocked after 3 clicks |
| scnsrc | cleared after 1 click (search results) | blocked after 3 clicks |

- **Both are required**: Patchright *and* a headful browser. Headless Patchright fails, headful Playwright + stealth fails.
- The challenge does not auto-clear; a click on the Turnstile checkbox is needed. A Patchright locator click (`iframe[src*="challenges.cloudflare.com"]` frame → `input[type=checkbox]`, closed shadow root) works headful as well (kinoger cleared, `cf_clearance` set), so no coordinate clicking is needed.
- Measured wall time per page ≈ 21 s including fixed 6 s + 8 s benchmark waits; the real solve time is lower and has to be measured in Phase 2.
- Camoufox and nodriver were not re-run headful: Patchright passes and keeps the Playwright API, so they are not needed.

Decision: go ahead with Phase 1 and Phase 2, headful under Xvfb.

### Decisions (user, 2026-09-28)

- **Order**: anti-bot Phase 1 + 2 first, then the non-Cloudflare repairs from `plugin-repair.md`.
- **RAM budget**: production hosts have 1–4 GB free. Headful only when a display is available (Xvfb present, `DISPLAY` set); otherwise headless with a warning. Browser fallback for httpx plugins stays on by default, but browser fetches are capped at 2 concurrent pages (`min(stremio.max_concurrent_playwright, 2)`, no new knob). Phase 1 measures headful vs. headless RSS and records it here.
- **Captcha hosters** (veev, vinovo, wolfstream): after Phase 2, collect current embed URLs via working plugins and test them headful; flip `needs_captcha` per hoster only if they pass.

## Phase 1 — Patchright instead of playwright + playwright-stealth

Changes:
1. `pyproject.toml`: remove `playwright` and `playwright-stealth`, add `patchright`. First commit pins the matching upstream version (`patchright` 1.57.x) so the swap is the only behavioral change; a follow-up commit bumps to the current release (1.63.x at time of writing) for a current Chromium.
2. Imports `playwright.async_api` → `patchright.async_api` in `playwright_base.py`, `shared_browser.py`, `stealth_pool.py`, `context_vars.py`, plugins `animeloads`, `boerse`, `byte`, `moflix`, `mygully`, `streamworld`, and `tests/live/{conftest,test_plugin_smoke}.py`. Unit tests patch `scavengarr.…async_playwright`, so their patch targets stay valid in Phase 1 (the Phase 2 module move changes them, see Phase 2 tests).
3. Remove `Stealth().apply_stealth_async(...)` from `PlaywrightPluginBase._ensure_context()`, `PlaywrightPluginBase.isolated_search()` and `StealthPool._ensure_context()`. Patchright and playwright-stealth must not be combined. Keep the resource blocking routes. `_stealth` then only controls resource blocking → rename to `_block_resources`. ddlspot, ddlvalley and scnsrc set `_stealth = True`, which equals the default; drop those redundant lines when renaming. Note that `stealth_pool.py` already has a module-level route handler named `_block_resources`.
4. Stop forcing a User-Agent in browser contexts: `new_context()` no longer passes `user_agent=` by default (Patchright recommendation). A plugin may still opt in via an explicit override. httpx plugins keep `DEFAULT_USER_AGENT`.
5. Headful is required (Phase 0). Keep `playwright.headless` as the switch but change its default to `false`; the process needs a display: `Dockerfile.prod` installs `xvfb` and starts the app under `xvfb-run -a` (or an Xvfb entrypoint). Without `DISPLAY`, fall back to headless with a warning log instead of crashing. Measure and document the RAM cost of headful Chromium.
6. `Dockerfile.prod`: `python -m patchright install chromium`; verify the browser cache path (`/root/.cache/ms-playwright` today) and adjust the `COPY` line. `.devcontainer/setup.sh`: install the Patchright Chromium for the Python venv (the existing `npx @playwright/mcp` install is for the MCP server only).
7. XFS captcha hosters: flip `needs_captcha` for veev/vinovo/wolfstream only if Phase 0 showed they pass. They are resolved via httpx today, so passing requires routing them through the browser port (Phase 2), one commit per hoster.

Known Patchright limitation: the Console domain is disabled (`page.on("console")` never fires). No current code uses it; add a note to `playwright_base.py` so future plugins don't rely on it.

Tests:
- `test_stealth_pool.py` (52 stealth references): drop assertions on `Stealth`, keep lifecycle/probe/resource-blocking tests.
- `test_playwright_base.py`: add tests that no stealth is applied, no `user_agent` is passed by default, and an explicit override is still honored.
- Live: `poetry run pytest -m live -k "ddlspot or ddlvalley or scnsrc or byte"` plus all 9 Playwright plugins — must not regress vs. baseline.

Acceptance: offline suite + pre-commit green; no Playwright plugin regresses in live smoke; ddlspot, ddlvalley and scnsrc pass the challenge once the click step (Phase 2 design, shared helper) is in place; SuperVideo stealth fallback still resolves.

Rollback: revert the commit(s); dependency swap is self-contained.

### Phase 1 results (2026-09-28)

Done on `staging`: Patchright 1.57 swap (then ^1.63), `_stealth` → `_block_resources`, `_browser_user_agent`/`_context_options()` (no forced UA), `resolve_headless()` + headful default, Xvfb entrypoint in `Dockerfile.prod`, Patchright Chromium install in `setup.sh`. Side fix: the live Chromium check used the sync API inside the event loop, so Playwright smoke tests had always been skipped.

- Live smoke, 9 Playwright plugins: identical on the old stack, Patchright 1.57, Patchright 1.63 and Patchright 1.63 headful: animeloads, moflix pass; ddlspot, ddlvalley, scnsrc fail (Turnstile click missing → Phase 2); streamworld fails (not Cloudflare, see `plugin-repair.md`); boerse, byte network error; myboerse, mygully no credentials.
- RAM (PSS of the Chromium process tree, 3 pages in one context): headless idle 230 MiB / loaded 451 MiB; headful idle 420 MiB / loaded 697 MiB; Xvfb ~70 MiB. With the 1–4 GB budget the second Chromium of `StealthPool` matters (worst case ~1.2–1.4 GB with both pools headful), so the pool consolidation below is a priority in Phase 2.
- Not verified: building `Dockerfile.prod` (no Docker in the devcontainer). `docker/entrypoint.sh` was smoke-tested directly (starts Xvfb `:99`, exports `DISPLAY`, execs the CLI).

## Phase 2 — Browser fallback port for httpx plugins

Design:

- **Port** `src/scavengarr/domain/ports/browser_fetcher.py`:

  ```python
  class BrowserFetcherPort(Protocol):
      async def fetch_text(self, url: str, *, timeout: float) -> str | None: ...
  ```

  Returns the rendered page body after the challenge cleared, `None` on failure. Two implementations are planned (StealthPool now, FlareSolverr in Phase 3), so the port is justified.

- **Browser package** `src/scavengarr/infrastructure/browser/`: move `SharedBrowserPool` (from `plugins/`), `StealthPool` (from `hoster_resolvers/`) and `cloudflare.py` here, keeping re-exports at the old paths only if needed for a transition commit. `StealthPool` obtains its browser from `SharedBrowserPool.warmup()` instead of launching a second Chromium → one browser process instead of two. Cloudflare handling gains a click step: wait briefly for auto-clear, then click the Turnstile checkbox via the challenge iframe locator (`iframe[src*="challenges.cloudflare.com"]` → `input[type=checkbox]`), then wait for the title to change; one shared helper used by `StealthPool` and `PlaywrightPluginBase._wait_for_cloudflare()` (the latter fixes ddlspot/ddlvalley/scnsrc). `StealthPool.fetch_text()` reuses the `goto` + this CF helper and is bounded by a semaphore of `min(stremio.max_concurrent_playwright, 2)` (RAM budget decision above; no new knob).

- **HttpxPluginBase**:
  - `set_browser_fetcher(fetcher: BrowserFetcherPort | None)` classmethod, wired in `composition.py` next to `set_shared_http_client()`.
  - `_fetch_text(url, *, context="") -> str | None`: httpx GET; if `is_cloudflare_challenge(status, body)` and a fetcher is set, retry via the fetcher; otherwise same logging/`None` semantics as `_safe_fetch()`.
  - Without a fetcher the behavior is exactly today's (graceful degradation).

- **Plugins**: filmfans, kinoger, serienfans switch their HTML page fetches from `client.get(...)` to `self._fetch_text(...)`. JSON API calls that still work over httpx (serienfans search API) stay on httpx.

- **Config**: `playwright.browser_fallback` (bool, default `true`; `AppConfig` field `playwright_browser_fallback` with an `AliasPath`, env override `SCAVENGARR_PLAYWRIGHT_BROWSER_FALLBACK`) so operators on small hosts can disable the extra Chromium load. When `false`, no fetcher is injected.

Out of scope for Phase 2: reusing `cf_clearance` cookies in httpx — the cookie is bound to the browser's TLS fingerprint and fails with httpx (see Phase 3).

Risks:
- Latency: a browser fetch costs seconds; detail-page fan-out (semaphore `_max_concurrent`, default 5) may exceed the plugin search timeout in `PluginSearchRunner`. Measure with live smoke; if needed cap browser-fetched detail pages per search or remember CF-blocked domains per plugin (skip the doomed httpx request).
- RAM: bounded by the shared semaphore and a single Chromium process.

Tests:
- `HttpxPluginBase._fetch_text`: respx 403 + CF HTML → fetcher called; 200 → fetcher not called; 403 non-CF → no fallback; fetcher `None` → `None`. Fetcher is an `AsyncMock` of the port.
- `StealthPool.fetch_text`: patched `async_playwright`, success/timeout/closed page.
- One CF-fallback test per migrated plugin.
- Update patch targets after the move: `scavengarr.infrastructure.hoster_resolvers.stealth_pool.{async_playwright,Stealth}` (`test_stealth_pool.py`) and `scavengarr.infrastructure.plugins.shared_browser.async_playwright` (`test_playwright_base.py`) → `scavengarr.infrastructure.browser.*`.
- Composition: fetcher injected when enabled, not injected when disabled.

Acceptance: filmfans, kinoger, serienfans return results in live smoke; no httpx plugin without Cloudflare changes behavior.

### Phase 2 results (2026-09-28)

Done on `staging`: pools in `infrastructure/browser/` with one Chromium; `turnstile.py` (`solve_cloudflare`, post-challenge settle, `read_when_settled`); `_passes_cloudflare()` (403/503 challenge no longer aborts navigation); `_fetch_page_html()` retry for transient statuses; `BrowserFetcherPort` with `StealthPool.fetch_text()` and `resolve_redirect()`; `HttpxPluginBase._fetch_text()`, `_resolve_redirect()`, `_resolve_result_links()`, Cloudflare host memo; config `playwright.browser_fallback`.

| Plugin | Live result (headful) | Was |
|---|---|---|
| ddlspot | 30 results for "Iron Man" in 8 s | 0 |
| ddlvalley | 250 results (full scrape) | 0 |
| scnsrc | 37 of 42 posts | 0 |
| filmfans | 13 releases, filecrypt links, ~140 s (429 backoff) | 0 |
| kinoger | 4 streams in 7 s | 0 |
| serienfans | 35 releases for "Breaking Bad", ~146 s (429 backoff) | 0 |

Findings beyond the plan:
- Several failures were not the challenge itself: `_navigate_and_wait()` aborted on the challenge's 403 before solving; ddlspot detail pages answer plain HTTP with an empty 200; scnsrc moved links to post pages; ddlvalley/scnsrc (nginx 503) and filmfans/serienfans (429) rate-limit bursts.
- filmfans/serienfans `/external/<hash>` links sit behind Cloudflare too, so they are resolved to filecrypt containers (Playwright routes do not see redirect hops; `request` events do).
- Limits: an uncached filmfans/serienfans search takes ~2–2.5 min and may exceed Prowlarr's request timeout; kinoger's first Stremio search after start can exceed the 15 s plugin timeout.

## Phase 3 — Optional: FlareSolverr/Byparr adapter

Only if plugins remain blocked after Phase 2.

- `FlareSolverrFetcher` implementing `BrowserFetcherPort` via the FlareSolverr v1 API (`POST /v1`, `cmd: request.get`); Byparr is API-compatible.
- Config `playwright.solver_url` (unset = disabled; `AppConfig` field `playwright_solver_url`, env `SCAVENGARR_PLAYWRIGHT_SOLVER_URL`). Composition builds a chained fetcher: StealthPool first, solver as fallback.
- `curl_cffi` (TLS impersonation) only if solver latency becomes the bottleneck: reuse the solver's `cf_clearance` cookie + User-Agent for follow-up requests. This is a new dependency with its own client stack (no respx, no `RetryTransport`); needs a separate justification at that point.

## Documentation per phase

- `CHANGELOG.md` (+ `KNOWN_ISSUES` updates as plugins recover).
- `docs/features/configuration.md` (new config keys), `docs/features/hoster-resolvers.md` (StealthPool location, captcha hosters), `docs/features/FEATURES.md`, `docs/architecture/codeplan.md` (browser package).
- `docs/features/python-plugins.md`: base class notes (`_fetch_text`, no forced browser UA, no console events under Patchright).
- `CLAUDE.md`: overview line (Patchright instead of Playwright) and the dev container note on the Chromium install.
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
