[← Back to Index](./README.md)

# Stremio Addon

> Stream resolution from Scavengarr plugins directly into the Stremio media player.

---

## Overview

Scavengarr includes a **Stremio addon** that provides catalog browsing, catalog search, and stream resolution. It bridges plugin search results with Stremio's stream protocol and resolves hoster embed URLs into direct video playback links via the [Hoster Resolver System](./hoster-resolvers.md).

The addon accepts IMDb (`tt*`) and TMDB (`tmdb:*`) identifiers and ranks streams by language, quality, and hoster reliability.

---

## Architecture

```text
Stremio App
  ├── GET /manifest.json                     → addon metadata + catalogs
  ├── GET /catalog/{type}/{id}.json          → TMDB trending
  ├── GET /catalog/{type}/{id}/search={q}.json → TMDB search
  ├── GET /stream/{type}/{id}.json           → plugin search → resolved, ranked streams
  ├── GET /play/{stream_id}                  → hoster resolution → 302 video URL
  ├── GET /proxy/{stream_id}/{path:path}     → HLS proxy (manifests + segments)
  └── GET /health                            → component status
```

### Request Flow (Stream Resolution)

```text
IMDb/TMDB ID → title lookup per plugin language → plugin search → episode filter
  → link validation → title matching → quality/language parsing → ranking
  → hoster resolution, one stream per hoster + playback check (deadline, early stop)
  → link cache → StremioStream list
```

1. **Plugin selection** — all plugins with `provides` = `stream` or `both`, or the scored top-N when scored selection is active (see [Plugin Scoring & Probing](./plugin-scoring-and-probing.md)).
1. **Title resolution** — per plugin language, look up title + year via TMDB `/find` (or the IMDB Suggest/Wikidata fallback); `tmdb:` IDs are resolved via the TMDB ID.
1. **Plugin search** — `PluginSearchRunner` searches each language group with the full title and, if the title contains `:`, the base title before the colon; bounded by the global `ConcurrencyPool`, with circuit breaker. The search ends `plugin_timeout_seconds` after the request started: plugins waiting for a concurrency slot use up that budget too, running ones are cut at the deadline, queued ones are skipped. Results of fallback queries are deduplicated by `download_link`.
1. **Episode filtering** — for series requests, results are filtered by season/episode (guessit on release names, falling back to episode labels such as `1x5` or `S01E05` in `download_links`). Multi-episode and multi-season releases (`S01E01-E03`, `S01-S03`) are kept when they contain the requested episode/season.
1. **Link validation** — Python plugin results are validated by the search engine.
1. **Title matching** — false positives (sequels, spin-offs) are filtered via fuzzy scoring.
1. **Stream conversion** — `SearchResult` objects become `RankedStream` objects with parsed quality/language.
1. **Ranking** — sort by language, quality, and hoster bonus.
1. **Resolution** — of the top `max_probe_count` streams, hosters are resolved in parallel (bounded by `probe_concurrency`) via `HosterResolverRegistry.resolve`, the streams of one hoster in rank order: the next one only after the better one failed, none after one resolved; with `verify_streams` every resolved URL must also pass a playback check. Resolution stops early once `resolve_target_count` genuine video URLs exist, and at the latest at `stream_deadline_seconds` after the request started (but never less than 2 s after the search); unfinished resolutions are cancelled.
1. **Dedup** — this yields the best *working* stream per hoster, so a hoster whose best-ranked link is dead still contributes its next one. Resolving every candidate at once instead opened dozens of connections to distinct CDNs within a second, which the home router blocked like a port scan (the whole machine lost network for ~30–60 s).
1. **Caching + formatting** — every stream gets a `CachedStreamLink` in the stream link cache (saved in parallel; a failed save is logged as `stremio_stream_link_save_failed` and drops only the streams served through the HLS proxy or `/play/`, which need the link; direct video URLs stay); resolved streams are returned with a direct URL or an HLS proxy URL. Streams that are not resolved (failed, only echoed the embed URL, beyond `max_probe_count`, or cancelled by the early stop) are dropped.

