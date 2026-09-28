# Changelog

All notable changes to Scavengarr are documented in this file. Format: version, date, grouped changes. Newest entries first.

---

## Unreleased (staging)

Massive expansion of the plugin ecosystem (2 → 42 plugins), Stremio addon integration, 56 hoster resolvers, plugin base class standardization, search result caching, circuit breaker, global concurrency pool, graceful shutdown, multi-language search, and growth of the test suite from 160 to 4151 tests (4113 excluding the opt-in live tests).

### Fix: Bugs Found by the Docs Audit
- **Torznab: unknown plugin returns 404 everywhere**: `/torznab/{plugin}/health` and the `extended=1` test probe caught only `TorznabPluginNotFound`, but the registry raises `PluginNotFoundError`, so unknown plugins produced 500 (dev) / 200 (prod). Both exceptions are now mapped to 404. E2E tests now mock the real registry exception (the old mocks hid the bug).
- **Torznab: non-numeric `cat` returns 400**: `cat=abc` raised an unhandled `ValueError` (500). It now raises `TorznabBadRequest` → empty feed with HTTP 400.
- **Torznab caps: `limits default` is 100**: caps advertised `default="50"` while search defaults to `limit=100`; `TorznabCaps.limits_default` now matches.
- **Config: rate-limit/retry env vars work**: `SCAVENGARR_RATE_LIMIT_ADAPTIVE`, `_MIN_RPS`, `_MAX_RPS` and `SCAVENGARR_HTTP_RETRY_MAX_ATTEMPTS`, `_BACKOFF_BASE`, `_MAX_BACKOFF` were read by `EnvOverrides` but dropped by the loader (missing from its flat-key map); they now apply.
- **Config: cache backend via env**: new `SCAVENGARR_CACHE_BACKEND`, `SCAVENGARR_CACHE_REDIS_URL` and `SCAVENGARR_CACHE_MAX_CONCURRENT`. The unprefixed `CACHE_*` variables suggested by a comment were never read (the section is validated from the merged dict); the comment now says so.
- **CLI: `HOST`/`PORT` from `--dotenv` apply**: the CLI read them before `load_config()` loaded the `.env` file; they are now resolved afterwards (`--host`/`--port` still win). First CLI unit tests (`tests/unit/interfaces/test_cli.py`).
- **Config: one set of defaults**: Pydantic field defaults in `schema.py` disagreed with `defaults.py` (`scoring.enabled`, `stremio.max_concurrent_plugins`, `http.user_agent`, `cache.dir`). The schema now mirrors the effective values (no runtime change); a test keeps them in sync.
- **Scoring: alias domains count as supported**: `MiniSearchProber` classified links only against resolver names, so alias domains (`filelions` → Vidhide, `luluvdo` → Lulustream) lowered the supported-hoster ratio. New `HosterResolverRegistry.supported_domains` (names + aliases) is wired in instead. The docs' claim that background probes skip `provides="both"` plugins was wrong (`get_by_provides("stream")` includes them) and is removed.
- **Stremio `/health` lists supported hosters**: the endpoint called a non-existent `list_hosters()` and swallowed the error, so `supported_hosters` was always empty. It now reads `HosterResolverRegistry.supported_hosters`; the E2E test mock is spec'd against the real class.

### Chore: LF Line Endings and Versioned Claude Code Hooks
- **All text files normalized to LF**: `.gitattributes` widened from `*.md` to `* text=auto eol=lf` and the index renormalized (227 files, mostly `.py`; EOL-only, `git diff --ignore-cr-at-eol` is empty). New `mixed-line-ending --fix=lf` pre-commit hook catches CRLF before commit.
- **Claude Code hooks fixed and versioned**: `.claude/hooks/` and `.claude/commands/` are no longer gitignored. Both hooks had CRLF shebangs, failed with exit 127 and therefore never ran (Claude Code treats non-2 exits as non-blocking). `block-dangerous.sh` rewritten: one `jq` call, no `grep` subprocesses, per-command matching (no false hits across `&&`/`;`/`|`), and new blocks for `git push -f`/`+refspec`, pushes to `…:main`, commits/pushes while `main` is checked out, `rm -fr`/`--recursive` on `/`, `~`, `$HOME`, and `git clean -f`; `--force-with-lease` is allowed.

### Chore: Xvfb in the Dev Container
- **`.devcontainer/setup.sh` installs `xvfb`** (only if `Xvfb` is missing): interactive Cloudflare Turnstile rejects every headless browser, while headful Patchright under `xvfb-run` clears filmfans, kinoger, serienfans, ddlspot, ddlvalley and scnsrc (anti-bot Phase 0, `docs/plans/antibot-patchright.md`).

### Docs: Audit and Style Unification
- **Every doc checked against the code**: all feature, architecture, plan, refactor and OpenSpec documents plus README were audited claim by claim and corrected. Among the fixes:
  - health endpoints are `/api/v1/healthz` and `/api/v1/readyz`, including the Docker healthcheck examples
  - `CACHE_*` env vars are ignored, so Redis is configured via YAML
  - CrawlJob TTL is fixed at 1 h, and CrawlJobs are stored as JSON, not pickle
  - the plugin concurrency default is 5, not 3
  - all plugins are imported at startup, not lazily
  - `HttpxSearchEngine` only validates links
  - removed non-existent APIs: `LinkValidatorPort`, `PluginValidationError`, `load_all()`, the stage engine and result dedup
  - effective config defaults are documented
  - README facts corrected: license GPL-3.0, Python 3.12–3.13, clone URL, no `--factory` flag
- **Current behaviour documented**: known bugs are documented as they behave today, with `Known issue` notes, and config keys nothing reads are marked "currently unused". Code fixes follow separately.
- **Consolidation**: `plugin-system.md` covers discovery, registry, overrides and dispatch, and `python-plugins.md` is the plugin author guide. `docs/architecture/codeplan.md` shrank from 1641 to 270 lines (module map, design invariants, dependency chains). Plans and OpenSpec changes carry accurate, dated status lines and ticked checklists.
- **Unified style**: every doc has the same header (back link, H1, one-line summary), `---` between sections, unwrapped paragraphs, ` — ` dashes and a `Source Code References` table.
- **Config comments**: `data/config.yaml` comments now state the real precedence (`.env` acts as env vars) and that `cache.max_concurrent`/`cache.redis_url` are YAML-only. The remaining German comments in `pyproject.toml`, `.pre-commit-config.yaml` and `Dockerfile` are translated.
- **Markdown syntax cleanup**: every code fence now declares a language (`text`, `python`, `http`, `bash`, ...), `***` separators replaced by `---`, non-breaking hyphens in `docs/PYTHON-BEST-PRACTICES.md` replaced so its table-of-contents anchors resolve.
- **Root `AGENTS.md` reduced** to the `openspec update`-managed block (restored missing `<!-- OPENSPEC:START -->` marker) plus a pointer to `CLAUDE.md`. The removed agent guide was outdated (Docker commands, `commitlint`/`mypy` pre-commit hooks and a `redact_config_for_logging()` helper that do not exist). The Conventional Commits rule moved into `CLAUDE.md`.

### Docs: Slim CLAUDE.md
- **`CLAUDE.md` cut from 42 KB to 9 KB**: it is loaded into every AI session, so it now keeps only the rules that apply to every change (workflow, dependency rule, Python rules, mock patterns, dev container pitfalls) and links to `docs/` for details. The hand-maintained test file tree was dropped (derivable from `tests/`), the unconfigured docs-mcp-server section removed.
- **Markdown line endings unified**: all `.md` files converted to LF; `.gitattributes` enforces `eol=lf` for `*.md`.
- **Guides moved to feature docs**: "Adding a New Plugin" (site analysis, mandatory search standards) now lives in `docs/features/python-plugins.md`, "Adding a New Resolver" (non-XFS workflow, test checklist, JDownloader sources) in `docs/features/hoster-resolvers.md`.

### Refactor: StremioStreamUseCase Split
- **Episode filter moved to infrastructure**: `filter_by_episode()` (guessit-based) now lives in `infrastructure/stremio/episode_filter.py` and is injected into `StremioStreamUseCase` as `episode_filter_fn`. The application layer no longer imports guessit directly.
- **Stream building extracted**: stream formatting, hoster dedup, direct-video detection, behaviorHints, cache links and proxy URL building moved to `application/stremio/stream_builder.py` (pure functions, tests in `test_stremio_stream_builder.py`).
- **Query building extracted**: search query normalisation, base-title fallback queries and multi-language reference/query construction moved to `application/stremio/queries.py` (tests in `test_stremio_queries.py`).
- **`PluginSearchRunner` extracted**: plugin fan-out (query fallback dedup, budget slots, per-plugin timeout, circuit breaker, metrics, episode filter, validation, browser warmup) moved to `application/stremio/plugin_search.py`. `StremioStreamUseCase` builds it from its existing dependencies, so the constructor API is unchanged. Benchmarks exercise the runner directly. Removed the unused `max_concurrent_*` attributes. `stremio_stream.py` shrank from 1374 to 670 lines. 14 new runner tests.

