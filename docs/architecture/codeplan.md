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
| `domain/entities/stremio.py` | Stremio types | `StremioStreamRequest`, `StremioStream`, `StremioMetaPreview`, `RankedStream`, `StreamQuality`, `StreamLanguage`, `TitleMatchInfo`, `CachedStreamLink`, `ResolvedStream` |
| `domain/entities/scoring.py` | Plugin-scoring types | `ProbeResult`, `EwmaState`, `PluginScoreSnapshot` |
| `domain/plugins/base.py` | Plugin contract | `SearchResult`, `PluginProtocol`, `PluginProvides`, `GrabResolvingPlugin` (optional grab-time link resolution) |
| `domain/plugins/exceptions.py` | Plugin errors | `PluginError`, `PluginLoadError`, `PluginNotFoundError` |
| `domain/ports/` | `Protocol` ports | `CachePort`, `SearchEnginePort`, `PluginRegistryPort` (sync), `CrawlJobRepository`, `StreamLinkRepository`, `HosterResolverPort`, `PluginScoreStorePort`, `TmdbClientPort`, `ConcurrencyPoolPort`, `ConcurrencyBudgetPort`, `ClientBoundResolverPort`, `BrowserFetcherPort`, `TelemetryPort` (sync; `NO_TELEMETRY` records nothing) |

### Application

| Path | Responsibility | Key classes/functions |
|---|---|---|
| `application/use_cases/torznab_search.py` | Torznab search: cache → `plugin.search` → validate until the requested page is full → items and CrawlJobs of that page | `TorznabSearchUseCase`, `SearchResponse` |
| `application/use_cases/torznab_caps.py` | Capabilities for one plugin | `TorznabCapsUseCase` |
| `application/use_cases/torznab_indexers.py` | Indexer listing | `TorznabIndexersUseCase` |
| `application/use_cases/crawljob_resolve.py` | Grab time: resolve a CrawlJob's page URLs through its `GrabResolvingPlugin` | `CrawlJobResolveUseCase` |
| `application/use_cases/stremio_stream.py` | IMDb ID → title(s) → cached or shared plugin search → filter/rank → resolve per hoster → play/proxy links | `StremioStreamUseCase` |
| `application/use_cases/stremio_links.py` | Stored stream links behind `/play` and the HLS proxy, resolved again when stale (1 h) or refused by the CDN | `StremioLinks` |
| `application/use_cases/stremio_catalog.py` | TMDB trending and search catalogs | `StremioCatalogUseCase` |
| `application/stremio/plugin_search.py` | Plugin fan-out with fair-share budget, shared search deadline, circuit breaker, fallback queries | `PluginSearchRunner` |
| `application/stremio/queries.py` | Search query normalization and multi-language references | `build_search_query`, `build_search_queries`, `build_multi_lang_reference`, `build_lang_group_queries` |
| `application/stremio/stream_builder.py` | Stream formatting, hoster dedup, behavior hints, cache/proxy links | `format_stream`, `deduplicate_by_hoster`, `is_direct_video_url`, `build_stream_from_resolved` |
| `application/stremio/search_cache.py` | Title-filtered plugin results per title, stale-while-revalidate | `SearchCache`, `CachedSearch` |
| `application/stremio/search_progress.py` | Results of a running search, shared by the requests waiting on it | `SearchProgress` |
| `application/stremio/resolution.py` | One request's resolutions: each hoster's best link in rank order | `HosterResolution` |
| `application/factories/crawljob_factory.py` | `SearchResult` → `CrawlJob` | `CrawlJobFactory` |

### Infrastructure

