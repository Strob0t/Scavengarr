[← Back to Index](./README.md)

# Prowlarr Integration Guide

This guide explains how to deploy Scavengarr and integrate it with Prowlarr,
Sonarr, Radarr, and JDownloader. It covers the complete setup from Docker
deployment to the end-to-end download flow.

---

## Overview

Scavengarr acts as a **Torznab-compatible indexer** that Prowlarr can query like
any other torrent indexer. The key difference is that Scavengarr serves direct
download links (via `.crawljob` files) instead of torrent files.

**Integration flow:**

```
Prowlarr                Scavengarr              Target Site
   |                        |                        |
   |-- search request ----->|                        |
   |                        |-- multi-stage scrape ->|
   |                        |<-- HTML/JS results ----|
   |                        |                        |
   |<-- RSS XML results ----|                        |
   |                        |                        |
Sonarr/Radarr           Scavengarr
   |                        |
   |-- download request --->|
   |<-- .crawljob file -----|
   |                        |
JDownloader
   |
   |-- processes .crawljob
   |-- downloads files
```

**Components involved:**

| Component | Role |
|-----------|------|
| **Prowlarr** | Indexer manager -- sends search queries, aggregates results |
| **Scavengarr** | Torznab indexer -- scrapes sites, serves RSS results and `.crawljob` files |
| **Sonarr/Radarr** | Media managers -- request downloads from search results |
| **JDownloader** | Download manager -- processes `.crawljob` files, downloads actual files |

---

## Prerequisites

Before setting up the integration, ensure you have:

