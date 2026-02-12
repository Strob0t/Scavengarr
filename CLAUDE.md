# CLAUDE.md

Instructions for developers and AI assistants (Claude, GPT, etc.) working on Scavengarr.

***

## 1. Workflow (IMPORTANT!)

This section defines the day-to-day workflow rules for contributions to Scavengarr.

### Branch rules

| Branch | Purpose |
|---|---|
| `staging` | Development branch (commit here). |
| `main` | Production branch (merge via PR only). |

Rules:
- Never commit directly to `main`.
- Never merge into `main` without an explicit user request.

### Commit rules

Before every commit, you must run:

```bash
poetry run pre-commit run --all-files
poetry run pytest
```

`pre-commit` is part of the dev toolchain and a `.pre-commit-config.yaml` exists in the repository.

Rules:
- Fix all errors before committing (warnings can be acceptable depending on the check).
- Make small, atomic commits (do not batch unrelated changes).
- Push after each successful change:

```bash
git add .
git commit -m "description"
git push origin staging
```

Never:
- Commit to `main`.
- Merge to `main` on your own.
- Accumulate multiple changes without committing.
- Commit without running `poetry run pre-commit run --all-files`.

### Merge to main (only when the user requests it)

When the user explicitly requests a release/merge to `main`:

1. Bump the version in `pyproject.toml` (PATCH +1 by default unless the change warrants MINOR/MAJOR).
2. Update the changelog (see “Version & changelog” below).
3. Commit & push to `staging`.
4. Create and merge a PR:

```bash
gh pr create --base main --head staging --title "..." --body "..."
gh pr merge --merge
```

5. Sync `staging` back with `main`:

```bash
git fetch origin
git merge origin/main
git push origin staging
```

### Version & changelog

- Version source of truth: `pyproject.toml`.
- Version bump policy: bump only when merging to `main` (default: PATCH +1).
- Changelog policy: keep a single changelog at repository root (recommended name: `CHANGELOG.md`), newest entry at the top, include `version`, `date`, and `changes[]`.

If you decide to track known issues, keep them in the changelog under a `KNOWN_ISSUES` section (current bugs only).

### Documentation

When changing behavior or adding features, update the relevant documentation:
- `CLAUDE.md` when architecture or constraints change.
- `README.md` when setup/run instructions change.
- `docs/features/` for feature documentation (plugins, API, config, scraping, validation).
- `docs/architecture/` for architecture docs (clean-architecture.md, codeplan.md).
- `docs/plans/` for planned features (playwright-engine, more-plugins, integration-tests, search-caching).
- `CHANGELOG.md` when adding notable changes.
- OpenSpec documents under `openspec/changes/...` when the change is specified or tracked there.

***

## 2. Project overview

Scavengarr is a self-hosted, container-ready Torznab/Newznab indexer for Prowlarr and other Arr applications.
The system scrapes sources via two engines (Scrapy for static HTML, Playwright for JavaScript-heavy sites) and delivers results through Torznab endpoints like `caps` and `search`.

### Core ideas (target architecture)
- Plugin-driven: YAML (declarative) and Python (imperative) plugins define site-specific logic without touching core code.
- Dual engine: Scrapy + Playwright are equal-weight backends; selection per plugin/stage depends on “JS-heavy” classification.
- Multi-stage scraping is a core feature: “Search → Detail → Links” is the norm.
- I/O dominates runtime: architecture and code must be non-blocking (no mutual blocking).
- CrawlJob system: multiple validated links are bundled into a `.crawljob` file (multi-link packaging).

***

## 3. Clean Architecture (dependency rule)

```
┌────────────────────────────────────────────────┐
│  Interfaces (Controllers, CLI, HTTP Router)    │ ← Frameworks & Drivers
├────────────────────────────────────────────────┤
│  Adapters (Scrapy, Playwright, DiskCache)      │ ← Interface Adapters
├────────────────────────────────────────────────┤
│  Application (Use Cases, Factories, Services)  │ ← Application Business Rules
├────────────────────────────────────────────────┤
│  Domain (Entities, Value Objects, Protocols)   │ ← Enterprise Business Rules
└────────────────────────────────────────────────┘
```