---

## Endpoints

All endpoints are prefixed with `/api/v1/stremio/`. All responses carry `Access-Control-Allow-Origin: *`.

### Manifest

```http
GET /api/v1/stremio/manifest.json
```

Returns the Stremio addon manifest with:

- Addon ID: `community.scavengarr`
- Supported types: `movie`, `series`
- Catalogs: `scavengarr-trending-movies`, `scavengarr-trending-series` (both with optional `search` extra)
- ID prefixes: `tt` (IMDb), `tmdb:` (TMDB)
- Resources: `catalog`, `stream`

### Catalog

```http
GET /api/v1/stremio/catalog/{content_type}/{catalog_id}.json
GET /api/v1/stremio/catalog/{content_type}/{catalog_id}/search={query}.json
```

- Trending: TMDB trending movies or series for `content_type` (`catalog_id` is not evaluated).
- Search: TMDB search (German locale), or IMDB Suggest without a TMDB key.
- Response: `{"metas": [StremioMetaPreview, ...]}`; errors and unknown types return an empty list.

### Stream Resolution

```http
GET /api/v1/stremio/stream/{content_type}/{stream_id}.json
```

- Movie: `stream_id` = `tt1234567` or `tmdb:12345`
- Series: `stream_id` = `tt1234567:1:5` or `tmdb:12345:1:5` (season 1, episode 5)
- Response: `{"streams": [StremioStream, ...]}`

Each stream contains:

- `name` — reference title + ` (year)` for movies or ` SxxEyy` for series, followed by the quality label (e.g. `HD 1080P`); falls back to the release name.
- `description` — `plugin | language | HOSTER | size`
- `url` — direct video URL, or `/api/v1/stremio/proxy/{stream_id}/{manifest}` for HLS streams that need headers
- `behaviorHints` — Stremio playback hints (see below)

> **Known issue:** `/play/{stream_id}` URLs are only emitted when no hoster resolver is wired into the use case. The default composition always wires `HosterResolverRegistry.resolve`, so unresolved streams are omitted from the response instead of falling back to `/play/`.

### Stream behaviorHints (proxyHeaders)

Direct (non-proxied) resolved streams include `behaviorHints` with a browser `User-Agent` merged with the resolver's headers:

```json
{
  "name": "Movie Title (2021) HD 1080P",
  "description": "kinoger | German Dub | VOE | 1.4 GB",
  "url": "https://cdn.hoster.com/video.mp4",
  "behaviorHints": {
    "notWebReady": true,
    "proxyHeaders": {
      "request": {
        "User-Agent": "Mozilla/5.0 ...",
        "Referer": "https://hoster.com/e/abc"
      }
    }
  }
}
```

- `notWebReady: true` routes playback through Stremio's local streaming server.
- `proxyHeaders.request` tells Stremio which HTTP headers to send when fetching the video.
- Most hoster CDNs reject requests without a valid `Referer` header.

**Platform support:**

| Platform | Status |
|---|---|
| Desktop (Electron) | Full support |
| Android | Partial (some Referer bugs with specific hosters) |
| iOS | Partial (KSPlayer engine only) |
| Web | Not supported (CORS restrictions) |

### HLS Proxy

```http
GET /api/v1/stremio/proxy/{stream_id}/{path:path}
```

Server-side proxy for HLS streams whose CDN requires headers (e.g. `Referer`) on **all** sub-requests — the master manifest, variant playlists, and segments. Stremio's `proxyHeaders` only applies to the initial manifest fetch, so sub-requests would otherwise get `403` from CDNs such as Dropload's `dropcdn.io`.

**When is it used?** For every resolved stream with `is_hls` and non-empty `headers`. This covers all XFS video hosters (they always set `Referer`), StreamUp, Vidsonic, and any other resolver that returns HLS with headers. Such streams get `behaviorHints: {"notWebReady": true}` only. MP4 streams and HLS streams without headers use the direct URL with `proxyHeaders`.

**How it works:**

