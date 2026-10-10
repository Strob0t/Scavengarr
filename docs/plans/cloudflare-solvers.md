# Cloudflare solvers: FlareSolverr, trawl and Scavengarr's own stack

Analysis of 2026-10-09 (FlareSolverr `master` of 2026-09-27, v3.5.2; trawl `dev`, v1.8.0 of 2026-10-08; Scavengarr `staging` d040933). The source code of both repos was read as a tarball, the metadata through the GitHub API. Scavengarr paths without a prefix live under `src/scavengarr/`.

## Summary

FlareSolverr (Python, MIT) starts one Chrome per request through Selenium with a frozen undetected-chromedriver, recognises Cloudflare's challenge page by title and selectors, hits the Turnstile checkbox with TAB and SPACE and returns HTML, cookies (`cf_clearance`) and the User-Agent; it is maintained, but with 54 open issues, without captcha solving and with status always 200. trawl (TypeScript on Bun, AGPL-3.0, three months old, one main author) is a FlareSolverr-compatible replacement on Camoufox Firefox with a four-tier escalation (HTTP fetch, cached session, fresh solve, residential proxy), a Redis clearance cache, a Turnstile click through the shadow DOM, a MITM proxy and natively built arm64 images. Scavengarr already has both in its own form: Patchright Chromium headful under Xvfb with a Turnstile click, a clearance store across restarts, the adoption of the browser session into httpx and a wired but never tested (against a running sidecar) `/v1` solver fallback (profile `solver` with Byparr in `docker-compose.yml`). Recommendation: no new mandatory service, but the existing `SolverFetcher` optionally against trawl (or FlareSolverr) as a sidecar for the cases in which the own browser fails on the Pi, and before that closing the five gaps in the own stack (503 challenge retries, `_safe_fetch` without fallback, bot User-Agent in the domain check, link validation and health prober, no challenge count in the plugin history, no negative memo after a failed solve). Official APIs replace the detour only where the repo already uses them (moflix, filmfans, serienfans, einschalten, nox, cine, DataApi); the forums' RSS feeds offer no search; nobody reads `robots.txt`, and a moderate rate limit exists already (AIMD 5 rps per domain, retry backoff, circuit breaker).

## Part 1: FlareSolverr

Repo: https://github.com/FlareSolverr/FlareSolverr. Paths relative to the repo.

### Architecture and the path of a request

