[← Back to Index](../features/README.md)

# Plan: Search Result Caching

**Status:** Core implemented (2026-09-28); Stremio search cache with stale-while-revalidate, single-flight, late plugins and early answer implemented (2026-10-04). Open for Torznab: cache invalidation endpoint, stale-while-revalidate, cache metrics, a TTL-expiry test
**Priority:** Medium
**Related:** `src/scavengarr/application/use_cases/torznab_search.py`, `src/scavengarr/infrastructure/cache/`

## Implementation Summary

Search result caching is implemented in `TorznabSearchUseCase`:

- **Cache key:** SHA-256 hash of `plugin_name:query:category` (query lowercased and stripped, 16-char hex prefix)
- **Default TTL:** 900 seconds (15 minutes), configurable via `CACHE_SEARCH_TTL_SECONDS`
- **Disable:** Set `cache.search_ttl_seconds: 0` to disable caching
- **Per-plugin TTL:** plugin class attribute `cache_ttl` overrides the global TTL
- **HTTP header:** `X-Cache: HIT` or `X-Cache: MISS` on every search response
- **Pagination:** Server-side slicing via `offset`/`limit` params works with cached results (subsequent pages hit cache)
- **Error handling:** Cache read/write errors are logged but never fail the request
- **Backend:** Uses existing `CachePort` (diskcache or Redis)

Key files:
- `src/scavengarr/application/use_cases/torznab_search.py` — `_search_cache_key()`, `_cache_read()`, `_cache_write()`
- `src/scavengarr/infrastructure/config/schema.py` — `search_ttl_seconds` config field
- `src/scavengarr/interfaces/api/torznab/router.py` — `X-Cache` header injection

## Stremio: Search Cache, Late Plugins, Early Answer (implemented 2026-10-04)

**Problem:**
- Stremio requests were not cached. Production served the same title twice within 17–18 s (Avengers: Endgame, Severance), each time a full fan-out of ~15 s and 50–270 outbound requests.
- A plugin cut by the request deadline lost all its results, even when its search finished seconds later (kinoking, kinoger or moflix while solving a challenge).
- The answer waited for the slowest plugin up to the 10 s search budget (`docs/plans/pi-performance.md`).

**Design**, decided by the maintainer: cache with stale-while-revalidate and single-flight, partial results, and an early answer with the cache.

- **What is cached:** the title-filtered search results of a request (`list[SearchResult]`), not the resolved streams, because hoster stream URLs expire and some are bound to the resolving IP. Resolution and ranking run on every request. Key: `stremio:search:{imdb_id}:{season}:{episode}`. Value: results, the unfiltered count, and the wall-clock time stored (`CachePort`, i.e. diskcache or Redis, so it survives restarts).
- **Fresh and stale:**
  - An entry younger than `cache.search_ttl_seconds` is fresh: the request skips the search.
  - An older one stays usable for 6 h more (stale-while-revalidate, RFC 5861): the request answers from it, and a background search refreshes it.
  - `cache.search_ttl_seconds: 0` turns the cache off, as for Torznab.
- **Single-flight:** requests for one key share one running search (one future per key, in-process). Background refreshes do the same.
- **Late plugins (partial results per plugin):** a plugin the deadline cuts is no longer cancelled. It runs on for at most another `plugin_timeout_seconds`, and its results are title-filtered and merged into the cache entry. The next request has them; the slow site is not asked twice.
- **Early answer:** on a cache miss the search ends at `stremio.search_soft_deadline_seconds` (default 7 s); plugins still running continue as late plugins. If there is no result at all by then, the request waits for the late plugins until the hard search deadline (`plugin_timeout_seconds`, 10 s), as before. Resolution keeps `resolve_target_count`: every shown stream is resolved, but a request answers sooner, and the next one finds the complete set in the cache.

**Concurrency slots:** a late plugin releases its concurrency slot when it is cut. Holding it would make the next requests' plugins queue behind slow sites. Late plugins are bounded by the plugins of one request and by `plugin_timeout_seconds`; browser work stays bounded by the stealth pool. App shutdown cancels them (`StremioStreamUseCase.aclose`).

**Circuit breaker:** a plugin cut by the soft deadline runs on and is not blamed for the cut; it counts as a timeout only when it is still running at the end of its extra time, and its answer reports like any other (results reset the breaker). The first version counted the cut (when the plugin had half its timeout), which opened the breaker for plugins that answered a few seconds later without hits (kinox for movies, end-to-end test 2026-10-04).

**Affected files:**
- `application/stremio/search_cache.py`: `CachedSearch` entries, key, fresh/stale, backend errors.
- `application/use_cases/stremio_stream.py`: cache lookup, single-flight, soft deadline, background completion, `aclose()`.
- `application/stremio/plugin_search.py`: late plugin tasks instead of cancelling (`LateSearch`, `finish_late`).
- config (`stremio.search_soft_deadline_seconds`), `interfaces/composition.py` (wiring), docs.