Dependency rule: inner layers never know about outer layers.
✅ Application imports Domain
✅ Infrastructure implements Domain Protocols
❌ Domain NEVER imports FastAPI, httpx, diskcache

***

## 4. Architecture layers (organization)

Note: this structure is the target architecture; parts of the current organization may be discarded.
Currently, Clean Architecture namespace blocks exist as top-level packages under `src/scavengarr/` (including `domain/`, `application/`, `infrastructure/`, `interfaces/`).

### Domain (enterprise business rules)
- Entities: long-lived business objects with identity (e.g., SearchResult, CrawlJob, query objects).
- Value objects: immutable values (e.g., plugin configuration, categories, query parameters).
- Protocols (ports): abstract contracts (ScrapingEngine, PluginRegistry, LinkValidator, Cache).

Rule: Domain is framework-free, I/O-free, and kept simple.

### Application (application business rules)
- Use cases: orchestrate the flow “Query → Plugin → Scrape → Validate → Present”.
- Factories: build domain objects consistently (IDs, TTL, normalization).
- Policies: quotas, limits, timeouts, retries (as rules, not framework code).

Rule: Application knows ports (protocols) but not concrete adapters.

### Infrastructure (interface adapters)
- Plugins: discovery/loading/validation for YAML & Python plugins.
- Scraping adapters: Scrapy/Playwright implementations of ScrapingEngine.
- Search engine: orchestrates multi-stage scraping, link validation, and result conversion.
- Validation: HTTP link validator (HEAD/GET strategies, redirects, parallelism).
- Cache: diskcache adapter (Redis optional only if already present).
- Persistence: repository implementations (CrawlJob cache repository).
- Torznab: presenter for XML generation, field mapping, attribute handling.
- Configuration: YAML/ENV/CLI loading with precedence, validation via Pydantic.
- Logging: structured logging setup (structlog + stdlib, async queue handler).
- Common utilities: parsers, converters, extractors for data transformation.

Rule: Infrastructure may use external libraries but must connect to Application via ports.

### Interfaces (frameworks & drivers)
- HTTP (FastAPI router): request parsing, response formatting, error mapping.
- CLI (Typer): local debugging/diagnostics/plugin checks.
- Composition root: dependency injection and wiring.

Rule: Interfaces contain no business rules, only input/output.

***

## 5. Technology stack (dependencies)

The source of truth for dependencies is `pyproject.toml`.
Scavengarr uses FastAPI/Uvicorn, Scrapy, Playwright, structlog, diskcache, Typer, pydantic-settings, httpx, and optionally Redis.

### Package documentation (docs-mcp-server)

When writing code that uses packages from `pyproject.toml`, use the **docs-mcp-server** MCP tools to look up current API documentation:
- `search_docs(library, query)` — search indexed docs for a package
- `scrape_docs(url, library)` — index new package docs if not yet available

This ensures code is written against the actual API of the installed package versions.

### Dependency principles
- Keep third-party dependencies minimal: prefer stdlib, then established libraries, only then custom code.
- Avoid building internal “mini-frameworks”.
- New dependencies require explicit justification (security, maintainability, tests, API stability).

***

## 6. Core components (terminology)

| Term | Brief description |
|---|---|
| Torznab query | Normalized input (e.g., `t=search`, `q=...`, categories, extended). |
| Plugin | Describes how to scrape (YAML: declarative; Python: imperative). |
| Stage | One step in the pipeline (e.g., `search_results`, `movie_detail`). |
| SearchResult | Domain entity: a found item, including metadata and links. |
| Link validation | I/O-heavy filter that removes dead/blocked links. |
| CrawlJob | Bundle of multiple validated links in `.crawljob` (multi-link packaging). |
| Presenter/renderer | Translates domain results into Torznab XML (Prowlarr-compatible). |

***

## 7. Request flow (high-level)

Goal: HTTP/CLI only provide input/output; the use case orchestrates; adapters perform I/O.

1. Request arrives (HTTP `caps/search/...` or CLI).
2. Use case loads plugin from registry (lazy).
3. Scraping engine executes the multi-stage pipeline (Scrapy or Playwright).
4. Link validator checks links in parallel (not sequentially).
5. CrawlJob generates `.crawljob` for multiple links (if feature is active).
6. Presenter renders the Torznab XML response.

