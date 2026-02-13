[← Back to Index](./README.md)

# Link Validation

Scavengarr validates download links before including them in search results. Since
indexer sites frequently reference dead, expired, or blocked download URLs, link
validation is essential for providing reliable results to Sonarr, Radarr, and other
Arr applications.

---

## Overview

Link validation is an I/O-dominant operation that runs after scraping and before
result delivery. It checks whether each download URL is reachable by making HTTP
requests, filtering out dead links and promoting alternative links when available.

```
Scraped Results
     │
     ▼
┌──────────────────────────────────────────┐
│  Collect all unique URLs                 │
│  (primary download_link + alternatives   │
│   from download_links)                   │
└──────────┬───────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────┐
│  Batch Validation (parallel)             │
│  - HEAD request first                    │
│  - GET fallback on failure               │
│  - Semaphore-bounded concurrency (20)    │
└──────────┬───────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────┐
│  Apply Validation Results                │
│  - Populate validated_links              │
│  - Promote alternative if primary dead   │
│  - Drop results with zero valid links    │
└──────────┬───────────────────────────────┘
           │
           ▼
     Validated SearchResult[]
```

---

## Why HEAD + GET?

The dual-strategy approach exists because **streaming hosters behave inconsistently**:

| Hoster Behavior | HEAD | GET | Strategy |
|---|---|---|---|
| Standard hosters | 200 | 200 | HEAD succeeds -- fast path |
| veev.to, savefiles.com | 403 | 200 | HEAD blocked, GET fallback needed |
| Dead/expired links | 404 | 404 | Both fail -- link marked invalid |
| Timeout (slow/offline) | timeout | timeout | Both fail -- link marked invalid |

Some streaming hosters (notably veev.to and savefiles.com) return `403 Forbidden` on
HEAD requests but `200 OK` on GET requests. The GET fallback catches these cases.

HEAD is tried first because it is significantly faster -- it does not download the
response body, making it ideal for reachability checks.

---

## Domain Port

The domain defines the validation contract via a `Protocol`:

```python
# src/scavengarr/domain/ports/link_validator.py
class LinkValidatorPort(Protocol):
    async def validate(self, url: str) -> bool:
        """Check if URL is reachable (HEAD first, GET fallback).

        Returns:
            True if URL returns 2xx/3xx on HEAD or GET, False otherwise.
        """
        ...

    async def validate_batch(self, urls: list[str]) -> dict[str, bool]:
        """Validate multiple URLs concurrently.

        Returns:
            Dict mapping url -> is_valid (True/False).
        """
        ...
```

This port is framework-free and lives in the domain layer. The infrastructure layer
provides the concrete HTTP-based implementation.

---

## HTTP Implementation

### `HttpLinkValidator`

The implementation uses `httpx.AsyncClient` for non-blocking HTTP requests:

```python
# src/scavengarr/infrastructure/validation/http_link_validator.py
class HttpLinkValidator:
    def __init__(
        self,
        http_client: AsyncClient,
        timeout_seconds: float = 5.0,
        max_concurrent: int = 20,
    ) -> None:
        self.http_client = http_client
        self.timeout = timeout_seconds
        self._semaphore = asyncio.Semaphore(max_concurrent)
```

### Configuration

| Parameter | Default | Description |
|---|---|---|
| `timeout_seconds` | `5.0` | Maximum time per validation request |
| `max_concurrent` | `20` | Maximum parallel validations (semaphore) |

### Single URL Validation

```python
async def validate(self, url: str) -> bool:
    async with self._semaphore:
        if await self._try_head(url):
            return True
        return await self._try_get(url)
```

The semaphore wraps the entire validation (HEAD + optional GET), ensuring that at
most `max_concurrent` URLs are being checked simultaneously.

### HEAD Request

```python
async def _try_head(self, url: str) -> bool:
    response = await self.http_client.head(
        url,
        timeout=self.timeout,
        follow_redirects=True,
    )
    return response.status_code < 400
```

- Follows redirects (common for download hosters)
- Status < 400 = valid (2xx success, 3xx redirect)
- Status >= 400 = invalid
- Any exception (timeout, connection error) = False (triggers GET fallback)

### GET Fallback

```python
async def _try_get(self, url: str) -> bool:
    response = await self.http_client.get(
        url,
        timeout=self.timeout,
        follow_redirects=True,
    )
    return response.status_code < 400
```

- Same status logic as HEAD
- Only called when HEAD fails
- Catches `TimeoutException`, `HTTPError`, and unexpected exceptions separately
- Each exception type is logged at appropriate level (warning vs error)

---

## Batch Validation

The batch validation method validates all URLs in a single parallel operation:

```python
# src/scavengarr/infrastructure/validation/http_link_validator.py
async def validate_batch(self, urls: list[str]) -> dict[str, bool]:
    if not urls:
        return {}

    tasks = [self.validate(url) for url in urls]
    results = await asyncio.gather(*tasks)

    return dict(zip(urls, results))
```

All URLs are validated concurrently via `asyncio.gather`. The semaphore inside
`validate()` limits actual parallelism to `max_concurrent` (default 20).

### Performance Characteristics

For a batch of N URLs with concurrency limit C and timeout T:

