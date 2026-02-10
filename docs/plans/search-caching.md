# Plan: Search Result Caching

**Status:** Planned
**Priority:** Medium
**Related:** `src/scavengarr/domain/ports/cache.py`, `src/scavengarr/infrastructure/cache/`

## Problem

Every Torznab search request triggers a full scraping pipeline: HTTP requests to
the target site, multi-stage HTML parsing, link validation, and result assembly.
This is slow (seconds per request) and puts unnecessary load on target sites when
the same query is repeated within a short time window.

Prowlarr and other Arr applications frequently retry the same search (e.g., when
monitoring for new releases), so caching search results would significantly reduce
latency and external request volume.

## Design

### Cache Key

The cache key is a hash of the parameters that uniquely identify a search:

```python
import hashlib

def search_cache_key(plugin_name: str, query: str, categories: tuple[int, ...]) -> str:
    raw = f"{plugin_name}:{query}:{categories}"
    return f"search:{hashlib.sha256(raw.encode()).hexdigest()[:16]}"
```

Components:
- **plugin_name**: Different plugins search different sites
- **query**: The search term
- **categories**: Torznab category filter (sorted tuple for deterministic hashing)

### Cache Value

The cached value is a list of `SearchResult` entities, serialized via pickle
(same mechanism already used by the CrawlJob cache).

### TTL Strategy

- Default TTL: 15 minutes (configurable via `cache.search_ttl_seconds`)
- Per-plugin override: plugins can declare `cache_ttl` in their config
- Manual invalidation: admin endpoint `DELETE /api/cache` clears all cached searches
- Stale-while-revalidate: optionally serve stale results while refreshing in background

```yaml
cache:
  backend: "diskcache"        # or "redis"
  search_ttl_seconds: 900     # 15 minutes default
  crawljob_ttl_seconds: 3600  # existing CrawlJob TTL
```

### Integration Point

Caching is applied in the `TorznabSearchUseCase`, wrapping the search engine call:

```python
async def execute(self, query: TorznabQuery) -> list[TorznabItem]:
    cache_key = search_cache_key(query.plugin, query.q, query.categories)

    # Check cache first
    cached = await self._cache.get(cache_key)
    if cached is not None:
        return cached

    # Execute search pipeline
    results = await self._search_engine.search(plugin, query.q)
    results = await self._search_engine.validate_results(results)

    # Cache results
    await self._cache.set(cache_key, results, ttl=self._search_ttl)
    return results
```

### Existing Infrastructure

The `CachePort` protocol already supports all required operations:

- `get(key)` / `set(key, value, ttl=)` / `delete(key)` / `exists(key)` / `clear()`
- Two implementations: `DiskcacheAdapter` (SQLite) and `RedisAdapter`
- Both support TTL-based expiration
- The CrawlJob cache already uses this infrastructure successfully

No new adapters are needed -- only the use case needs cache-aware logic.

## Checklist

### Phase 1: Core Implementation
- [ ] Add `search_ttl_seconds` to configuration schema
- [ ] Implement `search_cache_key()` function
- [ ] Add cache lookup/store to `TorznabSearchUseCase`
- [ ] Pass `CachePort` to use case via dependency injection (composition root)

### Phase 2: Cache Control
- [ ] Add `Cache-Control` / `X-Cache` headers to HTTP responses (HIT/MISS)
- [ ] Add admin endpoint for cache invalidation (`DELETE /api/cache`)
- [ ] Add per-plugin TTL override in plugin schema
- [ ] Log cache hits/misses with structlog context

### Phase 3: Testing
- [ ] Unit test: cache hit returns stored results without calling search engine
- [ ] Unit test: cache miss triggers search and stores results
- [ ] Unit test: TTL expiration causes re-fetch
- [ ] Unit test: different queries produce different cache keys
- [ ] Unit test: cache key is deterministic (same input = same key)

### Phase 4: Observability
- [ ] Add cache hit rate metric (counter)
- [ ] Add cache size metric (gauge)
- [ ] Log TTL and cache key in search request context
- [ ] Dashboard-ready structured log fields

## Dependencies

- No new packages required
- Uses existing `CachePort` and adapter implementations
- Configuration change to `AppConfig` (new field)
