# Changelog

All notable changes to Scavengarr are documented in this file.
Format: version, date, grouped changes. Newest entries first.

---

## Unreleased (staging)

Massive expansion of the plugin ecosystem (2 → 40 plugins), Stremio addon integration,
hoster resolver system, plugin base class standardization, search result caching, and
growth of the test suite from 160 to 3225 tests.

### Stremio Playback: behaviorHints.proxyHeaders
Pre-resolve hoster embed URLs at `/stream` time and emit `behaviorHints.proxyHeaders`
so Stremio's local streaming server sends the correct `Referer` and `User-Agent` headers
to hoster CDNs. This eliminates buffering caused by 403 rejections on missing headers.

- Add `behavior_hints` field to `StremioStream` domain entity
- Add `_build_behavior_hints()` and `_resolve_top_streams()` to `StremioStreamUseCase`
- Add `ResolveCallback` type and wire `HosterResolverRegistry.resolve` via composition root
- Add `Referer` header to all streaming resolver returns (VOE, Filemoon, SuperVideo, Streamtape)
- Emit `behaviorHints.notWebReady` + `proxyHeaders.request` in Stremio stream JSON
- Fallback to `/play/` proxy redirect for streams that fail pre-resolution

### Code Quality (Audit)
- Remove dead code across domain, application, infrastructure, and plugin layers
- Consolidate duplicate constants and unused imports

### YAML Plugin Infrastructure Removal (Refactor)
Migrated 3 remaining YAML plugins (warezomen, filmpalast, scnlog) to Python httpx
plugins. Removed entire YAML plugin infrastructure: ScrapyAdapter, YAML schema models,
YAML loader, YAML discovery, and all associated tests (~3,500 lines deleted). Renamed
`HttpxScrapySearchEngine` to `HttpxSearchEngine`. Removed `scrapy` and `beautifulsoup4`
dependencies from `pyproject.toml`.

- Add warezomen Python httpx plugin replacing YAML (`fd4bc98`)
- Add filmpalast Python httpx plugin replacing YAML (`3ae7921`)
- Add scnlog Python httpx plugin replacing YAML (`0fa4f36`)
- Remove YAML plugin infrastructure and scrapy dependency (`42fced9`)
- Rename HttpxScrapySearchEngine to HttpxSearchEngine (`12aa0a5`)

### Plugin Standardization (Refactor)
All 29 Python plugins migrated to shared base classes (`HttpxPluginBase` /
`PlaywrightPluginBase`), eliminating 50–100 lines of duplicated boilerplate per plugin
(client setup, domain fallback, cleanup, semaphore, user-agent).

- Add `HttpxPluginBase` shared base class for httpx plugins (`16b084b`)
- Add `PlaywrightPluginBase` shared base class for Playwright plugins (`16b084b`)
- Add shared plugin constants and CSS-selector HTML helpers (`b12c5a7`)
- Migrate 5 API-only plugins (einschalten, fireani, haschcon, megakino_to, movie4k) to HttpxPluginBase (`407fef9`)
- Migrate aniworld, dataload, nima4k plugins to HttpxPluginBase (`21030d3`, `bded483`, `e4f7f13`)
- Migrate all remaining 21 plugins to shared base classes (`10d23db`)
- Add missing `season`/`episode` params to 10 plugin `search()` signatures (`deef995`)
- Reorganize configurable settings (`_DOMAINS`, `_MAX_PAGES`, etc.) to top of all 28 plugins with section headers (`a79fb8e`)
- Replace hardcoded year boundary with dynamic `datetime.now().year + 1` in cine plugin (`b3e40e3`)

### New Plugins (36 Python plugins added)
Expanded from 2 plugins (filmpalast YAML + boerse Python) to 40 total plugins
(3 YAML + 37 Python), covering German streaming, DDL, and anime sites.

