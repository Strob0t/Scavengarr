[← Back to Index](../features/README.md)

# Code Plan

Comprehensive module-by-module documentation of the Scavengarr codebase.
This document covers every source file, its purpose, public API, internal design, and relationships to other modules.

**Version:** 0.1.0
**Architecture:** Clean Architecture (4 layers)
**Runtime:** Python 3.12+, FastAPI, Uvicorn

---

## Table of Contents

1. [Project Layout](#project-layout)
2. [Technology Stack](#technology-stack)
3. [Domain Layer](#domain-layer)
   - [Entities](#entities)
   - [Plugins (Domain Models)](#plugins-domain-models)
   - [Ports](#ports)
4. [Application Layer](#application-layer)
   - [Use Cases](#use-cases)
   - [Factories](#factories)
5. [Infrastructure Layer](#infrastructure-layer)
   - [Cache Subsystem](#cache-subsystem)
   - [Plugin Subsystem](#plugin-subsystem)
   - [Scraping Subsystem](#scraping-subsystem)
   - [Search Engine](#search-engine)
   - [Presenter](#presenter)
   - [Link Validation](#link-validation)
   - [Persistence](#persistence)
   - [Configuration](#configuration)
   - [Logging](#logging)
   - [Common Utilities](#common-utilities)
6. [Interfaces Layer](#interfaces-layer)
   - [FastAPI Application](#fastapi-application)
   - [Composition Root](#composition-root)
   - [Application State](#application-state)
   - [Torznab API Router](#torznab-api-router)
   - [Download API Router](#download-api-router)
   - [CLI](#cli)
7. [Plugin Files](#plugin-files)
8. [Test Suite](#test-suite)
9. [Cross-Cutting Concerns](#cross-cutting-concerns)
10. [Dependency Graph](#dependency-graph)

---

## Project Layout

```
src/scavengarr/
├── domain/
│   ├── __init__.py
│   ├── entities/
│   │   ├── __init__.py               # Re-exports all Torznab types
│   │   ├── crawljob.py               # CrawlJob entity + enums
│   │   └── torznab.py                # Torznab entities + exceptions
│   ├── plugins/
│   │   ├── __init__.py               # Re-exports SearchResult, plugin types, exceptions
│   │   ├── base.py                   # SearchResult, StageResult, Protocols
│   │   ├── exceptions.py             # Plugin exception hierarchy
│   │   └── plugin_schema.py          # YAML plugin value objects
│   └── ports/
│       ├── __init__.py               # Re-exports all port protocols
│       ├── cache.py                  # CachePort
│       ├── crawljob_repository.py    # CrawlJobRepository
│       ├── link_validator.py         # LinkValidatorPort
│       ├── plugin_registry.py        # PluginRegistryPort
│       └── search_engine.py          # SearchEnginePort
├── application/
│   ├── factories/
│   │   ├── __init__.py               # Re-exports CrawlJobFactory
│   │   └── crawljob_factory.py       # SearchResult → CrawlJob conversion
│   └── use_cases/
│       ├── __init__.py
│       ├── torznab_caps.py           # Capabilities use case
│       ├── torznab_indexers.py        # Indexer listing use case
│       └── torznab_search.py         # Search orchestration use case
├── infrastructure/
│   ├── cache/
│   │   ├── __init__.py
│   │   ├── cache_factory.py          # create_cache() factory function
│   │   ├── diskcache_adapter.py      # SQLite-based CachePort impl
│   │   └── redis_adapter.py          # Redis-based CachePort impl
│   ├── common/
│   │   ├── __init__.py
│   │   ├── converters.py             # to_int()
│   │   ├── extractors.py             # extract_download_link()
│   │   └── parsers.py                # parse_size_to_bytes()
│   ├── config/
│   │   ├── __init__.py               # Re-exports AppConfig, load_config
│   │   ├── defaults.py               # DEFAULT_CONFIG dict
│   │   ├── load.py                   # Layered config loading
│   │   └── schema.py                 # AppConfig, CacheConfig, EnvOverrides
│   ├── logging/
│   │   ├── __init__.py
│   │   └── setup.py                  # Structlog + QueueHandler setup
│   ├── persistence/
│   │   └── crawljob_cache.py         # CacheCrawlJobRepository
│   ├── plugins/
│   │   ├── __init__.py               # Re-exports PluginRegistry
│   │   ├── adapters.py               # Pydantic → Domain model conversion
│   │   ├── loader.py                 # YAML + Python plugin loading
│   │   ├── registry.py               # PluginRegistry (lazy loading)
│   │   └── validation_schema.py      # Pydantic validation models
│   ├── scraping/
│   │   ├── __init__.py               # Re-exports ScrapyAdapter
│   │   └── scrapy_adapter.py         # Multi-stage scraping engine
│   ├── torznab/
│   │   ├── presenter.py              # XML rendering (caps + RSS)
│   │   └── search_engine.py          # HttpxScrapySearchEngine
│   └── validation/
│       ├── __init__.py               # Re-exports HttpLinkValidator
│       └── http_link_validator.py    # HEAD/GET link validation
└── interfaces/
    ├── __init__.py
    ├── app_state.py                  # AppState typed container
    ├── composition.py                # Lifespan (DI composition root)
    ├── main.py                       # FastAPI factory
    ├── test.py                       # Test utilities
    ├── api/
    │   ├── __init__.py
    │   ├── download/
    │   │   ├── __init__.py
    │   │   └── router.py             # CrawlJob download endpoints
    │   └── torznab/
    │       ├── __init__.py
    │       └── router.py             # Torznab API endpoints
    └── cli/
        ├── __init__.py
        └── cli.py                    # CLI entry point

plugins/
├── filmpalast.to.yaml               # YAML plugin example
└── boerse.py                        # Python plugin example

tests/
├── conftest.py                       # Shared fixtures
└── unit/
    ├── domain/                       # Pure entity/schema tests
    ├── application/                  # Use case tests (mocked ports)
    └── infrastructure/               # Adapter/parser/converter tests
```

---

## Technology Stack

| Category | Technology | Version | Purpose |
|---|---|---|---|
| Web Framework | FastAPI | ^0.128 | HTTP API server |
| ASGI Server | Uvicorn | ^0.40 | Production ASGI server |
| HTTP Client | httpx | ^0.28 | Async HTTP for scraping and validation |
| HTML Parsing | BeautifulSoup4 | (transitive) | CSS selector extraction |
| Browser Automation | Playwright | ^1.47 | JS-heavy site scraping (future) |
| Spider Framework | Scrapy | ^2.14 | Static HTML scraping engine |
| Structured Logging | structlog | ^25.5 | JSON/console logging |
| Cache (SQLite) | diskcache | ^5.6 | Local persistent cache |
| Cache (Redis) | redis | ^7.1 | Optional distributed cache |
| Configuration | pydantic-settings | ^2.10 | Typed config with env var support |
| Env Files | python-dotenv | ^1.1 | `.env` file loading |
| CLI | argparse (stdlib) | -- | Argument parsing |
| Testing | pytest | ^9.0 | Test runner |
| HTTP Mocking | respx | ^0.22 | httpx request mocking |
| Async Testing | pytest-asyncio | ^1.3 | Async test support |

---

## Domain Layer

### Entities

#### `domain/entities/torznab.py`

Core Torznab entities and the exception hierarchy. All entities are immutable frozen dataclasses.

**TorznabQuery** -- Normalized search input.

```python
# src/scavengarr/domain/entities/torznab.py
@dataclass(frozen=True)
class TorznabQuery:
    action: str           # "search", "caps"
    plugin_name: str      # Plugin identifier (e.g., "filmpalast")
    query: str            # Search query string
    category: int | None = None     # 2000=Movies, 5000=TV
    extended: int | None = None     # 1 = extended search mode
    offset: int | None = None       # Pagination (future)
    limit: int | None = None        # Pagination (future)
```

**TorznabItem** -- A single search result for RSS output.

```python
@dataclass(frozen=True)
class TorznabItem:
    title: str
    download_url: str
    job_id: str | None = None         # CrawlJob reference
    seeders: int | None = None
    peers: int | None = None
    size: str | None = None
    release_name: str | None = None
    description: str | None = None
    source_url: str | None = None     # Detail page URL
    category: int = 2000              # Default: Movies
    grabs: int = 0
    download_volume_factor: float = 0.0  # 0 = Direct Download
    upload_volume_factor: float = 0.0
```

**TorznabCaps** -- Server capabilities metadata.

```python
@dataclass(frozen=True)
class TorznabCaps:
    server_title: str
    server_version: str
    limits_max: int = 100
    limits_default: int = 50
```

**TorznabIndexInfo** -- Indexer info for listing endpoint.

```python
@dataclass(frozen=True)
class TorznabIndexInfo:
    name: str
    version: str | None
    mode: str | None
```

**Exception Hierarchy:**

| Exception | Base | Meaning |
|---|---|---|
| `TorznabError` | `Exception` | Base for all Torznab errors |
| `TorznabBadRequest` | `TorznabError` | Invalid query parameters |
| `TorznabUnsupportedAction` | `TorznabError` | Action not caps/search |
| `TorznabNoPluginsAvailable` | `TorznabError` | No plugins discovered |
| `TorznabPluginNotFound` | `TorznabError` | Named plugin not in registry |
| `TorznabUnsupportedPlugin` | `TorznabError` | Plugin mode not supported |
| `TorznabExternalError` | `TorznabError` | Network/upstream failure |

**Re-exports** (`domain/entities/__init__.py`): All types and exceptions are re-exported for convenient importing as `from scavengarr.domain.entities import TorznabQuery, ...`.

---

#### `domain/entities/crawljob.py`

JDownloader `.crawljob` file entity.

**BooleanStatus** -- JDownloader tri-state enum.

```python
class BooleanStatus(str, Enum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNSET = "UNSET"
```

**Priority** -- Download priority enum.

```python
class Priority(str, Enum):
    HIGHEST = "HIGHEST"
    HIGHER = "HIGHER"
    HIGH = "HIGH"
    DEFAULT = "DEFAULT"
    LOWER = "LOWER"
```

**CrawlJob** -- Mutable entity (not frozen) because fields are populated in stages.

Key fields:
- `job_id: str` -- UUID4, auto-generated
- `text: str` -- Newline-separated download links (required by JDownloader)
- `package_name: str` -- Display name
- `validated_urls: list[str]` -- Validated download links
- `created_at / expires_at: datetime` -- TTL management
- `auto_start / auto_confirm / priority` -- JDownloader behavior flags

Key methods:
- `is_expired() -> bool` -- Checks if `now > expires_at`
- `to_crawljob_format() -> str` -- Serializes to JDownloader property-file format

The serialization output looks like:
```
# Generated by Scavengarr
# Job ID: <uuid>
text=https://hoster1.com/file1\r\nhttps://hoster2.com/file2
packageName=Iron Man 2008
autoStart=TRUE
priority=DEFAULT
...
```

---

### Plugins (Domain Models)

#### `domain/plugins/base.py`

Core plugin domain models and protocols.

**SearchResult** -- Normalized search output from any plugin.

```python
@dataclass
class SearchResult:
    title: str
    download_link: str
    seeders: int | None = None
    leechers: int | None = None
    size: str | None = None
    release_name: str | None = None
    description: str | None = None
    published_date: str | None = None
    download_links: list[dict[str, str]] | None = None  # Alternative links
    source_url: str | None = None          # Detail page URL
    scraped_from_stage: str | None = None  # Which stage produced this
    validated_links: list[str] | None = None  # Post-validation links
    metadata: dict[str, Any] = field(default_factory=dict)
    category: int = 2000
    grabs: int = 0
    download_volume_factor: float = 0.0
    upload_volume_factor: float = 0.0
```

Mutable because fields like `download_link` and `validated_links` are updated during the validation pipeline (e.g., alternative link promotion).

**StageResult** -- Intermediate scraping stage output.

```python
@dataclass
class StageResult:
    url: str            # Page URL that was scraped
    stage_name: str     # Name of the stage
    depth: int          # Recursion depth
    data: dict[str, Any]  # Extracted data
    links: list[str] = field(default_factory=list)  # Links to next stage
```

**PluginProtocol** -- Contract for Python plugins.

```python
class PluginProtocol(Protocol):
    name: str
    async def search(self, query: str, category: int | None = None) -> list[SearchResult]: ...
```

**MultiStagePluginProtocol** -- Extended protocol with stage-level scraping.

```python
class MultiStagePluginProtocol(Protocol):
    name: str
    async def search(self, query: str, category: int | None = None) -> list[SearchResult]: ...
    async def scrape_stage(self, stage_name: str, url: str | None = None, depth: int = 0, **url_params: Any) -> list[StageResult]: ...
```

---

#### `domain/plugins/plugin_schema.py`

Pure domain models for YAML plugin configuration. All are frozen dataclasses -- no validation logic (validation lives in Infrastructure).

**YamlPluginDefinition** -- Top-level plugin definition.

```python
@dataclass(frozen=True)
class YamlPluginDefinition:
    name: str                                    # e.g., "filmpalast"
    version: str                                 # semver, e.g., "1.0.0"
    base_url: str                                # Primary URL
    scraping: ScrapingConfig                     # Scraping mode + stages
    mirror_urls: list[str] | None = None         # Fallback URLs
    auth: AuthConfig | None = None               # Authentication
    http: HttpOverrides | None = None            # Per-plugin HTTP overrides
```

**ScrapingConfig** -- Scraping engine configuration.

```python
@dataclass(frozen=True)
class ScrapingConfig:
    mode: Literal["scrapy", "playwright"]
    # Legacy Playwright fields
    search_url_template: str | None = None
    wait_for_selector: str | None = None
    locators: PlaywrightLocators | None = None
    # Scrapy multi-stage pipeline
    stages: list[ScrapingStage] | None = None
    start_stage: str | None = None
    max_depth: int = 5
    delay_seconds: float = 1.5
```

**ScrapingStage** -- Single pipeline stage.

```python
@dataclass(frozen=True)
class ScrapingStage:
    name: str                                    # e.g., "movie_list"
    type: Literal["list", "detail"]              # list=intermediate, detail=terminal
    selectors: StageSelectors                    # CSS selectors
    url: str | None = None                       # Static URL
    url_pattern: str | None = None               # Dynamic URL template
    next_stage: str | None = None                # Next stage to chain
    pagination: PaginationConfig | None = None
    conditions: dict[str, Any] | None = None     # Processing conditions
```

**StageSelectors** -- CSS selectors for data extraction.

Fields: `link`, `title`, `description`, `release_name`, `download_link`, `seeders`, `leechers`, `size`, `published_date`, `download_links` (nested), `custom` (dict).

**NestedSelector** -- Complex nested extraction.

```python
@dataclass(frozen=True)
class NestedSelector:
    container: str                               # Main container CSS selector
    items: str                                   # Item CSS selector
    fields: dict[str, str] = field(default_factory=dict)
    item_group: str | None = None                # Optional grouping container
    field_attributes: dict[str, list[str]] = field(default_factory=dict)
    multi_value_fields: list[str] | None = None  # Fields collected as lists
```

Two extraction modes:
1. **Direct:** Each `items` element = 1 result
2. **Grouped:** All `items` within each `item_group` are merged into 1 result

**AuthConfig** -- Authentication configuration.

```python
@dataclass(frozen=True)
class AuthConfig:
    type: Literal["none", "basic", "form", "cookie"] = "none"
    username: str | None = None
    password: str | None = None
    login_url: str | None = None
    username_field: str | None = None
    password_field: str | None = None
    submit_selector: str | None = None
    username_env: str | None = None     # Env var name for username
    password_env: str | None = None     # Env var name for password
```

**Other value objects:** `HttpOverrides` (timeout, redirects, user-agent), `PaginationConfig` (enabled, selector, max_pages), `ScrapySelectors` (legacy), `PlaywrightLocators` (legacy).

---

#### `domain/plugins/exceptions.py`

Plugin-specific exception hierarchy.

```
PluginError (base)
├── PluginValidationError   # YAML schema validation failure
├── PluginLoadError         # Python plugin import/protocol failure
├── PluginNotFoundError     # Plugin name not in registry
└── DuplicatePluginError    # Two plugins share the same name
```

---

### Ports

#### `domain/ports/cache.py` -- CachePort

```python
class CachePort(Protocol):
    async def get(self, key: str) -> Any: ...
    async def set(self, key: str, value: Any, *, ttl: int | None = None) -> None: ...
    async def delete(self, key: str) -> bool: ...
    async def exists(self, key: str) -> bool: ...
    async def clear(self) -> None: ...
    async def aclose(self) -> None: ...
    async def __aenter__(self) -> CachePort: ...
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None: ...
```

Implementations: `DiskcacheAdapter`, `RedisAdapter`.

#### `domain/ports/search_engine.py` -- SearchEnginePort

```python
class SearchEnginePort(Protocol):
    async def search(self, plugin: PluginRegistryPort, query: str) -> list[SearchResult]: ...
    async def validate_results(self, results: list[SearchResult]) -> list[SearchResult]: ...
```

Implementation: `HttpxScrapySearchEngine`.

#### `domain/ports/plugin_registry.py` -- PluginRegistryPort

```python
@runtime_checkable
class PluginRegistryPort(Protocol):
    def discover(self) -> None: ...
    def list_names(self) -> list[str]: ...
    def get(self, name: str) -> YamlPluginDefinition | PluginProtocol: ...
```

**Synchronous** -- plugin files are local. Decorated with `@runtime_checkable` for isinstance checks.

Implementation: `PluginRegistry`.

#### `domain/ports/link_validator.py` -- LinkValidatorPort

```python
class LinkValidatorPort(Protocol):
    async def validate(self, url: str) -> bool: ...
    async def validate_batch(self, urls: list[str]) -> dict[str, bool]: ...
```

Implementation: `HttpLinkValidator`.

#### `domain/ports/crawljob_repository.py` -- CrawlJobRepository

```python
class CrawlJobRepository(Protocol):
    async def save(self, job: CrawlJob) -> None: ...
    async def get(self, job_id: str) -> CrawlJob | None: ...
```

Implementation: `CacheCrawlJobRepository`.

---

## Application Layer

### Use Cases

#### `application/use_cases/torznab_search.py` -- TorznabSearchUseCase

The central orchestrator. Coordinates the full search pipeline.

**Constructor dependencies:**

```python
def __init__(
    self,
    plugins: PluginRegistryPort,      # Plugin discovery
    engine: SearchEnginePort,          # Scraping + validation
    crawljob_factory: CrawlJobFactory, # SearchResult → CrawlJob
    crawljob_repo: CrawlJobRepository, # CrawlJob persistence
):
```

**`execute(q: TorznabQuery) -> list[TorznabItem]`** -- Main entry point.

Flow:
1. **Validate** -- action must be "search", query and plugin_name must be present.
2. **Resolve plugin** -- `plugins.get(q.plugin_name)`. Raises `TorznabPluginNotFound`.
3. **Route by type:**
   - Python plugin (has `search()`, no `scraping`): calls `plugin.search()` then `engine.validate_results()`.
   - YAML plugin (has `scraping.mode`): calls `engine.search(plugin, query)`.
4. **Build TorznabItems** -- For each SearchResult:
   - Create TorznabItem with mapped fields.
   - Create CrawlJob via factory.
   - Save CrawlJob to repository.
   - Enrich TorznabItem with `job_id`.
5. **Return** -- list of enriched TorznabItems.

Helper function `_is_python_plugin(plugin)` detects Python plugins by checking for `search()` method without `scraping` attribute.

**Error handling:** Individual CrawlJob creation failures are logged and skipped (degraded result, not crash).

---

#### `application/use_cases/torznab_caps.py` -- TorznabCapsUseCase

Builds capabilities metadata for a named plugin.

**Constructor:**
```python
def __init__(self, *, plugins: PluginRegistryPort, app_name: str, plugin_name: str, server_version: str):
```

**`execute() -> TorznabCaps`** -- Synchronous. Resolves plugin, extracts name, returns `TorznabCaps` with composed title.

---

#### `application/use_cases/torznab_indexers.py` -- TorznabIndexersUseCase

Lists all discovered plugins with metadata.

**`execute() -> list[dict]`** -- Iterates `plugins.list_names()`, reads version and scraping mode for each. Resilient: broken plugins appear with minimal info (name only).

---

### Factories

#### `application/factories/crawljob_factory.py` -- CrawlJobFactory

Converts `SearchResult` to `CrawlJob` entity.

**Constructor:**
```python
def __init__(self, *, default_ttl_hours: int = 1, auto_start: bool = True, default_priority: Priority = Priority.DEFAULT):
```

**`create_from_search_result(result: SearchResult, *, job_id: str | None = None) -> CrawlJob`**

Logic:
1. Calculate `expires_at` from `now + ttl`.
2. Use `result.title` as `package_name`.
3. Bundle `validated_links` (or fallback to `download_link`) as newline-separated `text`.
4. Build comment from description + size + source_url.
5. Apply auto_start, priority settings.
6. Use `release_name` as filename override.

---

## Infrastructure Layer

### Cache Subsystem

#### `infrastructure/cache/cache_factory.py` -- create_cache()

Factory function that selects cache backend.

```python
def create_cache(
    backend: CacheBackend = "diskcache",  # "diskcache" | "redis"
    *, directory: str = "./cache",
    redis_url: str = "redis://localhost:6379/0",
    ttl_seconds: int = 3600,
    max_concurrent: int = 10,
) -> CachePort:
```

Returns `DiskcacheAdapter` or `RedisAdapter`.

---

#### `infrastructure/cache/diskcache_adapter.py` -- DiskcacheAdapter

SQLite-based async cache adapter wrapping the synchronous `diskcache.Cache` library.

**Key design:**
- All disk I/O runs via `asyncio.to_thread()` to avoid blocking the event loop.
- A `Semaphore(max_concurrent)` prevents SQLite lock contention from too many parallel writes.
- Implements async context manager (`__aenter__`/`__aexit__`).

**Constructor:**
```python
def __init__(self, directory: str | Path = "./cache", ttl_seconds: int = 3600, max_concurrent: int = 10):
```

**Methods:** `get`, `set`, `delete`, `exists`, `clear`, `aclose` -- all async, all use semaphore + `to_thread`.

---

#### `infrastructure/cache/redis_adapter.py` -- RedisAdapter

Async Redis cache adapter using `redis.asyncio`.

**Key design:**
- Uses native async Redis client (`redis.asyncio.Redis`), no `to_thread` needed.
- Semaphore limits concurrent connections (default: 50).
- Values serialized with `pickle` (consistent with DiskcacheAdapter).
- Health check via `PING` on connect.

**Constructor:**
```python
def __init__(self, url: str = "redis://localhost:6379/0", ttl_seconds: int = 3600, max_concurrent: int = 50):
```

**Methods:** Same interface as DiskcacheAdapter. `set` uses `SETEX` for atomic TTL. `clear` uses `FLUSHDB`.

---

### Plugin Subsystem

#### `infrastructure/plugins/registry.py` -- PluginRegistry

Lazy-loading plugin registry implementing `PluginRegistryPort`.

**Design principles:**
- `discover()` only indexes file paths (no YAML parsing, no Python execution).
- `get()` loads and caches on first access.
- Separate caches for YAML and Python plugins.

**Internal state:**
- `_refs: list[_PluginRef]` -- Discovered file references (path + type).
- `_yaml_cache: dict[str, YamlPluginDefinition]` -- Loaded YAML plugins.
- `_python_cache: dict[str, PluginProtocol]` -- Loaded Python plugins.

**`discover() -> None`** -- Scans `plugin_dir` for `.yaml`/`.yml`/`.py` files. Idempotent (only runs once).

**`list_names() -> list[str]`** -- Peeks plugin names without full loading. For YAML: reads just the `name` field via `yaml.safe_load()`. For Python: imports module and reads `plugin.name`.

**`get(name: str) -> YamlPluginDefinition | PluginProtocol`** -- Returns cached plugin or loads on demand. Raises `PluginNotFoundError`.

**`get_by_mode(mode) -> list[YamlPluginDefinition]`** -- Returns YAML plugins filtered by scraping mode (scrapy/playwright).

**`load_all() -> None`** -- Force-loads all plugins. Raises `DuplicatePluginError` if name collision found.

---

#### `infrastructure/plugins/loader.py` -- Plugin Loading

Two loading functions:

**`load_yaml_plugin(path: Path) -> YamlPluginDefinition`**

Flow:
1. Read YAML file → `yaml.safe_load()`.
2. Validate with Pydantic (`YamlPluginDefinitionPydantic.model_validate()`).
3. Convert to domain model via `to_domain_plugin_definition()`.

Raises: `PluginLoadError` (file I/O), `PluginValidationError` (schema/YAML errors).

**`load_python_plugin(path: Path) -> PluginProtocol`**

Flow:
1. Dynamic import via `importlib.util.spec_from_file_location()`.
2. Execute module.
3. Verify `plugin` variable exists with `name: str` and `search()` method.

Raises: `PluginLoadError` (import errors, missing protocol).

---

#### `infrastructure/plugins/validation_schema.py` -- Pydantic Validation Models

Pydantic `BaseModel` classes that validate YAML plugin content. Mirrors the domain schema but with validation rules.

**Key models:**

| Model | Validates |
|---|---|
| `YamlPluginDefinitionPydantic` | Top-level plugin: name (regex), version (semver), base_url (HttpUrl list), scraping, auth, http |
| `ScrapingConfig` | Mode-specific requirements: scrapy needs stages, playwright needs search_url_template |
| `ScrapingStage` | Stage needs url/url_pattern, list stages need link selector, stage references validated |
| `StageSelectors` | At least one selector required |
| `NestedSelector` | At least one field, link/url fields must have field_attributes |
| `AuthConfig` | Type-specific requirements: basic needs username+password, form needs all form fields |
| `PaginationConfig` | Enabled pagination needs selector, max_pages >= 1 |
| `HttpOverrides` | timeout_seconds > 0 |

**Special features:**
- `base_url` accepts single string or list (first = primary, rest = mirrors).
- `AuthConfig._resolve_env_credentials()` reads username/password from environment variables.

---

#### `infrastructure/plugins/adapters.py` -- Model Conversion

Functions that convert Pydantic validation models to pure domain dataclasses:

```python
to_domain_plugin_definition(pydantic: YamlPluginDefinitionPydantic) -> YamlPluginDefinition
to_domain_scraping_config(pydantic: ScrapingConfig) -> domain.ScrapingConfig
to_domain_scraping_stage(pydantic: ScrapingStage) -> domain.ScrapingStage
to_domain_stage_selectors(pydantic: StageSelectors) -> domain.StageSelectors
to_domain_nested_selector(pydantic: NestedSelector) -> domain.NestedSelector
to_domain_auth_config(pydantic: AuthConfig) -> domain.AuthConfig
to_domain_http_overrides(pydantic: HttpOverrides) -> domain.HttpOverrides
to_domain_pagination(pydantic: PaginationConfig) -> domain.PaginationConfig
to_domain_scrapy_selectors(pydantic: ScrapySelectors) -> domain.ScrapySelectors
to_domain_playwright_locators(pydantic: PlaywrightLocators) -> domain.PlaywrightLocators
```

This adapter layer keeps Pydantic out of the Domain. Domain models are plain dataclasses with no validation framework dependency.

---

### Scraping Subsystem

#### `infrastructure/scraping/scrapy_adapter.py` -- ScrapyAdapter + StageScraper

The multi-stage scraping engine. Despite the name "Scrapy", it uses **httpx** for HTTP and **BeautifulSoup** for HTML parsing (not the Scrapy framework directly).

**StageScraper** -- Executes a single scraping stage.

```python
class StageScraper:
    def __init__(self, stage: ScrapingStage, base_url: str):
```

Key methods:
- `build_url(url, **url_params) -> str` -- Constructs URL from static url, url_pattern, or provided url. Uses `urljoin` for relative paths.
- `extract_data(soup) -> dict` -- Extracts data using CSS selectors. Simple fields use text extraction; link fields use attribute extraction with fallback chain.
- `extract_links(soup) -> list[str]` -- Extracts deduplicated links for next stage.
- `should_process(data) -> bool` -- Evaluates conditions (e.g., `min_seeders: 5`).

Nested extraction (`_extract_nested`) supports two modes:
1. **Direct:** Each `items` match = 1 result.
2. **Grouped:** All `items` within each `item_group` are merged (with multi-value field support).

Attribute extraction (`_extract_from_attributes`) has special handling for `onclick` attributes (extracts URLs from JavaScript calls like `embedy('https://...')`).

**ScrapyAdapter** -- Orchestrates the multi-stage pipeline.

```python
class ScrapyAdapter:
    def __init__(
        self, plugin: YamlPluginDefinition,
        http_client: httpx.AsyncClient,
        cache: Cache,
        delay_seconds: float = 1.5,
        max_depth: int = 5,
        max_retries: int = 3,
        retry_backoff_base: float = 2.0,
    ):
```

Key methods:

**`scrape(query, **params) -> dict[str, list[dict]]`** -- Entry point. Resets visited URLs, starts from `start_stage_name`.

**`scrape_stage(stage_name, url, depth, **url_params) -> dict[str, list[dict]]`** -- Recursive stage execution:
1. Check max_depth.
2. Fetch page via `_fetch_page()`.
3. Extract data and links.
4. Handle pagination if enabled.
5. Recursively scrape next_stage for each link (parallel via `asyncio.gather`, max 10 links).

**`_fetch_page(url) -> BeautifulSoup | None`** -- HTTP fetch with:
- Loop detection (visited URL set, checked before yielding to event loop).
- Rate limiting (`asyncio.sleep(delay)`).
- Exponential backoff retry (3 attempts, 2x backoff).
- 4xx = no retry, 5xx = retry.
- Mirror fallback on final failure.

**`_try_mirrors(url) -> BeautifulSoup | None`** -- Tries mirror URLs by replacing domain. On success, switches `base_url` for all subsequent requests.

**`normalize_results(stage_results) -> list[SearchResult]`** -- Converts raw stage dicts to SearchResult entities.

---

### Search Engine

#### `infrastructure/torznab/search_engine.py` -- HttpxScrapySearchEngine

Orchestrates ScrapyAdapter and HttpLinkValidator. Implements `SearchEnginePort`.

```python
class HttpxScrapySearchEngine:
    def __init__(
        self, *, http_client: httpx.AsyncClient, cache: CachePort,
        validate_links: bool = True,
        validation_timeout: float = 5.0,
        validation_concurrency: int = 20,
    ):
```

**`search(plugin, query, **params) -> list[SearchResult]`**

Flow:
1. Create `ScrapyAdapter` for the plugin.
2. Execute `adapter.scrape(query)` -- multi-stage scraping.
3. Convert stage results to SearchResult via `_convert_stage_results()`.
4. Validate links (if enabled) via `_filter_valid_links()`.
5. Return validated results.

**`validate_results(results) -> list[SearchResult]`** -- Used by Python plugins that produce their own SearchResults. Delegates to `_filter_valid_links()`.

**`_convert_stage_results(stage_results) -> list[SearchResult]`** -- Deduplicates by `(title, download_link)` tuple. Tries multiple field names for title (`release_name` > `title` > `name`). Extracts download link from `download_link`, `link`, or first entry in `download_links`.

**`_filter_valid_links(results) -> list[SearchResult]`** -- Batch validation pipeline:
1. Collect all unique URLs across all results (primary + alternative links).
2. Single `validate_batch()` call for everything.
3. For each result: assemble validated_links, promote alternative if primary is dead.
4. Drop results with zero valid links.

---

### Presenter

#### `infrastructure/torznab/presenter.py` -- XML Rendering

Renders Torznab-compliant RSS 2.0 XML using `xml.etree.ElementTree`.

**`render_caps_xml(caps: TorznabCaps) -> TorznabRendered`**

Generates capabilities XML with:
- `<server>` element (title, version).
- `<limits>` element (max, default).
- `<searching>` element (supported params: q).
- `<categories>` element (Movies=2000, TV=5000, Other=8000).

**`render_rss_xml(*, title, items, description, scavengarr_base_url) -> TorznabRendered`**

Generates RSS 2.0 XML with Torznab namespace extensions:
- Channel metadata (title, description, link, language).
- For each item:
  - `<title>` uses `release_name` if available.
  - `<guid>` uses original download_url for deduplication.
  - `<link>` and `<enclosure>` point to CrawlJob download URL (`/api/v1/download/{job_id}`).
  - Torznab attributes: category, size (converted to bytes), seeders, peers, grabs, volume factors.
  - `<enclosure>` type is `application/x-crawljob`.

**TorznabRendered** -- Frozen dataclass with `payload: bytes` and `media_type: str`.

---

### Link Validation

#### `infrastructure/validation/http_link_validator.py` -- HttpLinkValidator

HTTP-based link validator implementing `LinkValidatorPort`.

```python
class HttpLinkValidator:
    def __init__(self, http_client: AsyncClient, timeout_seconds: float = 5.0, max_concurrent: int = 20):
```

**Strategy:** HEAD-first with GET fallback.

Some streaming hosters (veev.to, savefiles.com) return 403 on HEAD but 200 on GET. The validator tries HEAD first, and on any failure (timeout, HTTP error, status >= 400) falls back to GET.

**Concurrency:** `asyncio.Semaphore(max_concurrent)` limits parallel validations. All validations within `validate_batch()` run via `asyncio.gather()`.

**`validate(url) -> bool`** -- Single URL validation (semaphore-bounded).

**`validate_batch(urls) -> dict[str, bool]`** -- Batch validation. Collects all URLs, validates in parallel, returns URL -> validity mapping.

---

### Persistence

#### `infrastructure/persistence/crawljob_cache.py` -- CacheCrawlJobRepository

CrawlJob storage backed by `CachePort`.

```python
class CacheCrawlJobRepository(CrawlJobRepository):
    def __init__(self, cache: CachePort, ttl_seconds: int = 3600):
```

**Key pattern:** Keys are prefixed with `crawljob:` for namespace isolation. Values are pickle-serialized CrawlJob entities.

**`save(job) -> None`** -- Stores `pickle.dumps(job)` at key `crawljob:{job_id}` with TTL.

**`get(job_id) -> CrawlJob | None`** -- Retrieves and unpickles. Returns None on miss or deserialization error.

---

### Configuration

#### `infrastructure/config/schema.py` -- Configuration Models

**AppConfig** -- Central configuration model (Pydantic `BaseModel`).

Sections:
- General: `app_name`, `environment` (dev/test/prod)
- Plugins: `plugin_dir`
- HTTP: `http_timeout_seconds`, `http_follow_redirects`, `http_user_agent`
- Link Validation: `validate_download_links`, `validation_timeout_seconds`, `validation_max_concurrent`
- Playwright: `playwright_headless`, `playwright_timeout_ms`
- Logging: `log_level`, `log_format`
- Cache: embedded `CacheConfig` + `cache_dir`, `cache_ttl_seconds`

Uses `AliasChoices` and `AliasPath` for dual access patterns (flat keys for env vars, nested for YAML sections).

**CacheConfig** -- Embedded cache settings (`BaseSettings` with `CACHE_` prefix).

```python
class CacheConfig(BaseSettings):
    backend: Literal["diskcache", "redis"] = "diskcache"
    directory: Path = Path("./cache/scavengarr")
    redis_url: str = "redis://localhost:6379/0"
    ttl_seconds: int = 3600
    max_concurrent: int = 10
```

**EnvOverrides** -- Environment variable reader (`BaseSettings` with `SCAVENGARR_` prefix).

All fields are optional. `to_update_dict()` returns only provided (non-None) values for merging.

**Derived defaults:** `log_format` defaults to `"console"` in dev/test, `"json"` in prod.

---

#### `infrastructure/config/load.py` -- Configuration Loading

**`load_config(*, config_path, dotenv_path, cli_overrides) -> AppConfig`**

Layered loading with strict precedence: defaults < YAML < ENV < CLI.

Flow:
1. Load `.env` file if provided (via `python-dotenv`).
2. Start with `DEFAULT_CONFIG` (normalized to sectioned shape).
3. Deep-merge YAML config if provided.
4. Deep-merge environment overrides (`EnvOverrides().to_update_dict()`).
5. Deep-merge CLI overrides.
6. Validate final dict via `AppConfig.model_validate()`.

Helper functions:
- `_deep_merge(base, override)` -- Recursive dict merge (override wins on conflict).
- `_normalize_layer(data)` -- Converts flat keys (`plugin_dir`) to sectioned shape (`plugins.plugin_dir`).
- `_read_yaml_config(path)` -- YAML file reader with validation.

---

#### `infrastructure/config/defaults.py` -- Default Configuration

```python
DEFAULT_CONFIG = {
    "app_name": "scavengarr",
    "environment": "dev",
    "plugins": {"plugin_dir": "./plugins"},
    "http": {
        "timeout_seconds": 30.0,
        "follow_redirects": True,
        "user_agent": "Scavengarr/0.1.0 (+https://github.com/Strob0t/Scavengarr)",
    },
    "playwright": {"headless": True, "timeout_ms": 30_000},
    "logging": {"level": "INFO", "format": None},  # format derived from environment
    "cache": {"dir": "./.cache/scavengarr", "backend": "diskcache", "ttl_seconds": 3600},
}
```

---

### Logging

#### `infrastructure/logging/setup.py` -- Structured Logging

Configures structlog + stdlib logging with async emission.

**`configure_logging(config: AppConfig) -> dict`**

Flow:
1. Configure structlog processors (timestamper, context vars, log level, exception formatting).
2. Build uvicorn-compatible dictConfig.
3. Apply dictConfig.
4. Enable async logging via QueueHandler/QueueListener.

**`_enable_async_logging(config)`** -- Routes ALL stdlib logging through a background QueueHandler:
- stdout: DEBUG/INFO/WARNING
- stderr: ERROR/CRITICAL
- Preserves structlog event_dicts (custom `_StructlogPreservingQueueHandler`).
- Uses `atexit.register()` for clean shutdown.

**Processors:**
- `_drop_color_message` -- Removes uvicorn color markup.
- `_add_record_created_timestamp_utc` -- Uses `LogRecord.created` for accurate timestamps on foreign records.

**Renderers:** JSON (`structlog.processors.JSONRenderer`) in prod, Console (`structlog.dev.ConsoleRenderer`) in dev/test.

---

### Common Utilities

#### `infrastructure/common/converters.py` -- to_int()

```python
def to_int(raw: str | int | None) -> int | None:
```

Handles: `None` -> `None`, `int` -> passthrough, `"123"` -> `123`, `"1,234"` -> `1234`, `"1 234"` -> `1234`, `""` -> `None`, invalid -> `None`.

#### `infrastructure/common/parsers.py` -- parse_size_to_bytes()

```python
def parse_size_to_bytes(size_str: str) -> int:
```

Supports: raw bytes (`"1234"`), human-readable (`"4.5 GB"`, `"500 MB"`, `"1.2 TB"`). Uses 1024-based multipliers.

#### `infrastructure/common/extractors.py` -- extract_download_link()

```python
def extract_download_link(raw_data: dict) -> str | None:
```

Placeholder implementation. Checks `raw_data["link"]` then `raw_data["url"]`.

---

## Interfaces Layer

### FastAPI Application

#### `interfaces/main.py` -- build_app()

Factory function that creates the FastAPI application.

```python
def build_app(config: AppConfig) -> FastAPI:
```

Responsibilities:
1. Create `FastAPI` instance with lifespan hook.
2. Initialize `AppState` and store config.
3. Register routers (torznab, download).
4. Add `/healthz` endpoint.
5. Add request logging middleware (method, path, status, duration_ms).

**Important:** `build_app()` does NOT initialize resources. All resource creation happens in `lifespan()`.

---

### Composition Root

#### `interfaces/composition.py` -- lifespan()

The DI composition root. Wires all concrete implementations together.

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
```

**Initialization order:**

| Step | Component | Factory/Constructor | Stored on |
|---|---|---|---|
| 1 | Cache | `create_cache()` | `state.cache` |
| 2 | HTTP Client | `httpx.AsyncClient()` | `state.http_client` |
| 3 | Plugin Registry | `PluginRegistry()` + `discover()` | `state.plugins` |
| 4 | Search Engine | `HttpxScrapySearchEngine()` | `state.search_engine` |
| 5 | CrawlJob Repository | `CacheCrawlJobRepository()` | `state.crawljob_repo` |
| 6 | CrawlJob Factory | `CrawlJobFactory()` | `state.crawljob_factory` |

**Dev mode:** Cache is cleared on startup (`cache.clear()`) when `environment == "dev"`.

**Cleanup:** HTTP client and cache are closed on shutdown (reverse order).

---

### Application State

#### `interfaces/app_state.py` -- AppState

Typed container extending Starlette `State`.

```python
class AppState(State):
    config: AppConfig
    cache: CachePort
    http_client: httpx.AsyncClient
    plugins: PluginRegistryPort
    search_engine: SearchEnginePort
    crawljob_repo: CrawlJobRepository
    crawljob_factory: CrawlJobFactory
```

Accessible from any request handler via `cast(AppState, request.app.state)`.

---

### Torznab API Router

#### `interfaces/api/torznab/router.py`

**`GET /api/v1/torznab/indexers`** -- Lists all discovered plugins.

Delegates to `TorznabIndexersUseCase`. Returns JSON: `{"indexers": [...]}`.

**`GET /api/v1/torznab/{plugin_name}?t=...&q=...`** -- Main Torznab endpoint.

Handles two actions:
- `t=caps` -- Returns capabilities XML via `TorznabCapsUseCase` + `render_caps_xml()`.
- `t=search` -- Full search pipeline via `TorznabSearchUseCase` + `render_rss_xml()`.

Special cases:
- `t=search` without `q` but `extended=1` -- Prowlarr test mode. Performs lightweight HTTP reachability probe against the plugin's `base_url` (HEAD with GET fallback). Returns test item if reachable, 503 if not.
- `t=search` without `q` and no `extended` -- Returns empty RSS (200).

Error handling: All domain exceptions are caught and mapped to appropriate HTTP responses. In production, all errors return empty RSS with HTTP 200.

**`GET /api/v1/torznab/{plugin_name}/health`** -- Plugin health check.

Performs lightweight HTTP probe against `base_url`. If primary is unreachable and mirrors are configured, probes mirrors. Returns JSON with reachability status.

---

### Download API Router

#### `interfaces/api/download/router.py`

**`GET /api/v1/download/{job_id}`** -- Serves `.crawljob` file.

Flow:
1. Look up CrawlJob from repository.
2. Check expiry (`is_expired()`).
3. Serialize via `to_crawljob_format()`.
4. Return as downloadable file with headers:
   - `Content-Type: application/x-crawljob`
   - `Content-Disposition: attachment; filename="<safe_name>_<id>.crawljob"`
   - Custom headers: `X-CrawlJob-ID`, `X-CrawlJob-Package`, `X-CrawlJob-Links`

**`GET /api/v1/download/{job_id}/info`** -- Returns CrawlJob metadata as JSON.

Fields: job_id, package_name, created_at, expires_at, is_expired, validated_urls, source_url, comment, auto_start, priority.

---

### CLI

#### `interfaces/cli/cli.py` -- CLI Entry Point

The process entry point registered as `start` in `pyproject.toml`.

```python
def start(argv: Iterable[str] | None = None) -> None:
```

**Argument parsing (`_parse_args`):**

| Flag | Default | Description |
|---|---|---|
| `--host` | `$HOST` or `0.0.0.0` | Bind host |
| `--port` | `$PORT` or `7979` | Bind port |
| `--config` | None | Path to YAML config file |
| `--dotenv` | None | Path to `.env` file |
| `--plugin-dir` | None | Override plugins directory |
| `--log-level` | None | DEBUG/INFO/WARNING/ERROR |
| `--log-format` | None | json/console |

**Startup flow:**
1. Parse CLI arguments.
2. Resolve host/port (CLI > env > defaults).
3. Build CLI overrides dict from provided flags.
4. Load config via `load_config()`.
5. Configure logging via `configure_logging()`.
6. Build FastAPI app and run via `uvicorn.run()`.

---

## Plugin Files

### `plugins/filmpalast.to.yaml` -- YAML Plugin Example

Declarative plugin for a streaming indexer site. Defines:
- `name`, `version`, `base_url` (with mirror URLs)
- `scraping.mode: "scrapy"` with multi-stage pipeline
- Two stages: list (search results) + detail (download links)
- Nested selectors for extracting download links from complex HTML
- Authentication configuration

### `plugins/boerse.py` -- Python Plugin Example

Imperative Python plugin implementing `PluginProtocol`. Exports a module-level `plugin` variable with:
- `name: str` attribute
- `async def search(query, category) -> list[SearchResult]` method

Python plugins handle their own scraping logic and return `SearchResult` lists directly. Link validation is still applied by the search engine.

---

## Test Suite

**235 tests** across three layers.

### Test Configuration

```toml
# pyproject.toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

### Shared Fixtures (`tests/conftest.py`)

Common test fixtures for entities, mock ports, and configuration.

**Mock patterns:**
- `PluginRegistryPort` is synchronous -- use `MagicMock`.
- `SearchEnginePort`, `CrawlJobRepository`, `CachePort` are async -- use `AsyncMock`.

### Domain Tests (`tests/unit/domain/`)

| File | Tests |
|---|---|
| `test_crawljob.py` | CrawlJob construction, expiry, serialization (`to_crawljob_format()`), BooleanStatus/Priority enums |
| `test_torznab_entities.py` | TorznabQuery, TorznabItem, TorznabCaps construction; exception hierarchy |
| `test_search_result.py` | SearchResult construction, defaults, StageResult |
| `test_plugin_schema.py` | YamlPluginDefinition, ScrapingStage, StageSelectors, NestedSelector, AuthConfig |

### Application Tests (`tests/unit/application/`)

| File | Tests |
|---|---|
| `test_crawljob_factory.py` | CrawlJobFactory: SearchResult -> CrawlJob conversion, TTL, URL bundling, comment generation |
| `test_torznab_caps.py` | TorznabCapsUseCase: plugin resolution, title composition, error paths |
| `test_torznab_indexers.py` | TorznabIndexersUseCase: plugin listing, resilience to broken plugins |
| `test_torznab_search.py` | TorznabSearchUseCase: full flow, validation, YAML vs Python routing, error handling |

### Infrastructure Tests (`tests/unit/infrastructure/`)

| File | Tests |
|---|---|
| `test_converters.py` | `to_int()`: None, int, string, comma-separated, spaces, empty, invalid |
| `test_parsers.py` | `parse_size_to_bytes()`: raw bytes, KB/MB/GB/TB, empty, invalid |
| `test_extractors.py` | `extract_download_link()`: dict with link/url, missing fields |
| `test_presenter.py` | Torznab XML rendering: caps XML structure, RSS XML items, Torznab attributes, CrawlJob URLs |
| `test_link_validator.py` | HttpLinkValidator: HEAD success, HEAD fail + GET success, timeout, batch validation |
| `test_search_engine.py` | HttpxScrapySearchEngine: result conversion, deduplication, link validation pipeline |
| `test_crawljob_cache.py` | CacheCrawlJobRepository: save/get, pickle serialization, missing keys, deserialization errors |
| `test_scrapy_fallback.py` | ScrapyAdapter mirror fallback behavior |
| `test_boerse_plugin.py` | Python plugin loading and execution |
| `test_auth_env_resolution.py` | AuthConfig environment variable credential resolution |

---

## Cross-Cutting Concerns

### Structured Logging

All modules use `structlog.get_logger(__name__)` for structured, contextual logging. Log events include:
- `plugin`, `stage`, `query` -- business context
- `duration_ms`, `results_count` -- performance metrics
- `url`, `status_code`, `error` -- I/O diagnostics

Logging is **non-blocking**: all emission goes through a `QueueHandler` to a background `QueueListener`.

### Error Handling Strategy

1. **Domain exceptions** carry business meaning (validation errors, not-found, unsupported).
2. **Application layer** maps external errors to domain exceptions (`TorznabExternalError`).
3. **Infrastructure layer** logs and re-raises or wraps exceptions.
4. **Interfaces layer** maps domain exceptions to HTTP responses.
5. **Production mode** returns empty RSS (200) for all errors to maintain Prowlarr stability.

### Async/Await Patterns

- All I/O operations are async (`await`).
- Parallel validation uses `asyncio.gather()` with `Semaphore` for bounded concurrency.
- Sync disk I/O (diskcache) uses `asyncio.to_thread()`.
- Multi-stage scraping parallelizes next-stage fetching (max 10 concurrent).

### Type Safety

- All function signatures are fully typed (params + return types).
- Modern Python 3.10+ syntax everywhere (`T | None`, `list[T]`, `dict[K, V]`).
- All ports use `Protocol` (structural subtyping).
- `from __future__ import annotations` in every file.

---

## Dependency Graph

```
                    ┌──────────────────────┐
                    │     CLI / HTTP       │  interfaces/
                    │  (FastAPI, argparse) │
                    └──────────┬───────────┘
                               │ depends on
                    ┌──────────▼───────────┐
                    │   Composition Root   │  interfaces/composition.py
                    │   (wires adapters)   │
                    └──────────┬───────────┘
               ┌───────────────┼───────────────┐
               │               │               │
    ┌──────────▼──────┐ ┌──────▼──────┐ ┌──────▼──────────┐
    │   Use Cases     │ │  Factories  │ │    Adapters      │  infrastructure/
    │ (orchestration) │ │             │ │ (ScrapyAdapter,  │
    └──────────┬──────┘ └──────┬──────┘ │  DiskcacheAdapter│
               │               │        │  PluginRegistry, │
               │               │        │  HttpLinkValidator│
               │               │        │  etc.)           │
               │               │        └──────┬───────────┘
               │               │               │
    ┌──────────▼───────────────▼───────────────▼──┐
    │              Domain Layer                    │  domain/
    │  (Entities, Value Objects, Ports/Protocols)  │
    │  No external dependencies                    │
    └─────────────────────────────────────────────┘
```

### Key Dependency Chains

**Search request:**
```
Router → TorznabSearchUseCase → SearchEnginePort (→ HttpxScrapySearchEngine)
                               → PluginRegistryPort (→ PluginRegistry)
                               → CrawlJobFactory
                               → CrawlJobRepository (→ CacheCrawlJobRepository → CachePort)
```

**Plugin loading:**
```
PluginRegistry → loader.load_yaml_plugin() → validation_schema (Pydantic)
                                            → adapters (Pydantic → Domain)
               → loader.load_python_plugin() → importlib dynamic import
```

**Configuration:**
```
CLI → load_config() → defaults.DEFAULT_CONFIG
                     → YAML file (yaml.safe_load)
                     → EnvOverrides (pydantic-settings)
                     → CLI overrides
                     → AppConfig.model_validate()
```

**Cache selection:**
```
Composition Root → cache_factory.create_cache()
                    → DiskcacheAdapter (SQLite via diskcache)
                    → RedisAdapter (redis.asyncio)
```
