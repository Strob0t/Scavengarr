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
│  (search_results)    │  via CSS selector → link
└──────────┬───────────┘
           │  URLs (max 10, parallel)
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

---

## Stage Types

Each stage in the pipeline has a `type` that determines its behavior:

### `list` Stage (Intermediate)

A list stage extracts **URLs for the next stage**. It does not produce final results.
The `link` selector identifies elements whose `href` attributes point to detail pages.

```yaml
# plugins/example.yaml
stages:
  - name: "search_results"
    type: "list"
    url_pattern: "/search/{query}/"
    selectors:
      link: "a.thread-title"         # Extracts href → next stage URLs
      title: "h3.result-title::text" # Optional: intermediate metadata
    next_stage: "movie_detail"
```

Key behaviors:
- Links are extracted from the `href` attribute of matched elements
- URLs are resolved to absolute paths via `urljoin(base_url, href)`
- Duplicate links are removed while preserving order
- A maximum of **10 links** are followed to the next stage (configurable cap)

### `detail` Stage (Terminal)

A detail stage extracts **SearchResult data** -- the final output of the pipeline.
It uses field selectors to capture title, download links, size, seeders, and other metadata.

```yaml
# plugins/example.yaml
stages:
  - name: "movie_detail"
    type: "detail"
    selectors:
      title: "h1.page-title"
      release_name: "span.release-name"
      size: "span.file-size"
      seeders: "span.seeders"
      leechers: "span.leechers"
      download_link: "a.download-btn"
    field_attributes:
      download_link: ["href", "data-url"]
```

Key behaviors:
- Simple selectors extract text content by default
- Link/URL fields use `field_attributes` to specify which HTML attribute to read
- Missing fields produce partial results with a warning (no full abort)
- The `source_url` is automatically added to track origin

---

## Architecture: Key Classes

### `StageScraper`

`StageScraper` is the single-stage executor. One instance exists per stage in the pipeline.

**Responsibilities:**
- Build URLs from templates or direct input (`build_url`)
- Extract data from parsed HTML using CSS selectors (`extract_data`)
- Extract links for the next stage (`extract_links`)
- Evaluate stage conditions (`should_process`)

```python
# src/scavengarr/infrastructure/scraping/scrapy_adapter.py
class StageScraper:
    def __init__(self, stage: ScrapingStage, base_url: str):
        self.stage = stage
        self.base_url = base_url
        self.name = stage.name
        self.selectors = stage.selectors
```

### `ScrapyAdapter`

`ScrapyAdapter` orchestrates the full multi-stage pipeline. It manages HTTP fetching,
retry logic, rate limiting, and recursive stage execution.

**Responsibilities:**
- Initialize stage scrapers from plugin configuration
- Fetch pages with rate limiting and exponential backoff retry
- Execute stages recursively (list → detail → ...)
- Aggregate results across all stages
- Normalize raw results into `SearchResult` objects

```python
# src/scavengarr/infrastructure/scraping/scrapy_adapter.py
class ScrapyAdapter:
    def __init__(
        self,
        plugin: YamlPluginDefinition,
        http_client: httpx.AsyncClient,
        cache: Cache,
        delay_seconds: float = 1.5,
        max_depth: int = 5,
        max_retries: int = 3,
        retry_backoff_base: float = 2.0,
    ): ...
```

### `HttpxScrapySearchEngine`

`HttpxScrapySearchEngine` wraps the adapter and adds link validation. This is the class
used by the Torznab use case.

```python
# src/scavengarr/infrastructure/torznab/search_engine.py
class HttpxScrapySearchEngine:
    async def search(self, plugin, query: str, **params) -> list[SearchResult]:
        # 1. Create ScrapyAdapter
        # 2. Execute multi-stage scrape
        # 3. Convert to SearchResult (dedup by title+link)
        # 4. Validate download links (batch, parallel)
        # 5. Filter results with dead links
        # 6. Promote alternative links when primary is dead
```