**Httpx plugins (28):**
- aniworld.to — anime streaming with domain fallback (`3321775`)
- burningseries (bs.to) — series streaming (`b1e46ff`)
- cine.to — movie streaming via JSON API (`3153df0`)
- dataload (data-load.me) — DDL forum with vBulletin auth (`94004e6`)
- einschalten.in — streaming via JSON API (`a729041`)
- filmfans.org — movie streaming with release parsing (`7924969` → `7cd46ed`)
- fireani.me — anime via JSON API (`160171f`)
- haschcon.com — streaming (`0d65a50`)
- hdfilme.legal — streaming with MeineCloud link extraction (`fdaf283`)
- kinoger.com — streaming with domain fallback (`1c03b95`)
- kinoking.cc — streaming with movie/series detection (`067a634`)
- kinox.to — streaming with 9 mirror domains and AJAX embed extraction (`d645ccf`, `20e40e9`)
- megakino.me — streaming (`ff68aeb`)
- megakino_to (megakino.org) — streaming via JSON API (`df2cf77`)
- movie2k.cx — streaming with 2-stage HTML scraping
- serienfans.org — TV series DDL with JSON search API and season/episode support
- movie4k.sx — streaming via JSON API with cross-language title matching (`52f07dd`, `dfc58db`)
- myboerse.bz — DDL forum with multi-domain fallback (`27b42b4`, `d80c69a`)
- nima4k.org — DDL with category browsing (`d001135`)
- nox.to — DDL archive with JSON API, movies + TV episodes
- sto (s.to/SerienStream) — TV-only streaming (`7924969`, `2a73f16`)
- streamcloud.plus — streaming with domain fallback (`10f3808`)
- streamkiste.taxi — streaming with 5 mirror domains (`ff8c662`, `bea8be1`)
- cineby.gd — streaming via JSON API
- crawli.net — single-stage download search engine
- hd-source.to — DDL with multi-page scraping
- hd-world.cc — DDL archive via WordPress REST API, movies + TV series
- serienjunkies.org — DDL with captcha-protected links

**Playwright plugins (9):**
- animeloads (anime-loads.org) — anime with DDoS-Guard bypass (`75176af`, `08cced5`)
- boerse.sx — DDL forum with Cloudflare + vBulletin auth (rewritten, see v0.1.0)
- byte.to — DDL with Cloudflare bypass and iframe link extraction (`2cdab77`)
- ddlspot.com — DDL with pagination up to 1000 results (`fca8947`, `21a0657`)
- ddlvalley.me — DDL WordPress with pagination (`0fedecf`, `3d80cec`)
- moflix (moflix-stream.xyz) — streaming via internal API with Cloudflare bypass (rewritten from httpx, `eaa0002`)
- mygully.com — DDL forum with Cloudflare + vBulletin auth
- scnsrc.me (SceneSource) — scene releases with multi-domain fallback (`cb34282`, `2d930bb`)
- streamworld.ws — streaming (rewritten from httpx to Playwright, `de29957`)

**YAML plugins (3):**
- filmpalast.to — movie/TV streaming (original)
- scnlog.me — scene log with pagination (`24dd4b3`, `a30164b`)
- warezomen.com — DDL converted from Python to YAML (`1bba59a`, `a30164b`)

### Stremio Addon
Full Stremio addon integration with manifest, catalog search, and stream resolution.
Allows using Scavengarr as a Stremio source for all indexed plugins.

