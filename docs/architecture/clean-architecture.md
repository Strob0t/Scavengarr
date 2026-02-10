[← Back to Index](../features/README.md)

# Clean Architecture

Scavengarr follows **Clean Architecture** (Robert C. Martin) to enforce strict separation of concerns.
Every module lives in one of four concentric layers. Dependencies always point **inward** -- outer layers depend on inner layers, never the reverse.

```
┌────────────────────────────────────────────────┐
│  Interfaces (Controllers, CLI, HTTP Router)    │  ← Frameworks & Drivers
├────────────────────────────────────────────────┤
│  Infrastructure (Scrapy, Playwright, Cache)    │  ← Interface Adapters
├────────────────────────────────────────────────┤
│  Application (Use Cases, Factories, Services)  │  ← Application Business Rules
├────────────────────────────────────────────────┤
│  Domain (Entities, Value Objects, Protocols)   │  ← Enterprise Business Rules
└────────────────────────────────────────────────┘
```

---

## Table of Contents

1. [The Dependency Rule](#the-dependency-rule)
2. [Layer 1 -- Domain](#layer-1----domain)
3. [Layer 2 -- Application](#layer-2----application)
4. [Layer 3 -- Infrastructure](#layer-3----infrastructure)
5. [Layer 4 -- Interfaces](#layer-4----interfaces)
6. [Composition Root](#composition-root)
7. [Request Flow](#request-flow)
8. [Error Mapping](#error-mapping)
9. [Testing Strategy Per Layer](#testing-strategy-per-layer)
10. [Key Design Decisions](#key-design-decisions)

---

## The Dependency Rule

The single most important principle:

> **Source code dependencies must point inward only.**

| Layer | May import from | Must NOT import from |
|---|---|---|
| Domain | stdlib, `typing`, `dataclasses`, `enum` | Application, Infrastructure, Interfaces |
| Application | Domain | Infrastructure, Interfaces |
| Infrastructure | Domain, Application (ports only) | Interfaces |
| Interfaces | All layers | -- |

Scavengarr enforces this through **Protocols** (PEP 544). The Domain layer defines abstract contracts (ports) that Infrastructure implements (adapters). Application orchestrates business logic by depending only on these protocols, never on concrete implementations.

### What this means in practice

```python
# src/scavengarr/domain/ports/cache.py  -- Domain defines the contract
class CachePort(Protocol):
    async def get(self, key: str) -> Any: ...
    async def set(self, key: str, value: Any, *, ttl: int | None = None) -> None: ...

# src/scavengarr/infrastructure/cache/diskcache_adapter.py  -- Infrastructure implements it
class DiskcacheAdapter:  # implicitly satisfies CachePort
    async def get(self, key: str) -> Any: ...
    async def set(self, key: str, value: Any, *, ttl: int | None = None) -> None: ...

# src/scavengarr/application/use_cases/torznab_search.py  -- Application uses the port
class TorznabSearchUseCase:
    def __init__(self, ..., engine: SearchEnginePort, ...):
        self.engine = engine  # depends on Protocol, not DiskcacheAdapter
```

---

## Layer 1 -- Domain

**Location:** `src/scavengarr/domain/`

The Domain layer contains enterprise business rules. It is **framework-free** and **I/O-free** -- no network calls, no file access, no third-party libraries beyond the standard library and `typing`.

### Entities

Entities are long-lived business objects with identity. They are implemented as `@dataclass` classes.

| Entity | File | Purpose |
|---|---|---|
| `TorznabQuery` | `entities/torznab.py` | Immutable (`frozen=True`) query parameters: action, plugin_name, query, category, pagination |
| `TorznabItem` | `entities/torznab.py` | Immutable search result item: title, download_url, seeders, size, category, job_id |
| `TorznabCaps` | `entities/torznab.py` | Server capabilities metadata (title, version, limits) |
| `TorznabIndexInfo` | `entities/torznab.py` | Indexer info for listing (name, version, mode) |
| `CrawlJob` | `entities/crawljob.py` | Mutable JDownloader `.crawljob` file representation with TTL and serialization |
| `SearchResult` | `plugins/base.py` | Normalized scraping result with download links and metadata |
| `StageResult` | `plugins/base.py` | Intermediate result from a single scraping stage |

### Value Objects

Value objects are immutable configuration types (`frozen=True` dataclasses):

| Value Object | File | Purpose |
|---|---|---|
| `YamlPluginDefinition` | `plugins/plugin_schema.py` | Complete YAML plugin configuration (name, base_url, stages, auth) |
| `ScrapingConfig` | `plugins/plugin_schema.py` | Scraping mode and stage definitions |
| `ScrapingStage` | `plugins/plugin_schema.py` | Single stage in multi-stage pipeline (name, type, selectors) |
| `StageSelectors` | `plugins/plugin_schema.py` | CSS selectors for data extraction in a stage |
| `NestedSelector` | `plugins/plugin_schema.py` | Complex nested extraction with grouping strategies |
| `AuthConfig` | `plugins/plugin_schema.py` | Authentication settings (none/basic/form/cookie) |
| `HttpOverrides` | `plugins/plugin_schema.py` | Per-plugin HTTP configuration overrides |
| `PaginationConfig` | `plugins/plugin_schema.py` | Pagination settings for list stages |

### Enums

| Enum | File | Purpose |
|---|---|---|
| `BooleanStatus` | `entities/crawljob.py` | JDownloader tri-state: TRUE / FALSE / UNSET |
| `Priority` | `entities/crawljob.py` | Download priority: HIGHEST through LOWER |

### Ports (Protocols)

Ports define the boundaries between Application and Infrastructure. All are `Protocol` classes (not ABCs).

| Port | File | Sync/Async | Methods |
|---|---|---|---|
| `CachePort` | `ports/cache.py` | async | `get`, `set`, `delete`, `exists`, `clear`, `aclose` |
| `SearchEnginePort` | `ports/search_engine.py` | async | `search`, `validate_results` |
| `PluginRegistryPort` | `ports/plugin_registry.py` | sync | `discover`, `list_names`, `get` |
| `LinkValidatorPort` | `ports/link_validator.py` | async | `validate`, `validate_batch` |
| `CrawlJobRepository` | `ports/crawljob_repository.py` | async | `save`, `get` |

Key design choice: `PluginRegistryPort` is **synchronous** (plugin files are loaded from disk, not from network). All other ports are **asynchronous** because they involve I/O (HTTP, cache, validation).

### Exception Hierarchy

```
TorznabError (base)
├── TorznabBadRequest         → HTTP 400
├── TorznabUnsupportedAction  → HTTP 422
├── TorznabNoPluginsAvailable → HTTP 503
├── TorznabPluginNotFound     → HTTP 404
├── TorznabUnsupportedPlugin  → HTTP 422
└── TorznabExternalError      → HTTP 502 (dev) / 200 (prod)

PluginError (base)
├── PluginValidationError     YAML schema validation failure
├── PluginLoadError           Python plugin import failure
├── PluginNotFoundError       Plugin name not in registry
└── DuplicatePluginError      Two plugins share the same name
```

Domain exceptions carry business meaning. The Interfaces layer maps them to HTTP status codes.

---

## Layer 2 -- Application

**Location:** `src/scavengarr/application/`

The Application layer contains use cases that orchestrate business logic. It knows about Domain entities and ports, but never about concrete adapters.

### Use Cases

| Use Case | File | Type | Description |
|---|---|---|---|
| `TorznabSearchUseCase` | `use_cases/torznab_search.py` | async | Full search pipeline: validate query, resolve plugin, execute search, create CrawlJobs, return TorznabItems |
| `TorznabCapsUseCase` | `use_cases/torznab_caps.py` | sync | Build capabilities XML for a named plugin |
| `TorznabIndexersUseCase` | `use_cases/torznab_indexers.py` | sync | List all discovered plugins with metadata |

#### TorznabSearchUseCase -- the central orchestrator

This is the most important use case. Its `execute()` method implements the full search flow:

```python
# src/scavengarr/application/use_cases/torznab_search.py
async def execute(self, q: TorznabQuery) -> list[TorznabItem]:
    # 1. Validate query (action, query string, plugin name)
    # 2. Resolve plugin from PluginRegistryPort
    # 3. Route to YAML or Python plugin execution path
    #    - YAML: engine.search(plugin, query)  [multi-stage scraping]
    #    - Python: plugin.search(query) + engine.validate_results()
    # 4. Convert SearchResults -> TorznabItems + CrawlJobs
    # 5. Save CrawlJobs to repository
    # 6. Return enriched TorznabItems (with job_id)
```

**Dependency injection:** The use case receives all dependencies via constructor (`__init__`), never creating them internally:

```python
class TorznabSearchUseCase:
    def __init__(
        self,
        plugins: PluginRegistryPort,
        engine: SearchEnginePort,
        crawljob_factory: CrawlJobFactory,
        crawljob_repo: CrawlJobRepository,
    ): ...
```

### Factories

| Factory | File | Description |
|---|---|---|
| `CrawlJobFactory` | `factories/crawljob_factory.py` | Converts `SearchResult` to `CrawlJob` entity |

The factory encapsulates CrawlJob creation logic: TTL calculation, URL bundling, comment generation, and JDownloader field mapping.

```python
# src/scavengarr/application/factories/crawljob_factory.py
class CrawlJobFactory:
    def __init__(self, *, default_ttl_hours: int = 1, auto_start: bool = True, ...):
        ...

    def create_from_search_result(self, result: SearchResult) -> CrawlJob:
        # Bundle validated_links into text field (newline-separated)
        # Set package_name from result.title
        # Build comment from description + size + source_url
        # Apply TTL, priority, auto_start settings
```

---

## Layer 3 -- Infrastructure

**Location:** `src/scavengarr/infrastructure/`

Infrastructure implements the ports defined by Domain and provides concrete adapters for external systems.

### Port Implementations

| Port | Adapter | File |
|---|---|---|
| `CachePort` | `DiskcacheAdapter` | `cache/diskcache_adapter.py` |
| `CachePort` | `RedisAdapter` | `cache/redis_adapter.py` |
| `SearchEnginePort` | `HttpxScrapySearchEngine` | `torznab/search_engine.py` |
| `PluginRegistryPort` | `PluginRegistry` | `plugins/registry.py` |
| `LinkValidatorPort` | `HttpLinkValidator` | `validation/http_link_validator.py` |
| `CrawlJobRepository` | `CacheCrawlJobRepository` | `persistence/crawljob_cache.py` |

### Subsystems

**Cache** (`cache/`): Two interchangeable adapters behind `CachePort`. A factory function (`create_cache()`) selects the backend based on configuration.

**Plugins** (`plugins/`): Discovery, loading, validation, and caching of YAML and Python plugins. The `PluginRegistry` indexes files lazily and caches loaded plugins in memory.

**Scraping** (`scraping/`): The `ScrapyAdapter` executes multi-stage CSS-selector-based scraping with httpx. The `StageScraper` handles individual stage execution including data extraction, link extraction, and pagination.

**Search Engine** (`torznab/search_engine.py`): Orchestrates ScrapyAdapter and HttpLinkValidator. Converts raw stage results to SearchResult objects, deduplicates, and filters by link validity.

**Presenter** (`torznab/presenter.py`): Renders Domain entities (TorznabCaps, TorznabItem) to Torznab-compliant RSS 2.0 XML.

**Validation** (`validation/`): HTTP-based link validation with HEAD-first, GET-fallback strategy and bounded concurrency (semaphore).

**Persistence** (`persistence/`): CrawlJob storage backed by the cache port (pickle serialization).

**Configuration** (`config/`): Layered config loading (defaults < YAML < ENV < CLI) with Pydantic validation.

**Logging** (`logging/`): Structured logging via structlog with async QueueHandler for non-blocking emission.

**Common Utilities** (`common/`): Pure functions for type conversion (`to_int`), size parsing (`parse_size_to_bytes`), and data extraction.

---

## Layer 4 -- Interfaces

**Location:** `src/scavengarr/interfaces/`

The Interfaces layer handles input/output exclusively. It contains no business logic.

### HTTP (FastAPI)

| File | Purpose |
|---|---|
| `main.py` | `build_app()` factory: creates FastAPI instance, registers routers, adds request logging middleware |
| `app_state.py` | `AppState` typed container (extends Starlette `State`) for DI resources |
| `composition.py` | `lifespan()` async context manager -- the composition root |
| `api/torznab/router.py` | Torznab endpoints: `GET /api/v1/torznab/indexers`, `GET /api/v1/torznab/{plugin_name}` (caps/search), health |
| `api/download/router.py` | CrawlJob download: `GET /api/v1/download/{job_id}` (serves `.crawljob` files), info endpoint |

### CLI (argparse + Uvicorn)

| File | Purpose |
|---|---|
| `cli/cli.py` | `start()` entry point: parses CLI args, loads config, configures logging, launches Uvicorn |

The CLI is the process entry point. It follows the pattern:
1. Parse arguments (host, port, config path, overrides)
2. Load configuration via `load_config()` with CLI overrides
3. Configure structured logging
4. Build FastAPI app and run via Uvicorn

---

## Composition Root

**File:** `src/scavengarr/interfaces/composition.py`

The composition root is the **only place** where concrete implementations are wired together. It runs inside the FastAPI `lifespan()` async context manager.

### Initialization Order

```
1. Cache (DiskcacheAdapter or RedisAdapter via create_cache())
     ↓
2. HTTP Client (httpx.AsyncClient with configured timeouts)
     ↓
3. Plugin Registry (PluginRegistry with discovery)
     ↓
4. Search Engine (HttpxScrapySearchEngine using HTTP client + cache)
     ↓
5. CrawlJob Repository (CacheCrawlJobRepository using cache)
     ↓
6. CrawlJob Factory (stateless, no external dependencies)
```

### Cleanup Order (reverse)

```
1. HTTP Client → aclose()
2. Cache → aclose()
```

All resources are stored on `AppState` and accessible from any request handler via `request.app.state`.

---

## Request Flow

### Torznab Search (end-to-end)

```
HTTP GET /api/v1/torznab/filmpalast?t=search&q=iron+man
│
├─ Router (torznab/router.py)
│   └─ Parse query params → TorznabQuery
│
├─ TorznabSearchUseCase.execute(query)
│   ├─ Validate: action == "search", query present, plugin_name present
│   ├─ PluginRegistry.get("filmpalast") → YamlPluginDefinition
│   ├─ SearchEngine.search(plugin, "iron man")
│   │   ├─ ScrapyAdapter.scrape(query="iron man")
│   │   │   ├─ Stage 1 (list): fetch search page → extract detail links
│   │   │   └─ Stage 2 (detail): fetch detail pages [parallel] → extract download links
│   │   ├─ Convert stage results → list[SearchResult]
│   │   ├─ Deduplicate by (title, download_link)
│   │   └─ LinkValidator.validate_batch(all_urls) → filter dead links
│   ├─ For each SearchResult:
│   │   ├─ CrawlJobFactory.create_from_search_result() → CrawlJob
│   │   ├─ CrawlJobRepository.save(crawljob)
│   │   └─ TorznabItem with job_id
│   └─ Return list[TorznabItem]
│
├─ Presenter.render_rss_xml(items) → RSS 2.0 XML
│
└─ Response(content=xml, media_type="application/xml")
```

### CrawlJob Download

```
HTTP GET /api/v1/download/{job_id}
│
├─ Router (download/router.py)
│   ├─ CrawlJobRepository.get(job_id) → CrawlJob
│   ├─ Check expiry (is_expired())
│   └─ CrawlJob.to_crawljob_format() → .crawljob content
│
└─ Response(content=crawljob, media_type="application/x-crawljob")
```

---

## Error Mapping

Domain exceptions are translated to HTTP responses in the Torznab router. In production, all errors return empty RSS (HTTP 200) to maintain Prowlarr stability.

| Domain Exception | Dev Status | Prod Status | Behavior |
|---|---|---|---|
| `TorznabBadRequest` | 400 | 200 (empty RSS) | Invalid query parameters |
| `TorznabPluginNotFound` | 404 | 200 (empty RSS) | Plugin not in registry |
| `TorznabUnsupportedAction` | 422 | 200 (empty RSS) | Action not caps/search |
| `TorznabUnsupportedPlugin` | 422 | 200 (empty RSS) | Unsupported scraping mode |
| `TorznabNoPluginsAvailable` | 503 | 200 (empty RSS) | No plugins discovered |
| `TorznabExternalError` | 502 | 200 (empty RSS) | Upstream/network failure |
| Unhandled `Exception` | 500 | 200 (empty RSS) | Unexpected error |

---

## Testing Strategy Per Layer

Each layer has a distinct testing approach:

### Domain Tests (pure unit tests)

- No mocking required -- all entities and value objects are pure data.
- Test entity construction, serialization (`to_crawljob_format()`), validation, and expiry logic.
- Test exception hierarchy.

```python
# tests/unit/domain/test_crawljob.py
def test_crawljob_not_expired():
    job = CrawlJob(expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    assert not job.is_expired()
```

### Application Tests (use case tests with mocked ports)

- Mock all ports (`PluginRegistryPort`, `SearchEnginePort`, `CrawlJobRepository`).
- Test orchestration logic: correct flow, error handling, edge cases.
- `PluginRegistryPort` is synchronous -- use `MagicMock`.
- All other ports are async -- use `AsyncMock`.

```python
# tests/unit/application/test_torznab_search.py
async def test_search_returns_items(mock_plugins, mock_engine, ...):
    uc = TorznabSearchUseCase(plugins=mock_plugins, engine=mock_engine, ...)
    items = await uc.execute(TorznabQuery(action="search", query="test", ...))
    assert len(items) > 0
```

### Infrastructure Tests (adapter tests)

- Test concrete implementations with controlled inputs.
- For HTTP adapters: use `respx` for HTTP mocking.
- For cache: test against real DiskCache (temp directory).
- For parsers/converters: pure function tests.

### Integration Tests

- Test HTTP router through `TestClient` with mocked use case dependencies.
- Verify correct XML responses, status codes, and error mapping.

---

## Key Design Decisions

### Why Protocols, not ABCs

- Protocols enable structural subtyping (duck typing with type safety).
- No inheritance required -- adapters satisfy the contract implicitly.
- Easier to test: any object with the right methods qualifies as a mock.

### Why sync PluginRegistryPort

- Plugin discovery reads `.yaml` and `.py` files from a local directory.
- This is fast local I/O, not network I/O.
- Making it async would add unnecessary complexity without benefit.

### Why CrawlJob instead of direct download URLs

- Prowlarr/Sonarr/Radarr expect a single download URL per result.
- Multi-link results (multiple mirror hosters) need bundling.
- CrawlJob provides: stable ID, TTL-based expiry, multi-link packaging.
- The download endpoint serves `.crawljob` files on demand.

### Why empty RSS on errors in production

- Prowlarr treats non-200 responses as indexer failures and may disable the indexer.
- Returning HTTP 200 with empty results preserves Prowlarr stability.
- In development, proper HTTP status codes aid debugging.

### Why layered configuration

- Defaults provide sane out-of-box behavior.
- YAML allows per-deployment configuration.
- Environment variables enable container-native overrides.
- CLI arguments provide per-invocation control.
- Strict precedence (defaults < YAML < ENV < CLI) prevents surprises.

---

## Directory Structure Summary

```
src/scavengarr/
├── domain/                         # Layer 1: Enterprise Business Rules
│   ├── entities/
│   │   ├── crawljob.py             # CrawlJob entity, BooleanStatus, Priority
│   │   └── torznab.py              # TorznabQuery, Item, Caps, IndexInfo, exceptions
│   ├── plugins/
│   │   ├── base.py                 # SearchResult, StageResult, PluginProtocol
│   │   ├── exceptions.py           # Plugin exception hierarchy
│   │   └── plugin_schema.py        # YAML plugin definition value objects
│   └── ports/
│       ├── cache.py                # CachePort
│       ├── crawljob_repository.py  # CrawlJobRepository
│       ├── link_validator.py       # LinkValidatorPort
│       ├── plugin_registry.py      # PluginRegistryPort
│       └── search_engine.py        # SearchEnginePort
│
├── application/                    # Layer 2: Application Business Rules
│   ├── factories/
│   │   └── crawljob_factory.py     # SearchResult → CrawlJob conversion
│   └── use_cases/
│       ├── torznab_caps.py         # Capabilities use case
│       ├── torznab_indexers.py     # Indexer listing use case
│       └── torznab_search.py       # Search use case (orchestrator)
│
├── infrastructure/                 # Layer 3: Interface Adapters
│   ├── cache/                      # CachePort implementations
│   ├── common/                     # Converters, parsers, extractors
│   ├── config/                     # Configuration loading + validation
│   ├── logging/                    # Structured logging setup
│   ├── persistence/                # CrawlJob cache repository
│   ├── plugins/                    # Plugin registry, loader, adapters
│   ├── scraping/                   # ScrapyAdapter (multi-stage engine)
│   ├── torznab/                    # Search engine + XML presenter
│   └── validation/                 # HTTP link validator
│
└── interfaces/                     # Layer 4: Frameworks & Drivers
    ├── api/
    │   ├── download/router.py      # CrawlJob download endpoint
    │   └── torznab/router.py       # Torznab API endpoints
    ├── cli/cli.py                  # CLI entry point
    ├── app_state.py                # Typed DI container
    ├── composition.py              # Composition root (lifespan)
    └── main.py                     # FastAPI application factory
```