### Live Tests Opt-In
- **`poetry run pytest` excludes live tests by default** (`addopts = "--ignore=tests/benchmark -m \"not live\""`). Live smoke tests hit real websites, so broken external sites made the mandatory pre-commit test run permanently red. Run them explicitly with `poetry run pytest -m live`.

### Dev Container: Node 22 + Caveman
- **Node 22 via devcontainer feature**: `ghcr.io/devcontainers/features/node:1` (version 22) replaces the apt `nodejs` fallback in `setup.sh` (Debian bookworm ships Node 18, too old for the `skills` CLI). `setup.sh` now fails early if Node lacks `node:util.styleText` (< 20.12) and installs npm globals without `sudo` (nvm prefix).
- **Caveman auto-install**: `setup.sh` installs the `@caveman-ai/cli` CLI and the `JuliusBrussee/caveman` Claude Code skills (`npx skills add ... -g -a claude-code`) on every attach, so a rebuilt container needs no manual setup. Failures only warn.

### Dev Container: No Docker, playwright-mcp via stdio
- **Docker removed from the devcontainer**: the docker-in-docker feature never worked on nftables-only host kernels ("can't initialize iptables table `nat'"), and the `docker-compose.yml` MCP stack (docs-mcp-server, llm-context, jaeger, nginx proxy) was unused by Scavengarr. Removed the dind feature, the compose file, the Docker steps in `setup.sh` and the MCP `forwardPorts` (only 7979 remains).
- **playwright-mcp over stdio**: `.mcp.json` is now committed and starts `@playwright/mcp@0.0.82` via `npx` (headless Chromium, isolated profile). `setup.sh` installs the matching Chromium with system dependencies. No container, port or daemon needed.
- **docs-mcp-server** is no longer configured.

### Dev Container Git Access
- **Push without DevPod tunnel**: the devcontainer installs `gh` (feature `github-cli`) and `setup.sh` runs `gh auth setup-git` when `GH_TOKEN` is set in `.env.devcontainer`, so `git push`/`gh pr` work even when the DevPod credential tunnel (`localhost:12049`) is down. `setup.sh` also sets `user.name`/`user.email` from `GIT_AUTHOR_NAME`/`GIT_AUTHOR_EMAIL`.

### JDownloader Reference Sync
- **Auto-updating JDownloader sources**: `.devcontainer/sync-jdownloader.sh` keeps `.devdata/JDownloader2/plugins/` and `controlling/` as SVN working copies of `svn://svn.jdownloader.org/jdownloader/trunk/src/jd/` (replaces the former manual copies). Runs on every devcontainer start via `postStartCommand`, never blocks startup on network errors, and writes changed files + commit messages of the last update to `.devdata/JDownloader2/CHANGES.md`.

### Documentation Drift Fixes
- **CLI docs corrected**: the CLI uses stdlib `argparse` (entry point `poetry run start` → `scavengarr.interfaces.cli:start`), not Typer. Fixed in `CLAUDE.md`, `AGENTS.md`, `README.md`, `docs/features/`.
- **Test inventory**: `CLAUDE.md` test tree now lists all 21 previously missing test files (incl. new `unit/interfaces/`); test counts updated across docs (4137 total = 3905 unit + 169 E2E + 25 integration + 38 live).

### animeloads Plugin Tests
- **56 unit tests** for the `animeloads` Playwright plugin (`tests/unit/infrastructure/test_animeloads_plugin.py`): category mapping/filtering, `SearchResult` building (embed vs. media page links, metadata, truncation), search flow incl. season/episode restriction, pagination (page URLs, empty page stop, `_MAX_PAGES`, `max_results`), DDoS-Guard wait handling, and page cleanup on errors. `animeloads` was the only plugin without unit tests.

### Dependency Cleanup
- **Fix: uninstallable lockfile**: `poetry.lock` pinned `lancedb 0.5.7` (pulled in via `crewai-tools`), which no longer exists on PyPI — `poetry install` failed on every fresh environment. Removed the unused dev dependencies `crewai`, `crewai-tools[mcp]`, `openinference-instrumentation-crewai` and `mcp` (not imported anywhere) and re-locked; ~100 transitive packages dropped.

### Concurrency Benchmark Suite & Auto-Tune Fix
- **Benchmark suite** (`tests/benchmark/`): synthetic E2E benchmarks for ConcurrencyPool slot tuning, probe/validation semaphore sweeps, and formula-vs-empirical comparison. Runs manually via `poetry run pytest tests/benchmark/ -s -v` (excluded from the default `pytest` run via `addopts = "--ignore=tests/benchmark"`).
- **Fix: probe/validation hard-caps**: `probe_concurrency` capped at 100 (was unbounded — 128 on 32 cores), `validation_max_concurrent` capped at 120 (was 160). Caps derived from benchmark diminishing-returns analysis (<5% throughput gain beyond threshold).
- **New test**: `test_extreme_host_probe_validation_capped` verifies caps on 32-core hosts.

### Code Review: Tests, Performance & Quality
- **HLS proxy E2E tests** (11 tests): full request-response cycle for `GET /proxy/{stream_id}/{path}` — manifest rewriting, segment streaming, error codes (400/404/502/503), query string fallback, CORS headers, header forwarding.
- **`_build_stream_from_resolved` unit tests** (8 tests): proxy URL construction for HLS with/without headers, direct MP4, echo-URL skip, query string preservation, custom manifest filenames.
- **`_resolve_query_string` unit tests** (5 tests): request query priority, video URL fallback, empty handling.
- **guessit offloaded to thread pool**: `_filter_by_episode()` and `convert_search_results()` now run in `run_in_executor()` to avoid blocking the async event loop during CPU-bound release name parsing.

### HLS Proxy Query String Fix & Resolver HEAD Verification
- **Fix: HLS proxy preserves CDN auth tokens**: proxy URL now extracts the actual manifest filename and query string from the video URL instead of hardcoding `master.m3u8`. CDN auth tokens (e.g. `?t=abc123&expires=...`) are forwarded correctly, fixing 403 errors on all HLS streams routed through the proxy.
- **Fix: proxy endpoint query string fallback**: when Stremio strips query params from the proxy URL, the endpoint falls back to the original video URL's query string to recover CDN auth tokens.
- **HEAD verification for VOE, Streamtape, SuperVideo resolvers**: extracted video URLs are now HEAD-checked before returning. Unreachable CDN URLs (403, timeout) return `None` instead of reaching Stremio as "video is not supported".
- **6 new tests**: HEAD verification failure tests for VOE (2), Streamtape (2), SuperVideo (2).

### HLS Proxy Endpoint & XFS Video Verification
- **HLS proxy endpoint** (`GET /api/v1/stremio/proxy/{stream_id}/{path:path}`): proxies HLS manifest and segment requests with correct CDN headers (Referer etc.) for hosters like Dropload whose CDNs require headers on all sub-requests, not just the master manifest. Rewrites absolute CDN URLs in variant playlists so the HLS player routes all fetches through the proxy.
- **XFS video URL verification**: after extracting a video URL from an XFS embed page, the resolver now performs a HEAD check against the CDN to verify the URL is actually reachable. Filters out IP-locked CDN tokens (e.g. LULUVID/LULUVDOO) that always return 403 because the token was bound to Cloudflare's edge IP.
- **Extended CachedStreamLink entity**: new `video_url`, `video_headers`, and `is_hls` fields support HLS proxy routing. Backward-compatible deserialization for existing cache entries.
- **New module** `infrastructure/stremio/hls_proxy.py`: manifest rewriting (`rewrite_manifest`), CDN base extraction (`cdn_base_from_url`), CDN fetch helper (`fetch_hls_resource`), URL builder (`build_cdn_url`).
- **34 new tests**: HLS proxy helpers (18), XFS video verification (5), stream link cache HLS fields (3), E2E streamable assertion updated for proxy URLs.

