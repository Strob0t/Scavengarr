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
│   │   ├── scoring.py                # ProbeResult, EwmaState, PluginScoreSnapshot
│   │   ├── stremio.py                # StremioStream, ResolvedStream, RankedStream, etc.
│   │   └── torznab.py                # Torznab entities + exceptions
│   ├── plugins/
│   │   ├── __init__.py               # Re-exports SearchResult, plugin types, exceptions
│   │   ├── base.py                   # SearchResult, StageResult, Protocols
│   │   ├── exceptions.py             # Plugin exception hierarchy
│   │   └── plugin_schema.py          # Plugin value objects
│   └── ports/
│       ├── __init__.py               # Re-exports all port protocols
│       ├── cache.py                  # CachePort
│       ├── concurrency.py            # ConcurrencyPoolPort, ConcurrencyBudgetPort
│       ├── crawljob_repository.py    # CrawlJobRepository
│       ├── hoster_resolver.py        # HosterResolverPort
│       ├── plugin_registry.py        # PluginRegistryPort
│       ├── plugin_score_store.py     # PluginScoreStorePort
│       ├── search_engine.py          # SearchEnginePort
│       ├── stream_link_repository.py # StreamLinkRepositoryPort
│       └── tmdb.py                   # TmdbClientPort
├── application/
│   ├── factories/
│   │   ├── __init__.py               # Re-exports CrawlJobFactory
│   │   └── crawljob_factory.py       # SearchResult → CrawlJob conversion
│   └── use_cases/
│       ├── __init__.py
│       ├── stremio_catalog.py        # TMDB trending + search catalog
│       ├── stremio_stream.py         # IMDb ID → plugin search → ranked streams
│       ├── torznab_caps.py           # Capabilities use case
│       ├── torznab_indexers.py       # Indexer listing use case
│       └── torznab_search.py         # Search orchestration + result caching
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
│   │   ├── html_selectors.py         # CSS-selector HTML extraction with fallback chains
│   │   └── parsers.py                # parse_size_to_bytes()
│   ├── config/
│   │   ├── __init__.py               # Re-exports AppConfig, load_config
│   │   ├── defaults.py               # DEFAULT_CONFIG dict
│   │   ├── load.py                   # Layered config loading
│   │   └── schema.py                 # AppConfig, CacheConfig, EnvOverrides
│   ├── hoster_resolvers/
│   │   ├── __init__.py
│   │   ├── _video_extract.py         # Shared video extraction (JWPlayer, packed JS, HLS)
│   │   ├── cloudflare.py             # Cloudflare detection utilities
│   │   ├── ddownload.py              # DDownload DDL resolver
│   │   ├── doodstream.py             # DoodStream pass_md5 extraction
│   │   ├── filemoon.py               # Filemoon packed JS + Byse SPA flow
│   │   ├── filernet.py               # Filer.net public API resolver
│   │   ├── generic_ddl.py            # 12 consolidated DDL resolvers
│   │   ├── gofile.py                 # GoFile guest-token DDL resolver
│   │   ├── mediafire.py              # Mediafire API DDL resolver
│   │   ├── probe.py                  # Content-type probing
│   │   ├── rapidgator.py             # Rapidgator DDL resolver
│   │   ├── registry.py               # HosterResolverRegistry + domain matching
│   │   ├── sendvid.py                # SendVid streaming resolver
│   │   ├── serienstream.py           # SerienStream resolver
│   │   ├── stealth_pool.py           # Playwright stealth pool (probes + SuperVideo CF bypass)
│   │   ├── stmix.py                  # Stmix embed resolver
│   │   ├── streamtape.py             # Streamtape token extraction
│   │   ├── strmup.py                 # StreamUp HLS resolver
│   │   ├── supervideo.py             # SuperVideo XFS + StealthPool CF fallback
│   │   ├── vidguard.py               # VidGuard multi-domain resolver
│   │   ├── vidking.py                # Vidking embed resolver
│   │   ├── vidsonic.py               # Vidsonic hex-obfuscated HLS resolver
│   │   ├── voe.py                    # VOE multi-method extraction
│   │   └── xfs.py                    # 27 consolidated XFS resolvers
│   ├── logging/
│   │   ├── __init__.py
│   │   └── setup.py                  # Structlog + QueueHandler setup
│   ├── persistence/
│   │   ├── crawljob_cache.py         # CacheCrawlJobRepository
│   │   └── stream_link_cache.py      # StreamLinkCache for hoster URLs
│   ├── plugins/
│   │   ├── __init__.py               # Re-exports PluginRegistry
│   │   ├── adapters.py               # Pydantic → Domain model conversion
│   │   ├── constants.py              # DEFAULT_USER_AGENT, DEFAULT_MAX_CONCURRENT, etc.
│   │   ├── httpx_base.py             # HttpxPluginBase shared base for 33 plugins
│   │   ├── loader.py                 # Python plugin loading
│   │   ├── playwright_base.py        # PlaywrightPluginBase shared base for 9 plugins
│   │   └── registry.py               # PluginRegistry (lazy loading)
│   ├── stremio/
│   │   ├── __init__.py
│   │   ├── release_parser.py         # guessit-based release name parsing
│   │   ├── stream_converter.py       # SearchResult → RankedStream conversion
│   │   ├── stream_sorter.py          # Configurable stream ranking/sorting
│   │   └── title_matcher.py          # Multi-candidate title scoring
│   ├── tmdb/
│   │   ├── __init__.py
│   │   ├── client.py                 # TMDB httpx client with caching
│   │   └── imdb_fallback.py          # IMDB → title via Wikidata (no API key)
│   ├── torznab/
│   │   ├── presenter.py              # XML rendering (caps + RSS)
│   │   └── search_engine.py          # HttpxSearchEngine
│   ├── validation/
│   │   ├── __init__.py               # Re-exports HttpLinkValidator
│   │   └── http_link_validator.py    # HEAD/GET link validation
│   ├── circuit_breaker.py            # Per-plugin circuit breaker (open/closed/half-open)
│   ├── concurrency.py                # Global fair-share concurrency pool (httpx + PW slots)
│   ├── graceful_shutdown.py          # In-flight request tracking + drain on shutdown
│   ├── metrics.py                    # Runtime metrics collector (plugin stats, uptime)
│   ├── retry_transport.py            # Rate-limiting + 429/503 retry httpx transport
│   └── shared_browser.py             # SharedBrowserPool (singleton Chromium for PW plugins)
└── interfaces/
    ├── __init__.py
    ├── app.py                        # FastAPI factory (build_app)
    ├── app_state.py                  # AppState typed container
    ├── composition.py                # Lifespan (DI composition root)
    ├── test.py                       # Test utilities
    ├── api/
    │   ├── __init__.py
    │   ├── download/
    │   │   ├── __init__.py
    │   │   └── router.py             # CrawlJob download endpoints
    │   ├── stats/
    │   │   ├── __init__.py
    │   │   └── router.py             # /stats/metrics, /stats/plugin-scores, /healthz, /readyz
    │   ├── stremio/
    │   │   ├── __init__.py
    │   │   └── router.py             # Stremio manifest, catalog, stream, play
    │   └── torznab/
    │       ├── __init__.py
    │       └── router.py             # Torznab API endpoints
    └── cli/
        ├── __init__.py
        └── __main__.py               # CLI entry point