- Add Stremio domain entities, TMDB port, and StremioConfig (`c055303`)
- Add TMDB httpx client with caching and German locale (`c7950ef`)
- Add release name parser with guessit integration (`89b8ca9`, `e8a07b`)
- Add stream converter for SearchResult → RankedStream (`a4b2e0c`)
- Add configurable stream sorter for Stremio addon (`015dde6`)
- Add StremioCatalogUseCase for TMDB trending and search (`8d7dfbc`)
- Add StremioStreamUseCase for IMDb-to-streams resolution (`526e0c5`)
- Add Stremio router with manifest, catalog, and stream endpoints (`0d5854e`, `fed81df`)
- Add title-match scoring module for Stremio stream filtering (`6a06df9`, `b9454cf`)
- Add `get_title_and_year()` to TMDB client and IMDB fallback (`55a7bf7`, `2af65b1`)
- Add IMDB fallback title resolver for Stremio without API key (`23fe5c4`)
- Add Wikidata German title lookup for IMDB fallback client (`eb8094a`)
- Robust title matching via guessit + multi-candidate scoring (`e8a07b`)
- Thread `plugin_default_language` through stream converter (`8bf0911`)
- Add `default_language` attribute to all plugins (`c53e04c`)
- Add per-plugin timeout to prevent slow plugins blocking response (`c03a28b`)

### Hoster Resolver System
39 hoster resolvers across three categories: 10 streaming resolvers (video URL extraction),
14 DDL resolvers (file availability validation), and 15 XFS-consolidated resolvers
(generic `XFSResolver` with parameterised `XFSConfig`). All resolver tests use respx
(httpx-native HTTP mocking).

**Core infrastructure:**
- Add ResolvedStream entity and HosterResolverPort protocol (`f6a3676`)
- Add HosterResolverRegistry with content-type probing fallback (`8a7642b`)
- Add hoster hint fallback for rotating redirect domains (`cfe3314`)
- URL domain priority + redirect following in hoster registry (`b083c0b`)
- Add `cleanup()` to HosterResolverRegistry (`c148640`)
- Integrate hoster resolvers into `/play/` endpoint (`2b1f82c`)
- Cache stream links and generate proxy play URLs (`686b4bf`, `f61e30a`)
- Add `/stremio/play/{stream_id}` endpoint with 302 redirect (`08be69c`)
- Add stream preflight probe to filter dead hoster links at `/stream` time
- Add hybrid Playwright Stealth probe for Cloudflare bypass

**Streaming resolvers (10):**
- Add VOE hoster resolver with multi-method extraction (`242ce2d`)
- Add Streamtape hoster resolver with token extraction (`b163637`)
- Add SuperVideo hoster resolver with XFS video extraction (`d980ebe`)
- Add DoodStream hoster resolver with pass_md5 extraction (`5ba3a58`)
- Add Filemoon hoster resolver with packed JS unpacker (`e9353f3`)
- Add Filemoon Byse SPA API extraction and challenge/attest/decrypt flow (`ad62013`, `8592356`)
- Add packed JS decoder for SuperVideo video URL extraction (`e7baaa6`)
- Add Playwright fallback to SuperVideo for Cloudflare bypass (`7ce90dd`, `4438322`)
- Add 429 rate-limit retry with back-off to SuperVideo resolver (`67babee`)
- Add Mixdrop hoster resolver with token extraction (multi-domain)
- Add VidGuard hoster resolver with multi-domain embed resolution
- Add Vidking hoster resolver with embed page validation
- Add Stmix hoster resolver with embed page validation
- Add SerienStream hoster resolver (s.to / serien.sx domain matching)

**DDL resolvers (14):**
- Add filer.net DDL hoster resolver via public status API
- Add Katfile DDL hoster resolver (XFS offline marker detection)
- Add Rapidgator DDL hoster resolver (website scraping validation)
- Add DDownload DDL hoster resolver (ddownload.com / ddl.to, XFS page check)
- Add Alfafile DDL hoster resolver (page scraping)
- Add AlphaDDL hoster resolver (page scraping)
- Add Fastpic image host resolver (fastpic.org / fastpic.ru)
- Add Filecrypt container resolver (container validation)
- Add FileFactory DDL hoster resolver (page scraping)
- Add FSST hoster resolver (page scraping)
- Add Go4up mirror link resolver (mirror link validation)
- Add Nitroflare DDL hoster resolver (page scraping)
- Add 1fichier DDL hoster resolver (multi-domain page scraping)
- Add Turbobit DDL hoster resolver (multi-domain page scraping)
- Add Uploaded DDL hoster resolver (uploaded.net / ul.to)

