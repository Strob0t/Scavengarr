# Deployment

## Quick Start

```bash
# Local development
poetry install
poetry run start --host 0.0.0.0 --port 7979

# With custom config
poetry run start --config config.yaml --dotenv .env --log-level INFO
```

## Docker

### Build

```bash
docker build -t scavengarr:latest .
```

### Run

```bash
docker run -d \
  --name scavengarr \
  -p 7979:7979 \
  -v ./plugins:/app/plugins \
  -v ./cache:/app/cache \
  -e SCAVENGARR_ENVIRONMENT=prod \
  -e SCAVENGARR_LOG_LEVEL=INFO \
  scavengarr:latest
```

### Docker Compose (Development)

The included `docker-compose.yml` starts development services:

```bash
docker compose up -d
```

Services:
| Service | Port | Purpose |
|---------|------|---------|
| `docs-mcp-server` | 6280 | Documentation MCP server |
| `llm-context-sse` | 9000 | LLM context streaming |
| `playwright-mcp` | 8001 | Playwright MCP bridge |
| `jaeger` | 16686 | Distributed tracing UI |
| `proxy` | 3000 | Nginx reverse proxy |

## Environment Configuration

### Minimal Production

```bash
SCAVENGARR_ENVIRONMENT=prod
SCAVENGARR_LOG_LEVEL=INFO
SCAVENGARR_PLUGIN_DIR=/app/plugins
HOST=0.0.0.0
PORT=7979
```

### With Redis Cache

```bash
CACHE_BACKEND=redis
CACHE_REDIS_URL=redis://redis:6379/0
CACHE_TTL_SECONDS=3600
```

### Full Example

```bash
# General
SCAVENGARR_ENVIRONMENT=prod
SCAVENGARR_APP_NAME=scavengarr
SCAVENGARR_PLUGIN_DIR=/app/plugins

# Server
HOST=0.0.0.0
PORT=7979

# HTTP
SCAVENGARR_HTTP_TIMEOUT_SECONDS=30
SCAVENGARR_HTTP_FOLLOW_REDIRECTS=true

# Logging
SCAVENGARR_LOG_LEVEL=INFO
SCAVENGARR_LOG_FORMAT=json

# Cache
CACHE_BACKEND=diskcache
CACHE_DIR=/app/cache/scavengarr
CACHE_TTL_SECONDS=3600
CACHE_MAX_CONCURRENT=10
```

## Volumes

| Path | Purpose | Required |
|------|---------|----------|
| `/app/plugins` | Plugin directory | Yes |
| `/app/cache` | Cache storage (diskcache) | Recommended |
| `/app/config.yaml` | Config file | Optional |

## Health Check

Use the `/healthz` endpoint for container health checks:

```yaml
# docker-compose.yml
services:
  scavengarr:
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:7979/healthz"]
      interval: 30s
      timeout: 5s
      retries: 3
```

## Prowlarr Integration

After deployment, add Scavengarr as a Torznab indexer in Prowlarr:

1. Go to **Settings > Indexers > Add**
2. Select **Generic Torznab**
3. Set URL: `http://<scavengarr-host>:7979/api/v1/torznab/<plugin_name>`
4. Leave API Key empty
5. Set Categories: `2000` (Movies), `5000` (TV)
6. Click **Test** to verify

## Logging

### Development (Console)

```
2025-01-01 12:00:00 [info] app_startup_complete
2025-01-01 12:00:01 [info] search_request plugin=filmpalast query=iron+man
```

### Production (JSON)

```json
{"timestamp": "2025-01-01T12:00:00Z", "level": "info", "event": "app_startup_complete"}
{"timestamp": "2025-01-01T12:00:01Z", "level": "info", "event": "search_request", "plugin": "filmpalast", "query": "iron+man"}
```

Log output is structured via `structlog` with context fields:
- `plugin` - Plugin name
- `stage` - Scraping stage
- `duration_ms` - Operation duration
- `results_count` - Number of results
- `method`, `path`, `status_code` - HTTP request details

## Startup Sequence

1. CLI parses arguments
2. Configuration loaded (defaults < YAML < ENV < CLI)
3. Logging configured
4. FastAPI app created
5. Lifespan hook initializes resources:
   - Cache (diskcache/redis)
   - HTTP client
   - Plugin registry (discovers plugins)
   - Search engine
   - CrawlJob repository
   - CrawlJob factory
6. Uvicorn starts serving on `host:port`

## Shutdown

Graceful shutdown closes resources in reverse order:
1. HTTP client closed
2. Cache closed

Active requests are allowed to complete before shutdown.
