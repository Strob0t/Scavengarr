[← Back to Index](./README.md)

# Multi-Stage Scraping

> Plugins navigate from search results through detail pages to download links inside their own `search()` method; link validation runs afterwards, outside the plugin.

---

## Overview

Most indexer sites separate search results from download details. A search returns a list of titles with links to detail pages, and each detail page contains the actual download links. Scavengarr has no generic stage engine for this: every plugin implements the pattern directly in `search()`, using httpx (`HttpxPluginBase`) or Playwright (`PlaywrightPluginBase`). See [Python Plugins](./python-plugins.md) for the base classes.

```text
Search query
     |
     v
+---------------------------+
|  Search pages             |  Fetch result pages (paginated),
|  (inside plugin.search()) |  extract detail URLs
+-------------+-------------+
              |  detail URLs (parallel, bounded by _new_semaphore())
              v
+---------------------------+
|  Detail pages             |  Extract title, download_link,
|  (inside plugin.search()) |  download_links, size, ...
+-------------+-------------+
              |  list[SearchResult]
              v
+---------------------------+
|  Link validation          |  Torznab: HttpxSearchEngine.validate_results()
|  (after the plugin)       |  batch HEAD/GET checks
+---------------------------+
```

Some sites need fewer steps (a JSON API that returns links directly) or more (an extra redirect or embed page); the plugin decides.

---

## Search Pages

The first step fetches the site's search results and collects detail URLs:

- Answer the category with `served_category()`: a category the site does not have returns `[]` without a request; pass the served category to the site's own filter (URL segment, dropdown ID, forum ID) when available
- Keep only the relevant hits before fetching detail pages: `relevant_hits()` (see [Python Plugins](python-plugins.md#adding-a-new-plugin), step 3)
- Resolve relative links with `urljoin(self.base_url, href)`, not string concatenation
- Drop duplicate detail URLs before fetching them
- Paginate until `self.effective_max_results` items or no more results (see [Pagination](#pagination))

---

## Detail Pages

The second step turns each detail page into a `SearchResult`:

- Set `title` and `download_link` (required), plus `download_links` for all hoster links (`{"hoster": ..., "link": ...}`), `size`, `release_name`, `category` and `source_url` (the detail page URL) where available
- Label `category` from the site's data, never with the requested one, then keep the matches with `filter_by_category()`
- Read URLs from attributes (`href`, `data-url`, embed `src`) rather than text content
- For grouped links (several hosters per release), walk the container/group/item hierarchy and collect every link
- On missing optional fields, return a partial result; on missing required fields, log and skip the item instead of aborting the whole search

Plugins parse HTML with `selectolax` (lexbor, a C parser, CSS selectors; see [Parsing Pages](python-plugins.md#parsing-pages)), JSON APIs via `_safe_parse_json()`, or Playwright (`page.content()`, `page.evaluate()`). Every HTML parser goes through `parse_page(parser, html)` (`infrastructure/plugins/dom.py`), which parses pages from 32 KiB in a worker thread; haschcon and hdworld scan small HTML pieces (a player embed, a WordPress API post) with regexes instead. Until 2026-10 the parsers were `html.parser` state machines in Python: filmpalast's ~300 KB detail pages took 20 ms with html.parser on x86 and 2.3 ms with selectolax, with the same results.

---

## Parallel Execution

Detail pages are fetched in parallel with `asyncio.gather`, bounded by the base-class semaphore:

```python
# (simplified) typical plugin code
sem = self._new_semaphore()  # _max_concurrent, default 5

async def _bounded_scrape(url: str) -> SearchResult | None:
    async with sem:
        return await self._scrape_detail(url)

results = await asyncio.gather(
    *[_bounded_scrape(url) for url in detail_urls],
    return_exceptions=True,
)
return [r for r in results if isinstance(r, SearchResult)]
```

`_max_concurrent` can be lowered per plugin in code or via `plugins.overrides.<name>.max_concurrent` (see [Plugin System](./plugin-system.md#per-plugin-overrides)).

---

## Pagination

Plugins collect up to 1000 results across multiple search pages:

```python
# (simplified) typical plugin code
_MAX_PAGES = 50  # 20 results/page -> 50 pages for 1000

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

Plugins that page through results cap the loop from the site's results-per-page, usually as `_MAX_PAGES` (e.g. 200/page = 5 pages, 50/page = 20 pages, 10/page = 100 pages; nox names it `_MAX_SEARCH_PAGES`, dataload and myboerse inherit it from `xenforo.py`); 15 plugins, aniworld, boerse, hdfilme, kinox and moflix among them, have no such constant. `effective_max_results` is `_max_results` (default 1000), lowered during Stremio searches.

---

## Rate Limiting and Errors

- **Rate limiting:** httpx plugins share one app-wide client whose `RetryTransport` applies a per-domain token-bucket rate limiter (`DomainRateLimiter`, optionally adaptive) before every request. Plugins add no pacing of their own, with three exceptions: devideosrc's player-page 429 retry (2 s per attempt; hdfilme, streamcloud, streamkiste), animeloads' pauses between captcha and episode steps (1–4 s), and the Playwright base's `retry_backoff_s` before a retry (ddlvalley, scnsrc). Playwright traffic does not go through this client.
- **429 / 503:** retried automatically by `RetryTransport` with exponential backoff, honouring `Retry-After`. A 429/503 served from Cloudflare's cache (`cf-cache-status: HIT/STALE/UPDATING`) is returned at once: it would come back unchanged on retry, and it does not lower the domain's adaptive rate.
- **Other HTTP errors, timeouts, network errors:** `_safe_fetch()` logs a warning and returns `None`; the plugin skips that page or item.
- **Unreachable domains:** `_verify_domain()` picks the domain once per plugin lifetime, not per request. httpx plugins follow a permanent site move and undo it when the new host stops answering, and Stremio skips plugins whose site failed the last health check or whose domain check found no domain during a search (`PluginUnreachableError`; see [Mirror URL Fallback](./mirror-url-fallback.md)).

---

## Link Validation

Link validation is not part of the plugin. For Torznab searches, the use case passes the plugin's results page by page (only as many as the requested `offset + limit` need) to `HttpxSearchEngine.validate_results()`, which batch-checks `download_link` and every `download_links` entry, promotes the first valid alternative when the primary link is dead, and drops results without any valid link. Results that already carry `validated_links` are passed through unchecked. Details: [Link Validation](./link-validation.md).

Torznab does not de-duplicate results after the plugin; plugins that can return duplicates must de-duplicate themselves. Stremio drops a result found again (`result_key()`: plugin, title, release, links; the full and the base title find many results twice) and keeps one stream per hoster and language.

---

## Source Code References

| Component | Path |
|---|---|
| `HttpxPluginBase` | `src/scavengarr/infrastructure/plugins/httpx_base.py` |
| `PlaywrightPluginBase` | `src/scavengarr/infrastructure/plugins/playwright_base.py` |
| `SearchResult` | `src/scavengarr/domain/plugins/base.py` |
| `RetryTransport` | `src/scavengarr/infrastructure/common/retry_transport.py` |
| `DomainRateLimiter` | `src/scavengarr/infrastructure/common/rate_limiter.py` |
| `HttpxSearchEngine.validate_results()` | `src/scavengarr/infrastructure/torznab/search_engine.py` |
| Torznab dispatch | `src/scavengarr/application/use_cases/torznab_search.py` |
| Reference plugin | `plugins/boerse.py` |
