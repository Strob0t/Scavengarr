[← Back to Index](../features/README.md)

# Code Plan

> Module map of the Scavengarr codebase plus the key design decisions and invariants that are not obvious from reading a single file.

---

## About This Document

This is a navigation aid, not an API reference. It lists every package with its responsibility and key classes/functions, then records cross-cutting design decisions and invariants. The code is the source of truth — when this document and the code disagree, the code wins and this document should be fixed.

Related documents:

- [Clean Architecture](clean-architecture.md) — layer rules, ports, composition root, request flow, error mapping.
- [Hoster Resolvers](../features/hoster-resolvers.md) — resolver inventory and how to add a hoster.
- [Configuration](../features/configuration.md) — all config keys and environment variables.

---

## Module Map

All paths are relative to `src/scavengarr/` unless stated otherwise.

### Domain

| Path | Responsibility | Key classes/functions |
|---|---|---|
| `domain/entities/torznab.py` | Torznab query/result types and error tree | `TorznabQuery`, `TorznabItem`, `TorznabCaps`, `TorznabIndexInfo`, `TorznabError` and subclasses |
| `domain/entities/crawljob.py` | JDownloader `.crawljob` entity (frozen) | `CrawlJob` (`is_expired()`, `to_crawljob_format()`), `BooleanStatus`, `Priority`, `CrawlJobResolveError` |
| `domain/entities/stremio.py` | Stremio types and error tree | `StremioStreamRequest`, `StremioStream`, `StremioMetaPreview`, `RankedStream`, `StreamQuality`, `StreamLanguage`, `TitleMatchInfo`, `CachedStreamLink`, `ResolvedStream`, `StremioError` and subclasses |
| `domain/entities/scoring.py` | Plugin-scoring types | `ProbeResult`, `EwmaState`, `PluginScoreSnapshot` |
| `domain/plugins/base.py` | Plugin contract | `SearchResult`, `PluginProtocol`, `PluginProvides`, `GrabResolvingPlugin` (optional grab-time link resolution) |
| `domain/plugins/plugin_schema.py` | Plugin value objects | `AuthConfig`, `HttpOverrides` |
| `domain/plugins/exceptions.py` | Plugin errors | `PluginError`, `PluginLoadError`, `PluginNotFoundError`, `DuplicatePluginError` |
| `domain/ports/` | `Protocol` ports | `CachePort`, `SearchEnginePort`, `PluginRegistryPort` (sync), `CrawlJobRepository`, `StreamLinkRepository`, `HosterResolverPort`, `PluginScoreStorePort`, `TmdbClientPort`, `ConcurrencyPoolPort`, `ConcurrencyBudgetPort` |

### Application

| Path | Responsibility | Key classes/functions |
|---|---|---|
| `application/use_cases/torznab_search.py` | Torznab search: cache → `plugin.search` → validate → CrawlJobs → paginate | `TorznabSearchUseCase`, `SearchResponse` |
| `application/use_cases/torznab_caps.py` | Capabilities for one plugin | `TorznabCapsUseCase` |
| `application/use_cases/torznab_indexers.py` | Indexer listing | `TorznabIndexersUseCase` |
| `application/use_cases/crawljob_resolve.py` | Grab time: resolve a CrawlJob's page URLs through its `GrabResolvingPlugin` | `CrawlJobResolveUseCase` |
| `application/use_cases/stremio_stream.py` | IMDb ID → title(s) → plugin fan-out → filter/rank → play/proxy links | `StremioStreamUseCase` |
| `application/use_cases/stremio_catalog.py` | TMDB trending and search catalogs | `StremioCatalogUseCase` |
| `application/stremio/plugin_search.py` | Plugin fan-out with fair-share budget, shared search deadline, circuit breaker, fallback queries | `PluginSearchRunner` |
| `application/stremio/queries.py` | Search query normalization and multi-language references | `build_search_query`, `build_search_queries`, `build_multi_lang_reference`, `build_lang_group_queries` |
| `application/stremio/stream_builder.py` | Stream formatting, hoster dedup, behavior hints, cache/proxy links | `format_stream`, `deduplicate_by_hoster`, `is_direct_video_url`, `build_stream_from_resolved` |
| `application/factories/crawljob_factory.py` | `SearchResult` → `CrawlJob` | `CrawlJobFactory` |

### Infrastructure

