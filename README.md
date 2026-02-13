# Scavengarr

**Self-hosted Torznab/Newznab indexer for Prowlarr and other Arr applications.**

Scavengarr scrapes sources via two engines (httpx for static HTML, Playwright for
JS-heavy sites) and delivers results through standard Torznab API endpoints. It
integrates directly with Prowlarr as a custom indexer.

**Version:** 0.1.0 |
**Python:** 3.12+ |
**License:** Private

## Features

- **Torznab API** compatible with Prowlarr, Sonarr, Radarr, and other Arr applications
- **Dual scraping engine:** httpx (static HTML) and Playwright (JS-heavy / Cloudflare)
- **Plugin system:** Python plugins for all scraping (httpx or Playwright-based)
- **Multi-stage scraping:** Search results, detail pages, and link extraction in a pipeline
- **Link validation:** Parallel HEAD/GET validation with dead-link filtering
- **CrawlJob packaging:** Bundle multiple validated download links into `.crawljob` files
- **Mirror URL fallback:** Automatic domain failover when primary mirrors are unreachable
- **Structured logging:** JSON/console output via structlog with correlation context
- **Flexible caching:** diskcache (SQLite) or Redis backends with TTL support

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

Copy the example environment file and adjust settings:

```bash
cp .env.example .env
```

Key environment variables:
- `SCAVENGARR_PLUGIN_DIR` -- path to plugin directory (default: `plugins/`)
- `SCAVENGARR_LOG_LEVEL` -- log level: `DEBUG`, `INFO`, `WARNING` (default: `INFO`)
- `SCAVENGARR_CACHE_BACKEND` -- `diskcache` or `redis` (default: `diskcache`)

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

## Tech Stack

| Component | Library |
|---|---|
| HTTP Framework | FastAPI + Uvicorn |
| Static Scraping | httpx |
| JS Scraping | Playwright (Chromium) |
| Configuration | pydantic-settings |
| Caching | diskcache (SQLite) / Redis |
| Logging | structlog |
| CLI | Typer |

## Plugins

Scavengarr ships with 40 Python plugins covering httpx and Playwright-based scraping. Examples:

| Plugin | Type | Site |
|---|---|---|
| `filmpalast_to.py` | httpx | filmpalast.to |
| `boerse.py` | Playwright | boerse.sx |

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

The test suite includes 235+ unit tests across domain, application, and infrastructure layers.

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
  infrastructure/   # Adapters (scraping, cache, plugins, validation)
  interfaces/       # HTTP router (FastAPI), CLI (Typer), composition root
plugins/            # Plugin definitions (YAML + Python)
tests/              # Unit tests (domain, application, infrastructure)
docs/               # Architecture, features, plans, refactor history
```

## Documentation

- [Features Overview](docs/features/README.md)
- [Architecture](docs/architecture/clean-architecture.md)
- [Configuration](docs/features/configuration.md)
- [Plugin System](docs/features/plugin-system.md)
- [Torznab API](docs/features/torznab-api.md)
- [Prowlarr Integration](docs/features/prowlarr-integration.md)
