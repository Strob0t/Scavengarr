# Project Context

## Purpose
Scavengarr is a **self-hosted, container-ready indexer** that emulates the Torznab/Newznab API used by applications like Prowlarr. It scrapes torrent sites using **dual-mode scraping engines** (Scrapy for static HTML, Playwright for JavaScript-heavy sites), normalizes results, and serves them through Torznab endpoints (`caps`, `search`, `tvsearch`, `movie`).

## Tech Stack
- **Language**: Python 3.12 (enforced via `pyproject.toml`)
- **Framework**: FastAPI (ASGI) for the web server
- **Scraping**: 
  - **ScrapyEngine** (MVP): `httpx` (async HTTP) + `parsel` (CSS selectors) for static HTML sites
  - **PlaywrightEngine** (future): Playwright (headless browser) for JavaScript-rendered sites
- **Configuration**: Pydantic Settings with precedence hierarchy (CLI → ENV → YAML → `.env` → defaults)
- **Plugins**: Dual-mode system supporting YAML (declarative) and Python (imperative) plugins
- **Cache**: `diskcache` (default), Redis optional (future)
- **Logging**: `structlog` with JSON (prod) or console (dev) output
- **CLI**: `typer` for command-line interface
- **Containerization**: Docker for deployment and orchestration

## Project Conventions

### Code Style
- Follows PEP 8 guidelines
- Uses **`ruff format`** for code formatting (Black-compatible, line length 88)
- Static analysis and linting with **`ruff check`**
- Import order management via `isort`-style (standard → third-party → local)
- **Full type annotations everywhere** (PEP 585 style: `list[str]` instead of `List[str]`)
- Docstrings in Google style for all public APIs

### Architecture Patterns
- **Layered architecture** with focus on emulating the Torznab/Newznab API
- **Plugin-driven scraping**: Site-specific logic encapsulated in YAML or Python plugins
- **Dual-engine support**: ScrapyEngine (MVP) for static HTML, PlaywrightEngine (future) for JS-rendered sites
- **Type-safe configuration**: Pydantic Settings with strict validation and deterministic precedence
- **Deployment modes**:
  - **Unified mode** (default): Single-process (FastAPI + scraping engines)
  - **Distributed mode** (future): Coordinator + separate worker processes for horizontal scaling
- **OpenSpec-driven development**: All major features tracked as changes in `openspec/changes/<change-id>/`

### Testing Strategy
- **Test-Driven Development (TDD)**: Write tests first (RED), implement (GREEN), refactor (REFACTOR)
- **Behavior-driven testing**: Tests verify OpenSpec scenarios from `spec.md` files
- **Test hierarchy**: End-to-end > Integration > Unit
- **Framework**: Pytest with `httpx.AsyncClient` for API tests, `respx` for HTTP mocking
- **Coverage target**: 80%+ for core modules (`config/`, `plugins/`, `engines/`)
- **Real dependencies preferred**: Minimize mocks, use fixtures for temporary files/env isolation

