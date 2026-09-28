[← Back to Index](./README.md)

# Scavengarr Feature Handbook

> Compact reference of all features, their status, and where to find details.

---

## Feature Overview

Current plugin, resolver, and test counts are listed in the [repository README](../../README.md) and the [feature index](./README.md).

| Feature | Status | Details |
|---|---|---|
| Python Plugin System | [x] Implemented | All plugins are Python-based (httpx or Playwright) |
| Multi-Stage Scraping | [x] Implemented | Plugins run search → detail → links internally with bounded concurrency |
| CrawlJob System | [x] Implemented | Multi-link `.crawljob` packaging for JDownloader |
| Torznab/Newznab API | [x] Implemented | `caps`, `search` endpoints compatible with Prowlarr |
| Link Validation | [x] Implemented | Parallel HEAD/GET validation |
| Mirror URL Fallback | [x] Implemented | Automatic domain failover across mirror lists |
| Prowlarr Integration | [x] Implemented | Torznab indexer compatible with Prowlarr discovery |
| Configuration System | [x] Implemented | YAML/ENV/CLI with typed settings and precedence |
| Structured Logging | [x] Implemented | JSON/console output via structlog with context fields |
| Stremio Addon | [x] Implemented | Manifest, catalog, stream resolution with TMDB metadata |
| Hoster Resolver System | [x] Implemented | Individual, generic DDL, and XFS consolidated resolvers |
| Plugin Base Classes | [x] Implemented | `HttpxPluginBase` / `PlaywrightPluginBase` shared base classes |
| Per-Plugin Overrides | [x] Implemented | YAML `plugins.overrides`: timeout, max concurrency, max results, enable/disable |
| Search Result Caching | [x] Implemented | `cache.search_ttl_seconds` (default 900s) with `X-Cache: HIT/MISS` header |
| Plugin Scoring & Probing | [x] Implemented | EWMA-based background scoring with health + search probes |
| Circuit Breaker | [x] Implemented | Per-plugin failure tracking, auto-skip after 5 consecutive failures |
| Global Concurrency Pool | [x] Implemented | Fair-share httpx/Playwright slot budgets across requests |
| Container-Aware Auto-Tune | [x] Implemented | Concurrency limits derived from cgroup v2/v1 CPU/memory at startup |
| Shared Browser Pool | [x] Implemented | One Chromium process shared by all Playwright plugins |
| Stealth Pool | [x] Implemented | Own Patchright context on the shared Chromium for Cloudflare-protected hosters and probes |
| Multi-Language Search | [x] Implemented | Per-language TMDB title resolution, plugins declare `languages` |
| Stream Deduplication | [x] Implemented | Per-hoster dedup keeps best-ranked stream only |
| Graceful Shutdown | [x] Implemented | Drain in-flight requests before stopping |
| Health & Metrics | [x] Implemented | `/api/v1/healthz`, `/api/v1/readyz`, `/api/v1/stats/metrics` |
| HTTP Rate Limiting | [x] Implemented | Adaptive per-domain token bucket + 429/503 retry with backoff |
| API Rate Limiting | [x] Implemented | Per-IP request limit on the API (`http.api_rate_limit_rpm`) |
| Test Suite | [x] Implemented | Unit, integration, E2E, and opt-in live smoke tests |

---

## Plugin System

Scavengarr is plugin-driven. Each plugin defines how to scrape a specific source site. All plugins are Python-based, inheriting from `HttpxPluginBase` (for static HTML) or `PlaywrightPluginBase` (for JS-heavy sites), and implement the `PluginProtocol` (`name` + `async search()`).

