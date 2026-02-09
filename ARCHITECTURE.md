# ARCHITECTURE.md

Instructions for developers and AI assistants (Claude, GPT, etc.) working on **Scavengarr**.

***

## 1. Project Overview

Scavengarr is a self-hosted, container-ready Torznab/Newznab indexer for Prowlarr and other Arr applications.
The system scrapes sources via two engines (Scrapy for static HTML, Playwright for JavaScript-heavy sites) and delivers results through Torznab endpoints like `caps` and `search`.

### Core Ideas (Target Architecture)
- Plugin-driven: YAML (declarative) and Python (imperative) plugins define site-specific logic without touching core code.
- Dual Engine: Scrapy + Playwright are equal-weight backends, selection per plugin/stage depends on "JS-heavy" classification.
- Multi-Stage Scraping is a core feature: "Search → Detail → Links" is the norm, not the exception.
- I/O dominates runtime: Architecture and code must be non-blocking (no mutual blocking).
- CrawlJob System: multiple validated links are bundled into a `.crawljob` file (multi-link packaging).

***

## 2. Clean Architecture (Dependency Rule)

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

**Dependency Rule**: Inner layers never know about outer layers.  
✅ Application imports Domain  
✅ Infrastructure implements Domain Protocols  
❌ Domain NEVER imports FastAPI, httpx, diskcache

***

## 3. Architecture Layers (Organization)

Note: This structure is the target architecture; parts of the current organization may be discarded.  
Currently, Clean Architecture namespace blocks exist as top-level packages under `src/scavengarr/` (including `domain/`, `application/`, `infrastructure/`, `interfaces/`).

### Domain (Enterprise Business Rules)
- **Entities**: long-lived business objects with identity (e.g., SearchResult, CrawlJob, Query objects).
- **Value Objects**: immutable values (e.g., plugin configuration, categories, query parameters).
- **Protocols (Ports)**: abstract contracts (ScrapingEngine, PluginRegistry, LinkValidator, Cache).

Rule: Domain is framework-free, async-free (when possible), and I/O-free.

### Application (Application Business Rules)
- **Use Cases**: orchestrate the flow "Query → Plugin → Scrape → Validate → Present".
- **Factories**: build domain objects consistently (IDs, TTL, normalization).
- **Policies**: quotas, limits, timeouts, retries (as rules, not framework code).

Rule: Application knows Ports (Protocols) but not concrete Adapters.

### Infrastructure (Interface Adapters)
- **Plugins**: discovery/loading/validation for YAML & Python plugins.
- **Scraping Adapters**: Scrapy/Playwright implementations of ScrapingEngine.
- **Validation**: HTTP Link Validator (HEAD/GET strategies, redirects, parallelism).
- **Cache**: diskcache adapter (Redis optional only if already present).
- **Torznab Rendering/Presentation**: XML generation, field mapping, attribute handling.

Rule: Infrastructure may use external libraries but must connect to Application via Ports.

### Interfaces (Frameworks & Drivers)
- **HTTP (FastAPI Router)**: request parsing, response formatting, error mapping.
- **CLI (Typer)**: local debugging/diagnostics/plugin checks.
- **Composition Root**: dependency injection and wiring.

Rule: Interfaces contain no business rules, only input/output.

***

## 4. Technology Stack (Dependencies)

The source of truth for dependencies is `pyproject.toml`.  
Scavengarr uses FastAPI/Uvicorn, Scrapy, Playwright, structlog, diskcache, Typer, pydantic-settings, httpx, and optionally Redis.

### Dependency Principles
- As few third-party dependencies as reasonable: check standard library first, then established libraries, only then build custom solutions.
- Prefer established libraries over building parallel "mini-frameworks" in the project.
- New dependencies only with explicit justification (security, maintainability, tests, API stability).

***

## 5. Core Components (Terminology)

| Term | Brief Description |
|---|---|
| Torznab Query | Normalized input (e.g., `t=search`, `q=...`, categories, extended). |
| Plugin | Describes how to scrape (YAML: declarative; Python: imperative). |
| Stage | One step in the pipeline (e.g., `search_results`, `movie_detail`). |
| SearchResult | Domain entity: a found item, including metadata and links. |
| Link Validation | I/O-heavy filter that removes dead/blocked links. |
| CrawlJob | Bundle of multiple validated links in `.crawljob` (multi-link packaging). |
| Presenter/Renderer | Translates domain results into Torznab XML (Prowlarr-compatible). |

***

## 6. Request Flow (High-Level)

Goal: HTTP/CLI only provide input/output; the use case orchestrates; adapters perform I/O.

1. Request arrives (HTTP `caps/search/...` or CLI).
2. Use case loads plugin from registry (lazy).
3. Scraping engine executes multi-stage pipeline (Scrapy or Playwright).
4. Link validator checks links in parallel (not sequentially).
5. CrawlJob generates `.crawljob` for multiple links (if feature active).
6. Presenter renders Torznab XML response.

