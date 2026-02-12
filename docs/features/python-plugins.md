[← Back to Index](./README.md)

# Python Plugins

> Imperative plugins for complex scraping scenarios that require full programmatic control: authentication flows, Cloudflare bypass, custom parsing, and multi-domain fallback.

---

## Overview

Python plugins complement YAML plugins for sites where declarative CSS selectors are insufficient. They implement the `PluginProtocol` directly and handle their own scraping logic using any Python library (Playwright, httpx, custom parsers).

**When to use a Python plugin:**
- The site requires JavaScript execution (Cloudflare challenge, SPA)
- Authentication is non-standard (MD5 hashed passwords, multi-step login, CAPTCHA)
- The page structure cannot be captured with CSS selectors alone
- Domain fallback logic is needed beyond simple mirror lists
- Custom post-processing or link extraction is required

**When to use a YAML plugin instead:**
- The site is static HTML with predictable structure
- Standard CSS selectors can extract all needed data
- No authentication or simple auth types suffice
- You want the simplicity of a declarative definition

---

## PluginProtocol

Every Python plugin must satisfy the `PluginProtocol` defined in the domain layer:

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

### SearchResult Entity

The `search()` method must return a list of `SearchResult` objects:

```python
# src/scavengarr/domain/plugins/base.py
@dataclass
class SearchResult:
    title: str                                    # Required: result title
    download_link: str                            # Required: primary download URL

    # Torznab standard fields
    seeders: int | None = None
    leechers: int | None = None
    size: str | None = None

    # Extended fields
    release_name: str | None = None
    description: str | None = None
    published_date: str | None = None

    # Multi-stage specific
    download_links: list[dict[str, str]] | None = None  # All download links
    source_url: str | None = None                       # Page the result was scraped from
    scraped_from_stage: str | None = None

    # Post-validation
    validated_links: list[str] | None = None

    # Metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    # Torznab-specific
    category: int = 2000                          # Default: Movies
    grabs: int = 0
    download_volume_factor: float = 0.0
    upload_volume_factor: float = 0.0
```

**Minimum required fields:** `title` and `download_link`. All other fields are optional and enhance the Torznab response when populated.

---

## Plugin Base Classes

Most plugins should extend one of the shared base classes instead of implementing everything from scratch. The base classes provide client lifecycle, domain fallback, bounded concurrency, and error handling.

### HttpxPluginBase (20 plugins use this)

For sites with JSON APIs or server-rendered HTML (no JavaScript needed):

```python
# plugins/my_site.py
from __future__ import annotations

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["my-site.com", "my-site.org"]  # Fallback domains
_MAX_PAGES = 50

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

        # Use self._safe_fetch() for HTTP requests
        resp = await self._safe_fetch(
            f"{self.base_url}/api/search",
            params={"q": query},
            context="search",
        )
        if resp is None:
            return []

        data = self._safe_parse_json(resp, context="search")
        # ... build SearchResult list ...
        return []

plugin = MySitePlugin()
```

**Provided by HttpxPluginBase:**
- `_ensure_client()` — creates/reuses httpx.AsyncClient with proper headers
- `_verify_domain()` — tries each domain in `_domains` until one responds
- `_safe_fetch(url, **kwargs)` — GET/POST with error handling, returns `None` on failure
- `_safe_parse_json(resp)` — JSON parsing with error handling
- `_new_semaphore()` — creates `asyncio.Semaphore` with `_max_concurrent` limit
- `cleanup()` — closes httpx client
- `base_url` property — `https://{first working domain}`

### PlaywrightPluginBase (9 plugins use this)

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
- `_ensure_browser()` / `_ensure_context()` — Chromium lifecycle
- `_verify_domain()` — domain fallback with Cloudflare wait
- `_wait_for_cloudflare(page)` — waits for JS challenge to complete
- `_fetch_page_html(url)` — navigates and returns page HTML
- `cleanup()` — closes context, browser, and Playwright

### Plugin Settings Organization

All plugins follow a standard layout with configurable settings at the top:

```python
# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["site.com", "site.org"]
_MAX_PAGES = 50
_PER_PAGE = 20

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_CATEGORY_MAP = { ... }
_LANG_LABELS = { ... }
```

## Plugin Skeleton (without base class)