| Path | Responsibility | Key classes/functions |
|---|---|---|
| `infrastructure/cache/` | `CachePort` backends | `create_cache()`, `DiskcacheAdapter`, `RedisAdapter` |
| `infrastructure/common/` | Converters, parsers, outbound rate limiting, retry and the SSRF guard | `to_int`, `parse_size_to_bytes`, `TokenBucket`, `DomainRateLimiter`, `RetryTransport`, `PrivateAddressGuard` |
| `infrastructure/config/` | Layered configuration | `DEFAULT_CONFIG`, `AppConfig`, `CacheConfig`, `StremioConfig`, `ScoringConfig`, `PluginsConfig`, `PluginOverride`, `EnvOverrides`, `load_config()` |
| `infrastructure/hoster_resolvers/` | Hoster URL resolution | `HosterResolverRegistry`, `extract_domain`, `XFSResolver`/`XFSConfig`, `GenericDDLResolver`/`GenericDDLConfig`, dedicated `*Resolver` classes, `verify_video_url`, `check_playable` |
| `infrastructure/browser/` | Browser process and Cloudflare handling | `SharedBrowserPool` (one Chromium), `StealthPool` (CF-bypass context on it), `PageGate` (its pages, by claim), `PageBudget` (their count), `ClearanceStore` (challenge cookies across restarts), `SolverFetcher`/`ChainedBrowserFetcher` (optional Byparr/FlareSolverr sidecar), `resolve_headless`, `is_cloudflare_challenge` |
| `infrastructure/captcha/` | In-process captcha handling | `detect_challenge` (challenge/captcha classification), `solve_altcha` (ALTCHA proof of work) |
| `infrastructure/logging/` | structlog + stdlib setup with async emission | `configure_logging()` |
| `infrastructure/persistence/` | `CachePort`-backed repositories (JSON) | `CacheCrawlJobRepository`, `CacheStreamLinkRepository`, `CachePluginScoreStore` |
| `infrastructure/plugins/` | Plugin discovery, loading and base classes | `PluginRegistry`, `load_python_plugin()`, `HttpxPluginBase`, `PlaywrightPluginBase`, `DataApiPluginBase` (shared "/data" API backend of megakino_to and movie4k), `XenForoPluginBase` (XenForo forums dataload and myboerse), category helpers (`categories.py`: `category_matches`, `served_category`, `filter_by_category`, `stream_category`), `parse_page` (`dom.py`: parses pages from 32 KiB in a worker thread), `relevant_hits` (`relevance.py`: the search hits worth scraping), `PluginHealthMonitor` (periodic checks of the Stremio plugins' sites; searches skip the unreachable ones), `decrypt_cnl` (Click'n'Load), devideosrc player helpers, `request_browser_context`, `search_max_results`, `DEFAULT_*` constants |
| `infrastructure/scoring/` | Background plugin scoring | EWMA functions (`ewma.py`), `HealthProber`, `MiniSearchProber`, `QueryPoolBuilder`, `ScoringScheduler` |
| `infrastructure/stremio/` | Stremio result processing | `convert_search_results`, `StreamSorter`, `score_title_match`, `filter_by_title_match`, `parse_quality`, `parse_language`, `filter_by_episode`, HLS proxy (`rewrite_manifest`, `fetch_hls_resource`, `stream_hls_segment`) |
| `infrastructure/tmdb/` | Title/metadata lookup | `HttpxTmdbClient`, `ImdbFallbackClient` (IMDb Suggest API + Wikidata) |
| `infrastructure/torznab/` | Link validation for results and XML rendering | `HttpxSearchEngine`, `render_caps_xml`, `render_rss_xml`, `TorznabRendered` |
| `infrastructure/validation/` | HTTP link validator | `HttpLinkValidator` |
| `infrastructure/circuit_breaker.py` | Per-plugin circuit breaker (closed/open/half-open) | `PluginCircuitBreaker` |
| `infrastructure/concurrency.py` | Global fair-share httpx + Playwright slots | `ConcurrencyPool`, `RequestBudget` |
| `infrastructure/graceful_shutdown.py` | Readiness and in-flight request draining | `GracefulShutdown` |
| `infrastructure/telemetry/` | Prometheus metrics of the core's stages, scrape-time collectors, event-loop lag | `Telemetry`, `BreakerCollector`, `ContainerCollector`, `monitor_loop_lag()` |
| `infrastructure/resource_detector.py` | cgroup v2/v1 CPU and memory detection, CPU load and free memory | `detect_resources()`, `DetectedResources`, `ResourceSampler` |
| `infrastructure/version.py` | The app version from the package metadata (its only source is `pyproject.toml`) | `APP_VERSION`, `APP_USER_AGENT` |

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
- `DiskcacheAdapter` wraps the synchronous `diskcache.Cache`: all disk I/O runs via `asyncio.to_thread()`, and an `asyncio.Semaphore(max_concurrent)` (default 10) bounds the parallel operations. Writes (`set`, `delete`, `clear`) run one at a time: SQLite has one writer, and 20 parallel writes waited 80-100 ms for its lock where one after another took 6 ms.
- `RedisAdapter` uses `redis.asyncio` (no thread offloading), a semaphore (`cache.max_concurrent`, default 10, as for diskcache), and pickles values.
- In `environment == "dev"` the cache is cleared on startup (`composition.py`).

### Plugin Registry and Loader Contract

- `PluginRegistry.discover()` only indexes `.py` file paths (no Python execution) and runs once.
- The first call of `list_names()`, `get()`, `get_by_provides()`, `get_languages()`, `get_mode()` or `remove()` imports every discovered file once and caches instances and metadata; files that fail to import are logged and skipped. Unknown names raise `PluginNotFoundError` (mapped to `TorznabPluginNotFound` by the use cases).
- `load_python_plugin(path)` imports the module dynamically and requires a module-level `plugin` with a `search` attribute and a non-empty string `name`; otherwise it raises `PluginLoadError`.
- `HttpxPluginBase.set_shared_http_client()` injects the app-wide `httpx.AsyncClient` into httpx plugins and `set_browser_fetcher()` their browser fallback for challenged pages; Playwright plugins get the shared `SharedBrowserPool`.

### Link Validation Pipeline

- `HttpxSearchEngine.validate_results()` is the only `SearchEnginePort` method; it does not call plugins. With `validate_links=False` results pass through unchanged.
- `_filter_valid_links()`: results that already have `validated_links` pass through; all unique URLs (primary + alternatives) of the remaining results go through a single `validate_batch()` call; each result gets its ordered, deduplicated `validated_links`; if the primary link is dead an alternative is promoted; results with zero valid links are dropped.
- `HttpLinkValidator` tries `HEAD` first and falls back to `GET` when `HEAD` fails, because some hosters return 403 on `HEAD` but 200 on `GET`. A host that refuses connections (connect error or timeout) gets no `GET`: its links count as invalid for 60 s, doubling with each further failure up to 15 min. 2xx/3xx counts as valid.
- Concurrency is bounded globally (`asyncio.Semaphore(max_concurrent)`) and per host (4); outcomes are cached in memory (valid: 6 h, invalid: 15 min).

### Torznab Search and Presenter

- `TorznabSearchUseCase` caches raw plugin results under `search:<sha256(plugin:query:category)[:16]>` with TTL `cache.search_ttl_seconds` (default 900 s), overridden by a plugin's `cache_ttl`. Cache read/write errors are logged and ignored.
- A failing `plugin.search()` or `validate_results()` raises `TorznabExternalError`; a single failing CrawlJob conversion is logged and skipped.
- CrawlJob saves run in parallel via `asyncio.gather`; only the requested page (`offset`, `limit`) is validated and turned into CrawlJobs.
- `render_rss_xml()`: item `<title>` uses `release_name` when present; `<guid>` is the original `download_url`; `<link>` and `<enclosure>` point to `{base_url}api/v1/download/{job_id}` with enclosure type `application/x-crawljob`; `size` is converted to bytes.
- `render_caps_xml()` advertises categories `2000` (Movies), `5000` (TV) and `8000` (Other).

### Torznab Router Special Cases

- `t=search` without `q` and `extended=1`: lightweight reachability probe of the plugin's `base_url`; returns one test item if reachable, HTTP 503 if not, HTTP 422 if the plugin has no `base_url`.
- `t=search` without `q` otherwise: empty RSS with HTTP 200.
- Only the first value of a comma-separated `cat` is used. Responses carry `X-Cache: HIT|MISS`.
- `/torznab/{plugin_name}/health` probes the plugin's current `base_url` (the domain in use of its `_domains`).
- Production error handling: see [Error Mapping](clean-architecture.md#error-mapping).

### CrawlJob Download

- `CrawlJobFactory` joins links with `\r\n` into `text`, uses `result.title` as `package_name`, builds `comment` from description, size and source URL, and sets `expires_at = now + ttl_seconds` (`cache.crawljob_ttl_seconds`, the TTL the repository stores the job with).
- `CacheCrawlJobRepository` stores JSON under `crawljob:{job_id}` with a TTL.
- `/download/{job_id}` returns 404 for missing or expired jobs and serves `to_crawljob_format()` with `Content-Type: application/x-crawljob`, `Content-Disposition: attachment; filename="<ascii_name>_<id8>.crawljob"; filename*=UTF-8''<name>`, and `X-CrawlJob-ID`, `X-CrawlJob-Package`, `X-CrawlJob-Links` headers.
- `/download/{job_id}/info` returns `job_id`, `package_name`, `created_at`, `expires_at`, `is_expired`, `validated_urls`, `source_url`, `comment`, `auto_start`, `priority` as JSON.

### Stremio

- `StremioStreamUseCase` gets infrastructure behaviour as injected callables (`convert_fn`, `filter_fn`, `episode_filter_fn`, `resolve_fn`, `cached_resolution_fn`, `browser_warmup_fn`) and protocols, so `application/` has no infrastructure imports.
- Each plugin search acquires one `ConcurrencyPool.request()` budget (requests for one title share the running search, a cached answer starts none); `PluginSearchRunner` takes httpx or Playwright slots per plugin (by `get_mode()`), ends the search `plugin_timeout_seconds` after the request start (a stale entry's background refresh: after its own start; a shared deadline: queued plugins are skipped, running ones cut; a timeout counts as breaker failure only when the plugin had at least half the budget) and skips plugins whose `PluginCircuitBreaker` is open (cooldown doubles per failed half-open trial, max 1 h).
- Resolution starts while the search runs: the request reads the search's `SearchProgress` (title-matching results as they arrive, shared by the requests on one search) and ranks each batch with the ones before; `HosterResolution` resolves each hoster's best link in rank order (the next only after the better one failed, a better one that arrives later too, each URL once). The answer goes out at `resolve_target_count` hosters with a video, when the search and every resolution are done, or at `stream_deadline_seconds`; a cached search answers at once with the resolver's cached results and resolves the rest in the background.
- Result conversion (`convert_fn`) runs in a worker thread (`asyncio.to_thread`) to keep the event loop free.
- `/stremio/play/{stream_id}` asks `StremioLinks` for the stored link (404 when missing): its video URL while under 1 h old, else a new resolution (one per link for all requests; per player for hosters whose CDN binds the URL to the player's headers, VEEV; the stale URL stays the fallback when the hoster gives no video). It returns a 302 redirect, or 502 without a video URL (never a redirect to an embed page); HEAD answers like GET.

### Configuration Load Flow

- Precedence: defaults < YAML < ENV < CLI. `load_config(*, config_path, dotenv_path, cli_overrides)` has no filesystem side effects.
- Flow: load `.env` (if given, `override=False`) → `DEFAULT_CONFIG` normalized to sectioned shape → deep-merge YAML → deep-merge `EnvOverrides().to_update_dict()` → deep-merge CLI overrides → `AppConfig.model_validate()`.
- `_normalize_layer()` maps flat keys (e.g. `plugin_dir`) to sections (e.g. `plugins.plugin_dir`); `_deep_merge()` lets the override win.
- At startup the composition root auto-tunes concurrency from detected container/host resources (`_auto_tune()` when `stremio.auto_tune_all`, else `_auto_tune_concurrency()`).

### Async Logging

- `configure_logging(config)` configures structlog processors, applies a uvicorn-compatible `dictConfig`, and routes all stdlib logging through a `QueueHandler` (`_StructlogPreservingQueueHandler`, hands over a shallow copy so structlog's dict messages survive) to a background `QueueListener`. Stdlib records (uvicorn, asyncio, libraries) run through `_foreign_pre_chain()`, which renders tracebacks to text (`exception` field, also in JSON).
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
- Outbound HTTP goes through `RetryTransport` + `DomainRateLimiter` on the shared client (`build_http_client()`); inbound API calls are limited by `RateLimitMiddleware` when `api_rate_limit_rpm > 0`.
- The shared client refuses every request and redirect hop to a non-public address (`PrivateAddressGuard`, a `request` event hook): scraped pages decide most outbound URLs (download links, embeds, CDNs, their redirects), and a hostile page must not reach the LAN, cloud metadata or Scavengarr itself (blind SSRF). IP literals are checked directly, hostnames after a cached DNS lookup (60 s); only the solver sidecar (`playwright.solver_url`) is allowed. Connections go to the addresses of that lookup (`GuardedNetworkBackend` in the client's `GuardedTransport`, IPv4 first, the next address when one refuses): a second lookup at connect time could answer with a LAN address (DNS rebinding). TLS still verifies the hostname, which httpcore sends as SNI. The connections themselves are asyncio streams (`AsyncioNetworkBackend`), whose TLS runs in the event loop (uvloop) instead of in Python (anyio, httpx's default): the HLS proxy needs about a quarter less CPU on the Pi. Browser (Playwright) navigation does not go through this client.

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
                                     → SearchCache (per title; a miss or a stale entry starts one shared search, SearchProgress)
                                     → ConcurrencyPool.request() → RequestBudget
                                     → PluginSearchRunner → PluginCircuitBreaker
                                                          → plugin.search() → filter_by_episode → SearchEnginePort.validate_results()
                                     → filter_by_title_match
                                     → convert_search_results → StreamSorter → HosterResolution (per hoster in rank order, deadline);
                                       without a resolver: per-hoster dedup
                                     → StreamLinkRepository (→ CacheStreamLinkRepository → CachePort)
StremioRouter (/play) → StremioLinks → StreamLinkRepository + HosterResolverRegistry → 302
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