| Path | Responsibility | Key classes/functions |
|---|---|---|
| `infrastructure/cache/` | `CachePort` backends | `create_cache()`, `DiskcacheAdapter`, `RedisAdapter` |
| `infrastructure/common/` | Converters, parsers, outbound rate limiting and retry | `to_int`, `parse_size_to_bytes`, `TokenBucket`, `DomainRateLimiter`, `RetryTransport` |
| `infrastructure/config/` | Layered configuration | `DEFAULT_CONFIG`, `AppConfig`, `CacheConfig`, `StremioConfig`, `ScoringConfig`, `PluginsConfig`, `PluginOverride`, `EnvOverrides`, `load_config()` |
| `infrastructure/hoster_resolvers/` | Hoster URL resolution and liveness probing | `HosterResolverRegistry`, `extract_domain`, `XFSResolver`/`XFSConfig`, `GenericDDLResolver`/`GenericDDLConfig`, dedicated `*Resolver` classes, `probe_url`, `probe_urls_stealth`, `verify_video_url` |
| `infrastructure/browser/` | Browser process and Cloudflare handling | `SharedBrowserPool` (one Chromium), `StealthPool` (CF-bypass context on it), `ClearanceStore` (challenge cookies across restarts), `SolverFetcher`/`ChainedBrowserFetcher` (optional Byparr/FlareSolverr sidecar), `resolve_headless`, `is_cloudflare_challenge` |
| `infrastructure/captcha/` | In-process captcha handling | `detect_challenge` (challenge/captcha classification), `solve_altcha` (ALTCHA proof of work) |
| `infrastructure/logging/` | structlog + stdlib setup with async emission | `configure_logging()` |
| `infrastructure/persistence/` | `CachePort`-backed repositories (JSON) | `CacheCrawlJobRepository`, `CacheStreamLinkRepository`, `CachePluginScoreStore` |
| `infrastructure/plugins/` | Plugin discovery, loading and base classes | `PluginRegistry`, `load_python_plugin()`, `HttpxPluginBase`, `PlaywrightPluginBase`, `SharedBrowserPool`, `decrypt_cnl` (Click'n'Load), devideosrc player helpers, `request_browser_context`, `search_max_results`, `DEFAULT_*` constants |
| `infrastructure/scoring/` | Background plugin scoring | EWMA functions (`ewma.py`), `HealthProber`, `MiniSearchProber`, `QueryPoolBuilder`, `ScoringScheduler` |
| `infrastructure/stremio/` | Stremio result processing | `convert_search_results`, `StreamSorter`, `score_title_match`, `filter_by_title_match`, `parse_quality`, `parse_language`, `filter_by_episode`, HLS proxy (`rewrite_manifest`, `fetch_hls_resource`, `stream_hls_segment`) |
| `infrastructure/tmdb/` | Title/metadata lookup | `HttpxTmdbClient`, `ImdbFallbackClient` (IMDb Suggest API + Wikidata) |
| `infrastructure/torznab/` | Link validation for results and XML rendering | `HttpxSearchEngine`, `render_caps_xml`, `render_rss_xml`, `TorznabRendered` |
| `infrastructure/validation/` | HTTP link validator | `HttpLinkValidator` |
| `infrastructure/circuit_breaker.py` | Per-plugin circuit breaker (closed/open/half-open) | `PluginCircuitBreaker` |
| `infrastructure/concurrency.py` | Global fair-share httpx + Playwright slots | `ConcurrencyPool`, `RequestBudget` |
| `infrastructure/graceful_shutdown.py` | Readiness and in-flight request draining | `GracefulShutdown` |
| `infrastructure/metrics.py` | In-memory runtime metrics | `MetricsCollector`, `PluginStats`, `ProbeStats` |
| `infrastructure/resource_detector.py` | cgroup v2/v1 CPU and memory detection | `detect_resources()`, `DetectedResources` |

### Interfaces