**XFS consolidation (15 hosters):**
- Add generic `XFSResolver` with `XFSConfig` dataclass consolidating 15 XFS hosters into one module (`xfs.py`)
- Configs: Katfile, Hexupload, Clicknupload, Filestore, Uptobox, Funxd, Bigwarp, Dropload, Goodstream, Savefiles, Streamwish (9 domains), Vidmoly, Vidoza, Vinovo, Vidhide (6 domains)
- Parameterised tests: 219 test cases auto-generated from 15 configs
- Delete 15 individual resolver files + 15 individual test files (~4,200 lines removed)

**Test improvements:**
- Migrate all 17 non-XFS resolver test files from AsyncMock to respx (httpx-native HTTP mocking)
- Add live contract test skeleton for resolver smoke tests (`tests/live/test_resolver_live.py`)

### Plugin Improvements
Various fixes and enhancements to individual plugins.

- Rewrite kinoger search parser for redesigned site template (`3cf475c`)
- Rewrite streamworld plugin from httpx to Playwright mode (`de29957`)
- Rewrite moflix plugin from httpx to Playwright mode (`eaa0002`)
- Fix streamkiste parser to handle `<span class="movie-title">` tags (`bea8be1`)
- Fix sto plugin to reject non-TV categories (TV-only site) (`2a73f16`)
- Fix filmpalast.to plugin selectors and change provides to stream (`dfc48a3`)
- Fix animeloads DDoS-Guard detection excludes h1 selector (`08cced5`)
- Optimize sto plugin to fetch only requested episode instead of full season (`bb48c58`)
- Add season/episode filtering to mixed plugins (`9d433f5`, `d109844`, `ea8385f`, `ec643ae`)
- Add `provides` attribute to plugin system (`e38a07b`)
- Add domain fallback to aniworld plugin (`f72fc2d`)
- Add pagination to ddlspot, ddlvalley, scnlog, warezomen, boerse (`21a0657`, `3d80cec`, `a30164b`, `3cfa20f`)
- Add Torznab category filtering for YAML plugins (`5dc3018`)
- Add kinox AJAX embed URL extraction for hoster resolution (`20e40e9`)

### API & Router Improvements
- Centralize `/api/v1/` prefix for all endpoints (`d25ee5c`)
- Rename `main.py` → `app.py`, `cli.py` → `__main__.py` (`b25bf5c`)
- Delegate router to use cases, remove inline business logic (`7c36166`)
- Wire Stremio use cases into AppState and composition (`d3f076d`)

### Search Result Caching
Cache layer for repeated search queries with configurable TTL and cache-hit indicators.

- Add `_search_cache_key()` with SHA-256 hashing of plugin + query + category
- Add cache read/write to `TorznabSearchUseCase` with graceful error handling
- Add `search_ttl_seconds` config (default 900s / 15 minutes, 0 = disabled)
- Add `X-Cache: HIT/MISS` response header to Torznab search responses

### Plugin Fixes (website changes)
Five plugins updated to match changed website structures.

- Fix filmfans release loading: extract `initMovie()` hash and fetch releases via `/api/v1/{hash}` JSON endpoint
- Fix kinoger search parser: update selectors for redesigned DLE template (`shortstory` → detail link extraction)
- Fix megakino_to: add GET fallback for domain verification (HEAD returns 405)
- Fix movie4k: add GET fallback for domain verification (HEAD returns 405)
- Fix streamkiste: rewrite detail parser to extract streams from meinecloud.click external script

### Test Suite Growth (160 → 3225 tests)
Test suite expanded from 160 to 3225 tests (3047 unit + 109 E2E + 31 integration + 38 live)
with comprehensive coverage across all layers.