### Container-Aware Resource Detection & Adaptive Auto-Tuning
- **cgroup-aware resource detector** (`infrastructure/resource_detector.py`): reads actual CPU/memory limits from Linux cgroups (v2 → v1 → OS fallback) instead of host values. Detection order mirrors JVM `UseContainerSupport` and Go 1.25. On a 16-core/64GB host with `--cpus=2 --memory=2g`, the system now correctly sees 2 CPUs and 2GB RAM
- **Extended auto-tuning** (`_auto_tune()` in composition root): when `stremio.auto_tune_all=true` (default), ALL concurrency parameters are scaled proportionally to detected resources:
  - `max_concurrent_plugins`: `min(cpu*3, mem_gb*2, 30)` — was only CPU-based
  - `max_concurrent_playwright`: `min(cpu, mem_gb/0.15, 10)` — RAM-limited (~150MB per BrowserContext)
  - `probe_concurrency`: `cpu * 4` — lightweight HEAD requests scale linearly
  - `validation_max_concurrent`: `cpu * 5` — same as probes
- **Adaptive AIMD rate limiting** (`TokenBucket` in `rate_limiter.py`): per-domain request rates now adjust automatically based on target-server feedback using TCP-style AIMD (Additive Increase / Multiplicative Decrease):
  - Success → rate increases by 10% (capped at `rate_limit_max_rps`, default 50)
  - 429/503 → rate halved immediately (floored at `rate_limit_min_rps`, default 0.5)
  - Timeout → rate reduced by 25%
  - Each domain has its own independent adaptive rate — throttling on site A doesn't affect site B
- **RetryTransport feedback integration**: after every HTTP response, the transport now calls `record_success()` or `record_throttle()` on the rate limiter, enabling real-time rate adaptation
- **New config fields**: `stremio.auto_tune_all` (bool, default true), `http.rate_limit_adaptive` (bool, default true), `http.rate_limit_min_rps` (float, default 0.5), `http.rate_limit_max_rps` (float, default 50.0)
- **48 new tests**: resource detector (cgroup v2/v1/fallback, fractional CPUs, unlimited, frozen dataclass), adaptive rate limiter (AIMD, min/max bounds, domain independence, compounding), auto-tune (5 container scenarios from 1CPU/512MB to 16CPU/64GB), retry transport feedback

### Performance Tuning
- **Aggressive timeout cuts**: HTTP scraping 30→15s, hoster resolution 15→10s, Playwright page load 30→20s, plugin timeout 30→15s, probe timeout 10→5s, stealth probe 15→10s, link validation 5→3s
- **Higher concurrency**: `max_concurrent_plugins` 5→15 with auto-tune cap raised 10→20, `max_concurrent_playwright` added at 7, `validation_max_concurrent` 20→30, `probe_concurrency` 10→20, `max_probe_count` 50→80, plugin default `_max_concurrent` 3→5
- **Configurable resolve semaphore**: `_resolve_top_streams()` now uses `probe_concurrency` from config (was hardcoded at 10)
- **Faster retries**: `retry_max_attempts` 3→2, `retry_backoff_base` 1.0→0.5s, `retry_max_backoff` 30→10s
- **Higher throughput**: `rate_limit_rps` 5→10 per domain, `max_results_per_plugin` 100→50 (less work per plugin, faster turnaround)
- **Longer cache**: `search_ttl_seconds` 900→1800 (30min cache for repeated searches)
- **`resolve_target_count` convention**: 0 = disabled (resolve all streams), replaces magic number

### Playwright Stealth Default
- **Stealth mode now on by default**: `PlaywrightPluginBase._stealth` changed from `False` to `True` — all 9 Playwright plugins (boerse, mygully, moflix, streamworld, animeloads, byte, scnsrc, ddlvalley, ddlspot) now use stealth evasions automatically
- **SuperVideoResolver uses shared StealthPool**: replaced dedicated Playwright browser lifecycle with injected `StealthPool` — eliminates a redundant Chromium process and reuses the stealth-enabled browser pool. Graceful degradation to httpx-only when no pool is available
- **StealthPool `wait_for_cloudflare()` public**: renamed from `_wait_for_cloudflare()` to allow external callers (SuperVideoResolver) to use the Cloudflare wait logic
- **StealthPool always created**: composition root now creates `StealthPool` unconditionally (was gated by `probe_stealth_enabled`), since SuperVideoResolver needs it regardless of probe configuration

### Plugin Fixes (megakino, boerse)
- **megakino**: Fix broken plugin (0 results in live tests). Three issues resolved:
  1. Domain redirect: `megakino.me` now redirects to `megakino1.biz` — updated `_DOMAINS` list with 4 mirrors (`megakino1.biz`, `megakino1.ws`, `megakino1.net`, `megakino.me`)
  2. yg_token JS challenge: detail page GET requests require a `yg_token` cookie — added `_ensure_token()` that fetches `/index.php?yg=token` (204 response sets cookie) before scraping detail pages
  3. iframe extraction: site changed from `<a href="/dl/...">` to `<iframe data-src="https://voe.sx/e/...">` for hoster links — updated `_DetailPageParser` to extract iframe `data-src`/`src` attributes
- **boerse**: Add missing `boerse.tw` mirror domain to `_DOMAINS` and `_INTERNAL_HOSTS` (6 mirrors total: am/tw/sx/im/ai/kz)

### Stremio Streamable Link E2E Tests
- **Fix existing E2E tests**: `test_stremio_endpoint.py` and `test_stremio_series_e2e.py` updated to pass `pool=ConcurrencyPool()` and mock `get_languages`/`get_mode` on the plugin registry — required after the global concurrency pool became mandatory
- **New `test_stremio_streamable_e2e.py`**: 31 comprehensive E2E tests verifying the full Stremio stream pipeline produces genuinely streamable links (direct `.mp4`/`.m3u8` video URLs with `behaviorHints`, or `/play/` proxy URLs). Covers movie resolution (MP4, HLS, mixed, echo filtering, dedup, behaviorHints), series resolution (episode filtering, multi-plugin, high episode numbers), circuit breaker integration, concurrency pool integration, edge cases (all plugins error, all resolvers fail/echo, empty results), and full pipeline roundtrips
- **Test suite total**: 3900 unit + E2E tests (158 E2E)

### Global Concurrency Pool & Playwright Request Isolation
- **ConcurrencyPool**: new infrastructure component (`infrastructure/concurrency.py`) provides a global concurrency budget with separate httpx and Playwright slot pools. Fair-share algorithm dynamically divides slots across active requests: `fair_share = max(1, total_slots // active_requests)`. When a request exits, remaining requests automatically get more slots
- **Per-request BrowserContext isolation**: Playwright plugins now create a fresh `BrowserContext` per request via `isolated_search()`, preventing state corruption when concurrent requests hit the same singleton plugin. Uses `ContextVar[BrowserContext]` to pass the per-request context transparently through `_ensure_context()`
- **`isolated_search()` method**: added to both `PlaywrightPluginBase` and `HttpxPluginBase`. Playwright plugins create an isolated BrowserContext; httpx plugins pass through to `search()` unchanged
- **`_serialize_search` mode**: Playwright plugins that rely on persistent page state (streamworld, moflix) set `_serialize_search = True` to serialize searches via `asyncio.Lock` instead of creating per-request contexts
- **Cookie-based session transfer**: authenticated Playwright plugins (boerse, mygully) now login in a temporary BrowserContext, export cookies, and inject them into per-request contexts via `_prepare_context()` override — enables concurrent searches without session conflicts
- **Domain ports**: `ConcurrencyPoolPort` and `ConcurrencyBudgetPort` protocols in `domain/ports/concurrency.py` keep the use case decoupled from the concrete infrastructure
- **Composition root wiring**: `ConcurrencyPool` is created with `httpx_slots=max_concurrent_plugins` and `pw_slots=max_concurrent_playwright`, then injected into `StremioStreamUseCase`
- **Unified code path**: pool is now required (not optional) — eliminated the dual code path that maintained a local semaphore fallback. `_dispatch_search()` static method cleanly routes to `isolated_search()` or `search()` based on plugin capability