***

## 8. Configuration system (precedence + logging)

### Precedence (high → low)
1. CLI arguments (e.g., `--config`, `--plugin-dir`, `--log-level`).
2. Environment variables (`SCAVENGARR_*`).
3. YAML config file.
4. `.env` (optional).
5. Defaults (in code).

### Configuration categories (incomplete)
| Category | Typical contents | Purpose |
|---|---|---|
| General | environment, base_url, app_name | deterministic behavior |
| Plugins | plugin_dir, discovery rules | reproducible plugin loading |
| Scraping | timeouts, user agent, redirects | stable requests |
| Playwright | headless, navigation timeouts, concurrency | controlled resources |
| Validation | HEAD/GET policy, timeouts, parallel limits | fast filtering |
| Cache | backend, ttl, storage path | less I/O |
| Logging | level, format (json/console), correlation fields | observability without noise |

### Logging (as a config topic)
- Logs are structured (JSON/console depending on environment) and include context fields like `plugin`, `stage`, `duration_ms`, `results_count`.
- Logging must never output secrets from config/env (masking/redaction).

***

## 9. Plugin system & multi-stage scraping

### Plugin types
- YAML plugins: declarative scraping (selector mapping, stages, URL templates).
- Python plugins: imperative logic for complex flows (auth, APIs, edge cases).

### Plugin discovery & loading (agent-relevant)
- Discovery: registry scans plugin dir for `.yaml` and `.py`.
- Lazy loading: plugins are parsed/imported on first access.
- Caching: once loaded, plugins remain in the process cache.

### YAML plugin (multi-stage example)

```yaml
name: "example-site"
description: "Example indexer plugin"
version: "1.0.0"
author: "scavengarr"

base_url: "https://example.org"

scraping:
  mode: "scrapy"  # "scrapy" (static HTML) or "playwright" (JS-heavy)

  stages:
    - name: "search_results"
      request:
        path: "/search/{query}/"
        method: "GET"
      selectors:
        rows: "div.result"
        title: "a.title::text"
        detail_url: "a.title::attr(href)"   # next stage input

    - name: "detail_page"
      request:
        method: "GET"
      selectors:
        rows: "div.downloads a"
        download_url: "::attr(href)"        # terminal output
        size: "span.size::text"

auth:
  type: "none"  # reserved for future/basic/form/cookie/token

categories:
  2000: "Movies"
  5000: "TV"
```

### Multi-stage execution (semantics)
- A stage can be intermediate (produces URLs for the next stage) or terminal (produces SearchResults).
- Within a stage, independent URLs are processed in parallel (bounded concurrency) to prevent blocking.
- Keep stages deterministic and testable (selectors + normalization); encapsulate edge cases in Python plugins.

***

## 10. Link validation strategy (non-blocking)

Link validation is I/O-dominant and must run in parallel.
Rule: no sequential URL checking in loops when parallelism is possible.

### Recommended policies
- Primary `HEAD` with redirects; fallback `GET` only when necessary (some hosters block HEAD).
- Keep timeout short, limit parallelism (semaphore), log results cleanly.
- Status-based decision (example): `200` ok; `403/404/timeout` invalid, optionally configurable per plugin.

***

## 11. CrawlJob system (multi-link packaging)

CrawlJob is a domain concept: a job bundles multiple validated links into a `.crawljob` artifact.
The system provides a stable download endpoint that delivers a `.crawljob` file for a job.

### Rules
- Job ID is stable, TTL is configurable, storage is interchangeable (cache port).
- `.crawljob` contains multiple links; order is deterministic (stable for tests).
- Never write unvalidated links into CrawlJobs (policy: validate-first).

***

## 12. Testing strategy (TDD + layers)

### Test layering
- Unit: Domain (pure), Application (use cases with mocks/fakes), Infrastructure (parser/mapping).
- Integration: HTTP router ↔ use case ↔ adapter with HTTP mocking.
- Optional E2E: real plugin fixtures, but deterministic (no external sites in CI).

### Current test suite (2531 tests)

