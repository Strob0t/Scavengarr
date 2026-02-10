# Configuration

Scavengarr uses a layered configuration system with strict precedence.

## Precedence (highest to lowest)

1. **CLI arguments** (`--plugin-dir`, `--log-level`, etc.)
2. **Environment variables** (`SCAVENGARR_*` prefix)
3. **YAML config file** (via `--config`)
4. **`.env` file** (via `--dotenv` or auto-detected)
5. **Defaults** (hardcoded in code)

Higher-precedence values override lower ones. Only explicitly provided values
at each level participate in the merge.

## CLI Arguments

```bash
poetry run start [OPTIONS]
```

| Flag | Type | Description |
|------|------|-------------|
| `--host` | string | Bind address (default: `0.0.0.0`) |
| `--port` | int | Bind port (default: `7979`) |
| `--config` | path | YAML config file |
| `--dotenv` | path | `.env` file path |
| `--plugin-dir` | path | Plugin directory override |
| `--log-level` | choice | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `--log-format` | choice | `json`, `console` |

## Environment Variables

All application-level variables use the `SCAVENGARR_` prefix.
Cache-specific variables use the `CACHE_` prefix.

### Application Variables

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `SCAVENGARR_APP_NAME` | string | `scavengarr` | Application name |
| `SCAVENGARR_ENVIRONMENT` | string | `dev` | `dev`, `test`, or `prod` |
| `SCAVENGARR_PLUGIN_DIR` | path | `./plugins` | Plugin directory |
| `SCAVENGARR_HTTP_TIMEOUT_SECONDS` | float | `30.0` | HTTP request timeout |
| `SCAVENGARR_HTTP_FOLLOW_REDIRECTS` | bool | `true` | Follow HTTP redirects |
| `SCAVENGARR_HTTP_USER_AGENT` | string | `Scavengarr/0.1.0 (...)` | User-Agent header |
| `SCAVENGARR_PLAYWRIGHT_HEADLESS` | bool | `true` | Playwright headless mode |
| `SCAVENGARR_PLAYWRIGHT_TIMEOUT_MS` | int | `30000` | Playwright timeout (ms) |
| `SCAVENGARR_LOG_LEVEL` | string | `INFO` | Log level |
| `SCAVENGARR_LOG_FORMAT` | string | (auto) | `json` or `console` |
| `SCAVENGARR_CACHE_DIR` | path | `./.cache/scavengarr` | Cache directory |
| `SCAVENGARR_CACHE_TTL_SECONDS` | int | `3600` | Cache entry TTL |

### Cache Variables

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `CACHE_BACKEND` | string | `diskcache` | `diskcache` or `redis` |
| `CACHE_DIR` | path | `./cache/scavengarr` | Diskcache SQLite path |
| `CACHE_REDIS_URL` | string | `redis://localhost:6379/0` | Redis URL |
| `CACHE_TTL_SECONDS` | int | `3600` | Default TTL |
| `CACHE_MAX_CONCURRENT` | int | `10` | Max parallel cache ops |

### Server Variables

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `HOST` | string | `0.0.0.0` | Server bind address |
| `PORT` | int | `7979` | Server bind port |

## YAML Configuration

Pass a YAML file via `--config config.yaml`. The file uses a sectioned structure:

```yaml
# config.yaml
app_name: "scavengarr"
environment: "prod"

plugins:
  plugin_dir: "./plugins"

http:
  timeout_seconds: 30.0
  follow_redirects: true
  user_agent: "Scavengarr/0.1.0"

playwright:
  headless: true
  timeout_ms: 30000

logging:
  level: "INFO"
  format: "json"   # "json" or "console"; auto-derived from environment if omitted

cache:
  backend: "diskcache"     # "diskcache" or "redis"
  dir: "./cache/scavengarr"
  ttl_seconds: 3600
  # redis_url: "redis://localhost:6379/0"  # only when backend=redis
```

## Configuration Sections

### General

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `app_name` | string | `scavengarr` | Application name (used in XML titles) |
| `environment` | string | `dev` | `dev`, `test`, or `prod` |

### Plugins

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `plugins.plugin_dir` | path | `./plugins` | Directory for YAML/Python plugins |

### HTTP

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `http.timeout_seconds` | float | `30.0` | Request timeout for scraping |
| `http.follow_redirects` | bool | `true` | Follow HTTP redirects |
| `http.user_agent` | string | `Scavengarr/0.1.0 (...)` | User-Agent header |

### Link Validation

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `validate_download_links` | bool | `true` | Enable link validation |
| `validation_timeout_seconds` | float | `5.0` | Per-link timeout |
| `validation_max_concurrent` | int | `20` | Max parallel validations |

### Playwright

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `playwright.headless` | bool | `true` | Run browser headless |
| `playwright.timeout_ms` | int | `30000` | Navigation timeout (ms) |

### Logging

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `logging.level` | string | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `logging.format` | string | (auto) | `json` (prod) or `console` (dev/test) |

Log format is automatically derived from `environment` when not explicitly set:
- `dev` / `test` -> `console`
- `prod` -> `json`

### Cache

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `cache.backend` | string | `diskcache` | `diskcache` or `redis` |
| `cache.dir` | path | `./cache/scavengarr` | SQLite path (diskcache) |
| `cache.redis_url` | string | `redis://localhost:6379/0` | Redis URL |
| `cache.ttl_seconds` | int | `3600` | Default cache TTL |
| `cache.max_concurrent` | int | `10` | Semaphore limit for cache ops |

## Environment-Specific Behavior

| Aspect | `dev` | `test` | `prod` |
|--------|-------|--------|--------|
| Log format | console | console | json |
| Cache cleared on startup | yes | no | no |
| Error descriptions in XML | yes | yes | no (empty RSS) |
| Error HTTP status codes | actual | actual | 200 (stable for Prowlarr) |

## Validation

- `http_timeout_seconds` must be > 0
- `playwright_timeout_ms` must be > 0
- `cache_ttl_seconds` must be >= 0
- Paths are automatically expanded (`~` resolves to home directory)