- HTTP server Bottle 0.13.4 (`JSONErrorBottle` in `src/flaresolverr.py`: `index()` GET `/`, `health()` GET `/health`, `controller_v1()` POST `/v1`), served through waitress 3.0.2 (`WaitressServerPoll`, lines 149-153), because Bottle's default cannot serve parallel requests. Bottle plugins in `src/bottle_plugins/`: access log, errors to `{"error": ...}` with HTTP 500, Prometheus counters per domain.
- Service layer `src/flaresolverr_service.py`: `controller_v1_endpoint` → `_controller_v1_handler` (dispatch by `cmd`) → `_cmd_request_get` / `_cmd_request_post` / `_cmd_sessions_*` → `_resolve_challenge` (browser from the session or a new one, `func_timeout(maxTimeout)`) → `_evil_logic` (detection, wait loop, result) with `click_verify`, `_resolve_turnstile_captcha`, `_get_turnstile_token`, `_post_request`.
- Browser creation `src/utils.py:get_webdriver(proxy)`: `uc.Chrome(...)` from the bundled copy `src/undetected_chromedriver/` (3.5.5, fork of ultrafunkamsterdam) through Selenium 4.47.0; `start_xvfb_display()` via xvfbwrapper; `create_proxy_extension` for proxy auth.
- Session store `src/sessions.py:SessionsStorage`: in-memory dict `session_id → Session(driver, created_at)`, no lock (issue #1752: browser leak on parallel `sessions.create`), no reaper.
- The path of a `POST /v1` with `cmd: request.get`: `controller_v1()` reads the JSON and applies the ENV proxy (lines 52-68) → `controller_v1_endpoint` logs the whole body at INFO (line 99, issue #1753: proxy password in the log) → `_controller_v1_handler` (`maxTimeout` default 60000 ms) → `_cmd_request_get` → `_resolve_challenge`: get a browser, `_evil_logic` in a thread under `func_timeout`; a timeout becomes "Error solving the challenge. Timeout after N seconds." → `_evil_logic`: optionally block media via CDP, `driver.get(url)`, access-denied check, challenge detection, wait loop, result → answer as `__dict__`; a browser without a session is `quit()` in the `finally`.

### Approach against the challenge

- Chrome/Chromium through Selenium plus a patched chromedriver: `Patcher.patch_exe` (`src/undetected_chromedriver/patcher.py` lines 416-444) replaces the `cdc_` block, the classic Selenium marker. Since v3.0.0 (2023-01) instead of Firefox/Puppeteer/Node.
- Headless default `HEADLESS=true`: on Linux Xvfb plus `--headless=new` plus a CDP script that sets `navigator.webdriver` to `false` (`__init__.py` lines 399-410, 496-520). The User-Agent is read from the browser once, "Headless" removed, and set at every start via `--user-agent=` (`src/utils.py` lines 164-165, 329-331). Flags: `--no-sandbox`, `--window-size=1920,1080`, `--disable-dev-shm-usage`, `--no-zygote`, `--disable-gpu-sandbox` only on ARM, `--accept-lang=$LANG`, the proxy as `--proxy-server` or an MV3 extension with auth.
- Detection in `_evil_logic` (lines 399-428, lists lines 25-54): access denied through `ACCESS_DENIED_TITLES` ("Access denied", "Attention Required! | Cloudflare") and `ACCESS_DENIED_SELECTORS`; a challenge through `CHALLENGE_TITLES` ("Just a moment...", "DDoS-Guard") and `CHALLENGE_SELECTORS` (`#cf-challenge-running`, `.ray_id`, `#cf-please-wait`, `#challenge-spinner`, `#turnstile-wrapper`, `.lds-ring`, `td.info #js_info`, `div.vc div.text-box h2`); `TURNSTILE_SELECTORS` = `input[name='cf-turnstile-response']`. The HTTP status is not checked (Selenium does not provide it).
- Waiting (lines 430-467): per attempt `BROWSER_WAIT_TIMEOUT` (1 s) with `WebDriverWait.until_not(title_is)` and `until_not(presence_of_element_located)`; on timeout `click_verify` (ActionChains: TAB `num_tabs` times, then SPACE, plus a click on the "Verify you are human" button by XPath) and again. The loop is unbounded, only `maxTimeout` ends it. Standalone Turnstile since v3.5.0 through `tabs_till_verify` (`_get_turnstile_token`: an endless loop until the hidden input changes).

### Return value and sessions

- `solution`: `url`, `status` always 200, `headers` always `{}` (both "todo: fix"), `response` = `page_source` (omitted with `returnOnlyCookies`), `cookies` in Selenium format with `cf_clearance`, `userAgent`, optionally `screenshot` and `turnstile_token`. Envelope: `status` ok/error, `message` ("Challenge solved!", "Challenge not detected!", "Error: ..."), timestamps, `version`. README line 210: the clearance cookie is valid only together with FlareSolverr's User-Agent (and the same IP).
- Sessions: `sessions.create` keeps a browser open (ID passed or `uuid1`), `request.get` with `session` and `session_ttl_minutes` renews it by destroy plus create; `proxy` only at creation; no limit, no reaper, sessions live until `destroy` or the end of the process. `maxTimeout` covers navigation, the wait loop and Turnstile, not the browser start.

### Limits and maintenance state

- Captchas: README lines 337-345 "At this time none of the captcha solvers work"; the code has neither `CAPTCHA_SOLVER` nor `/captcha` (only in `docker-compose.yml` and `flaresolverr.service`); hCaptcha only as an example page. Interactive Turnstile only through the keyboard trick without a success check. Access denied / IP ban → error with HTTP 500; an unsolved challenge = timeout (there is no message "Challenge not solved"). No real status codes and headers, POST only urlencoded, no authentication, request body in the log.
- Maintenance (GitHub API 2026-10-09): 15,820 stars, releases v3.5.2 (2026-09-12), v3.5.0 (2026-05-26), v3.4.6 (2025-11-29); last commit 2026-09-27 (SECURITY.md); 24 commits since 2026-01-01; maintainers ngosang (241) and ilike2burnthing (119), then single contributions; 54 open issues, 44 open PRs; the newest reports (July to September 2026: #1791, #1750 to #1754, #1743, #1719) are unanswered. The undetected-chromedriver is frozen as a copy and patched locally.

### Resource needs

- No numbers in README or code; README lines 23-24: "Web browsers consume a lot of memory ... With each request a new browser is launched." Without a session one Chrome start with a temporary profile per request, a test browser at process start; the histogram buckets 0/10/25/50 s (`src/metrics.py`) show the expected latency. No browser semaphore, only the waitress threads.
- ARM: images for linux/386, amd64, arm/v7, arm64/v8 (`.github/workflows/release-docker.yml`), base `python:3.11-slim-bookworm` with Chromium from Debian apt (not pinned), `--disable-gpu-sandbox` on ARM, `dumb-init`, ports 8191 and 8192 (Prometheus). ENVs: `LOG_LEVEL`, `LOG_HTML`, `PROXY_URL`/`_USERNAME`/`_PASSWORD`, `LANG`, `HEADLESS`, `DISABLE_MEDIA`, `BROWSER_WAIT_TIMEOUT`, `PORT`, `HOST`, `PROMETHEUS_ENABLED`/`_PORT`. `TZ`, `TEST_URL` and `CAPTCHA_SOLVER` appear in the README, not in the code.
- Licence MIT (`LICENSE`); Python ≥ 3.9 according to the check, in fact ≥ 3.10 (`str | None` in `src/dtos.py`); dependencies bottle, waitress, selenium, func-timeout, prometheus-client, requests, xvfbwrapper.

## Part 1: trawl

Repo: https://github.com/germondai/trawl (docs https://docs.trawl.germondai.com). Paths relative to the repo. Not a fork; the code names Byparr as its model (`packages/tiers/src/utils/challengeWait.ts` line 1).

### Architecture and the path of a request

- TypeScript on Bun 1.4.2 with Elysia; monorepo `apps/api` (server), `apps/web`, `apps/docs`, `packages/browser` (pool, fingerprints, session cache), `packages/tiers` (orchestrator, tiers, detection, solvers), `packages/types`. `apps/api/src/index.ts` → `createApiApp()` (`app.ts`), port 8191; routes `/` (FlareSolverr-like status message), `/health`, `/stats`, `/dashboard`, `POST /v1`, `POST /scrape`, `/sessions`, `GET /proxy-ca.crt`, optionally `/mcp` (tools scrape, read, extract, screenshot, inspect); MITM proxy on 8192 (`proxy/server.ts`, own CA, tier 0 forwarding, challenge cache 5 min per domain).
- `/v1` (`apps/api/src/routes/v1.ts`, `adapters/flaresolverr.ts`): JSON like FlareSolverr v2 with `request.get`, `request.post`, `sessions.create/list/destroy`; `maxTimeout` default 60000; answer `{status, message, startTimestamp, endTimestamp, version:"2.0.0", solution:{url, status, headers:{}, response, cookies, userAgent}}`; errors `status:"error"`, HTTP 400 on validation, 429 with an exhausted pool, 503 during initialisation.
- Orchestrator `packages/tiers/src/orchestrator.ts`: `scrape()` → `RequestBudget(maxTimeout)` → `scrapeWithinBudget()`: tier 1 `tiers/1.ts` Bun's `fetch()` with Firefox headers (`success`, `needs-js`, `blocked`, `error`) → `BrowserPool.acquire` (`packages/browser/src/pool.ts`, prefers a browser with `lastDomain === domain`) → tier 2 `tiers/2.ts` replay of the cached session in the shared context → tier 3 `tiers/browser.ts` fresh context, `routeChallengeWait`, `solvePageCaptchas`, `persistentBrowserWall`, success stores the session → tier 4 the same with a residential proxy (`RESIDENTIAL_PROXY_URL` or `proxy` per request), otherwise an error.

### Approach against the challenge

- Camoufox, a Firefox 152.0.4-beta.30 patched at the C++/Juggler level, through `camoufox-js` 0.12.1 and `playwright-core` 1.62.1 (`packages/browser/package.json`); no Chromium, no Selenium. Headless by default (`pool.ts` line 276, "virtual" = Xvfb only for the optional DataDome headful pool). Launch with `os` per fingerprint, a randomised screen, `geoip`, `humanize`, `block_webrtc`, uBlock Origin; the only own init script patches `attachShadow` (closed shadow roots as `shadowRootUnl`). The standard image has Linux fingerprints only (`CAMOUFOX_KEEP_SPOOFED_OS_FONTS=0`).
- Detection `packages/tiers/src/utils/detect.ts:isCloudflarePage` (lines 42-80): header `cf-mitigated: challenge`, title regex "just a moment|please wait|checking|attention required", texts, DOM ids `challenge-running`, `turnstile-wrapper`, `challenge-form`, marker `_cf_chl_opt`, block page `cf-error-details`, slim stub with `__CF$cv$params`; `isBlocked` evaluates 403 and 429; `detectChallengeType` also knows imperva, akamai, ddos-guard, aws-waf, datadome, hcaptcha, recaptcha, altcha and others.
- Waiting `utils/challengeWait.ts:waitForChallengeResolution`: a poll every 300 ms over title, DOM, URL and challenge frames; two inactive samples count as solved; `cf_clearance` for the target host is watched, after 5 s without a redirect it navigates itself; `/cdn-cgi/error/`, 1020, 1015 → "ip-blocked". `attemptTurnstileClick` every 3 s in four stages: the checkbox in the shadow DOM, frame selectors, a coordinate click on the iframe, Tab and Space. Embedded widgets through `solvers/turnstile.ts:solveTurnstile` (25 s). Timeouts: budget 60 s, `goto` 30 s, browser acquire 15 s → 429, launch 90 s.
- After the wait `persistentBrowserWall` inspects the document; the results "cloudflare-persistent", "cloudflare-challenge-timeout", "datacenter-ip-blocked" escalate only to tier 4.

### Return value and sessions

- `ScrapeResult` (`packages/types/src/index.ts`): `url`, `html`, `cookies[]` (all cookies of the context, `cf_clearance` included), `userAgent` from `navigator.userAgent`, `statusCode`, `tier`, `sessionCached`, `timings`, `body` as raw bytes, `responseHeaders`, optionally screenshot, consoleLogs, networkLogs, redirectChain. In `/v1` url, status, response, cookies and userAgent arrive; `headers` stays empty.
- Clearance cache `session:{hostname}` with `{cookies, userAgent, savedAt}` in Redis (`packages/browser/src/session.ts`, `REDIS_SESSION_TTL_SECONDS` 3600) or a process-local LRU (1000 entries); stored after tier 3 and 4 without an explicit proxy; tier 2 replays in the shared pool context with the UA only as a header. Named browser sessions (`browserSessions.ts`): an own persistent context per ID, capacity 4, idle TTL 3600 s, proxy fixed, process-local only.

### Limits and maintenance state

- When the Cloudflare wall stands or answers 1020/1015, only tier 4 with a residential proxy helps; without `RESIDENTIAL_PROXY_URL` the request ends in an error. hCaptcha image grids, DataDome sliders, Imperva captchas, DuckDuckGo and AWS WAF captchas are unsolved; reCAPTCHA v2 through audio and speech-to-text; the paid fallback `CAPTCHA_SOLVER=2captcha` is "unverified" (CHANGELOG 1.8.0). Tier 1 sends Firefox headers with Bun's TLS signature (issue #213); issue #212: tier 1 requests zstd since 1.8.0 and passes it raw through the MITM proxy to .NET clients such as Jackett and Prowlarr. `/v1`: `headers` empty, imported cookies force a fresh context.
- Maintenance: repo since 2026-06-26, v1.8.0 of 2026-10-08, 25 releases, 582 commits, 962 stars, 17 contributors, but 526 of 582 commits by germondai (bus factor 1), 2 open issues, 0 open PRs, daily nightlies.

### Resource needs

| Item | Value | Source |
|---|---|---|
| RAM per Camoufox instance | 350 to 500 MB under load | `apps/docs/architecture/browser-pool.md` line 98 |
| Pool 1 / 3 / 5 | ~400 MB / ~1.2 GB / ~2 GB; host 1 / 2 / 3 GB recommended | `browser-pool.md` lines 100-104 |
| Compose | `shm_size: 1gb` mandatory, prod `mem_limit: 3g` | `docker-compose.yml`, `docker-compose.prod.yml` |
| First start | 15 to 30 s, healthcheck `start_period` 90 s | README, `apps/docs/getting-started/quick-start.md` line 38 |
| CPU | no numbers; `:latest` needs AVX2 on x86, `:baseline` does not | README "Docker images" |
| ARM | arm64 built natively (`publish.yml` on ubuntu-24.04-arm), Camoufox `lin.arm64` with a SHA256 pin, fingerprint "Linux armv8" | `apps/api/Dockerfile`, `packages/browser/src/fingerprint.ts` |

Licence AGPL-3.0 (`LICENSE`). As an unmodified, separately operated service over HTTP it does not touch Scavengarr's code; changes to trawl's code that run as a network service trigger the source obligation for trawl.

## Part 2: Comparison

| | FlareSolverr 3.5.2 | trawl 1.8.0 |
|---|---|---|
| Approach | Chrome/Chromium through Selenium with a patched chromedriver, Xvfb plus `--headless=new`, one browser per request (or session), Turnstile via TAB+SPACE | Camoufox Firefox (C++ patches) through playwright-core, browser pool, four tiers (fetch, cached session, fresh solve, residential proxy), Turnstile click through shadow DOM, frame, coordinates, keyboard |
| Language, runtime | Python 3.11, Bottle/waitress | TypeScript, Bun 1.4.2, Elysia |
| API | `POST /v1` (request.get/post, sessions.*), `/health`, Prometheus on 8192 | `POST /v1` FlareSolverr-compatible, native `POST /scrape` (tier, timings, status, headers, bytes), `/sessions`, MCP, MITM proxy 8192, dashboard |
| Return value | HTML, cookies, UA; status always 200, headers empty | HTML, cookies, UA, in `/scrape` the real status and headers; in `/v1` headers empty |
| Session reuse | named browser sessions in memory, no reaper, TTL per request | clearance cache per host in Redis or LRU (TTL 1 h), named browser sessions (4, idle 1 h) |
| Performance | browser start per request (seconds), buckets 0/10/25/50 s, no concurrency limit | tier 1 without a browser, tier 2 from the cache; pool bounded, 429 when exhausted; first start 15-30 s |
| Reliability | wait loop bounded only by `maxTimeout`, no captcha solver, WAF block = error; issues about gray screens and page crashes | persistent wall only with a residential proxy, tier 1's TLS signature is Bun's, zstd problem with .NET clients (open) |
| Maintenance | since 2020, 15.8k stars, two maintainers, 54 open issues, new reports unanswered, last commit 2026-09-27 | since 2026-06, 962 stars, 25 releases in three months, one main author, 2 open issues |
| Resources, ARM | no numbers; images 386/amd64/arm/v7/arm64 | 350-500 MB per browser, `shm_size 1g`; arm64 native |
| Licence | MIT | AGPL-3.0 |

## Part 3: Use for Scavengarr

### Where HTTP requests can meet Cloudflare

Scavengarr has a stack of its own; the places and their protection today:

- **httpx plugins** (`infrastructure/plugins/httpx_base.py`): a shared client with SSRF guard, `RetryTransport` and `DomainRateLimiter` (`interfaces/composition.py:287-338`). Only `_fetch_text()` detects challenges (from status 400, `infrastructure/captcha/detect.py:24-66`: 403/503 plus "Just a moment", "challenge-platform", "cf-error-details", "Attention Required", "cf-turnstile") and falls back to the browser (`httpx_base.py:418-435`); a host with a challenge goes straight to the browser for 30 min (`:47,96-99`); after the solve httpx adopts the browser's cookies and UA (`:479-509,590-604`), a second challenge within 5 min counts as a rejected session (`:50,459-466`). `_safe_fetch()` has no detection and no fallback (`:318-370`); jjs sits behind Cloudflare according to its docstring and uses `_safe_fetch` only (`plugins/jjs.py:4,262,304`). The domain check `HEAD /` runs with the bot User-Agent `Scavengarr/<version>` (`:231`, `infrastructure/version.py:26`).
- **Playwright plugins** (`infrastructure/plugins/playwright_base.py`): Patchright instead of Playwright, one shared Chromium (`infrastructure/browser/shared_browser.py:66-98`), headful under Xvfb in the prod image (`Dockerfile.prod:67,109`, `docker/entrypoint.sh:22-34`); `_wait_for_cloudflare` → `solve_cloudflare` with 30 s (`:104,343-352`, `infrastructure/browser/turnstile.py:26-74`: titles "just a moment", "attention required", "nur einen moment"; the checkbox in the `challenges.cloudflare.com` frame after 3 s, then every 8 s); clearance cookies are stored and restored in the `ClearanceStore` (`infrastructure/browser/clearance_store.py:27-60,103-124`, up to 24 h, in the CachePort, survives restarts).
- **Stealth pool** (`infrastructure/browser/stealth_pool.py`): `fetch_text`, `capture_media`, `resolve_redirect`, `click_through` (the s.to gate with Turnstile and ALTCHA, `:516-588`), pages through `PageGate`/`PageBudget` (`page_gate.py:1-40`).
- **Hoster resolvers** (`infrastructure/hoster_resolvers/`): only SuperVideo, DoodStream, Filemoon, Vixeo and the XFS resolvers get the stealth pool (`composition.py:569-617`); no resolver uses the solver; the User-Agents differ (Chrome 131 in firestream, playmate, mixdrop, veev, vinovo; a truncated UA without the Chrome token in filemoon, xfs, doodstream; the bot UA in generic_ddl, rapidgator, ddownload, gofile, mediafire, streamtape).
- **Link validation** (`infrastructure/validation/http_link_validator.py:159-215`, Torznab only): HEAD/GET without own headers, no detection; a Cloudflare 403 makes the link invalid for 15 min.
- **HLS proxy** (`infrastructure/stremio/hls_proxy.py:161-167`): Chrome UA plus the stored stream headers; no challenge handling, CDN errors become 502.
- **Metadata clients** on the shared client with the bot UA, without fallback: the Anime Kitsu addon (behind Cloudflare, accepts the Scavengarr UA, `infrastructure/anime/kitsu_addon.py:7-9,26`), Fribb's list, Cinemeta (`infrastructure/stremio/cinemeta.py`), IMDb Suggest and Wikidata (`infrastructure/tmdb/imdb_fallback.py`), TMDB.
- **Sites with evidence**: Turnstile before the search at ddlspot, ddlvalley, scnsrc, mygully (Playwright); boerse JS challenge with a dead origin; filmfans and serienfans `/external` links behind Cloudflare; kinoger Cloudflare plus HostAdmin WAF, in production every page through the browser and 13 of 13 searches in timeout (`CHANGELOG.md:1709`); the moflix API challenge for VPN IPs; byte; the sto Turnstile/ALTCHA gate before the link-outs with a quota; the devideosrc player (hdfilme, streamcloud, streamkiste) behind the Cloudflare cache; kinox, megakino_to and movie4k Cloudflare 522 (dead origins); animeloads and cine DDoS-Guard. Health probes counted 91 challenges in 48 h, all kinoger (`docs/plans/plugin-domains.md:95-97`).
- **Existing solver**: profile `solver` in `docker-compose.yml:80-84` with `ghcr.io/thephaseless/byparr:latest`, enabled through `SCAVENGARR_PLAYWRIGHT_SOLVER_URL` (`:44-45`); `SolverFetcher` (`infrastructure/browser/solver_fetcher.py:49-80`) sends `POST {solver_url}/v1` with `cmd: request.get`, `url`, `maxTimeout`, takes `solution.response`, `cookies`, `userAgent`, remembers cookies and UA per host in memory (`:82-100`); `ChainedBrowserFetcher` asks the own stealth pool first, then the solver (`composition.py:245-270`), for httpx plugins only (`:555-564`); the solver does not support `click_through`. Status according to the docs: "not verified against a running sidecar" (`docs/plans/antibot-patchright.md:5,194-200`, `docs/plans/captcha-solving.md:5`); a sidecar does not change the reputation of the exit address and costs the same Chromium CPU (`docs/plans/optimization-options.md:59,86`); `cf_clearance` is bound to IP and UA (`antibot-patchright.md:226-229`).

### Recommendation

1. **No new mandatory service, no library.** What FlareSolverr and trawl do, the own stack does in-process already: Patchright Chromium with a Turnstile click, a clearance store across restarts, session adoption into httpx, a stealth pool for the resolvers and the s.to gate. An embedded library would bring a second browser into the same process; the Playwright plugins and the resolvers could not use a sidecar because they need the page itself (clicks, capture).
2. **The sidecar stays the optional fallback it already is, with trawl as the best candidate.** The `/v1` interface is the same for both, the `SolverFetcher` needs no new code for the switch. trawl fits better than FlareSolverr: a second engine (Firefox instead of Chromium) helps exactly when Cloudflare flags Chromium automation; the clearance cache lives in Redis, which Scavengarr already has; arm64 is built natively; tiers 1 and 2 answer without a browser when a session exists. Against trawl: AGPL (without consequence for unmodified operation), one main author, and 400 MB plus `shm 1g` on the Pi. FlareSolverr is the conservative choice (MIT, older, more widely used), but starts one Chrome per request and delivers neither status nor headers. Byparr, today's profile service, is trawl's model and was never tested here against a running sidecar.
3. **First close the gaps in the own stack; they cost no service:** (a) `RetryTransport` retries a 503 challenge three times and halves the domain rate while doing so (`infrastructure/common/retry_transport.py:86-91`): do not retry an answer with `cf-mitigated: challenge` or a challenge page; (b) give `_safe_fetch()` the detection and the browser memo of `_fetch_text()` (`httpx_base.py:318-370`), which gives jjs and the `_safe_fetch` plugins the fallback; (c) send the domain check, the health prober (`infrastructure/scoring/health_prober.py:21-22`) and the link validation with the plugin UA instead of the bot UA and unify the resolver UAs on `DEFAULT_USER_AGENT` (`infrastructure/plugins/constants.py:13-17`); (d) count challenges and solver outcomes in `PluginHistory` (`domain/ports/plugin_history.py:15-17`) and show them in the digest, so that the decision about a sidecar stands on numbers instead of 91 log lines counted by hand; (e) the `SolverFetcher` stores its sessions in the `ClearanceStore` instead of in memory and gets a negative memo after "blocked", so that a failed solve does not occupy the sidecar anew every 30 s. The contradiction between scoring (challenge = health 0, `infrastructure/scoring/ewma.py:87-91`) and the health monitor (challenge = site alive, `infrastructure/plugins/health_monitor.py:42-49`) belongs to it. State in the backlog (`docs/plans/ideas-backlog.md`, idea N19): rows 43 and 44 closed (a) and (b) (d7ffd7e, 2026-10-09) and (c) (dae332d, 2026-10-10); row 50 closed (d) (2026-10-10: `challenges` in `PluginHistory`, a column and the challenged plugins in the digest; solver outcomes wait for the sidecar) and row 51 carries the two settings that are loaded but never read (`playwright.timeout_ms`, `stremio.probe_stealth_timeout_seconds`); (e) belongs to the sidecar decision (idea N20).
4. **Official APIs and RSS.** Where a site has an API, the repo uses it already: DataApi `/data/browse` and `/data/watch` (megakino_to, movie4k), moflix `/api/v1/search`, filmfans and serienfans `/api/v2/search`, serienjunkies `/api/media/<id>/releases`, einschalten `/api/search`, nox `/api/search` with ALTCHA, cine `/request/*`, the fireani RPC, aniworld `/ajax/search`, WordPress REST at haschcon and hdworld, devideosrc `/api/embed-links`. The vBulletin and XenForo forums (boerse, mygully, myboerse, dataload) offer RSS for new threads (`external.php?type=RSS2`, `index.rss`) but no search, and boerse's origin is dead; a feed does not replace the search query. The Cloudflare-protected streaming sites have no official API.

### Integration in concrete terms

Files that change (with the decision for the sidecar):

- The Pi's compose file (the file contains secrets and is not reproduced here) and `docker-compose.yml` in the repo: the service next to Scavengarr, Scavengarr gets `SCAVENGARR_PLAYWRIGHT_SOLVER_URL`.
- `infrastructure/browser/solver_fetcher.py`: sessions through the `ClearanceStore`, a `session` per host, the real status from trawl, a negative memo, the target host in the rate limiter, a telemetry stage `solver` with the outcomes `solved`, `blocked`, `busy`, `unreachable`.
- `infrastructure/common/retry_transport.py`, `infrastructure/plugins/httpx_base.py` (`_safe_fetch`, domain check headers), `infrastructure/scoring/health_prober.py`, `domain/ports/plugin_history.py` with `infrastructure/plugins/plugin_history.py` and `scripts/digest.py`: the gaps (a) to (e).
- `docs/features/configuration.md` (solver section), `docs/plans/antibot-patchright.md` (phase 3 verified), `CHANGELOG.md`.

Compose sketch (variable names from trawl's `.env.example` and `docker-compose.yml`):

```yaml
  trawl:
    image: ghcr.io/germondai/trawl:latest   # arm64 native; :baseline for CPUs without AVX2 or older kernels
    shm_size: 1gb
    mem_limit: 1g
    environment:
      BROWSER_POOL_SIZE: "1"
      REDIS_URL: redis://redis:6379         # the clearance cache survives restarts; omit = LRU in memory
      REDIS_SESSION_TTL_SECONDS: "3600"
      LOG_LEVEL: info
    profiles: [solver]
  scavengarr:
    environment:
      SCAVENGARR_PLAYWRIGHT_SOLVER_URL: http://trawl:8191
      SCAVENGARR_PLAYWRIGHT_BROWSER_FALLBACK: "true"   # own browser first; "false" only for a comparison run
```

The call, session reuse, error handling and fallback as a sketch for `SolverFetcher` (the existing chain stays: own stealth pool, then solver, then empty):

```python
async def fetch_text(self, url: str, *, budget_ms: int) -> SolverPage | None:
    host = urlsplit(url).hostname or ""
    if self._blocked_until.get(host, 0.0) > time.monotonic():   # negative memo after "blocked"
        return None
    async with self._slots:                                     # semaphore 1 to 2: the sidecar has one browser
        await self._limiter.acquire(host)                       # the target host's rate limit, not the solver's
        body = {"cmd": "request.get", "url": url, "maxTimeout": budget_ms,
                "session": host, "session_ttl_minutes": 60}     # a browser session per host, with both services
        try:
            resp = await self._client.post(f"{self._url}/v1", json=body, timeout=budget_ms / 1000 + 10)
        except httpx.HTTPError as exc:
            log.warning("solver_unreachable", host=host, error=type(exc).__name__)
            return None                                         # the plugin reports empty, the history counts it
    if resp.status_code == 429:                                 # trawl: pool exhausted; no retry within the request
        log.info("solver_busy", host=host)
        return None
    data = resp.json()
    if data.get("status") != "ok":
        reason = str(data.get("message", ""))[:80]
        if any(w in reason for w in ("blocked", "persistent", "banned")):
            self._blocked_until[host] = time.monotonic() + 1800  # 30 min like the browser memo
        log.warning("solver_failed", host=host, reason=reason)
        return None
    sol = data["solution"]
    if int(sol.get("status", 200)) >= 400:                      # trawl delivers real status codes, FlareSolverr always 200
        return None
    await self._clearance.save(host, sol.get("cookies", []), sol.get("userAgent"))   # ClearanceStore instead of a dict
    return SolverPage(sol["response"], sol.get("cookies", []), sol.get("userAgent"))
```

`HttpxPluginBase._adopt_browser_session` then adopts cookies and UA into the shared client's cookie jar as today (`httpx_base.py:498-509`); the clearance is valid only with the sidecar's UA and from the same address, so the sidecar must have the same egress as Scavengarr.

Frame: a moderate rate limit exists (AIMD 5 rps per domain with burst 10, halving on 429/503, `infrastructure/common/rate_limiter.py:1-25,88-93`; retry backoff 1/2/4 s with `Retry-After`; circuit breaker 5 failures, 60 s to 1 h; plugin semaphores 5), only `record_timeout` is called by nobody (`rate_limiter.py:98-105`). `robots.txt` is read nowhere; the protected sites usually forbid automated access entirely, honouring it would mean switching those plugins off, which is the operator's decision. The terms of use of the hosters and sites mostly forbid automated retrieval as well; operation stays limited to content the operator may retrieve.

### Trade-offs

- **Latency:** the own browser needs 16 to 70 s for Turnstile on the Pi (`docs/plans/stremio-latency.md:138`); a sidecar comes after the own attempt, so on top, unless `browser_fallback=false`. trawl answers with a cached session without a browser (tiers 1 and 2), FlareSolverr starts one Chrome per request. The Stremio budget per request stays the limit; a solve that exceeds it only helps the next request.
- **Resources:** trawl 350 to 500 MB per browser plus `shm 1g`, FlareSolverr one Chrome per request; both burn the same Pi CPU as Patchright (`optimization-options.md:59,86`). With a pool of 1, trawl costs about as much as a second Scavengarr Chromium.
- **Stability against Cloudflare changes:** all three stacks break with the same detection change; FlareSolverr reacts slowly (two maintainers, new issues unanswered), trawl quickly but with one author; the own stack depends on Patchright. A second engine (Firefox) is the only real diversification.
- **Address and reputation:** none of the services changes that the exit address (VPN) triggers the challenges; `cf_clearance` is bound to address and UA, the sidecar must go through the same egress. A residential proxy (trawl's tier 4) would be a paid route that is not recommended here.
- **Security:** both services without authentication, reachable only within the compose network; FlareSolverr logs request bodies.

## Sources

- FlareSolverr: `src/flaresolverr.py`, `src/flaresolverr_service.py`, `src/sessions.py`, `src/dtos.py`, `src/utils.py`, `src/metrics.py`, `src/undetected_chromedriver/{__init__,patcher}.py`, `Dockerfile`, `docker-compose.yml`, `requirements.txt`, `README.md`, `CHANGELOG.md`, `LICENSE`, `.github/workflows/release-docker.yml`; issues #128, #166, #737, #1599, #1719, #1743, #1750 to #1754, #1791; https://github.com/FlareSolverr/FlareSolverr
- trawl: `README.md`, `CHANGELOG.md`, `LICENSE`, `.env.example`, `docker-compose.yml`, `apps/api/Dockerfile`, `.github/workflows/publish*.yml`, `apps/api/src/{index,app,deps}.ts`, `apps/api/src/routes/{v1,scrape,sessions,mcp}.ts`, `apps/api/src/adapters/flaresolverr.ts`, `apps/api/src/proxy/server.ts`, `packages/browser/src/{pool,fingerprint,session,memory-session}.ts`, `packages/tiers/src/orchestrator.ts`, `packages/tiers/src/tiers/{1,2,3,4,browser}.ts`, `packages/tiers/src/utils/{detect,challengeWait,challengeRouter,browserWall}.ts`, `packages/tiers/src/solvers/{turnstile,hcaptcha,recaptcha,index}.ts`, `packages/types/src/index.ts`, `apps/docs/architecture/{tiered-execution,browser-pool}.md`, `apps/docs/deployment/{docker-compose,standalone,troubleshooting}.md`; issues #212, #213; https://github.com/germondai/trawl
- Scavengarr: the paths named in the text under `src/scavengarr/`, `plugins/`, `docker-compose.yml`, `Dockerfile.prod`, `docker/entrypoint.sh`, `docs/plans/antibot-patchright.md`, `docs/plans/captcha-solving.md`, `docs/plans/optimization-options.md`, `docs/plans/plugin-domains.md`, `docs/plans/stremio-latency.md`, `docs/features/configuration.md`, `docs/features/python-plugins.md`, `docs/features/hoster-resolvers.md`, `CHANGELOG.md` (KNOWN_ISSUES)
