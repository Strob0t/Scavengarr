# Scavengarr

**Self-hosted Torznab/Newznab indexer and Stremio addon for Prowlarr and other Arr applications.**

Scavengarr scrapes sources via two engines (httpx for static HTML, Playwright for JS-heavy sites) and delivers results through standard Torznab API endpoints and a full Stremio addon with stream resolution. It integrates directly with Prowlarr as a custom indexer and with Stremio as a community addon.

**Version:** 0.1.0 |
**Python:** 3.12–3.13 |
**License:** GPL-3.0

---

## Features

- **Torznab API** compatible with Prowlarr, Sonarr, Radarr, and other Arr applications
- **Stremio addon** with catalog browsing, stream resolution, and hoster video URL extraction
- **Dual scraping engine:** httpx (static HTML) and Playwright (JS-heavy / Cloudflare)
- **42 Python plugins** (33 httpx + 9 Playwright) covering German and English streaming, DDL, and anime sites
- **56 hoster resolvers** for video URL extraction and file availability validation (17 individual + 12 generic DDL + 27 XFS consolidated)
- **Multi-stage scraping:** plugins run search → detail → links internally with bounded concurrency
- **Link validation:** parallel HEAD/GET validation with dead-link filtering
- **CrawlJob packaging:** bundle multiple validated download links into `.crawljob` files
- **Plugin scoring:** EWMA-based background probing ranks plugins by health and search quality
- **Circuit breaker:** per-plugin failure tracking skips consistently failing plugins
- **Global concurrency pool:** fair-share httpx and Playwright slot budgets across concurrent requests, auto-tuned from container CPU/memory limits
- **Multi-language search:** plugins declare supported languages; TMDB titles resolved per language
- **Stream deduplication:** per-hoster dedup keeps only the best-ranked stream per hoster
- **Shared Playwright browser pool:** one Chromium process shared across all Playwright plugins
- **Graceful shutdown:** drains in-flight requests before stopping
- **Mirror URL fallback:** automatic domain failover when primary mirrors are unreachable
- **HTTP rate limiting:** adaptive per-domain rate limits for outgoing requests, per-IP limit for the API
- **Structured logging:** JSON/console output via structlog
- **Flexible caching:** diskcache (SQLite) or Redis backends with TTL support
- **Health & metrics endpoints:** `/api/v1/healthz`, `/api/v1/readyz`, and `/api/v1/stats/metrics`

For detailed feature documentation, see [docs/features/README.md](docs/features/README.md).

---

## Quick Start

### Prerequisites

- Python 3.12 or 3.13
- [Poetry](https://python-poetry.org/) for dependency management
- Docker (optional, for containerized deployment)

### Install with Poetry

```bash
git clone https://github.com/Strob0t/Scavengarr.git
cd Scavengarr
poetry install
```

### Configure

Configure via environment variables or a YAML file (`--config config.yaml` or `SCAVENGARR_CONFIG`). `data/config.yaml` is a complete, commented example.

Key environment variables:

- `SCAVENGARR_CONFIG` — path to the YAML config file (used when `--config` is not given)
- `SCAVENGARR_PLUGIN_DIR` — path to the plugin directory (default: `./plugins`)
- `SCAVENGARR_LOG_LEVEL` — `DEBUG`, `INFO`, `WARNING`, or `ERROR` (default: `INFO`)
- `SCAVENGARR_TMDB_API_KEY` — TMDB API key for Stremio catalog/title resolution (optional; IMDB fallback without it)
- `SCAVENGARR_CACHE_BACKEND` / `SCAVENGARR_CACHE_REDIS_URL` — `diskcache` (default) or `redis` plus its URL

The same settings can be set in YAML (`cache.backend`, `cache.redis_url`). See [docs/features/configuration.md](docs/features/configuration.md) for all settings.

### Run

```bash
poetry run start --host 0.0.0.0 --port 7979
```

### Run with Docker

```bash
docker build -f Dockerfile.prod -t scavengarr .
docker run -p 7979:7979 -v ./plugins:/app/plugins -v ./data:/app/config -v ./cache:/app/cache scavengarr
```

The image reads `/app/config/config.yaml` (via `SCAVENGARR_CONFIG`) and does not bundle plugins, so the plugin directory must be mounted. A Docker Compose example is in [docs/features/prowlarr-integration.md](docs/features/prowlarr-integration.md).

### Add to Prowlarr

1. In Prowlarr, go to **Settings > Indexers > Add Indexer**.
1. Select **Generic Torznab**.
1. Set URL: `http://<host>:7979/api/v1/torznab/<plugin_name>`.
1. Leave API Key empty (not required).
1. Set Categories: `2000` (Movies), `5000` (TV).
1. Click **Test** to verify connectivity.

### Add to Stremio

1. Open Stremio and navigate to the addon catalog.
1. Enter the addon URL: `http://<host>:7979/api/v1/stremio/manifest.json`.
1. Click **Install**.

See [docs/features/stremio-addon.md](docs/features/stremio-addon.md) for details.

---

## Tech Stack

| Component | Library |
|---|---|
| HTTP Framework | FastAPI + Uvicorn |
| Static Scraping | httpx |
| HTML Parsing | stdlib `html.parser` |
| JS Scraping | Patchright (Playwright fork, Chromium) |
| Title Matching | rapidfuzz |
| Release Parsing | guessit |
| Configuration | pydantic-settings, PyYAML, python-dotenv |
| Caching | diskcache (SQLite) / Redis |
| Logging | structlog |
| CLI | argparse (stdlib) |

---

## Plugins

Scavengarr ships with 42 Python plugins (33 httpx + 9 Playwright). Examples:

| Plugin | Type | Site |
|---|---|---|
| `filmpalast_to.py` | httpx | filmpalast.to |
| `boerse.py` | Playwright | boerse.am |
| `cineby.py` | httpx | cineby.gd |
| `aniworld.py` | httpx | aniworld.to |

See [docs/features/plugin-system.md](docs/features/plugin-system.md) for how to write your own plugins.

---

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

The test suite has 4151 tests: 4113 offline (3919 unit + 169 E2E + 25 integration) plus 38 live smoke tests. Live tests are opt-in: `poetry run pytest -m live`. Concurrency benchmarks run separately: `poetry run pytest tests/benchmark/ -s -v`.

### Code Quality

```bash
# Lint and format
poetry run ruff check .
poetry run ruff format .

# Run all pre-commit checks
poetry run pre-commit run --all-files
```

### Project Structure

```text
src/scavengarr/
  domain/           # Entities, value objects, protocols (ports)
  application/      # Use cases, factories, Stremio services
  infrastructure/   # Adapters (scraping, cache, plugins, resolvers, validation, scoring)
  interfaces/       # HTTP routers (FastAPI), CLI (argparse), composition root
plugins/            # 42 Python plugins (33 httpx + 9 Playwright)
tests/              # 4151 tests (unit, E2E, integration, live) + benchmarks
docs/               # Architecture, features, plans, refactor history
```

---

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
