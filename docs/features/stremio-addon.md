[< Back to Index](./README.md)

# Stremio Addon

> Stream resolution from Scavengarr plugins directly into the Stremio media player.

---

## Overview

Scavengarr includes a full **Stremio addon** that provides catalog browsing, search, and stream resolution. It bridges plugin search results with Stremio's stream protocol, resolving hoster embed URLs into direct video playback links.

The addon supports both IMDb (`tt*`) and TMDB (`tmdb:*`) identifiers and ranks streams by language, quality, and hoster reliability.

---

## Architecture

```
Stremio App
  ├── GET /manifest.json             → addon metadata + catalogs
  ├── GET /catalog/{type}/{id}.json  → TMDB trending / search
  ├── GET /stream/{type}/{id}.json   → plugin search → ranked streams
  └── GET /play/{stream_id}          → hoster resolution → 302 video URL
```

### Request Flow (Stream Resolution)

```
IMDb ID → TMDB title lookup → parallel plugin search → title matching
  → quality/language parsing → ranking → dead link probing → proxy caching
  → sorted StremioStream list
```

1. **Title resolution** — Look up the German title + year via TMDB (or IMDB/Wikidata fallback)
2. **Plugin search** — Search all streaming plugins in parallel (bounded concurrency)
3. **Title matching** — Filter false positives (sequels, spin-offs) via scoring
4. **Stream conversion** — Convert `SearchResult` objects into `RankedStream` with parsed quality/language
5. **Ranking** — Sort by language preference, quality, and hoster bonus
6. **Probing** — Optionally probe top N hoster URLs to filter dead links
7. **Pre-resolution** — Resolve top N hoster embed URLs to direct video URLs (for `behaviorHints.proxyHeaders`)
8. **Caching** — Cache resolved URLs; generate `/play/{stream_id}` fallback for unresolved streams
9. **Formatting** — Return sorted `StremioStream` objects with `behaviorHints` to the Stremio app

---

## Endpoints

All endpoints are prefixed with `/api/v1/stremio/`.

### Manifest

```
GET /api/v1/stremio/manifest.json
```

Returns the Stremio addon manifest with:
- Addon ID: `community.scavengarr`
- Supported types: `movie`, `series`
- Catalogs: trending movies, trending series
- ID prefixes: `tt` (IMDb), `tmdb:` (TMDB)
- Resources: `catalog`, `stream`

### Catalog

```
GET /api/v1/stremio/catalog/{content_type}/{catalog_id}.json
GET /api/v1/stremio/catalog/{content_type}/{catalog_id}/search={query}.json
```

- Trending: returns TMDB trending movies or series
- Search: full-text search via TMDB API (German locale)
- Response: `{"metas": [StremioMetaPreview, ...]}`

### Stream Resolution

```
GET /api/v1/stremio/stream/{content_type}/{stream_id}.json
```

- Movie: `stream_id` = `tt1234567` or `tmdb:12345`
- Series: `stream_id` = `tt1234567:1:5` (season 1, episode 5)
- Response: `{"streams": [StremioStream, ...]}`

Each stream contains:
- `name` — Title with year and quality badge
- `description` — Plugin source, language, hoster, file size
- `url` — Direct video URL (pre-resolved) or proxy URL (`/play/{stream_id}`) as fallback
- `behaviorHints` — (optional) Stremio playback hints including `proxyHeaders`

### Stream behaviorHints (proxyHeaders)

When a stream is pre-resolved at `/stream` time, the response includes `behaviorHints`:

```json
{
  "name": "Movie Title [1080p]",
  "url": "https://cdn.hoster.com/video.mp4",
  "behaviorHints": {
    "notWebReady": true,
    "proxyHeaders": {
      "request": {
        "User-Agent": "Mozilla/5.0 ...",
        "Referer": "https://hoster.com/"
      }
    }
  }
}
```

- `notWebReady: true` activates Stremio's local streaming server proxy
- `proxyHeaders.request` tells Stremio what HTTP headers to send when fetching video content
- This is required because most hoster CDNs reject requests without a valid `Referer` header