1. Look up the `CachedStreamLink` (`video_url`, `video_headers`, `is_hls`); `404` if missing, `400` if it is not an HLS proxy stream.
1. Build the CDN URL from the CDN base of `video_url` + `path`, using the request query string or, if empty, the original `video_url` query (auth tokens). A `path` that would leave the stream's CDN (absolute URL, `//host`, other scheme) is answered with `400`: the path comes from the client, and without this check the endpoint was an open proxy into the server's network (SSRF).
1. Paths not ending in `.m3u8` are streamed from the CDN as segments (`StreamingResponse`); a failed segment request closes its connection before the error is returned, so CDN errors cannot drain the shared HTTP connection pool.
1. `.m3u8` manifests are fetched with the stored headers (cached for 60 s, at most 512 manifests: when full, expired ones go first, then the oldest), and URI lines starting with the CDN base are rewritten to proxy URLs; relative URIs are left as-is because they resolve against the proxy URL.
1. CDN fetches share a global semaphore (50); CDN errors return `502`.

### Play (Proxy Fallback)

```http
GET /api/v1/stremio/play/{stream_id}
```

Fallback endpoint for cached stream links (see the known issue above for when its URLs are emitted):

1. Look up `stream_id` in the stream link cache (`404` if expired).
1. Resolve via `HosterResolverRegistry` using the cached hoster URL and hoster hint.
1. Return a **302 redirect** to the resolved `.mp4`/`.m3u8` URL.
1. Return **502** if resolution fails or the resolver only echoed the embed page (never redirects to embed pages).

### Health

```http
GET /api/v1/stremio/health
```

Reports component status (`tmdb_configured`, `stream_plugin_count`, `stream_plugins`, use case/resolver/link-cache flags), `supported_hosters` (resolver names from `HosterResolverRegistry.supported_hosters`), and a metrics snapshot. Returns `200` when healthy and `503` otherwise; healthy requires a title client (`tmdb_configured` is also `true` for the IMDB fallback client), both use cases, the resolver registry, the stream link cache, and at least one `stream` plugin.

---

## Title Matching

Title matching prevents false positives when plugin results include sequels, spin-offs, or unrelated titles.

| Feature | Details |
|---|---|
| Scoring | `max(token_sort_ratio, token_set_ratio) / 100` via `rapidfuzz` on normalised strings (lowercase, Unicode → ASCII, punctuation stripped) |
| Year bonus | `+title_year_bonus` (0.2) if the result year is within tolerance |
| Year penalty | `-title_year_penalty` (0.3) if a year is present but outside tolerance |
| Sequel penalty | `-title_sequel_penalty` (0.35) if the trailing sequel numbers differ (either side) |
| Threshold | `title_match_threshold` (0.7) minimum score |
| Year tolerance | Movies ±1 year, series ±3 years |
| Title candidates | Up to 4 deduplicated candidates: raw title, guessit title of `title`, guessit title of `release_name`, raw `release_name` |
| Reference titles | Primary (localised) title plus `alt_titles` (TMDB original title when it differs) |

---

## Stream Ranking

Streams are ranked with a weighted score:

```text
rank_score = language_score + (quality.value * quality_multiplier) + hoster_bonus
```

Only one stream per hoster is returned (e.g. 5 VOE links from 5 plugins collapse to one); streams without a hoster name are always kept. With a resolver configured (the normal case) the resolution does it: a hoster's streams are tried in rank order until one resolves and passes the playback check. Without a resolver, `deduplicate_by_hoster()` keeps the best-ranked stream per hoster before formatting.

### Default Weights

| Language | Score |
|---|---|
| German Dub (`de`) | 1000 |
| German Sub (`de-sub`) | 500 |
| English Sub (`en-sub`) | 200 |
| English Dub (`en`) | 150 |
| Unknown (`default_language_score`) | 100 |

| Quality | Value |
|---|---|
| `UHD_4K` | 60 |
| `HD_1080P` | 50 |
| `HD_720P` | 40 |
| `SD` | 30 |
| `TS` | 20 |
| `CAM` | 10 |
| `UNKNOWN` | 0 |

