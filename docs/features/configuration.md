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
  timeout_seconds: 30.0
  follow_redirects: true
  user_agent: "Scavengarr/0.1.0 (+https://github.com/Strob0t/Scavengarr)"

playwright:
  headless: true
  timeout_ms: 30000

logging:
  level: "INFO"
  format: "json"    # "json" or "console"; auto-derived from environment if omitted

cache:
  backend: "diskcache"           # "diskcache" or "redis"
  dir: "/app/cache/scavengarr"
  ttl_seconds: 3600
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
| `http_follow_redirects` | `http.follow_redirects` |
| `http_user_agent` | `http.user_agent` |
| `playwright_headless` | `playwright.headless` |
| `playwright_timeout_ms` | `playwright.timeout_ms` |
| `log_level` | `logging.level` |
| `log_format` | `logging.format` |
| `cache_dir` | `cache.dir` |
| `cache_ttl_seconds` | `cache.ttl_seconds` |

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
| `http.follow_redirects` | bool | `true` | Whether the HTTP client follows redirects |
| `http.user_agent` | string | `Scavengarr/0.1.0 (...)` | User-Agent header sent with every request |

**Validation:** `timeout_seconds` must be greater than 0.

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
