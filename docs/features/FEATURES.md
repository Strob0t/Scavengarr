[← Back to Index](./README.md)

# Scavengarr Feature Handbook

> Compact reference of all features, their status, and where to find details.

---

## Feature Overview

| Feature | Status | Details |
|---|---|---|
| YAML Plugin System | [x] Implemented | Declarative plugins with CSS selectors and multi-stage pipelines |
| Python Plugin System | [x] Implemented | Imperative plugins for complex auth, APIs, JS-heavy sites |
| Multi-Stage Scraping | [x] Implemented | Search -> Detail -> Links pipeline with parallel execution |
| CrawlJob System | [x] Implemented | Multi-link `.crawljob` packaging for JDownloader |
| Torznab/Newznab API | [x] Implemented | `caps`, `search` endpoints compatible with Prowlarr |
| Link Validation | [x] Implemented | Parallel HEAD/GET validation with configurable policies |
| Mirror URL Fallback | [x] Implemented | Automatic domain failover across mirror lists |
| Prowlarr Integration | [x] Implemented | Torznab indexer compatible with Prowlarr discovery |
| Configuration System | [x] Implemented | YAML/ENV/CLI with typed settings and precedence |
| Structured Logging | [x] Implemented | JSON/console output via structlog with context fields |
| Stremio Addon | [x] Implemented | Manifest, catalog, stream resolution with TMDB metadata |
| Hoster Resolver System | [x] Implemented | 39 hoster resolvers (streaming + DDL + 15 XFS consolidated) |
| Plugin Base Classes | [x] Implemented | `HttpxPluginBase` / `PlaywrightPluginBase` shared base classes |
| Scrapy Engine | [x] Implemented | Static HTML scraping backend for YAML plugins |
| 40 Plugins | [x] Implemented | 37 Python + 3 YAML plugins for German streaming/DDL sites |
| Playwright Engine | [ ] Planned | Native Playwright scraping backend for YAML plugins |
| Search Result Caching | [x] Implemented | 900s TTL with X-Cache HIT/MISS header |
| Integration Test Suite | [x] Implemented | 31 integration + 109 E2E + 38 live smoke tests |

---

## Plugin System

Scavengarr is plugin-driven. Each plugin defines how to scrape a specific source site. Two plugin types are supported.

### YAML Plugins (Declarative)

YAML plugins define scraping rules using CSS selectors and URL templates. They are processed by the Scrapy engine through a multi-stage pipeline.

| Capability | Status | Notes |
|---|---|---|
| Multi-stage pipeline (list -> detail) | [x] Implemented | Arbitrary stage depth with `next_stage` chaining |
| CSS selector extraction | [x] Implemented | Text content and attribute extraction |
| Nested download link extraction | [x] Implemented | Container/group/item hierarchy |
| Field attribute fallbacks | [x] Implemented | Ordered list of attributes to try |
| URL pattern templating | [x] Implemented | `{query}`, `{movie_id}` substitution |
| Pagination | [x] Implemented | Configurable max pages per list stage |
| Mirror URL support | [x] Implemented | `base_url` accepts single URL or list |
| Per-plugin HTTP overrides | [x] Implemented | Timeout, redirects, user agent |
| Auth: none | [x] Implemented | Default for public sites |
| Auth: basic | [x] Implemented | HTTP Basic Authentication |
| Auth: form | [x] Implemented | Form-based login with selectors |
| Auth: cookie | [x] Implemented | Cookie-based session management |
| Pydantic validation | [x] Implemented | Schema validation on load with clear errors |

**Detailed docs:** [Plugin System (YAML)](./plugin-system.md)

### Python Plugins (Imperative)

Python plugins implement the `PluginProtocol` directly. They handle their own scraping logic, making them suitable for sites requiring complex authentication, Cloudflare bypass, or non-standard page structures.

