[← Back to Index](./README.md)

# Python Plugins

> Author guide for Scavengarr plugins: base classes, settings layout, search standards, a reference implementation, best practices and testing.

---

## Overview

Every plugin is a Python file in `plugins/` that inherits from one of two base classes: `HttpxPluginBase` (33 plugins) or `PlaywrightPluginBase` (9 plugins). The base classes provide client/browser lifecycle, domain fallback, bounded concurrency and error handling; the plugin implements `search()`.

How plugins are discovered, loaded, configured and called is documented in [Plugin System](./plugin-system.md):

- Contract (`PluginProtocol`, `provides`, `search()` parameters): [PluginProtocol](./plugin-system.md#pluginprotocol)
- Discovery, import and validation: [Discovery & Loading](./plugin-system.md#discovery--loading)
- Torznab vs Stremio dispatch, `isolated_search()`, `effective_max_results`: [How Plugins Are Called](./plugin-system.md#how-plugins-are-called)
- `PluginLoadError`, `PluginNotFoundError`: [Plugin Exceptions](./plugin-system.md#plugin-exceptions)

**When to use `HttpxPluginBase`:**
- The site is static HTML with server-rendered pages
- JSON APIs or standard HTTP requests are sufficient
- No JavaScript execution or Cloudflare challenge bypass is needed

**When to use `PlaywrightPluginBase`:**
- The site requires JavaScript execution (Cloudflare challenge, SPA, DDoS-Guard)
- Authentication is non-standard (MD5-hashed passwords, multi-step login)
- Dynamic content loading or browser interaction is required

---

## SearchResult

`search()` returns a list of `SearchResult` objects:

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
    download_links: list[dict[str, str]] | None = None  # All links, e.g. {"hoster": ..., "link": ...}
    source_url: str | None = None                       # Detail page the result was scraped from
    scraped_from_stage: str | None = None               # Currently not set by any plugin

    # Post-validation: all valid URLs (primary + alternatives)
    validated_links: list[str] | None = None

    # Metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    # Torznab-specific
    category: int = 2000                          # Default: Movies
    grabs: int = 0
    download_volume_factor: float = 0.0
    upload_volume_factor: float = 0.0
```

Only `title` and `download_link` are required. Link validation reads `download_link` plus the `link` key of each `download_links` entry. A plugin that sets `validated_links` itself (e.g. behind anti-bot protection) skips link validation, see [Link Validation](./link-validation.md).

---

## Plugin Base Classes

Both base classes live in `src/scavengarr/infrastructure/plugins/` and share these class attributes:

| Attribute | Default | Purpose |
|---|---|---|
| `name` | `""` | Plugin identifier (must be set) |
| `provides` | `"download"` | `"stream"`, `"download"` or `"both"` |
| `version` | `"1.0.0"` | Informational |
| `mode` | `"httpx"` / `"playwright"` | Reported by `registry.get_mode()` |
| `languages` | `["de"]` | Content languages; `default_language` property returns `languages[0]` |
| `_domains` | `[]` | Domains in fallback order (must be set) |
| `_max_concurrent` | `5` | Semaphore size for `_new_semaphore()` |
| `_max_results` | `1000` | Upper bound for pagination |
| `_user_agent` | Chrome 131 UA | `User-Agent` header for httpx requests (Playwright plugins: side requests only) |
| `cache_ttl` | `None` | Torznab search cache TTL in seconds (`None` = global default) |

Shared helpers in both classes:

- `base_url` — instance attribute, initially `https://{_domains[0]}`, updated by `_verify_domain()`
- `effective_max_results` — `min(search_max_results, _max_results)`; lower during Stremio searches, use it as the pagination limit
- `_category_matches(requested, accepted)` — Torznab parent/child match: `None` matches everything, `5000` matches `5000-5999`, `5070` matches only `5070`
- `_new_semaphore()` — `asyncio.Semaphore(self._max_concurrent)`
- `_verify_domain()` — domain fallback (see [Mirror URL Fallback](./mirror-url-fallback.md)); no-op with one domain; the verified domain stays until `cleanup()`
- `isolated_search(query, category, *, season, episode)` — entry point used by Stremio (see [How Plugins Are Called](./plugin-system.md#how-plugins-are-called))
- `cleanup()` — releases resources and resets `_domain_verified`

### HttpxPluginBase

For sites with JSON APIs or server-rendered HTML (no JavaScript needed):

```python
# plugins/my_site.py
from __future__ import annotations

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["my-site.com", "my-site.org"]  # Fallback order
_MAX_PAGES = 50  # 20 results/page -> 50 pages for 1000


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
        await self._verify_domain()

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

**Provided by `HttpxPluginBase`:**
- `_timeout` — per-plugin request timeout (default `15.0` seconds)
- `set_shared_http_client(client)` (classmethod) — the composition root injects one app-wide `httpx.AsyncClient` with per-domain rate limiting and 429/503 retry (`RetryTransport`); all httpx plugins reuse it
- `_ensure_client()` — returns the shared client, or creates a private client (timeout, redirects, user agent) when none was injected
- `_verify_domain()` — sends `HEAD https://{domain}/` (5 s timeout) for each domain; the first status `< 400` wins and `base_url` is taken from the final URL after redirects (e.g. a `www.` prefix); if all fail, `_domains[0]` is kept and a warning is logged
- `_fetch_text(url, *, params=None, context="") -> str | None` — GET returning the body text; when the site answers with a Cloudflare challenge (`is_cloudflare_challenge`) and a browser fetcher is injected (`HttpxPluginBase.set_browser_fetcher()`, wired to the `StealthPool` when `playwright.browser_fallback` is on), the same URL (query string included) is loaded through the browser instead. JSON comes back as raw text: parse it with `json.loads`. Use it for every request of a Cloudflare-protected site
- `_parse_json_text(body, context="") -> dict | None` — decodes a JSON object from `_fetch_text()` (logs `{name}_invalid_json`)
- `_resolve_redirect(url, *, context="") -> str | None` — first off-site `Location` of a link-out URL (follows same-host hops); behind Cloudflare the browser fetcher resolves it
- `_resolve_result_links(results)` — replaces link-out URLs on the plugin's own host (e.g. `/external/<hash>`) by their targets in the final results and drops results left without links; call it on the capped result list only (each link costs a round trip)
- Hosts that answered with a Cloudflare challenge skip plain httpx for 30 min and go straight to the browser (a doomed request still counts against the site's rate limit)
- `_safe_fetch(url, *, method="GET", context="", **kwargs)` — request with `raise_for_status()`; returns `None` (and logs `{name}_timeout` / `{name}_http_error` / `{name}_fetch_error`) on timeout, non-2xx status or any other error; applies the plugin's `_timeout` and `_user_agent` per request when using the shared client
- `_safe_parse_json(response, context="")` — returns parsed JSON or `None`
- `isolated_search()` — plain passthrough to `search()`
- `cleanup()` — closes a private client (never the shared one)

**devideosrc.co player** (`scavengarr.infrastructure.plugins.devideosrc`): DLE streaming sites such as streamcloud embed a devideosrc player (`devideosrc.co/movie/<imdb>`, `devideosrc.co/serial/<imdb>`) instead of listing hoster links. `find_player(html)` detects it on a detail page, `fetch_links(client, player, **request_kwargs)` returns a `PlayerLinks(kind, links)`: the hoster embeds (movie: best rank first; series: every episode, labelled `<season>x<episode> <hoster>`) and the player kind that answered — sites like streamkiste embed the series player for movies too, so a series page without token falls back to the movie player. `filter_episodes(links, season, episode)` narrows series links to one season/episode. Used by streamcloud and streamkiste. The player page is always loaded past Cloudflare's cache (cached copies carry expired tokens and even cached 429s); a 429 there is retried with a fresh URL. Only the separate download embed (`/embed/download/<imdb>`) sits behind Turnstile.

### PlaywrightPluginBase

For sites requiring JavaScript execution or Cloudflare bypass:

```python
# plugins/my_js_site.py
from __future__ import annotations

from urllib.parse import quote_plus

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
        await self._verify_domain()

        page = await self._new_page()  # uses the per-request context when isolated
        try:
            ok = await self._navigate_and_wait(
                page, f"{self.base_url}/search?q={quote_plus(query)}"
            )
            if not ok:
                return []
            html = await page.content()
            # ... parse HTML, build results ...
            return []
        finally:
            if not page.is_closed():
                await page.close()


plugin = MyJsSitePlugin()
```

Always obtain pages via `_new_page()` / `_ensure_page()` (or the context from `_ensure_context()`), never via `self._context` directly: under `isolated_search()` the active context is a per-request one and `self._context` may be `None`.

**Provided by `PlaywrightPluginBase`:**
- `_headless` (default `False` = headful when `DISPLAY` exists, else headless fallback via `resolve_headless()`; standalone launches only, the shared pool follows `playwright.headless`), `_cf_timeout_ms` (default `30_000`, covers a Turnstile click; only spent while a challenge shows), `_networkidle_timeout_ms` (default `10_000`)
- `_block_resources` (default `True`) — aborts image, font and CSS requests in each new context. Anti-bot evasion comes from Patchright itself (imports use `patchright.async_api`); its Console domain is disabled, so `page.on("console")` never fires
- `set_shared_pool(pool)` — the composition root injects the `SharedBrowserPool`; `_ensure_browser()` then reuses the shared Chromium instead of launching its own
- `_ensure_browser()` — shared browser, or a standalone Chromium launch with one retry; reconnects if the browser disconnected
- `_browser_user_agent` (default `None`) — browser contexts keep Patchright's real User-Agent; a forced UA disagrees with the client hints (`sec-ch-ua`) and gets flagged. Set it only when a site needs a specific UA
- `_context_options()` — keyword arguments for `browser.new_context()` (1280x720 viewport, `_browser_user_agent` if set); use it for extra contexts such as login contexts
- `_ensure_context()` — returns the per-request context from `isolated_search()` if set, otherwise a persistent context built from `_context_options()` plus resource blocking
- `_ensure_page()` — persistent page in the current context; `_new_page()` — fresh page, caller closes it
- `_wait_for_cloudflare(page) -> bool` — solves a Cloudflare challenge via `browser/turnstile.solve_cloudflare()`: returns at once without a challenge title, otherwise waits ~3 s for an auto-clear, then clicks the Turnstile checkbox in the `challenges.cloudflare.com` iframe (re-click every 8 s); `False` on timeout. Needs a headful browser to pass
- `_passes_cloudflare(page, resp) -> bool` — accepts a navigation: status `< 400`, or a 403/503 Cloudflare challenge page that gets solved
- `_navigate_and_wait(page, url, *, wait_for_cf=True, wait_for_idle=True) -> bool` — `goto` (`domcontentloaded`), `_passes_cloudflare()`, `networkidle`; `False` on an error status that is not a solvable challenge
- `_fetch_page_html(url, *, wait_until="domcontentloaded", timeout=30_000) -> str` — fresh page, navigate, wait, return HTML (`""` on failure)
- `_verify_domain()` — navigates the persistent page to each domain (5 s timeout); status `< 400` and a resolved Cloudflare challenge are required; otherwise the next domain is tried
- `isolated_search()` — creates a fresh `BrowserContext` per call (stealth applied), calls `_prepare_context(ctx)`, runs `search()` with the context set in a `ContextVar`, then closes all pages and the context
- `_prepare_context(ctx)` — hook for authenticated plugins to inject session cookies (`ctx.add_cookies()`) into the per-request context
- `_serialize_search` (default `False`) — when `True`, `isolated_search()` runs `search()` behind a lock on the persistent context instead (plugins that depend on page state, e.g. `moflix`)
- `cleanup()` — closes page and context; closes browser and Playwright only when the plugin launched them itself (not with the shared pool)

---

## Plugin Settings Organization

Every plugin starts with a "Configurable settings" block followed by constants:

```python
# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["site.com", "site.org"]
_MAX_PAGES = 50

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_CATEGORY_MAP = { ... }
```

Names such as `_MAX_PAGES`, `_PER_PAGE`, `_CATEGORY_MAP` or `_LANG_LABELS` are conventions used where the site needs them, not a required set. Some plugins also define a `categories` class attribute; it is plugin-internal and not read by core code.

---

## Adding a New Plugin

**Step 1: Site analysis (mandatory before writing code).** Use the `playwright-mcp` server to inspect all relevant pages (search, categories, detail, download). Document selectors, link patterns and pagination, check for JS dependencies (Cloudflare, dynamic loading, SPAs), auth (login, cookies, tokens) and URL patterns.

**Step 2: Choose the base class.** Every plugin inherits from `HttpxPluginBase` (static HTML/JSON) or `PlaywrightPluginBase` (JS-heavy sites); see [Plugin Base Classes](#plugin-base-classes). Never duplicate base-class boilerplate (client setup, domain fallback, cleanup, semaphore, user agent).

- `HttpxPluginBase` class attributes: `_domains`, `_max_concurrent` (default 5), `_max_results` (default 1000), `_timeout` (default 15), `_user_agent`, `languages` (default `["de"]`, e.g. `["en"]` for English sites).
- Playwright plugins: add `from playwright.async_api import Page` when using `Page` type hints.

**Step 3: Mandatory search standards (all plugins).**

1. **Category filtering**: map Torznab categories to the site's filter system (dropdown IDs, URL path segments, forum IDs) and pass them in the search request. Use `self._category_matches()` to skip searches the site cannot serve.
2. **Pagination up to 1000 items**: parse pagination links/hit counts from the first page, then fetch further pages until `self.effective_max_results` items or no more results. Set `_MAX_PAGES` from the site's page size (200/page → 5, 50/page → 20, 10/page → 100).
3. **Bounded concurrency** for detail pages: `self._new_semaphore()` (default 5 parallel requests).

```python
# (simplified) pagination
all_results: list[SearchResult] = []
for page_num in range(1, _MAX_PAGES + 1):
    resp = await self._safe_fetch(f"{search_url}&page={page_num}", context="search")
    if resp is None:
        break
    items = self._parse_results(resp.text)
    if not items:
        break
    all_results.extend(items)
    if len(all_results) >= self.effective_max_results:
        break
```

**Step 4: Implement and test.**

1. Create `plugins/<sitename>.py`, set `name`, `provides` and `_domains`, optionally override `_max_results`, `_max_concurrent`, `languages`, `cache_ttl`.
2. Implement `async def search(self, query, category, season, episode) -> list[SearchResult]` using `self._safe_fetch()` (httpx) or `self._new_page()` / `self._ensure_page()` (Playwright), and `self._log` for logging.
3. Add unit tests in `tests/unit/infrastructure/test_<sitename>_plugin.py` (see [Testing Plugins](#testing-plugins)).
4. Restart the server and query `http://localhost:7979/api/v1/torznab/<name>?t=search&q=test`.

---

## Reference Implementation: boerse.py

`plugins/boerse.py` is the reference Playwright plugin: a vBulletin forum scraper with login, domain fallback and per-request cookie injection.

### Architecture

```text
BoersePlugin (PlaywrightPluginBase)
  |
  +-- _ensure_session()        Login once (guarded by _login_lock)
  |     |
  |     +-- For each domain in _domains:
  |           +-- Temporary login BrowserContext
  |           +-- Navigate to homepage, wait for Cloudflare
  |           +-- Fill and submit login form (MD5 password)
  |           +-- Session cookie "bbsessionhash" present?
  |                 -> set base_url, store _session_cookies
  |     +-- All domains failed -> RuntimeError
  |
  +-- _prepare_context(ctx)    Inject _session_cookies into per-request context
  |
  +-- search(query, category)  Main entry point
        |
        +-- _search_threads()  Submit #searchform (query, forum, title-only)
        |                      and extract thread URLs (_ThreadLinkParser)
        |
        +-- _scrape_thread()   Per-thread extraction (bounded by _new_semaphore())
              +-- Title via _ThreadTitleParser
              +-- Download links via _PostLinkParser
              +-- Keep only known link-container hosts
```

### Domain Fallback

The plugin tries six domains in order during login:

```python
# plugins/boerse.py
_DOMAINS = [
    "boerse.am",
    "boerse.tw",
    "boerse.sx",
    "boerse.im",
    "boerse.ai",
    "boerse.kz",
]
```

The first domain where login succeeds becomes `self.base_url`. If all domains fail, `RuntimeError("All boerse domains failed during login")` is raised. The plugin does not call `_verify_domain()`; the login loop is its domain fallback.

### vBulletin Authentication

The login flow uses MD5-hashed passwords (a vBulletin 3.x convention) in a temporary context per domain:

```python
# plugins/boerse.py (simplified)
md5_pass = hashlib.md5(password.encode()).hexdigest()

login_ctx = await browser.new_context(**self._context_options())
page = await login_ctx.new_page()
await page.goto(domain_url, wait_until="domcontentloaded")
await self._wait_for_cloudflare(page)

# Fill the existing vBulletin login form via JavaScript
async with page.expect_navigation(wait_until="domcontentloaded", timeout=15_000):
    await page.evaluate("""([user, md5]) => {
        const f = document.querySelector('form[action*="login"]');
        f.querySelector('input[name="vb_login_username"]').value = user;
        f.querySelector('input[name="vb_login_md5password"]').value = md5;
        f.submit();
    }""", [username, md5_pass])

# Verify: check for session cookie, keep cookies for per-request contexts
cookies = await login_ctx.cookies()
if any(c["name"] == "bbsessionhash" for c in cookies):
    self.base_url = domain_url
    self._session_cookies = cookies
    self._logged_in = True
```

The stored cookies are injected into every per-request context:

```python
# plugins/boerse.py
async def _prepare_context(self, ctx: BrowserContext) -> None:
    if self._session_cookies:
        await ctx.add_cookies(self._session_cookies)
```

Credentials are provided via environment variables:

```bash
export SCAVENGARR_BOERSE_USERNAME="myuser"
export SCAVENGARR_BOERSE_PASSWORD="mypass"
```

### Cloudflare Handling

boerse uses the inherited `PlaywrightPluginBase._wait_for_cloudflare()`:

```python
# src/scavengarr/infrastructure/plugins/playwright_base.py
async def _wait_for_cloudflare(self, page: Page) -> bool:
    return await solve_cloudflare(page, timeout_ms=self._cf_timeout_ms)
```

### Bounded Concurrency

Thread scraping uses the base-class semaphore to limit parallel browser pages:

```python
# plugins/boerse.py
sem = self._new_semaphore()  # _max_concurrent, default 5

async def _bounded_scrape(url: str) -> SearchResult | None:
    async with sem:
        return await self._scrape_thread(url)

results = await asyncio.gather(
    *[_bounded_scrape(url) for url in thread_urls],
    return_exceptions=True,
)
return [r for r in results if isinstance(r, SearchResult)]
```

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

This prevents internal forum links, images and other non-download URLs from being returned as results.

### Category Mapping

Torznab categories are mapped to vBulletin forum IDs (default `"30"`):

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

## Best Practices

### Resource Management

Override `cleanup()` only to reset plugin state, and always call the base implementation:

```python
# plugins/boerse.py
async def cleanup(self) -> None:
    await super().cleanup()
    self._logged_in = False
    self._session_cookies = None
```

### Error Handling

- Wrap page navigation in `try`/`finally` to ensure pages are closed
- Use `return_exceptions=True` with `asyncio.gather` so one failure does not kill all tasks, then filter out exceptions
- Prefer `_safe_fetch()` / `_fetch_page_html()`, which log and return `None` / `""` instead of raising
- Invalidate session state on errors so the next call re-authenticates

```python
async def _scrape_page(self, url: str) -> SearchResult | None:
    page = await self._new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded")
        # ... extraction logic ...
    except Exception:  # noqa: BLE001
        self._log.warning("scrape_failed", url=url)
        return None
    finally:
        if not page.is_closed():
            await page.close()
```

### Concurrency

- Use `self._new_semaphore()` to bound parallel requests or browser pages (`_max_concurrent`, default 5)
- Use `asyncio.gather` for parallel execution, not sequential loops
- Lower `_max_concurrent` for Playwright plugins or fragile sites to avoid RAM spikes and blocks

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
- Use Playwright's built-in waits: `wait_for_selector`, `wait_for_function`, `wait_for_load_state`, or the base helpers `_wait_for_cloudflare()` / `_navigate_and_wait()`
- Set explicit timeouts on all wait operations

```python
# Good: condition-based waiting
await page.wait_for_function(
    "() => !document.title.includes('Just a moment')",
    timeout=self._cf_timeout_ms,
)

# Bad: sleep-based waiting
await asyncio.sleep(5)  # DO NOT DO THIS
```

### Domain Fallback

- List domains in `_domains` in order of preference and call `await self._verify_domain()` at the start of `search()`
- The base class keeps the verified domain until `cleanup()`; it does not re-probe after later request errors
- See [Mirror URL Fallback](./mirror-url-fallback.md)

---

## Testing Plugins

### Unit Testing

Plugin files are not an importable package; tests load them by path with `importlib`:

```python
# tests/unit/infrastructure/test_my_site_plugin.py (simplified)
import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "my_site.py"


@pytest.fixture()
def my_site_mod():
    spec = importlib.util.spec_from_file_location("my_site", _PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["my_site"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("my_site", None)


@pytest.mark.asyncio()
async def test_search_returns_results(my_site_mod) -> None:
    plugin = my_site_mod.MySitePlugin()
    plugin._client = AsyncMock(spec=httpx.AsyncClient)
    plugin._client.get = AsyncMock(return_value=...)
    results = await plugin.search("test query")
    assert all(r.title and r.download_link for r in results)
```

For Playwright plugins, patch `scavengarr.infrastructure.plugins.playwright_base.async_playwright`.

### Mock Patterns

- `PluginRegistryPort` is **synchronous** — use `MagicMock`
- `SearchEnginePort`, `CrawlJobRepository`, `CachePort`, `PluginScoreStorePort` are **async** — use `AsyncMock`
- Plugin `search()` is **async** — use `AsyncMock` when mocking it

---

## Httpx vs Playwright Plugin Comparison

| Aspect | HttpxPluginBase | PlaywrightPluginBase |
|---|---|---|
| HTTP client | Shared app-wide httpx client (rate-limited, retrying) | Chromium via the shared browser pool |
| Use case | Static HTML, JSON APIs | JS-heavy sites, SPAs, Cloudflare |
| Resource usage | Low (no browser) | Higher (browser context per request) |
| Domain fallback | `_verify_domain()` with HTTP `HEAD` | `_verify_domain()` with browser navigation + Cloudflare wait |
| Cloudflare bypass | Not supported | `_wait_for_cloudflare()`, stealth mode |
| Request isolation | Not needed (`isolated_search()` passthrough) | Per-request `BrowserContext` or `_serialize_search` lock |
| Concurrency | `_new_semaphore()` for parallel requests | `_new_semaphore()` for parallel pages |
| Cleanup | Close private httpx client | Close page/context; browser only if not shared |
| Testing | Mock `plugin._client` or `respx` | Patch `async_playwright` |

---

## Source Code References

| Component | Path |
|---|---|
| SearchResult dataclass | `src/scavengarr/domain/plugins/base.py` |
| HttpxPluginBase | `src/scavengarr/infrastructure/plugins/httpx_base.py` |
| PlaywrightPluginBase | `src/scavengarr/infrastructure/plugins/playwright_base.py` |
| Plugin constants (defaults, `search_max_results`) | `src/scavengarr/infrastructure/plugins/constants.py` |
| Per-request browser context (`request_browser_context`) | `src/scavengarr/infrastructure/plugins/context_vars.py` |
| SharedBrowserPool | `src/scavengarr/infrastructure/browser/shared_browser.py` |
| Shared client and pool wiring | `src/scavengarr/interfaces/composition.py` |
| Reference Playwright plugin | `plugins/boerse.py` |
| Reference httpx plugins | `plugins/einschalten.py`, `plugins/filmpalast_to.py` |
