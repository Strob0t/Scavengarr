[← Back to Index](./README.md)

# Configuration

> Layered configuration (CLI, environment, YAML, defaults) with strict precedence and Pydantic validation.

---

## Precedence

Each layer only contributes values that are explicitly set — unset values fall through to the next layer.

```text
CLI arguments         (--plugin-dir, --log-level, --log-format)
        ↓
Environment variables (SCAVENGARR_*, including values loaded from --dotenv)
        ↓
YAML config file      (--config or SCAVENGARR_CONFIG)
        ↓
Defaults              (src/scavengarr/infrastructure/config/defaults.py + schema.py)
```

Higher-precedence values override lower ones. For example, setting `SCAVENGARR_LOG_LEVEL=DEBUG` overrides whatever the YAML file or defaults specify, but does not affect other settings.

A `.env` file (`--dotenv`) is loaded into the process environment before the environment layer is read, so its values have **environment-variable precedence** (above YAML). It never overrides variables that are already set in the real environment.

`HOST` and `PORT` are not part of the merged configuration: the CLI reads them directly (see [Server Variables](#server-variables)).

### How Merging Works

The configuration loader (`load_config()` in `src/scavengarr/infrastructure/config/load.py`) normalizes each layer into a canonical sectioned dictionary and performs a recursive deep merge:

1. Load the `.env` file into the process environment (if `--dotenv` is given; existing variables are kept).
1. Start with hardcoded defaults (`DEFAULT_CONFIG`).
1. Deep-merge the YAML config (if a config path is given).
1. Deep-merge environment variable overrides (`SCAVENGARR_*`, read by `EnvOverrides`).
1. Deep-merge CLI argument overrides.
1. Validate the merged result with Pydantic (`AppConfig`).

This means you can have a base YAML config and override individual fields via environment variables without repeating the entire configuration. `data/config.yaml` is a complete, commented example of every YAML key.

---

## CLI Arguments

Start the server with optional configuration overrides:

```bash
poetry run start [OPTIONS]
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--host` | string | `HOST` env, else `0.0.0.0` | Bind address |
| `--port` | int | `PORT` env, else `7979` | Bind port |
| `--config` | path | `SCAVENGARR_CONFIG` env, else none | Path to YAML config file |
| `--dotenv` | path | — | Path to `.env` file |
| `--plugin-dir` | path | — | Plugin directory override |
| `--log-level` | choice | — | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `--log-format` | choice | — | `json` or `console` |

Flags without a default fall through to the lower layers. A `--config` or `--dotenv` path that does not exist aborts startup with `FileNotFoundError`.

**Examples:**

```bash
# Development with debug logging
poetry run start --log-level DEBUG --log-format console

# Production with config file
poetry run start --config /app/config.yaml --log-level INFO

# Override plugin directory
poetry run start --plugin-dir /custom/plugins
```

---

## Environment Variables

### Config File Variable

| Variable | Type | Default | Description |
|---|---|---|---|
| `SCAVENGARR_CONFIG` | path | — | YAML config path, used when `--config` is not given. If the file does not exist, a `config_file_not_found` warning is logged and defaults are used. |

### Application Variables (SCAVENGARR_ prefix)

These variables are read by the `EnvOverrides` Pydantic Settings model (case-insensitive). Unknown `SCAVENGARR_*` variables are silently ignored.

| Variable | Type | Default | Maps to |
|---|---|---|---|
| `SCAVENGARR_APP_NAME` | string | `scavengarr` | `app_name` |
| `SCAVENGARR_ENVIRONMENT` | string | `dev` | `environment` (`dev`, `test`, `prod`) |
| `SCAVENGARR_PLUGIN_DIR` | path | `./plugins` | `plugins.plugin_dir` |
| `SCAVENGARR_HTTP_TIMEOUT_SECONDS` | float | `30.0` | `http.timeout_seconds` |
| `SCAVENGARR_HTTP_TIMEOUT_RESOLVE_SECONDS` | float | `15.0` | `http.timeout_resolve_seconds` |
| `SCAVENGARR_HTTP_FOLLOW_REDIRECTS` | bool | `true` | `http.follow_redirects` |
| `SCAVENGARR_HTTP_USER_AGENT` | string | `Scavengarr/0.1.0` | `http.user_agent` |
| `SCAVENGARR_RATE_LIMIT_REQUESTS_PER_SECOND` | float | `5.0` | `http.rate_limit_rps` |
| `SCAVENGARR_RATE_LIMIT_ADAPTIVE` | bool | `true` | `http.rate_limit_adaptive` |
| `SCAVENGARR_RATE_LIMIT_MIN_RPS` | float | `0.5` | `http.rate_limit_min_rps` |
| `SCAVENGARR_RATE_LIMIT_MAX_RPS` | float | `50.0` | `http.rate_limit_max_rps` |
| `SCAVENGARR_HTTP_RETRY_MAX_ATTEMPTS` | int | `3` | `http.retry_max_attempts` |
| `SCAVENGARR_HTTP_RETRY_BACKOFF_BASE` | float | `1.0` | `http.retry_backoff_base` |
| `SCAVENGARR_HTTP_RETRY_MAX_BACKOFF` | float | `30.0` | `http.retry_max_backoff` |
| `SCAVENGARR_API_RATE_LIMIT_RPM` | int | `120` | `http.api_rate_limit_rpm` |
| `SCAVENGARR_PLAYWRIGHT_HEADLESS` | bool | `false` | `playwright.headless` |
| `SCAVENGARR_PLAYWRIGHT_BROWSER_FALLBACK` | bool | `true` | `playwright.browser_fallback` |
| `SCAVENGARR_PLAYWRIGHT_SOLVER_URL` | string | (unset) | `playwright.solver_url` |
| `SCAVENGARR_PLAYWRIGHT_TIMEOUT_MS` | int | `30000` | `playwright.timeout_ms` (currently unused, no effect) |
| `SCAVENGARR_LOG_LEVEL` | string | `INFO` | `logging.level` |
| `SCAVENGARR_LOG_FORMAT` | string | (auto) | `logging.format` |
| `SCAVENGARR_CACHE_DIR` | path | `./.cache/scavengarr` | `cache.dir` |
| `SCAVENGARR_CACHE_TTL_SECONDS` | int | `3600` | `cache.ttl_seconds` |
| `SCAVENGARR_CACHE_BACKEND` | string | `diskcache` | `cache.backend` (`diskcache`, `redis`) |
| `SCAVENGARR_CACHE_REDIS_URL` | string | `redis://localhost:6379/0` | `cache.redis_url` (only for `backend: redis`) |
| `SCAVENGARR_CACHE_MAX_CONCURRENT` | int | `10` | `cache.max_concurrent` |
| `SCAVENGARR_TMDB_API_KEY` | string | — | `tmdb_api_key` |
| `SCAVENGARR_SCORING_ENABLED` | bool | `true` | `scoring.enabled` |
| `SCAVENGARR_SCORING_W_HEALTH` | float | `0.4` | `scoring.w_health` |
| `SCAVENGARR_SCORING_W_SEARCH` | float | `0.6` | `scoring.w_search` |

All other settings (including the whole `stremio:` section and the link validation keys) are YAML-only. Unprefixed `CACHE_*` variables are not read; use the `SCAVENGARR_CACHE_*` names above.

### Plugin Credentials

Plugins that require a login read their credentials directly from the environment (not from YAML).

| Variable | Plugin |
|---|---|
| `SCAVENGARR_BOERSE_USERNAME`, `SCAVENGARR_BOERSE_PASSWORD` | `boerse` |
| `SCAVENGARR_MYBOERSE_USERNAME`, `SCAVENGARR_MYBOERSE_PASSWORD` | `myboerse` |
| `SCAVENGARR_MYGULLY_USERNAME`, `SCAVENGARR_MYGULLY_PASSWORD` | `mygully` |
| `SCAVENGARR_DATALOAD_USERNAME`, `SCAVENGARR_DATALOAD_PASSWORD` | `dataload` |
| `SCAVENGARR_ANIMELOADS_USERNAME`, `SCAVENGARR_ANIMELOADS_PASSWORD` | `animeloads` (optional: whole releases with one captcha per grab; without them only releases up to 13 episodes, one captcha per episode) |

### Server Variables

Server bind address and port. These do not use the `SCAVENGARR_` prefix and are read by the CLI before configuration loading. `--host`/`--port` take precedence.

| Variable | Type | Default | Description |
|---|---|---|---|
| `HOST` | string | `0.0.0.0` | Server bind address |
| `PORT` | int | `7979` | Server bind port |

`HOST`/`PORT` may come from the real environment or a `--dotenv` file (the real environment wins); `--host`/`--port` take precedence over both.

---

## YAML Configuration

Pass a YAML file via `--config` (or `SCAVENGARR_CONFIG`). The file uses a sectioned structure that maps directly to the internal configuration model. Example (simplified; see `data/config.yaml` for every key):

```yaml
app_name: "scavengarr"
environment: "prod"
tmdb_api_key: ""                # optional; IMDB fallback without it

plugins:
  plugin_dir: "/app/plugins"
  overrides: {}                 # per-plugin overrides, see Plugins below

http:
  timeout_seconds: 15.0         # scraping timeout (default: 30)
  timeout_resolve_seconds: 10.0 # hoster resolution timeout (default: 15)
  follow_redirects: true
  user_agent: "Scavengarr/0.1.0 (+https://github.com/Strob0t/Scavengarr)"
  rate_limit_rps: 10.0          # per-domain rate limit (default: 5)
  rate_limit_adaptive: true     # AIMD: rate grows on success, halves on 429/503
  rate_limit_min_rps: 0.5       # adaptive lower bound per domain
  rate_limit_max_rps: 50.0      # adaptive upper bound per domain
  api_rate_limit_rpm: 120       # per-IP API limit (0 = unlimited)
  retry_max_attempts: 2         # retries on 429/503 (default: 3)
  retry_backoff_base: 0.5       # initial backoff (default: 1.0)
  retry_max_backoff: 10.0       # max backoff (default: 30)

validate_download_links: true
validation_timeout_seconds: 3.0 # default: 5.0
validation_max_concurrent: 30   # default: 20 (auto-tuned when stremio.auto_tune_all)

playwright:
  headless: false               # headful under Xvfb, headless fallback without DISPLAY
  browser_fallback: true        # httpx plugins: Cloudflare pages via browser
  # solver_url: http://byparr:8191  # optional Byparr/FlareSolverr sidecar

stremio:
  auto_tune_all: true           # container-aware auto-tune of concurrency params
  max_results_per_plugin: 50    # default: 100
  plugin_timeout_seconds: 10.0  # search budget from request start (default: 10)
  stream_deadline_seconds: 15.0 # answer budget per stream request (default: 15)
  title_match_threshold: 0.7
  resolve_target_count: 0       # 0 = resolve all streams (default: 15)
  max_probe_count: 80           # default: 50

scoring:
  enabled: true
  w_health: 0.4
  w_search: 0.6

logging:
  level: "INFO"
  format: "json"                # "json" or "console"; derived from environment if omitted

cache:
  backend: "diskcache"          # "diskcache" or "redis"
  dir: "/app/cache"
  ttl_seconds: 3600
  search_ttl_seconds: 1800      # default: 900
  crawljob_ttl_seconds: 3600    # how long a grab stays downloadable
  # redis_url: "redis://redis:6379/0"   # when backend: redis
```

### Canonical Section Keys

The loader recognizes the sections `plugins`, `http`, `playwright`, `logging`, `cache`, `stremio`, and `scoring`, plus the top-level keys `app_name`, `environment`, `tmdb_api_key`, `validate_download_links`, `validation_timeout_seconds`, and `validation_max_concurrent`. Flat keys (from env/CLI) are mapped to their sectioned equivalents:

| Flat key (env/CLI) | Sectioned key (YAML) |
|---|---|
| `plugin_dir` | `plugins.plugin_dir` |
| `http_timeout_seconds` | `http.timeout_seconds` |
| `http_timeout_resolve_seconds` | `http.timeout_resolve_seconds` |
| `http_follow_redirects` | `http.follow_redirects` |
| `http_user_agent` | `http.user_agent` |
| `rate_limit_requests_per_second` | `http.rate_limit_rps` |
| `rate_limit_adaptive`, `rate_limit_min_rps`, `rate_limit_max_rps` | `http.rate_limit_adaptive`, `http.rate_limit_min_rps`, `http.rate_limit_max_rps` |
| `http_retry_max_attempts`, `http_retry_backoff_base`, `http_retry_max_backoff` | `http.retry_max_attempts`, `http.retry_backoff_base`, `http.retry_max_backoff` |
| `api_rate_limit_rpm` | `http.api_rate_limit_rpm` |
| `playwright_headless` | `playwright.headless` |
| `playwright_browser_fallback` | `playwright.browser_fallback` |
| `playwright_solver_url` | `playwright.solver_url` |
| `playwright_timeout_ms` | `playwright.timeout_ms` |
| `log_level` | `logging.level` |
| `log_format` | `logging.format` |
| `cache_dir` | `cache.dir` |
| `cache_ttl_seconds` | `cache.ttl_seconds` |
| `cache_backend`, `cache_redis_url`, `cache_max_concurrent` | `cache.backend`, `cache.redis_url`, `cache.max_concurrent` |
| `scoring_enabled`, `scoring_w_health`, `scoring_w_search` | `scoring.enabled`, `scoring.w_health`, `scoring.w_search` |

Other flat keys are dropped.

---

## Configuration Sections

Defaults are defined in `defaults.py` and mirrored by the Pydantic field defaults in `schema.py`; a test keeps both in sync.

### General

| Key | Type | Default | Description |
|---|---|---|---|
| `app_name` | string | `scavengarr` | Application name (used in Torznab XML titles) |
| `environment` | string | `dev` | Runtime environment: `dev`, `test`, or `prod` |
| `tmdb_api_key` | string | — | TMDB API key for Stremio catalog and title lookup. Without it, the IMDB Suggest API fallback is used. |

The `environment` setting controls several behavioral defaults (see [Environment-Specific Behavior](#environment-specific-behavior) below).

### Plugins

| Key | Type | Default | Description |
|---|---|---|---|
| `plugins.plugin_dir` | path | `./plugins` | Directory containing Python plugin files |
| `plugins.overrides.<name>.timeout` | float | — | Override the plugin timeout (seconds) |
| `plugins.overrides.<name>.max_concurrent` | int | — | Override the plugin's max concurrent requests |
| `plugins.overrides.<name>.max_results` | int | — | Override the plugin's max results |
| `plugins.overrides.<name>.enabled` | bool | `true` | `false` removes the plugin from the registry |

The plugin registry scans the plugin directory at startup for `.py` files. All plugins are imported once during startup wiring and cached in memory. Unknown plugin names in `overrides` are logged as a warning. See [Plugin System](./plugin-system.md) and [Per-Plugin Overrides](./plugin-system.md#per-plugin-overrides) for details.

```yaml
plugins:
  overrides:
    kinoger:
      timeout: 20.0
      max_results: 500
    sto:
      enabled: false
```

### HTTP

Controls the shared HTTP client used by httpx plugins, hoster resolvers, and API requests.

| Key | Type | Default | Description |
|---|---|---|---|
| `http.timeout_seconds` | float | `30.0` | Request timeout for scraping operations |
| `http.timeout_resolve_seconds` | float | `15.0` | Time bound for one hoster resolution (the resolver's whole `resolve()`), and timeout of the registry's redirect and content-type requests |
| `http.follow_redirects` | bool | `true` | Whether the HTTP client follows redirects |
| `http.user_agent` | string | `Scavengarr/0.1.0` | User-Agent header sent with every request |
| `http.rate_limit_rps` | float | `5.0` | Per-domain rate limit (requests/second). 0 = unlimited |
| `http.rate_limit_adaptive` | bool | `true` | Enable AIMD adaptive rate limiting per domain (`SCAVENGARR_RATE_LIMIT_ADAPTIVE`) |
| `http.rate_limit_min_rps` | float | `0.5` | Adaptive lower bound per domain (`SCAVENGARR_RATE_LIMIT_MIN_RPS`) |
| `http.rate_limit_max_rps` | float | `50.0` | Adaptive upper bound per domain (`SCAVENGARR_RATE_LIMIT_MAX_RPS`) |
| `http.api_rate_limit_rpm` | int | `120` | Incoming API requests per client IP per minute (sliding window, HTTP 429 when exceeded). 0 = unlimited. Not counted: the HLS proxy (`/api/v1/stremio/proxy/…`, a playing stream loads a segment every few seconds) and the health endpoints (`healthz`, `readyz`, `stremio/health`) |
| `http.retry_max_attempts` | int | `3` | Max retry attempts on 429/503 responses. 0 = no retries (`SCAVENGARR_HTTP_RETRY_MAX_ATTEMPTS`) |
| `http.retry_backoff_base` | float | `1.0` | Base delay in seconds for exponential backoff (`SCAVENGARR_HTTP_RETRY_BACKOFF_BASE`) |
| `http.retry_max_backoff` | float | `30.0` | Maximum backoff delay in seconds (`SCAVENGARR_HTTP_RETRY_MAX_BACKOFF`) |

**Validation:** `timeout_seconds` and `timeout_resolve_seconds` must be greater than 0.

### Link Validation

Controls the download link validation step. Link validation runs after scraping to filter out dead or blocked links before bundling them into CrawlJobs. These are top-level YAML keys.

| Key | Type | Default | Description |
|---|---|---|---|
| `validate_download_links` | bool | `true` | Enable/disable link validation entirely |
| `validation_timeout_seconds` | float | `5.0` | Timeout per individual link validation (seconds) |
| `validation_max_concurrent` | int | `20` | Maximum parallel link validations (overwritten by auto-tune when `stremio.auto_tune_all` is `true`) |

Setting `validate_download_links` to `false` skips all validation and includes all scraped links directly. This can be useful for debugging but is not recommended in production. See [Link Validation](./link-validation.md) for the full validation strategy.

### Playwright

Controls the Playwright browser engine for JavaScript-heavy sites.

| Key | Type | Default | Description |
|---|---|---|---|
| `playwright.headless` | bool | `false` | `false`: headful when a display exists (`DISPLAY`, e.g. Xvfb), otherwise headless with one `browser_headful_no_display` warning. `true`: always headless |
| `playwright.browser_fallback` | bool | `true` | httpx plugins load pages that answer with a Cloudflare challenge through the shared browser (stealth context, at most `min(stremio.max_concurrent_playwright, 2)` pages at a time). `false`: no browser for httpx plugins; Cloudflare-protected httpx plugins (filmfans, kinoger, serienfans) then return nothing |
| `playwright.solver_url` | string | unset | Base URL of an optional [Byparr](https://github.com/ThePhaseless/Byparr) or FlareSolverr sidecar (FlareSolverr v1 API, e.g. `http://byparr:8191`). Order: own browser (if `browser_fallback`) → solver. With `browser_fallback: false` the solver is used alone, e.g. on hosts without RAM for Chromium. Byparr is recommended: maintained, Firefox-based, no Xvfb needed; FlareSolverr's own README says its captcha solvers do not work |
| `playwright.timeout_ms` | int | `30000` | Currently unused (no effect) |

**Validation:** `timeout_ms` must be greater than 0.

Headful is the default because interactive Cloudflare Turnstile rejects every headless browser while headful Patchright passes (`docs/plans/antibot-patchright.md`). The Docker image starts Xvfb itself; locally, run under `xvfb-run -a` or on a desktop session. Cost (measured 2026-09-28, PSS): Chromium idle 230 → 420 MiB, with 3 pages 451 → 697 MiB, plus about 70 MiB for Xvfb. Set `headless: true` on hosts where that is too much; Cloudflare-protected plugins then fail.

### Stremio

Controls the Stremio addon behavior: stream ranking, plugin concurrency, title matching, hoster probing, and scored plugin selection.

| Key | Type | Default | Description |
|---|---|---|---|
| `stremio.preferred_language` | string | `de` | Currently unused (no effect); ranking uses `language_scores` |
| `stremio.language_scores` | dict | `{de: 1000, de-sub: 500, en-sub: 200, en: 150}` | Language ranking scores (higher = preferred) |
| `stremio.default_language_score` | int | `100` | Score for unknown/undetected languages |
| `stremio.quality_multiplier` | int | `10` | Multiplier for quality value in ranking |
| `stremio.hoster_scores` | dict | `{supervideo: 5, voe: 4, filemoon: 3, streamtape: 2, doodstream: 1}` | Hoster reliability bonus (tie-breaker) |
| `stremio.max_concurrent_plugins` | int | `5` | Max parallel plugin searches (auto-tuned at startup by default) |
| `stremio.max_concurrent_playwright` | int | `5` | Max parallel Playwright plugin searches (auto-tuned at startup by default) |
| `stremio.auto_tune_all` | bool | `true` | Container-aware auto-tune of all concurrency params (cgroup v2/v1) |
| `stremio.max_concurrent_plugins_auto` | bool | `true` | Legacy auto-tune of `max_concurrent_plugins` only; used only when `auto_tune_all` is `false` |
| `stremio.max_results_per_plugin` | int | `100` | Max results per plugin in Stremio search |
| `stremio.plugin_timeout_seconds` | float | `10.0` | Plugin search budget per stream request, counted from the request start (slot queueing included); running plugins are cut, queued ones skipped |
| `stremio.stream_deadline_seconds` | float | `15.0` | Overall budget per stream request; hoster resolution stops here (at least 2 s after the search). Keep it above `plugin_timeout_seconds` |
| `stremio.verify_streams` | bool | `true` | Playback check of every resolved video URL (first bytes with playback headers); unplayable streams are dropped |
| `stremio.title_match_threshold` | float | `0.7` | Minimum title similarity score |
| `stremio.title_year_bonus` | float | `0.2` | Score bonus for matching year |
| `stremio.title_year_penalty` | float | `0.3` | Score penalty for non-matching year |
| `stremio.title_sequel_penalty` | float | `0.35` | Score penalty for sequel number mismatch |
| `stremio.title_year_tolerance_movie` | int | `1` | Allowed year difference for movies (±N) |
| `stremio.title_year_tolerance_series` | int | `3` | Allowed year difference for series (±N) |
| `stremio.stream_link_ttl_seconds` | int | `7200` | TTL for cached stream links (2h) |
| `stremio.probe_concurrency` | int | `10` | Max parallel hoster resolutions (auto-tuned at startup by default) |
| `stremio.max_probe_count` | int | `50` | Max streams to resolve (top-ranked first) |
| `stremio.resolve_target_count` | int | `15` | Stop resolving after this many successes (0 = disabled) |
| `stremio.probe_stealth_timeout_seconds` | float | `15.0` | Page timeout of the stealth browser (Patchright): browser-based resolvers and the Cloudflare fallback |

`stremio.probe_at_stream_time`, `probe_timeout_seconds`, `probe_stealth_enabled` and `probe_stealth_concurrency` were removed with the unused stream-time liveness probe; configs that still set them load (the keys are ignored).

**Scored plugin selection** (requires `scoring.enabled: true`):

| Key | Type | Default | Description |
|---|---|---|---|
| `stremio.scoring_enabled` | bool | `false` | Use scores to limit plugin selection (YAML-only) |
| `stremio.max_plugins_scored` | int | `5` | Top-N plugins when scoring is active |
| `stremio.exploration_probability` | float | `0.15` | Chance to include a random mid-score plugin |
| `stremio.stremio_deadline_ms` | int | `2000` | Currently unused (no effect); the request budget is `stremio.stream_deadline_seconds` |
| `stremio.max_items_total` | int | `50` | Currently unused (no effect) |
| `stremio.max_items_per_plugin` | int | `20` | Currently unused (no effect) |

See [Stremio Addon](./stremio-addon.md) for the full feature description and [Plugin Scoring & Probing](./plugin-scoring-and-probing.md) for the scoring system.

### Concurrency Pool

The global concurrency pool distributes httpx and Playwright slots across concurrent requests using fair-share scheduling.

| Key | Type | Default | Description |
|---|---|---|---|
| `stremio.max_concurrent_plugins` | int | `5` | Total httpx concurrency slots (shared globally) |
| `stremio.max_concurrent_playwright` | int | `5` | Total Playwright concurrency slots |

The pool is created at composition time. Each concurrent request gets a fair share: `max(1, httpx_slots // active_requests)` httpx permits and `max(1, pw_slots // active_requests)` Playwright permits.

### Auto-Tune (Container-Aware)

When `stremio.auto_tune_all` is `true` (default), four concurrency parameters are derived from detected container/host resources at startup. Manual values in the config file for these four parameters are overwritten.

| Parameter | Formula | Min | Max (hard cap) |
|---|---|---|---|
| `stremio.max_concurrent_plugins` | `min(cpu * 3, mem_gb * 2)` | 2 | 30 |
| `stremio.max_concurrent_playwright` | `min(cpu, mem_gb / 0.15)` | 1 | 10 |
| `stremio.probe_concurrency` | `cpu * 4` | 4 | 100 |
| `validation_max_concurrent` | `cpu * 5` | 5 | 120 |

Hard caps for `probe_concurrency` and `validation_max_concurrent` are derived from synthetic benchmark diminishing-returns analysis (`tests/benchmark/`): throughput gains drop below 5% beyond these thresholds.

Resource detection order: cgroup v2 → cgroup v1 → `os.cpu_count()` for CPU and `psutil` for memory. `psutil` is not a project dependency; without it, memory falls back to 4 GB. See `src/scavengarr/infrastructure/resource_detector.py`.

When `auto_tune_all` is `false` and `max_concurrent_plugins_auto` is `true`, only `max_concurrent_plugins` is tuned: `max(2, min(cpu, available_ram_gb * 2, 20))` (RAM term is 8 without `psutil`).

### Scoring

Controls the background plugin scoring and probing system. See [Plugin Scoring & Probing](./plugin-scoring-and-probing.md) for the full architecture and data model.

| Key | Type | Default | Description |
|---|---|---|---|
| `scoring.enabled` | bool | `true` | Enable background probing |
| `scoring.health_halflife_days` | float | `2.0` | Health EWMA half-life (days) |
| `scoring.search_halflife_weeks` | float | `2.0` | Search EWMA half-life (weeks) |
| `scoring.health_interval_hours` | float | `24.0` | Hours between health probes |
| `scoring.search_runs_per_week` | int | `2` | Search probes per week |
| `scoring.health_timeout_seconds` | float | `5.0` | Health probe timeout |
| `scoring.search_timeout_seconds` | float | `10.0` | Search probe timeout |
| `scoring.search_max_items` | int | `20` | Max items per search probe |
| `scoring.health_concurrency` | int | `5` | Parallel health probes |
| `scoring.search_concurrency` | int | `3` | Parallel search probes |
| `scoring.score_ttl_days` | int | `30` | Score expiry (days) |
| `scoring.w_health` | float | `0.4` | Health weight in composite score |
| `scoring.w_search` | float | `0.6` | Search weight in composite score |

### Logging

| Key | Type | Default | Description |
|---|---|---|---|
| `logging.level` | string | `INFO` | Minimum log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `logging.format` | string | (auto) | Log renderer: `json` or `console` |

**Auto-derived format:** When `logging.format` is not explicitly set, it is derived from the `environment` setting:

- `dev` / `test` → `console` (human-readable, colored output)
- `prod` → `json` (machine-parseable, suitable for log aggregation)

Logs are structured via `structlog` with ISO UTC timestamps and include context fields such as `plugin`, `query`, and result counts. Secrets are masked in every string field, rendered tracebacks included (`_redact_secrets`): values of `api_key`/`apikey`, `access_token`, `token`, `password`/`passwd` and `secret` parameters (TMDB key in retry and error URLs, the Torznab `apikey` in request logs) and passwords in URLs (`redis://:***@redis:6379/0`).

**Console format example (simplified):**

```text
2025-01-01T12:00:00Z [info] search_cache_hit plugin=filmpalast query=iron man result_count=5
2025-01-01T12:00:02Z [info] torznab_search_no_results plugin=filmpalast query=foo
```

**JSON format example (simplified):**

```json
{"timestamp": "2025-01-01T12:00:00Z", "level": "info", "event": "search_cache_hit", "plugin": "filmpalast", "query": "iron man", "result_count": 5}
```

### Cache

| Key | Type | Default | Description |
|---|---|---|---|
| `cache.backend` | string | `diskcache` | Backend: `diskcache` (SQLite-based) or `redis` (`SCAVENGARR_CACHE_BACKEND`) |
| `cache.dir` | path | `./.cache/scavengarr` | SQLite database path (diskcache only) |
| `cache.redis_url` | string | `redis://localhost:6379/0` | Redis connection URL (redis only, `SCAVENGARR_CACHE_REDIS_URL`) |
| `cache.ttl_seconds` | int | `3600` | Default time-to-live for cache entries (seconds) |
| `cache.search_ttl_seconds` | int | `900` | TTL for cached search results (seconds). 0 = disabled (YAML-only) |
| `cache.crawljob_ttl_seconds` | int | `3600` | How long a Torznab result's CrawlJob stays downloadable (seconds, > 0); the grab answers 404 afterwards (YAML-only) |
| `cache.max_concurrent` | int | `10` | Semaphore limit for parallel cache operations, both backends (`SCAVENGARR_CACHE_MAX_CONCURRENT`); Redis handles more, e.g. `50` |

The top-level key `cache_dir` also exists in the schema but is currently unused (no effect); only `cache.dir` is used.

**Validation:** `ttl_seconds` must be >= 0 (0 disables expiration).

The cache stores CrawlJobs, search results, stream links, plugin scores, and other intermediate data. Diskcache is the default and requires no external services. Redis can be used for shared state across multiple instances.

**Diskcache setup (default):**

```yaml
cache:
  backend: "diskcache"
  dir: "/app/cache"
  ttl_seconds: 3600
```

**Redis setup:**

```yaml
cache:
  backend: "redis"
  redis_url: "redis://redis:6379/0"
  ttl_seconds: 3600
```

---

## Environment-Specific Behavior

The `environment` setting (`dev`, `test`, or `prod`) controls several runtime behaviors:

| Aspect | `dev` | `test` | `prod` |
|---|---|---|---|
| Default log format | `console` | `console` | `json` |
| Cache cleared on startup | yes | no | no |
| Error descriptions in Torznab XML | yes | yes | no (empty RSS) |
| Upstream (502) / internal (500) error status | actual | actual | `200` (stable for Prowlarr) |
| Client errors (400/404/422/503) | actual | actual | actual |

### Production Mode

In production, Torznab endpoints return empty RSS feeds with HTTP 200 for upstream and internal errors. This prevents Prowlarr from marking the indexer as permanently failed due to transient issues. Error details are logged server-side but not exposed in the XML response.

### Development Mode

In development, actual HTTP status codes are returned and error descriptions are included in the RSS `<description>` element. The cache is cleared on startup to avoid stale data during development.

---

## Validation Rules

The configuration model enforces these validation rules:

| Field | Rule |
|---|---|
| `http.timeout_seconds`, `http.timeout_resolve_seconds` | Must be > 0 |
| `playwright.timeout_ms` | Must be > 0 |
| `cache.ttl_seconds` | Must be >= 0 |
| `environment` | Must be one of: `dev`, `test`, `prod` |
| `logging.level` | Must be one of: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `logging.format` | Must be one of: `json`, `console` (or unset for auto) |
| `cache.backend` | Must be one of: `diskcache`, `redis` |
| Path fields | `~` is expanded to the home directory |

Invalid configuration causes the application to fail at startup with a descriptive Pydantic validation error.

---

## .env File Support

Scavengarr supports `.env` files for local development. Pass the path via `--dotenv`:

```bash
poetry run start --dotenv .env
```

The `.env` file is loaded with `python-dotenv` (`override=False`) into the process environment before the configuration layers are merged. Its values therefore have environment-variable precedence (above YAML), and variables already set in the real environment always win.

**Example `.env` file:**

```bash
# .env
SCAVENGARR_ENVIRONMENT=dev
SCAVENGARR_PLUGIN_DIR=./plugins
SCAVENGARR_LOG_LEVEL=DEBUG
SCAVENGARR_LOG_FORMAT=console
SCAVENGARR_CACHE_DIR=./.cache/scavengarr
SCAVENGARR_CACHE_TTL_SECONDS=3600
SCAVENGARR_TMDB_API_KEY=your-tmdb-key
```

`HOST`/`PORT` in the `.env` file are honored too (see [Server Variables](#server-variables)).

---

## Docker Configuration

The production image (`Dockerfile.prod`) sets these defaults:

| Variable | Value |
|---|---|
| `SCAVENGARR_ENVIRONMENT` | `prod` |
| `SCAVENGARR_CONFIG` | `/app/config/config.yaml` (copied from `data/config.yaml`) |
| `SCAVENGARR_LOG_LEVEL` | `INFO` |
| `SCAVENGARR_LOG_FORMAT` | `json` |
| `SCAVENGARR_PLUGIN_DIR` | `/app/plugins` |
| `SCAVENGARR_CACHE_DIR` | `/app/cache` |
| `SCAVENGARR_PLAYWRIGHT_HEADLESS` | `false` |
| `HOST` / `PORT` | `0.0.0.0` / `7979` |

The entrypoint is `docker/entrypoint.sh`: it starts Xvfb on `:99` (unless `DISPLAY` is already set; a stale `/tmp/.X99-lock` from before a container restart is removed first, and it waits up to 5 s for the display socket) and then `exec`s `python -m scavengarr.interfaces.cli`, so the app stays the signal recipient (graceful shutdown) and CLI flags can still be appended to `docker run`. Plugins are not bundled in the image.

### Minimal Production

```bash
docker run -d --name scavengarr \
  -p 7979:7979 \
  -v ./plugins:/app/plugins \
  -v ./cache:/app/cache \
  scavengarr:latest
```

### With Config File

```bash
docker run -d --name scavengarr \
  -p 7979:7979 \
  -v ./plugins:/app/plugins \
  -v ./cache:/app/cache \
  -v ./data:/app/config:ro \
  scavengarr:latest
```

### With Redis Cache

Select the Redis backend via environment variables (`SCAVENGARR_CACHE_BACKEND=redis`, `SCAVENGARR_CACHE_REDIS_URL=...`) or a mounted config file containing:

```yaml
cache:
  backend: "redis"
  redis_url: "redis://redis:6379/0"
```

```bash
docker run -d --name scavengarr \
  -p 7979:7979 \
  -v ./plugins:/app/plugins \
  -v ./data:/app/config:ro \
  scavengarr:latest
```

### Volumes

| Container Path | Purpose | Required |
|---|---|---|
| `/app/plugins` | Plugin directory (Python files) | Yes |
| `/app/cache` | Cache storage (diskcache SQLite) | Recommended |
| `/app/config` | Directory containing `config.yaml` | Optional (image ships a default) |

See [Prowlarr Integration](./prowlarr-integration.md) for the complete deployment guide.

---

## Source Code References

| Component | File |
|---|---|
| Configuration schema (Pydantic) | `src/scavengarr/infrastructure/config/schema.py` |
| Configuration loader | `src/scavengarr/infrastructure/config/load.py` |
| Default values | `src/scavengarr/infrastructure/config/defaults.py` |
| CLI entry point | `src/scavengarr/interfaces/cli/__main__.py` |
| Auto-tune + plugin overrides | `src/scavengarr/interfaces/composition.py` |
| Resource detection | `src/scavengarr/infrastructure/resource_detector.py` |
| API rate-limit middleware | `src/scavengarr/interfaces/api/middleware.py` |
| Application state | `src/scavengarr/interfaces/app_state.py` |
| Example config | `data/config.yaml` |