| Hoster | Bonus |
|---|---|
| `supervideo` | 5 |
| `voe` | 4 |
| `filemoon` | 3 |
| `streamtape` | 2 |
| `doodstream` | 1 |

---

## TMDB Integration

The addon uses TMDB for title resolution and catalog browsing.

| Feature | Details |
|---|---|
| Title resolution | `find_by_imdb_id()` / `get_title_and_year()` via `/find/{imdb_id}` |
| TMDB IDs | `get_title_by_tmdb_id()` for `tmdb:` IDs from the own catalog |
| Locale | `de-DE` by default; `/find` lookups use each plugin language (`{lang}-{LANG}`) |
| Alt titles | Original title (`original_title`/`original_name`) when it differs from the localised title |
| Trending | `/trending/movie/week` and `/trending/tv/week` |
| Search | `/search/movie` and `/search/tv` |
| Posters | `https://image.tmdb.org/t/p/w500{poster_path}` |
| Caching | Find: 24 h, trending: 6 h, search: 1 h |

### IMDB Fallback (No API Key)

Without a TMDB API key, `ImdbFallbackClient` is used:

| Source | Purpose |
|---|---|
| IMDB Suggest API | Title resolution and catalog search |
| Wikidata API | Localised title lookup via the IMDb property (`P345`) |

Limitations: no trending catalogs and no resolution of `tmdb:` IDs.

---

## Configuration