### Minimal Plugin

```python
# plugins/my_site.py
from __future__ import annotations

from scavengarr.domain.plugins.base import SearchResult


class MySitePlugin:
    name = "my-site"
    provides = "stream"

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        # Your scraping logic here
        return []


# REQUIRED: module-level plugin variable
plugin = MySitePlugin()
```

---

## Plugin Loading Internals

### How Python Plugins Are Loaded

```python
# src/scavengarr/infrastructure/plugins/loader.py (simplified)

def load_python_plugin(path: Path) -> PluginProtocol:
    # 1. Dynamic import via importlib
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # 2. Validate: must export 'plugin' variable
    if not hasattr(module, "plugin"):
        raise PluginLoadError("Plugin must export 'plugin' variable")

    # 3. Validate: must have 'search' method
    if not hasattr(plugin, "search"):
        raise PluginLoadError("Plugin must have 'search' method")

    # 4. Validate: must have non-empty 'name' attribute
    if not isinstance(plugin.name, str) or not plugin.name:
        raise PluginLoadError("Plugin must have non-empty 'name' attribute")

    return plugin
```

### Key Points

- The plugin file is imported dynamically using `importlib.util.spec_from_file_location`
- Module-level code executes during import (including `plugin = MySitePlugin()`)
- The module name is prefixed: `scavengarr_dynamic_plugin_{stem}`
- Syntax errors and import failures are wrapped in `PluginLoadError`
- The plugin is cached after first load (same behavior as YAML plugins)

---

## Search Use Case Integration

The `TorznabSearchUseCase` automatically detects Python plugins and dispatches accordingly:

```python
# src/scavengarr/application/use_cases/torznab_search.py (simplified)

def _is_python_plugin(plugin: Any) -> bool:
    """Detect Python plugins (have search() method, no scraping config)."""
    return (
        hasattr(plugin, "search")
        and callable(plugin.search)
        and not hasattr(plugin, "scraping")
    )

# In TorznabSearchUseCase.execute():
if _is_python_plugin(plugin):
    raw_results = await plugin.search(
        query, category=category, season=season, episode=episode
    )
    validated = await engine.validate_results(raw_results)
else:
    validated = await engine.search(plugin, query, category=category)
```

**Flow for Python plugins:**
1. Use case calls `plugin.search(query, category, season, episode)` directly
2. Plugin returns `list[SearchResult]`
3. Use case passes results to `SearchEngine.validate_results()` for link validation
4. Validated results are converted to `CrawlJob` entities
5. `TorznabItem` XML responses are generated

**Flow for Stremio:**
1. Stremio stream request arrives with IMDb ID + optional season/episode
2. `StremioStreamUseCase` resolves title via TMDB (or IMDB fallback)
3. Searches all `provides="stream"` plugins with title query
4. Ranks streams by title match score, quality, and language
5. Returns ranked streams with `/play/{id}` URLs for hoster resolution

**Key difference from YAML plugins:** Python plugins bypass the multi-stage pipeline entirely. They own the full search lifecycle and return final results directly.

---

## Reference Implementation: boerse.py

The `boerse.py` plugin is the reference Python plugin. It demonstrates advanced patterns for a real-world vBulletin forum scraper.

### Architecture

```
BoersePlugin
  |
  +-- _ensure_browser()        Launch Chromium if needed
  |
  +-- _ensure_session()        Domain fallback + vBulletin login
  |     |
  |     +-- For each mirror:
  |           +-- Navigate to homepage
  |           +-- Wait for Cloudflare challenge
  |           +-- Fill and submit login form (MD5 password)
  |           +-- Verify session cookie
  |
  +-- search(query, category)  Main entry point
        |
        +-- _search_threads()  Full-form search with forum filtering
        |     |
        |     +-- Navigate to search.php
        |     +-- Fill #searchform (query, forum, title-only)
        |     +-- Submit and extract thread URLs
        |
        +-- _scrape_thread()   Per-thread download link extraction
              |                 (bounded concurrency: semaphore)
              +-- Navigate to thread page
              +-- Extract title via _ThreadTitleParser
              +-- Extract download links via _PostLinkParser
              +-- Filter: only keep known container hosts
```

### Domain Fallback

