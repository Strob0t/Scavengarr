[← Back to Index](./README.md)

# Plugin System

> All 40 Scavengarr plugins are Python-based, inheriting from `HttpxPluginBase` (for static HTML / API sites) or `PlaywrightPluginBase` (for JS-heavy sites requiring browser automation).

---

## Overview

Scavengarr is plugin-driven. Each plugin defines how to scrape a specific indexer source site. Plugins implement the `PluginProtocol` directly and handle their own scraping logic, including multi-stage pipelines, authentication, pagination, and domain fallback.

Key characteristics:
- **Python-only:** All plugins are `.py` files implementing `PluginProtocol`
- **Two base classes:** `HttpxPluginBase` (31 plugins) and `PlaywrightPluginBase` (9 plugins)
- **Lazy-loaded:** Parsed only when first accessed, cached in memory afterward
- **Multi-stage:** Plugins implement search-to-detail pipelines in their `search()` method
- **Bounded concurrency:** Semaphore-limited parallel page scraping

---

## Plugin Discovery & Loading

### Discovery Flow

```
Server startup
  |
  v
PluginRegistry.discover()
  |-- Scans plugin_dir for .py files
  |-- Indexes files by path (NO importing yet)
  |-- Logs: "plugins_discovered count=N"
  |
  v
First request for plugin "filmpalast"
  |
  v
PluginRegistry.get("filmpalast")
  |-- Dynamically imports .py file via importlib
  |-- Validates: must export 'plugin' variable
  |-- Validates: must have 'search' method and non-empty 'name'
  |-- Caches in memory
  |-- Returns plugin instance
```

### Key Behaviors

1. **File scan only at startup:** `discover()` reads no file contents, just indexes paths
2. **Lazy loading:** Plugins are imported only on first `get()` call
3. **In-memory caching:** Once loaded, the plugin stays cached for the process lifetime
4. **Name peeking:** `list_names()` does a lightweight import to read the `name` attribute
5. **Duplicate detection:** `load_all()` raises `DuplicatePluginError` if two plugins share a name

### Plugin Directory

The default plugin directory is `./plugins`. Override via configuration:

```bash
# Environment variable
export SCAVENGARR_PLUGIN_DIR=/path/to/plugins

# CLI argument
start --plugin-dir /path/to/plugins
```

Supported file extension: `.py` (Python plugins).

---

## PluginProtocol

Every plugin must satisfy the `PluginProtocol` defined in the domain layer:

```python
# src/scavengarr/domain/plugins/base.py
class PluginProtocol(Protocol):
    name: str
    provides: str  # "stream" or "download"

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]: ...
```

### Contract Requirements

| Requirement | Details |
|---|---|
| Module-level `plugin` variable | The `.py` file must export a variable named `plugin` |
| `name: str` attribute | Non-empty string, used as the plugin identifier |
| `provides: str` attribute | `"stream"` (streaming links) or `"download"` (DDL links) |
| `async def search(...)` method | Returns `list[SearchResult]` |
| `query: str` parameter | The search term from the Torznab query |
| `category: int \| None` parameter | Optional Torznab category ID for filtering |
| `season: int \| None` parameter | Optional season number for TV content |
| `episode: int \| None` parameter | Optional episode number for TV content |

---

## Plugin Base Classes

### HttpxPluginBase

For sites with JSON APIs or server-rendered HTML (no JavaScript needed):

```python
# plugins/my_site.py
from __future__ import annotations

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

_DOMAINS = ["my-site.com", "my-site.org"]

class MySitePlugin(HttpxPluginBase):
    name = "my-site"
    provides = "stream"
    _domains = _DOMAINS

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        await self._ensure_client()
        resp = await self._safe_fetch(
            f"{self.base_url}/api/search",
            params={"q": query},
            context="search",
        )
        if resp is None:
            return []
        # ... build SearchResult list ...
        return []

plugin = MySitePlugin()
```

**Provided by HttpxPluginBase:**
- `_ensure_client()` -- creates/reuses httpx.AsyncClient with proper headers
- `_verify_domain()` -- tries each domain in `_domains` until one responds
- `_safe_fetch(url, **kwargs)` -- GET/POST with error handling, returns `None` on failure
- `_safe_parse_json(resp)` -- JSON parsing with error handling
- `_new_semaphore()` -- creates `asyncio.Semaphore` with `_max_concurrent` limit
- `cleanup()` -- closes httpx client
- `base_url` property -- `https://{first working domain}`

### PlaywrightPluginBase

For sites requiring JavaScript execution or Cloudflare bypass:

```python
# plugins/my_js_site.py
from __future__ import annotations

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.playwright_base import PlaywrightPluginBase

_DOMAINS = ["my-js-site.com"]

class MyJsSitePlugin(PlaywrightPluginBase):
    name = "my-js-site"
    provides = "stream"
    _domains = _DOMAINS

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        await self._ensure_browser()
        await self._ensure_context()
        page = await self._context.new_page()
        try:
            await page.goto(f"{self.base_url}/search?q={query}")
            html = await page.content()
            # ... parse HTML, build results ...
            return []
        finally:
            if not page.is_closed():
                await page.close()

plugin = MyJsSitePlugin()
```

**Provided by PlaywrightPluginBase:**
- `_ensure_browser()` / `_ensure_context()` -- Chromium lifecycle
- `_verify_domain()` -- domain fallback with Cloudflare wait
- `_wait_for_cloudflare(page)` -- waits for JS challenge to complete
- `_fetch_page_html(url)` -- navigates and returns page HTML
- `cleanup()` -- closes context, browser, and Playwright

---

## Plugin Search Standards

Every plugin MUST implement these search features:

### 1. Category Filtering

Use the site's category/filter system in the search URL whenever available. Map Torznab categories to site categories:

```python
_CATEGORY_MAP: dict[int, str] = {
    2000: "movies",    # Torznab Movies -> site category
    5000: "tv",        # Torznab TV -> site category
}
```

### 2. Pagination (up to 1000 items)

Scrape multiple search result pages to collect up to 1000 items total:

```python
_MAX_PAGES = 50  # Based on site's results-per-page

for page_num in range(1, _MAX_PAGES + 1):
    resp = await self._safe_fetch(f"{search_url}&page={page_num}")
    if resp is None or not items:
        break
    all_results.extend(items)
    if len(all_results) >= self._max_results:
        break
```

### 3. Bounded Concurrency

Use `asyncio.Semaphore` for parallel detail page scraping:

```python
sem = self._new_semaphore()  # default: 3 concurrent

async def _bounded_scrape(url: str) -> SearchResult | None:
    async with sem:
        return await self._scrape_detail(url)

results = await asyncio.gather(
    *[_bounded_scrape(url) for url in detail_urls],
    return_exceptions=True,
)
```

---

## Creating a New Plugin

### Step-by-Step

1. **Inspect the target site** using browser DevTools
   - Identify the search URL pattern and HTML structure
   - Check for JS dependencies (Cloudflare, dynamic loading)
   - Decide: `HttpxPluginBase` (static HTML) or `PlaywrightPluginBase` (JS-heavy)

2. **Create the plugin file** in the plugin directory:
   ```bash
   touch plugins/my_site.py
   ```

3. **Implement the plugin:**
   - Inherit from appropriate base class
   - Set `name`, `_domains`, `provides`
   - Implement `search()` with category filtering, pagination, and bounded concurrency

4. **Write tests** in `tests/unit/infrastructure/test_my_site_plugin.py`

5. **Restart the server** to trigger plugin discovery

6. **Test the plugin:**
   ```bash
   curl "http://localhost:7979/api/v1/torznab/my-site?t=search&q=test"
   ```

---

## Plugin Exceptions

The plugin system defines a hierarchy of exceptions:

```python
# src/scavengarr/domain/plugins/exceptions.py
class PluginError(Exception):           # Base class
class PluginValidationError(PluginError): # Plugin validation failure
class PluginLoadError(PluginError):       # Python import/protocol failure
class PluginNotFoundError(PluginError):   # Name not in registry
class DuplicatePluginError(PluginError):  # Two plugins with same name
```

| Exception | Trigger |
|---|---|
| `PluginLoadError` | Module has no `plugin` variable, no `search` method, or empty `name` |
| `PluginLoadError` | SyntaxError or ImportError during module import |
| `PluginNotFoundError` | `registry.get("unknown-name")` with no matching file |
| `DuplicatePluginError` | Two files resolve to the same plugin name during `load_all()` |

---

## Source Code References

| Component | Path |
|---|---|
| PluginProtocol definition | `src/scavengarr/domain/plugins/base.py` |
| SearchResult dataclass | `src/scavengarr/domain/plugins/base.py` |
| Plugin exceptions | `src/scavengarr/domain/plugins/exceptions.py` |
| HttpxPluginBase | `src/scavengarr/infrastructure/plugins/httpx_base.py` |
| PlaywrightPluginBase | `src/scavengarr/infrastructure/plugins/playwright_base.py` |
| Plugin constants | `src/scavengarr/infrastructure/plugins/constants.py` |
| Python plugin loader | `src/scavengarr/infrastructure/plugins/loader.py` |
| Plugin registry | `src/scavengarr/infrastructure/plugins/registry.py` |
| Search use case (dispatch) | `src/scavengarr/application/use_cases/torznab_search.py` |
| Reference httpx plugin | `plugins/filmpalast_to.py` |
| Reference Playwright plugin | `plugins/boerse.py` |