### Git Workflow
- **Branch naming**: `<type>/<issue>-<short-desc>` (e.g., `feat/add-config-system`, `fix/123-plugin-crash`)
- **Commit messages**: `<type>(<scope>): <subject>` format enforced by `commitlint`
  - Types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`
  - Subject: lowercase, no trailing period, ≤100 chars
- **Pre-commit hooks**: Runs `ruff check`, `ruff format`, `mypy`, `pytest` – **never skip** (`--no-verify`)
- **Quality gates** (must pass before PR):
  - ✅ `ruff check .` (no linting errors)
  - ✅ `ruff format --check .` (code formatted)
  - ✅ `mypy src/` (no type errors)
  - ✅ `pytest` (all tests pass)
  - ✅ 80%+ coverage for changed modules

### Configuration System
- **Single entrypoint**: `load_config()` called once at app startup
- **Precedence (high → low)**:
  1. CLI arguments (`--config`, `--plugin-dir`, `--log-level`)
  2. Environment variables (`SCAVENGARR_*` prefix)
  3. YAML config file (path via `--config`, default: `./config.yaml`)
  4. `.env` file (loaded via `python-dotenv`)
  5. Built-in defaults (`src/scavengarr/config/defaults.py`)
- **No side effects**: Config loading is pure (never creates dirs, writes files, or starts network activity)
- **Secrets redaction**: Use `redact_config_for_logging()` before logging config

### Plugin System
- **Discovery**: Auto-discover `.yaml` and `.py` files from `SCAVENGARR_PLUGIN_DIR` (default: `./plugins`)
- **YAML plugins** (declarative):
  - Schema validated with Pydantic (`PluginDefinition`)
  - Modes: `scrapy` (CSS selectors) or `playwright` (locators)
  - Use cases: 80% of trackers with static HTML tables
- **Python plugins** (imperative):
  - Protocol validated (must export `plugin` with `async def search(...)` method)
  - Use cases: Complex auth (OAuth), JSON APIs, dynamic scraping logic
- **Lazy-loading**: Plugins parsed only on first access (`PluginRegistry.get(name)`)
- **Caching**: Parsed plugins cached in-memory for subsequent requests

## Domain Context
Understanding of torrent tracker site structures (HTML tables, RSS feeds, JSON APIs) is crucial for defining scraping plugins. The system must handle diverse authentication strategies (Basic, Form, Cookie, Token) and effectively emulate Torznab endpoints to integrate with Prowlarr and similar Arr-applications.

## Important Constraints
- **Python 3.12 only**: No support for 3.11 or 3.13+ (enforced via `pyproject.toml`)
- **Privacy & security**: 
  - Never log secrets in plaintext (passwords, API keys, cookies)
  - SSL verification always enabled (`verify=True`)
  - Timeout enforcement (30s default) to prevent hanging requests
- **Torznab/Newznab compatibility**: XML schema compliance required for Prowlarr integration
- **Deployment flexibility**: Must operate efficiently in unified (single-process) and distributed (multi-worker) modes
- **OpenSpec compliance**: All major features must have corresponding change in `openspec/changes/<change-id>/`

## External Dependencies
- **GitHub**: Code management, issue tracking, CI/CD (GitHub Actions)
- **Docker Hub**: Container image registry
- **PyPI**: Python package dependencies (managed via Poetry)
- **Optional services**:
  - Redis (future caching backend)
  - Prometheus (future metrics endpoint)

## Development Dependencies
- **Poetry**: Dependency management and packaging
- **pytest**: Testing framework
- **ruff**: Linting and formatting
- **mypy**: Static type checking
- **pre-commit**: Git hooks for quality enforcement
- **structlog**: Structured logging
- **httpx**: Async HTTP client (ScrapyEngine)
- **parsel**: CSS selector library (ScrapyEngine)
- **pydantic-settings**: Type-safe configuration
- **typer**: CLI framework

## OpenSpec Integration
All major features are tracked as **OpenSpec changes** in `openspec/changes/<change-id>/`:
- `proposal.md` – Why, what, impact
- `tasks.md` – Implementation checklist
- `design.md` – Architectural decisions, trade-offs
- `specs/<capability>/spec.md` – BDD-style requirements (WHEN/THEN scenarios)

### Active Changes (MVP Roadmap)
1. ✅ `add-config-system` – Type-safe config loading (Pydantic Settings)
2. ✅ `add-plugin-loader` – YAML + Python plugin discovery and validation
3. ✅ `add-scrapy-engine` – Static HTML scraping with httpx + parsel
4. ⏳ `add-playwright-engine` – JavaScript-heavy site scraping
5. ⏳ `add-auth-strategies` – Per-plugin authentication (Basic, Form, Cookie)
6. ⏳ `add-search-service` – Orchestration layer (plugin → engine → renderer)
7. ⏳ `add-torznab-renderer` – XML serialization with `<torznab:attr>`
8. ⏳ `add-fastapi-endpoints` – `/api?t=search`, `/api?t=caps` routes

## Canonical Contracts (Non-Negotiable)
These are fixed across all OpenSpec changes and AI implementations:

| Contract | Value | Source |
|----------|-------|--------|
| **Entry Point** | `poetry run scavengarr` (CLI via `src/scavengarr/application/cli.py:start`) | `pyproject.toml` |
| **Config Prefix** | `SCAVENGARR_` (all env vars) | `add-config-system` |
| **Plugin Directory** | Configurable via `SCAVENGARR_PLUGIN_DIR` (default: `./plugins`) | `add-plugin-loader` |
| **Plugin Formats** | `.yaml` (declarative) + `.py` (imperative) | `add-plugin-loader` |
| **Scraping Engines** | ScrapyEngine (MVP), PlaywrightEngine (future) | `add-scrapy-engine` |
| **Cache Backend** | `diskcache` (default), Redis (optional/future) | `add-config-system` |
| **Logging Framework** | `structlog` with JSON/console output | `add-config-system` |
| **Config Precedence** | CLI > ENV > YAML > `.env` > defaults | `add-config-system` |
| **Python Version** | 3.12 only (enforced) | `pyproject.toml` |

***

**Last Updated**: 2026-01-25  
**Author**: Scavengarr Team  
**OpenSpec Version**: `add-config-system`, `add-plugin-loader`, `add-scrapy-engine` integrated

***

## Warum diese Änderungen kritisch sind

Wenn `project.md` veraltete Infos enthält, passiert Folgendes:
- AI-Assistenten schreiben **Playwright-Code** statt ScrapyEngine
- Config-Loading wird **Redis-first** implementiert statt diskcache
- **`black`** wird als Formatter genannt, obwohl nur `ruff format` installiert ist
- Plugin-System wird **nicht erwähnt**, sodass Agents Features direkt in Core schreiben statt Plugins zu nutzen

Mit der aktualisierten `project.md` sind **AGENTS.md, ARCHITECTURE.md und project.md synchron** – das ist entscheidend für konsistente AI-Implementierungen.