The plugin tries 5 mirror domains sequentially during login:

```python
# plugins/boerse.py
_DOMAINS = [
    "https://boerse.am",
    "https://boerse.sx",
    "https://boerse.im",
    "https://boerse.ai",
    "https://boerse.kz",
]
```

If login succeeds on a mirror, `self.base_url` is updated and all subsequent requests use that domain. If all domains fail, a `RuntimeError` is raised.

### vBulletin Authentication

The login flow uses MD5-hashed passwords (a vBulletin 3.x convention):

```python
# plugins/boerse.py (simplified)
md5_pass = hashlib.md5(password.encode()).hexdigest()

# Fill the hidden vBulletin login form via JavaScript
await page.evaluate("""([user, md5]) => {
    const f = document.querySelector('form[action*="login"]');
    f.querySelector('input[name="vb_login_username"]').value = user;
    f.querySelector('input[name="vb_login_md5password"]').value = md5;
    f.submit();
}""", [username, md5_pass])

# Verify: check for session cookie
cookies = await self._context.cookies()
has_session = any(c["name"] == "bbsessionhash" for c in cookies)
```

Credentials are provided via environment variables:
```bash
export SCAVENGARR_BOERSE_USERNAME="myuser"
export SCAVENGARR_BOERSE_PASSWORD="mypass"
```

### Cloudflare Bypass

The plugin waits for Cloudflare JS challenges to complete:

```python
# plugins/boerse.py
async def _wait_for_cloudflare(self, page: Page) -> None:
    try:
        await page.wait_for_function(
            "() => !document.title.includes('Just a moment')",
            timeout=15_000,
        )
    except Exception:
        pass  # proceed anyway
```

This uses Playwright's `wait_for_function` to poll the page title until the Cloudflare interstitial resolves.

### Bounded Concurrency

Thread scraping uses an `asyncio.Semaphore` to limit parallel browser pages:

```python
# plugins/boerse.py
_MAX_CONCURRENT_PAGES = 3

sem = asyncio.Semaphore(_MAX_CONCURRENT_PAGES)

async def _bounded_scrape(url: str) -> SearchResult | None:
    async with sem:
        return await self._scrape_thread(url)

results = await asyncio.gather(
    *[_bounded_scrape(url) for url in thread_urls],
    return_exceptions=True,
)
```

This prevents RAM spikes from opening too many Chromium pages simultaneously while still processing multiple threads in parallel.

### Custom HTML Parsers

The plugin uses stdlib `HTMLParser` subclasses instead of CSS selectors for robustness against varied vBulletin markup:

| Parser | Purpose |
|---|---|
| `_ThreadLinkParser` | Extract thread URLs from search results, deduplicate by thread ID |
| `_ThreadTitleParser` | Extract clean title from `<title>` tag, strip forum suffix |
| `_PostLinkParser` | Extract download links from post content, filter to known container hosts |

### Link Container Filtering

Only links from recognized link-protection services are accepted as download links:

```python
# plugins/boerse.py
_LINK_CONTAINER_HOSTS = {
    "keeplinks.org", "keeplinks.eu",
    "share-links.biz", "share-links.org",
    "filecrypt.cc", "filecrypt.co",
    "safelinks.to", "protectlinks.com",
}
```

This prevents internal forum links, images, and other non-download URLs from being returned as results.

### Category Mapping

Torznab categories are mapped to vBulletin forum IDs:

```python
# plugins/boerse.py
_CATEGORY_FORUM_MAP: dict[int, str] = {
    2000: "30",  # Movies  -> Videoboerse
    5000: "30",  # TV      -> Videoboerse
    3000: "25",  # Audio   -> Audioboerse
    7000: "21",  # Books   -> Dokumente
    1000: "16",  # Console -> Spiele Boerse
    4000: "16",  # PC      -> Spiele Boerse
}
```

---

## Best Practices for Python Plugins

### Resource Management

Always clean up browser resources to prevent memory leaks:

```python
class MyPlugin:
    async def cleanup(self) -> None:
        """Close browser and Playwright resources."""
        if self._context is not None:
            await self._context.close()
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()
```

### Error Handling