### Resilience & Observability Improvements
- **Circuit breaker for plugins**: new `PluginCircuitBreaker` (infrastructure/circuit_breaker.py) tracks per-plugin failure counts. After 5 consecutive failures (configurable), the breaker opens and skips the plugin for 60s (configurable cooldown). Half-open state allows a single probe request. Integrated into `StremioStreamUseCase` — failures, timeouts, and successes are all recorded. `snapshot()` exposes per-plugin state for diagnostics
- **Graceful shutdown**: new `GracefulShutdown` (infrastructure/graceful_shutdown.py) tracks in-flight HTTP requests via `request_started()` / `request_finished()`. On shutdown, `wait_for_drain(timeout=10.0)` blocks until all in-flight requests complete or the timeout elapses. Integrated into the HTTP middleware and composition root lifespan
- **Health & readiness endpoints**: `/api/v1/healthz` (liveness) and `/api/v1/readyz` (readiness) — readiness returns 503 until startup is complete and during shutdown drain
- **`/api/v1/stats/metrics` endpoint**: exposes runtime metrics as JSON — plugin search stats (count, success rate, avg duration), probe stats, circuit breaker state per plugin, concurrency pool utilisation (slots total/available/active), and shutdown status
- **Playwright browser relaunch retry**: `_launch_standalone(*, retries=1)` retries browser launch once after a 1s delay on failure, with proper cleanup of partial Playwright state between attempts
- **`isolated_search()` context leak fix**: moved `_prepare_context()` and stealth setup inside the `try` block so that the `BrowserContext` is always closed even if preparation fails. Uses `Token[BrowserContext | None] | None` pattern for safe ContextVar reset

### Cineby Plugin Timeout Fix
- **Increase cineby concurrency**: override `_max_concurrent` from 3 → 8 (lightweight JSON API at db.videasy.net handles higher concurrency)
- **Cap detail fetches**: add `_MAX_DETAIL_FETCH = 25` — only the first 25 search results get detail-fetched (IMDB ID, runtime); remaining results are built from search data only. Prevents timeout on broad queries like "Avengers" (100+ results × 3 concurrency = ~15s detail phase → now 25 results × 8 concurrency = ~1.5s)

### Parallel Language Group Search
- **Parallelize `_search_lang_groups()`**: language groups (e.g. German plugins + English plugins) now search concurrently via `asyncio.gather()` instead of sequentially. Saves ~2-5s on multi-language requests where the slower group no longer blocks the faster one

### Shared Playwright Browser Pool & Pre-Warming
- **SharedBrowserPool**: new infrastructure component (`shared_browser.py`) manages a single Chromium process shared by all 9 Playwright plugins. Each plugin gets its own `BrowserContext` for isolation while sharing the underlying browser — eliminates per-plugin ~1-2s browser startup overhead
- **Composition-time pool injection**: plugins receive the shared pool reference at startup via `set_shared_pool()` instead of per-request task injection — eliminates race conditions on plugin state and simplifies the search orchestration
- **Browser pre-warming**: when a Stremio search request arrives and Playwright plugins are present, the browser warmup fires as a background task (named `browser-warmup` with exception callback) immediately while httpx plugins begin searching
- **Parallel Playwright plugins**: Playwright plugins now run concurrently on the shared browser (bounded by `max_concurrent_playwright`, default 5) instead of sequentially via `Semaphore(1)`. PW semaphore is dynamically sized to `min(pw_plugin_count, max_concurrent_playwright)` per request
- **Add `max_concurrent_playwright` config** (default 5): upper bound for parallel Playwright plugin searches; actual concurrency is dynamically capped at the number of PW plugins in the request
- **Ownership-aware cleanup**: `PlaywrightPluginBase.cleanup()` only closes the browser/Playwright when the plugin owns it (standalone mode). When using a shared pool, only the context and page are closed
- **Disconnection recovery**: `_ensure_browser()` checks `browser.is_connected()` and relaunches transparently if the browser has crashed or disconnected. Context and page state are reset on disconnect
- **Resilient cleanup**: `SharedBrowserPool.cleanup()` logs warnings instead of silently swallowing exceptions during browser close / Playwright stop
- **`get_mode()` on PluginRegistryPort**: use case checks plugin mode without loading the full plugin object — avoids double plugin fetch in search orchestration

### Stremio Stream Resolution Performance
- **Skip probe when resolve is active**: probe phase (`probe_at_stream_time`) is now skipped when a resolve callback is configured, since resolution implicitly checks liveness — saves one entire I/O phase (~5-10s)
- **Early-stop resolve**: `_resolve_top_streams()` now uses `asyncio.wait(FIRST_COMPLETED)` and stops once `resolve_target_count` (default 15) genuine video URLs have been extracted, cancelling remaining tasks. Avoids waiting for slow hosters when enough playable streams are ready
- **Add `resolve_target_count` config** (default 15): target number of successfully resolved video streams before early-stop
- **Shared semaphore across query variants**: `_search_with_fallback()` now shares a single semaphore across all query variants (e.g. "Dune: Part Two" + "Dune") instead of creating independent semaphores per variant — prevents connection overload
- **Increase `max_concurrent_plugins` default** from 5 → 10: allows more httpx plugins to search in parallel

### Multi-Language Search & unidecode Migration
- **unidecode for universal transliteration**: replace manual 4-character German umlaut table (`_UMLAUT_TABLE`) and 15-character transliteration table (`_TRANSLITERATION`) with `unidecode` library — supports 130+ Unicode scripts for title matching and search query generation
- **Multi-language plugin support**: plugins now declare `languages: list[str]` (default `["de"]`) instead of `default_language: str`. Backward-compatible property `default_language` returns `languages[0]`
- **Per-language TMDB title resolution**: `get_title_and_year()` and `find_by_imdb_id()` accept a `language` parameter; cache keys include language to avoid cross-language collisions
- **Wikidata title lookup generalized**: IMDB fallback client `_fetch_wikidata_title()` supports any language (was German-only)
- **Multi-language search dispatch**: Stremio use case groups plugins by language, fetches TMDB titles for each unique language in parallel, and searches each group with language-specific queries. A plugin with `languages=["de", "en"]` gets searched with both German and English title queries. Combined `TitleMatchInfo` reference merges all language titles for the title matcher
- **Registry `get_languages()`**: `PluginRegistryPort` exposes `get_languages(name)` to retrieve a plugin's language list

### E2E-Discovered Fixes (Stream Resolution)
- **Domain alias dispatch**: `HosterResolverRegistry` now maps all `supported_domains` from XFS/DDL resolvers, not just the resolver name — fixes dispatch for vidhide family domains (filelions, streamhide, louishide, etc.)
- **Drop unresolvable proxy streams**: when a resolve function is configured but returns `None`, the stream is excluded from Stremio responses instead of creating a `/play/` proxy URL that always 502s
- **Subtitle fallback search queries**: titles with colons (e.g. "Dune: Part One") now generate a second search query using just the base title ("Dune"), because German streaming sites often omit subtitles — improves Dune from 2 to 3 streams, Starship Troopers from 5 to 6
- **Punctuation-safe title matching**: `_normalize()` now strips punctuation (colons, hyphens, etc.) before token comparison, so "dune:" and "dune" are correctly recognized as matching tokens
- **rapidfuzz title matching**: replace `difflib.SequenceMatcher` + custom `_token_similarity()` with `rapidfuzz.fuzz.token_sort_ratio` / `token_set_ratio` for faster, more robust fuzzy matching (C++ backend) — also enhance sequel detection to penalise ANY sequel number mismatch (bidirectional: "Iron Man 2" ref vs "Iron Man 3" result, or "Iron Man 2" ref vs "Iron Man" result)
- **Add goodstream XFS config**: new video hoster resolver for goodstream.one/goodstream.uno (XFS-based, confirmed via JDownloader plugin)
- **Add 6 vidhide domain aliases**: streamhide, louishide, streamvid, availedsmallest, tummulerviolableness, tubelessceliolymph (all parklogic.com anti-adblock protected vidhide family)
- Fix XFS two-step form hosters: detect `<form id="F1" action="/dl">` splash pages and POST to `/dl` with `op=embed&file_code={id}&auto=1` to obtain the actual player page (affects bigwarp, savefiles, streamruby, and other form-based XFS hosters)
- Mark wolfstream as `needs_captcha=True` — embed pages return obfuscated JS redirect (anti-bot), not extractable with httpx
- Fix vidking resolver regex: accept `/embed/tv/{tmdb_id}/{season}/{episode}` paths for series content (was only matching `/embed/movie/`)

### XFS Video Hoster Extraction
- Upgrade XFS resolver from validate-only to full video URL extraction for 18 video hosters
- Extract playable HLS/MP4 URLs from embed pages via JWPlayer config, Dean Edwards packed JS, and Streamwish `hls2` patterns
- Shared video extraction module (`_video_extract.py`) reused by both XFS and Filemoon resolvers
- Add `is_video_hoster` / `needs_captcha` flags to `XFSConfig` for per-hoster behavior
- Video hosters: fetch `/e/{file_id}` embed page → extract video URL → return `ResolvedStream` with Referer header
- DDL hosters (katfile, hexupload, clicknupload, filestore, uptobox, hotlink): keep validate-only behavior
- Captcha-required hosters (veev, vinovo): return None immediately (Cloudflare Turnstile required)
- Add `extra_domains` field to `XFSConfig` for JDownloader-sourced domain aliases (vidhide has 19 aliases)
- Supported video hosters: streamwish, vidmoly, vidoza, vidhide, lulustream, upstream, wolfstream, vidnest, mp4upload, uqload, vidshar, vidroba, vidspeed, bigwarp, dropload, savefiles, funxd, streamruby

