[← Back to Index](./README.md)

# Link Validation

> Before Torznab results are returned, their download links are checked in parallel (HEAD first, GET fallback) and dead links and results with no valid link are dropped.

---

## Overview

Indexer sites frequently reference dead, expired or blocked download URLs. Link validation runs after the plugin search and before CrawlJob creation. Every Torznab search passes the plugin's results through `HttpxSearchEngine.validate_results()` automatically — plugins do not call it themselves.

```text
Plugin results
     │
     ▼
┌──────────────────────────────────────────┐
│  Split: pre-validated results pass       │
│  through (validated_links already set)   │
└──────────┬───────────────────────────────┘
           ▼
┌──────────────────────────────────────────┐
│  Collect all unique URLs                 │
│  (download_link + download_links)        │
└──────────┬───────────────────────────────┘
           ▼
┌──────────────────────────────────────────┐
│  validate_batch() — one parallel call    │
│  - dedup, drop non-http(s) strings       │
│  - in-memory result cache                │
│  - HEAD first, GET fallback              │
│  - semaphore-bounded concurrency         │
└──────────┬───────────────────────────────┘
           ▼
┌──────────────────────────────────────────┐
│  Apply results per SearchResult          │
│  - populate validated_links              │
│  - promote alternative if primary dead   │
│  - drop results with zero valid links    │
└──────────┬───────────────────────────────┘
           ▼
     Validated SearchResult[]
```

---

## Why HEAD + GET?

Streaming hosters behave inconsistently:

| Hoster behavior | HEAD | GET | Outcome |
|---|---|---|---|
| Standard hosters | 200 | 200 | HEAD succeeds — fast path |
| HEAD blocked (e.g. veev.to, savefiles.com) | 403 | 200 | GET fallback marks the link valid |
| Dead/expired links | 404 | 404 | Both fail — link invalid |
| Timeout (slow/offline) | timeout | timeout | Both fail — link invalid |

HEAD is tried first because it does not download the response body. Any HEAD failure (status ≥ 400 or an exception) triggers the GET fallback. Validation only checks the HTTP status; it does not detect hoster-specific "file not found" pages served with 200.

---

## Domain Port

There is no dedicated link-validator port. The domain defines `SearchEnginePort`, which the application layer uses; `HttpLinkValidator` is an infrastructure detail of `HttpxSearchEngine`.

```python
# src/scavengarr/domain/ports/search_engine.py
@runtime_checkable
class SearchEnginePort(Protocol):
    async def validate_results(
        self, results: list[SearchResult]
    ) -> list[SearchResult]: ...
```

`TorznabSearchUseCase` calls `validate_results()` on the raw plugin results; any exception is re-raised as `TorznabExternalError` (HTTP 502 in dev/test, empty feed with 200 in prod).

---

## HTTP Implementation

### HttpLinkValidator

```python
# src/scavengarr/infrastructure/validation/http_link_validator.py (simplified)
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
        self._cache: dict[str, _ValidationCacheEntry] = {}
```