| Capability | Status | Notes |
|---|---|---|
| `PluginProtocol` compliance | [x] Implemented | `name` + `async search()` contract |
| Playwright integration | [x] Implemented | Full browser automation (Chromium) |
| Domain fallback | [x] Implemented | Try multiple mirrors sequentially |
| Form-based auth (vBulletin) | [x] Implemented | MD5 password hashing, session cookies |
| Cloudflare bypass | [x] Implemented | JS challenge wait via Playwright |
| Bounded concurrency | [x] Implemented | Semaphore-limited parallel page scraping |
| Custom HTML parsing | [x] Implemented | `HTMLParser` subclasses for extraction |
| Environment variable credentials | [x] Implemented | `SCAVENGARR_*` env vars for secrets |
| `HttpxPluginBase` | [x] Implemented | Shared base for 28 httpx plugins (client, domain fallback, semaphore) |
| `PlaywrightPluginBase` | [x] Implemented | Shared base for 9 Playwright plugins (browser lifecycle, Cloudflare) |
| Season/episode support | [x] Implemented | All plugins accept `season`/`episode` params for TV content |
| Settings organization | [x] Implemented | Configurable settings at top of each plugin file |

**Detailed docs:** [Python Plugins](./python-plugins.md)

---

## Stremio Addon

Scavengarr includes a full Stremio addon that provides catalog browsing, search, and stream resolution with automatic hoster video URL extraction.

| Feature | Status | Details |
|---|---|---|
| Addon manifest | [x] Implemented | Standard Stremio manifest with movie + series types |
| TMDB catalog (trending) | [x] Implemented | Trending movies and series via TMDB API |
| Catalog search | [x] Implemented | TMDB-based search with German locale |
| Stream resolution | [x] Implemented | IMDb ID → plugin search → ranked streams |
| Title matching | [x] Implemented | guessit-based scoring with multi-candidate support |
| `/play/` endpoint | [x] Implemented | 302 redirect to resolved video URL |
| Stream link caching | [x] Implemented | Cached hoster URLs with TTL |
| IMDB fallback | [x] Implemented | Title lookup without TMDB API key via Wikidata |
| Per-plugin timeout | [x] Implemented | Slow plugins don't block the response |

---

## Hoster Resolver System

Validates file availability and extracts direct video URLs from streaming hosters. 39 resolvers across three categories.

### Streaming resolvers (extract direct `.mp4`/`.m3u8` URLs)

| Resolver | Status | Technique |
|---|---|---|
| VOE | [x] Implemented | Multi-method extraction (JSON, obfuscated JS) |
| Streamtape | [x] Implemented | Token extraction from page source |
| SuperVideo | [x] Implemented | XFS extraction + Playwright Cloudflare fallback |
| DoodStream | [x] Implemented | `pass_md5` API extraction |
| Filemoon | [x] Implemented | Packed JS unpacker + Byse SPA challenge flow |
| Mixdrop | [x] Implemented | Token extraction, multi-domain |
| VidGuard | [x] Implemented | Multi-domain embed resolution |
| Vidking | [x] Implemented | Embed page validation |
| Stmix | [x] Implemented | Embed page validation |
| SerienStream | [x] Implemented | s.to / serien.sx domain matching |

### DDL resolvers (validate file availability)

| Resolver | Status | Technique |
|---|---|---|
| Filer.net | [x] Implemented | Public status API |
| Rapidgator | [x] Implemented | Website scraping |
| DDownload | [x] Implemented | XFS page check with canonical URL normalization |
| Alfafile | [x] Implemented | Page scraping |
| AlphaDDL | [x] Implemented | Page scraping |
| Fastpic | [x] Implemented | Image host validation |
| Filecrypt | [x] Implemented | Container validation |
| FileFactory | [x] Implemented | Page scraping |
| FSST | [x] Implemented | Page scraping |
| Go4up | [x] Implemented | Mirror link validation |
| Nitroflare | [x] Implemented | Page scraping |
| 1fichier | [x] Implemented | Page scraping, multi-domain |
| Turbobit | [x] Implemented | Page scraping, multi-domain |
| Uploaded | [x] Implemented | Page scraping, multi-domain |

### XFS consolidated resolvers (15 hosters via generic XFSResolver)