### Stremio Playback Fixes
- Filter non-video URLs from Stremio responses: `_is_direct_video_url()` detects embed pages vs actual video URLs (.mp4, .m3u8, HLS patterns)
- Skip unplayable streams at search time: resolvers that only validate availability (XFS, DDL) but cannot extract video URLs are excluded from Stremio results instead of producing guaranteed-502 proxy URLs
- Guard in `/play/` endpoint rejects resolved URLs that are just the embed page echoed back (returns 502 instead of redirecting to HTML)
- Fix VOE resolver: follow JS redirects from voe.sx → rotating domains (e.g. lauradaydo.com), fetch token array from external loader.js script

### Production Bug Fixes (Stream Resolution)
- Fix `http-equiv=` garbage treated as URL in megakino_to/movie4k `_collect_streams()` — reject non-HTTP stream values
- Add belt-and-suspenders URL scheme validation in `HttpLinkValidator.validate_batch()`
- Fix veev resolver regex: accept 12+ char alphanumeric IDs (was exactly 12, veev.to now uses 43-char IDs)
- Fix vidking resolver regex: accept `/embed/movie/{id}` paths used by cineby/videasy plugins
- Re-add goodstream XFS config (previously removed in error — confirmed XFS-based via JDownloader GoodstreamUno.java plugin)

### Torznab Pagination
- Wire `TorznabQuery.offset`/`limit` fields through router → use case for server-side result pagination
- Add `offset` and `limit` query parameters to the Torznab search endpoint (defaults: 0 / 100)
- Prowlarr can now page through cached result sets via standard Torznab pagination

### Cloudflare Detection in Health Probes
- `HealthProber` now detects Cloudflare challenges during HEAD/GET probes:
  - HEAD path: `cf-ray` header + 403/503 status code heuristic
  - GET fallback: body-based marker detection via `is_cloudflare_challenge()`
- `ProbeResult.captcha_detected` is now set by the health prober (was always `False`)
- `compute_health_observation()` returns 0.0 for captcha-blocked probes — CF-blocked plugins rank lower

### Dead Code Cleanup
- Remove empty `infrastructure/scraping/` package (stale from Scrapy removal)
- Remove dead `TorznabAction` type alias (defined but never used)
- Remove dead `CacheBackend` re-export from `infrastructure/cache/__init__.py`
- Remove dead `AgeBucket` re-export from `domain/entities/__init__.py`
- Fix duplicate `AgeBucket` in `query_pool.py` — import from domain instead of redefining
- Remove 3 dead config field assignments in `StremioStreamUseCase` (`stremio_deadline_ms`, `max_items_total`, `max_items_per_plugin`) — stored but never read
- Delete dead `/indexers` data file (obsolete Scrapy reference)
- Delete empty `.env.example`
- Clean orphaned `__pycache__` directories
- Remove dead `PluginRegistry.load_all()` method (never called)
- Remove dead `router` re-export from `interfaces/api/__init__.py`
- Remove dead `PluginStats.last_search_ns` field (written but never read)
- Remove dead `StageResult` dataclass from `domain/plugins/base.py`
- Remove dead `PluginValidationError` from `domain/plugins/exceptions.py`
- Remove dead `LinkValidatorPort` protocol (entire file deleted)
- Remove dead `validation_schema.py` module (entire file deleted)
- Remove dead `test_auth_env_resolution.py` test file (entire file deleted)
- Remove dead `probe_urls()` function from `infrastructure/hoster_resolvers/probe.py`
- Remove dead `_DOMAINS` set and `_is_streamtape_domain()` from `streamtape.py`
- Remove dead `get_german_title()` from `TmdbClientPort`, `HttpxTmdbClient`, and `ImdbFallbackClient`

### HTTP Rate Limiting & 429 Retry (Defense in Depth)
- Add `RetryTransport` — custom httpx transport wrapping all outgoing HTTP requests
  - Proactive: per-domain token-bucket rate limiting via existing `DomainRateLimiter` (5 RPS default)
  - Reactive: automatic retry on 429/503 with exponential backoff + jitter + `Retry-After` header support
  - Configurable: `retry_max_attempts` (default 3), `retry_backoff_base` (1s), `retry_max_backoff` (30s)
- Wire `DomainRateLimiter` (previously dead code) into shared `httpx.AsyncClient` via transport layer
- Remove manual 429 retry from `SuperVideoResolver` (now handled transparently by transport)
- Add 3 config fields: `http.retry_max_attempts`, `http.retry_backoff_base`, `http.retry_max_backoff`

### Plugin Scoring & Probing
Background plugin scoring system that measures plugin health and search quality via EWMA-based probes, then selects only the top-N plugins per Stremio request.

- Add domain entities: `ProbeResult`, `EwmaState`, `PluginScoreSnapshot` with age buckets
- Add `PluginScoreStorePort` protocol and `CachePluginScoreStore` persistence (JSON via CachePort)
- Add pure EWMA scoring functions: `alpha_from_halflife`, `ewma_update`, `compute_confidence`, `compute_health_observation`, `compute_search_observation`, `compute_final_score`
- Add `HealthProber` (HEAD with 405/501 GET fallback, Cloudflare detection) and `MiniSearchProber` (limited search + hoster HEAD checks)
- Enhance `MiniSearchProber` to filter HEAD-checks by supported hosters (from `HosterResolverRegistry`)
- Add `supported_ratio` (5th component, weight 0.25) to `compute_search_observation()` — scores now reflect whether a plugin's result links point to hosters with registered resolvers
- Add `hoster_supported` / `hoster_total` fields to `ProbeResult`
- Add `QueryPoolBuilder` with dynamic TMDB-based query generation (trending + discover endpoints, weekly rotation, German locale, bundled fallback lists)
- Add `ScoringScheduler` background task (health probes daily, search probes 2x/week per plugin/category/bucket)
- Add `ScoringConfig` and extend `StremioConfig` with scoring budget parameters
- Add per-plugin YAML overrides (`PluginOverride` model: timeout, max_concurrent, max_results, enabled)
- Fix YAML config loading for `stremio` and `scoring` sections (pre-existing gap in `_SECTION_KEYS`)
- Wire scoring components in composition root with clean cancellation on shutdown
- Add scored plugin selection in `StremioStreamUseCase` with cold-start fallback and exploration slot
- Add `GET /api/v1/stats/plugin-scores` debug endpoint with query filters
- Add `PluginRegistry.remove()` method for disabling plugins via config overrides

### Hoster Resolver Expansion
- Add 6 new XFS hoster configs: Mp4Upload, Uqload, Vidshar, Vidroba, Hotlink, Vidspeed (27 XFS hosters total)
- Add new SendVid streaming resolver (two-stage: API status check + page video extraction)
- Add new Mediafire DDL resolver (public file info API, offline detection via error 110 + delete_date)
- Add new GoFile DDL resolver (ephemeral guest token with 25-min cache, content availability API)
- Add 6 new XFS hoster configs: StreamRuby, Veev, Lulustream, Upstream, Wolfstream, Vidnest
- Add 20 new StreamWish domain aliases from JDownloader (obeywish, awish, embedwish, etc.)
- Add 5 new Streamtape domain aliases (scloud, strtapeadblock, tapeblocker, etc.)
- Add new StreamUp (strmup) standalone HLS resolver with page + AJAX fallback extraction
- Add `vidara` domain alias to StreamUp resolver (Vidara = StreamUp infrastructure)
- Add new Vidsonic standalone HLS resolver with hex-obfuscated URL decoding
- Wire StrmupResolver and VidsonicResolver in composition root

### Stremio Stream Deduplication
- Add per-hoster deduplication: only the best-ranked stream per hoster is returned
- Prevents duplicate links from the same hoster (e.g., 5 VOE links → 1 best VOE link)
- Applied after sorting, before probing/caching — keeps highest-ranked link per hoster

### Architecture Fixes (Clean Architecture Compliance)
- Remove all infrastructure imports from `StremioStreamUseCase` (application layer)
  - Define `_StremioConfig`, `_StreamSorter`, `_MetricsRecorder` protocols locally
  - Inject `sorter`, `convert_fn`, `filter_fn`, `user_agent`, `max_results_var` via constructor
  - Composition root (interfaces layer) now owns all infrastructure wiring