It uses the shared `httpx.AsyncClient` (with the application's rate limiting and retry transport).

### Single URL Validation

```python
# (simplified)
async def validate(self, url: str) -> bool:
    cached = self._cache.get(url)
    if cached is not None and not cached.is_expired:
        return cached.is_valid

    async with self._semaphore:
        if await self._try_head(url):
            self._cache[url] = _ValidationCacheEntry(True, _CACHE_TTL_VALID)
            return True
        is_valid = await self._try_get(url)
        ttl = _CACHE_TTL_VALID if is_valid else _CACHE_TTL_INVALID
        self._cache[url] = _ValidationCacheEntry(is_valid, ttl)
        return is_valid
```

- The semaphore wraps HEAD and the optional GET, so at most `max_concurrent` URLs are checked at once.
- `_try_head()` and `_try_get()` both send the request with `follow_redirects=True` and the validator's timeout; status `< 400` is valid.
- Exceptions never propagate: every failure returns `False`.

### Result Cache

Validation outcomes are cached in memory per process (not in the diskcache/Redis cache):

| Outcome | TTL |
|---|---|
| Valid | 6 hours (`_CACHE_TTL_VALID = 21600`) |
| Invalid | 15 minutes (`_CACHE_TTL_INVALID = 900`) |

Cache hits skip the semaphore and the network entirely.

---

## Batch Validation

```python
# src/scavengarr/infrastructure/validation/http_link_validator.py (simplified)
async def validate_batch(self, urls: list[str]) -> dict[str, bool]:
    if not urls:
        return {}
    unique_urls = [
        u for u in dict.fromkeys(urls) if u.startswith(("http://", "https://"))
    ]
    results = await asyncio.gather(*(self.validate(u) for u in unique_urls))
    unique_map = dict(zip(unique_urls, results))
    return {url: unique_map.get(url, False) for url in urls}
```

- Duplicates are validated once (order preserved).
- Strings that do not start with `http://` or `https://` are not requested and are marked invalid.
- All unique URLs run concurrently via `asyncio.gather`; the semaphore limits real parallelism.

For N uncached URLs with concurrency C and timeout T, wall time ranges from about `ceil(N/C)` × HEAD latency (all HEAD succeed) to about `ceil(N/C)` × 2T (every URL needs GET and times out).

---

## Integration with the Search Engine

### Pre-Validated Results

Results whose `validated_links` is already populated by the plugin are passed through without HTTP checks. Plugins behind anti-bot protection (e.g. `animeloads` behind DDoS-Guard) use this, because validation via httpx would always fail for them.

### URL Collection

All unique URLs from the remaining results are collected and validated in a **single** `validate_batch()` call:

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

`download_links` entries may be dicts (URL under the `link` key) or plain strings. If no URL at all is collected, a `no_download_links_to_validate` warning is logged and all results are returned unfiltered.

### Applying Results

```python
# src/scavengarr/infrastructure/torznab/search_engine.py (simplified)
def _apply_validation(self, result, validation_map) -> bool:
    valid_links = self._collect_valid_links(result, validation_map)
    if not valid_links:
        return False  # drop result — no valid links

    if not validation_map.get(result.download_link, False):
        result.download_link = valid_links[0]  # promote alternative

    result.validated_links = valid_links
    return True
```

Results with an empty `download_link` are dropped as well. The returned list contains the pre-validated results first, followed by the validated ones.

### Link Promotion

When the primary `download_link` is dead but alternatives are valid, the first valid alternative becomes the new `download_link`, so the Torznab `<guid>` always refers to a working link:

```text
Before validation:
  download_link: https://hoster1.example/file/abc  (DEAD)
  download_links: [
    {"link": "https://hoster2.example/file/def"},  (ALIVE)
    {"link": "https://hoster3.example/file/ghi"},  (ALIVE)
  ]

After validation:
  download_link: https://hoster2.example/file/def  (PROMOTED)
  validated_links: [
    "https://hoster2.example/file/def",
    "https://hoster3.example/file/ghi",
  ]
```

### Valid Link Order

`validated_links` is assembled deterministically:

1. Primary `download_link` (if valid).
1. Alternatives from `download_links` in their original order (if valid).
1. Duplicates are skipped.

This list becomes the CrawlJob's link list (see [CrawlJob System](./crawljob-system.md)).

---

## Configuration

| Key (YAML, top level) | Default | Description |
|---|---|---|
| `validate_download_links` | `true` | `false` makes `validate_results()` return all results unchanged |
| `validation_timeout_seconds` | `5.0` | Timeout per HEAD/GET request |
| `validation_max_concurrent` | `20` | Semaphore size (overridden by auto-tuning, see below) |

These keys have no `SCAVENGARR_*` environment variable; set them in the YAML config file.

With `stremio.auto_tune_all: true` (the default), startup overwrites `validation_max_concurrent` with `max(5, min(cpu_cores * 5, 120))` based on the detected container/host CPU count, regardless of the configured value. See [Configuration](./configuration.md) for details.

---

## Logging

| Event | Level | Fields |
|---|---|---|
| `batch_validation_started` | INFO | `total`, `unique`, `duplicates_skipped` |
| `batch_validation_completed` | INFO | `total`, `unique`, `valid`, `invalid` |
| `link_head_result` | DEBUG | `url`, `status_code`, `valid` |
| `link_head_timeout` | DEBUG | `url` |
| `link_head_http_error` | DEBUG | `url`, `error` |
| `link_head_failed` | DEBUG | `url`, `error` |
| `link_get_fallback_result` | DEBUG | `url`, `status_code`, `valid` |
| `link_validation_timeout` | WARNING | `url`, `timeout` |
| `link_validation_http_error` | WARNING | `url`, `error` |
| `link_validation_unexpected_error` | WARNING | `url`, `error` |
| `no_download_links_to_validate` | WARNING | — |
| `links_filtered` | INFO | `total`, `valid`, `invalid`, `pre_validated` (only when at least one result was dropped) |
| `alternative_link_promoted` | INFO | `title`, `failed`, `promoted` |

---

## Source Code References

| Component | Path |
|---|---|
| `SearchEnginePort` | `src/scavengarr/domain/ports/search_engine.py` |
| `HttpLinkValidator` | `src/scavengarr/infrastructure/validation/http_link_validator.py` |
| `HttpxSearchEngine` | `src/scavengarr/infrastructure/torznab/search_engine.py` |
| Caller (`TorznabSearchUseCase`) | `src/scavengarr/application/use_cases/torznab_search.py` |
| Wiring and auto-tuning | `src/scavengarr/interfaces/composition.py` |
| Unit tests (validator) | `tests/unit/infrastructure/test_link_validator.py` |
| Unit tests (search engine) | `tests/unit/infrastructure/test_search_engine.py` |