**Platform support:**
| Platform | Status |
|---|---|
| Desktop (Electron) | Full support |
| Android | Partial (some Referer bugs with specific hosters) |
| iOS | Partial (KSPlayer engine only) |
| Web | Not supported (CORS restrictions) |

Streams that fail pre-resolution fall back to the `/play/` proxy endpoint.

### Play (Proxy Fallback)

```
GET /api/v1/stremio/play/{stream_id}
```

Fallback endpoint for streams that could not be pre-resolved at `/stream` time:
1. Look up `stream_id` in the stream link cache
2. Resolve via `HosterResolverRegistry` (e.g., VOE, Filemoon, Streamtape)
3. Return **302 redirect** to the direct `.mp4`/`.m3u8` URL
4. Return **502** if resolution fails (never redirects to embed pages)

### Health

```
GET /api/v1/stremio/health
```

Reports component status, supported hosters, and metrics.

---

## Title Matching

Title matching prevents false positives when plugin results include sequels, spin-offs, or unrelated titles.

| Feature | Details |
|---|---|
| Scoring | `rapidfuzz.fuzz.token_sort_ratio` + `token_set_ratio` (C++ backend) |
| Year bonus | +0.2 if release year matches reference (within tolerance) |
| Year penalty | -0.3 if year is present but wrong |
| Sequel penalty | -0.35 if result has trailing number not in reference |
| Threshold | 0.7 minimum score (configurable) |
| Year tolerance | Movies: +/-1 year, Series: +/-3 years |
| Title candidates | 4 candidates per result (raw, guessit-parsed, release name) |
| Alt titles | Matches against both German and English titles |

---

## Stream Ranking

Streams are ranked using a weighted scoring formula:

```
rank_score = language_score + (quality.value * quality_multiplier) + hoster_bonus
```

### Default Weights

| Language | Score |
|---|---|
| German Dub (`de`) | 1000 |
| German Sub (`de-sub`) | 500 |
| English Sub (`en-sub`) | 200 |
| English Dub (`en`) | 150 |
| Unknown | 100 |

| Quality | Value |
|---|---|
| UHD 4K | 60 |
| HD 1080p | 50 |
| HD 720p | 40 |
| SD | 30 |
| TS | 20 |
| CAM | 10 |

| Hoster | Bonus |
|---|---|
| SuperVideo | 5 |
| VOE | 4 |
| Filemoon | 3 |
| Streamtape | 2 |
| DoodStream | 1 |

---

## TMDB Integration

The addon uses TMDB for title resolution and catalog browsing.

| Feature | Details |
|---|---|
| Title resolution | `find_by_imdb_id()` — IMDb-to-TMDB lookup |
| German titles | `language=de-DE` locale in all requests |
| Alt titles | English title included as alternative for matching |
| Trending | `/trending/movie` and `/trending/tv` endpoints |
| Search | `/search/movie` and `/search/tv` with German locale |
| Posters | `https://image.tmdb.org/t/p/w500{poster_path}` |
| Caching | Find: 24h, Trending: 6h, Search: 1h |

### IMDB Fallback (No API Key)

When no TMDB API key is configured, the addon falls back to free sources:

| Source | Purpose |
|---|---|
| IMDB Suggest API | Title resolution and search |
| Wikidata API | German title lookup via IMDB property (P345) |

Limitations: no trending catalogs, no TMDB numeric ID resolution.

---

## Configuration

All Stremio settings are grouped under `StremioConfig`:

### Stream Ranking

| Setting | Default | Description |
|---|---|---|
| `language_scores` | de=1000, de-sub=500, ... | Language weight map |
| `quality_multiplier` | 10 | Quality as tie-breaker |
| `hoster_scores` | supervideo=5, voe=4, ... | Hoster reliability bonus |

### Plugin Search

| Setting | Default | Description |
|---|---|---|
| `max_concurrent_plugins` | 10 | Parallel plugin search limit |
| `max_results_per_plugin` | 100 | Results per plugin (Stremio limit) |
| `plugin_timeout_seconds` | 30 | Per-plugin timeout |

### Title Matching