- Wrap page navigation in try/finally to ensure pages are closed
- Use `return_exceptions=True` with `asyncio.gather` to prevent one failure from killing all tasks
- Invalidate session state on errors so the next call re-authenticates

```python
async def _scrape_page(self, url: str) -> SearchResult | None:
    page = await self._context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded")
        # ... extraction logic ...
    except Exception:
        return None
    finally:
        if not page.is_closed():
            await page.close()
```

### Concurrency

- Use `asyncio.Semaphore` to bound parallel browser pages (3-5 is typical)
- Use `asyncio.gather` for parallel execution, not sequential loops
- Keep the semaphore value conservative to avoid RAM spikes

### Credentials

- Never hardcode credentials in plugin source code
- Use environment variables with a `SCAVENGARR_` prefix
- Fail fast with a clear error message if credentials are missing

```python
username = os.environ.get("SCAVENGARR_MYSITE_USERNAME", "")
password = os.environ.get("SCAVENGARR_MYSITE_PASSWORD", "")

if not username or not password:
    raise RuntimeError(
        "Missing credentials: set SCAVENGARR_MYSITE_USERNAME "
        "and SCAVENGARR_MYSITE_PASSWORD"
    )
```

### Waiting Strategies

- Never use `asyncio.sleep()` as a waiting mechanism
- Use Playwright's built-in waits: `wait_for_selector`, `wait_for_function`, `wait_for_load_state`
- Set explicit timeouts on all wait operations

```python
# Good: condition-based waiting
await page.wait_for_function(
    "() => !document.title.includes('Just a moment')",
    timeout=15_000,
)

# Bad: sleep-based waiting
await asyncio.sleep(5)  # DO NOT DO THIS
```

### Domain Fallback

For sites with multiple mirrors:
- Try domains sequentially during initial connection
- Remember which domain succeeded and reuse it for the session
- Reset on errors to try the next domain on the next call

---

## Plugin Exceptions

The plugin system defines a hierarchy of exceptions:

```python
# src/scavengarr/domain/plugins/exceptions.py
class PluginError(Exception):           # Base class
class PluginValidationError(PluginError): # YAML schema validation failure
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

## Testing Python Plugins

### Unit Testing

Test the plugin's `search()` method with mocked browser interactions:

```python
import pytest
from unittest.mock import AsyncMock, patch

from plugins.my_site import MySitePlugin


@pytest.mark.asyncio
async def test_search_returns_results():
    plugin = MySitePlugin()
    # Mock browser calls
    with patch.object(plugin, "_ensure_session", new_callable=AsyncMock):
        with patch.object(plugin, "_search_pages", new_callable=AsyncMock) as mock:
            mock.return_value = [...]
            results = await plugin.search("test query")
            assert len(results) > 0
            assert all(r.title for r in results)
            assert all(r.download_link for r in results)
```

### Mock Patterns

- `PluginRegistryPort` is **synchronous** -- use `MagicMock`
- `SearchEnginePort`, `CrawlJobRepository`, `CachePort` are **async** -- use `AsyncMock`
- Python plugin `search()` is **async** -- use `AsyncMock` when mocking

---

## YAML vs Python Plugin Comparison

| Aspect | YAML Plugin | Python Plugin |
|---|---|---|
| Definition | Declarative `.yaml` file | Imperative `.py` file |
| Scraping engine | Scrapy (managed by framework) | Self-managed (Playwright, httpx, etc.) |
| Multi-stage pipeline | Automatic via stage definitions | Manual implementation |
| Link validation | Automatic (post-scraping) | Automatic (use case validates results) |
| Authentication | Declarative auth config | Custom code (full flexibility) |
| Cloudflare bypass | Not supported (scrapy mode) | Playwright wait_for_function |
| Domain fallback | `base_url` list (automatic) | Custom logic in plugin code |
| Complexity | Low (no code) | Medium-High (full Python) |
| Testing | Schema validation tests | Unit tests with mocked browser |
| Loading | YAML parse + Pydantic validation | `importlib` dynamic import |

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
| Stremio stream use case | `src/scavengarr/application/use_cases/stremio_stream.py` |
| Reference plugin (boerse) | `plugins/boerse.py` |
| Reference httpx plugin | `plugins/einschalten.py` |
| Reference YAML plugin | `plugins/filmpalast_to.yaml` |
