# Changelog

All notable changes to Scavengarr are documented in this file.
Format: version, date, grouped changes. Newest entries first.

---

## Unreleased (staging)

Massive expansion of the plugin ecosystem (2 → 40 plugins), Stremio addon integration,
hoster resolver system, plugin base class standardization, search result caching, and
growth of the test suite from 160 to 3225 tests.

### E2E-Discovered Fixes (Stream Resolution)
- Fix XFS two-step form hosters: detect `<form id="F1" action="/dl">` splash pages and POST to `/dl` with `op=embed&file_code={id}&auto=1` to obtain the actual player page (affects bigwarp, savefiles, streamruby, and other form-based XFS hosters)
- Mark wolfstream as `needs_captcha=True` — embed pages return obfuscated JS redirect (anti-bot), not extractable with httpx
- Fix vidking resolver regex: accept `/embed/tv/{tmdb_id}/{season}/{episode}` paths for series content (was only matching `/embed/movie/`)

### XFS Video Hoster Extraction
- Upgrade XFS resolver from validate-only to full video URL extraction for 18 video hosters
- Extract playable HLS/MP4 URLs from embed pages via JWPlayer config, Dean Edwards packed JS, and Streamwish `hls2` patterns
- Shared video extraction module (`_video_extract.py`) reused by both XFS and Filemoon resolvers
- Add `is_video_hoster` / `needs_captcha` flags to `XFSConfig` for per-hoster behavior
- Video hosters: fetch `/e/{file_id}` embed page → extract video URL → return `ResolvedStream` with Referer header
- DDL hosters (katfile, hexupload, clicknupload, filestore, uptobox, hotlink): keep validate-only behavior
- Captcha-required hosters (veev, vinovo): return None immediately (Cloudflare Turnstile required)
- Add `extra_domains` field to `XFSConfig` for JDownloader-sourced domain aliases (vidhide has 19 aliases)
- Supported video hosters: streamwish, vidmoly, vidoza, vidhide, lulustream, upstream, wolfstream, vidnest, mp4upload, uqload, vidshar, vidroba, vidspeed, bigwarp, dropload, savefiles, funxd, streamruby

### Stremio Playback Fixes
- Filter non-video URLs from Stremio responses: `_is_direct_video_url()` detects embed pages vs actual video URLs (.mp4, .m3u8, HLS patterns)
- Skip unplayable streams at search time: resolvers that only validate availability (XFS, DDL) but cannot extract video URLs are excluded from Stremio results instead of producing guaranteed-502 proxy URLs
- Guard in `/play/` endpoint rejects resolved URLs that are just the embed page echoed back (returns 502 instead of redirecting to HTML)
- Fix VOE resolver: follow JS redirects from voe.sx → rotating domains (e.g. lauradaydo.com), fetch token array from external loader.js script

### Production Bug Fixes (Stream Resolution)
- Fix `http-equiv=` garbage treated as URL in megakino_to/movie4k `_collect_streams()` — reject non-HTTP stream values
- Add belt-and-suspenders URL scheme validation in `HttpLinkValidator.validate_batch()`
- Fix veev resolver regex: accept 12+ char alphanumeric IDs (was exactly 12, veev.to now uses 43-char IDs)
- Fix vidking resolver regex: accept `/embed/movie/{id}` paths used by cineby/videasy plugins
- Remove goodstream from XFS configs — URLs use custom player format, not XFS

### Torznab Pagination
- Wire `TorznabQuery.offset`/`limit` fields through router → use case for server-side result pagination
- Add `offset` and `limit` query parameters to the Torznab search endpoint (defaults: 0 / 100)
- Prowlarr can now page through cached result sets via standard Torznab pagination

### Cloudflare Detection in Health Probes
- `HealthProber` now detects Cloudflare challenges during HEAD/GET probes:
  - HEAD path: `cf-ray` header + 403/503 status code heuristic
  - GET fallback: body-based marker detection via `is_cloudflare_challenge()`
