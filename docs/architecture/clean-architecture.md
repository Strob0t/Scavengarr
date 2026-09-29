[← Back to Index](../features/README.md)

# Clean Architecture

> How Scavengarr splits its code into four concentric layers (Domain, Application, Infrastructure, Interfaces) and how dependencies, wiring and requests flow between them.

---

## Overview

Scavengarr follows **Clean Architecture** (Robert C. Martin) to enforce strict separation of concerns. Every module lives in one of four concentric layers. Dependencies always point **inward** — outer layers depend on inner layers, never the reverse.

```text
┌────────────────────────────────────────────────┐
│  Interfaces (Controllers, CLI, HTTP Router)    │  ← Frameworks & Drivers
├────────────────────────────────────────────────┤
│  Infrastructure (httpx, Playwright, Cache)     │  ← Interface Adapters
├────────────────────────────────────────────────┤
│  Application (Use Cases, Factories, Services)  │  ← Application Business Rules
├────────────────────────────────────────────────┤
│  Domain (Entities, Value Objects, Protocols)   │  ← Enterprise Business Rules
└────────────────────────────────────────────────┘
```

For a per-package module map see [codeplan.md — Module Map](codeplan.md#module-map).

---

## Table of Contents

1. [The Dependency Rule](#the-dependency-rule)
2. [Layer 1 — Domain](#layer-1--domain)
3. [Layer 2 — Application](#layer-2--application)
4. [Layer 3 — Infrastructure](#layer-3--infrastructure)
5. [Layer 4 — Interfaces](#layer-4--interfaces)
6. [Composition Root](#composition-root)
7. [Request Flow](#request-flow)
8. [Error Mapping](#error-mapping)
9. [Testing Strategy Per Layer](#testing-strategy-per-layer)
10. [Key Design Decisions](#key-design-decisions)
11. [Directory Structure](#directory-structure)

---

## The Dependency Rule

The single most important principle:

> **Source code dependencies must point inward only.**

| Layer | May import from | Must NOT import from |
|---|---|---|
| Domain | stdlib (`typing`, `dataclasses`, `enum`, `datetime`, `json`, `uuid`) | Application, Infrastructure, Interfaces, any third-party library |
| Application | Domain, stdlib, non-framework libraries (`structlog`, `unidecode`) | Infrastructure, Interfaces, frameworks (`fastapi`, `httpx`, `playwright`, `diskcache`) |
| Infrastructure | Domain | Application, Interfaces |
| Interfaces | All layers | — |

Scavengarr enforces this through **Protocols** (PEP 544). The Domain layer defines abstract contracts (ports) that Infrastructure implements (adapters). Application orchestrates business logic by depending only on these protocols, never on concrete implementations.

### What this means in practice

```python
# src/scavengarr/domain/ports/cache.py — Domain defines the contract (simplified)
class CachePort(Protocol):
    async def get(self, key: str) -> Any: ...
    async def set(self, key: str, value: Any, *, ttl: int | None = None) -> None: ...

# src/scavengarr/infrastructure/cache/diskcache_adapter.py — Infrastructure implements it (simplified)
class DiskcacheAdapter:  # implicitly satisfies CachePort
    async def get(self, key: str) -> Any: ...
    async def set(self, key: str, value: Any, *, ttl: int | None = None) -> None: ...

# src/scavengarr/application/use_cases/torznab_search.py — Application uses the port (simplified)
class TorznabSearchUseCase:
    def __init__(self, ..., engine: SearchEnginePort, ..., cache: CachePort | None = None, ...):
        self._cache = cache  # CachePort protocol, not DiskcacheAdapter
```

---

## Layer 1 — Domain

**Location:** `src/scavengarr/domain/`

The Domain layer contains enterprise business rules. It is **framework-free** and **I/O-free** — no network calls, no file access, no third-party libraries beyond the standard library.

### Entities

Entities and value types are implemented as `@dataclass` classes.

| Entity | File | Purpose |
|---|---|---|
| `TorznabQuery` | `entities/torznab.py` | Immutable query: `action`, `plugin_name`, `query`, `category`, `extended`, `offset` (default `0`), `limit` (default `100`) |
| `TorznabItem` | `entities/torznab.py` | Immutable result item: `title`, `download_url`, `job_id`, `seeders`, `peers`, `size`, `release_name`, `description`, `source_url`, `category`, `grabs`, volume factors |
| `TorznabCaps` | `entities/torznab.py` | Server capabilities metadata (title, version, limits) |
| `TorznabIndexInfo` | `entities/torznab.py` | Indexer info for listing (name, version, mode) |
| `CrawlJob` | `entities/crawljob.py` | Immutable (`frozen=True`) JDownloader `.crawljob` representation with TTL (`is_expired()`) and serialization (`to_crawljob_format()`); `resolve_plugin` names the plugin that resolves its links at grab time |
| `SearchResult` | `plugins/base.py` | Normalized (mutable) scraping result with download links and metadata |
| `StremioStreamRequest`, `StremioStream`, `StremioMetaPreview`, `RankedStream`, `StreamLanguage`, `TitleMatchInfo`, `CachedStreamLink`, `ResolvedStream` | `entities/stremio.py` | Frozen Stremio types: parsed request, output stream, catalog preview, ranked candidate, language, title info, cached hoster link, resolved video URL |
| `ProbeResult`, `EwmaState`, `PluginScoreSnapshot` | `entities/scoring.py` | Frozen plugin-scoring types (probe outcome, EWMA state, per-plugin score snapshot) |

`plugins/base.py` also defines `PluginProtocol` (`name`, `provides`, `async search(query, category, season, episode)`), `PluginProvides = Literal["stream", "download", "both"]` and the optional, `runtime_checkable` capability `GrabResolvingPlugin` (`async resolve_download(url) -> list[str]`, links resolved when a CrawlJob is grabbed).

### Value Objects

Value objects are immutable configuration types (`frozen=True` dataclasses):

| Value Object | File | Purpose |
|---|---|---|
| `AuthConfig` | `plugins/plugin_schema.py` | Authentication settings (`none`/`basic`/`form`/`cookie`) |
| `HttpOverrides` | `plugins/plugin_schema.py` | Per-plugin HTTP configuration overrides |

### Enums

| Enum | File | Purpose |
|---|---|---|
| `BooleanStatus` | `entities/crawljob.py` | JDownloader tri-state: `TRUE` / `FALSE` / `UNSET` |
| `Priority` | `entities/crawljob.py` | Download priority: `HIGHEST` through `LOWER` |
| `StreamQuality` | `entities/stremio.py` | `IntEnum` stream quality ranking |

### Ports (Protocols)

Ports define the boundaries between Application and Infrastructure. All are `Protocol` classes (not ABCs).

| Port | File | Sync/Async | Methods |
|---|---|---|---|
| `CachePort` | `ports/cache.py` | async | `get`, `set`, `delete`, `exists`, `clear`, `aclose`, async context manager |
| `SearchEnginePort` | `ports/search_engine.py` | async | `validate_results` |
| `PluginRegistryPort` | `ports/plugin_registry.py` | sync | `discover`, `list_names`, `get`, `get_by_provides`, `get_languages`, `get_mode` |
| `CrawlJobRepository` | `ports/crawljob_repository.py` | async | `save`, `get` |
| `StreamLinkRepository` | `ports/stream_link_repository.py` | async | `save`, `get` |
| `HosterResolverPort` | `ports/hoster_resolver.py` | async | `name` (property), `resolve` |
| `PluginScoreStorePort` | `ports/plugin_score_store.py` | async | `get_snapshot`, `put_snapshot`, `list_snapshots`, `get_last_run`, `set_last_run` |
| `TmdbClientPort` | `ports/tmdb.py` | async | `find_by_imdb_id`, `get_title_and_year`, `get_title_by_tmdb_id`, `trending_movies`, `trending_tv`, `search_movies`, `search_tv` |
| `ConcurrencyPoolPort` | `ports/concurrency.py` | async context manager | `request()` → `ConcurrencyBudgetPort` |
| `ConcurrencyBudgetPort` | `ports/concurrency.py` | async context manager | `acquire_httpx()`, `acquire_pw()` |

Key design choice: `PluginRegistryPort` is **synchronous** (plugin files are loaded from disk, not from network). All other ports are **asynchronous** because they involve I/O (HTTP, cache, validation) or awaitable slot acquisition.

### Exception Hierarchy

```text
TorznabError (base)
├── TorznabBadRequest         → HTTP 400
├── TorznabUnsupportedAction  → HTTP 422
├── TorznabNoPluginsAvailable → HTTP 503 (defined, currently not raised)
├── TorznabPluginNotFound     → HTTP 404
├── TorznabUnsupportedPlugin  → HTTP 422 (defined, currently not raised)
└── TorznabExternalError      → HTTP 502 (dev) / 200 (prod)

PluginError (base)
├── PluginLoadError           Python plugin import/contract failure
├── PluginNotFoundError       Plugin name not in registry
└── DuplicatePluginError      Two plugins share the same name (defined, currently not raised)

StremioError (base)
├── StremioTitleNotFound
├── StremioNoPluginsAvailable
└── StremioExternalError
```

Domain exceptions carry business meaning. The Interfaces layer maps them to HTTP status codes.

---

## Layer 2 — Application

**Location:** `src/scavengarr/application/`

The Application layer contains use cases that orchestrate business logic. It knows about Domain entities and ports, but never about concrete adapters.

### Use Cases

| Use Case | File | Type | Description |
|---|---|---|---|
| `TorznabSearchUseCase` | `use_cases/torznab_search.py` | async | Validate query, resolve plugin, cache lookup, `plugin.search()`, cache write (unvalidated), `engine.validate_results()` chunk-wise until the requested page is full, build `TorznabItem`s + CrawlJobs for that page; returns `SearchResponse(items, cache_hit)` |
| `CrawlJobResolveUseCase` | `use_cases/crawljob_resolve.py` | async | Grab time: resolve a job's page URLs via its `GrabResolvingPlugin`, store and return the resolved job; `CrawlJobResolveError` → HTTP 502 |
| `TorznabCapsUseCase` | `use_cases/torznab_caps.py` | sync | Build `TorznabCaps` for a named plugin (XML is rendered by the presenter) |
| `TorznabIndexersUseCase` | `use_cases/torznab_indexers.py` | sync | List all discovered plugins with version/mode (returns `list[dict]`) |
| `StremioStreamUseCase` | `use_cases/stremio_stream.py` | async | IMDb ID → title(s) → plugin fan-out (search deadline) → title/episode filter → convert, sort → resolve one working stream per hoster (answer deadline) → cached play/proxy links; returns `list[StremioStream]` |
| `StremioCatalogUseCase` | `use_cases/stremio_catalog.py` | async | TMDB trending and search catalogs (`list[StremioMetaPreview]`) |

Helpers for `StremioStreamUseCase` live in `application/stremio/`:

- `plugin_search.py` — `PluginSearchRunner`: plugin fan-out with fair-share concurrency budget, a search deadline counted from the request start, circuit breaker, metrics and fallback queries.
- `queries.py` — search query normalization (`build_search_query`, `build_search_queries`) and multi-language title references.
- `stream_builder.py` — `format_stream`, `deduplicate_by_hoster` (only without resolver; with one, resolution picks one stream per hoster), `is_direct_video_url`, behavior hints and cache/proxy link building.

#### TorznabSearchUseCase — the central orchestrator

```python
# src/scavengarr/application/use_cases/torznab_search.py (simplified)
async def execute(self, q: TorznabQuery) -> SearchResponse:
    # 1. Validate query (action == "search", query and plugin name present)
    # 2. Resolve plugin from PluginRegistryPort (→ TorznabPluginNotFound)
    # 3. Cache read (key: sha256 of plugin:query:category)
    # 4. On miss: plugin.search(q.query, category=q.category)
    #    → cache write of the unvalidated results (plugin.cache_ttl or search_ttl)
    # 5. engine.validate_results() on chunks of `limit` results, in order,
    #    until offset + limit valid ones exist → page = valid[offset : offset + limit]
    # 6. Convert the page → TorznabItems + CrawlJobs (factory), save in parallel
    # 7. → SearchResponse(items, cache_hit)
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
        cache: CachePort | None = None,
        search_ttl: int = 900,
    ): ...
```

### Factories

| Factory | File | Description |
|---|---|---|
| `CrawlJobFactory` | `factories/crawljob_factory.py` | Converts `SearchResult` to `CrawlJob` entity |

The factory encapsulates CrawlJob creation logic: TTL calculation, URL bundling, comment generation, and JDownloader field mapping.

```python
# src/scavengarr/application/factories/crawljob_factory.py (simplified)
class CrawlJobFactory:
    def __init__(self, *, ttl_seconds: int = 3600, auto_start: bool = True, default_priority: Priority = Priority.DEFAULT):
        ...

    def create_from_search_result(self, result: SearchResult, *, job_id: str | None = None) -> CrawlJob:
        # Bundle validated_links (fallback: download_link) into text (CRLF-separated)
        # Set package_name from result.title
        # Build comment from description + size + source_url
        # Apply TTL, priority, auto_start settings
```

---

## Layer 3 — Infrastructure

**Location:** `src/scavengarr/infrastructure/`

Infrastructure implements the ports defined by Domain and provides concrete adapters for external systems.

### Port Implementations

| Port | Adapter | File |
|---|---|---|
| `CachePort` | `DiskcacheAdapter` | `cache/diskcache_adapter.py` |
| `CachePort` | `RedisAdapter` | `cache/redis_adapter.py` |
| `SearchEnginePort` | `HttpxSearchEngine` | `torznab/search_engine.py` |
| `PluginRegistryPort` | `PluginRegistry` | `plugins/registry.py` |
| `CrawlJobRepository` | `CacheCrawlJobRepository` | `persistence/crawljob_cache.py` |
| `StreamLinkRepository` | `CacheStreamLinkRepository` | `persistence/stream_link_cache.py` |
| `PluginScoreStorePort` | `CachePluginScoreStore` | `persistence/plugin_score_cache.py` |
| `TmdbClientPort` | `HttpxTmdbClient` / `ImdbFallbackClient` | `tmdb/client.py` / `tmdb/imdb_fallback.py` |
| `HosterResolverPort` | `XFSResolver`, `GenericDDLResolver`, dedicated `*Resolver` classes | `hoster_resolvers/` |
| `ConcurrencyPoolPort` | `ConcurrencyPool` (budget: `RequestBudget`) | `concurrency.py` |

### Subsystems

- **Cache** (`cache/`): two interchangeable adapters behind `CachePort`. A factory function (`create_cache()`) selects the backend based on configuration.
- **Plugins** (`plugins/`): discovery, loading and caching of Python plugins. `PluginRegistry` indexes `.py` files lazily and caches loaded plugins in memory. All plugins inherit from `HttpxPluginBase` or `PlaywrightPluginBase`; Playwright plugins share one Chromium via `SharedBrowserPool`.
- **Search Engine** (`torznab/search_engine.py`): `HttpxSearchEngine` validates links on plugin results — batch HEAD/GET via `HttpLinkValidator`, promotes alternative links when the primary is dead, drops results with no valid link; results with `validated_links` already set pass through unchanged.
- **Presenter** (`torznab/presenter.py`): renders Domain entities (`TorznabCaps`, `TorznabItem`) to Torznab-compliant RSS 2.0 XML.
- **Validation** (`validation/`): HTTP link validation with HEAD-first, GET-fallback strategy, bounded concurrency (global and per host), an in-memory TTL result cache and a 15-minute skip list for hosts that refuse connections.
- **Persistence** (`persistence/`): `CachePort`-backed repositories (CrawlJobs, stream links, plugin scores) with JSON serialization.
- **Configuration** (`config/`): layered config loading (defaults < YAML < ENV < CLI) with Pydantic validation.
- **Logging** (`logging/`): structured logging via structlog with an async `QueueHandler` for non-blocking emission.
- **Common** (`common/`): `to_int`, `parse_size_to_bytes`, `DomainRateLimiter`/`TokenBucket` (optionally adaptive), `RetryTransport` (429/503 retry + rate limiting), `PrivateAddressGuard` (the shared client refuses non-public targets, SSRF).
- **Hoster resolvers** (`hoster_resolvers/`): `HosterResolverRegistry`, XFS/generic-DDL/dedicated resolvers; `StealthPool` (in `infrastructure/browser/`) for Cloudflare-protected pages and for capturing the stream request of players that build it at runtime (`capture_media`: manifests/MP4 by URL, extension-less CDN URLs by the video element's request type). See [Hoster Resolvers](../features/hoster-resolvers.md).
- **Browser fetchers and anti-bot** (`browser/`, `captcha/`): `BrowserFetcherPort` implementations `StealthPool` and `SolverFetcher` (Byparr/FlareSolverr sidecar), chained by `ChainedBrowserFetcher`; `ClearanceStore` keeps challenge cookies in `CachePort` across restarts; `detect_challenge` classifies challenges and captchas; `solve_altcha` solves ALTCHA proof of work. See [Captcha Solving](../plans/captcha-solving.md).
- **Stremio** (`stremio/`): stream converter, sorter, title matcher, release parser, episode filter, HLS proxy.
- **TMDB** (`tmdb/`): `HttpxTmdbClient` and the key-less `ImdbFallbackClient`.
- **Scoring** (`scoring/`): EWMA plugin scoring, health/search probers, query pool, background `ScoringScheduler`.
- **Runtime services** (top-level modules): `PluginCircuitBreaker`, `ConcurrencyPool`, `GracefulShutdown`, `MetricsCollector`, `detect_resources()` (cgroup-aware).

---

## Layer 4 — Interfaces

**Location:** `src/scavengarr/interfaces/`

The Interfaces layer handles input/output exclusively. It contains no business logic.

### HTTP (FastAPI)

| File | Purpose |
|---|---|
| `app.py` | `create_app(config)`: creates FastAPI instance, `AppState`, `GracefulShutdown`, `RateLimitMiddleware`, registers routers (download, torznab, stremio, stats), `/api/v1/healthz` + `/api/v1/readyz`, request-logging middleware |
| `app_state.py` | `AppState` typed container (extends Starlette `State`) for DI resources |
| `composition.py` | `lifespan()` async context manager — the composition root |
| `api/middleware.py` | `RateLimitMiddleware` (per-IP sliding window) |
| `api/torznab/router.py` | `GET /api/v1/torznab/indexers`, `GET /api/v1/torznab/{plugin_name}` (caps/search), `GET /api/v1/torznab/{plugin_name}/health` |
| `api/download/router.py` | `GET /api/v1/download/{job_id}` (serves `.crawljob` files), `GET /api/v1/download/{job_id}/info` |
| `api/stremio/router.py` | Stremio addon: `manifest.json`, catalog, catalog search, stream, `play/{stream_id}` (302), HLS `proxy/{stream_id}/{path}`, `health` |
| `api/stats/router.py` | `GET /api/v1/stats/plugin-scores`, `GET /api/v1/stats/metrics` |

### CLI (argparse + Uvicorn)

| File | Purpose |
|---|---|
| `cli/__main__.py` | `start()` entry point (re-exported by `cli/__init__.py`, registered as `start` in `pyproject.toml`) |

The CLI is the process entry point. It follows the pattern:

1. Parse arguments (host, port, config path, overrides).
2. Load configuration via `load_config()` with CLI overrides.
3. Configure structured logging.
4. Build the FastAPI app via `create_app()` and run it via Uvicorn.

---

## Composition Root

**File:** `src/scavengarr/interfaces/composition.py`

The composition root is where concrete implementations are wired together. It runs inside the FastAPI `lifespan()` async context manager. Torznab use cases are instantiated per request in the router from `AppState`; Stremio use cases are built once in `lifespan()`. `create_app()` creates `AppState` and `GracefulShutdown`.

### Initialization Order

```text
0.  MetricsCollector, auto-tune concurrency (_auto_tune / _auto_tune_concurrency)
1.  Cache via create_cache() (cleared on startup when environment == "dev")
2.  httpx.AsyncClient with RetryTransport + DomainRateLimiter + PrivateAddressGuard; shared with HttpxPluginBase
3.  PluginRegistry + discover() + per-plugin config overrides
4.  HttpxSearchEngine (HTTP client + cache)
5.  CacheCrawlJobRepository
6.  CrawlJobFactory
7.  TMDB client (HttpxTmdbClient with API key, else ImdbFallbackClient)
8.  SharedBrowserPool (one Chromium) + StealthPool (own context on that browser)
9.  HosterResolverRegistry (dedicated + generic DDL + XFS resolvers)
10. CacheStreamLinkRepository
11. Plugin scoring (CachePluginScoreStore + ScoringScheduler task, only if scoring.enabled)
12. SharedBrowserPool injected into Playwright plugins
13. ConcurrencyPool
14. PluginCircuitBreaker
15. StremioStreamUseCase + StremioCatalogUseCase
    → GracefulShutdown.mark_ready()
```

### Cleanup Order

```text
1. Drain in-flight requests (GracefulShutdown, 10 s timeout)
2. Cancel scoring task
3. StealthPool.cleanup() (its context lives on the shared browser)
4. SharedBrowserPool.cleanup()
5. HosterResolverRegistry.cleanup()
6. http_client.aclose()
7. cache.aclose()
```

All resources are stored on `AppState` and accessible from any request handler via `request.app.state`.

---

## Request Flow

### Torznab Search (end-to-end)

```text
HTTP GET /api/v1/torznab/filmpalast?t=search&q=iron+man
│
├─ Router (torznab/router.py)
│   ├─ t=caps → TorznabCapsUseCase + render_caps_xml()
│   ├─ no q → empty RSS (200), or extended=1 reachability probe
│   └─ Build TorznabQuery (first value of cat, offset, limit)
│
├─ TorznabSearchUseCase.execute(query)
│   ├─ Validate: action == "search", query present, plugin_name present
│   ├─ PluginRegistry.get("filmpalast") → Python plugin
│   ├─ cache.get(key) → hit? skip plugin
│   ├─ miss: plugin.search("iron man", category=…) → list[SearchResult]
│   │   └─ cache.set(key, results, ttl)            (unvalidated)
│   ├─ Page validation: for each chunk of `limit` results, in order,
│   │   ├─ SearchEngine.validate_results(chunk)
│   │   │   └─ LinkValidator.validate_batch(chunk_urls) → filter dead links
│   │   └─ stop once offset + limit valid results exist
│   ├─ For each SearchResult of the page:
│   │   ├─ CrawlJobFactory.create_from_search_result() → CrawlJob
│   │   └─ TorznabItem with job_id
│   ├─ asyncio.gather(CrawlJobRepository.save(...) for the page's jobs)
│   └─ Return SearchResponse(page items, cache_hit)
│
├─ render_rss_xml(title=…, items=…, scavengarr_base_url=…) → RSS 2.0 XML
│
└─ Response(application/xml, header X-Cache: HIT|MISS)
```

### CrawlJob Download

```text
HTTP GET /api/v1/download/{job_id}
│
├─ Router (download/router.py)
│   ├─ CrawlJobRepository.get(job_id) → CrawlJob (404 if missing)
│   ├─ Check expiry (is_expired() → 404)
│   └─ CrawlJob.to_crawljob_format() → .crawljob content
│
└─ Response(content=crawljob, media_type="application/x-crawljob")
```

---

## Error Mapping

Domain exceptions are translated to HTTP responses in the Torznab router. Every error returns an RSS body without items. In production, the error description is omitted; upstream and unexpected errors additionally return HTTP 200 to maintain Prowlarr stability, while client/config errors keep their status code.

| Domain Exception | Dev Status | Prod Status | Behavior |
|---|---|---|---|
| `TorznabBadRequest` | 400 | 400 (empty RSS) | Invalid query parameters |
| `TorznabPluginNotFound` | 404 | 404 (empty RSS) | Plugin not in registry |
| `TorznabUnsupportedAction` | 422 | 422 (empty RSS) | Action not caps/search |
| `TorznabUnsupportedPlugin` | 422 | 422 (empty RSS) | Unsupported plugin (currently not raised) |
| `TorznabNoPluginsAvailable` | 503 | 503 (empty RSS) | No plugins discovered (currently not raised) |
| `TorznabExternalError` | 502 | 200 (empty RSS) | Upstream/network failure |
| Unhandled `Exception` | 500 | 200 (empty RSS) | Unexpected error |

---

## Testing Strategy Per Layer

Each layer has a distinct testing approach:

### Domain Tests (pure unit tests)

- No mocking required — all entities and value objects are pure data.
- Test entity construction, serialization (`to_crawljob_format()`), validation, and expiry logic.
- Test exception hierarchy.

```python
# tests/unit/domain/test_crawljob.py (simplified)
def test_crawljob_not_expired():
    job = CrawlJob(expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    assert job.is_expired() is False
```

### Application Tests (use case tests with mocked ports)

- Mock all ports (`PluginRegistryPort`, `SearchEnginePort`, `CrawlJobRepository`, …).
- Test orchestration logic: correct flow, error handling, edge cases.
- `PluginRegistryPort` is synchronous — use `MagicMock`.
- All other ports are async — use `AsyncMock`.

```python
# tests/unit/application/test_torznab_search.py (simplified)
async def test_search_returns_items(mock_plugins, mock_engine, ...):
    uc = TorznabSearchUseCase(plugins=mock_plugins, engine=mock_engine, ...)
    response = await uc.execute(TorznabQuery(action="search", query="test", ...))
    assert len(response.items) > 0
```

### Infrastructure Tests (adapter tests)

- Test concrete implementations with controlled inputs.
- For HTTP adapters and hoster resolvers: use `respx` for HTTP mocking.
- For repositories: mock `CachePort`; integration tests use a real `DiskcacheAdapter` in a temp directory.
- For parsers/converters: pure function tests.

### Integration and E2E Tests

- `tests/integration/`: configuration precedence, CrawlJob lifecycle with a real cache, link validation with mocked HTTP.
- `tests/e2e/`: full app through `TestClient` (Torznab and Stremio) — XML/JSON responses, status codes, error mapping.

---

## Key Design Decisions

### Why Protocols, not ABCs

- Protocols enable structural subtyping (duck typing with type safety).
- No inheritance required — adapters satisfy the contract implicitly.
- Easier to test: any object with the right methods qualifies as a mock.

### Why sync PluginRegistryPort

- Plugin discovery reads `.py` files from a local directory.
- This is fast local I/O, not network I/O.
- Making it async would add unnecessary complexity without benefit.

### Why injected callables in StremioStreamUseCase

- `StremioStreamUseCase` receives infrastructure behaviour as injected callables and protocols: `convert_fn`, `filter_fn`, `episode_filter_fn`, `resolve_fn`, `browser_warmup_fn`, `sorter`, plus local protocols (`_StremioConfig`, `_MetricsRecorder`, `CircuitBreaker`).
- This keeps `application/` free of infrastructure imports while the composition root plugs in `convert_search_results`, `filter_by_title_match`, `filter_by_episode`, `probe_urls_stealth` and `HosterResolverRegistry.resolve`.

### Why CrawlJob instead of direct download URLs

- Prowlarr/Sonarr/Radarr expect a single download URL per result.
- Multi-link results (multiple mirror hosters) need bundling.
- CrawlJob provides: stable ID, TTL-based expiry, multi-link packaging.
- The download endpoint serves `.crawljob` files on demand.

### Why HTTP 200 on upstream errors in production

- Prowlarr treats non-200 responses as indexer failures and may disable the indexer.
- Returning HTTP 200 with empty results for upstream/unexpected failures preserves Prowlarr stability; genuine client errors keep their 4xx/503 status.
- In development, proper HTTP status codes and error descriptions aid debugging.

### Why layered configuration

- Defaults provide sane out-of-box behavior.
- YAML allows per-deployment configuration.
- Environment variables enable container-native overrides.
- CLI arguments provide per-invocation control.
- Strict precedence (defaults < YAML < ENV < CLI) prevents surprises.

---

## Directory Structure

The per-package module map (paths, responsibilities, key classes) is maintained in [codeplan.md — Module Map](codeplan.md#module-map).