- Add unit tests for all 35 plugin test files
- Add unit tests for all 39 hoster resolvers (10 streaming + 14 DDL + 15 XFS consolidated)
- Add unit tests for HttpxPluginBase and PlaywrightPluginBase
- Add unit tests for Stremio components (stream converter, stream sorter, TMDB client, title matcher, IMDB fallback)
- Add unit tests for release name parser, plugin registry, HTML selectors
- Add unit tests for stream link cache and hoster registry
- Add 109 E2E tests (46 Torznab endpoint + 63 Stremio endpoint)
- Add 31 integration tests (config loading, crawljob lifecycle, link validation, plugin pipeline)
- Add 38 live smoke tests (plugin smoke tests + resolver contract tests)
- Migrate all resolver tests from AsyncMock to respx (httpx-native HTTP mocking)
- Add parameterised XFS resolver tests (219 test cases across 15 configs)

### Documentation
- Add plugin search standards (categories + pagination up to 1000) (`c1fa2c4`)
- Update agent policy — only for simple mechanical tasks (`e01246c`)
- Add team agents rules to CLAUDE.md (`2a0eddc`)
- Restructure documentation following MasterSelects pattern (`dea6fad`)

---

## v0.1.0 - 2025-XX-XX (Initial Release)

First release of Scavengarr as a self-hosted Torznab/Newznab indexer. Includes the
core scraping pipeline, plugin system (YAML + Python), Torznab API, CrawlJob packaging,
link validation, and a comprehensive unit test suite.

### Boerse Plugin Rewrite
Complete rewrite of the boerse.sx plugin to handle the real site structure, including
Cloudflare JS challenge bypass via Playwright and vBulletin form-based authentication.

- Rewrite boerse.py plugin with Playwright for Cloudflare JS challenge bypass (`502c2b7`)
- Rewrite login, search, and link extraction to match real vBulletin site structure (`7e41ad3`)
- Resolve nested `<div>` parsing bug in post content and use full `#searchform` (`9bfecf6`)
- Filter download links to known container hosts only (keeplinks.org, filecrypt.cc, etc.), deduplicate thread URLs by thread ID (`533f6aa`)
- Read boerse credentials lazily in `_ensure_session()` to avoid startup failures when env vars are not yet set (`e090033`)

### Mirror URL Fallback
Automatic domain failover for plugins with multiple mirror URLs. When the primary
domain is unreachable, the system probes mirrors and falls back transparently.

- Add `mirror_urls` field to YAML plugin schema for declaring alternative domains (`1591072`)
- Add mirror domain fallback to ScrapyAdapter: probe mirrors on connection failure (`9543e2c`)
- Probe mirror URLs in health endpoint when primary domain is unreachable (`b1b8901`)
- Merge `mirror_urls` into `base_url` as a single-or-list field for simpler plugin config (`4eab6b5`)

### Multi-Link CrawlJob Packaging
CrawlJob system extended to bundle multiple validated download links from different
hosters into a single `.crawljob` file, with automatic promotion of alternatives when
primary links are dead.

- Multi-link CrawlJob packaging: bundle all valid hoster URLs into a single `.crawljob` artifact (`35326b7`)
- Promote alternative download links when primary link fails HEAD/GET validation (`078fcae`)

### Python Plugin System
New imperative plugin type for sites that require complex logic beyond what YAML
selectors can express (authentication, JavaScript interaction, custom parsing).

- Add boerse.sx Python plugin with domain fallback across 5 mirrors and anonymizer link handling (`bf0a9d3`)
- Add Python plugin dispatch to TorznabSearchUseCase: detect `.py` plugins and call their `search()` method (`98e6081`)
- Add env var support to AuthConfig for YAML plugin credentials: `$ENV{VAR_NAME}` syntax (`5b53f00`)
- Align `PluginRegistryPort.get()` return type with concrete registry implementation (`a73c6b9`)

### Link Validation
HTTP-based link validation with parallel execution, HEAD-first strategy, and GET
fallback for hosters that block HEAD requests.

- Add GET fallback to HttpLinkValidator for hosters that return 403/405 on HEAD requests (`e69cd54`)
- Add `validate_results()` method to SearchEnginePort protocol for post-search filtering (`d7d1dab`)

