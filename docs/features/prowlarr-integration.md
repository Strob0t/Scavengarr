[← Back to Index](./README.md)

# Prowlarr Integration Guide

> How to deploy Scavengarr and connect it to Prowlarr, Sonarr/Radarr and JDownloader, from the Docker build to the end-to-end download flow.

---

## Overview

Scavengarr is a Torznab-compatible indexer that Prowlarr queries like any torrent indexer. The difference is that it serves direct download links packaged as `.crawljob` files instead of torrent files.

```text
Prowlarr                Scavengarr              Target Site
   |                        |                        |
   |-- search request ----->|                        |
   |                        |-- plugin scrape ------>|
   |                        |<-- HTML/JS results ----|
   |<-- RSS XML results ----|                        |
   |                        |
Sonarr/Radarr (Blackhole client)
   |-- download request --->|
   |<-- .crawljob file -----|
   |-- saves to watch dir
   |
JDownloader (FolderWatch)
   |-- processes .crawljob, downloads files
```

| Component | Role |
|---|---|
| Prowlarr | Indexer manager — sends search queries, syncs indexers to the Arr apps |
| Scavengarr | Torznab indexer — scrapes sites, serves RSS results and `.crawljob` files |
| Sonarr/Radarr | Media managers — grab results via a Blackhole-type download client |
| JDownloader | Download manager — picks up `.crawljob` files via FolderWatch |

---

## Prerequisites