| Setting | Default | Description |
|---|---|---|
| `title_match_threshold` | 0.7 | Minimum score to keep result |
| `title_year_bonus` | 0.2 | Score bonus for year match |
| `title_year_penalty` | -0.3 | Score penalty for year mismatch |
| `title_sequel_penalty` | -0.35 | Penalty for sequels |

### Resolution & Concurrency

| Setting | Default | Description |
|---|---|---|
| `resolve_target_count` | 15 | Target resolved video streams before early-stop |
| `max_concurrent_playwright` | 5 | Max parallel Playwright plugin searches |

### Circuit Breaker

| Setting | Default | Description |
|---|---|---|
| `failure_threshold` | 5 | Consecutive failures before opening circuit |
| `cooldown_seconds` | 60 | Seconds to wait before half-open probe |

When a plugin accumulates `failure_threshold` consecutive failures, the circuit breaker
opens and skips the plugin for `cooldown_seconds`. After cooldown, a single probe request
is allowed (half-open). If the probe succeeds, the breaker resets; if it fails, cooldown restarts.

### Global Concurrency Pool

The `ConcurrencyPool` provides separate httpx and Playwright slot pools with fair-share
distribution across concurrent requests:
- `httpx_slots` = `max_concurrent_plugins` (default 10)
- `pw_slots` = `max_concurrent_playwright` (default 5)
- Fair share: `max(1, total_slots // active_requests)` per request

### Multi-Language Search

Plugins declare `languages: list[str]` (default `["de"]`). The use case groups plugins
by language, fetches TMDB titles for each unique language in parallel, and searches each
group with language-specific queries. A plugin with `languages=["de", "en"]` gets searched
with both German and English title queries.

### Stream Deduplication

After sorting, per-hoster deduplication keeps only the best-ranked stream per hoster.
This prevents duplicate links from the same hoster (e.g., 5 VOE links from 5 plugins
are collapsed to the single best-ranked VOE link).

### Caching & Probing

| Setting | Default | Description |
|---|---|---|
| `stream_link_ttl_seconds` | 7200 (2h) | Hoster URL cache TTL |
| `probe_at_stream_time` | true | Enable dead link filtering (skipped when resolve_fn is active) |
| `probe_concurrency` | 10 | Parallel probe limit |
| `probe_timeout_seconds` | 10 | Per-URL probe timeout |
| `max_probe_count` | 50 | Max streams to probe |

---

## Testing

| Test File | Coverage |
|---|---|
| `test_stream_converter.py` | SearchResult to RankedStream conversion |
| `test_stream_sorter.py` | Stream ranking and sorting |
| `test_title_matcher.py` | Title-match scoring and filtering |
| `test_release_parser.py` | Quality/language parsing from release names |
| `test_tmdb_client.py` | TMDB httpx client with caching |
| `test_imdb_fallback.py` | IMDB Suggest + Wikidata fallback |
| `test_stream_link_cache.py` | Stream link cache repository |
| E2E tests | 112 Stremio endpoint tests (manifest, catalog, stream, play, streamable link verification) |

---

## Source Code

| Component | Path |
|---|---|
| Domain entities | `src/scavengarr/domain/entities/stremio.py` |
| TMDB port | `src/scavengarr/domain/ports/tmdb.py` |
| Stream link port | `src/scavengarr/domain/ports/stream_link_repository.py` |
| Stream use case | `src/scavengarr/application/use_cases/stremio_stream.py` |
| Catalog use case | `src/scavengarr/application/use_cases/stremio_catalog.py` |
| Stream converter | `src/scavengarr/infrastructure/stremio/stream_converter.py` |
| Stream sorter | `src/scavengarr/infrastructure/stremio/stream_sorter.py` |
| Title matcher | `src/scavengarr/infrastructure/stremio/title_matcher.py` |
| Release parser | `src/scavengarr/infrastructure/stremio/release_parser.py` |
| TMDB client | `src/scavengarr/infrastructure/tmdb/client.py` |
| IMDB fallback | `src/scavengarr/infrastructure/tmdb/imdb_fallback.py` |
| Stremio router | `src/scavengarr/interfaces/api/stremio/router.py` |