plugins/                              # 42 plugins (33 httpx + 9 Playwright)
├── filmpalast_to.py                  # Httpx plugin (streaming)
├── scnlog.py                         # Httpx plugin (scene log)
├── warezomen.py                      # Httpx plugin (DDL)
├── boerse.py                         # Playwright plugin (Cloudflare + vBulletin)
├── einschalten.py                    # Httpx plugin (JSON API)
├── movie4k.py                        # Httpx plugin (JSON API, multi-domain)
└── ... (36 more Python plugins)

tests/
├── conftest.py                       # Shared fixtures
├── e2e/                              # 158 E2E tests
│   ├── test_stremio_endpoint.py      # Stremio endpoint tests
│   ├── test_stremio_series_e2e.py    # Stremio series endpoint tests
│   ├── test_stremio_streamable_e2e.py # Streamable link verification (31 tests)
│   └── test_torznab_endpoint.py      # 46 Torznab endpoint tests
├── integration/                      # 25 integration tests
│   ├── test_config_loading.py        # Config precedence + validation
│   ├── test_crawljob_lifecycle.py    # CrawlJob create → retrieve → expire
│   ├── test_link_validation.py       # HEAD/GET validation with mocked HTTP
│   └── test_plugin_pipeline.py       # Plugin load → search → results
├── live/                             # 38 live smoke tests
│   ├── test_plugin_smoke.py          # Parametrized across all plugins
│   └── test_resolver_live.py         # Resolver contract tests (XFS + non-XFS)
└── unit/
    ├── domain/                       # Pure entity/schema tests (6 files)
    ├── application/                  # Use case tests with mocked ports (7 files)
    ├── infrastructure/               # Adapter, parser, plugin, resolver tests (~90 files)
    └── interfaces/                   # Router tests (3 files)