| Capability | Status | Notes |
|---|---|---|
| `PluginProtocol` compliance | [x] Implemented | `name` + `async search()` contract |
| Playwright integration | [x] Implemented | Full browser automation (Chromium) |
| Domain fallback | [x] Implemented | Try multiple mirrors sequentially |
| Form-based auth (vBulletin) | [x] Implemented | MD5 password hashing, session cookies (e.g. `boerse`, `mygully`) |
| Cloudflare bypass | [x] Implemented | JS challenge wait via Playwright |
| Bounded concurrency | [x] Implemented | Semaphore-limited parallel detail-page scraping |
| Custom HTML parsing | [x] Implemented | `HTMLParser` subclasses for extraction |
| Environment variable credentials | [x] Implemented | `SCAVENGARR_<PLUGIN>_USERNAME` / `_PASSWORD` (see [Configuration](./configuration.md#plugin-credentials)) |
| `HttpxPluginBase` | [x] Implemented | Shared base for httpx plugins (client, domain fallback, semaphore) |
| `PlaywrightPluginBase` | [x] Implemented | Shared base for Playwright plugins (browser lifecycle, Cloudflare) |
| Season/episode support | [x] Implemented | All plugins accept `season`/`episode` params for TV content |
| Per-plugin overrides | [x] Implemented | See [Plugin System](./plugin-system.md#per-plugin-overrides) |

**Detailed docs:** [Plugin System](./plugin-system.md), [Python Plugins](./python-plugins.md)

---

## Stremio Addon

Scavengarr includes a full Stremio addon that provides catalog browsing, search, and stream resolution with automatic hoster video URL extraction. All routes live under `/api/v1/stremio/`.

| Feature | Status | Details |
|---|---|---|
| Addon manifest | [x] Implemented | `/api/v1/stremio/manifest.json` with movie + series types |
| TMDB catalog (trending) | [x] Implemented | Trending movies and series via TMDB API |
| Catalog search | [x] Implemented | TMDB-based search with German locale |
| Stream resolution | [x] Implemented | IMDb ID → plugin search → ranked streams |
| Title matching | [x] Implemented | rapidfuzz-based scoring with multi-candidate support |
| `/play/` endpoint | [x] Implemented | 302 redirect to resolved video URL |
| Stream link caching | [x] Implemented | Cached hoster URLs with TTL |
| IMDB fallback | [x] Implemented | Title lookup without TMDB API key via IMDB Suggest API (+ Wikidata for German titles) |
| Per-plugin timeout | [x] Implemented | Slow plugins don't block the response |
| behaviorHints.proxyHeaders | [x] Implemented | Pre-resolve hoster URLs, emit Referer/User-Agent for CDN playback |
| HLS proxy endpoint | [x] Implemented | `/api/v1/stremio/proxy/{stream_id}/{path}` for HLS streams requiring Referer on all sub-requests |
| Health endpoint | [x] Implemented | `/api/v1/stremio/health` reports component status |
| Circuit breaker integration | [x] Implemented | Skip consistently failing plugins |
| Concurrency pool integration | [x] Implemented | Fair-share httpx/PW slots across concurrent requests |
| Multi-language search | [x] Implemented | Per-language TMDB titles, plugins declare `languages` |
| Stream deduplication | [x] Implemented | Per-hoster dedup keeps best-ranked stream |
| Scored plugin selection | [x] Implemented | Optional top-N plugin selection by score (`stremio.scoring_enabled`) |
| Early-stop resolve | [x] Implemented | Stop resolving after `resolve_target_count` (default 15) successes |

**Detailed docs:** [Stremio Addon](./stremio-addon.md)

---

## Hoster Resolver System

Validates file availability and extracts direct video URLs from streaming hosters. Resolvers fall into individual resolvers, one parameterised generic DDL resolver, and one parameterised XFS resolver.

### Streaming resolvers (extract direct `.mp4`/`.m3u8` URLs)

| Resolver | Status | Technique |
|---|---|---|
| VOE | [x] Implemented | Multi-method extraction (JSON, obfuscated JS) |
| Streamtape | [x] Implemented | Token extraction from page source |
| SuperVideo | [x] Implemented | XFS extraction + StealthPool Cloudflare fallback |
| DoodStream | [x] Implemented | `pass_md5` API extraction |
| Filemoon | [x] Implemented | Packed JS unpacker + Byse SPA challenge flow |
| StreamUp (strmup) | [x] Implemented | HLS extraction with page + AJAX fallback |
| Vidsonic | [x] Implemented | HLS extraction with hex-obfuscated URL decoding |

### Validate-only streaming hosters (no video extraction)

These resolvers confirm the embed URL is alive and return it unchanged.

| Resolver | Status | Technique |
|---|---|---|
| VidGuard | [x] Implemented | Multi-domain embed page validation |
| Vidking | [x] Implemented | Embed page validation |
| Stmix | [x] Implemented | Embed page validation |
| SerienStream | [x] Implemented | s.to / serien.sx domain matching |
| SendVid | [x] Implemented | Availability check |

### DDL resolvers (validate only)

| Resolver | Status | Technique |
|---|---|---|
| Filer.net | [x] Implemented | Public status API |
| Rapidgator | [x] Implemented | Website scraping |
| DDownload | [x] Implemented | XFS page check with canonical URL normalization |
| Mediafire | [x] Implemented | Public file info API, offline via error 110 |
| GoFile | [x] Implemented | Ephemeral guest token, content availability API |
| Generic DDL | [x] Implemented | Alfafile, AlphaDDL, Fastpic, Filecrypt, FileFactory, FSST, Go4up, Mixdrop, Nitroflare, 1fichier, Turbobit, Uploaded |

### XFS consolidated resolvers (generic `XFSResolver`)

| Category | Hosters |
|---|---|
| DDL (validate only) | Katfile, Hexupload, Clicknupload, Filestore, Uptobox, Hotlink |
| Video (extract URL) | Funxd, Bigwarp, Dropload, Goodstream, Savefiles, Streamwish, Vidmoly, Vidoza, Vidhide, Mp4Upload, Uqload, Vidshar, Vidroba, Vidspeed, StreamRuby, Lulustream, Upstream, Vidnest |
| Captcha-required (return `None`) | Veev, Vinovo, Wolfstream |

### System features

| Feature | Status | Details |
|---|---|---|
| Content-type probing | [x] Implemented | Fallback: probe URL for direct video links |
| URL domain priority | [x] Implemented | Match resolver by domain with redirect following |
| Hoster hint fallback | [x] Implemented | Plugin-provided hoster name for rotating domains |
| XFS consolidation | [x] Implemented | XFS hosters share one parameterised resolver |
| Generic DDL consolidation | [x] Implemented | DDL hosters share one parameterised resolver |
| Video extraction utilities | [x] Implemented | JWPlayer, packed JS, HLS `hls2` pattern extraction |
| XFS video URL verification | [x] Implemented | HEAD check filters IP-locked CDN tokens (e.g. LULUVID) |
| Domain alias mapping | [x] Implemented | All `supported_domains` mapped (e.g., vidhide family) |
| respx-based tests | [x] Implemented | All resolver tests use httpx-native HTTP mocking |
| Live contract tests | [x] Implemented | Opt-in resolver live/dead URL validation (`tests/live/test_resolver_live.py`) |

**Detailed docs:** [Hoster Resolvers](./hoster-resolvers.md)

---

## Multi-Stage Scraping

Plugins implement the search → detail → links flow internally; there is no separate stage engine.

| Feature | Status | Details |
|---|---|---|
| Search → detail → links | [x] Implemented | Implemented inside each plugin's `search()` |
| Parallel detail pages | [x] Implemented | Bounded concurrency via `_new_semaphore()` |
| Pagination | [x] Implemented | Plugins page through search results up to their `_max_results` |
| Rate limiting | [x] Implemented | Per-domain rate limiting in the shared HTTP client |

**Detailed docs:** [Multi-Stage Scraping](./multi-stage-scraping.md)

---

## CrawlJob System

CrawlJobs bundle multiple validated download links into `.crawljob` files for JDownloader integration.

| Feature | Status | Details |
|---|---|---|
| SearchResult → CrawlJob conversion | [x] Implemented | Via `CrawlJobFactory` |
| Multi-link packaging | [x] Implemented | All validated URLs in one `.crawljob` |
| Unique job IDs | [x] Implemented | Random UUID4 per job |
| Configurable TTL | [x] Implemented | Time-to-live for cached jobs |
| Download endpoint | [x] Implemented | `/api/v1/download/{job_id}` serves the `.crawljob` file; `/api/v1/download/{job_id}/info` returns metadata |
| Cache-backed storage | [x] Implemented | JSON-serialized via the cache port (diskcache or Redis) |
| Validate-first policy | [x] Implemented | Only validated links enter CrawlJobs |

**Detailed docs:** [CrawlJob System](./crawljob-system.md)

---

## Torznab/Newznab API

Scavengarr exposes a Torznab-compatible API that integrates with Prowlarr, Sonarr, Radarr, and other Arr applications.

| Feature | Status | Details |
|---|---|---|
| `t=caps` endpoint | [x] Implemented | Returns XML capabilities document |
| `t=search` endpoint | [x] Implemented | Full-text search with category filtering |
| Pagination (offset/limit) | [x] Implemented | Server-side slicing via `offset` and `limit` query params |
| Torznab XML rendering | [x] Implemented | RSS 2.0 with `torznab:attr` extensions |
| Per-plugin indexers | [x] Implemented | `/api/v1/torznab/{plugin_name}` per plugin |
| Indexer listing | [x] Implemented | `/api/v1/torznab/indexers` lists all available plugins |
| Plugin health | [x] Implemented | `/api/v1/torznab/{plugin_name}/health` checks the plugin's base URL |
| Category mapping | [x] Implemented | Torznab standard category IDs |
| Error responses | [x] Implemented | Torznab RSS error responses; in `prod` upstream/internal errors return empty RSS with HTTP 200 |

**Detailed docs:** [Torznab API](./torznab-api.md)

---

## Link Validation

Links are validated in parallel before inclusion in search results and CrawlJobs.

| Feature | Status | Details |
|---|---|---|
| HEAD request primary | [x] Implemented | Fast validation without downloading |
| GET fallback | [x] Implemented | On any HEAD failure (some hosters block HEAD) |
| Parallel execution | [x] Implemented | Semaphore-bounded concurrent checks (`validation_max_concurrent`) |
| Status-based decisions | [x] Implemented | 2xx/3xx valid; ≥400 or network error invalid |
| Redirect following | [x] Implemented | Redirects are always followed |
| Configurable timeouts | [x] Implemented | `validation_timeout_seconds` per request |

**Detailed docs:** [Link Validation](./link-validation.md)

---

## Mirror URL Fallback

Plugins can define multiple domains. If the primary domain is unreachable, the plugin falls back to its mirrors.

| Feature | Status | Details |
|---|---|---|
| Multiple domain entries | [x] Implemented | Plugin `_domains` list for mirror fallback |
| Sequential fallback | [x] Implemented | Try mirrors in order until one works |

**Detailed docs:** [Mirror URL Fallback](./mirror-url-fallback.md)

---

## Configuration System

Configuration follows a strict precedence hierarchy with typed validation.

| Feature | Status | Details |
|---|---|---|
| YAML config file | [x] Implemented | `--config` or `SCAVENGARR_CONFIG` |
| Environment variables (`SCAVENGARR_*`) | [x] Implemented | Override YAML settings |
| CLI arguments | [x] Implemented | Highest precedence |
| `.env` file support | [x] Implemented | `--dotenv`; values act as environment variables and never override the real environment |
| Pydantic-settings validation | [x] Implemented | Typed, validated settings |
| Per-plugin overrides | [x] Implemented | `plugins.overrides.<name>`: `timeout`, `max_concurrent`, `max_results`, `enabled` |

**Detailed docs:** [Configuration](./configuration.md)

---

## Observability

| Feature | Status | Details |
|---|---|---|
| Structured logging (structlog) | [x] Implemented | JSON and console formatters |
| Context fields | [x] Implemented | e.g. `plugin`, `duration_ms`, `results_count` |
| Health endpoints | [x] Implemented | `/api/v1/healthz` (liveness), `/api/v1/readyz` (readiness) |
| Metrics endpoint | [x] Implemented | `/api/v1/stats/metrics` — plugin stats, circuit breaker, pool utilisation |
| Plugin score endpoint | [x] Implemented | `/api/v1/stats/plugin-scores` — EWMA scores, filterable by `plugin`, `category`, `bucket` |

---

## Architecture Summary

Scavengarr follows **Clean Architecture** with four layers:

```text
Interfaces  -->  Application  -->  Domain
     |                |
     v                v
Infrastructure (implements Domain ports)
```

| Layer | Responsibility | Key Modules |
|---|---|---|
| **Domain** | Entities, value objects, protocols (ports) | `SearchResult`, `PluginProtocol`, `TorznabQuery` |
| **Application** | Use cases, factories, policies | `TorznabSearchUseCase`, `StremioStreamUseCase`, `CrawlJobFactory` |
| **Infrastructure** | Adapters, engine, validation, cache | `PluginRegistry`, `HttpxSearchEngine`, `HttpLinkValidator` |
| **Interfaces** | HTTP routers, CLI, composition root | FastAPI routers, argparse CLI, `composition.py` |

**Dependency rule:** Inner layers never import outer layers. Domain is framework-free and I/O-free.

**Detailed docs:** [Clean Architecture](../architecture/clean-architecture.md)

---

## Source Code References

| Component | Path |
|---|---|
| Domain entities | `src/scavengarr/domain/entities/` |
| Plugin domain models | `src/scavengarr/domain/plugins/` |
| Domain ports | `src/scavengarr/domain/ports/` |
| Use cases | `src/scavengarr/application/use_cases/` |
| Stremio application services | `src/scavengarr/application/stremio/` |
| CrawlJob factory | `src/scavengarr/application/factories/` |
| Plugin registry | `src/scavengarr/infrastructure/plugins/registry.py` |
| Plugin loader | `src/scavengarr/infrastructure/plugins/loader.py` |
| HttpxPluginBase | `src/scavengarr/infrastructure/plugins/httpx_base.py` |
| PlaywrightPluginBase | `src/scavengarr/infrastructure/plugins/playwright_base.py` |
| Shared browser pool | `src/scavengarr/infrastructure/browser/shared_browser.py` |
| Search engine | `src/scavengarr/infrastructure/torznab/search_engine.py` |
| Torznab presenter | `src/scavengarr/infrastructure/torznab/presenter.py` |
| Link validator | `src/scavengarr/infrastructure/validation/http_link_validator.py` |
| Stremio infrastructure | `src/scavengarr/infrastructure/stremio/` |
| TMDB client + IMDB fallback | `src/scavengarr/infrastructure/tmdb/` |
| Hoster resolvers | `src/scavengarr/infrastructure/hoster_resolvers/` |
| Stealth pool | `src/scavengarr/infrastructure/browser/stealth_pool.py` |
| Circuit breaker | `src/scavengarr/infrastructure/circuit_breaker.py` |
| Concurrency pool | `src/scavengarr/infrastructure/concurrency.py` |
| Resource detector | `src/scavengarr/infrastructure/resource_detector.py` |
| Graceful shutdown | `src/scavengarr/infrastructure/graceful_shutdown.py` |
| Metrics collector | `src/scavengarr/infrastructure/metrics.py` |
| Plugin scoring | `src/scavengarr/infrastructure/scoring/` |
| Torznab router | `src/scavengarr/interfaces/api/torznab/` |
| Stremio router | `src/scavengarr/interfaces/api/stremio/` |
| Download router | `src/scavengarr/interfaces/api/download/` |
| Stats router | `src/scavengarr/interfaces/api/stats/` |
| API rate-limit middleware | `src/scavengarr/interfaces/api/middleware.py` |
| Composition root | `src/scavengarr/interfaces/composition.py` |
| CLI | `src/scavengarr/interfaces/cli/` |
| Python plugin example (httpx) | `plugins/filmpalast_to.py` |
| Python plugin example (Playwright) | `plugins/boerse.py` |
| Test suite | `tests/` |