- `ProbeResult.captcha_detected` is now set by the health prober (was always `False`)
- `compute_health_observation()` returns 0.0 for captcha-blocked probes — CF-blocked plugins rank lower

### Dead Code Cleanup
- Remove empty `infrastructure/scraping/` package (stale from Scrapy removal)
- Remove dead `TorznabAction` type alias (defined but never used)
- Remove dead `CacheBackend` re-export from `infrastructure/cache/__init__.py`
- Remove dead `AgeBucket` re-export from `domain/entities/__init__.py`
- Fix duplicate `AgeBucket` in `query_pool.py` — import from domain instead of redefining
- Remove 3 dead config field assignments in `StremioStreamUseCase` (`stremio_deadline_ms`,
  `max_items_total`, `max_items_per_plugin`) — stored but never read
- Delete dead `/indexers` data file (obsolete Scrapy reference)
- Delete empty `.env.example`
- Clean orphaned `__pycache__` directories
- Remove dead `PluginRegistry.load_all()` method (never called)
- Remove dead `router` re-export from `interfaces/api/__init__.py`
- Remove dead `PluginStats.last_search_ns` field (written but never read)
- Remove dead `StageResult` dataclass from `domain/plugins/base.py`
- Remove dead `PluginValidationError` from `domain/plugins/exceptions.py`
- Remove dead `LinkValidatorPort` protocol (entire file deleted)
- Remove dead `validation_schema.py` module (entire file deleted)
- Remove dead `test_auth_env_resolution.py` test file (entire file deleted)
- Remove dead `probe_urls()` function from `infrastructure/hoster_resolvers/probe.py`
- Remove dead `_DOMAINS` set and `_is_streamtape_domain()` from `streamtape.py`
- Remove dead `get_german_title()` from `TmdbClientPort`, `HttpxTmdbClient`, and `ImdbFallbackClient`

### HTTP Rate Limiting & 429 Retry (Defense in Depth)
- Add `RetryTransport` — custom httpx transport wrapping all outgoing HTTP requests
  - Proactive: per-domain token-bucket rate limiting via existing `DomainRateLimiter` (5 RPS default)
  - Reactive: automatic retry on 429/503 with exponential backoff + jitter + `Retry-After` header support
  - Configurable: `retry_max_attempts` (default 3), `retry_backoff_base` (1s), `retry_max_backoff` (30s)
- Wire `DomainRateLimiter` (previously dead code) into shared `httpx.AsyncClient` via transport layer
- Remove manual 429 retry from `SuperVideoResolver` (now handled transparently by transport)
- Add 3 config fields: `http.retry_max_attempts`, `http.retry_backoff_base`, `http.retry_max_backoff`

### Plugin Scoring & Probing
Background plugin scoring system that measures plugin health and search quality via
EWMA-based probes, then selects only the top-N plugins per Stremio request.

- Add domain entities: `ProbeResult`, `EwmaState`, `PluginScoreSnapshot` with age buckets
- Add `PluginScoreStorePort` protocol and `CachePluginScoreStore` persistence (JSON via CachePort)
- Add pure EWMA scoring functions: `alpha_from_halflife`, `ewma_update`, `compute_confidence`,
  `compute_health_observation`, `compute_search_observation`, `compute_final_score`
- Add `HealthProber` (HEAD with 405/501 GET fallback, Cloudflare detection) and `MiniSearchProber` (limited search + hoster HEAD checks)
- Enhance `MiniSearchProber` to filter HEAD-checks by supported hosters (from `HosterResolverRegistry`)
- Add `supported_ratio` (5th component, weight 0.25) to `compute_search_observation()` — scores now reflect
  whether a plugin's result links point to hosters with registered resolvers