```

---

## Technology Stack

| Category | Technology | Version | Purpose |
|---|---|---|---|
| Web Framework | FastAPI | ^0.128 | HTTP API server |
| ASGI Server | Uvicorn | ^0.40 | Production ASGI server |
| HTTP Client | httpx | ^0.28 | Async HTTP for scraping and validation |
| Browser Automation | Playwright | ^1.47 | JS-heavy site scraping (9 plugins) |
| Title Matching | rapidfuzz | -- | Fuzzy string matching (C++ backend) |
| Structured Logging | structlog | ^25.5 | JSON/console logging |
| Cache (SQLite) | diskcache | ^5.6 | Local persistent cache |
| Cache (Redis) | redis | ^7.1 | Optional distributed cache |
| Configuration | pydantic-settings | ^2.10 | Typed config with env var support |
| Env Files | python-dotenv | ^1.1 | `.env` file loading |
| Release parsing | guessit | -- | Title matching for Stremio |
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

Pure domain models for plugin configuration. All are frozen dataclasses -- no validation logic (validation lives in Infrastructure).

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

**Other value objects:** `HttpOverrides` (timeout, redirects, user-agent).

---

#### `domain/plugins/exceptions.py`

Plugin-specific exception hierarchy.

```
PluginError (base)
├── PluginValidationError   # Plugin validation failure
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

Implementation: `HttpxSearchEngine`.

#### `domain/ports/plugin_registry.py` -- PluginRegistryPort