---

## Data Extraction

### Simple Selectors

Simple selectors extract text or attributes from a single element:

```yaml
selectors:
  title: "h1.movie-title"           # → get_text(strip=True)
  size: "span.file-size"            # → get_text(strip=True)
  download_link: "a.download-btn"   # → attribute extraction
```

For fields named with `_link` or `_url` suffixes, extraction uses `field_attributes`:

```yaml
field_attributes:
  download_link: ["href", "data-player-url", "onclick"]
```

The extractor tries each attribute in order until a value is found. Special handling
exists for the `onclick` attribute, where a URL is extracted via regex from JavaScript code:

```python
# Extracts URL from: onclick="embedy('https://example.com/play')"
match = re.search(r'https?://[^\'")\s]+', value)
```

### Nested Selectors (`download_links`)

For pages with multiple grouped download links (e.g., multiple hosters per release),
nested selectors support two extraction modes:

**Mode 1: Direct extraction** (no `item_group`)

```yaml
selectors:
  download_links:
    container: "div.download-section"
    items: "a.download-link"
    fields:
      link: "::self"
      hoster: "span.hoster-name"
    field_attributes:
      link: ["href"]
```

Each matched `items` element produces one entry in the `download_links` list.

**Mode 2: Grouped extraction** (with `item_group`)

```yaml
selectors:
  download_links:
    container: "div.releases"
    item_group: "div.release-group"
    items: "div.hoster-row"
    fields:
      link: "a"
      hoster: "span.name"
    field_attributes:
      link: ["href"]
    multi_value_fields: ["link"]
```

All `items` within each `item_group` are merged into a single result. Fields listed
in `multi_value_fields` are collected as lists instead of being overwritten.

---

## URL Building

Each stage builds its URL from one of three sources (in priority order):

1. **Direct URL** -- passed from a previous stage's link extraction
2. **Static `url`** -- a fixed path joined with `base_url`
3. **`url_pattern`** -- a template with `{query}`, `{category}`, etc. placeholders

```python
# src/scavengarr/infrastructure/scraping/scrapy_adapter.py
def build_url(self, url: str | None = None, **url_params) -> str:
    if url:
        return url                                    # Direct
    if self.stage.url:
        return urljoin(self.base_url, self.stage.url) # Static
    if self.stage.url_pattern:
        path = self.stage.url_pattern.format(**url_params)
        return urljoin(self.base_url, path)           # Template
```

---

## Parallel Execution

When a list stage produces multiple URLs for the next stage, they are processed
**in parallel** using `asyncio.gather`:

```python
# src/scavengarr/infrastructure/scraping/scrapy_adapter.py (scrape_stage)
max_links = 10
limited_links = links[:max_links]

sub_results_list = await asyncio.gather(
    *(
        self.scrape_stage(next_stage_name, url=link, depth=depth + 1)
        for link in limited_links
    )
)
```

- Maximum **10 links** are processed per stage transition
- If more links exist, excess links are logged as truncated
- URL deduplication prevents the same page from being fetched twice
- Rate limiting (`delay_seconds`) is applied per request, not per stage

---

## Pagination

List stages can be configured with pagination to follow "next page" links:

```yaml
stages:
  - name: "search_results"
    type: "list"
    url_pattern: "/search/{query}/"
    selectors:
      link: "a.thread-title"
    next_stage: "movie_detail"
    pagination:
      enabled: true
      selector: "a.next-page"
      max_pages: 3
```

The pagination handler:
1. Looks for a `next` link using the CSS selector
2. Follows it up to `max_pages` times
3. Extracts data from each paginated page
4. Respects the same rate limiting and condition checks as the main stage

---

## Stage Conditions

Stages can define conditions that filter results before they enter the pipeline:

```yaml
stages:
  - name: "movie_detail"
    type: "detail"
    selectors:
      title: "h1"
      seeders: "span.seeds"
    conditions:
      min_seeders: 5
```