```
tests/
  conftest.py                          # Shared fixtures (entities, mock ports)
  unit/
    domain/
      test_crawljob.py                 # CrawlJob entity, enums, serialization
      test_torznab_entities.py         # TorznabQuery/Item/Caps, exceptions
      test_search_result.py            # SearchResult, StageResult
      test_plugin_schema.py            # YamlPluginDefinition, stage config
    application/
      test_crawljob_factory.py         # SearchResult → CrawlJob conversion
      test_torznab_caps.py             # Capabilities use case
      test_torznab_indexers.py         # Indexer listing use case
      test_torznab_search.py           # Search use case (validation, error paths)
    infrastructure/
      test_converters.py               # to_int()
      test_parsers.py                  # parse_size_to_bytes()
      test_extractors.py               # extract_download_link()
      test_presenter.py                # Torznab XML rendering (caps + RSS)
      test_link_validator.py           # HTTP HEAD/GET validation
      test_search_engine.py            # Multi-stage result conversion, dedup
      test_crawljob_cache.py           # Cache repository (pickle storage)
      test_auth_env_resolution.py      # AuthConfig env var resolution
      test_scrapy_fallback.py          # ScrapyAdapter mirror fallback
      test_scrapy_category.py          # Scrapy category filtering
      test_scrapy_rows_and_transform.py # Scrapy rows selector + query transform
      test_html_selectors.py           # CSS-selector HTML helpers
      test_httpx_base.py               # HttpxPluginBase shared base class
      test_playwright_base.py          # PlaywrightPluginBase shared base class
      test_plugin_registry.py          # Plugin discovery and loading
      test_release_parser.py           # guessit release name parsing
      test_title_matcher.py            # Title-match scoring for Stremio
      test_imdb_fallback.py            # IMDB suggest API fallback client
      test_tmdb_client.py              # TMDB httpx client
      test_stream_converter.py         # SearchResult → RankedStream conversion
      test_stream_sorter.py            # Stremio stream sorting/ranking
      test_stream_link_cache.py        # Stream link cache repository
      test_hoster_registry.py          # HosterResolverRegistry
      test_voe_resolver.py             # VOE hoster resolver
      test_streamtape_resolver.py      # Streamtape hoster resolver
      test_supervideo_resolver.py      # SuperVideo hoster resolver
      test_doodstream_resolver.py      # DoodStream hoster resolver
      test_filemoon_resolver.py        # Filemoon hoster resolver
      test_filernet_resolver.py        # Filer.net DDL hoster resolver
      test_katfile_resolver.py         # Katfile DDL hoster resolver
      test_rapidgator_resolver.py      # Rapidgator DDL hoster resolver
      test_ddownload_resolver.py       # DDownload DDL hoster resolver
      test_aniworld_plugin.py          # aniworld plugin tests
      test_boerse_plugin.py            # boerse plugin tests
      test_burningseries_plugin.py     # burningseries plugin tests
      test_byte_plugin.py              # byte plugin tests
      test_cineby_plugin.py            # cineby plugin tests
      test_cine_plugin.py              # cine plugin tests
      test_crawli_plugin.py            # crawli plugin tests
      test_dataload_plugin.py          # dataload plugin tests
      test_ddlspot_plugin.py           # ddlspot plugin tests
      test_ddlvalley_plugin.py         # ddlvalley plugin tests
      test_einschalten_plugin.py       # einschalten plugin tests
      test_filmfans_plugin.py          # filmfans plugin tests
      test_fireani_plugin.py           # fireani plugin tests
      test_haschcon_plugin.py          # haschcon plugin tests
      test_hdfilme_plugin.py           # hdfilme plugin tests
      test_hdsource_plugin.py          # hdsource plugin tests
      test_kinoger_plugin.py           # kinoger plugin tests
      test_kinoking_plugin.py          # kinoking plugin tests
      test_kinox_plugin.py             # kinox plugin tests
      test_megakino_plugin.py          # megakino plugin tests
      test_megakino_to.py              # megakino_to plugin tests
      test_moflix_plugin.py            # moflix plugin tests
      test_movie2k_plugin.py           # movie2k plugin tests
      test_myboerse_plugin.py          # myboerse plugin tests
      test_mygully_plugin.py           # mygully plugin tests
      test_nima4k_plugin.py            # nima4k plugin tests
      test_nox_plugin.py               # nox plugin tests
      test_scnsrc_plugin.py            # scnsrc plugin tests
      test_serienfans_plugin.py        # serienfans plugin tests
      test_serienjunkies_plugin.py     # serienjunkies plugin tests
      test_sto_plugin.py               # sto plugin tests
      test_streamcloud_plugin.py       # streamcloud plugin tests
      test_streamkiste_plugin.py       # streamkiste plugin tests
      test_streamworld_plugin.py       # streamworld plugin tests
      test_warezomen_plugin.py         # warezomen plugin tests
```