```python
@runtime_checkable
class PluginRegistryPort(Protocol):
    def discover(self) -> None: ...
    def list_names(self) -> list[str]: ...
    def get(self, name: str) -> PluginProtocol: ...
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
3. **Execute search** -- calls `plugin.search()` then `engine.validate_results()`.
4. **Build TorznabItems** -- For each SearchResult:
   - Create TorznabItem with mapped fields.
   - Create CrawlJob via factory.
   - Save CrawlJob to repository.
   - Enrich TorznabItem with `job_id`.
5. **Return** -- list of enriched TorznabItems.

All plugins are Python-based and implement the `PluginProtocol` with a `search()` method.

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

#### `application/use_cases/stremio_catalog.py` -- StremioCatalogUseCase

Provides TMDB-based catalog for the Stremio addon (trending + search).

**`trending(content_type) -> list[StremioMeta]`** -- Returns trending movies or series from TMDB with German locale.

**`search(query, content_type) -> list[StremioMeta]`** -- Searches TMDB and returns metadata for Stremio catalog display.

---

#### `application/use_cases/stremio_stream.py` -- StremioStreamUseCase

Resolves IMDb IDs to ranked streams for the Stremio addon.

**`execute(imdb_id, content_type, season, episode) -> list[RankedStream]`** -- Flow:
1. Look up title and year via TMDB client (or IMDB fallback).
2. Search all compatible plugins in parallel (with per-plugin timeout).
3. Convert SearchResults to streams via stream converter.
4. Score and rank streams via title matcher and stream sorter.
5. Return ranked streams for Stremio display.

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
- `discover()` only indexes file paths (no Python execution).
- `get()` loads and caches on first access.

**Internal state:**
- `_refs: list[_PluginRef]` -- Discovered file references (path + type).
- `_python_cache: dict[str, PluginProtocol]` -- Loaded Python plugins.

**`discover() -> None`** -- Scans `plugin_dir` for `.py` files. Idempotent (only runs once).

**`list_names() -> list[str]`** -- Peeks plugin names without full loading. Imports module and reads `plugin.name`.

**`get(name: str) -> PluginProtocol`** -- Returns cached plugin or loads on demand. Raises `PluginNotFoundError`.

**`load_all() -> None`** -- Force-loads all plugins. Raises `DuplicatePluginError` if name collision found.

---

#### `infrastructure/plugins/loader.py` -- Plugin Loading

**`load_python_plugin(path: Path) -> PluginProtocol`**

Flow:
1. Dynamic import via `importlib.util.spec_from_file_location()`.
2. Execute module.
3. Verify `plugin` variable exists with `name: str` and `search()` method.

Raises: `PluginLoadError` (import errors, missing protocol).
| `AuthConfig` | Type-specific requirements: basic needs username+password, form needs all form fields |
| `HttpOverrides` | timeout_seconds > 0 |

**Special features:**
- `AuthConfig._resolve_env_credentials()` reads username/password from environment variables.

---

### Search Engine

#### `infrastructure/torznab/search_engine.py` -- HttpxSearchEngine

Orchestrates Python plugins and HttpLinkValidator. Implements `SearchEnginePort`.

```python
class HttpxSearchEngine:
    def __init__(
        self, *, http_client: httpx.AsyncClient, cache: CachePort,
        validate_links: bool = True,
        validation_timeout: float = 5.0,
        validation_concurrency: int = 20,
    ):
```

**`search(plugin, query, **params) -> list[SearchResult]`**

Flow:
1. Call `plugin.search(query)` -- plugin handles its own multi-stage scraping.
2. Deduplicate results by `(title, download_link)` tuple.
3. Validate links (if enabled) via `_filter_valid_links()`.
4. Return validated results.

**`validate_results(results) -> list[SearchResult]`** -- Validates results from plugins. Delegates to `_filter_valid_links()`.

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

#### `infrastructure/common/html_selectors.py` -- CSS-selector HTML Helpers

Shared HTML extraction functions used by Python plugins. Provides CSS-selector-based
extraction with fallback chains for common patterns (titles, links, sizes, dates).

Functions include:
- `select_text(soup, *selectors)` -- Try multiple CSS selectors, return first match text.
- `select_attr(soup, selector, attr)` -- Extract attribute from matched element.
- `select_all_text(soup, selector)` -- Extract text from all matches.

---

### Plugin Base Classes

#### `infrastructure/plugins/httpx_base.py` -- HttpxPluginBase

Shared base class for the 20 httpx-based Python plugins. Eliminates 50-100 lines of
boilerplate per plugin by providing:

- Automatic httpx client lifecycle (`_ensure_client()`, `cleanup()`)
- Domain fallback across `_domains` list (`_verify_domain()`)
- Safe HTTP fetching with error handling (`_safe_fetch()`)
- Safe JSON parsing (`_safe_parse_json()`)
- Bounded concurrency via `_new_semaphore()` (default: 3)
- Configurable max results (`_max_results`, default: 1000)
- Structured logging via `self._log`

```python
class HttpxPluginBase:
    name: str                              # Plugin identifier
    provides: str                          # "stream" or "download"
    default_language: str = "de"           # Language for Stremio
    _domains: list[str]                    # Domain list for fallback

    async def search(self, query, category, season, episode) -> list[SearchResult]: ...
    async def cleanup(self) -> None: ...