- Scavengarr deployed and running (see [Deployment](#deployment)).
- Prowlarr installed and able to reach Scavengarr over the network.
- At least one plugin (`*.py`) in Scavengarr's plugin directory.
- For downloads: JDownloader with the FolderWatch extension, and a Blackhole-type download client in Sonarr/Radarr whose folder is JDownloader's watch directory.

---

## Deployment

### Build the Image

No prebuilt image is referenced by the repository; build it locally from `Dockerfile.prod`:

```bash
docker build -f Dockerfile.prod -t scavengarr .
```

The image's entrypoint starts a virtual display (Xvfb) for the headful browser, then runs `python -m scavengarr.interfaces.cli` as a non-root user. The image sets these defaults:

| Env var | Value |
|---|---|
| `SCAVENGARR_ENVIRONMENT` | `prod` |
| `SCAVENGARR_CONFIG` | `/app/config/config.yaml` |
| `SCAVENGARR_PLUGIN_DIR` | `/app/plugins` |
| `SCAVENGARR_CACHE_DIR` | `/app/cache` |
| `SCAVENGARR_LOG_LEVEL` / `SCAVENGARR_LOG_FORMAT` | `INFO` / `json` |
| `HOST` / `PORT` | `0.0.0.0` / `7979` |

It also has a built-in `HEALTHCHECK` against `/api/v1/healthz`. The image ships a default `config.yaml` (a copy of `data/config.yaml`) but **no plugins** — the plugin directory must be mounted.

### Docker Run

```bash
docker run -d \
  --name scavengarr \
  --init \
  -p 7979:7979 \
  -v ./plugins:/app/plugins \
  -v ./data:/app/config \
  -v ./cache:/app/cache \
  scavengarr
```

`--init` (`init: true` in Compose, as in the shipped `docker-compose.yml`) reaps orphaned Chromium and driver processes, which the app as PID 1 would not.

| Host path | Container path | Purpose |
|---|---|---|
| `./plugins` | `/app/plugins` | Python plugin files — required, the image ships none |
| `./data` | `/app/config` | Must contain `config.yaml` (read via `SCAVENGARR_CONFIG`); replaces the built-in default |
| `./cache` | `/app/cache` | Diskcache storage (search cache, CrawlJobs, other cached data) |

Environment variables override values from `config.yaml` (precedence: CLI > ENV > YAML > defaults), so `SCAVENGARR_CACHE_DIR=/app/cache` from the image wins over `cache.dir` in the YAML.

### Docker Compose

The repository ships a ready [`docker-compose.yml`](../../docker-compose.yml) that builds the image locally and has optional `solver` (Byparr), `redis` and `tracing` (Grafana Tempo) profiles; see the [README Quick Start](../../README.md#quick-start). A minimal hand-written service looks like this:

```yaml
services:
  scavengarr:
    image: scavengarr
    container_name: scavengarr
    init: true
    ports:
      - "7979:7979"
    volumes:
      - ./plugins:/app/plugins
      - ./data:/app/config
      - ./cache:/app/cache
    environment:
      SCAVENGARR_ENVIRONMENT: prod
      SCAVENGARR_LOG_LEVEL: INFO
      SCAVENGARR_CACHE_TTL_SECONDS: 3600
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:7979/api/v1/healthz"]
      interval: 30s
      timeout: 5s
      retries: 3
    restart: unless-stopped
```

### Docker Compose with Redis

Select Redis either with `SCAVENGARR_CACHE_BACKEND=redis` and `SCAVENGARR_CACHE_REDIS_URL` in the service's `environment`, or in the mounted `./data/config.yaml`:

```yaml
cache:
  backend: "redis"
  redis_url: "redis://redis:6379/0"
```

```yaml
services:
  scavengarr:
    image: scavengarr
    container_name: scavengarr
    init: true
    ports:
      - "7979:7979"
    volumes:
      - ./plugins:/app/plugins
      - ./data:/app/config
    environment:
      SCAVENGARR_ENVIRONMENT: prod
    depends_on:
      redis:
        condition: service_healthy
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    container_name: scavengarr-redis
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 3
    restart: unless-stopped
```

### Local Development

```bash
poetry install
poetry run start --host 0.0.0.0 --port 7979 --log-level DEBUG
```

Available CLI flags: `--host`, `--port`, `--config`, `--dotenv`, `--plugin-dir`, `--log-level`, `--log-format`. Without `--config` or `SCAVENGARR_CONFIG`, built-in defaults are used (environment `dev`).

### Verify Deployment

```bash
# Liveness
curl http://localhost:7979/api/v1/healthz
# {"status": "ok", "plugins": <count>, "hosters": [...]}

# Readiness (200; the port opens only after startup has completed)
curl http://localhost:7979/api/v1/readyz

# List available plugins
curl http://localhost:7979/api/v1/torznab/indexers
# {"indexers": [{"name": "...", "version": "...", "mode": "..."}]}
```

---

## Adding Scavengarr to Prowlarr

### Step-by-Step Setup

1. Open Prowlarr and go to **Settings > Indexers**.
1. Click **Add Indexer** and select **Generic Torznab**.
1. Configure the indexer:

   | Field | Value |
   |---|---|
   | Name | Any name (e.g. `Scavengarr - filmpalast`) |
   | URL | `http://<scavengarr-host>:7979/api/v1/torznab/<plugin_name>` |
   | API Key | Leave empty (no authentication) |
   | Categories | `2000` (Movies), `5000` (TV) |

   Use the container name as host when both run in the same Docker network (e.g. `http://scavengarr:7979/...`). `<plugin_name>` is the exact (case-sensitive) name listed by `/api/v1/torznab/indexers`.

1. Click **Test**, then **Save**.

### What Happens During the Test

Scavengarr answers the two request types Prowlarr uses for indexer setup:

1. `GET /api/v1/torznab/{plugin_name}?t=caps` — returns static capabilities (free-text search only; categories 2000/5000/8000).
1. `GET /api/v1/torznab/{plugin_name}?t=search&extended=1` (no `q`) — a lightweight HTTP probe of the plugin's `base_url` instead of a full scrape. Any HTTP response counts as reachable and yields one synthetic test item (200); a connection error yields an empty feed with HTTP 503.

See [Prowlarr Test Mode](./torznab-api.md#prowlarr-test-mode) for all outcomes (an unknown plugin name returns HTTP 404).

### Multiple Plugins

Add each plugin as a separate indexer; each has its own URL:

```text
http://scavengarr:7979/api/v1/torznab/filmpalast
http://scavengarr:7979/api/v1/torznab/<other_plugin>
```

Many plugin indexers on one Prowlarr instance all come from the same client IP and share the [API rate limit](#api-rate-limit).

---

## Connecting to Sonarr and Radarr

Prowlarr syncs indexers to Sonarr and Radarr:

1. In Prowlarr, open **Settings > Apps** and add your Sonarr/Radarr instances.
1. Prowlarr pushes the Scavengarr indexers to each connected app.
1. Use Prowlarr's sync profiles to limit indexers to specific apps (e.g. a movie plugin only to Radarr).

### Download Client (Required)

Scavengarr results are `.crawljob` files, not torrents, so a regular torrent client cannot handle them. Sonarr/Radarr need a Blackhole-type download client whose drop/watch folder is JDownloader's FolderWatch directory. The Arr app then fetches the `.crawljob` file on grab and saves it into that folder, where JDownloader picks it up. Both containers need access to the same directory (e.g. a shared volume).

---

## Download Flow

1. **Search** — Sonarr/Radarr search through Prowlarr, which calls `GET /api/v1/torznab/{plugin}?t=search&q=...`.
1. **Scraping** — the plugin performs its own multi-stage scrape (search page → detail pages → links).
1. **Link validation** — download links are validated in parallel with HEAD/GET, only as far as the requested page needs (see [Link Validation](./link-validation.md)).
1. **CrawlJob creation** — every result of the requested page becomes its own CrawlJob; it lives `cache.crawljob_ttl_seconds` (default 1 hour) after the search (see [CrawlJob System](./crawljob-system.md)).
1. **RSS response** — each `<item>` has `<link>`/`<enclosure>` pointing to `/api/v1/download/{job_id}` and a `<guid>` with the primary download URL.
1. **Grab** — the Blackhole download client requests `/api/v1/download/{job_id}` and saves the `.crawljob` file into JDownloader's watch folder. For plugins with grab-time resolution (nox, animeloads) the links are resolved during this request; it fails with HTTP 502 when no links come back.
1. **JDownloader** — FolderWatch processes the file and downloads the links.

JDownloader side: enable the FolderWatch extension in JDownloader's settings and point it to the same directory the Arr download client writes to.

---

## Health Monitoring

### Application Health

- `GET /api/v1/healthz` — liveness; returns `{"status": "ok", "plugins": ..., "hosters": [...]}`.
- `GET /api/v1/readyz` — readiness; 200 (the port opens only after startup has completed).

### Plugin Health

```bash
curl http://scavengarr:7979/api/v1/torznab/filmpalast/health
```

```json
{
  "plugin": "filmpalast",
  "base_url": "https://filmpalast.to",
  "checked_url": "https://filmpalast.to/",
  "reachable": true,
  "status_code": 200,
  "error": null
}
```

`reachable` only means that an HTTP response arrived; check `status_code` for 403/5xx. A site that is down usually gives an empty feed with HTTP 200 in every environment; the logs show `<plugin>_timeout` or `<plugin>_fetch_error`. Only an error the plugin raises (e.g. a failed browser page load) gives HTTP 502 with the error in the channel description in dev/test (an empty feed with 200 in prod).

### CrawlJob Inspection

```bash
curl http://scavengarr:7979/api/v1/download/{job_id}/info
```

Shows a job's links and expiration status without downloading the file.

---

## Startup and Shutdown

### Startup Sequence

1. The CLI parses arguments and resolves the config file (`--config` or `SCAVENGARR_CONFIG`).
1. Configuration is loaded with layered precedence (defaults < YAML < ENV < CLI).
1. Logging (structlog) is configured.
1. The FastAPI app is created (API rate-limit middleware, routers, health probes).
1. The lifespan hook initializes resources, in order:
   - telemetry (Prometheus metrics, optional tracing, event-loop lag monitor) and concurrency auto-tuning (`stremio.auto_tune_all`)
   - cache backend (diskcache or Redis); in `dev` the cache is cleared
   - shared HTTP client with per-domain rate limiting and 429/503 retries
   - plugin registry (discovery + per-plugin overrides from `plugins.overrides`)
   - search engine (link validation), CrawlJob repository, CrawlJob factory
   - TMDB/IMDB client, shared browser pool with stealth pool, hoster resolvers, stream link repository, optional plugin scoring, concurrency pool, circuit breaker, plugin health monitor, Stremio use cases
1. The app is marked ready and Uvicorn opens `host:port`; until then connections are refused.

### Shutdown

1. Uvicorn stops accepting connections and waits for in-flight requests to finish, without a time limit of its own (`docker stop` kills the container after its grace period, 10 s by default).
1. Background Stremio searches and resolutions end; the scoring, plugin-health and loop-lag tasks are cancelled.
1. Stealth pool, shared browser pool and hoster resolvers are cleaned up.
1. The HTTP client is closed, then the cache.
1. Telemetry sends its last spans (up to 3 s).

---

## API Rate Limit

Scavengarr limits requests per client IP with a sliding one-minute window: default `120` requests/minute, configured via `http.api_rate_limit_rpm` or `SCAVENGARR_API_RATE_LIMIT_RPM` (`0` disables it). Excess requests get HTTP 429 with a JSON body (`{"error": "Rate limit exceeded", "retry_after_seconds": 60}`) and a `Retry-After: 60` header — also on Torznab endpoints. If Prowlarr fans out to many plugin indexers, raise the limit or set it to `0`. See [Rate Limiting](./torznab-api.md#rate-limiting).

---

## Troubleshooting

### Prowlarr Test Fails

- **Scavengarr not running** — check `curl http://<host>:7979/api/v1/healthz`.
- **Wrong URL** — the plugin name is case-sensitive; list names with `/api/v1/torznab/indexers`.
- **Target site unreachable** — check `/api/v1/torznab/{plugin_name}/health`; the site may be down or blocking requests. The check probes the plugin's current `base_url` only (the first domain until a search has picked a working one).
- **Network issue** — in Docker, put both containers on the same network and use the container name instead of `localhost`.
- **HTTP 429** — the [API rate limit](#api-rate-limit) was hit.

### Empty Search Results

- **Site layout changed** — the plugin's selectors may be outdated; the plugin needs a code update.
- **All links invalid** — link validation may drop every result; check the logs for `links_filtered`. To test, set `validate_download_links: false` in `config.yaml` (there is no environment variable for it). Without validation each CrawlJob holds only the result's primary link.
- **Scraping blocked** — the site may block automated requests (Cloudflare, DDoS-Guard). httpx plugins load Cloudflare challenge pages through the shared browser (`playwright.browser_fallback`, on by default) or an optional Byparr/FlareSolverr sidecar (`playwright.solver_url`, compose profile `solver`). Other protections (DDoS-Guard) need a plugin built on `PlaywrightPluginBase`.

### CrawlJob Download Returns 404

- **CrawlJob expired** — jobs live `cache.crawljob_ttl_seconds` (default 1 hour) after the search, not after the grab. Raise it when the download client grabs late, or re-run the search.
- **Cache cleared** — in the `dev` environment the cache is cleared on every startup. Use `SCAVENGARR_ENVIRONMENT=prod` (the Docker image default).
- **Container restart** — with diskcache, mount the cache directory as a volume.

### CrawlJob Download Returns 502

- **Grab-time resolution failed** — nox and animeloads resolve the links when the job is grabbed (captcha, download quota); the logs show `crawljob_resolve_failed`. Retry later or grab another release.

### No Log Output

- **Log level** — set `SCAVENGARR_LOG_LEVEL=DEBUG`.
- **Log format** — JSON is the default in prod; set `SCAVENGARR_LOG_FORMAT=console` for human-readable output.

---

## Network Configuration

### Docker Networking

```yaml
services:
  scavengarr:
    # ... see the Compose examples above
    networks:
      - arr-network

  prowlarr:
    image: lscr.io/linuxserver/prowlarr:latest
    networks:
      - arr-network

networks:
  arr-network:
    driver: bridge
```

Use `http://scavengarr:7979/...` as indexer URL in Prowlarr.

### Reverse Proxy

Give Scavengarr its own host name and proxy it at `/`. A path prefix (`/scavengarr/`) is not supported: the feed's `<link>`/`<enclosure>` URLs and the Stremio stream URLs are built from the request without it, so grabs would miss the proxy's location.

The proxy must pass the `Host` header and send `X-Forwarded-Proto` and `X-Forwarded-For`. The app trusts them only from the addresses in `FORWARDED_ALLOW_IPS` (see [Configuration](./configuration.md#server-variables)); otherwise links come out as `http://` and all clients share one rate-limit bucket.

```nginx
location / {
    proxy_pass http://scavengarr:7979;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

Caddy's `reverse_proxy scavengarr:7979` sends these headers by default.

---

## Configuration Reference

| Setting | Recommended | Purpose |
|---|---|---|
| `SCAVENGARR_ENVIRONMENT` | `prod` | Prowlarr-friendly error handling (empty feed with 200), persistent cache |
| `SCAVENGARR_LOG_LEVEL` | `INFO` | Balanced logging |
| `SCAVENGARR_API_RATE_LIMIT_RPM` | `120` or `0` | Per-IP API rate limit |
| `SCAVENGARR_CACHE_TTL_SECONDS` | `3600` | Default cache entry TTL (CrawlJobs have their own: `cache.crawljob_ttl_seconds`, default 3600) |
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `7979` | Bind port |

See [Configuration](./configuration.md) for the complete reference and [Torznab API Reference](./torznab-api.md) for the API specification.

---

## Source Code References

| Component | Path |
|---|---|
| Torznab router | `src/scavengarr/interfaces/api/torznab/router.py` |
| Download router | `src/scavengarr/interfaces/api/download/router.py` |
| App factory (health probes, middleware) | `src/scavengarr/interfaces/app.py` |
| API rate limit middleware | `src/scavengarr/interfaces/api/middleware.py` |
| Composition root (lifespan) | `src/scavengarr/interfaces/composition.py` |
| Application state | `src/scavengarr/interfaces/app_state.py` |
| CLI entry point | `src/scavengarr/interfaces/cli/__main__.py` |
| Config schema | `src/scavengarr/infrastructure/config/schema.py` |
| Production image | `Dockerfile.prod` |
