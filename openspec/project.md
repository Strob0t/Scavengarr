# Project Context

## Purpose

Scavengarr is a **self-hosted, container-ready indexer** that emulates the Torznab/Newznab API used by applications like Prowlarr, and also serves as a **Stremio addon**. It scrapes German and English streaming and DDL (direct download) sites through **Python plugins** (httpx for static HTML/JSON APIs, Playwright for JavaScript-heavy or Cloudflare-protected sites), validates the links, and serves the results through Torznab endpoints (`t=caps`, `t=search`) under `/api/v1/torznab/{plugin}` and Stremio endpoints under `/api/v1/stremio/`.

## Tech Stack

- **Language**: Python `^3.12,<3.14` (`pyproject.toml`; Docker images use Python 3.12, ruff targets `py312`)
- **Framework**: FastAPI + Uvicorn (ASGI) for the web server
- **Scraping**:
  - **httpx plugins**: `HttpxPluginBase` (`src/scavengarr/infrastructure/plugins/httpx_base.py`) — `httpx` (async HTTP) + `selectolax` (lexbor HTML parser)
  - **Playwright plugins**: `PlaywrightPluginBase` (`src/scavengarr/infrastructure/plugins/playwright_base.py`) — Patchright (Playwright fork), one shared Chromium via `SharedBrowserPool`
- **Hoster resolvers**: 60 resolvers in `src/scavengarr/infrastructure/hoster_resolvers/` (streaming and DDL hosters)
- **Configuration**: Pydantic Settings with precedence CLI → ENV (incl. `--dotenv` file) → YAML → defaults
- **Plugins**: Python plugins only (`plugins/*.py`); the YAML plugin system was removed in `42fced9`
- **Cache**: `diskcache` (default) or Redis (`cache.backend: redis`)
- **Logging**: `structlog` with JSON (prod) or console (dev) output
- **CLI**: stdlib `argparse` (`src/scavengarr/interfaces/cli/__main__.py`)
- **Containerization**: Docker (`Dockerfile.prod`)

## Project Conventions

### Code Style

- Follows PEP 8 guidelines
- Uses **`ruff format`** for code formatting (Black-compatible, line length 88)
- Static analysis and linting with **`ruff check`** (rules `E`, `F`, `W`, `C90`, `I`)
- Import sorting via ruff's `I` rules (standard → third-party → local)
- **Full type annotations everywhere** (modern syntax: `list[str]`, `T | None`, `from __future__ import annotations`)
- Docstrings in Google style for public APIs

### Architecture Patterns

- **Clean Architecture**: `domain/`, `application/`, `infrastructure/`, `interfaces/` under `src/scavengarr/` (dependency rule: inner layers never import outer layers)
- **Plugin-driven scraping**: Site-specific logic encapsulated in Python plugins inheriting `HttpxPluginBase` or `PlaywrightPluginBase`
- **Type-safe configuration**: Pydantic Settings with strict validation and deterministic precedence
- **Deployment mode**: single process (FastAPI + plugins + background scoring); a distributed coordinator/worker mode is not implemented
- **OpenSpec**: two legacy changes live in `openspec/changes/`; features since then are tracked in `CHANGELOG.md`, `docs/features/` and `docs/plans/`

### Testing Strategy

- **Test-Driven Development (TDD)**: Write tests first (RED), implement (GREEN), refactor (REFACTOR)
- **Test pyramid**: mostly unit tests (3919), plus 169 E2E, 25 integration and 38 opt-in live tests (`poetry run pytest -m live`)
- **Framework**: pytest with FastAPI `TestClient` for API tests, `respx` for HTTP mocking, `pytest-asyncio` (`asyncio_mode = "auto"`)
- **Coverage**: `pytest-cov` is available; no coverage threshold is enforced
- **Mock patterns**: see `AGENTS.md` section 6 (e.g. `PluginRegistryPort` is synchronous → `MagicMock`)

### Git Workflow