Important mock patterns:
- `PluginRegistryPort` is **synchronous** → use `MagicMock` (not `AsyncMock`).
- `SearchEnginePort`, `CrawlJobRepository`, `CachePort` are **async** → use `AsyncMock`.

### TDD loop (mandatory for agents)
1. Write test first (precise acceptance, small scope).
2. Run test (must be red).
3. Implement minimally (green).
4. Refactor (stay green).
5. Checkpoint commit (small, auditable).

***

## 13. Python best practices (MUST READ!)

Zen of Python (PEP 20): explicit is better than implicit; simple is better than complex; readability counts.

### Architecture patterns for AI collaboration
- Atomic task pattern: tasks at file/function level (“Convert X to asyncio” instead of “Make faster”).
- Functional over OOP: prefer functions/small modules over deep class hierarchies (fewer side effects).
- Dependency injection: dependencies explicit via constructors/factory functions.

### Typing standards (modern Python 3.10+ syntax, MANDATORY)

ALWAYS use modern syntax everywhere (classes, functions, variables, return types, parameters):

```python
from __future__ import annotations   # ALWAYS include in every file

# ✅ correct
def process(items: list[str], default: int | None = None) -> dict[str, int]: ...

# ❌ wrong (legacy typing)
from typing import List, Optional, Dict
def process(items: List[str], default: Optional[int] = None) -> Dict[str, int]: ...
```

| Modern syntax | Legacy (forbidden) |
|---|---|
| `T \| None` | `Optional[T]` |
| `list[T]` | `List[T]` |
| `dict[K, V]` | `Dict[K, V]` |
| `set[T]` | `Set[T]` |
| `tuple[T, ...]` | `Tuple[T, ...]` |
| `collections.abc.Iterable` | `typing.Iterable` |

Additional rules:
- Fully type all function signatures (parameters + return types).
- Only import from `typing`: `Any`, `Protocol`, `Literal`, `TypeVar`, `runtime_checkable`.
- All ports/interfaces use `Protocol` (not `ABC`).
- Use `@dataclass` for entities and value objects (`frozen=True` for immutables).

### Dignified Python (safety rules)
- No mutable default arguments (`def f(x=[]): ...` is forbidden).
- Don't swallow exceptions (`except: pass` is forbidden); log + re-raise or cleanly map.
- Take type hints seriously: use `Literal` for fixed values; casts only with runtime checks.

### Async/await: non-blocking I/O is mandatory

```python
# ✅ correct: parallel (bounded elsewhere)
tasks = [fetch(url) for url in urls]
pages = await asyncio.gather(*tasks)

# ❌ wrong: sequential (blocks)
pages = []
for url in urls:
    pages.append(await fetch(url))
```

When CPU-bound parsing is unavoidable, move it out of the event loop (`run_in_executor`) to avoid blocking everything.

### Scrapy-specific patterns
- Selectors: as specific as needed, as robust as possible (don’t match too broadly).
- URL handling: `urljoin` instead of string concatenation.
- Degradation: missing fields → partial result + warning, not a full abort.

### Playwright-specific patterns
- No `sleep()` delays as “waiting”; use conditions/events (`wait_until="networkidle"`, locators).
- Resources: close contexts/pages deterministically (avoid leaks).
- Concurrency: strictly limit browser parallelism (semaphore) to avoid RAM spikes.

### Mutable default arguments (classic Python gotcha)