| Resolver | Status | Details |
|---|---|---|
| Katfile | [x] Implemented | DDL XFS offline marker detection |
| Hexupload | [x] Implemented | Standard XFS markers |
| Clicknupload | [x] Implemented | Multi-domain (clicknupload, clickndownload) |
| Filestore | [x] Implemented | Standard XFS markers |
| Uptobox | [x] Implemented | Multi-domain (uptobox, uptostream) |
| Funxd | [x] Implemented | `/e/`/`/d/` path prefix |
| Bigwarp | [x] Implemented | Extended markers |
| Dropload | [x] Implemented | Extended markers |
| Goodstream | [x] Implemented | Extended markers |
| Savefiles | [x] Implemented | Extended markers |
| Streamwish | [x] Implemented | 9 domains, extended+custom markers |
| Vidmoly | [x] Implemented | `/w/` path prefix support |
| Vidoza | [x] Implemented | Multi-domain (vidoza, videzz) |
| Vinovo | [x] Implemented | 12+ char file IDs |
| Vidhide | [x] Implemented | 6 domains, lowercase-only IDs |

### System features

| Feature | Status | Details |
|---|---|---|
| Content-type probing | [x] Implemented | Fallback: probe URL for direct video links |
| URL domain priority | [x] Implemented | Match resolver by domain with redirect following |
| Hoster hint fallback | [x] Implemented | Plugin-provided hoster name for rotating domains |
| XFS consolidation | [x] Implemented | 15 XFS hosters share one parameterised resolver |
| respx-based tests | [x] Implemented | All resolver tests use httpx-native HTTP mocking |
| Live contract tests | [x] Implemented | Skeleton for resolver live/dead URL validation |

---

## Multi-Stage Scraping

The scraping pipeline supports multiple stages that cascade from search results to detail pages to download links.

| Feature | Status | Details |
|---|---|---|
| List stages (intermediate) | [x] Implemented | Extract URLs for the next stage |
| Detail stages (terminal) | [x] Implemented | Extract SearchResult data |
| Parallel URL processing | [x] Implemented | Bounded concurrency within each stage |
| Stage chaining via `next_stage` | [x] Implemented | Declarative stage flow |
| Depth limiting (`max_depth`) | [x] Implemented | Prevent runaway recursion |
| Rate limiting (`delay_seconds`) | [x] Implemented | Per-plugin request throttling |
| Result deduplication | [x] Implemented | By `(title, download_link)` tuple |

**Detailed docs:** [Multi-Stage Scraping](./multi-stage-scraping.md)

---

## CrawlJob System

CrawlJobs bundle multiple validated download links into `.crawljob` files for JDownloader integration.

| Feature | Status | Details |
|---|---|---|
| SearchResult -> CrawlJob conversion | [x] Implemented | Via `CrawlJobFactory` |
| Multi-link packaging | [x] Implemented | All validated URLs in one `.crawljob` |
| Stable job IDs (UUID) | [x] Implemented | Deterministic, cacheable |
| Configurable TTL | [x] Implemented | Time-to-live for cached jobs |
| Download endpoint | [x] Implemented | Serves `.crawljob` file by job ID |
| Cache-backed storage | [x] Implemented | diskcache with pickle serialization |
| Validate-first policy | [x] Implemented | Only validated links enter CrawlJobs |

**Detailed docs:** [CrawlJob System](./crawljob-system.md)

---

## Torznab/Newznab API

Scavengarr exposes a Torznab-compatible API that integrates with Prowlarr, Sonarr, Radarr, and other Arr applications.

| Feature | Status | Details |
|---|---|---|
| `t=caps` endpoint | [x] Implemented | Returns XML capabilities document |
| `t=search` endpoint | [x] Implemented | Full-text search with category filtering |
| Torznab XML rendering | [x] Implemented | RSS 2.0 with `torznab:attr` extensions |
| Per-plugin indexers | [x] Implemented | Each plugin gets its own Torznab endpoint |
| Indexer listing | [x] Implemented | Discovery endpoint for all available plugins |
| Category mapping | [x] Implemented | Torznab standard category IDs |
| Error responses | [x] Implemented | Proper Torznab error XML format |

**Detailed docs:** [Torznab API](./torznab-api.md)

---

## Link Validation

Links are validated in parallel before inclusion in search results and CrawlJobs.

| Feature | Status | Details |
|---|---|---|
| HEAD request primary | [x] Implemented | Fast validation without downloading |
| GET fallback | [x] Implemented | For hosters that block HEAD requests |
| Parallel execution | [x] Implemented | Semaphore-bounded concurrent checks |
| Status-based decisions | [x] Implemented | 200 ok, 403/404/timeout invalid |
| Redirect following | [x] Implemented | Configurable redirect policy |
| Configurable timeouts | [x] Implemented | Per-validation-request timeout |