- **Branches**: develop on `staging`; `main` is production and only updated via PR on explicit request (see `AGENTS.md` section 1)
- **Commit messages**: Conventional Commits (`<type>(<scope>): <subject>`), not enforced by tooling
  - Types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`
- **Pre-commit hooks** (`.pre-commit-config.yaml`): `end-of-file-fixer`, `trailing-whitespace`, `check-yaml`, `ruff-check` (import sorting), `ruff-check --fix`, `ruff-format` — **never skip** (`--no-verify`)
- **Quality gates** (must pass before every commit):
  - ✅ `poetry run pre-commit run --all-files`
  - ✅ `poetry run pytest -n auto` (parallel; live tests excluded by default)

### Configuration System

- **Single entrypoint**: `load_config()` (`src/scavengarr/infrastructure/config/load.py`) called once in the CLI `start()` function
- **Precedence (high → low)**:
  1. CLI arguments (`--config`, `--dotenv`, `--plugin-dir`, `--log-level`, `--log-format`)
  2. Environment variables (`SCAVENGARR_*` prefix; `CACHE_*` for cache settings). A `.env` file passed via `--dotenv` is loaded into the environment without overriding variables that are already set
  3. YAML config file (path via `--config` or `SCAVENGARR_CONFIG`; no default path; the Docker image sets `SCAVENGARR_CONFIG=/app/config/config.yaml`)
  4. Built-in defaults (`src/scavengarr/infrastructure/config/defaults.py` + field defaults in `schema.py`)
- **No side effects**: Config loading is pure (never creates dirs, writes files, or starts network activity)
- **Secrets**: never log secrets; a redaction helper does not exist yet

### Plugin System

- **Discovery**: `PluginRegistry.discover()` indexes `*.py` files in `plugins.plugin_dir` (`SCAVENGARR_PLUGIN_DIR`, default `./plugins`) without executing them
- **Python plugins**:
  - Must export a module-level `plugin` with a non-empty `name` and an `async def search(query, category, season, episode)` method
  - Inherit `HttpxPluginBase` or `PlaywrightPluginBase` (shared client/browser setup, domain fallback, cleanup, semaphore)
  - Declare `provides` (`"stream"`, `"download"` or `"both"`) and `languages` (default `["de"]`)
- **Lazy-loading**: Plugins are imported on demand (`PluginRegistry.get(name)`)
- **Caching**: Loaded plugins are cached in-memory for subsequent requests

## Domain Context

Understanding of German and English streaming and DDL site structures (HTML listings, forums, WordPress sites, JSON APIs) is crucial for writing plugins. Plugins handle authentication individually (e.g. vBulletin form login with cookie transfer in boerse, mygully, dataload) and bot protection (Cloudflare, DDoS-Guard). Results are exposed as Torznab XML for Prowlarr and similar Arr applications, and as Stremio streams.

## Important Constraints

- **Python version**: `^3.12,<3.14` (enforced via `pyproject.toml`)
- **Privacy & security**:
  - Never log secrets in plaintext (passwords, API keys, cookies)
  - SSL verification stays enabled (httpx default)
  - Timeout enforcement to prevent hanging requests (`http.timeout_seconds`, default 30 s; plugin client default 15 s)
- **Torznab/Newznab compatibility**: XML schema compliance required for Prowlarr integration
- **Non-blocking I/O**: independent requests run in parallel with bounded concurrency
- **Documentation ships with the code**: behavior, feature, architecture or configuration changes update `CHANGELOG.md` and `docs/` in the same commit

## External Dependencies

- **GitHub**: Code management and issue tracking (no CI workflows configured)
- **Container image**: built locally from `Dockerfile.prod`
- **PyPI**: Python package dependencies (managed via Poetry)
- **Optional services**:
  - Redis (alternative cache backend)
  - TMDB API (optional API key for Stremio title lookup; IMDB/Wikidata fallback without key)
- **Metrics**: JSON metrics at `/api/v1/stats/metrics` (Prometheus format: future)

## Development Dependencies

From `[tool.poetry.group.dev.dependencies]`:

- **Poetry**: Dependency management and packaging
- **pytest**, **pytest-asyncio**, **pytest-cov**, **pytest-mock**: Testing
- **respx**: httpx-native HTTP mocking
- **requests**: test helper dependency
- **ruff**: Linting and formatting
- **pre-commit**: Git hooks for quality enforcement

Runtime libraries relevant for development: `structlog`, `httpx`, `patchright`, `pydantic-settings`, `diskcache`, `redis`, `guessit`, `rapidfuzz`, `unidecode`.

## OpenSpec Integration

OpenSpec changes live in `openspec/changes/<change-id>/`:
- `proposal.md` – Why, what, impact
- `tasks.md` – Implementation checklist
- `design.md` – Architectural decisions, trade-offs
- `specs/<capability>/spec.md` – BDD-style requirements (WHEN/THEN scenarios)

### Changes

1. `add-config-system` – Type-safe config loading (Pydantic Settings): implemented except the redaction helper, the effective-config debug print and wrapped YAML errors
2. `add-plugin-loader` – Plugin discovery and validation: Python part implemented under `domain/plugins` + `infrastructure/plugins`; YAML part superseded (removed in `42fced9`)

Features built without an OpenSpec change (see `CHANGELOG.md`): Playwright plugins and shared browser pool, per-plugin authentication, Torznab use cases and presenter, FastAPI endpoints (`/api/v1/torznab/{plugin}?t=search|caps`), Stremio addon, hoster resolvers, search caching, plugin scoring.

## Canonical Contracts (Non-Negotiable)

These are fixed across all OpenSpec changes and AI implementations:

| Contract | Value | Source |
|---|---|---|
| **Entry Point** | `poetry run start` (`scavengarr.interfaces.cli:start`, `src/scavengarr/interfaces/cli/__main__.py`) | `pyproject.toml` |
| **Config Prefix** | `SCAVENGARR_` (cache settings: `CACHE_`) | `add-config-system` |
| **Plugin Directory** | Configurable via `SCAVENGARR_PLUGIN_DIR` (default: `./plugins`) | `add-plugin-loader` |
| **Plugin Format** | `.py` (Python plugins inheriting `HttpxPluginBase`/`PlaywrightPluginBase`) | `add-plugin-loader` |
| **Scraping Engines** | httpx and Playwright (equal-weight, chosen per plugin) | `AGENTS.md` |
| **Cache Backend** | `diskcache` (default), Redis (optional) | `add-config-system` |
| **Logging Framework** | `structlog` with JSON/console output | `add-config-system` |
| **Config Precedence** | CLI > ENV (incl. `--dotenv`) > YAML > defaults | `add-config-system` |
| **Python Version** | `^3.12,<3.14` | `pyproject.toml` |
| **API Prefix** | `/api/v1` (port 7979 by default) | `src/scavengarr/interfaces/app.py` |

---

**Last Updated**: 2026-09-28
**Author**: Scavengarr Team
**OpenSpec Changes**: `add-config-system`, `add-plugin-loader`

---

## Why This File Must Stay Current

If `project.md` contains outdated information, AI assistants act on it:
- They write code for engines or plugin formats that no longer exist (e.g. Scrapy or YAML plugins)
- They name tools that are not installed (e.g. `mypy`, `commitlint`, `typer`)
- They put features directly into core code instead of plugins or hoster resolvers

Keep this file in sync with `pyproject.toml`, `AGENTS.md` and `docs/`. This is essential for consistent AI implementations.
