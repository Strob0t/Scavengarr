[← Back to Index](./README.md)

# Configuration

Scavengarr uses a layered configuration system with strict precedence. Each
layer only contributes values that are explicitly set -- unset values fall
through to the next layer.

---

## Precedence (highest to lowest)

```
CLI arguments         (--plugin-dir, --log-level, ...)
        ↓
Environment variables (SCAVENGARR_*, CACHE_*, HOST, PORT)
        ↓
YAML config file      (--config config.yaml)
        ↓
.env file             (--dotenv .env)
        ↓
Defaults              (hardcoded in code)
```

Higher-precedence values override lower ones. Only explicitly provided values
at each layer participate in the merge. For example, setting
`SCAVENGARR_LOG_LEVEL=DEBUG` overrides whatever the YAML file or defaults
specify, but does not affect other settings.

### How Merging Works

The configuration loader (`load_config()`) normalizes each layer into a
canonical sectioned dictionary and performs a recursive deep merge:

1. Start with hardcoded defaults.
2. Deep-merge YAML config (if provided via `--config`).
3. Deep-merge environment variable overrides (`SCAVENGARR_*`).
4. Deep-merge CLI argument overrides.
5. Validate the merged result with Pydantic (`AppConfig`).

This means you can have a base YAML config and override individual fields via
environment variables without repeating the entire configuration.

---

## CLI Arguments

Start the server with optional configuration overrides:

```bash
poetry run start [OPTIONS]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--host` | string | `0.0.0.0` | Bind address |
| `--port` | int | `7979` | Bind port |
| `--config` | path | -- | Path to YAML config file |
| `--dotenv` | path | -- | Path to `.env` file |
| `--plugin-dir` | path | `./plugins` | Plugin directory override |
| `--log-level` | choice | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `--log-format` | choice | (auto) | `json` or `console` |

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

### Application Variables (SCAVENGARR_ prefix)

All application-level variables use the `SCAVENGARR_` prefix and are
case-insensitive. These are handled by the `EnvOverrides` Pydantic Settings
model, which reads them automatically.

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `SCAVENGARR_APP_NAME` | string | `scavengarr` | Application name (appears in XML titles and logs) |
| `SCAVENGARR_ENVIRONMENT` | string | `dev` | Runtime environment: `dev`, `test`, or `prod` |
| `SCAVENGARR_PLUGIN_DIR` | path | `./plugins` | Directory containing Python plugin files |
| `SCAVENGARR_HTTP_TIMEOUT_SECONDS` | float | `30.0` | HTTP request timeout for scraping operations |
| `SCAVENGARR_HTTP_FOLLOW_REDIRECTS` | bool | `true` | Whether the HTTP client follows redirects |
| `SCAVENGARR_HTTP_USER_AGENT` | string | `Scavengarr/0.1.0 (...)` | User-Agent header for outgoing requests |
| `SCAVENGARR_PLAYWRIGHT_HEADLESS` | bool | `true` | Run Playwright browser in headless mode |
| `SCAVENGARR_PLAYWRIGHT_TIMEOUT_MS` | int | `30000` | Playwright navigation timeout in milliseconds |
| `SCAVENGARR_LOG_LEVEL` | string | `INFO` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `SCAVENGARR_LOG_FORMAT` | string | (auto) | Log format: `json` or `console` (auto-derived from environment) |
| `SCAVENGARR_CACHE_DIR` | path | `./.cache/scavengarr` | Cache directory for disk-based caching |
| `SCAVENGARR_CACHE_TTL_SECONDS` | int | `3600` | Default TTL for cache entries in seconds |

### Stremio & Scoring Variables

Key Stremio and scoring settings are also overridable via environment variables:

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `SCAVENGARR_STREMIO_SCORING_ENABLED` | bool | `false` | Use scoring to limit plugin selection |
| `SCAVENGARR_SCORING_ENABLED` | bool | `false` | Enable background probing |
| `SCAVENGARR_SCORING_W_HEALTH` | float | `0.4` | Health weight in composite score |
| `SCAVENGARR_SCORING_W_SEARCH` | float | `0.6` | Search weight in composite score |