The condition `min_seeders: 5` checks that the `seeders` field (parsed as float)
is >= 5. If the condition fails, the result is silently dropped.

---

## Retry and Rate Limiting

### Rate Limiting

Every HTTP request is preceded by `asyncio.sleep(delay_seconds)` (default: 1.5s).
This prevents overwhelming target sites and reduces the chance of IP blocks.

### Exponential Backoff Retry

Failed requests are retried with exponential backoff:

```
Attempt 1: immediate (after rate limit delay)
Attempt 2: wait 2^0 = 1 second
Attempt 3: wait 2^1 = 2 seconds
```

Configuration:
- `max_retries`: Maximum retry attempts (default: 3)
- `retry_backoff_base`: Base for exponential calculation (default: 2.0)

Behavior:
- **4xx errors** (client errors): No retry, return `None` immediately
- **5xx errors** (server errors): Retry with backoff
- **Request errors** (network): Retry with backoff, then try [mirror URLs](./mirror-url-fallback.md) on final failure

---

## Deduplication

Deduplication happens at two levels:

### URL-Level Deduplication

`ScrapyAdapter` maintains a `visited_urls: set[str]` that prevents the same page
from being fetched twice during a single scrape operation. URLs are added to the set
**before** the HTTP request to prevent duplicate fetches from concurrent `asyncio` tasks.

### Result-Level Deduplication

`HttpxScrapySearchEngine._convert_stage_results()` deduplicates by `(title, download_link)` tuple:

```python
# src/scavengarr/infrastructure/torznab/search_engine.py
seen: set[tuple[str, str]] = set()
for stage_name, items in stage_results.items():
    for item in items:
        result = self._convert_to_result(item, stage_name)
        if result:
            key = (result.title, result.download_link)
            if key in seen:
                continue
            seen.add(key)
            results.append(result)
```

---

## YAML Plugin Example (Complete)

```yaml
# plugins/example-site.yaml
name: "example-site"
version: "1.0.0"
base_url: "https://example.org"
mirror_urls:
  - "https://example.net"
  - "https://example.cc"

scraping:
  mode: "scrapy"
  delay_seconds: 2.0
  max_depth: 3

  stages:
    - name: "search_results"
      type: "list"
      url_pattern: "/search/{query}/"
      selectors:
        link: "a.result-link"
        title: "h3.result-title"
      next_stage: "movie_detail"
      pagination:
        enabled: true
        selector: "a.next"
        max_pages: 2

    - name: "movie_detail"
      type: "detail"
      selectors:
        title: "h1.page-title"
        release_name: "span.release"
        size: "span.file-size"
        download_links:
          container: "div.downloads"
          item_group: "div.hoster-group"
          items: "div.hoster-row"
          fields:
            link: "a"
            hoster: "span.name"
          field_attributes:
            link: ["href"]
          multi_value_fields: ["link"]
      conditions:
        min_seeders: 1

categories:
  2000: "Movies"
  5000: "TV"
```

---

## Source Code References

| Component | File |
|---|---|
| `StageScraper` | `src/scavengarr/infrastructure/scraping/scrapy_adapter.py` |
| `ScrapyAdapter` | `src/scavengarr/infrastructure/scraping/scrapy_adapter.py` |
| `HttpxScrapySearchEngine` | `src/scavengarr/infrastructure/torznab/search_engine.py` |
| `ScrapingStage` (schema) | `src/scavengarr/domain/plugins/plugin_schema.py` |
| `StageSelectors` (schema) | `src/scavengarr/domain/plugins/plugin_schema.py` |
| `NestedSelector` (schema) | `src/scavengarr/domain/plugins/plugin_schema.py` |
| `PaginationConfig` (schema) | `src/scavengarr/domain/plugins/plugin_schema.py` |
| `SearchResult` (entity) | `src/scavengarr/domain/plugins/base.py` |
| Unit tests | `tests/unit/infrastructure/test_search_engine.py` |