Stremio settings live in `StremioConfig` (YAML section `stremio:`). See [Configuration](./configuration.md#stremio) for the full reference.

### Stream Ranking

| Setting | Default | Description |
|---|---|---|
| `language_scores` | `de`=1000, `de-sub`=500, `en-sub`=200, `en`=150 | Language weight map |
| `default_language_score` | 100 | Score for unknown languages |
| `quality_multiplier` | 10 | Multiplier for the quality value |
| `hoster_scores` | `supervideo`=5, `voe`=4, `filemoon`=3, `streamtape`=2, `doodstream`=1 | Hoster reliability bonus |
| `preferred_language` | `de` | Currently unused (no effect) |

### Plugin Search

| Setting | Default | Description |
|---|---|---|
| `max_concurrent_plugins` | 10 | httpx slots of the global concurrency pool |
| `max_concurrent_playwright` | 5 | Playwright slots of the global concurrency pool |
| `max_results_per_plugin` | 100 | Per-plugin result limit in Stremio searches |
| `plugin_timeout_seconds` | 10 | Plugin search budget, counted from the request start (queueing for a slot included) |
| `stream_deadline_seconds` | 15 | Overall budget per stream request; resolution stops here (at least 2 s after the search) |
| `max_concurrent_plugins_auto` | `true` | Auto-tune `max_concurrent_plugins` (superseded by `auto_tune_all`) |
| `auto_tune_all` | `true` | Auto-tune all concurrency parameters from container resources |

### Title Matching

| Setting | Default | Description |
|---|---|---|
| `title_match_threshold` | 0.7 | Minimum score to keep a result |
| `title_year_bonus` | 0.2 | Added when the year matches |
| `title_year_penalty` | 0.3 | Subtracted when the year does not match |
| `title_sequel_penalty` | 0.35 | Subtracted when sequel numbers differ |
| `title_year_tolerance_movie` | 1 | Allowed year difference for movies |
| `title_year_tolerance_series` | 3 | Allowed year difference for series |

### Resolution, Probing & Caching

| Setting | Default | Description |
|---|---|---|
| `max_probe_count` | 50 | Top-ranked streams to probe/resolve; streams beyond are dropped |
| `probe_concurrency` | 10 | Parallel probes and parallel resolutions |
| `resolve_target_count` | 15 | Stop resolving after this many genuine video URLs (`0` = resolve all) |
| `verify_streams` | `true` | Playback check of every resolved URL: first bytes with the playback headers; error status, HTML or a non-playlist HLS answer drops the stream (result cached like a failed resolution) |
| `verify_streams` | `true` | Playback check of every resolved URL: first bytes with the playback headers; error status, HTML or a non-playlist HLS answer drops the stream (result cached like a failed resolution) |
| `stream_link_ttl_seconds` | 7200 | TTL of cached stream links (`streamlink:{stream_id}`) |
| `probe_at_stream_time` | `true` | Dead-link probe before caching (see known issue) |
| `probe_timeout_seconds` | 10 | Per-URL httpx probe timeout |
| `probe_stealth_concurrency` | 5 | Parallel stealth-browser (Patchright) probes |
| `probe_stealth_timeout_seconds` | 15 | Stealth probe timeout; also the `StealthPool` timeout used by SuperVideo |
| `probe_stealth_enabled` | `true` | Currently unused (no effect) — the stealth phase always receives the `StealthPool` |

> **Known issue:** the dead-link probe (`probe_urls_stealth()`) only runs when no resolve callback is configured. The default composition always wires `HosterResolverRegistry.resolve`, so `probe_at_stream_time` currently has no effect; resolution acts as the liveness check.

Scored plugin selection keys (`scoring_enabled`, `max_plugins_scored`, `exploration_probability`, …) are documented in [Plugin Scoring & Probing](./plugin-scoring-and-probing.md#configuration).

### Circuit Breaker

`PluginCircuitBreaker` is created in the composition root with hardcoded values (`failure_threshold=5`, `cooldown_seconds=60.0`, `max_cooldown_seconds=3600.0`); they are not configurable. After 5 consecutive failures (exceptions or timeouts) a plugin is skipped for 60 s. After the cooldown a single probe request is allowed (half-open): concurrent requests skip the plugin until the probe reports, and a probe that never reports (cancelled, or a timeout that is not counted) is replaced by the next request after one more cooldown. Success resets the breaker and its cooldown, failure reopens it with twice the previous cooldown (60 s → 2 min → 4 min … capped at 1 h). Stremio requests are usually minutes apart, so a fixed 60 s cooldown would let an unreachable plugin cost almost every request its full timeout. A timeout counts as a failure when the plugin had at least half of `plugin_timeout_seconds`; a plugin cut by the search deadline after queueing for most of the budget is not blamed.

### Behind AIOStreams

AIOStreams can include Scavengarr as a `custom` addon (`manifestUrl` = `<base>/api/v1/stremio/manifest.json`). Its per-addon `timeout` (ms, default 7000) must exceed `stream_deadline_seconds`, otherwise every Scavengarr answer is cut: use `stream_deadline_seconds` + 2 s (17000 with the defaults). AIOStreams checks the manifest when a user config is saved, so Scavengarr must be reachable from the AIOStreams host at that moment. Recommended user settings and measurements: [`docs/plans/stremio-latency.md`](../plans/stremio-latency.md#aiostreams).

### Global Concurrency Pool

`ConcurrencyPool` holds separate httpx and Playwright slot pools with fair-share distribution across concurrent requests:

- `httpx_slots` = `max_concurrent_plugins`
- `pw_slots` = `max_concurrent_playwright`
- Fair share per request: `max(1, total_slots // active_requests)`
- A task reserves its share under the pool's condition before it waits for a global slot (a cancelled waiter gives the reservation back), so tasks queued behind a full pool cannot push their request above its share

When `auto_tune_all` is enabled (default), slot counts are derived from container resources at startup. See [Configuration → Auto-Tune](./configuration.md#auto-tune-container-aware) for formulas and caps.

### Multi-Language Search

Plugins declare `languages: list[str]` (default `["de"]`). The use case groups plugins by identical language lists, fetches TMDB titles for each unique language in parallel, and searches each group with language-specific queries. A plugin with `languages=["de", "en"]` is searched with both German and English title queries.

---

## Testing

| Test file | Coverage |
|---|---|
| `tests/unit/application/test_stremio_stream.py` | Stream use case (search, filter, resolve, rank; `TestResolvePhase`: per-hoster resolution, answer deadline) |
| `tests/unit/infrastructure/test_check_playable.py` | Playback check of resolved URLs (`verify_streams`) |
| `tests/unit/application/test_stremio_catalog.py` | Catalog use case |
| `tests/unit/application/test_plugin_search_runner.py` | `PluginSearchRunner` (fan-out, timeout, search deadline, circuit breaker) |
| `tests/unit/application/test_stremio_queries.py` | Search queries and multi-language references |
| `tests/unit/application/test_stremio_stream_builder.py` | Stream formatting, dedup, direct-video detection, proxy URLs |
| `tests/unit/infrastructure/test_stream_converter.py` | `SearchResult` → `RankedStream` conversion |
| `tests/unit/infrastructure/test_stream_sorter.py` | Stream ranking and sorting |
| `tests/unit/infrastructure/test_title_matcher.py` | Title-match scoring and filtering |
| `tests/unit/infrastructure/test_episode_filter.py` | Season/episode filtering |
| `tests/unit/infrastructure/test_release_parser.py` | Quality/language parsing from release names |
| `tests/unit/infrastructure/test_tmdb_client.py` | TMDB client with caching |
| `tests/unit/infrastructure/test_imdb_fallback.py` | IMDB Suggest + Wikidata fallback |
| `tests/unit/infrastructure/test_stream_link_cache.py` | Stream link cache repository (incl. HLS proxy fields) |
| `tests/unit/infrastructure/test_hls_proxy.py` | HLS manifest rewriting, CDN fetch, query resolution |
| `tests/unit/infrastructure/test_circuit_breaker.py` | `PluginCircuitBreaker` |
| `tests/unit/infrastructure/test_concurrency.py` | `ConcurrencyPool` + budgets |
| `tests/unit/interfaces/test_stremio_router.py` | Router endpoints |
| `tests/e2e/test_stremio_endpoint.py` | Full HTTP flow (manifest, catalog, stream, play, HLS proxy, health) |
| `tests/e2e/test_stremio_series_e2e.py` | Series season/episode filtering |
| `tests/e2e/test_stremio_streamable_e2e.py` | Streamable link verification |

---

## Source Code References

| Component | Path |
|---|---|
| Domain entities | `src/scavengarr/domain/entities/stremio.py` |
| TMDB port | `src/scavengarr/domain/ports/tmdb.py` |
| Stream link port | `src/scavengarr/domain/ports/stream_link_repository.py` |
| Concurrency port | `src/scavengarr/domain/ports/concurrency.py` |
| Stream use case | `src/scavengarr/application/use_cases/stremio_stream.py` |
| Catalog use case | `src/scavengarr/application/use_cases/stremio_catalog.py` |
| Plugin search runner | `src/scavengarr/application/stremio/plugin_search.py` |
| Query building | `src/scavengarr/application/stremio/queries.py` |
| Stream building | `src/scavengarr/application/stremio/stream_builder.py` |
| Stream converter | `src/scavengarr/infrastructure/stremio/stream_converter.py` |
| Stream sorter | `src/scavengarr/infrastructure/stremio/stream_sorter.py` |
| Title matcher | `src/scavengarr/infrastructure/stremio/title_matcher.py` |
| Episode filter | `src/scavengarr/infrastructure/stremio/episode_filter.py` |
| Release parser | `src/scavengarr/infrastructure/stremio/release_parser.py` |
| HLS proxy helpers | `src/scavengarr/infrastructure/stremio/hls_proxy.py` |
| Stream link cache | `src/scavengarr/infrastructure/persistence/stream_link_cache.py` |
| Circuit breaker | `src/scavengarr/infrastructure/circuit_breaker.py` |
| Concurrency pool | `src/scavengarr/infrastructure/concurrency.py` |
| TMDB client | `src/scavengarr/infrastructure/tmdb/client.py` |
| IMDB fallback | `src/scavengarr/infrastructure/tmdb/imdb_fallback.py` |
| Stremio config | `src/scavengarr/infrastructure/config/schema.py` (`StremioConfig`) |
| Composition wiring | `src/scavengarr/interfaces/composition.py` |
| Stremio router | `src/scavengarr/interfaces/api/stremio/router.py` |
