# Refactor: Clean Architecture Migration

**Status:** Completed
**Commits:** `7726ba8` through `6f770b8` (plus follow-up commits through `a9eab40`)
**Date:** Pre-v0.1.0

## Summary

The codebase was restructured from a flat, ad-hoc layout into four Clean Architecture
layers: Domain, Application, Infrastructure, and Interfaces. This was the largest
refactoring effort in the project's history, executed in three phases plus several
follow-up commits.

## Motivation

The original codebase had:
- Pydantic `BaseModel` in domain entities (framework dependency in the innermost layer)
- `SearchResult` defined in multiple places with inconsistent fields
- Adapter logic (scraping, caching, presentation) mixed into application and domain layers
- No clear separation between "what the system does" (use cases) and "how it does it" (adapters)
- The composition root lived in the wrong layer

Clean Architecture provides:
- Testability: inner layers can be tested without frameworks or I/O
- Flexibility: adapters can be swapped without touching business logic
- Clarity: each layer has a defined responsibility and dependency direction

## Phase 1: Remove Pydantic from Domain Layer

**Commit:** `7726ba8` - *refactor(phase1): remove Pydantic from Domain layer*

### What Changed
Domain entities (`TorznabQuery`, `TorznabItem`, `TorznabCaps`, `CrawlJob`, etc.) were
converted from Pydantic `BaseModel` subclasses to plain Python `@dataclass` classes.

### Before
```python
from pydantic import BaseModel

class TorznabQuery(BaseModel):
    t: str
    q: str | None = None
    cat: list[int] = []
```

### After
```python
from dataclasses import dataclass, field

@dataclass
class TorznabQuery:
    t: str
    q: str | None = None
    cat: list[int] = field(default_factory=list)
```

### Key Decisions
- Used `field(default_factory=list)` to avoid mutable default arguments
- Kept `frozen=True` for value objects (immutable by design)
- Moved validation logic to factory functions in the Application layer
- Pydantic remained in the Infrastructure layer for config parsing (`pydantic-settings`)

See also: `docs/refactor/COMPLETED/pydantic-domain-removal.md` for detailed entity changes.

## Phase 2: Consolidate SearchResult Definition

**Commit:** `b7bc0be` - *refactor(phase2): consolidate SearchResult definition*

### What Changed
`SearchResult` was defined in multiple modules with slightly different fields. All
definitions were consolidated into a single canonical location in the Domain layer.

### Before
- `SearchResult` in scraping adapter (with adapter-specific fields)
- `SearchResult` in domain (minimal)
- Implicit result dicts in some code paths

### After
- Single `SearchResult` dataclass in `src/scavengarr/domain/plugins/base.py`
- All layers import from this single source
- Fields: `title`, `download_link`, `download_links`, `source_url`, `category`, `size`, `description`

## Phase 3: Reorganize Adapters to Infrastructure Layer

**Commit:** `d97d7a3` - *refactor(phase3): reorganize adapters to infrastructure layer*

### What Changed
Adapter implementations were moved from scattered locations into the
`src/scavengarr/infrastructure/` namespace, organized by concern:

```
infrastructure/
  cache/               # DiskcacheAdapter, RedisAdapter, factory
  config/              # YAML/ENV/CLI configuration loading
  logging/             # structlog setup, formatters
  plugins/             # Plugin registry, YAML parser, Python loader
  scraping/            # ScrapyAdapter (search engine)
  torznab/             # Presenter, XML rendering
  validation/          # HttpLinkValidator
  common/              # Shared utilities (parsers, converters, extractors)
```

### Key Moves
| Before | After |
|---|---|
| `adapters/scrapy_engine.py` | `infrastructure/scraping/search_engine.py` |
| `adapters/presenter.py` | `infrastructure/torznab/presenter.py` |
| `adapters/cache.py` | `infrastructure/cache/diskcache_adapter.py` |
| `core/plugin_loader.py` | `infrastructure/plugins/registry.py` |
| Various utility functions | `infrastructure/common/{parsers,converters,extractors}.py` |

## Follow-Up Commits

After the three main phases, several commits completed the migration:

| Commit | Description |
|---|---|
| `8729319` | Move presenter to infrastructure layer |
| `56b48df` | Rename `httpx_scrapy_engine` to `search_engine` |
| `d32066c` | Rename cache factory for consistency |
| `de788dc` | Use shared size parser in interfaces |
| `7610b9b` | Consolidate duplicate int parsing |
| `b0f4cca` | Add common utils structure |
| `a9eab40` | Move composition root to interfaces layer |
| `028c932` | Remove redundant `discover()` calls from use cases |
| `f419b5e` | Parallelize multi-stage scraping with `asyncio.gather` |

## Final Architecture

```
src/scavengarr/
  domain/              # Entities, value objects, protocols (ports)
    entities/          # CrawlJob, TorznabQuery, TorznabItem, etc.
    plugins/           # SearchResult, plugin base definitions
    ports/             # CachePort, SearchEnginePort, PluginRegistryPort, etc.
  application/         # Use cases, factories
    use_cases/         # TorznabSearchUseCase, TorznabCapsUseCase, etc.
    factories/         # CrawlJobFactory
  infrastructure/      # Adapter implementations
    cache/             # Diskcache, Redis adapters
    config/            # Configuration loading
    logging/           # Structured logging
    plugins/           # Plugin registry and loaders
    scraping/          # ScrapyAdapter (search engine)
    torznab/           # XML presenter
    validation/        # Link validator
    common/            # Shared parsers, converters, extractors
  interfaces/          # HTTP router, CLI, composition root
    http/              # FastAPI router
    cli/               # Typer CLI
    deps.py            # Dependency injection (composition root)
```

## Dependency Rule Verification

After migration, the dependency rule holds:
- Domain imports nothing from outer layers
- Application imports only Domain (protocols)
- Infrastructure implements Domain protocols, uses external libraries
- Interfaces wires everything together, contains no business logic

## Lessons Learned

1. **Do it in phases.** Attempting all three phases at once would have been error-prone.
   Each phase had a clear scope and could be tested independently.

2. **Keep the test suite green.** Every phase commit passed all existing tests. This
   required updating imports throughout, but the test suite caught every missed reference.

3. **Pydantic belongs in Infrastructure.** Using it for domain entities was convenient
   but violated the dependency rule. `@dataclass` is sufficient for entities.

4. **Composition root placement matters.** Initially it lived in the application layer,
   but it belongs in interfaces (the outermost layer that knows about all concrete types).