```

#### `infrastructure/plugins/playwright_base.py` -- PlaywrightPluginBase

Shared base class for the 9 Playwright-based Python plugins. Provides:

- Browser and context lifecycle management
- Cloudflare JS challenge detection and wait
- DDoS-Guard bypass detection
- Page navigation with timeout and error handling
- Domain fallback across `_domains` list
- Bounded concurrency via semaphore

#### `infrastructure/plugins/constants.py` -- Plugin Constants

Shared constants used by both base classes:

```python
DEFAULT_USER_AGENT = "Mozilla/5.0 ..."
DEFAULT_MAX_CONCURRENT = 3
DEFAULT_MAX_RESULTS = 1000
DEFAULT_CLIENT_TIMEOUT = 15.0
```

---

### Stremio Subsystem

#### `infrastructure/stremio/stream_converter.py` -- Stream Converter

Converts `SearchResult` entities from plugin searches into `RankedStream` entities
for Stremio display. Handles:
- Release name parsing via guessit
- Quality/resolution extraction
- Plugin language threading
- Download link → stream URL mapping

#### `infrastructure/stremio/stream_sorter.py` -- Stream Sorter

Configurable sorting/ranking of streams for Stremio presentation. Ranks by:
- Title match score (how well the result matches the requested title)
- Video quality (4K > 1080p > 720p > SD)
- Hoster preference
- Language match

#### `infrastructure/stremio/title_matcher.py` -- Title Matcher

Multi-candidate title scoring module. Compares search result titles against
the expected title (from TMDB) using:
- Normalized string comparison
- Year matching
- guessit-based title extraction from release names

#### `infrastructure/stremio/release_parser.py` -- Release Name Parser

Wraps guessit for release name parsing. Extracts:
- Title, year, resolution, codec, audio
- Season/episode for TV content
- Quality indicators (BluRay, WEB-DL, etc.)

---

### TMDB Subsystem

#### `infrastructure/tmdb/client.py` -- TMDB Client

Async TMDB API client using httpx with:
- German locale for title/overview
- Response caching
- `get_title_and_year(imdb_id)` for Stremio stream resolution
- Trending and search endpoints for Stremio catalog

#### `infrastructure/tmdb/imdb_fallback.py` -- IMDB Fallback

Title resolver for Stremio when no TMDB API key is configured:
- Fetches IMDB page directly for English title
- Wikidata SPARQL query for German title
- No API key required

---

### Hoster Resolver System

#### `infrastructure/hoster_resolvers/registry.py` -- HosterResolverRegistry

Central registry for all hoster resolvers. Features:
- URL domain matching to select appropriate resolver
- Content-type probing fallback for unrecognized domains
- Redirect following for rotating hoster domains
- Hoster hint support (plugin-provided hoster names)
- Cleanup lifecycle management

#### Hoster Resolvers

39 resolvers across three categories: streaming (extract direct video URLs), DDL (validate file availability), and XFS (15 hosters consolidated via generic `XFSResolver`).

**Streaming resolvers** (extract `.mp4`/`.m3u8` URLs):

| Resolver | File | Technique |
|---|---|---|
| VOE | `voe.py` | Multi-method: JSON extraction, obfuscated JS |
| Streamtape | `streamtape.py` | Token extraction from page source |
| SuperVideo | `supervideo.py` | XFS extraction + packed JS + StealthPool CF fallback |
| DoodStream | `doodstream.py` | `pass_md5` API endpoint extraction |
| Filemoon | `filemoon.py` | Packed JS unpacker + Byse SPA challenge/decrypt flow |
| Mixdrop | `mixdrop.py` | Token extraction, multi-domain |
| VidGuard | `vidguard.py` | Multi-domain embed resolution |
| Vidking | `vidking.py` | Embed page validation |
| Stmix | `stmix.py` | Embed page validation |
| SerienStream | `serienstream.py` | s.to / serien.sx domain matching |

**DDL resolvers** (validate file availability):

| Resolver | File | Technique |
|---|---|---|
| Filer.net | `filernet.py` | Public status API |
| Rapidgator | `rapidgator.py` | Website scraping |
| DDownload | `ddownload.py` | XFS page check with canonical URL normalization |
| Alfafile | `alfafile.py` | Page scraping |
| AlphaDDL | `alphaddl.py` | Page scraping |
| Fastpic | `fastpic.py` | Image host validation |
| Filecrypt | `filecrypt.py` | Container validation |
| FileFactory | `filefactory.py` | Page scraping |
| FSST | `fsst.py` | Page scraping |
| Go4up | `go4up.py` | Mirror link validation |
| Nitroflare | `nitroflare.py` | Page scraping |
| 1fichier | `onefichier.py` | Page scraping, multi-domain |
| Turbobit | `turbobit.py` | Page scraping, multi-domain |
| Uploaded | `uploaded.py` | Page scraping, multi-domain |

**XFS consolidated resolvers** (`xfs.py` -- 15 hosters via parameterised `XFSConfig`):

Katfile, Hexupload, Clicknupload, Filestore, Uptobox, Funxd, Bigwarp, Dropload, Goodstream, Savefiles, Streamwish, Vidmoly, Vidoza, Vinovo, Vidhide.

Each is defined as an `XFSConfig` (name, domains, file_id_re, offline_markers). The `XFSResolver` class handles resolution logic once. `create_all_xfs_resolvers()` factory creates all 15 instances.

---

### Stream Link Cache

#### `infrastructure/persistence/stream_link_cache.py` -- StreamLinkCache

Caches resolved hoster URLs (video direct links) to avoid repeated resolution.
Uses the same `CachePort` backend as CrawlJob and search caching.

---

## Interfaces Layer

### FastAPI Application

#### `interfaces/app.py` -- build_app()

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
| 4 | Search Engine | `HttpxSearchEngine()` | `state.search_engine` |
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

### Stremio API Router

#### `interfaces/api/stremio/router.py`

Stremio addon HTTP endpoints:

**`GET /api/v1/stremio/manifest.json`** -- Returns Stremio addon manifest with supported types (movie, series), catalogs, and resources.

**`GET /api/v1/stremio/catalog/{type}/{id}.json`** -- Catalog endpoint for TMDB trending and search. Delegates to `StremioCatalogUseCase`.

**`GET /api/v1/stremio/stream/{type}/{id}.json`** -- Stream resolution endpoint. Takes IMDb ID, resolves to ranked streams via `StremioStreamUseCase`.

**`GET /api/v1/stremio/play/{stream_id}`** -- Play endpoint. Resolves cached stream link via hoster resolver and returns 302 redirect to direct video URL.

---

### CLI

#### `interfaces/cli/__main__.py` -- CLI Entry Point

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

Scavengarr ships with **42 plugins** (33 httpx + 9 Playwright) in the `plugins/` directory. All plugins are Python-based.

### Python Plugins -- Httpx (33)

All httpx plugins extend `HttpxPluginBase` and follow a consistent pattern:
- Configurable settings at top (`_DOMAINS`, `_MAX_PAGES`, `_PAGE_SIZE`)
- `search()` method with `query`, `category`, `season`, `episode` params
- Bounded concurrency via `self._new_semaphore()`
- Domain fallback via `self._verify_domain()`

Examples:
- `einschalten.py` -- JSON API plugin (simplest pattern)
- `movie4k.py` -- JSON API with browse + detail pages
- `filmfans.py` -- HTML parsing with `HTMLParser` subclass + API-based release loading
- `kinox.py` -- Complex plugin with 9 mirror domains and AJAX embed extraction

### Python Plugins — Playwright (9)

All Playwright plugins extend `PlaywrightPluginBase`:
- `boerse.py` -- Cloudflare bypass + vBulletin form auth
- `byte.py` -- Cloudflare bypass + iframe link extraction
- `moflix.py` -- Internal API with Cloudflare bypass
- `scnsrc.py` -- Scene releases with multi-domain fallback

### Plugin Protocol

All Python plugins export a module-level `plugin` variable implementing:
```python
class PluginProtocol(Protocol):
    name: str
    provides: str                         # "stream" or "download"
    default_language: str                 # "de", "en", etc.
    async def search(self, query, category, season, episode) -> list[SearchResult]: ...
    async def cleanup(self) -> None: ...