- **Best case** (all HEAD succeed): ~ceil(N/C) * (HEAD latency) seconds
- **Worst case** (all need GET fallback + timeout): ~ceil(N/C) * 2T seconds
- **Typical**: Most validations complete in the HEAD phase, with a few falling through to GET

Example: 50 URLs, 20 concurrency, 5s timeout
- Best case: ~3 rounds of HEAD requests, ~1-2 seconds total
- Worst case: ~3 rounds, each up to 10 seconds (HEAD timeout + GET timeout)

---

## Integration with Search Engine

The `HttpxSearchEngine` uses link validation as part of its search flow.

### URL Collection

Before validation, all unique URLs are collected from all results:

```python
# src/scavengarr/infrastructure/torznab/search_engine.py
def _collect_all_urls(self, results: list[SearchResult]) -> set[str]:
    all_urls: set[str] = set()
    for r in results:
        if r.download_link:
            all_urls.add(r.download_link)
        if r.download_links:
            for entry in r.download_links:
                url = self._extract_url_from_entry(entry)
                if url:
                    all_urls.add(url)
    return all_urls
```

This collects:
- Primary `download_link` from each result
- All alternative URLs from `download_links` entries (dict or string format)

### Single Batch Call

All collected URLs are validated in a **single** `validate_batch()` call. This is
more efficient than validating per-result because:
- Duplicate URLs across results are only checked once
- The semaphore distributes work optimally across all URLs
- Total wall time is minimized

### Validation Application

After batch validation, results are filtered:

```python
# src/scavengarr/infrastructure/torznab/search_engine.py
def _apply_validation(self, result, validation_map) -> bool:
    valid_links = self._collect_valid_links(result, validation_map)
    if not valid_links:
        return False  # Drop result -- no valid links

    # Promote alternative if primary is dead
    if not validation_map.get(result.download_link, False):
        result.download_link = valid_links[0]

    result.validated_links = valid_links
    return True
```

### Link Promotion

When a result's primary `download_link` is dead but alternative links from
`download_links` are valid, the first valid alternative is **promoted** to become the
new `download_link`. This ensures the Torznab XML always contains a working primary link.

```
Before validation:
  download_link: https://hoster1.com/file/abc  (DEAD)
  download_links: [
    {"link": "https://hoster2.com/file/def"},  (ALIVE)
    {"link": "https://hoster3.com/file/ghi"},  (ALIVE)
  ]

After validation:
  download_link: https://hoster2.com/file/def  (PROMOTED)
  validated_links: [
    "https://hoster2.com/file/def",
    "https://hoster3.com/file/ghi",
  ]
```

### Valid Link Collection Order

Valid links are assembled in a deterministic order:

1. Primary `download_link` (if valid)
2. Alternative links from `download_links` (in original order, if valid)
3. Duplicates are skipped

This ensures the `validated_links` list is stable across runs for the same input.

---

## Disabling Validation

Link validation can be disabled for specific use cases (e.g., testing, trusted sources):

```python
# src/scavengarr/infrastructure/torznab/search_engine.py
engine = HttpxSearchEngine(
    http_client=client,
    cache=cache,
    validate_links=False,  # Skip validation
)
```

When disabled, all scraped results pass through without filtering.

---

## Python Plugin Integration

Python plugins that perform their own scraping can still use link validation via the
`validate_results()` method:

```python
# src/scavengarr/infrastructure/torznab/search_engine.py
async def validate_results(
    self,
    results: list[SearchResult],
) -> list[SearchResult]:
    """Validate download links on pre-built SearchResults.

    Used by Python plugins that do their own scraping and return
    SearchResult lists directly.
    """
    if self._validate_links:
        return await self._filter_valid_links(results)
    return results
```

This allows plugins like `boerse.py` to benefit from the same validation infrastructure
without reimplementing it.

---

## Logging

Link validation produces structured log messages at multiple levels:

| Event | Level | Fields |
|---|---|---|
| `batch_validation_started` | INFO | `count` |
| `batch_validation_completed` | INFO | `total`, `valid`, `invalid` |
| `link_head_result` | DEBUG | `url`, `status_code`, `valid` |
| `link_head_failed` | DEBUG | `url`, `error` |
| `link_get_fallback_result` | DEBUG | `url`, `status_code`, `valid` |
| `link_validation_timeout` | WARNING | `url`, `timeout` |
| `link_validation_error` | WARNING | `url`, `error` |
| `link_validation_unexpected_error` | ERROR | `url`, `error` |
| `links_filtered` | INFO | `total`, `valid`, `invalid` |
| `alternative_link_promoted` | INFO | `title`, `failed`, `promoted` |

---

## Source Code References

| Component | File |
|---|---|
| `LinkValidatorPort` (protocol) | `src/scavengarr/domain/ports/link_validator.py` |
| `HttpLinkValidator` | `src/scavengarr/infrastructure/validation/http_link_validator.py` |
| `HttpxSearchEngine` (integration) | `src/scavengarr/infrastructure/torznab/search_engine.py` |
| Unit tests (validator) | `tests/unit/infrastructure/test_link_validator.py` |
| Unit tests (search engine) | `tests/unit/infrastructure/test_search_engine.py` |
