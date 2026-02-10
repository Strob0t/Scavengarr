# Changelog

All notable changes to Scavengarr are documented in this file.
Format: version, date, grouped changes. Newest entries first.

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

Current known issues as of v0.1.0:

- **No PlaywrightAdapter:** The Playwright engine does not have a formal `SearchEnginePort` adapter. The boerse.py plugin manages its own Playwright browser lifecycle directly. See `docs/plans/playwright-engine.md` for the implementation plan.
- **No integration tests:** The test suite contains 235+ unit tests but no integration tests that verify end-to-end wiring. See `docs/plans/integration-tests.md`.
- **No search caching:** Every Torznab search request triggers a full scraping pipeline. Repeated queries are not cached. See `docs/plans/search-caching.md`.
- **Limited plugin set:** Only two plugins ship with the project (filmpalast.to YAML, boerse.py Python). See `docs/plans/more-plugins.md`.