- Add `@runtime_checkable` to all 9 domain Protocol ports (was only on 2 of 9)
- Make `CrawlJob` entity immutable (`frozen=True`) — built once by factory, never mutated
- Remove explicit Protocol inheritance from `CacheCrawlJobRepository` (duck-typing consistency)

### Stremio Playback: behaviorHints.proxyHeaders
Pre-resolve hoster embed URLs at `/stream` time and emit `behaviorHints.proxyHeaders` so Stremio's local streaming server sends the correct `Referer` and `User-Agent` headers to hoster CDNs. This eliminates buffering caused by 403 rejections on missing headers.

- Add `behavior_hints` field to `StremioStream` domain entity
- Add `_build_behavior_hints()` and `_resolve_top_streams()` to `StremioStreamUseCase`
- Add `ResolveCallback` type and wire `HosterResolverRegistry.resolve` via composition root
- Add `Referer` header to all streaming resolver returns (VOE, Filemoon, SuperVideo, Streamtape)
- Emit `behaviorHints.notWebReady` + `proxyHeaders.request` in Stremio stream JSON
- Fallback to `/play/` proxy redirect for streams that fail pre-resolution

### Performance (Audit)
- Parallelize `_select_plugins()` score fetching with `asyncio.gather()` (was sequential await loop)
- Parallelize `_run_search_cycle()` probes: collect all due probes first, then run concurrently with semaphore
- Offload CPU-bound `filter_by_title_match()` (guessit + SequenceMatcher) to thread pool via `run_in_executor()`
- Add periodic eviction of expired entries in `HosterResolverRegistry` caches (prevents unbounded memory growth)

### Code Quality (Audit)
- Consolidate 12 identical DDL hoster resolvers into parameterised `GenericDDLConfig` + `GenericDDLResolver` (alfafile, alphaddl, fastpic, filecrypt, filefactory, fsst, go4up, mixdrop, nitroflare, 1fichier, turbobit, uploaded)
- Add shared `extract_domain()` utility for URL domain extraction, replacing 15 inline duplicates across resolver modules
- Add `exc_info=True` to 4 `except Exception` handlers in business logic (stremio_stream.py, composition.py)
- Remove dead code across domain, application, infrastructure, and plugin layers
- Consolidate duplicate constants and unused imports

### YAML Plugin Infrastructure Removal (Refactor)
Migrated 3 remaining YAML plugins (warezomen, filmpalast, scnlog) to Python httpx plugins. Removed entire YAML plugin infrastructure: ScrapyAdapter, YAML schema models, YAML loader, YAML discovery, and all associated tests (~3,500 lines deleted). Renamed `HttpxScrapySearchEngine` to `HttpxSearchEngine`. Removed the `scrapy` dependency from `pyproject.toml`.

- Add warezomen Python httpx plugin replacing YAML (`fd4bc98`)
- Add filmpalast Python httpx plugin replacing YAML (`3ae7921`)
- Add scnlog Python httpx plugin replacing YAML (`0fa4f36`)
- Remove YAML plugin infrastructure and scrapy dependency (`42fced9`)
- Rename HttpxScrapySearchEngine to HttpxSearchEngine (`12aa0a5`)

### Plugin Standardization (Refactor)
All 29 Python plugins migrated to shared base classes (`HttpxPluginBase` / `PlaywrightPluginBase`), eliminating 50–100 lines of duplicated boilerplate per plugin (client setup, domain fallback, cleanup, semaphore, user-agent).

- Add `HttpxPluginBase` shared base class for httpx plugins (`16b084b`)
- Add `PlaywrightPluginBase` shared base class for Playwright plugins (`16b084b`)
- Add shared plugin constants and CSS-selector HTML helpers (`b12c5a7`)
- Migrate 5 API-only plugins (einschalten, fireani, haschcon, megakino_to, movie4k) to HttpxPluginBase (`407fef9`)
- Migrate aniworld, dataload, nima4k plugins to HttpxPluginBase (`21030d3`, `bded483`, `e4f7f13`)
- Migrate all remaining 21 plugins to shared base classes (`10d23db`)
- Add missing `season`/`episode` params to 10 plugin `search()` signatures (`deef995`)
- Reorganize configurable settings (`_DOMAINS`, `_MAX_PAGES`, etc.) to top of all 28 plugins with section headers (`a79fb8e`)
- Replace hardcoded year boundary with dynamic `datetime.now().year + 1` in cine plugin (`b3e40e3`)

### New Plugins (40 Python plugins added)
Expanded from 2 plugins (filmpalast YAML + boerse Python) to 42 total plugins (33 httpx + 9 Playwright), covering German streaming, DDL, and anime sites.

**Httpx plugins (33):**
- aniworld.to — anime streaming with domain fallback (`3321775`)
- burningseries (bs.to) — series streaming (`b1e46ff`)
- cine.to — movie streaming via JSON API (`3153df0`)
- dataload (data-load.me) — DDL forum with vBulletin auth (`94004e6`)
- einschalten.in — streaming via JSON API (`a729041`)
- filmfans.org — movie DDL with release parsing (`7924969` → `7cd46ed`)
- fireani.me — anime via JSON API (`160171f`)
- haschcon.com — streaming (`0d65a50`)
- hdfilme.legal — streaming with MeineCloud link extraction (`fdaf283`)
- kinoger.com — streaming with domain fallback (`1c03b95`)
- kinoking.cc — streaming with movie/series detection (`067a634`)
- kinox.to — streaming with 9 mirror domains and AJAX embed extraction (`d645ccf`, `20e40e9`)
- megakino.me — streaming (`ff68aeb`)
- megakino_to (megakino.org) — streaming via JSON API (`df2cf77`)
- movie2k.cx — streaming with 2-stage HTML scraping
- serienfans.org — TV series DDL with JSON search API and season/episode support
- movie4k.sx — streaming via JSON API with cross-language title matching (`52f07dd`, `dfc58db`)
- myboerse.bz — DDL forum with multi-domain fallback (`27b42b4`, `d80c69a`)
- nima4k.org — DDL with category browsing (`d001135`)
- nox.to — DDL archive with JSON API, movies + TV episodes
- sto (s.to/SerienStream) — TV-only streaming (`7924969`, `2a73f16`)
- streamcloud.plus — streaming with domain fallback (`10f3808`)
- streamkiste.taxi — streaming with 5 mirror domains (`ff8c662`, `bea8be1`)
- cineby.gd — streaming via JSON API
- crawli.net — single-stage download search engine
- hd-source.to — DDL with multi-page scraping
- hd-world.cc — DDL archive via WordPress REST API, movies + TV series
- jjs (jjs.page) — DDL with multi-stage scraping
- movieblog.to — DDL blog (WordPress)
- serienjunkies.org — DDL with captcha-protected links
- filmpalast.to — movie/TV streaming (migrated from YAML)
- scnlog.me — scene log with pagination (migrated from YAML)
- warezomen.com — DDL site (migrated from YAML)

**Playwright plugins (9):**
- animeloads (anime-loads.org) — anime with DDoS-Guard bypass (`75176af`, `08cced5`)
- boerse.sx — DDL forum with Cloudflare + vBulletin auth (rewritten, see v0.1.0)
- byte.to — DDL with Cloudflare bypass and iframe link extraction (`2cdab77`)
- ddlspot.com — DDL with pagination up to 1000 results (`fca8947`, `21a0657`)
- ddlvalley.me — DDL WordPress with pagination (`0fedecf`, `3d80cec`)
- moflix (moflix-stream.xyz) — streaming via internal API with Cloudflare bypass (rewritten from httpx, `eaa0002`)
- mygully.com — DDL forum with Cloudflare + vBulletin auth
- scnsrc.me (SceneSource) — scene releases with multi-domain fallback (`cb34282`, `2d930bb`)
- streamworld.ws — streaming (rewritten from httpx to Playwright, `de29957`)

**YAML plugins (removed):**
- filmpalast.to, scnlog.me, warezomen.com — all migrated to Python httpx plugins (see YAML Plugin Infrastructure Removal)

### Stremio Addon
Full Stremio addon integration with manifest, catalog search, and stream resolution. Allows using Scavengarr as a Stremio source for all indexed plugins.