| Path | Responsibility | Key classes/functions |
|---|---|---|
| `interfaces/app.py` | FastAPI factory, health probes, request logging | `create_app()`, `/api/v1/healthz`, `/api/v1/readyz` |
| `interfaces/app_state.py` | Typed DI container | `AppState` (extends Starlette `State`) |
| `interfaces/composition.py` | Composition root | `lifespan()`, `_auto_tune()`, `_auto_tune_concurrency()` |
| `interfaces/api/torznab/router.py` | Torznab endpoints | `/torznab/indexers`, `/torznab/{plugin_name}`, `/torznab/{plugin_name}/health` |
| `interfaces/api/download/router.py` | CrawlJob download (resolves grab-time links first, `502` on failure) | `/download/{job_id}`, `/download/{job_id}/info` |
| `interfaces/api/stremio/router.py` | Stremio addon | `/stremio/manifest.json`, catalog, catalog search, stream, `/stremio/play/{stream_id}`, `/stremio/proxy/{stream_id}/{path}`, `/stremio/health` |
| `interfaces/api/stats/router.py` | Stats endpoints | `/stats/plugin-scores`, `/stats/metrics` |
| `interfaces/api/middleware.py` | Inbound API rate limiting | `RateLimitMiddleware` |
| `interfaces/cli/__main__.py` | Process entry point | `start()` |

### Repository Top Level

| Path | Responsibility |
|---|---|
| `plugins/` | Site plugins; each module exports a module-level `plugin` inheriting from `HttpxPluginBase` or `PlaywrightPluginBase` |
| `tests/unit/` | Unit tests per layer (`domain/`, `application/`, `infrastructure/`, `interfaces/`) |
| `tests/integration/` | Config loading, CrawlJob lifecycle with a real `DiskcacheAdapter`, link validation |
| `tests/e2e/` | Full-app Torznab and Stremio endpoint tests via `TestClient` |
| `tests/live/` | Opt-in (`-m live`) plugin smoke tests, resolver contract tests and the Stremio end-to-end use case (`test_stremio_e2e_live.py`) against real sites |
| `tests/benchmark/` | Concurrency/probe/auto-tune benchmarks (ignored by default `addopts`) |
| `tests/fixtures/` | HTML fixtures |

---

## Key Design Decisions & Invariants

### Cache

- `create_cache(backend="diskcache" | "redis", *, directory, redis_url, ttl_seconds=3600, max_concurrent=10)` returns a `CachePort`.
- `DiskcacheAdapter` wraps the synchronous `diskcache.Cache`: all disk I/O runs via `asyncio.to_thread()`, and an `asyncio.Semaphore(max_concurrent)` (default 10) limits SQLite lock contention.
- `RedisAdapter` uses `redis.asyncio` (no thread offloading), a semaphore (default 50), and pickles values.
- In `environment == "dev"` the cache is cleared on startup (`composition.py`).

### Plugin Registry and Loader Contract

- `PluginRegistry.discover()` only indexes `.py` file paths (no Python execution) and runs once.
- `get(name)` loads the plugin on first access and caches it; unknown names raise `PluginNotFoundError` (mapped to `TorznabPluginNotFound` by the use cases).
- `get_by_provides()`, `get_languages()` and `get_mode()` use a metadata cache built on first call.
- `load_python_plugin(path)` imports the module dynamically and requires a module-level `plugin` with a `search` attribute and a non-empty string `name`; otherwise it raises `PluginLoadError`.
- `HttpxPluginBase.set_shared_http_client()` injects the app-wide `httpx.AsyncClient` into httpx plugins; Playwright plugins get the shared `SharedBrowserPool`.

### Link Validation Pipeline

- `HttpxSearchEngine.validate_results()` is the only `SearchEnginePort` method; it does not call plugins. With `validate_links=False` results pass through unchanged.
- `_filter_valid_links()`: results that already have `validated_links` pass through; all unique URLs (primary + alternatives) of the remaining results go through a single `validate_batch()` call; each result gets its ordered, deduplicated `validated_links`; if the primary link is dead an alternative is promoted; results with zero valid links are dropped.
- `HttpLinkValidator` tries `HEAD` first and falls back to `GET` on any failure, because some hosters return 403 on `HEAD` but 200 on `GET`. 2xx/3xx counts as valid.
- Concurrency is bounded by `asyncio.Semaphore(max_concurrent)`; outcomes are cached in memory (valid: 6 h, invalid: 15 min).

### Torznab Search and Presenter