```python
# ❌ wrong: mutable default shared between calls
def add_item(item: str, items: list[str] = []) -> list[str]:
    items.append(item)
    return items

# ✅ correct: None default + factory
def add_item(item: str, items: list[str] | None = None) -> list[str]:
    if items is None:
        items = []
    items.append(item)
    return items
```

***

## 14. Team Agents (when to use, when not to)

### When to use agents
Agents are ONLY for **simple, explicit, mechanical tasks** where the scope is 100% clear:
- Adding the same attribute to many files (e.g., `default_language = "de"` to 28 plugins)
- Renaming a variable across a codebase
- Generating boilerplate from a precise spec
- Running isolated, well-defined subtasks that don't require project context

### When NOT to use agents (do it yourself!)
**Any task that requires understanding project architecture, cross-file dependencies, or design decisions MUST be done manually.** This includes:
- Use cases, domain logic, application services
- Refactoring that spans multiple layers (router → use case → infrastructure)
- Functions/classes that interact with ports, protocols, or DI wiring
- Test updates that require understanding mock patterns and dispatch logic
- Any code where a wrong decision cascades into other files

**Rule of thumb:** If the task needs project overview → do it yourself. If it's purely mechanical → agent is OK.

### Task assignment (when agents are used)
- **1 file = 1 agent** — Never assign two agents to modify the same file.
- **Tight, explicit scope** — Define exactly what the agent should do AND what it should not.
- **Include project conventions in the prompt** — Agents don't know codebase style. Always specify: `structlog` (not `logging`), typing conventions, ID formats, naming patterns, etc.

### Quality control
- **Review is mandatory** — Every agent output must be reviewed before committing.
- **Don't trust, verify** — Run full test suite + pre-commit after merging agent outputs.
- **Don't wait for notifications** — Actively check output files instead of waiting for async notifications.

***

## 15. Development workflow (general)

### Quick commands (examples)
- `poetry install`
- `poetry run pytest`
- `poetry run ruff check .` and `poetry run ruff format .`

### Checkpoint commits
- Commit after each isolated subtask (audit trail).
- Avoid “mega-commits”; prefer small, green-tested steps.

### Planning mode (for larger changes)
- Before major refactors: write a brief Markdown plan (problem, design, affected files, tests).
- Then implement.

***

## 16. Important files (project navigation)

| Area | Path (example/pattern) |
|---|---|
| Dependencies & tooling | `pyproject.toml` |
| Pre-commit configuration | `.pre-commit-config.yaml` |
| Changelog | `CHANGELOG.md` |
| Domain entities/ports | `src/scavengarr/domain/...` |
| Use cases | `src/scavengarr/application/...` |
| Adapters (scraping/cache/plugins) | `src/scavengarr/infrastructure/...` |
| Plugin base classes | `src/scavengarr/infrastructure/plugins/httpx_base.py`, `playwright_base.py` |
| Hoster resolvers | `src/scavengarr/infrastructure/hoster_resolvers/` |
| HTTP router / CLI | `src/scavengarr/interfaces/...` |
| Stremio addon | `src/scavengarr/interfaces/api/stremio/` |
| Tests | `tests/unit/{domain,application,infrastructure}/...` |
| Feature documentation | `docs/features/` (README.md is the index) |
| Architecture documentation | `docs/architecture/` (clean-architecture.md, codeplan.md) |
| Future plans | `docs/plans/` (playwright-engine, more-plugins, integration-tests, search-caching) |
| Refactor history | `docs/refactor/COMPLETED/` |
| Python best practices | `docs/PYTHON-BEST-PRACTICES.md` |
| Plugins (39 total) | `plugins/` (3 YAML + 36 Python, all inheriting from base classes) |
| OpenSpec change specs | `openspec/changes/...` |

### Adding a new plugin (general workflow)

**Step 1: Thorough site analysis (MANDATORY before writing any code)**
- Use Playwright MCP to visit and inspect all relevant pages (search, categories, detail pages, download pages)
- Document HTML structure precisely (selectors, tables, link patterns, pagination)
- Check for JS dependencies (Cloudflare, dynamic loading, SPAs)
- Identify auth mechanisms (login, cookies, tokens)
- Map URL patterns (search, detail, download)