- Add Stremio domain entities, TMDB port, and StremioConfig (`c055303`)
- Add TMDB httpx client with caching and German locale (`c7950ef`)
- Add release name parser with guessit integration (`89b8ca9`)
- Add stream converter for SearchResult → RankedStream (`a4b2e0c`)
- Add configurable stream sorter for Stremio addon (`015dde6`)
- Add StremioCatalogUseCase for TMDB trending and search (`8d7dfbc`)
- Add StremioStreamUseCase for IMDb-to-streams resolution (`526e0c5`)
- Add Stremio router with manifest, catalog, and stream endpoints (`0d5854e`, `fed81df`)
- Add title-match scoring module for Stremio stream filtering (`6a06df9`, `b9454cf`)
- Add `get_title_and_year()` to TMDB client and IMDB fallback (`55a7bf7`, `2af65b1`)
- Add IMDB fallback title resolver for Stremio without API key (`23fe5c4`)
- Add Wikidata German title lookup for IMDB fallback client (`eb8094a`)
- Robust title matching via guessit + multi-candidate scoring (`e0b6e76`)
- Thread `plugin_default_language` through stream converter (`8bf0911`)
- Add `default_language` attribute to all plugins (`c53e04c`)
- Add per-plugin timeout to prevent slow plugins blocking response (`c03a28b`)

### Hoster Resolver System
56 hoster resolvers across three categories: 17 individual resolvers (streaming + DDL), 12 generic DDL resolvers (parameterised `GenericDDLConfig`), and 27 XFS-consolidated resolvers (generic `XFSResolver` with parameterised `XFSConfig`). All resolver tests use respx (httpx-native HTTP mocking).

**Core infrastructure:**
- Add ResolvedStream entity and HosterResolverPort protocol (`f6a3676`)
- Add HosterResolverRegistry with content-type probing fallback (`8a7642b`)
- Add hoster hint fallback for rotating redirect domains (`cfe3314`)
- URL domain priority + redirect following in hoster registry (`b083c0b`)
- Add `cleanup()` to HosterResolverRegistry (`c148640`)
- Integrate hoster resolvers into `/play/` endpoint (`2b1f82c`)
- Cache stream links and generate proxy play URLs (`686b4bf`, `f61e30a`)
- Add `/stremio/play/{stream_id}` endpoint with 302 redirect (`08be69c`)
- Add stream preflight probe to filter dead hoster links at `/stream` time
- Add hybrid Playwright Stealth probe for Cloudflare bypass

**Streaming resolvers (10):**
- Add VOE hoster resolver with multi-method extraction (`242ce2d`)
- Add Streamtape hoster resolver with token extraction (`b163637`)
- Add SuperVideo hoster resolver with XFS video extraction (`d980ebe`)
- Add DoodStream hoster resolver with pass_md5 extraction (`5ba3a58`)
- Add Filemoon hoster resolver with packed JS unpacker (`e9353f3`)
- Add Filemoon Byse SPA API extraction and challenge/attest/decrypt flow (`ad62013`, `8592356`)
- Add packed JS decoder for SuperVideo video URL extraction (`e7baaa6`)
- Add Playwright fallback to SuperVideo for Cloudflare bypass (`7ce90dd`, `4438322`)
- Add 429 rate-limit retry with back-off to SuperVideo resolver (`67babee`)
- Add Mixdrop hoster resolver with token extraction (multi-domain)
- Add VidGuard hoster resolver with multi-domain embed resolution
- Add Vidking hoster resolver with embed page validation
- Add Stmix hoster resolver with embed page validation
- Add SerienStream hoster resolver (s.to / serien.sx domain matching)

**DDL resolvers (15):**
- Add filer.net DDL hoster resolver via public status API
- Add Katfile DDL hoster resolver (XFS offline marker detection)
- Add Rapidgator DDL hoster resolver (website scraping validation)
- Add DDownload DDL hoster resolver (ddownload.com / ddl.to, XFS page check)
- Add Alfafile DDL hoster resolver (page scraping)
- Add AlphaDDL hoster resolver (page scraping)
- Add Fastpic image host resolver (fastpic.org / fastpic.ru)
- Add Filecrypt container resolver (container validation)
- Add FileFactory DDL hoster resolver (page scraping)
- Add FSST hoster resolver (page scraping)
- Add Go4up mirror link resolver (mirror link validation)
- Add Nitroflare DDL hoster resolver (page scraping)
- Add 1fichier DDL hoster resolver (multi-domain page scraping)
- Add Turbobit DDL hoster resolver (multi-domain page scraping)
- Add Uploaded DDL hoster resolver (uploaded.net / ul.to)

**XFS consolidation (27 hosters):**
- Add generic `XFSResolver` with `XFSConfig` dataclass consolidating 27 XFS hosters into one module (`xfs.py`)
- Original 15: Katfile, Hexupload, Clicknupload, Filestore, Uptobox, Funxd, Bigwarp, Dropload, Goodstream, Savefiles, Streamwish (9 domains), Vidmoly, Vidoza, Vinovo, Vidhide (6 domains)
- Added 12 more: Mp4Upload, Uqload, Vidshar, Vidroba, Hotlink, Vidspeed, StreamRuby, Veev, Lulustream, Upstream, Wolfstream, Vidnest
- Parameterised tests auto-generated from all 27 configs
- Delete 15 individual resolver files + 15 individual test files (~4,200 lines removed)

**Test improvements:**
- Migrate all 17 non-XFS resolver test files from AsyncMock to respx (httpx-native HTTP mocking)
- Add live contract test skeleton for resolver smoke tests (`tests/live/test_resolver_live.py`)

### Plugin Improvements
Various fixes and enhancements to individual plugins.

- Rewrite kinoger search parser for redesigned site template (`3cf475c`)
- Rewrite streamworld plugin from httpx to Playwright mode (`de29957`)
- Rewrite moflix plugin from httpx to Playwright mode (`eaa0002`)
- Fix streamkiste parser to handle `<span class="movie-title">` tags (`bea8be1`)
- Fix sto plugin to reject non-TV categories (TV-only site) (`2a73f16`)
- Fix filmpalast.to plugin selectors and change provides to stream (`dfc48a3`)
- Fix animeloads DDoS-Guard detection excludes h1 selector (`08cced5`)
- Optimize sto plugin to fetch only requested episode instead of full season (`bb48c58`)
- Add season/episode filtering to mixed plugins (`9d433f5`, `d109844`, `ea8385f`, `ec643ae`)
- Add `provides` attribute to plugin system (`e38a07b`)
- Add domain fallback to aniworld plugin (`f72fc2d`)
- Add pagination to ddlspot, ddlvalley, scnlog, warezomen, boerse (`21a0657`, `3d80cec`, `a30164b`, `3cfa20f`)
- Add Torznab category filtering for YAML plugins (`5dc3018`)
- Add kinox AJAX embed URL extraction for hoster resolution (`20e40e9`)

### API & Router Improvements
- Centralize `/api/v1/` prefix for all endpoints (`d25ee5c`)
- Rename `main.py` → `app.py`, `cli.py` → `__main__.py` (`b25bf5c`)
- Delegate router to use cases, remove inline business logic (`7c36166`)
- Wire Stremio use cases into AppState and composition (`d3f076d`)

### Search Result Caching
Cache layer for repeated search queries with configurable TTL and cache-hit indicators.

- Add `_search_cache_key()` with SHA-256 hashing of plugin + query + category
- Add cache read/write to `TorznabSearchUseCase` with graceful error handling
- Add `search_ttl_seconds` config (default 900s / 15 minutes, 0 = disabled)
- Add `X-Cache: HIT/MISS` response header to Torznab search responses

### Plugin Fixes (website changes)
Five plugins updated to match changed website structures.

- Fix filmfans release loading: extract `initMovie()` hash and fetch releases via `/api/v1/{hash}` JSON endpoint
- Fix kinoger search parser: update selectors for redesigned DLE template (`shortstory` → detail link extraction)
- Fix megakino_to: add GET fallback for domain verification (HEAD returns 405)
- Fix movie4k: add GET fallback for domain verification (HEAD returns 405)
- Fix streamkiste: rewrite detail parser to extract streams from meinecloud.click external script

### Test Suite Growth (160 → 3963 tests)
Test suite expanded from 160 to 3963 tests (3742 unit + 158 E2E + 25 integration + 38 live) with comprehensive coverage across all layers.