- `TorznabSearchUseCase` caches raw plugin results under `search:<sha256(plugin:query:category)[:16]>` with TTL `cache.search_ttl_seconds` (default 900 s), overridden by a plugin's `cache_ttl`. Cache read/write errors are logged and ignored.
- A failing `plugin.search()` or `validate_results()` raises `TorznabExternalError`; a single failing CrawlJob conversion is logged and skipped.
- CrawlJob saves run in parallel via `asyncio.gather`; pagination (`offset`, `limit`) is applied after item building.
- `render_rss_xml()`: item `<title>` uses `release_name` when present; `<guid>` is the original `download_url`; `<link>` and `<enclosure>` point to `{base_url}api/v1/download/{job_id}` with enclosure type `application/x-crawljob`; `size` is converted to bytes.
- `render_caps_xml()` advertises categories `2000` (Movies), `5000` (TV) and `8000` (Other).

### Torznab Router Special Cases

- `t=search` without `q` and `extended=1`: lightweight reachability probe of the plugin's `base_url`; returns one test item if reachable, HTTP 503 if not, HTTP 422 if the plugin has no `base_url`.
- `t=search` without `q` otherwise: empty RSS with HTTP 200.
- Only the first value of a comma-separated `cat` is used. Responses carry `X-Cache: HIT|MISS`.
- `/torznab/{plugin_name}/health` probes `base_url` and, when unreachable and `mirror_urls` are configured, probes the mirrors.
- Production error handling: see [Error Mapping](clean-architecture.md#error-mapping).

### CrawlJob Download

- `CrawlJobFactory` joins links with `\r\n` into `text`, uses `result.title` as `package_name`, builds `comment` from description, size and source URL, and sets `expires_at = now + default_ttl_hours`.
- `CacheCrawlJobRepository` stores JSON under `crawljob:{job_id}` with a TTL.
- `/download/{job_id}` returns 404 for missing or expired jobs and serves `to_crawljob_format()` with `Content-Type: application/x-crawljob`, `Content-Disposition: attachment; filename="<ascii_name>_<id8>.crawljob"; filename*=UTF-8''<name>`, and `X-CrawlJob-ID`, `X-CrawlJob-Package`, `X-CrawlJob-Links` headers.
- `/download/{job_id}/info` returns `job_id`, `package_name`, `created_at`, `expires_at`, `is_expired`, `validated_urls`, `source_url`, `comment`, `auto_start`, `priority` as JSON.

### Stremio

- `StremioStreamUseCase` gets infrastructure behaviour as injected callables (`convert_fn`, `filter_fn`, `episode_filter_fn`, `probe_fn`, `resolve_fn`, `browser_warmup_fn`) and protocols, so `application/` has no infrastructure imports.
- Each stream request acquires one `ConcurrencyPool.request()` budget; `PluginSearchRunner` takes httpx or Playwright slots per plugin (by `get_mode()`), ends the search `plugin_timeout_seconds` after the request start (a shared deadline: queued plugins are skipped, running ones cut; a timeout counts as breaker failure only when the plugin had at least half the budget) and skips plugins whose `PluginCircuitBreaker` is open (cooldown doubles per failed half-open trial, max 1 h).
- Resolution (`_resolve_top_streams`) runs hosters in parallel and a hoster's streams in rank order (`_HosterQueues`: next stream only after the better one failed), stops at `stream_deadline_seconds` after the request start (at least 2 s after the search) and yields one working stream per hoster; `deduplicate_by_hoster` is only used when no resolver is configured.
- Result conversion (`convert_fn`) runs in a thread executor to keep the event loop free.
- `/stremio/play/{stream_id}` loads the `CachedStreamLink`, resolves it via `HosterResolverRegistry.resolve()` and returns a 302 redirect; it returns 502 instead of redirecting to an embed page.

### Configuration Load Flow

- Precedence: defaults < YAML < ENV < CLI. `load_config(*, config_path, dotenv_path, cli_overrides)` has no filesystem side effects.
- Flow: load `.env` (if given, `override=False`) → `DEFAULT_CONFIG` normalized to sectioned shape → deep-merge YAML → deep-merge `EnvOverrides().to_update_dict()` → deep-merge CLI overrides → `AppConfig.model_validate()`.
- `_normalize_layer()` maps flat keys (e.g. `plugin_dir`) to sections (e.g. `plugins.plugin_dir`); `_deep_merge()` lets the override win.
- At startup the composition root auto-tunes concurrency from detected container/host resources (`_auto_tune()` when `stremio.auto_tune_all`, else `_auto_tune_concurrency()`).

### Async Logging

- `configure_logging(config)` configures structlog processors, applies a uvicorn-compatible `dictConfig`, and routes all stdlib logging through a `QueueHandler` (`_StructlogPreservingQueueHandler`) to a background `QueueListener`.
- DEBUG/INFO/WARNING go to stdout, ERROR/CRITICAL to stderr; the listener is stopped via `atexit`.
- JSON renderer in production, console renderer in dev/test (unless `log_format` is set).

### CLI Startup

| Flag | Default | Description |
|---|---|---|
| `--host` | `$HOST` or `0.0.0.0` | Bind host |
| `--port` | `$PORT` or `7979` | Bind port |
| `--config` | none | Path to YAML config file |
| `--dotenv` | none | Path to `.env` file |
| `--plugin-dir` | none | Override plugins directory |
| `--log-level` | none | Log level override |
| `--log-format` | none | `json` or `console` |

Startup: parse arguments → resolve host/port → build CLI overrides → `load_config()` → `configure_logging()` → `uvicorn.run(create_app(config), ...)`. Resources are created in `lifespan()`, never in `create_app()`.

### Cross-Cutting

- Logging uses `structlog.get_logger(__name__)` with context fields such as `plugin`, `query`, `duration_ms`.
- All I/O is async; parallel work uses `asyncio.gather()` bounded by semaphores; sync disk I/O uses `asyncio.to_thread()`.
- Plugins implement multi-stage scraping internally; `HttpxPluginBase._new_semaphore()` bounds their parallelism (`_max_concurrent`, default `DEFAULT_MAX_CONCURRENT = 5`).
- Outbound HTTP goes through `RetryTransport` + `DomainRateLimiter` on the shared client; inbound API calls are limited by `RateLimitMiddleware` when `api_rate_limit_rpm > 0`.

---

## Dependency Chains

### Layers

```text
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
    ┌──────────▼──────┐ ┌──────▼──────┐ ┌──────▼───────────┐
    │   Use Cases     │ │  Factories  │ │    Adapters      │  infrastructure/
    │ (orchestration) │ │             │ │ (SearchEngine,   │
    └──────────┬──────┘ └──────┬──────┘ │  caches, registry│
               │               │        │  resolvers, ...) │
               │               │        └──────┬───────────┘
    ┌──────────▼───────────────▼───────────────▼──┐
    │              Domain Layer                    │  domain/
    │  (Entities, Value Objects, Ports/Protocols)  │
    │  No external dependencies                    │
    └──────────────────────────────────────────────┘
```

### Torznab Search Request

```text
Router → TorznabSearchUseCase → PluginRegistryPort (→ PluginRegistry)
                              → CachePort (search result cache, default 900 s TTL)
                              → plugin.search()
                              → SearchEnginePort (→ HttpxSearchEngine → HttpLinkValidator)
                              → CrawlJobFactory
                              → CrawlJobRepository (→ CacheCrawlJobRepository → CachePort)
Router → render_rss_xml()
```

### Stremio Stream Request

```text
StremioRouter → StremioStreamUseCase → TmdbClientPort (→ HttpxTmdbClient / ImdbFallbackClient)
                                     → PluginRegistryPort.get_by_provides("stream" | "both")
                                     → ConcurrencyPool.request() → RequestBudget
                                     → PluginSearchRunner → PluginCircuitBreaker
                                                          → plugin.search() → SearchEnginePort.validate_results()
                                     → filter_by_title_match / filter_by_episode
                                     → convert_search_results → StreamSorter → resolve (deadline) → per-hoster dedup
                                     → StreamLinkRepository (→ CacheStreamLinkRepository → CachePort)
StremioRouter (/play) → StreamLinkRepository → HosterResolverRegistry.resolve() → 302
```

### Plugin Loading

```text
PluginRegistry → loader.load_python_plugin() → importlib dynamic import
                                              → HttpxPluginBase / PlaywrightPluginBase
```

### Configuration

```text
CLI → load_config() → defaults.DEFAULT_CONFIG
                    → YAML file (yaml.safe_load)
                    → EnvOverrides (pydantic-settings)
                    → CLI overrides
                    → AppConfig.model_validate()
```

### Cache Selection

```text
Composition Root → cache_factory.create_cache()
                   → DiskcacheAdapter (SQLite via diskcache)
                   → RedisAdapter (redis.asyncio)
```
