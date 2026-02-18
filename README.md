# Scavengarr

**Self-hosted Torznab/Newznab indexer and Stremio addon for Prowlarr and other Arr applications.**

Scavengarr scrapes sources via two engines (httpx for static HTML, Playwright for
JS-heavy sites) and delivers results through standard Torznab API endpoints and a
full Stremio addon with stream resolution. It integrates directly with Prowlarr as
a custom indexer and with Stremio as a community addon.

**Version:** 0.1.0 |
**Python:** 3.12+ |
**License:** Private

## Features

- **Torznab API** compatible with Prowlarr, Sonarr, Radarr, and other Arr applications
- **Stremio addon** with catalog browsing, stream resolution, and hoster video URL extraction
- **Dual scraping engine:** httpx (static HTML) and Playwright (JS-heavy / Cloudflare)
- **42 Python plugins** (33 httpx + 9 Playwright) covering German streaming, DDL, and anime sites
- **56 hoster resolvers** for video URL extraction and file availability validation (17 individual + 12 generic DDL + 27 XFS consolidated)
- **Multi-stage scraping:** Search results, detail pages, and link extraction in a pipeline
- **Link validation:** Parallel HEAD/GET validation with dead-link filtering
- **CrawlJob packaging:** Bundle multiple validated download links into `.crawljob` files
- **Plugin scoring:** EWMA-based background probing ranks plugins by health and search quality
- **Circuit breaker:** Per-plugin failure tracking skips consistently failing plugins
- **Global concurrency pool:** Fair-share httpx and Playwright slot budgets across concurrent requests
- **Multi-language search:** Plugins declare supported languages; TMDB titles resolved per language
- **Stream deduplication:** Per-hoster dedup keeps only the best-ranked stream per hoster
- **Shared Playwright browser pool:** Single Chromium process shared across all 9 Playwright plugins
- **Graceful shutdown:** Drains in-flight requests before stopping
- **Mirror URL fallback:** Automatic domain failover when primary mirrors are unreachable
- **Structured logging:** JSON/console output via structlog with correlation context
- **Flexible caching:** diskcache (SQLite) or Redis backends with TTL support
- **Health & metrics endpoints:** `/healthz`, `/readyz`, and `/stats/metrics` for observability

For detailed feature documentation, see [docs/features/README.md](docs/features/README.md).

## Quick Start

### Prerequisites

- Python 3.12 or later
- [Poetry](https://python-poetry.org/) for dependency management
- Docker (optional, for containerized deployment)

### Install with Poetry

```bash
git clone https://github.com/youruser/scavengarr.git
cd scavengarr
poetry install
```

### Configure

Configure via environment variables or `config.yaml`:

Key environment variables:
- `SCAVENGARR_PLUGIN_DIR` -- path to plugin directory (default: `plugins/`)
- `SCAVENGARR_LOG_LEVEL` -- log level: `DEBUG`, `INFO`, `WARNING` (default: `INFO`)
- `SCAVENGARR_CACHE_BACKEND` -- `diskcache` or `redis` (default: `diskcache`)
- `SCAVENGARR_TMDB_API_KEY` -- TMDB API key for Stremio catalog/title resolution (optional; IMDB fallback available)

See [docs/features/configuration.md](docs/features/configuration.md) for all settings.

### Run

```bash
# Start the server
poetry run start --factory --host 0.0.0.0 --port 7979
```

### Run with Docker Compose

```bash
docker compose up --build
```

### Add to Prowlarr

1. In Prowlarr, go to **Settings > Indexers > Add Indexer**
2. Select **Generic Torznab**
3. Set URL: `http://<host>:7979/api/v1/torznab/<plugin_name>`
4. Leave API Key empty (not required)
5. Set Categories: `2000` (Movies), `5000` (TV)
6. Click **Test** to verify connectivity

### Add to Stremio

1. Open Stremio and navigate to the addon catalog
2. Enter the addon URL: `http://<host>:7979/api/v1/stremio/manifest.json`
3. Click **Install**

See [docs/features/stremio-addon.md](docs/features/stremio-addon.md) for details.

## Tech Stack

| Component | Library |
|---|---|
| HTTP Framework | FastAPI + Uvicorn |
| Static Scraping | httpx |
| JS Scraping | Playwright (Chromium) |
| Title Matching | rapidfuzz |
| Release Parsing | guessit |
| Configuration | pydantic-settings |
| Caching | diskcache (SQLite) / Redis |
| Logging | structlog |
| CLI | Typer |

## Plugins

Scavengarr ships with 42 Python plugins (33 httpx + 9 Playwright). Examples:

| Plugin | Type | Site |
|---|---|---|
| `filmpalast_to.py` | httpx | filmpalast.to |
| `boerse.py` | Playwright | boerse.sx |
| `cineby.py` | httpx | cineby.gd |
| `aniworld.py` | httpx | aniworld.to |

See [docs/features/plugin-system.md](docs/features/plugin-system.md) for how to write your own plugins.

## Development

### Setup

```bash
poetry install
poetry run pre-commit install
```

### Run Tests

```bash
poetry run pytest
```

The test suite includes 4043 tests (unit + E2E + integration + live smoke).
Concurrency benchmarks run separately: `poetry run pytest tests/benchmark/ -s -v`.

### Code Quality

```bash
# Lint and format
poetry run ruff check .
poetry run ruff format .

# Run all pre-commit checks
poetry run pre-commit run --all-files
```

### Project Structure

```
src/scavengarr/
  domain/           # Entities, value objects, protocols (ports)
  application/      # Use cases, factories
  infrastructure/   # Adapters (scraping, cache, plugins, resolvers, validation)
  interfaces/       # HTTP router (FastAPI), CLI (Typer), composition root
plugins/            # 42 Python plugins (33 httpx + 9 Playwright)
tests/              # 4043 tests (unit, E2E, integration, live, benchmark)
docs/               # Architecture, features, plans, refactor history
```

## Documentation

- [Features Overview](docs/features/README.md)
- [Stremio Addon](docs/features/stremio-addon.md)
- [Hoster Resolvers](docs/features/hoster-resolvers.md)
- [Plugin Scoring](docs/features/plugin-scoring-and-probing.md)
- [Architecture](docs/architecture/clean-architecture.md)
- [Configuration](docs/features/configuration.md)
- [Plugin System](docs/features/plugin-system.md)
- [Torznab API](docs/features/torznab-api.md)
- [Prowlarr Integration](docs/features/prowlarr-integration.md)