**Step 2: Decide YAML vs Python**
- Default: YAML plugin (Scrapy) — faster, simpler, preferred
- Python plugin only when YAML is technically impossible (Cloudflare/JS challenge, complex logic, auth, API calls, non-standard table structures)
- Justification for Python plugin must be stated explicitly in the plan

**Step 2b: Use the correct base class**
- Httpx plugins: inherit from `HttpxPluginBase` (`src/scavengarr/infrastructure/plugins/httpx_base.py`)
  - Provides: `_ensure_client()`, `_verify_domain()`, `cleanup()`, `_safe_fetch()`, `_safe_parse_json()`, `_new_semaphore()`
  - Class attributes: `_domains`, `_max_concurrent` (default 3), `_max_results` (default 1000), `_timeout` (default 15), `_user_agent`
  - Instance: `self._client`, `self._log`, `self.base_url`, `self._domain_verified`
- Playwright plugins: inherit from `PlaywrightPluginBase` (`src/scavengarr/infrastructure/plugins/playwright_base.py`)
  - Provides: `_ensure_browser()`, `_ensure_context()`, `_ensure_page()`, `_new_page()`, `_verify_domain()`, `_fetch_page_html()`, `cleanup()`
  - Instance: `self._pw`, `self._browser`, `self._context`, `self._page`, `self._log`, `self.base_url`
- All Python plugins MUST inherit from one of these base classes. Do NOT duplicate boilerplate (client setup, domain fallback, cleanup, semaphore, user-agent).

**Step 3: Implement (MANDATORY search standards for ALL plugins)**

Every plugin MUST implement the following search features:

1. **Category filtering**: Use the site's category/filter system in the search URL whenever available (dropdown IDs, URL path segments, forum IDs, etc.). Map Torznab categories → site categories and pass them in the search request.
2. **Pagination up to 1000 items**: Scrape multiple search result pages to collect up to 1000 items total. Parse pagination links/hit counts from the first page to determine how many pages exist, then fetch subsequent pages sequentially until 1000 items or no more results. Define `_MAX_PAGES` based on the site's results-per-page (e.g., 200/page → 5 pages, 50/page → 20 pages, 10/page → 100 pages).
3. **Bounded concurrency** for detail page scraping: Use `asyncio.Semaphore(3)` to scrape detail pages in parallel without overwhelming the target.

#### Adding a new Python plugin (httpx)
1. Create `plugins/<sitename>.py`, inherit from `HttpxPluginBase`.
2. Set `name`, `_domains = [...]`, and optionally override `_max_results`, `_max_concurrent`, `categories`.
3. Implement `async def search(self, query, category, season, episode) -> list[SearchResult]`.
4. Use `self._safe_fetch()` for HTTP requests, `self._new_semaphore()` for concurrency, `self._log` for logging.
5. Add comprehensive unit tests in `tests/unit/infrastructure/test_<sitename>_plugin.py`.

#### Adding a new Python plugin (Playwright)
1. Create `plugins/<sitename>.py`, inherit from `PlaywrightPluginBase`.
2. Set `name`, `_domains = [...]`, add `from playwright.async_api import Page` if using `Page` type hints.
3. Implement `async def search(self, query, category, season, episode) -> list[SearchResult]`.
4. Use `self._ensure_context()` / `self._ensure_page()` for browser management, `self._new_semaphore()` for concurrency.
5. Add comprehensive unit tests; patch `async_playwright` at `scavengarr.infrastructure.plugins.playwright_base.async_playwright`.

#### Adding a new YAML plugin
1. Place the YAML file in the plugin dir (configurable via `SCAVENGARR_PLUGIN_DIR`).
2. Set minimal fields: `name`, `base_url`, `scraping.mode`, `stages[]`, `categories`.
3. Stage 1 outputs `detail_url` (or directly a terminal `download_url`), stage 2+ outputs `download_url`.
4. Test locally (CLI), then add an integration test with fixtures.

### Adding a new stage (multi-stage)
1. Decide: intermediate or terminal.
2. Keep selectors minimal (extract only what you need later).
3. Verify concurrency limits so the stage runs in parallel but does not exhaust resources.