1. **Scavengarr** deployed and running (see [Deployment](#deployment) below)
2. **Prowlarr** installed and accessible
3. **At least one plugin** in Scavengarr's plugin directory
4. (Optional) **JDownloader** configured with folder watch for `.crawljob` files

---

## Deployment

### Docker (Recommended)

The simplest way to deploy Scavengarr is with Docker:

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

**Volume mounts:**

| Host Path | Container Path | Purpose |
|-----------|----------------|---------|
| `./plugins` | `/app/plugins` | Plugin YAML/Python files (required) |
| `./cache` | `/app/cache` | Cache storage for CrawlJobs (recommended) |

### Docker Compose

For a complete stack with health checks:

```yaml
# docker-compose.yml
version: "3.8"

services:
  scavengarr:
    image: scavengarr:latest
    container_name: scavengarr
    ports:
      - "7979:7979"
    volumes:
      - ./plugins:/app/plugins
      - ./cache:/app/cache
    environment:
      SCAVENGARR_ENVIRONMENT: prod
      SCAVENGARR_LOG_LEVEL: INFO
      SCAVENGARR_PLUGIN_DIR: /app/plugins
      HOST: 0.0.0.0
      PORT: 7979
      CACHE_BACKEND: diskcache
      CACHE_DIR: /app/cache/scavengarr
      CACHE_TTL_SECONDS: 3600
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:7979/healthz"]
      interval: 30s
      timeout: 5s
      retries: 3
    restart: unless-stopped
```

### Docker Compose with Redis

For multi-instance deployments or shared cache:

```yaml
# docker-compose.yml
version: "3.8"

services:
  scavengarr:
    image: scavengarr:latest
    container_name: scavengarr
    ports:
      - "7979:7979"
    volumes:
      - ./plugins:/app/plugins
    environment:
      SCAVENGARR_ENVIRONMENT: prod
      SCAVENGARR_LOG_LEVEL: INFO
      CACHE_BACKEND: redis
      CACHE_REDIS_URL: redis://redis:6379/0
    depends_on:
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:7979/healthz"]
      interval: 30s
      timeout: 5s
      retries: 3
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
# Install dependencies
poetry install

# Start the server
poetry run start --host 0.0.0.0 --port 7979 --log-level DEBUG
```

### Verify Deployment

After starting Scavengarr, verify it is running:

```bash
# Application health
curl http://localhost:7979/healthz
# Expected: {"status": "ok"}

# List available plugins
curl http://localhost:7979/api/v1/torznab/indexers
# Expected: {"indexers": [{"name": "...", "version": "...", "mode": "..."}]}
```

---

## Adding Scavengarr to Prowlarr

### Step-by-Step Setup

1. Open Prowlarr and go to **Settings > Indexers**.

2. Click **Add Indexer** (the `+` button).

3. In the search box, type **Torznab** and select **Generic Torznab**.

4. Configure the indexer:

   | Field | Value |
   |-------|-------|
   | **Name** | Any descriptive name (e.g., `Scavengarr - filmpalast`) |
   | **URL** | `http://<scavengarr-host>:7979/api/v1/torznab/<plugin_name>` |
   | **API Key** | Leave empty (not required) |
   | **Categories** | `2000` (Movies), `5000` (TV) |

   Replace `<scavengarr-host>` with the hostname or IP where Scavengarr is
   running. If both services are in Docker on the same network, use the
   container name (e.g., `http://scavengarr:7979/...`).

   Replace `<plugin_name>` with the name of the plugin you want to use
   (as shown by the `/api/v1/torznab/indexers` endpoint).

5. Click **Test** to verify connectivity.

6. Click **Save** if the test passes.

### What Happens During the Test

When you click "Test" in Prowlarr, it sends two requests:

1. **Capabilities request:** `GET /api/v1/torznab/{plugin_name}?t=caps`
   - Prowlarr learns the supported search types and categories.

2. **Test search:** `GET /api/v1/torznab/{plugin_name}?t=search&extended=1`
   - Scavengarr performs a lightweight HTTP probe against the plugin's
     `base_url` (not a full scrape).
   - If the target site is reachable, a synthetic test item is returned.
   - If unreachable, an empty RSS feed is returned and the test may fail.

### Multiple Plugins

If Scavengarr has multiple plugins loaded, add each one as a separate indexer
in Prowlarr. Each plugin has its own URL:

```
http://scavengarr:7979/api/v1/torznab/filmpalast
http://scavengarr:7979/api/v1/torznab/example-site
http://scavengarr:7979/api/v1/torznab/another-plugin
```

Use the indexers endpoint to discover all available plugins:

```bash
curl http://scavengarr:7979/api/v1/torznab/indexers
```

---

## Connecting to Sonarr and Radarr

Prowlarr syncs indexers to Sonarr and Radarr automatically. After adding
Scavengarr as an indexer in Prowlarr:

1. Go to Prowlarr **Settings > Apps**.
2. Add your Sonarr and/or Radarr instances (if not already configured).
3. Prowlarr will push the Scavengarr indexer to each connected app.
4. Sonarr/Radarr will now include Scavengarr in their automatic and manual
   searches.

### Sync Profiles

You can control which indexers sync to which apps using Prowlarr's sync
profiles. For example, you might sync a movie-focused plugin only to Radarr
and a TV-focused plugin only to Sonarr.

---

## Download Flow

Understanding the complete download flow helps with troubleshooting:

### 1. Search Request

Sonarr/Radarr sends a search query through Prowlarr to Scavengarr:

```
GET /api/v1/torznab/filmpalast?t=search&q=iron+man
```

### 2. Multi-Stage Scraping

Scavengarr executes the plugin's scraping pipeline:

1. **Search stage:** Queries the target site, extracts result rows.
2. **Detail stage:** Visits each detail page, extracts download links.
3. **Link validation:** Validates download links in parallel (HEAD/GET probes).
4. **CrawlJob creation:** Bundles validated links into CrawlJobs with TTL.

### 3. RSS Response

Scavengarr returns Torznab RSS XML where each `<item>` contains:
- `<link>` and `<enclosure>` pointing to the Scavengarr download endpoint
- `<guid>` containing the original source URL (for deduplication)
- `<torznab:attr>` elements with metadata (size, category, etc.)

### 4. Download Request

When a user grabs a result (or automatic download triggers), Sonarr/Radarr
requests the download:

```
GET /api/v1/download/{job_id}
```

Scavengarr serves the `.crawljob` file containing validated download links.

### 5. JDownloader Processing

The `.crawljob` file is placed in JDownloader's folder watch directory.
JDownloader processes it and starts downloading the actual files.

**JDownloader folder watch setup:**

1. Open JDownloader > Settings > Extensions > Folder Watch.
2. Set the watch folder to the same directory where `.crawljob` files are saved.
3. JDownloader will automatically process new `.crawljob` files.

---

## Health Monitoring

### Application Health

Use the `/healthz` endpoint for container health checks:

```bash
curl http://scavengarr:7979/healthz
# {"status": "ok"}
```

### Plugin Health

Check individual plugin reachability:

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

If a plugin's target site is down, Scavengarr will return empty results for
that plugin but continue serving other plugins normally.

### CrawlJob Inspection

Inspect a CrawlJob's metadata without downloading the file:

```bash
curl http://scavengarr:7979/api/v1/download/{job_id}/info
```

This is useful for verifying that a CrawlJob was created correctly and checking
its expiration status.

---

## Startup Sequence

Understanding the startup sequence helps diagnose issues:

1. **CLI** parses command-line arguments.
2. **Configuration** loaded with layered precedence (defaults < YAML < ENV < CLI).
3. **Logging** configured (structured logging via structlog).
4. **FastAPI** application created.
5. **Lifespan** hook initializes resources in order:
   - Cache backend (diskcache or Redis)
   - HTTP client (httpx.AsyncClient)
   - Plugin registry (discovers and loads plugins)
   - Search engine
   - CrawlJob repository
   - CrawlJob factory
6. **Uvicorn** starts serving on `host:port`.

### Shutdown

Graceful shutdown closes resources in reverse order:

1. HTTP client closed.
2. Cache closed.

Active requests are allowed to complete before shutdown.

---

## Troubleshooting

### Prowlarr Test Fails

**Symptom:** Prowlarr shows "Unable to connect" or "Indexer returned 0 results"
when testing.

**Possible causes:**

1. **Scavengarr not running:** Verify with `curl http://<host>:7979/healthz`.
2. **Wrong URL:** Check the plugin name matches exactly (case-sensitive). Use
   `/api/v1/torznab/indexers` to list available plugins.
3. **Target site unreachable:** Check plugin health with
   `/api/v1/torznab/{plugin_name}/health`. The target website may be down or
   blocking requests.
4. **Network issue:** If running in Docker, ensure both containers are on the
   same network. Use the container name instead of `localhost`.

### Empty Search Results

**Symptom:** Searches return no results even though the target site has content.

**Possible causes:**

1. **Selectors outdated:** The target site may have changed its HTML structure.
   Check the plugin's selectors against the current site layout.
2. **All links invalid:** Link validation may be filtering out all results.
   Check logs for validation failures. Temporarily set
   `validate_download_links=false` to test.
3. **Scraping blocked:** The target site may be blocking automated requests.
   Check the HTTP user agent and consider using Playwright mode for
   JavaScript-heavy sites.

### CrawlJob Download Returns 404

**Symptom:** Sonarr/Radarr gets 404 when trying to download.

**Possible causes:**

1. **CrawlJob expired:** Default TTL is 1 hour. Increase `CACHE_TTL_SECONDS`
   if needed.
2. **Cache cleared:** In dev mode, cache is cleared on startup. Use
   `SCAVENGARR_ENVIRONMENT=prod` to persist cache.
3. **Container restart:** If using diskcache, ensure the cache directory is
   mounted as a volume.

### Logs Show No Output

**Symptom:** No log output visible.

**Possible causes:**

1. **Wrong log level:** Set `SCAVENGARR_LOG_LEVEL=DEBUG` for maximum verbosity.
2. **Wrong log format:** JSON logs may not be visible in a terminal. Set
   `SCAVENGARR_LOG_FORMAT=console` for human-readable output.

---

## Network Configuration

### Docker Networking

When running Prowlarr and Scavengarr in Docker, they need to communicate over
a shared network:

```yaml
# docker-compose.yml
services:
  scavengarr:
    # ... (see Docker Compose examples above)
    networks:
      - arr-network

  prowlarr:
    image: lscr.io/linuxserver/prowlarr:latest
    networks:
      - arr-network
    # ... other prowlarr config

networks:
  arr-network:
    driver: bridge
```

With this setup, use `http://scavengarr:7979/...` as the indexer URL in Prowlarr
(using the container name for DNS resolution).

### Reverse Proxy

If running behind a reverse proxy (nginx, Caddy, Traefik), ensure the proxy
passes the full path and preserves headers:

```nginx
# nginx example
location /scavengarr/ {
    proxy_pass http://scavengarr:7979/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
```

When using a path prefix, update the Prowlarr indexer URL accordingly:

```
http://proxy-host/scavengarr/api/v1/torznab/{plugin_name}
```

---

## Configuration Reference

Key configuration options for Prowlarr integration:

| Setting | Recommended Value | Purpose |
|---------|-------------------|---------|
| `SCAVENGARR_ENVIRONMENT` | `prod` | Stable error handling for Prowlarr |
| `SCAVENGARR_LOG_LEVEL` | `INFO` | Balanced logging |
| `CACHE_TTL_SECONDS` | `3600` (1 hour) | CrawlJob availability window |
| `HOST` | `0.0.0.0` | Accept connections from all interfaces |
| `PORT` | `7979` | Default Scavengarr port |

For the complete configuration reference, see [Configuration](./configuration.md).

For the full API specification, see [Torznab API Reference](./torznab-api.md).

---

## Source Code References

| Component | File |
|-----------|------|
| Torznab router | `src/scavengarr/interfaces/api/torznab/router.py` |
| Download router | `src/scavengarr/interfaces/api/download/router.py` |
| Application composition | `src/scavengarr/interfaces/composition.py` |
| Application state | `src/scavengarr/interfaces/app_state.py` |
| CLI entry point | `src/scavengarr/interfaces/cli/main.py` |
