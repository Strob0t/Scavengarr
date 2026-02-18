# Scavengarr Feature Documentation

> Central index for all feature documentation. Start here to navigate the system.

Scavengarr is a self-hosted, container-ready **Torznab/Newznab indexer** for Prowlarr and other Arr applications. It scrapes sources via Python plugins (httpx for static HTML, Playwright for JS-heavy sites) and delivers results through standard Torznab endpoints.

**Version:** 0.1.0 | **Python:** 3.12+ | **Tests:** 3963 | **Plugins:** 42 (33 httpx + 9 Playwright) | **Hoster Resolvers:** 56 | **Architecture:** Clean Architecture

---

## Quick Navigation

### Feature Handbook

| Document | Description |
|---|---|
| [FEATURES.md](./FEATURES.md) | Compact feature handbook -- all features at a glance |

### Core Features

| Document | Description |
|---|---|
| [Plugin System](./plugin-system.md) | Python plugin authoring, base classes, protocol, discovery |
| [Python Plugins](./python-plugins.md) | Detailed Python plugin development, base class reference, examples |
| [Multi-Stage Scraping](./multi-stage-scraping.md) | Search-Detail-Links pipeline, stage types, parallel execution |
| [CrawlJob System](./crawljob-system.md) | Multi-link packaging for JDownloader integration |
| [Torznab API](./torznab-api.md) | Torznab/Newznab endpoint reference, XML format, Prowlarr compat |
| [Link Validation](./link-validation.md) | HEAD/GET validation strategy, parallel checking, status policies |
| [Configuration](./configuration.md) | YAML/ENV/CLI config, precedence rules, all settings |

### Streaming & Integration

| Document | Description |
|---|---|
| [Stremio Addon](./stremio-addon.md) | Stremio integration with catalog, streams, and hoster resolution |
| [Hoster Resolvers](./hoster-resolvers.md) | 56 hoster resolvers (streaming + DDL + 27 XFS consolidated) |
| [Plugin Scoring & Probing](./plugin-scoring-and-probing.md) | EWMA-based plugin ranking via background health and search probes |
| [Mirror URL Fallback](./mirror-url-fallback.md) | Automatic domain fallback when primary mirrors are unreachable |
| [Prowlarr Integration](./prowlarr-integration.md) | Step-by-step Prowlarr setup, endpoint mapping, category sync |

### Architecture

| Document | Description |
|---|---|
| [Clean Architecture](../architecture/clean-architecture.md) | Layer diagram, dependency rules, module organization |
| [Codeplan](../architecture/codeplan.md) | Implementation roadmap and architectural decisions |

### Plans & Roadmap

| Document | Description |
|---|---|
| [Playwright Engine](../plans/playwright-engine.md) | Browser pool and resource management for Playwright plugins |
| [More Plugins](../plans/more-plugins.md) | Plugin inventory and remaining candidates |
| [Integration Tests](../plans/integration-tests.md) | Implemented: 25 integration + 158 E2E + 38 live smoke tests |
| [Search Caching](../plans/search-caching.md) | Implemented: 900s TTL with X-Cache header |

### Refactoring History

| Document | Description |
|---|---|
| [Clean Architecture Migration](../refactor/COMPLETED/clean-architecture-migration.md) | Migration from flat structure to layered architecture |
| [Pydantic Domain Removal](../refactor/COMPLETED/pydantic-domain-removal.md) | Removing Pydantic from the domain layer |
| [German-English Translation](../refactor/COMPLETED/german-english-translation.md) | Codebase localization from German to English |

### Developer Reference

| Document | Description |
|---|---|
| [Python Best Practices](../PYTHON-BEST-PRACTICES.md) | Coding standards, typing rules, async patterns |

---

## Technology Stack

| Component | Technology | Purpose |
|---|---|---|
| Web framework | FastAPI + Uvicorn | HTTP API (Torznab endpoints) |
| Static scraping | httpx | HTML parsing for Python plugins |
| Dynamic scraping | Playwright (Chromium) | JS-heavy sites, Cloudflare bypass |
| Release parsing | guessit | Release name parsing for title matching |
| Configuration | pydantic-settings | Typed config with env/YAML/CLI support |
| Caching | diskcache (+ optional Redis) | Search result and CrawlJob storage |
| Logging | structlog | Structured JSON/console logging |
| CLI | Typer | Local debugging and diagnostics |
| Testing | pytest | 3963 tests across all layers (3742 unit + 158 E2E + 25 integration + 38 live) |

---

## Project Layout

```
src/scavengarr/
  domain/                  # Enterprise business rules
    entities.py            # TorznabQuery, TorznabItem, TorznabCaps
    plugins/               # Plugin schema, protocol, exceptions
    ports/                 # Abstract contracts (Protocol classes)
  application/             # Application business rules
    use_cases/             # TorznabSearch, TorznabCaps, TorznabIndexers
    factories/             # CrawlJob factory
  infrastructure/          # Interface adapters
    plugins/               # Registry, loader, base classes (HttpxPluginBase, PlaywrightPluginBase)
    torznab/               # HttpxSearchEngine + XML presenter
    validation/            # Link validator (HEAD/GET)
    cache/                 # diskcache adapter
    stremio/               # Stream converter, sorter, TMDB client, title matcher
    hoster_resolvers/      # 56 resolvers (27 XFS consolidated + 12 generic DDL + 17 individual)
    config/                # Settings, logging
    common/                # Parsers, converters, extractors, HTML selectors
  interfaces/              # Frameworks & drivers
    api/                   # FastAPI routers (Torznab + Stremio)
    cli/                   # Typer CLI
    composition/           # Dependency injection

plugins/                   # Plugin directory (42 Python plugins)
  filmpalast_to.py         # Python plugin example (httpx)
  boerse.py                # Python plugin example (Playwright)
  einschalten.py           # Python plugin example (httpx API)

tests/
  e2e/                     # 158 E2E tests (Torznab + Stremio endpoints)
  integration/             # 25 integration tests (config, crawljob, links, pipeline)
  live/                    # 38 live smoke tests (plugins + resolver contract tests)
  unit/
    domain/                # Pure domain tests
    application/           # Use case tests (mocked ports)
    infrastructure/        # Adapter, parser, resolver, and plugin tests (~90 files)
    interfaces/            # Router tests
```

---

## How to Use These Docs

1. **New to Scavengarr?** Start with [FEATURES.md](./FEATURES.md) for a bird's-eye view.
2. **Writing a new plugin?** Read [Plugin System](./plugin-system.md) and [Python Plugins](./python-plugins.md).
3. **Understanding multi-stage scraping?** Read [Multi-Stage Scraping](./multi-stage-scraping.md).
4. **Setting up Prowlarr?** Read [Prowlarr Integration](./prowlarr-integration.md) and [Torznab API](./torznab-api.md).
5. **Understanding the architecture?** Read [Clean Architecture](../architecture/clean-architecture.md).
6. **Configuring the system?** Read [Configuration](./configuration.md).