***

## 7. Configuration System (Precedence + Logging)

### Precedence (High → Low)
1. CLI Arguments (e.g., `--config`, `--plugin-dir`, `--log-level`)
2. Environment Variables (`SCAVENGARR_*`)
3. YAML Config File
4. `.env` (optional)
5. Defaults (in code)

### Configuration Categories (incomplete)
| Category | Typical Contents | Purpose |
|---|---|---|
| General | environment, base_url, app_name | deterministic behavior |
| Plugins | plugin_dir, discovery rules | reproducible plugin loading |
| Scraping | timeouts, user agent, redirects | stable requests |
| Playwright | headless, navigation timeouts, concurrency | controlled resources |
| Validation | HEAD/GET policy, timeouts, parallel limits | fast filtering |
| Cache | backend, ttl, storage path | less I/O |
| Logging | level, format (json/console), correlation fields | observability without noise |

### Logging (as Configuration Topic)
- Logs are structured (JSON/Console depending on environment) and contain context fields like `plugin`, `stage`, `duration_ms`, `results_count`.
- Logging must never output secrets from config/env (masking/redaction).

***

## 8. Plugin System & Multi-Stage Scraping

### Plugin Types
- **YAML Plugins**: declarative scraping (selector mapping, stages, URL templates).
- **Python Plugins**: imperative logic for complex flows (auth, APIs, special cases).

### Plugin Discovery & Loading (Agent-relevant)
- **Discovery**: registry scans plugin dir for `.yaml` and `.py`.
- **Lazy Loading**: plugins are only parsed/imported on first access.
- **Caching**: once loaded, plugins remain in process cache.

### YAML Plugin (Multi-Stage Example)

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

### Multi-Stage Execution (Semantics)
- A stage can be "intermediate" (produces URLs for next stage) or "terminal" (produces SearchResults).
- Within a stage, independent URLs are processed in parallel (bounded concurrency) to prevent requests from blocking each other.
- Stages should remain deterministic and testable (selectors + normalization); encapsulate "edge cases" in Python plugins.

***

## 9. Link Validation Strategy (Non-Blocking)

Link validation is I/O-dominant and must run in parallel.  
Rule: no sequential URL checking in loops when parallelism is possible.

### Recommended Policies
- Primary `HEAD` with redirects; fallback `GET` only when necessary (for hosters that block HEAD).
- Keep timeout short, limit parallelism (semaphore), log results cleanly.
- Status-based decision (example): `200` ok; `403/404/timeout` invalid, potentially configurable per plugin.

***

## 10. CrawlJob System (Multi-Link Packaging)

CrawlJob is a domain concept: a job bundles multiple validated links into a `.crawljob` artifact.  
The system provides a stable download endpoint that delivers a `.crawljob` file from a job.

### Rules
- Job ID is stable, TTL is configurable, storage is interchangeable (cache port).
- `.crawljob` contains multiple links; order is deterministic (stable for tests).
- Never write unvalidated links into CrawlJobs (policy: validate-first).

***

## 11. Testing Strategy (TDD + Layers)

### Test Layering
- **Unit**: Domain (pure), Application (use cases with mocks/fakes), Infrastructure (parser/mapping).
- **Integration**: HTTP Router ↔ Use Case ↔ Adapter with HTTP mocking.
- **Optional E2E**: Real plugin fixtures, but deterministic (no external sites in CI).

### TDD-Loop (Mandatory for Agents)
1. Write test first (precise acceptance, small scope).
2. Run test (must be red).
3. Implement minimally (green).
4. Refactor (stay green).
5. Checkpoint commit (small, auditable).

***

## 12. Python Best Practices (MUST READ!)

> Zen of Python (PEP 20): Explicit is better than implicit. Simple is better than complex. Readability counts.

### Architecture Patterns for AI Collaboration
- **Atomic Task Pattern**: formulate tasks at file/function level ("Convert X to asyncio" instead of "Make faster").
- **Functional over OOP**: prefer functions/small modules over deep class hierarchies (fewer side effects).
- **Dependency Injection**: dependencies explicit via constructors/factory functions.

### Dignified Python (Safety Rules)
- No mutable default arguments (`def f(x=[]): ...` is forbidden).
- Don't swallow exceptions (`except: pass` is forbidden); log + re-raise or cleanly map.
- Take type hints seriously: `Literal` for fixed values; casts only with runtime check.

### Async/Await: Non-Blocking I/O is Mandatory

```python
# ✅ CORRECT: parallel, bounded
tasks = [fetch(url) for url in urls]
pages = await asyncio.gather(*tasks)

# ❌ WRONG: sequential (blocks)
pages = []
for url in urls:
    pages.append(await fetch(url))
```

When CPU-bound parsing is unavoidable, it must be moved out of the event loop (`run_in_executor`), otherwise it blocks everything.

