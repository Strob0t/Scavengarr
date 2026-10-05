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
  ├── GET|HEAD /proxy/{stream_id}/{path:path} → HLS proxy (manifests + segments)
  └── GET /health                            → component status
```

### Request Flow (Stream Resolution)

```text
IMDb/TMDB ID → title lookup per plugin language → search cache, or plugin search
  → episode filter → link validation → title matching (cached per title)
  → quality/language parsing → ranking
  → hoster resolution, one stream per hoster and language + playback check (deadline, early stop)
  → link cache → StremioStream list
```

1. **Plugin selection** — all plugins with `provides` = `stream` or `both`, or the scored top-N when scored selection is active (see [Plugin Scoring & Probing](./plugin-scoring-and-probing.md)).
1. **Title resolution** — per plugin language, look up title + year via TMDB `/find` (or the IMDB Suggest/Wikidata fallback); `tmdb:` IDs are resolved via the TMDB ID.
1. **Search cache** — the title-matching search results of a request are cached per title, season and episode (`stremio:search:{imdb_id}:{season}:{episode}`, `CachePort`: diskcache or Redis) for `cache.search_ttl_seconds`; resolution and ranking run on every request, since hoster stream URLs expire and some are bound to the resolving IP. A fresh entry skips the plugin search. An older one still answers for 6 h while one background search refreshes it (stale-while-revalidate). Requests for the same title share one running search (single-flight), and a search goes on when the request that started it goes away. Entries without results are not stored. `cache.search_ttl_seconds: 0` turns the cache off, and with it the early answer and late plugins below.
1. **Plugin search** — `PluginSearchRunner` searches each language group with the full title and, if the title contains `:`, the base title before the colon; bounded by the global `ConcurrencyPool`, with circuit breaker. The search ends `plugin_timeout_seconds` after the request started: plugins waiting for a concurrency slot use up that budget too, running ones are cut at the deadline, queued ones are skipped. Results of fallback queries are deduplicated by `download_link`. With the search cache on, the search answers at `search_soft_deadline_seconds` (7 s) when it has title-matching results, else it waits for the cut plugins until `plugin_timeout_seconds`. Plugins cut by that deadline are not cancelled but run on as late plugins for up to `plugin_timeout_seconds` more (`stremio_plugin_timeout` with `runs_on=true`); their title-matching results are added to the cache entry (`stremio_late_plugins_done`), so the next request has them and slow sites are not asked twice. Late plugins hold no concurrency slot: they would block the next requests' plugins; their number is bounded by the plugins of one request, their time by the timeout. App shutdown cancels them.
1. **Episode filtering** — for series requests, results are filtered by season/episode (guessit on release names; a title without an episode, such as a show page, a season page ("Staffel 1") or a season pack (`S01`), falls back to episode labels such as `1x5` or `S01E05` in `download_links`). Multi-episode and multi-season releases (`S01E01-E03`, `S01-S03`) are kept when they contain the requested episode/season.
1. **Link validation** — Python plugin results are validated by the search engine.
1. **Title matching** — false positives (sequels, spin-offs) are filtered via fuzzy scoring.
1. **Stream conversion** — `SearchResult` objects become `RankedStream` objects with parsed quality/language.
1. **Ranking** — sort by language, quality, and hoster bonus.
1. **Resolution** — of the top `max_probe_count` streams, hosters are resolved in parallel (bounded by `probe_concurrency`) via `HosterResolverRegistry.resolve`, the streams of one hoster and language in rank order: the next one only after the better one failed, none after one resolved; with `verify_streams` every resolved URL must also pass a playback check. Resolution stops early once `resolve_target_count` genuine video URLs exist, `resolve_grace_seconds` after the first one (browser-resolved hosters such as DoodStream mirrors or Dropload's captcha take 3–7 s and held answers that were complete but for them until the deadline), and at the latest at `stream_deadline_seconds` after the request started (but never less than 2 s after the search); unfinished resolutions are cancelled.
1. **Dedup** — this yields the best *working* stream per hoster and language (`hoster_key()`: dub and sub on one hoster are different content, e.g. anime), so a hoster whose best-ranked link is dead still contributes its next one. Resolving every candidate at once instead opened dozens of connections to distinct CDNs within a second, which the home router blocked like a port scan (the whole machine lost network for ~30–60 s).
1. **Caching + formatting** — only streams served through the HLS proxy or `/play/` get a `CachedStreamLink` in the stream link cache, since only those endpoints look it up (saved in parallel; a failed save is logged as `stremio_stream_link_save_failed` and drops those streams; direct video URLs need no link). Saving a link for every ranked stream cost seconds per answer. Resolved streams are returned with a direct URL or an HLS proxy URL. Streams that are not resolved (failed, only echoed the embed URL, beyond `max_probe_count`, or cancelled by the early stop) are dropped.

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
- Catalogs: `scavengarr-trending-movies`, `scavengarr-trending-series` (trending rows with optional `search` extra; without a TMDB key they are search-only, `"isRequired": true`, named "Scavengarr Movies"/"Scavengarr Series", since the IMDB fallback has no trending lists and Stremio showed empty rows on its board)
- ID prefixes: `tt` (IMDb), `tmdb:` (TMDB)
- Resources: `catalog`, `stream` (only `stream` without a catalog use case)

### Catalog

```http
GET /api/v1/stremio/catalog/{content_type}/{catalog_id}.json
GET /api/v1/stremio/catalog/{content_type}/{catalog_id}/search={query}.json
```

- Trending: TMDB trending movies or series for `content_type` (`catalog_id` is not evaluated).
- Search: TMDB search (German locale), or IMDB Suggest without a TMDB key.
- Response: `{"metas": [StremioMetaPreview, ...]}`; errors and unknown types return an empty list.
- Items carry IMDb ids (`tt…`): Stremio opens a catalog item through a meta addon for its id prefix, Cinemeta knows IMDb ids, and with a `tmdb:` id Stremio showed "No addons were requested for this meta!". TMDB lists carry no IMDb ids, so each title's comes from `/{movie|tv}/{id}/external_ids` (cached 30 days); titles without one are left out.

### Stream Resolution

```http
GET /api/v1/stremio/stream/{content_type}/{stream_id}.json
```

- Movie: `stream_id` = `tt1234567` or `tmdb:12345`
- Series: `stream_id` = `tt1234567:1:5` or `tmdb:12345:1:5` (season 1, episode 5)
- Response: `{"streams": [StremioStream, ...]}`

Each stream contains:

- `name` — `Scavengarr` and the quality on a second line (`4K`, `1080p`, `720p`, `SD`, `TS`, `CAM`; no line for unknown quality). Stremio shows `name` in a narrow column, like other addons' `Torrentio\n1080p`.
- `description` — one short line per fact, since Stremio cuts long lines: the site's own title (release name, else the site's title, else the reference title with the year; series titles get ` SxxEyy` unless they are release names), then `language · size`, then `HOSTER · plugin`. The site's title shows a wrong match that the reference title would hide.
- `url` — direct video URL, or `/api/v1/stremio/proxy/{stream_id}/{manifest}` for HLS streams that need headers
- `behaviorHints` — `bingeGroup` and `filename` on every stream, plus the playback hints (see below)

### Autoplay of the next episode (bingeGroup)

Stremio's binge watching (Settings → Player → auto-play next episode) requests the next episode's streams when an episode starts and later plays the **first** of them whose `behaviorHints.bingeGroup` equals the current stream's (exact string match; no group, no autoplay). Scavengarr sets `bingeGroup` to `scavengarr|<language code>` (`scavengarr|de`, `scavengarr|de-sub`, `scavengarr|unknown`): a German dub continues in German, with the best-ranked stream the next episode has, whatever its hoster or quality. Groups with hoster or quality would often find no match in the next episode, and autoplay would stop.

`behaviorHints.filename` carries the release name when the site has one; Stremio passes it to subtitle addons (OpenSubtitles matches releases by name).

> **Known issue:** `/play/{stream_id}` URLs are only emitted when no hoster resolver is wired into the use case. The default composition always wires `HosterResolverRegistry.resolve`, so unresolved streams are omitted from the response instead of falling back to `/play/`.

### Stream behaviorHints (proxyHeaders)

Direct (non-proxied) resolved streams include `behaviorHints` with a browser `User-Agent` merged with the resolver's headers:

```json
{
  "name": "Scavengarr\n1080p",
  "description": "Movie.Title.2021.German.DL.1080p.WEB.x264\nGerman Dub · 1.4 GB\nVOE · kinoger",
  "url": "https://cdn.hoster.com/video.mp4",
  "behaviorHints": {
    "bingeGroup": "scavengarr|de",
    "filename": "Movie.Title.2021.German.DL.1080p.WEB.x264",
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
| Web | With a streaming server: plays through the server's `/proxy` (checked 2026-10-03 with DoodStream, Vinovo, FireStream); without one not supported (CORS) |

> **Known issue (IP-bound streams):** DoodStream and Vinovo bind a stream URL to the IP that resolved it; another IP gets `200 error_wrong_ip` (DoodStream's CDN) or 403 (Vinovo). A direct stream therefore plays only when the player (Stremio's streaming server) reaches the internet through the same IP as Scavengarr: with Scavengarr behind a VPN and the player at home it fails. Streams through the HLS proxy are fetched by Scavengarr and are not affected. `scripts/stremio_playcheck.py` shows it from the player's machine.

> **Self-hosted streaming server (e.g. tsaridas/stremio-docker behind a reverse proxy):**
> - **Reachability.** Stremio Web hands every stream to the server's `/hlsv2/probe` before it plays: a `proxyHeaders` stream as the server's own public URL (`https://stremio.example/proxy/…`), an HLS-proxy stream as Scavengarr's URL. The server must resolve and reach both names. In a VPN container's network (gluetun) its DNS knows no LAN names and its firewall blocks the LAN, so every probe answers 500 (map the names with `extra_hosts` on the VPN container and allow the target with `FIREWALL_OUTBOUND_SUBNETS`). Streams the browser can play directly still play then, through a `HEAD` check of their content type; transcoding (MKV, HEVC, AC3) does not.
> - **Disguised segments.** tsaridas/stremio-docker's nginx nests `location ~* \.(jpg|jpeg|png|gif|ico|css|js|woff2|env)$` inside `location /`, and nginx matches it before its server routes. HLS segments disguised with such an extension (Playmate's `…_000.css`) are looked up as web player files and answer 404: error 81, "Error occurred when downloading".

> **Behind a VPN container (gluetun):** gluetun's DNS refuses answers whose address is on its malicious lists (`BLOCK_MALICIOUS`, on by default), and its IP list holds 168.80.0.0/15, mixdrop's CDN (`*.mxcontent.net`; checked 2026-10-04: REFUSED by gluetun, answered by 1.1.1.1 through the same tunnel). Scavengarr then resolves mixdrop links but no player can fetch them (`hoster_resolve_unplayable`; `cannot resolve …mxcontent.net` in the log). `DNS_UNBLOCK_HOSTNAMES` does not help: gluetun applies it to its hostname list only, not to blocked addresses. Turn `BLOCK_MALICIOUS` off: the lookups stay in the tunnel (gluetun's own DNS server, DNS over TLS), unfiltered. A filtering resolver of your own as gluetun's upstream (`DNS_UPSTREAM_RESOLVER_TYPE=plain`, `DNS_UPSTREAM_PLAIN_ADDRESSES=<ip>:53`) sends every lookup of the VPN stack outside the tunnel; with a LAN Pi-hole as upstream the production VPN container was unhealthy (2026-10-05; the cause is not confirmed), and an unhealthy VPN container takes down every container that shares its network.

### HLS Proxy

```http
GET  /api/v1/stremio/proxy/{stream_id}/{path:path}
HEAD /api/v1/stremio/proxy/{stream_id}/{path:path}
```

Server-side proxy for HLS streams whose CDN requires headers (e.g. `Referer`) on **all** sub-requests — the master manifest, variant playlists, and segments. Stremio's `proxyHeaders` only applies to the initial manifest fetch, so sub-requests would otherwise get `403` from CDNs such as Dropload's `dropcdn.io`.

**When is it used?** For every resolved stream with `is_hls` and non-empty `headers`. This covers all XFS video hosters (they always set `Referer`), StreamUp, Vidsonic, and any other resolver that returns HLS with headers. Such streams get `behaviorHints: {"notWebReady": true}` only. MP4 streams and HLS streams without headers use the direct URL with `proxyHeaders`.

**How it works:**

1. Look up the `CachedStreamLink` (`video_url`, `video_headers`, `is_hls`); `404` if missing, `400` if it is not an HLS proxy stream.
1. Build the CDN URL from the CDN base of `video_url` + `path`, using the request query string or, if empty, the original `video_url` query (auth tokens). A `path` that would leave the stream's CDN (absolute URL, `//host`, other scheme) is answered with `400`: the path comes from the client, and without this check the endpoint was an open proxy into the server's network (SSRF).
1. Paths not ending in `.m3u8` are streamed from the CDN as segments (`StreamingResponse`); a failed segment request closes its connection before the error is returned, so CDN errors cannot drain the shared HTTP connection pool. The CDN's chunks pass through as they arrive; only an encoded body (`Content-Encoding`, which the proxy does not forward) is decoded. TLS runs in the event loop (the shared client's `AsyncioNetworkBackend`): on the Raspberry Pi 4 a relayed MB costs 75–88 ms of CPU, against 103–112 ms with httpx's default anyio backend.
1. `.m3u8` manifests are fetched with the stored headers (cached for 60 s, at most 512 manifests: when full, expired ones go first, then the oldest), and the CDN's URIs are rewritten to proxy URLs, in URI lines and in the `URI="…"` attributes of tags (audio renditions, keys, init segments). Relative URIs are left as-is because they resolve against the proxy URL. A URI from the CDN's root (`/secure/98/<id>/video.m3u8`, Vidsonic's variants), a protocol-relative one or an absolute URL of the CDN outside the stream's directory becomes `<proxy>/<stream-id>//<path>`; the proxy joins that absolute path with the CDN origin. URIs of other origins stay direct, since the proxy fetches from the stream's own CDN only.
1. CDN fetches share a global semaphore (50); CDN errors return `502`.
1. `HEAD` answers like `GET`; the server drops the body. When its streaming server cannot probe a stream, Stremio Web reads the content type with `HEAD` (stremio-video's `getContentType`) before it plays. A `405` without CORS headers ended these streams in error 83, "Video is not supported".

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
| Extra-words penalty | `-title_extra_words_penalty` (0.35) on the `token_set_ratio` when the result adds words to the reference: the set ratio rates "Dark Matter", "Dark Gathering" or "Naruto Shippuden" 1.0 against "Dark" or "Naruto". A result that only drops words ("Dune" for "Dune: Part One") is not penalised; one that adds words passes with a matching year (0.85) and fails without one (0.65) or with a wrong one (0.35). Release tags are no extra words: the guessit candidate carries the clean title |
| Year bonus | `+title_year_bonus` (0.2) if the result year is within tolerance |
| Year penalty | `-title_year_penalty` (0.3) if a year is present but outside tolerance |
| Sequel penalty | `-title_sequel_penalty` (0.35) if the trailing sequel numbers differ (either side) |
| Threshold | `title_match_threshold` (0.7) minimum score |
| Year tolerance | Movies ±1 year, series ±3 years |
| Title candidates | Up to 4 deduplicated candidates: raw title, guessit title of `title`, guessit title of `release_name`, raw `release_name` |
| Reference titles | Primary (localised) title plus `alt_titles` (TMDB original title when it differs). Original titles are no search queries: a plugin is searched with the titles of its languages only (recall check in `docs/plans/pi-performance.md`) |

---

## Stream Ranking

Streams are ranked with a weighted score:

```text
rank_score = language_score + (quality.value * quality_multiplier) + hoster_bonus
```

Only one stream per hoster and language is returned (e.g. 5 German-dub VOE links from 5 plugins collapse to one; a German-sub VOE link stays); streams without a hoster name are always kept. Hoster names are resolver names (`HosterResolverRegistry.canonical_hoster`, wired into the stream converter): a plugin label that names a known hoster wins (redirect links such as `s.to/r?t=…` name the hoster only in the label), then the URL's domain if a resolver handles it, so mirror domains and aliases share one name (`dood.re`, `d0000d.com` → `doodstream`) for the dedup, the hoster bonus and the label. Otherwise the plugin label is used, with a domain label reduced to its second-level part (`voe.sx` → `voe`), then the URL's second-level domain; placeholder labels such as `unknown` fall through to the URL. With a resolver configured (the normal case) the resolution does it: a hoster's streams of one language are tried in rank order until one resolves and passes the playback check. Without a resolver, `deduplicate_by_hoster()` keeps the best-ranked stream per hoster and language before formatting.

A stream's language comes from its release name (guessit), else from the plugin's language label for the link (`parse_language`; English and German words: "German Sub", "Ger-Sub", "Englisch", "Japanisch mit deutschen Untertiteln" → `de-sub`), else from the plugin's default language. An unrecognized label falls back to the default, so a label missing from the patterns lists English audio as German Dub (s.to's "Englisch" did until 2026-10-01).

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
| TMDB IDs | `get_title_by_tmdb_id()` for `tmdb:` IDs (catalog items of versions before 2026-10, e.g. in a Stremio library) |
| Locale | `de-DE` by default; `/find` lookups use each plugin language (`{lang}-{LANG}`) |
| Alt titles | Original title (`original_title`/`original_name`) when it differs from the localised title |
| Trending | `/trending/movie/week` and `/trending/tv/week` |
| IMDb ids of catalog items | `/movie/{id}/external_ids`, `/tv/{id}/external_ids` |
| Search | `/search/movie` and `/search/tv` |
| Posters | `https://image.tmdb.org/t/p/w500{poster_path}` |
| Caching | Find: 24 h, trending: 6 h, search: 1 h, IMDb ids: 30 days |

### IMDB Fallback (No API Key)

Without a TMDB API key, `ImdbFallbackClient` is used:

| Source | Purpose |
|---|---|
| IMDB Suggest API | Title resolution and catalog search |
| Wikidata API | Localised title lookup via the IMDb property (`P345`) |

Limitations: no trending catalogs (the manifest declares the catalogs search-only) and no resolution of `tmdb:` IDs.

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
| `search_soft_deadline_seconds` | 7 | With the search cache on: answer this long after the request start when there are results; late plugins fill the cache |
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
| `title_extra_words_penalty` | 0.35 | Subtracted from the subset score when the result adds words |
| `title_year_tolerance_movie` | 1 | Allowed year difference for movies |
| `title_year_tolerance_series` | 3 | Allowed year difference for series |

### Resolution, Probing & Caching

| Setting | Default | Description |
|---|---|---|
| `max_probe_count` | 50 | Top-ranked streams to resolve; streams beyond are dropped |
| `probe_concurrency` | 10 | Parallel resolutions |
| `resolve_target_count` | 15 | Stop resolving after this many genuine video URLs (`0` = resolve all) |
| `resolve_grace_seconds` | 4.0 | Once the first genuine video URL is there, unfinished resolutions get at most this long (`0` = wait until `stream_deadline_seconds`) |
| `verify_streams` | `true` | Playback check of every resolved URL: first bytes with the playback headers; error status, HTML or a non-playlist HLS answer drops the stream (result cached like a failed resolution) |
| `stream_link_ttl_seconds` | 7200 | TTL of cached stream links (`streamlink:{stream_id}`) |
| `probe_stealth_timeout_seconds` | 15 | Page timeout of the `StealthPool` (browser-based resolvers, Cloudflare fallback) |

Resolution is the liveness check: a stream is only returned when its hoster link resolves (and passes the playback check).

Scored plugin selection keys (`scoring_enabled`, `max_plugins_scored`, `exploration_probability`, …) are documented in [Plugin Scoring & Probing](./plugin-scoring-and-probing.md#configuration).

### Circuit Breaker

`PluginCircuitBreaker` is created in the composition root with hardcoded values (`failure_threshold=5`, `cooldown_seconds=60.0`, `max_cooldown_seconds=3600.0`); they are not configurable. The breaker tracks each plugin per requested category (`kinoking:2000` for its movies, `kinoking:5000` for its series): a site can be too slow for one content type only (kinoking's movie pages take 12–17 s to answer, its series pages 1 s), and its movies must not cost every movie request the search budget while its series keep coming. After 5 consecutive failures (exceptions or timeouts) a plugin is skipped for that category for 60 s (`stremio_plugin_circuit_open`). An answer without results neither counts nor resets the breaker: kinoking answers a search without hits at once, and those answers kept closing the breaker between its timeouts. After the cooldown a single probe request is allowed (half-open): concurrent requests skip the plugin until the probe reports, and a probe that never reports (cancelled, or a timeout that is not counted) is replaced by the next request after one more cooldown. Success resets the breaker and its cooldown, failure reopens it with twice the previous cooldown (60 s → 2 min → 4 min … capped at 1 h). Stremio requests are usually minutes apart, so a fixed 60 s cooldown would let an unreachable plugin cost almost every request its full timeout. A timeout counts as a failure when the plugin had at least half of `plugin_timeout_seconds`; a plugin cut by the search deadline after queueing for most of the budget is not blamed. A late plugin (search cache on) is not blamed for the cut: it counts as a failure only when it is still running at the end of its extra time (`_late_search_done`), and when it answers, the answer reports like any other (results reset the breaker). Counting the 7 s cut opened the breaker for plugins that answered a few seconds later without hits (kinox for movies in the 2026-10-04 end-to-end test).

Hoster resolution has a breaker of its own, per resolver: a hoster whose resolutions keep timing out or giving unplayable streams is skipped the same way, except that its half-open probe runs to its end even when the request is cut ([Registry Features](hoster-resolvers.md#registry-features)).

### Mirror Groups

Some sites front one database: hdfilme, streamcloud and streamkiste list the same titles under the same news ids (Oppenheimer is 23684 on all three), in the same order, with the same texts and the same devideosrc player, only in three themes. A plugin declares such a group with `mirror_group` (the three set `"hdfilme"`); the composition root collects them (`_mirror_groups`) for `PluginSearchRunner`. A Stremio request asks one member per group: the first in the request's plugin order whose breaker for the category is closed (`PluginCircuitBreaker.is_closed`, which unlike `allow` starts no probe). The others are skipped (`stremio_mirrors_skipped`). When that member's breaker opens, the next one takes over; when no member's breaker is closed, every member goes to its breaker, so a half-open probe can bring one back. Torznab searches are not affected: each plugin stays its own indexer.

A member that answers without results counts as healthy (empty answers do not trip the breaker), so a member whose parser broke after a theme change keeps being asked while the others would still deliver. The parser tests on real pages (`tests/unit/infrastructure/test_real_pages.py`) and the live smoke tests watch for that.

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
| `tests/unit/application/test_plugin_search_runner.py` | `PluginSearchRunner` (fan-out, timeout, search deadline, circuit breaker, mirror groups) |
| `tests/unit/interfaces/test_mirror_groups.py` | The mirror groups the plugins declare |
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
| `tests/unit/infrastructure/test_stremio_playcheck_script.py` | `scripts/stremio_playcheck.py`, which fetches every stream of a running instance like a player (HLS to the first segments, files with a seek) |

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