### Test Suite
Comprehensive unit test suite covering all three architecture layers with proper
mock patterns (sync MagicMock for PluginRegistryPort, AsyncMock for async ports).

- Add comprehensive unit test suite: 160+ tests across domain, application, and infrastructure (`e0674c5`)
  - Domain: CrawlJob entity, TorznabQuery/Item/Caps, SearchResult, plugin schema validation
  - Application: CrawlJobFactory, Torznab caps/indexers/search use cases
  - Infrastructure: parsers, converters, extractors, presenter, link validator, search engine, cache
- Apply ruff format to test files for consistent style (`2040852`)

### Clean Architecture Refactor
Three-phase migration from flat codebase to Clean Architecture with Domain,
Application, Infrastructure, and Interfaces layers. See
`docs/refactor/COMPLETED/clean-architecture-migration.md` for full details.

**Phase 1: Domain layer cleanup**
- Remove Pydantic from Domain layer, convert all entities to `@dataclass` (`7726ba8`)

**Phase 2: Entity consolidation**
- Consolidate SearchResult definition into single canonical location (`b7bc0be`)

**Phase 3: Adapter reorganization**
- Reorganize all adapters into `infrastructure/` namespace by concern (`d97d7a3`)

**Follow-up commits:**
- Move presenter to infrastructure layer (`8729319`)
- Rename `httpx_scrapy_engine` to `search_engine` for clarity (`56b48df`)
- Rename cache factory for naming consistency (`d32066c`)
- Use shared size parser across layers, eliminating duplication (`de788dc`)
- Consolidate duplicate int parsing into `infrastructure/common/` utils (`7610b9b`)
- Add common utils structure: parsers, converters, extractors (`b0f4cca`)
- Move composition root from application to interfaces layer (correct placement) (`a9eab40`)
- Remove redundant `discover()` calls from use cases and router (`028c932`)
- Parallelize multi-stage scraping with `asyncio.gather` for non-blocking I/O (`f419b5e`)
- Prevent duplicate search results from multi-stage scraping via dedup logic (`6b7fd8d`)

### Code Quality
Codebase-wide standardization of typing patterns, docstring conventions, and
language consistency.

- Standardize typing to modern Python 3.10+ syntax (`T | None`, `list[T]`, `dict[K, V]`) and replace ABC with Protocol across all ports (`84995a1`)
- Standardize docstrings: remove redundant comments, ensure consistent English documentation (`2c6278a`)
- Translate all remaining German comments and docstrings to English for international consistency (`04f4b81`, `bcfe059`, `b18711e`, `4e440d7`)
- Apply pre-commit auto-fixes: trailing whitespace, end-of-file, import sorting (`dccc4ad`, `0e6c937`)

### Documentation
Project documentation covering architecture, coding standards, plugin system, and
test suite organization.

- Add comprehensive project documentation covering all architecture layers (`d1d4a56`)
- Add typing standards and test suite information to CLAUDE.md (`9c7106a`)
- Document all infrastructure components and their responsibilities in CLAUDE.md (`9abf1c3`)

### Core Infrastructure (Initial)
Foundation of the project: FastAPI server, Scrapy scraping engine, plugin loader,
configuration system, and CrawlJob generation.

- Initial content commit: FastAPI/Uvicorn server, Scrapy-based scraping, Playwright integration, structlog logging, diskcache backend (`7fd6747`)
- Add YAML configuration system with pydantic-settings and plugin loader with filesystem discovery (`3889847`)
- Add CrawlJob system for `.crawljob` file generation and assorted bug fixes (`df3e952`)
- Refactoring: improve module structure, separate concerns, clean up imports (`ed53426`)

---

## KNOWN_ISSUES

Current known issues:

- **Cloudflare-heavy sites:** Several Playwright plugins (ddlspot, ddlvalley, scnsrc, byte) return 0 results when Cloudflare challenges cannot be bypassed in headless mode.