### Scrapy-Specific Patterns
- **Selectors**: as specific as necessary, as robust as possible (don't match "too broadly").
- **URL Handling**: `urljoin` instead of string concatenation.
- **Degradation**: missing fields → partial result + warning, not complete abort.

```python
# ✅ CORRECT: specific selector (more stable)
row_selector = "div.search-results > div.movie-item"

# ❌ WRONG: too broad selector (matches irrelevant elements)
row_selector = "div.movie-item"  # Could also match sidebar items!
```

```python
# ✅ CORRECT: graceful degradation
def extract_title(row: parsel.Selector) -> str | None:
    title = row.css("h2.title::text").get()
    if not title:
        log.warning("title_selector_no_match", html=row.get())
        return None  # Partial result OK
    return title.strip()

# ❌ WRONG: exception on every error (loses all results!)
def extract_title(row: parsel.Selector) -> str:
    return row.css("h2.title::text").get().strip()  # Raises AttributeError!
```

```python
from urllib.parse import urljoin

# ✅ CORRECT: handles relative/absolute URLs
full_url = urljoin(base_url, relative_url)

# ❌ WRONG: breaks with absolute URLs
full_url = f"{base_url}{relative_url}"  # Double scheme with absolute URLs!
```

### Playwright-Specific Patterns
- **No `sleep()` delays** as "waiting"; instead use events/conditions (`wait_until="networkidle"` etc.).
- **Resources**: close contexts/pages deterministically (avoid leaks).
- **Concurrency**: strictly limit browser parallelism (semaphore), otherwise RAM spikes.

```python
# ✅ CORRECT: wait for network stability
await page.goto(url, wait_until="networkidle")

# ❌ WRONG: fixed delays (unreliable + slow)
await page.goto(url)
await asyncio.sleep(2)  # What if page needs 3s?
```

```python
# ✅ CORRECT: context closed after use
async with await browser.new_context() as context:
    page = await context.new_page()
    # ... scraping ...
    # Context automatically closed!

# ❌ WRONG: context not closed
context = await browser.new_context()
page = await context.new_page()
# ... Memory leak with many requests!
```

```python
# ✅ CORRECT: CSS selector (faster)
await page.locator("div.movie-item").first.click()

# ⚠️ SLOWER: XPath (only when CSS not possible)
await page.locator("xpath=//div[@class='movie-item']").click()
```

### Mutable Default Arguments (Classic Python Gotcha)

```python
# ❌ WRONG: mutable default shared between calls!
def add_item(item: str, items: list[str] = []) -> list[str]:
    items.append(item)
    return items

result1 = add_item("a")  # ["a"]
result2 = add_item("b")  # ["a", "b"]  ❌ Unexpected!

# ✅ CORRECT: None as default + factory pattern
def add_item(item: str, items: list[str] | None = None) -> list[str]:
    if items is None:
        items = []
    items.append(item)
    return items

result1 = add_item("a")  # ["a"]
result2 = add_item("b")  # ["b"]  ✅ Correct!

# ✅ ALTERNATIVE: dataclass with field(default_factory)
@dataclass
class Container:
    items: list[str] = field(default_factory=list)
```

***

## 13. Development Workflow (AI-Agent Friendly)

### Quick Commands (Examples)
- `poetry install`
- `poetry run pytest`
- `poetry run ruff check .` and `poetry run ruff format .`
- CLI entry exists as Poetry script (`start = ...`) in `pyproject.toml`.

### Checkpoint Commits
- Commit after each isolated subtask (audit trail).
- No "mega-commits" with 20 changes; prefer 5 small, green-tested steps.

### Planning Mode (for larger changes)
- Before major refactors: brief Markdown plan (problem, design, affected files, tests).
- Then implementation.

***

## 14. Important Files (Project Navigation)

| Area | Path (Example/Pattern) |
|---|---|
| Dependencies & Tooling | `pyproject.toml` |
| Domain Entities/Ports | `src/scavengarr/domain/...` |
| Use Cases | `src/scavengarr/application/...` |
| Adapters (Scraping/Cache/Plugins) | `src/scavengarr/infrastructure/...` |
| HTTP Router / CLI | `src/scavengarr/interfaces/...` |

### Adding a New YAML Plugin
1. Place YAML file in plugin dir (configurable via `SCAVENGARR_PLUGIN_DIR`).
2. Set minimal fields: `name`, `base_url`, `scraping.mode`, `stages[]`, `categories`.
3. Stage 1 delivers `detail_url` (or directly terminal `download_url`), stage 2+ delivers `download_url`.
4. Test plugin locally (CLI), then add integration test with fixture.

### Adding a New Stage (Multi-Stage)
1. Decide: intermediate or terminal.
2. Keep selector set minimal (only extract fields needed later).
3. Check concurrency limit so stage runs in parallel but doesn't exhaust resources.

***

**Last Updated**: 2026-02-09  
**Author**: Scavengarr Team  
**Architecture Pattern**: Clean Architecture