**Detailed docs:** [Link Validation](./link-validation.md)

---

## Mirror URL Fallback

Plugins can define multiple base URLs. If the primary domain is unreachable, the system automatically falls back to mirrors.

| Feature | Status | Details |
|---|---|---|
| Multiple `base_url` entries | [x] Implemented | YAML `base_url` accepts a list |
| Sequential fallback | [x] Implemented | Try mirrors in order until one works |
| Python plugin domain lists | [x] Implemented | Custom fallback logic in Python plugins |

**Detailed docs:** [Mirror URL Fallback](./mirror-url-fallback.md)

---

## Configuration System

Configuration follows a strict precedence hierarchy with typed validation.

| Feature | Status | Details |
|---|---|---|
| YAML config file | [x] Implemented | Primary configuration source |
| Environment variables (`SCAVENGARR_*`) | [x] Implemented | Override YAML settings |
| CLI arguments | [x] Implemented | Highest precedence |
| `.env` file support | [x] Implemented | Optional dotenv loading |
| Pydantic-settings validation | [x] Implemented | Typed, validated settings |
| Per-plugin HTTP overrides | [x] Implemented | Timeout, user agent, redirects |

**Detailed docs:** [Configuration](./configuration.md)

---

## Observability

| Feature | Status | Details |
|---|---|---|
| Structured logging (structlog) | [x] Implemented | JSON and console formatters |
| Context fields | [x] Implemented | `plugin`, `stage`, `duration_ms`, `results_count` |
| Secret masking | [x] Implemented | Credentials never appear in logs |
| Correlation IDs | [x] Implemented | Request-scoped tracing |
| Health endpoints | [x] Implemented | Per-plugin health checks |

---

## Architecture Summary

Scavengarr follows **Clean Architecture** with four layers:

```
Interfaces  -->  Application  -->  Domain
     |                |
     v                v
Infrastructure (implements Domain ports)
```

| Layer | Responsibility | Key Modules |
|---|---|---|
| **Domain** | Entities, value objects, protocols (ports) | `SearchResult`, `PluginProtocol`, `TorznabQuery` |
| **Application** | Use cases, factories, policies | `TorznabSearchUseCase`, `CrawlJobFactory` |
| **Infrastructure** | Adapters, engine, validation, cache | `PluginRegistry`, `SearchEngine`, `LinkValidator` |
| **Interfaces** | HTTP router, CLI, composition root | FastAPI router, Typer CLI |

**Dependency rule:** Inner layers never import outer layers. Domain is framework-free and I/O-free.

**Detailed docs:** [Clean Architecture](../architecture/clean-architecture.md)

---

## Source Code References

| Component | Path |
|---|---|
| Domain entities | `src/scavengarr/domain/entities.py` |
| Plugin domain models | `src/scavengarr/domain/plugins/` |
| Domain ports | `src/scavengarr/domain/ports/` |
| Use cases | `src/scavengarr/application/use_cases/` |
| CrawlJob factory | `src/scavengarr/application/factories/` |
| Plugin registry | `src/scavengarr/infrastructure/plugins/registry.py` |
| Plugin loader | `src/scavengarr/infrastructure/plugins/loader.py` |
| HttpxPluginBase | `src/scavengarr/infrastructure/plugins/httpx_base.py` |
| PlaywrightPluginBase | `src/scavengarr/infrastructure/plugins/playwright_base.py` |
| Search engine | `src/scavengarr/infrastructure/scraping/` |
| Link validator | `src/scavengarr/infrastructure/validation/` |
| Torznab presenter | `src/scavengarr/infrastructure/torznab/` |
| Stremio infrastructure | `src/scavengarr/infrastructure/stremio/` |
| Hoster resolvers | `src/scavengarr/infrastructure/hoster_resolvers/` |
| Torznab router | `src/scavengarr/interfaces/api/torznab/` |
| Stremio router | `src/scavengarr/interfaces/api/stremio/` |
| CLI | `src/scavengarr/interfaces/cli/` |
| YAML plugin example | `plugins/filmpalast_to.yaml` |
| Python plugin example | `plugins/boerse.py` |
| Test suite (3225 tests) | `tests/` |