```

---

## Test Suite

**3963 tests** across unit, integration, E2E, and live test categories.

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

### E2E Tests (`tests/e2e/`) -- 158 tests

| File | Tests |
|---|---|
| `test_torznab_endpoint.py` | 46 tests: caps, search, indexers, download, error responses via full app |
| `test_stremio_endpoint.py` | Stremio endpoint E2E (mock plugins, full HTTP flow) |
| `test_stremio_series_e2e.py` | Stremio series E2E (season/episode filtering) |
| `test_stremio_streamable_e2e.py` | 31 streamable link verification tests |

### Integration Tests (`tests/integration/`) -- 25 tests

| File | Tests |
|---|---|
| `test_config_loading.py` | Configuration precedence (YAML + ENV + CLI + defaults) |
| `test_crawljob_lifecycle.py` | CrawlJob create → retrieve → expire with real cache |
| `test_link_validation.py` | HEAD/GET validation with mocked HTTP responses |

### Live Smoke Tests (`tests/live/`) -- 38 tests

| File | Tests |
|---|---|
| `test_plugin_smoke.py` | Parametrized across all plugins, hits real websites with 60s timeout. Skips on network errors. |
| `test_resolver_live.py` | Parameterised XFS resolver contract tests for known live/dead URLs. |

### Domain Tests (`tests/unit/domain/`) -- 6 files

| File | Tests |
|---|---|
| `test_crawljob.py` | CrawlJob construction, expiry, serialization, enums |
| `test_torznab_entities.py` | TorznabQuery, TorznabItem, TorznabCaps, exception hierarchy |
| `test_search_result.py` | SearchResult construction, defaults, StageResult |
| `test_plugin_schema.py` | Plugin schema, auth config |
| `test_stremio_entities.py` | Stremio domain entities |

### Application Tests (`tests/unit/application/`) -- 7 files

| File | Tests |
|---|---|
| `test_crawljob_factory.py` | SearchResult → CrawlJob conversion, TTL, URL bundling |
| `test_torznab_caps.py` | Capabilities use case |
| `test_torznab_indexers.py` | Indexer listing, resilience to broken plugins |
| `test_torznab_search.py` | Full search flow, caching, plugin dispatch |
| `test_stremio_catalog.py` | Stremio catalog use case (trending, search) |
| `test_stremio_stream.py` | Stremio stream resolution (IMDb → ranked streams) |

### Infrastructure Tests (`tests/unit/infrastructure/`) -- ~90 files

Core infrastructure:

| File | Tests |
|---|---|
| `test_converters.py` | `to_int()` |
| `test_parsers.py` | `parse_size_to_bytes()` |
| `test_extractors.py` | `extract_download_link()` |
| `test_html_selectors.py` | CSS-selector HTML extraction |
| `test_presenter.py` | Torznab XML rendering |
| `test_link_validator.py` | HEAD/GET validation, batch, timeout |
| `test_search_engine.py` | Result conversion, dedup, validation pipeline |
| `test_crawljob_cache.py` | CrawlJob persistence |
| `test_stream_link_cache.py` | Stream link caching |
| `test_auth_env_resolution.py` | Env var credential resolution |
| `test_plugin_registry.py` | Plugin discovery and loading |
| `test_httpx_base.py` | HttpxPluginBase shared base class |
| `test_playwright_base.py` | PlaywrightPluginBase shared base class |

Stremio components:

| File | Tests |
|---|---|
| `test_stream_converter.py` | SearchResult → RankedStream conversion |
| `test_stream_sorter.py` | Stream ranking and sorting |
| `test_title_matcher.py` | Multi-candidate title scoring |
| `test_release_parser.py` | guessit release name parsing |
| `test_tmdb_client.py` | TMDB API client |
| `test_imdb_fallback.py` | IMDB/Wikidata title fallback |

Hoster resolvers:

| File | Tests |
|---|---|
| `test_xfs_resolver.py` | Generic XFS resolver (15 hosters, 219 parameterised tests) |
| `test_voe_resolver.py` | VOE multi-method extraction |
| `test_streamtape_resolver.py` | Streamtape token extraction |
| `test_supervideo_resolver.py` | SuperVideo XFS + packed JS |
| `test_doodstream_resolver.py` | DoodStream pass_md5 |
| `test_filemoon_resolver.py` | Filemoon packed JS + Byse SPA |
| `test_ddownload_resolver.py` | DDownload XFS + canonical URL |
| `test_alfafile_resolver.py` | Alfafile DDL validation |
| `test_alphaddl_resolver.py` | AlphaDDL validation |
| `test_fastpic_resolver.py` | Fastpic image host |
| `test_filecrypt_resolver.py` | Filecrypt container |
| `test_filefactory_resolver.py` | FileFactory DDL |
| `test_fsst_resolver.py` | FSST validation |
| `test_go4up_resolver.py` | Go4up mirror links |
| `test_mixdrop_resolver.py` | Mixdrop streaming |
| `test_nitroflare_resolver.py` | Nitroflare DDL |
| `test_onefichier_resolver.py` | 1fichier DDL |
| `test_serienstream_resolver.py` | SerienStream resolution |
| `test_stmix_resolver.py` | Stmix streaming |
| `test_turbobit_resolver.py` | Turbobit DDL |
| `test_uploaded_resolver.py` | Uploaded DDL |
| `test_vidguard_resolver.py` | VidGuard streaming |
| `test_vidking_resolver.py` | Vidking streaming |
| `test_hoster_registry.py` | Registry domain matching, probing |

All resolver tests use `respx` (httpx-native HTTP mocking).

Plugin tests (35 files, one per Python plugin):

| File | Plugin |
|---|---|
| `test_aniworld_plugin.py` | aniworld |
| `test_boerse_plugin.py` | boerse |
| `test_burningseries_plugin.py` | burningseries |
| `test_byte_plugin.py` | byte |
| `test_cine_plugin.py` | cine |
| ... | (22 more plugin test files) |

### Interfaces Tests (`tests/unit/interfaces/`) -- 3 files

| File | Tests |
|---|---|
| `test_stremio_router.py` | Stremio router endpoints |
| `test_router_category.py` | Category routing/mapping |

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
    │ (orchestration) │ │             │ │ (SearchEngine,   │
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

**Torznab search request:**
```
Router → TorznabSearchUseCase → CachePort (search result caching, 900s TTL)
                               → SearchEnginePort (→ HttpxSearchEngine)
                               → PluginRegistryPort (→ PluginRegistry)
                               → CrawlJobFactory
                               → CrawlJobRepository (→ CacheCrawlJobRepository → CachePort)
```

**Stremio stream request:**
```
StremioRouter → StremioStreamUseCase → TMDBClient / IMDBFallback (title lookup)
                                      → PluginRegistryPort (all compatible plugins)
                                      → StreamConverter (SearchResult → RankedStream)
                                      → TitleMatcher (score against expected title)
                                      → StreamSorter (rank by quality, hoster, language)
              → HosterResolverRegistry → VOE/Streamtape/SuperVideo/DoodStream/Filemoon
                                       → StreamLinkCache (cache resolved URLs)
```

**Plugin loading:**
```
PluginRegistry → loader.load_python_plugin() → importlib dynamic import
                                              → HttpxPluginBase / PlaywrightPluginBase
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
