[← Back to Index](../../features/README.md)

# Refactor: Clean Architecture Migration

**Status:** Completed
**Commits:** `7726ba8` through `6f770b8` (plus follow-up commits through `028c932`)
**Date:** Pre-v0.1.0

## Summary

The codebase was restructured into four Clean Architecture layers: Domain, Application, Infrastructure, and Interfaces. The migration ran in three phases plus several follow-up commits. Some modules named below were later removed or renamed; this is noted inline as "(later removed in …)" or "(now …)".

## Motivation

The original codebase had:
- Pydantic `BaseModel` in the domain plugin layer (`StageResult`, plugin schema in `domain/plugins/schema.py`) — a framework dependency in the innermost layer
- `SearchResult` defined twice (domain and the Torznab search engine adapter)
- Adapter logic (scraping, presentation) spread across `adapters/`, `interfaces/` and `infrastructure/`
- No clear separation between "what the system does" (use cases) and "how it does it" (adapters)
- The composition root lived in the wrong layer (`infrastructure/composition.py`)

Clean Architecture provides:
- Testability: inner layers can be tested without frameworks or I/O
- Flexibility: adapters can be swapped without touching business logic
- Clarity: each layer has a defined responsibility and dependency direction

## Phase 1: Remove Pydantic from Domain Layer

**Commit:** `7726ba8` — *refactor(phase1): remove Pydantic from Domain layer*

### What Changed

The Torznab entities (`TorznabQuery`, `TorznabItem`, `TorznabCaps`) and `CrawlJob` were already plain `@dataclass` classes. Phase 1 removed the remaining Pydantic models from the domain:

- `StageResult` in `domain/plugins/base.py` converted from `BaseModel` to `@dataclass` (later removed in `03454a8`)
- `domain/plugins/schema.py` (Pydantic plugin schema) deleted and replaced by pure dataclasses in `domain/plugins/plugin_schema.py` (YAML-related parts such as `YamlPluginDefinition` later removed in `42fced9`; `AuthConfig` and `HttpOverrides` remain)
- Pydantic validation moved to `infrastructure/plugins/validation_schema.py` (later removed in `b72c420`) with an adapter layer `infrastructure/plugins/adapters.py` converting Pydantic models to domain dataclasses (later removed in `352f7ca`)
- `SearchResult.metadata` got a `field(default_factory=dict)` default instead of a shared `None`/mutable default

### Before

```python
from pydantic import BaseModel

class StageResult(BaseModel):
    ...
```

### After

```python
from dataclasses import dataclass, field

@dataclass
class StageResult:
    ...

@dataclass
class SearchResult:
    ...
    metadata: dict[str, Any] = field(default_factory=dict)
```

### Key Decisions

- Used `field(default_factory=...)` to avoid mutable default arguments
- Kept `frozen=True` for value objects (immutable by design)
- Moved Pydantic validation of plugin definitions to the Infrastructure layer (validate with Pydantic, then convert to domain dataclasses)
- Pydantic remained in the Infrastructure layer for config parsing (`pydantic-settings`)

See also: `docs/refactor/COMPLETED/pydantic-domain-removal.md` for detailed entity changes.

## Phase 2: Consolidate SearchResult Definition

**Commit:** `b7bc0be` — *refactor(phase2): consolidate SearchResult definition*

### What Changed

`SearchResult` was defined twice. The duplicate in the Torznab search engine adapter was removed, so a single canonical definition remains in the Domain layer.

### Before

- `SearchResult` in `infrastructure/torznab/httpx_scrapy_engine.py` (adapter copy)
- `SearchResult` in `domain/plugins/base.py`
- `application/factories/crawljob_factory.py` imported `SearchResult` from infrastructure (`TYPE_CHECKING` import)

### After

- Single `SearchResult` dataclass in `src/scavengarr/domain/plugins/base.py`
- All layers import from this single source
- The adapter's `_convert_to_result` populates all `SearchResult` fields
- Current fields: `title`, `download_link`, `seeders`, `leechers`, `size`, `release_name`, `description`, `published_date`, `download_links`, `source_url`, `scraped_from_stage`, `validated_links`, `metadata`, `category`, `grabs`, `download_volume_factor`, `upload_volume_factor`

## Phase 3: Reorganize Adapters to Infrastructure Layer

**Commit:** `d97d7a3` — *refactor(phase3): reorganize adapters to infrastructure layer*

### What Changed

The last package under `src/scavengarr/adapters/` (the Scrapy scraping adapter) moved into the `src/scavengarr/infrastructure/` namespace. Together with the follow-up commits, infrastructure was organized by concern:

