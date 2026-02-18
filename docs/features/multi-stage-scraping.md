[← Back to Index](./README.md)

# Multi-Stage Scraping Pipeline

Scavengarr's core scraping capability is a multi-stage pipeline that navigates from
search results through detail pages to extract download links. This cascading approach
handles sites where content is spread across multiple pages -- a common pattern in
indexer and forum-based sources.

---

## Overview

Most indexer sites separate search results from download details. A search returns a
list of titles with links to detail pages, and each detail page contains the actual
download links. Scavengarr models this with a **stage-based pipeline**:

```
Search Query
     │
     ▼
┌──────────────────────┐
│  Stage 1: "list"     │  Extract thread/result URLs from search page
│  (search_results)    │  via CSS selector / HTML parsing
└──────────┬───────────┘
           │  URLs (parallel, bounded by semaphore)
           ▼
┌──────────────────────┐
│  Stage 2: "detail"   │  Extract title, download_link, download_links,
│  (movie_detail)      │  seeders, size, etc. from each detail page
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  Deduplication       │  Remove duplicates by (title, download_link)
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  Link Validation     │  Parallel HEAD/GET checks on all URLs
└──────────┬───────────┘
           │
           ▼
     SearchResult[]
```

All plugins are Python-based and implement this pipeline in their `search()` method,
using either httpx (for static HTML) or Playwright (for JS-heavy sites).

---

## Stage Types

Each stage in the pipeline has a conceptual `type` that determines its behavior:

### List Stage (Intermediate)

A list stage extracts **URLs for the next stage**. It does not produce final results.
The plugin parses HTML to identify elements whose `href` attributes point to detail pages.

Key behaviors:
- Links are extracted from the `href` attribute of matched elements
- URLs are resolved to absolute paths via `urljoin(base_url, href)`
- Duplicate links are removed while preserving order
- Bounded concurrency limits how many detail pages are fetched in parallel

### Detail Stage (Terminal)

A detail stage extracts **SearchResult data** -- the final output of the pipeline.
It uses HTML parsing to capture title, download links, size, seeders, and other metadata.

Key behaviors:
- Text content and HTML attributes are extracted from matched elements
- Missing fields produce partial results with a warning (no full abort)
- The `source_url` is automatically added to track origin

---

## Architecture: Key Classes

### `HttpxSearchEngine`

`HttpxSearchEngine` orchestrates the search pipeline and adds link validation. This is the class
used by the Torznab use case. It dispatches to Python plugins and validates results.

```python
# src/scavengarr/infrastructure/torznab/search_engine.py
class HttpxSearchEngine:
    async def search(self, plugin, query: str, **params) -> list[SearchResult]:
        # 1. Call plugin.search() directly
        # 2. Deduplicate by (title, download_link)
        # 3. Validate download links (batch, parallel)
        # 4. Filter results with dead links
        # 5. Promote alternative links when primary is dead
```

### Python Plugins

All 42 plugins implement the multi-stage pattern directly in Python code. Httpx plugins
use `self._safe_fetch()` for HTTP requests, while Playwright plugins use browser automation.
Both types use `asyncio.Semaphore` for bounded concurrency when processing detail pages.

---

## Data Extraction

### HTML Parsing

Plugins extract data from HTML using BeautifulSoup CSS selectors or Playwright locators:

```python
# Example: extracting title and download links from a detail page
soup = BeautifulSoup(html, "html.parser")
title = soup.select_one("h1.movie-title")
links = soup.select("a.download-btn")
```

For fields like download URLs, extraction reads HTML attributes (e.g., `href`, `data-url`)
rather than text content.

### Nested Download Links

For pages with multiple grouped download links (e.g., multiple hosters per release),
plugins traverse a container/group/item hierarchy to extract all available links.

---

## Parallel Execution

When a list stage produces multiple URLs for the next stage, they are processed
**in parallel** using `asyncio.gather` with bounded concurrency:

```python
# Bounded concurrency via semaphore
sem = self._new_semaphore()  # default: 3 concurrent

async def _bounded_scrape(url: str) -> SearchResult | None:
    async with sem:
        return await self._scrape_detail(url)

results = await asyncio.gather(
    *[_bounded_scrape(url) for url in detail_urls],
    return_exceptions=True,
)
```

- Concurrency is bounded by `asyncio.Semaphore` (typically 3)
- URL deduplication prevents the same page from being fetched twice
- Rate limiting may be applied per request depending on the plugin

---

## Pagination

Plugins implement pagination to collect up to 1000 results across multiple search pages:

```python
# Example pagination pattern
_MAX_PAGES = 50  # Based on results-per-page

for page_num in range(1, _MAX_PAGES + 1):
    resp = await self._safe_fetch(f"{search_url}&page={page_num}")
    if resp is None:
        break
    items = self._parse_results(resp.text)
    if not items:
        break
    all_results.extend(items)
    if len(all_results) >= self._max_results:
        break
```

Each plugin defines `_MAX_PAGES` based on the site's results-per-page count
(e.g., 200/page = 5 pages, 50/page = 20 pages, 10/page = 100 pages).

---

## Retry and Rate Limiting

### Rate Limiting

Plugins may apply delays between requests to prevent overwhelming target sites
and reduce the chance of IP blocks. This is handled per-plugin based on site requirements.

### Error Handling

Failed requests are handled gracefully:
- **4xx errors** (client errors): Skip the page, log warning
- **5xx errors** (server errors): May retry depending on plugin implementation
- **Network errors**: Try [mirror URLs](./mirror-url-fallback.md) if configured

---

## Deduplication

Deduplication happens at two levels:

### URL-Level Deduplication

Plugins maintain visited URL sets to prevent the same page from being fetched twice
during a single scrape operation.

### Result-Level Deduplication

`HttpxSearchEngine` deduplicates by `(title, download_link)` tuple:

```python
# src/scavengarr/infrastructure/torznab/search_engine.py
seen: set[tuple[str, str]] = set()
for result in raw_results:
    key = (result.title, result.download_link)
    if key in seen:
        continue
    seen.add(key)
    results.append(result)
```

---

## Source Code References

| Component | File |
|---|---|
| `HttpxSearchEngine` | `src/scavengarr/infrastructure/torznab/search_engine.py` |
| `HttpxPluginBase` | `src/scavengarr/infrastructure/plugins/httpx_base.py` |
| `PlaywrightPluginBase` | `src/scavengarr/infrastructure/plugins/playwright_base.py` |
| `SearchResult` (entity) | `src/scavengarr/domain/plugins/base.py` |
| Unit tests | `tests/unit/infrastructure/test_search_engine.py` |