**Tests:** miss writes the cache; a fresh hit skips the search; a stale hit answers and refreshes in the background; concurrent requests search once; a cut plugin is not cancelled and its results land in the cache; the soft deadline answers early; without results the request waits for the hard deadline.

## Original Problem

Every Torznab search request triggers a full scraping pipeline: HTTP requests to the target site, multi-stage HTML parsing, link validation, and result assembly. This is slow (seconds per request) and puts unnecessary load on target sites when the same query is repeated within a short time window.

Prowlarr and other Arr applications frequently retry the same search (e.g., when monitoring for new releases), so caching search results significantly reduces latency and external request volume.

## Design

### Cache Key

The cache key is a hash of the parameters that uniquely identify a search (as implemented in `torznab_search.py`):

```python
def _search_cache_key(plugin_name: str, query: str, category: int | None) -> str:
    """Compute deterministic cache key for a search query."""
    cat_str = str(category) if category is not None else "none"
    raw = f"{plugin_name}:{query.lower().strip()}:{cat_str}"
    return f"search:{hashlib.sha256(raw.encode()).hexdigest()[:16]}"
```

Components:
- **plugin_name**: Different plugins search different sites
- **query**: The search term, lowercased and stripped
- **category**: The single Torznab category passed to the use case (the router uses the first value of `cat`), or `none`

### Cache Value

The cached value is the list of `SearchResult` entities, stored via `CachePort.set()` (the adapters serialize it; same mechanism as the CrawlJob cache). Empty result lists are not cached.

### TTL Strategy

- Default TTL: 15 minutes (`cache.search_ttl_seconds`, env `CACHE_SEARCH_TTL_SECONDS`; the shipped `data/config.yaml` sets 1800)
- Per-plugin override: plugins set the class attribute `cache_ttl` (`HttpxPluginBase`/`PlaywrightPluginBase`, default `None` = global TTL)
- Manual invalidation: admin endpoint `DELETE /api/cache` clears all cached searches (not implemented)
- Stale-while-revalidate: optionally serve stale results while refreshing in background (not implemented)

```yaml
cache:
  backend: "diskcache"        # or "redis"
  search_ttl_seconds: 900     # 15 minutes default
  ttl_seconds: 3600           # default TTL for other cache entries (CrawlJobs)
```

### Integration Point

Caching is applied in `TorznabSearchUseCase.execute()`, wrapping the plugin search (simplified):

```python
async def execute(self, q: TorznabQuery) -> SearchResponse:
    cache_key = _search_cache_key(q.plugin_name, q.query, q.category)

    # Check cache first (errors are logged, never raised)
    raw_results = await self._cache_read(cache_key, q)
    if raw_results is None:
        # Execute search pipeline, then store non-empty results
        raw_results = ...  # plugin search
        if raw_results:
            await self._cache_write(cache_key, raw_results, q, plugin)

    # Validation, CrawlJob creation and offset/limit slicing run on every request
    ...
```

The `CachePort` is passed in by the Torznab router (`cache=state.cache`, `search_ttl=config.cache.search_ttl_seconds`), which sets the `X-Cache` header from `SearchResponse.cache_hit`.

### Existing Infrastructure

The `CachePort` protocol already supports all required operations:

- `get(key)` / `set(key, value, ttl=)` / `delete(key)` / `exists(key)` / `clear()`
- Two implementations: `DiskcacheAdapter` (SQLite) and `RedisAdapter`
- Both support TTL-based expiration
- The CrawlJob cache already uses this infrastructure successfully

No new adapters are needed — only the use case needs cache-aware logic.

## Checklist

### Phase 1: Core Implementation

- [x] Add `search_ttl_seconds` to configuration schema
- [x] Implement `_search_cache_key()` function
- [x] Add cache lookup/store to `TorznabSearchUseCase`
- [x] Pass `CachePort` to use case via dependency injection (Torznab router, from `AppState`)

### Phase 2: Cache Control

- [x] Add `X-Cache` header to HTTP responses (HIT/MISS); no `Cache-Control` header
- [ ] Add admin endpoint for cache invalidation (`DELETE /api/cache`)
- [x] Add per-plugin TTL override (plugin class attribute `cache_ttl`)
- [x] Log cache hits/stores with structlog context (`search_cache_hit`, `search_cache_stored`)

### Phase 3: Testing

- [x] Unit test: cache hit returns stored results without calling search engine
- [x] Unit test: cache miss triggers search and stores results
- [ ] Unit test: TTL expiration causes re-fetch
- [x] Unit test: different inputs produce different cache keys (categories, `None` vs explicit)
- [x] Unit test: cache key is deterministic (same input = same key)

### Phase 4: Observability

- [ ] Add cache hit rate metric (counter)
- [ ] Add cache size metric (gauge)
- [x] Log TTL and cache key in search request context (`search_cache_stored` logs `ttl` and `cache_key`)
- [ ] Dashboard-ready structured log fields

## Dependencies

- No new packages required
- Uses existing `CachePort` and adapter implementations
- Configuration change to `CacheConfig` (new field `search_ttl_seconds`)