- Add `hoster_supported` / `hoster_total` fields to `ProbeResult`
- Add `QueryPoolBuilder` with dynamic TMDB-based query generation (trending + discover endpoints, weekly rotation, German locale, bundled fallback lists)
- Add `ScoringScheduler` background task (health probes daily, search probes 2x/week per plugin/category/bucket)
- Add `ScoringConfig` and extend `StremioConfig` with scoring budget parameters
- Add per-plugin YAML overrides (`PluginOverride` model: timeout, max_concurrent, max_results, enabled)
- Fix YAML config loading for `stremio` and `scoring` sections (pre-existing gap in `_SECTION_KEYS`)
- Wire scoring components in composition root with clean cancellation on shutdown
- Add scored plugin selection in `StremioStreamUseCase` with cold-start fallback and exploration slot
- Add `GET /api/v1/stats/plugin-scores` debug endpoint with query filters
- Add `PluginRegistry.remove()` method for disabling plugins via config overrides

### Hoster Resolver Expansion
- Add 6 new XFS hoster configs: Mp4Upload, Uqload, Vidshar, Vidroba, Hotlink, Vidspeed (27 XFS hosters total)
- Add new SendVid streaming resolver (two-stage: API status check + page video extraction)
- Add new Mediafire DDL resolver (public file info API, offline detection via error 110 + delete_date)
- Add new GoFile DDL resolver (ephemeral guest token with 25-min cache, content availability API)
- Add 6 new XFS hoster configs: StreamRuby, Veev, Lulustream, Upstream, Wolfstream, Vidnest
- Add 20 new StreamWish domain aliases from JDownloader (obeywish, awish, embedwish, etc.)
- Add 5 new Streamtape domain aliases (scloud, strtapeadblock, tapeblocker, etc.)
- Add new StreamUp (strmup) standalone HLS resolver with page + AJAX fallback extraction
- Add `vidara` domain alias to StreamUp resolver (Vidara = StreamUp infrastructure)
- Add new Vidsonic standalone HLS resolver with hex-obfuscated URL decoding
- Wire StrmupResolver and VidsonicResolver in composition root

### Stremio Stream Deduplication
- Add per-hoster deduplication: only the best-ranked stream per hoster is returned
- Prevents duplicate links from the same hoster (e.g., 5 VOE links → 1 best VOE link)
- Applied after sorting, before probing/caching — keeps highest-ranked link per hoster

### Architecture Fixes (Clean Architecture Compliance)
- Remove all infrastructure imports from `StremioStreamUseCase` (application layer)
  - Define `_StremioConfig`, `_StreamSorter`, `_MetricsRecorder` protocols locally
  - Inject `sorter`, `convert_fn`, `filter_fn`, `user_agent`, `max_results_var` via constructor
  - Composition root (interfaces layer) now owns all infrastructure wiring
- Add `@runtime_checkable` to all 9 domain Protocol ports (was only on 2 of 9)
- Make `CrawlJob` entity immutable (`frozen=True`) — built once by factory, never mutated
- Remove explicit Protocol inheritance from `CacheCrawlJobRepository` (duck-typing consistency)

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

### Performance (Audit)
- Parallelize `_select_plugins()` score fetching with `asyncio.gather()` (was sequential await loop)
- Parallelize `_run_search_cycle()` probes: collect all due probes first, then run concurrently with semaphore
- Offload CPU-bound `filter_by_title_match()` (guessit + SequenceMatcher) to thread pool via `run_in_executor()`
- Add periodic eviction of expired entries in `HosterResolverRegistry` caches (prevents unbounded memory growth)

### Code Quality (Audit)
- Consolidate 12 identical DDL hoster resolvers into parameterised `GenericDDLConfig` + `GenericDDLResolver`
  (alfafile, alphaddl, fastpic, filecrypt, filefactory, fsst, go4up, mixdrop, nitroflare, 1fichier, turbobit, uploaded)
- Add shared `extract_domain()` utility for URL domain extraction, replacing 15 inline duplicates across resolver modules
- Add `exc_info=True` to 4 `except Exception` handlers in business logic (stremio_stream.py, composition.py)
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

### Test Suite Growth (160 → 3533 tests)
Test suite expanded from 160 to 3533 tests (3355 unit + 109 E2E + 31 integration + 38 live)
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