```text
infrastructure/
  cache/               # DiskcacheAdapter, RedisAdapter, factory (added in d32066c)
  config/              # YAML/ENV/CLI configuration loading
  logging/             # structlog setup, formatters
  persistence/         # CrawlJob cache repository
  plugins/             # Plugin registry, YAML loader (later removed in 42fced9), Python loader
  scraping/            # ScrapyAdapter (later removed in 42fced9)
  torznab/             # Search engine, presenter (XML rendering)
  validation/          # HttpLinkValidator
  common/              # Shared utilities (parsers, converters; extractors later removed in 6314d34)
```

### Key Moves

| Before | After | Commit |
|---|---|---|
| `adapters/scraping/scrapy_adapter.py` | `infrastructure/scraping/scrapy_adapter.py` (later removed in `42fced9`) | `d97d7a3` |
| `interfaces/api/torznab/presenter.py` | `infrastructure/torznab/presenter.py` | `8729319` |
| `infrastructure/torznab/httpx_scrapy_engine.py` | `infrastructure/torznab/search_engine.py` | `56b48df` |
| (new) | `infrastructure/cache/{cache_factory,diskcache_adapter,redis_adapter}.py` | `d32066c` |
| Various utility functions | `infrastructure/common/{parsers,converters,extractors}.py` (extractors later removed in `6314d34`) | `b0f4cca` |
| `infrastructure/composition.py` | `interfaces/composition.py` | `a9eab40` |

## Follow-Up Commits

After the three main phases, several commits completed the migration:

| Commit | Description |
|---|---|
| `8729319` | Move presenter to infrastructure layer |
| `56b48df` | Rename `httpx_scrapy_engine` to `search_engine` |
| `d32066c` | Add cache adapters and factory (`cache_factory.py`) |
| `de788dc` | Use shared size parser in the presenter |
| `7610b9b` | Consolidate duplicate int parsing |
| `b0f4cca` | Add common utils structure |
| `a9eab40` | Move composition root to interfaces layer |
| `028c932` | Remove redundant `discover()` calls from use cases |
| `f419b5e` | Parallelize multi-stage scraping with `asyncio.gather` |

## Final Architecture

As of `028c932`:

```text
src/scavengarr/
  domain/              # Entities, value objects, protocols (ports)
    entities/          # CrawlJob, TorznabQuery, TorznabItem, etc.
    plugins/           # SearchResult, plugin protocol, plugin_schema dataclasses
    ports/             # CachePort, SearchEnginePort, PluginRegistryPort, etc.
  application/         # Use cases, factories
    use_cases/         # TorznabSearchUseCase, TorznabCapsUseCase, etc.
    factories/         # CrawlJobFactory
  infrastructure/      # Adapter implementations
    cache/             # Diskcache, Redis adapters
    config/            # Configuration loading
    logging/           # Structured logging
    persistence/       # CrawlJob cache repository
    plugins/           # Plugin registry and loaders
    scraping/          # ScrapyAdapter (later removed in 42fced9)
    torznab/           # Search engine, XML presenter
    validation/        # Link validator
    common/            # Shared parsers, converters, extractors (extractors later removed in 6314d34)
  interfaces/          # HTTP routers, CLI, composition root
    api/               # FastAPI routers (torznab/, download/)
    cli/               # argparse CLI (cli.py, now __main__.py)
    main.py            # FastAPI app factory (now app.py)
    composition.py     # Dependency injection (composition root)
```

Since then the tree grew: `domain/entities/` gained `stremio.py` and `scoring.py`, `application/` gained `stremio/`, `infrastructure/` gained `hoster_resolvers/`, `scoring/`, `stremio/`, `tmdb/` and top-level modules such as `circuit_breaker.py`, `concurrency.py` and `metrics.py`, and `interfaces/api/` gained `stremio/` and `stats/` routers. See `docs/architecture/codeplan.md` for the current layout.

## Dependency Rule Verification

After migration, the dependency rule holds:
- Domain imports nothing from outer layers
- Application imports only Domain (protocols)
- Infrastructure implements Domain protocols, uses external libraries
- Interfaces wires everything together, contains no business logic

## Lessons Learned

1. **Do it in phases.** Attempting all three phases at once would have been error-prone. Each phase had a clear scope and could be tested independently.
2. **Keep the test suite green.** Every phase commit passed all existing tests. This required updating imports throughout, but the test suite caught every missed reference.
3. **Pydantic belongs in Infrastructure.** Using it for domain entities was convenient but violated the dependency rule. `@dataclass` is sufficient for entities.
4. **Composition root placement matters.** Initially it lived in the infrastructure layer, but it belongs in interfaces (the outermost layer that knows about all concrete types).
