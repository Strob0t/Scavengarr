# Architecture

Scavengarr follows **Clean Architecture** with four layers. Each layer has a clear
responsibility, and dependencies always point inward.

## Layer Diagram

```
+----------------------------------------------------+
|  Interfaces (FastAPI Router, CLI, Composition Root) |  Frameworks & Drivers
+----------------------------------------------------+
|  Infrastructure (Scrapy, Cache, Plugins, Presenter) |  Interface Adapters
+----------------------------------------------------+
|  Application (Use Cases, Factories)                 |  Application Business Rules
+----------------------------------------------------+
|  Domain (Entities, Value Objects, Protocols)         |  Enterprise Business Rules
+----------------------------------------------------+
```

**Dependency rule:** inner layers never import from outer layers.

## Layer Responsibilities

### Domain (`src/scavengarr/domain/`)

Framework-free, I/O-free business logic.

| Module | Contents |
|--------|----------|
| `entities/torznab.py` | `TorznabQuery`, `TorznabItem`, `TorznabCaps`, `TorznabIndexInfo`, exception hierarchy |
| `entities/crawljob.py` | `CrawlJob` entity, `BooleanStatus`/`Priority` enums, `.crawljob` serialization |
| `plugins/base.py` | `SearchResult`, `StageResult` dataclasses |
| `plugins/plugin_schema.py` | `YamlPluginDefinition` and all nested schema types |
| `ports/cache.py` | `CachePort` protocol |
| `ports/search_engine.py` | `SearchEnginePort` protocol |
| `ports/plugin_registry.py` | `PluginRegistryPort` protocol |
| `ports/link_validator.py` | `LinkValidatorPort` protocol |
| `ports/crawljob_repository.py` | `CrawlJobRepository` protocol |

### Application (`src/scavengarr/application/`)

Orchestration logic. Depends on domain protocols, not concrete implementations.

| Module | Purpose |
|--------|---------|
| `use_cases/torznab_search.py` | `TorznabSearchUseCase` &mdash; query validation, scraping, CrawlJob creation |
| `use_cases/torznab_caps.py` | `TorznabCapsUseCase` &mdash; plugin capabilities |
| `use_cases/torznab_indexers.py` | `TorznabIndexersUseCase` &mdash; list available plugins |
| `factories/crawljob_factory.py` | `CrawlJobFactory` &mdash; converts `SearchResult` to `CrawlJob` |

### Infrastructure (`src/scavengarr/infrastructure/`)

Concrete adapters implementing domain protocols.

| Module | Implements |
|--------|------------|
| `cache/diskcache_adapter.py` | `CachePort` via SQLite (diskcache) |
| `cache/redis_adapter.py` | `CachePort` via Redis |
| `plugins/registry.py` | `PluginRegistryPort` &mdash; YAML/Python plugin discovery |
| `torznab/search_engine.py` | `SearchEnginePort` &mdash; multi-stage scraping orchestration |
| `validation/http_link_validator.py` | `LinkValidatorPort` &mdash; HEAD/GET link validation |
| `persistence/crawljob_cache.py` | `CrawlJobRepository` &mdash; pickle-based cache storage |
| `torznab/presenter.py` | Torznab XML rendering (RSS 2.0, caps) |
| `scraping/scrapy_adapter.py` | Multi-stage CSS scraper (httpx + BeautifulSoup) |
| `config/` | Configuration loading, schema, defaults |
| `logging/setup.py` | Structured logging (structlog + stdlib) |
| `common/converters.py` | `to_int()` data converter |
| `common/parsers.py` | `parse_size_to_bytes()` size parser |
| `common/extractors.py` | `extract_download_link()` field extractor |

### Interfaces (`src/scavengarr/interfaces/`)

Thin layer for frameworks and drivers. No business logic.

| Module | Purpose |
|--------|---------|
| `main.py` | `build_app()` &mdash; FastAPI app factory |
| `composition.py` | `lifespan()` &mdash; dependency injection (composition root) |
| `app_state.py` | `AppState` typed container for shared resources |
| `api/torznab/router.py` | Torznab HTTP endpoints |
| `api/download/router.py` | CrawlJob download endpoint |
| `cli/cli.py` | CLI entry point (argparse + uvicorn) |

## Request Flow

```
HTTP GET /api/v1/torznab/filmpalast?t=search&q=iron+man
  |
  v
[Router] Parse query params -> TorznabQuery
  |
  v
[TorznabSearchUseCase]
  |-- PluginRegistry.get("filmpalast")
  |-- SearchEngine.search(plugin, query)
  |     |-- ScrapyAdapter: Stage 1 (search_results) -> detail URLs
  |     |-- ScrapyAdapter: Stage 2 (movie_detail) -> download links
  |     |-- LinkValidator.validate_batch(urls) -> filter dead links
  |     \-- Convert to list[SearchResult]
  |-- CrawlJobFactory.create_from_search_result() -> CrawlJob
  |-- CrawlJobRepository.save(crawljob)
  \-- Return list[TorznabItem] (with job_id)
  |
  v
[Presenter] render_rss_xml(items) -> RSS 2.0 XML
  |
  v
HTTP Response (application/xml)
```

When Prowlarr/Sonarr/Radarr clicks a download link:

```
HTTP GET /api/v1/download/{job_id}
  |
  v
[Download Router]
  |-- CrawlJobRepository.get(job_id)
  |-- Check expiry
  |-- CrawlJob.to_crawljob_format()
  \-- Return .crawljob file
```

## Composition Root

All dependencies are wired in `composition.py` via FastAPI's lifespan hook.

Initialization order:
1. **Cache** (diskcache or redis)
2. **HTTP Client** (httpx.AsyncClient)
3. **Plugin Registry** (discovers plugins from plugin_dir)
4. **Search Engine** (uses HTTP client + link validation config)
5. **CrawlJob Repository** (uses cache for persistence)
6. **CrawlJob Factory** (stateless)

Cleanup runs in reverse order (HTTP client, then cache).

## Error Handling

Domain errors map to HTTP status codes:

| Exception | HTTP Status | Notes |
|-----------|-------------|-------|
| `TorznabBadRequest` | 400 | Invalid query parameters |
| `TorznabPluginNotFound` | 404 | Unknown plugin name |
| `TorznabUnsupportedAction` | 422 | Action is not `caps` or `search` |
| `TorznabUnsupportedPlugin` | 422 | Plugin uses unsupported scraping mode |
| `TorznabNoPluginsAvailable` | 503 | No plugins loaded |
| `TorznabExternalError` | 200 (prod) / 502 (dev) | Upstream scraping failures |
| Unhandled exception | 200 (prod) / 500 (dev) | Internal errors |

In production, errors return empty RSS feeds (HTTP 200) to keep Prowlarr stable.
In development, errors include descriptions and appropriate HTTP status codes.
