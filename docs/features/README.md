# Scavengarr Feature Documentation

> Central index for all feature documentation. Start here to navigate the system.

Scavengarr is a self-hosted, container-ready **Torznab/Newznab indexer** for Prowlarr and other Arr applications. It scrapes sources via two engines (Scrapy for static HTML, Playwright for JS-heavy sites) and delivers results through standard Torznab endpoints.

**Version:** 0.1.0 | **Python:** 3.12+ | **Tests:** 1894 unit tests | **Plugins:** 32 (29 Python + 3 YAML) | **Architecture:** Clean Architecture

---

## Quick Navigation

### Feature Handbook

| Document | Description |
|---|---|
| [FEATURES.md](./FEATURES.md) | Compact feature handbook -- all features at a glance |

### Core Features

| Document | Description |
|---|---|
| [Plugin System (YAML)](./plugin-system.md) | Declarative YAML plugin authoring, schema reference, selectors |
| [Python Plugins](./python-plugins.md) | Imperative Python plugin development, protocol, examples |
| [Multi-Stage Scraping](./multi-stage-scraping.md) | Search-Detail-Links pipeline, stage types, parallel execution |
| [CrawlJob System](./crawljob-system.md) | Multi-link packaging for JDownloader integration |
| [Torznab API](./torznab-api.md) | Torznab/Newznab endpoint reference, XML format, Prowlarr compat |
| [Link Validation](./link-validation.md) | HEAD/GET validation strategy, parallel checking, status policies |
| [Configuration](./configuration.md) | YAML/ENV/CLI config, precedence rules, all settings |

### Streaming & Integration

| Document | Description |
|---|---|
| [Stremio Addon](./stremio-addon.md) | Stremio integration with catalog, streams, and hoster resolution |
| [Hoster Resolvers](./hoster-resolvers.md) | Video URL extraction from VOE, Streamtape, SuperVideo, DoodStream, Filemoon |
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
| [Playwright Engine](../plans/playwright-engine.md) | Native Playwright scraping engine for JS-heavy sites |
| [More Plugins](../plans/more-plugins.md) | Planned plugin targets and community contributions |
| [Integration Tests](../plans/integration-tests.md) | End-to-end testing strategy with deterministic fixtures |
| [Search Caching](../plans/search-caching.md) | Response caching layer for repeated queries |

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
| Static scraping | Scrapy | HTML parsing for YAML plugins |
| Dynamic scraping | Playwright (Chromium) | JS-heavy sites, Cloudflare bypass |
| HTTP client | httpx | Link validation, async requests, Python plugins |
| Release parsing | guessit | Release name parsing for title matching |
| Configuration | pydantic-settings | Typed config with env/YAML/CLI support |
| Caching | diskcache (+ optional Redis) | Search result and CrawlJob storage |
| Logging | structlog | Structured JSON/console logging |
| CLI | Typer | Local debugging and diagnostics |
| Testing | pytest | 1894 unit tests across all layers |

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
    scraping/              # Scrapy search engine
    validation/            # Link validator (HEAD/GET)
    cache/                 # diskcache adapter
    torznab/               # XML presenter
    stremio/               # Stream converter, sorter, TMDB client, title matcher
    hoster_resolvers/      # VOE, Streamtape, SuperVideo, DoodStream, Filemoon
    config/                # Settings, logging
    common/                # Parsers, converters, extractors, HTML selectors
  interfaces/              # Frameworks & drivers
    api/                   # FastAPI routers (Torznab + Stremio)
    cli/                   # Typer CLI
    composition/           # Dependency injection

plugins/                   # Plugin directory (29 Python + 3 YAML)
  filmpalast_to.yaml       # YAML plugin example
  boerse.py                # Python plugin example (Playwright)
  einschalten.py           # Python plugin example (httpx API)

tests/
  unit/
    domain/                # Pure domain tests
    application/           # Use case tests (mocked ports)
    infrastructure/        # Adapter and parser tests
```

---

## How to Use These Docs

1. **New to Scavengarr?** Start with [FEATURES.md](./FEATURES.md) for a bird's-eye view.
2. **Writing a YAML plugin?** Read [Plugin System](./plugin-system.md) and [Multi-Stage Scraping](./multi-stage-scraping.md).
3. **Writing a Python plugin?** Read [Python Plugins](./python-plugins.md).
4. **Setting up Prowlarr?** Read [Prowlarr Integration](./prowlarr-integration.md) and [Torznab API](./torznab-api.md).
5. **Understanding the architecture?** Read [Clean Architecture](../architecture/clean-architecture.md).
6. **Configuring the system?** Read [Configuration](./configuration.md).