- Add unit tests for all 42 plugin test files
- Add unit tests for all 56 hoster resolvers (17 individual + 12 generic DDL + 27 XFS consolidated)
- Add unit tests for HttpxPluginBase and PlaywrightPluginBase
- Add unit tests for Stremio components (stream converter, stream sorter, TMDB client, title matcher, IMDB fallback)
- Add unit tests for release name parser, plugin registry, HTML selectors
- Add unit tests for stream link cache and hoster registry
- Add unit tests for circuit breaker, concurrency pool, graceful shutdown, metrics endpoint
- Add unit tests for EWMA scoring, plugin score cache, query pool, health prober, search prober, scoring scheduler
- Add 158 E2E tests (46 Torznab endpoint + 112 Stremio endpoint including 31 streamable link tests)
- Add 25 integration tests (config loading, crawljob lifecycle, link validation)
- Add 38 live smoke tests (plugin smoke tests + resolver contract tests)
- Migrate all resolver tests from AsyncMock to respx (httpx-native HTTP mocking)
- Add parameterised XFS resolver tests auto-generated from 27 configs

### Documentation
- Add plugin search standards (categories + pagination up to 1000) (`c1fa2c4`)
- Update agent policy — only for simple mechanical tasks (`e01246c`)
- Add team agents rules to CLAUDE.md (`2a0eddc`)
- Restructure documentation following MasterSelects pattern (`dea6fad`)

---

## v0.1.0 - 2025-XX-XX (Initial Release)

First release of Scavengarr as a self-hosted Torznab/Newznab indexer. Includes the core scraping pipeline, plugin system (YAML + Python), Torznab API, CrawlJob packaging, link validation, and a comprehensive unit test suite.

### Boerse Plugin Rewrite
Complete rewrite of the boerse.sx plugin to handle the real site structure, including Cloudflare JS challenge bypass via Playwright and vBulletin form-based authentication.

- Rewrite boerse.py plugin with Playwright for Cloudflare JS challenge bypass (`502c2b7`)
- Rewrite login, search, and link extraction to match real vBulletin site structure (`7e41ad3`)
- Resolve nested `<div>` parsing bug in post content and use full `#searchform` (`9bfecf6`)
- Filter download links to known container hosts only (keeplinks.org, filecrypt.cc, etc.), deduplicate thread URLs by thread ID (`533f6aa`)
- Read boerse credentials lazily in `_ensure_session()` to avoid startup failures when env vars are not yet set (`e090033`)

### Mirror URL Fallback
Automatic domain failover for plugins with multiple mirror URLs. When the primary domain is unreachable, the system probes mirrors and falls back transparently.

- Add `mirror_urls` field to YAML plugin schema for declaring alternative domains (`1591072`)
- Add mirror domain fallback to ScrapyAdapter: probe mirrors on connection failure (`9543e2c`)
- Probe mirror URLs in health endpoint when primary domain is unreachable (`b1b8901`)
- Merge `mirror_urls` into `base_url` as a single-or-list field for simpler plugin config (`4eab6b5`)

### Multi-Link CrawlJob Packaging
CrawlJob system extended to bundle multiple validated download links from different hosters into a single `.crawljob` file, with automatic promotion of alternatives when primary links are dead.

- Multi-link CrawlJob packaging: bundle all valid hoster URLs into a single `.crawljob` artifact (`35326b7`)
- Promote alternative download links when primary link fails HEAD/GET validation (`078fcae`)

### Python Plugin System
New imperative plugin type for sites that require complex logic beyond what YAML selectors can express (authentication, JavaScript interaction, custom parsing).

- Add boerse.sx Python plugin with domain fallback across 5 mirrors and anonymizer link handling (`bf0a9d3`)
- Add Python plugin dispatch to TorznabSearchUseCase: detect `.py` plugins and call their `search()` method (`98e6081`)
- Add env var support to AuthConfig for YAML plugin credentials: `$ENV{VAR_NAME}` syntax (`5b53f00`)
- Align `PluginRegistryPort.get()` return type with concrete registry implementation (`a73c6b9`)

### Link Validation
HTTP-based link validation with parallel execution, HEAD-first strategy, and GET fallback for hosters that block HEAD requests.

- Add GET fallback to HttpLinkValidator for hosters that return 403/405 on HEAD requests (`e69cd54`)
- Add `validate_results()` method to SearchEnginePort protocol for post-search filtering (`d7d1dab`)

### Test Suite
Comprehensive unit test suite covering all three architecture layers with proper mock patterns (sync MagicMock for PluginRegistryPort, AsyncMock for async ports).

- Add comprehensive unit test suite: 160+ tests across domain, application, and infrastructure (`e0674c5`)
  - Domain: CrawlJob entity, TorznabQuery/Item/Caps, SearchResult, plugin schema validation
  - Application: CrawlJobFactory, Torznab caps/indexers/search use cases
  - Infrastructure: parsers, converters, extractors, presenter, link validator, search engine, cache
- Apply ruff format to test files for consistent style (`2040852`)

### Clean Architecture Refactor
Three-phase migration from flat codebase to Clean Architecture with Domain, Application, Infrastructure, and Interfaces layers. See `docs/refactor/COMPLETED/clean-architecture-migration.md` for full details.

**Phase 1: Domain layer cleanup**
- Remove Pydantic from Domain layer, convert all entities to `@dataclass` (`7726ba8`)

**Phase 2: Entity consolidation**
- Consolidate SearchResult definition into single canonical location (`b7bc0be`)

**Phase 3: Adapter reorganization**
- Reorganize all adapters into `infrastructure/` namespace by concern (`d97d7a3`)

**Follow-up commits:**
- Move presenter to infrastructure layer (`8729319`)
- Rename `httpx_scrapy_engine` to `search_engine` for clarity (`56b48df`)
- Rename cache factory for naming consistency (`d32066c`)
- Use shared size parser across layers, eliminating duplication (`de788dc`)
- Consolidate duplicate int parsing into `infrastructure/common/` utils (`7610b9b`)
- Add common utils structure: parsers, converters, extractors (`b0f4cca`)
- Move composition root from application to interfaces layer (correct placement) (`a9eab40`)
- Remove redundant `discover()` calls from use cases and router (`028c932`)
- Parallelize multi-stage scraping with `asyncio.gather` for non-blocking I/O (`f419b5e`)
- Prevent duplicate search results from multi-stage scraping via dedup logic (`6b7fd8d`)

### Code Quality
Codebase-wide standardization of typing patterns, docstring conventions, and language consistency.

- Standardize typing to modern Python 3.10+ syntax (`T | None`, `list[T]`, `dict[K, V]`) and replace ABC with Protocol across all ports (`84995a1`)
- Standardize docstrings: remove redundant comments, ensure consistent English documentation (`2c6278a`)
- Translate all remaining German comments and docstrings to English for international consistency (`04f4b81`, `bcfe059`, `b18711e`, `4e440d7`)
- Apply pre-commit auto-fixes: trailing whitespace, end-of-file, import sorting (`dccc4ad`, `0e6c937`)

### Documentation
Project documentation covering architecture, coding standards, plugin system, and test suite organization.

- Add comprehensive project documentation covering all architecture layers (`d1d4a56`)
- Add typing standards and test suite information to CLAUDE.md (`9c7106a`)
- Document all infrastructure components and their responsibilities in CLAUDE.md (`9abf1c3`)

### Core Infrastructure (Initial)
Foundation of the project: FastAPI server, Scrapy scraping engine, plugin loader, configuration system, and CrawlJob generation.

- Initial content commit: FastAPI/Uvicorn server, Scrapy-based scraping, Playwright integration, structlog logging, diskcache backend (`7fd6747`)
- Add YAML configuration system with pydantic-settings and plugin loader with filesystem discovery (`3889847`)
- Add CrawlJob system for `.crawljob` file generation and assorted bug fixes (`df3e952`)
- Refactoring: improve module structure, separate concerns, clean up imports (`ed53426`)

---

## KNOWN_ISSUES

Current known issues:

- **11 httpx plugins return 0 results** (live smoke tests, 2026-09-28): filmfans, kinoger, serienfans (Cloudflare 403), fireani, nox (search API 404), dataload (search form 400), scnlog, kinoking (selectors/search), hdfilme (domain moved), streamcloud, streamkiste (stream source changed). Triage and fix plan: `docs/plans/plugin-repair.md`.
- **Cloudflare-heavy sites:** ddlspot, ddlvalley, scnsrc (Playwright) and filmfans, kinoger, serienfans (httpx) sit behind an interactive Cloudflare Turnstile challenge that no headless browser passes (Playwright + stealth, Patchright, Camoufox, nodriver tested 2026-09-28). Headful Patchright under Xvfb with one checkbox click passes all six; the fix is planned in `docs/plans/antibot-patchright.md` (Phase 1 + 2). byte is not Cloudflare-blocked; its 0 results are a parser issue (`docs/plans/plugin-repair.md`).