Most Stremio/scoring settings are best configured via the YAML `stremio:` and
`scoring:` sections. See [Stremio](#stremio) and [Scoring](#scoring) below.

### Cache Variables (CACHE_ prefix)

Cache-specific variables use the `CACHE_` prefix. These configure the cache
backend independently of the application settings and are handled by the
`CacheConfig` Pydantic Settings model.

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `CACHE_BACKEND` | string | `diskcache` | Cache backend: `diskcache` (SQLite) or `redis` |
| `CACHE_DIR` | path | `./cache/scavengarr` | Diskcache SQLite database path |
| `CACHE_REDIS_URL` | string | `redis://localhost:6379/0` | Redis connection URL (only when `backend=redis`) |
| `CACHE_TTL_SECONDS` | int | `3600` | Default TTL for cache entries |
| `CACHE_MAX_CONCURRENT` | int | `10` | Maximum parallel cache operations (semaphore limit) |

### Server Variables

Server bind address and port. These do not use the `SCAVENGARR_` prefix.

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `HOST` | string | `0.0.0.0` | Server bind address |
| `PORT` | int | `7979` | Server bind port |

---

## YAML Configuration

Pass a YAML file via the `--config` flag. The file uses a sectioned structure
that maps directly to the internal configuration model.

```yaml
# config.yaml
app_name: "scavengarr"
environment: "prod"

plugins:
  plugin_dir: "/app/plugins"

http:
  timeout_seconds: 15.0         # scraping timeout (schema default: 30)
  timeout_resolve_seconds: 10.0 # hoster resolution timeout (schema default: 15)
  follow_redirects: true
  user_agent: "Scavengarr/0.1.0 (+https://github.com/Strob0t/Scavengarr)"
  rate_limit_rps: 10.0          # per-domain rate limit (schema default: 5)
  rate_limit_adaptive: true     # AIMD: rate grows on success, halves on 429/503
  rate_limit_min_rps: 0.5       # adaptive lower bound per domain
  rate_limit_max_rps: 50.0      # adaptive upper bound per domain
  retry_max_attempts: 2         # retries on 429/503 (schema default: 3)
  retry_backoff_base: 0.5       # initial backoff (schema default: 1.0)
  retry_max_backoff: 10.0       # max backoff (schema default: 30)

playwright:
  headless: true
  timeout_ms: 20000             # page load timeout (schema default: 30000)

stremio:
  max_concurrent_plugins: 15    # parallel plugin searches (schema default: 10)
  max_concurrent_plugins_auto: true
  auto_tune_all: true           # container-aware auto-tune ALL concurrency params
  max_concurrent_playwright: 7  # parallel Playwright searches (schema default: 5)
  max_results_per_plugin: 50    # results per plugin (schema default: 100)
  plugin_timeout_seconds: 15.0  # per-plugin timeout (schema default: 30)
  title_match_threshold: 0.7
  resolve_target_count: 0       # 0 = disabled, resolve all streams (schema default: 15)
  probe_at_stream_time: true
  probe_concurrency: 20         # parallel probes (schema default: 10)
  probe_timeout_seconds: 5.0    # probe timeout (schema default: 10)
  max_probe_count: 80           # streams to probe (schema default: 50)

scoring:
  enabled: false
  health_halflife_days: 2.0
  search_halflife_weeks: 2.0
  w_health: 0.4
  w_search: 0.6

logging:
  level: "INFO"
  format: "json"    # "json" or "console"; auto-derived from environment if omitted

cache:
  backend: "diskcache"           # "diskcache" or "redis"
  dir: "/app/cache/scavengarr"
  ttl_seconds: 3600
  search_ttl_seconds: 1800      # search result cache (schema default: 900)
  # redis_url: "redis://redis:6379/0"  # uncomment when backend=redis
```

### Canonical Section Keys

The configuration loader recognizes these top-level sections. Flat keys (like
`plugin_dir`) are automatically mapped to their sectioned equivalents (like
`plugins.plugin_dir`).

| Flat key (env/CLI) | Sectioned key (YAML) |
|---------------------|----------------------|
| `plugin_dir` | `plugins.plugin_dir` |
| `http_timeout_seconds` | `http.timeout_seconds` |
| `http_timeout_resolve_seconds` | `http.timeout_resolve_seconds` |
| `http_follow_redirects` | `http.follow_redirects` |
| `http_user_agent` | `http.user_agent` |
| `rate_limit_requests_per_second` | `http.rate_limit_rps` |
| `rate_limit_adaptive` | `http.rate_limit_adaptive` |
| `rate_limit_min_rps` | `http.rate_limit_min_rps` |
| `rate_limit_max_rps` | `http.rate_limit_max_rps` |
| `http_retry_max_attempts` | `http.retry_max_attempts` |
| `http_retry_backoff_base` | `http.retry_backoff_base` |
| `http_retry_max_backoff` | `http.retry_max_backoff` |
| `playwright_headless` | `playwright.headless` |
| `playwright_timeout_ms` | `playwright.timeout_ms` |
| `log_level` | `logging.level` |
| `log_format` | `logging.format` |
| `cache_dir` | `cache.dir` |
| `cache_ttl_seconds` | `cache.ttl_seconds` |
| (nested object) | `stremio.*` |
| (nested object) | `scoring.*` |

---

## Configuration Sections

### General

Top-level settings that affect the application globally.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `app_name` | string | `scavengarr` | Application name (used in Torznab XML titles and log context) |
| `environment` | string | `dev` | Runtime environment: `dev`, `test`, or `prod` |

The `environment` setting controls several behavioral defaults (see
[Environment-Specific Behavior](#environment-specific-behavior) below).

### Plugins

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `plugins.plugin_dir` | path | `./plugins` | Directory containing Python plugin files |

The plugin registry scans this directory at startup for `.py` files.
Plugins are loaded lazily on first access and cached in memory. See
[Plugin System](./plugin-system.md) for details.

### HTTP

Controls the HTTP client used by httpx plugins for static HTML pages and API requests.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `http.timeout_seconds` | float | `30.0` | Request timeout for scraping operations |
| `http.timeout_resolve_seconds` | float | `15.0` | Timeout for hoster resolution requests |
| `http.follow_redirects` | bool | `true` | Whether the HTTP client follows redirects |
| `http.user_agent` | string | `Scavengarr/0.1.0 (...)` | User-Agent header sent with every request |
| `http.rate_limit_rps` | float | `5.0` | Per-domain rate limit (requests/second). 0 = unlimited |
| `http.rate_limit_adaptive` | bool | `true` | Enable AIMD adaptive rate limiting per domain |
| `http.rate_limit_min_rps` | float | `0.5` | Adaptive lower bound per domain (rps) |
| `http.rate_limit_max_rps` | float | `50.0` | Adaptive upper bound per domain (rps) |
| `http.api_rate_limit_rpm` | int | `120` | API rate limit per IP (requests/minute). 0 = unlimited |
| `http.retry_max_attempts` | int | `3` | Max retry attempts on 429/503 responses. 0 = no retries |
| `http.retry_backoff_base` | float | `1.0` | Base delay in seconds for exponential backoff |
| `http.retry_max_backoff` | float | `30.0` | Maximum backoff delay in seconds |

**Validation:** `timeout_seconds` and `timeout_resolve_seconds` must be greater than 0.

### Link Validation

Controls the download link validation step. Link validation runs after scraping
to filter out dead or blocked links before bundling them into CrawlJobs.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `validate_download_links` | bool | `true` | Enable/disable link validation entirely |
| `validation_timeout_seconds` | float | `5.0` | Timeout per individual link validation (seconds) |
| `validation_max_concurrent` | int | `20` | Maximum parallel link validations (semaphore) |

Setting `validate_download_links` to `false` skips all validation and includes
all scraped links directly. This can be useful for debugging but is not
recommended in production. See [Link Validation](./link-validation.md) for
the full validation strategy.

### Playwright

Controls the Playwright browser engine for JavaScript-heavy sites.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `playwright.headless` | bool | `true` | Run the browser in headless mode (no GUI) |
| `playwright.timeout_ms` | int | `30000` | Navigation timeout in milliseconds |

**Validation:** `timeout_ms` must be greater than 0.

Set `headless: false` only for local debugging -- it requires a display server
(X11/Wayland) and is not supported in Docker containers.

### Stremio

Controls the Stremio addon behavior: stream ranking, plugin concurrency, title
matching, hoster probing, and scored plugin selection.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `stremio.preferred_language` | string | `de` | Preferred audio language for stream ranking |
| `stremio.language_scores` | dict | `{de: 1000, de-sub: 500, en-sub: 200, en: 150}` | Language ranking scores (higher = preferred) |
| `stremio.default_language_score` | int | `100` | Score for unknown/undetected languages |
| `stremio.quality_multiplier` | int | `10` | Multiplier for quality value in ranking |
| `stremio.hoster_scores` | dict | `{supervideo: 5, voe: 4, filemoon: 3, ...}` | Hoster reliability bonus (tie-breaker) |
| `stremio.max_concurrent_plugins` | int | `10` | Max parallel plugin searches |
| `stremio.max_concurrent_playwright` | int | `5` | Max parallel Playwright plugin searches |
| `stremio.max_concurrent_plugins_auto` | bool | `true` | Auto-tune concurrency based on host CPU/RAM |
| `stremio.auto_tune_all` | bool | `true` | Container-aware auto-tune ALL concurrency params (cgroup v2/v1) |
| `stremio.max_results_per_plugin` | int | `100` | Max results per plugin in Stremio search |
| `stremio.plugin_timeout_seconds` | float | `30.0` | Per-plugin timeout for stream search |
| `stremio.title_match_threshold` | float | `0.7` | Minimum title similarity score |
| `stremio.title_year_bonus` | float | `0.2` | Score bonus for matching year |
| `stremio.title_year_penalty` | float | `0.3` | Score penalty for non-matching year |
| `stremio.title_sequel_penalty` | float | `0.35` | Score penalty for sequel number mismatch |
| `stremio.title_year_tolerance_movie` | int | `1` | Allowed year difference for movies (±N) |
| `stremio.title_year_tolerance_series` | int | `3` | Allowed year difference for series (±N) |
| `stremio.stream_link_ttl_seconds` | int | `7200` | TTL for cached stream links (2h default) |
| `stremio.probe_at_stream_time` | bool | `true` | Probe hoster URLs at /stream time |
| `stremio.probe_concurrency` | int | `10` | Max parallel hoster probes |
| `stremio.probe_timeout_seconds` | float | `10.0` | Per-URL probe timeout |
| `stremio.max_probe_count` | int | `50` | Max streams to probe/resolve (top-ranked first) |
| `stremio.resolve_target_count` | int | `15` | Stop resolving after this many successes (0 = disabled) |
| `stremio.probe_stealth_enabled` | bool | `true` | Use Playwright Stealth for Cloudflare bypass |
| `stremio.probe_stealth_concurrency` | int | `5` | Max parallel Playwright Stealth probes |
| `stremio.probe_stealth_timeout_seconds` | float | `15.0` | Per-URL Playwright Stealth timeout |

**Scored plugin selection** (requires `scoring.enabled=true`):

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `stremio.scoring_enabled` | bool | `false` | Use scores to limit plugin selection |
| `stremio.stremio_deadline_ms` | int | `2000` | Overall search deadline (ms) |
| `stremio.max_plugins_scored` | int | `5` | Top-N plugins when scoring is active |
| `stremio.max_items_total` | int | `50` | Global result cap across all plugins |
| `stremio.max_items_per_plugin` | int | `20` | Per-plugin result cap in scored mode |
| `stremio.exploration_probability` | float | `0.15` | Chance to include random mid-score plugin |

See [Stremio Addon](./stremio-addon.md) for the full feature description and
[Plugin Scoring](./plugin-scoring-and-probing.md) for the scoring system.

### Concurrency Pool

The global concurrency pool distributes httpx and Playwright slots across
concurrent requests using fair-share scheduling.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `stremio.max_concurrent_plugins` | int | `10` | Total httpx concurrency slots (shared globally) |
| `stremio.max_concurrent_playwright` | int | `5` | Total Playwright concurrency slots |

The pool is created at composition time. Each concurrent request gets a
fair share: `httpx_slots // active_requests` httpx permits and
`pw_slots // active_requests` Playwright permits.

### Auto-Tune (Container-Aware)

When `stremio.auto_tune_all` is `true` (default), all concurrency parameters
are automatically derived from detected container/host resources via cgroup
v2/v1 at startup. Manual values in the config file are overridden.

| Parameter | Formula | Min | Max (hard cap) |
|---|---|---|---|
| `max_concurrent_plugins` | `cpu * 3`, `mem_gb * 2` | 2 | 30 |
| `max_concurrent_playwright` | `cpu`, `mem_gb / 0.15` | 1 | 10 |
| `probe_concurrency` | `cpu * 4` | 4 | 100 |
| `validation_max_concurrent` | `cpu * 5` | 5 | 120 |

Hard caps for `probe_concurrency` and `validation_max_concurrent` are derived
from synthetic benchmark diminishing-returns analysis (`tests/benchmark/`):
throughput gains drop below 5% beyond these thresholds.

Resource detection order: cgroup v2 → cgroup v1 → `os.cpu_count()` + psutil.
See `src/scavengarr/infrastructure/resource_detector.py`.

### Scoring

Controls the background plugin scoring and probing system.
See [Plugin Scoring & Probing](./plugin-scoring-and-probing.md) for the full
architecture and data model.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `scoring.enabled` | bool | `false` | Enable background probing |
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
|-----|------|---------|-------------|
| `logging.level` | string | `INFO` | Minimum log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `logging.format` | string | (auto) | Log renderer: `json` or `console` |

**Auto-derived format:** When `logging.format` is not explicitly set, it is
derived from the `environment` setting:
- `dev` / `test` --> `console` (human-readable, colored output)
- `prod` --> `json` (machine-parseable, suitable for log aggregation)

Logs are structured via `structlog` and include context fields like `plugin`,
`stage`, `duration_ms`, and `results_count`. Secrets from configuration and
environment variables are never logged.

**Console format example:**

```
2025-01-01 12:00:00 [info] search_request  plugin=filmpalast query=iron+man
2025-01-01 12:00:02 [info] search_complete plugin=filmpalast results=5 duration_ms=1823
```

**JSON format example:**

```json
{"timestamp": "2025-01-01T12:00:00Z", "level": "info", "event": "search_request", "plugin": "filmpalast", "query": "iron+man"}
{"timestamp": "2025-01-01T12:00:02Z", "level": "info", "event": "search_complete", "plugin": "filmpalast", "results": 5, "duration_ms": 1823}
```

### Cache

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `cache.backend` | string | `diskcache` | Backend: `diskcache` (SQLite-based) or `redis` |
| `cache.dir` | path | `./cache/scavengarr` | SQLite database path (diskcache only) |
| `cache.redis_url` | string | `redis://localhost:6379/0` | Redis connection URL (redis only) |
| `cache.ttl_seconds` | int | `3600` | Default time-to-live for cache entries (seconds) |
| `cache.search_ttl_seconds` | int | `900` | TTL for cached search results (seconds). 0 = disabled |
| `cache.max_concurrent` | int | `10` | Semaphore limit for parallel cache operations |

**Validation:** `ttl_seconds` must be >= 0 (0 disables expiration).

The cache stores CrawlJobs, scraping results, and other intermediate data.
Diskcache is the default and requires no external services. Redis can be used
for shared state across multiple instances.

**Diskcache setup (default):**

```yaml
cache:
  backend: "diskcache"
  dir: "/app/cache/scavengarr"
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

The `environment` setting (`dev`, `test`, or `prod`) controls several runtime
behaviors:

| Aspect | `dev` | `test` | `prod` |
|--------|-------|--------|--------|
| Default log format | `console` | `console` | `json` |
| Cache cleared on startup | yes | no | no |
| Error descriptions in XML | yes | yes | no (empty RSS) |
| Error HTTP status codes | actual | actual | `200` (stable for Prowlarr) |

### Production Mode

In production, Torznab endpoints return empty RSS feeds with HTTP 200 for
upstream and internal errors. This prevents Prowlarr from marking the indexer
as permanently failed due to transient issues. Error details are logged
server-side but not exposed in the XML response.

### Development Mode

In development, actual HTTP status codes are returned and error descriptions
are included in the RSS `<description>` element. The cache is cleared on
startup to avoid stale data during development.

---

## Validation Rules

The configuration model enforces these validation rules:

| Field | Rule |
|-------|------|
| `http_timeout_seconds` | Must be > 0 |
| `playwright_timeout_ms` | Must be > 0 |
| `cache_ttl_seconds` | Must be >= 0 |
| `environment` | Must be one of: `dev`, `test`, `prod` |
| `log_level` | Must be one of: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `log_format` | Must be one of: `json`, `console` (or unset for auto) |
| `cache.backend` | Must be one of: `diskcache`, `redis` |
| Path fields | Automatically expanded (`~` resolves to home directory) |

Invalid configuration causes the application to fail at startup with a
descriptive Pydantic validation error.

---

## .env File Support

Scavengarr supports `.env` files for local development. Pass the path via
`--dotenv`:

```bash
poetry run start --dotenv .env
```

The `.env` file is loaded with `python-dotenv` and does **not** override
existing environment variables (`override=False`). This means real environment
variables always take precedence over `.env` values.

**Example `.env` file:**

```bash
# .env
SCAVENGARR_ENVIRONMENT=dev
SCAVENGARR_PLUGIN_DIR=./plugins
SCAVENGARR_LOG_LEVEL=DEBUG
SCAVENGARR_LOG_FORMAT=console

HOST=0.0.0.0
PORT=7979

CACHE_BACKEND=diskcache
CACHE_DIR=./cache/scavengarr
CACHE_TTL_SECONDS=3600
```

---

## Docker Configuration

When running in Docker, use environment variables or mount a config file.

### Minimal Production

```bash
docker run -d --name scavengarr \
  -p 7979:7979 \
  -v ./plugins:/app/plugins \
  -v ./cache:/app/cache \
  -e SCAVENGARR_ENVIRONMENT=prod \
  -e SCAVENGARR_LOG_LEVEL=INFO \
  scavengarr:latest
```

### With Config File

```bash
docker run -d --name scavengarr \
  -p 7979:7979 \
  -v ./plugins:/app/plugins \
  -v ./cache:/app/cache \
  -v ./config.yaml:/app/config.yaml:ro \
  scavengarr:latest \
  --config /app/config.yaml
```

### With Redis Cache

```bash
docker run -d --name scavengarr \
  -p 7979:7979 \
  -v ./plugins:/app/plugins \
  -e SCAVENGARR_ENVIRONMENT=prod \
  -e CACHE_BACKEND=redis \
  -e CACHE_REDIS_URL=redis://redis:6379/0 \
  scavengarr:latest
```

### Required Volumes

| Container Path | Purpose | Required |
|----------------|---------|----------|
| `/app/plugins` | Plugin directory (Python files) | Yes |
| `/app/cache` | Cache storage (diskcache SQLite) | Recommended |
| `/app/config.yaml` | Configuration file | Optional |

See [Prowlarr Integration](./prowlarr-integration.md) for the complete
deployment guide including Docker Compose examples.

---

## Source Code References

| Component | File |
|-----------|------|
| Configuration schema (Pydantic) | `src/scavengarr/infrastructure/config/schema.py` |
| Configuration loader | `src/scavengarr/infrastructure/config/load.py` |
| Default values | `src/scavengarr/infrastructure/config/defaults.py` |
| Application state | `src/scavengarr/interfaces/app_state.py` |
