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
1. **Search cache** — the title-matching search results of a request are cached per title, season and episode (`stremio:search:{content_type}:{imdb_id}:{season}:{episode}`; the content type keeps a TMDB movie and series with the same number apart, `CachePort`: diskcache or Redis) for `cache.search_ttl_seconds`; resolution and ranking run on every request, since hoster stream URLs expire and some are bound to the resolving IP. A fresh entry skips the plugin search. An older one still answers for 6 h while one background search refreshes it (stale-while-revalidate). Refreshes run one title at a time, each with the whole plugin time from its own start: a burst of stale titles would split the plugin slots with the requests' own searches, and a refresh cut short replaces its entry with a thinner one. Requests for the same title share one running search (single-flight), and a search goes on when the request that started it goes away. Entries without results are not stored. `cache.search_ttl_seconds: 0` turns the cache off; the search and the answer work the same without it.
1. **Plugin search** — `PluginSearchRunner` searches each language group with the full title and, if the title contains `:`, the base title before the colon; bounded by the global `ConcurrencyPool`, with circuit breaker. The search ends `plugin_timeout_seconds` after the request started (a stale entry's refresh: after its own start): plugins waiting for a concurrency slot use up that budget too, running ones are cut at the deadline, queued ones are skipped. Each result goes on once (`result_key`: plugin, title, release, links), so the title filter scores a result both queries find once. Plugins whose site failed the periodic health check are skipped (`stremio_plugins_unreachable`), before a mirror group picks its member: `PluginHealthMonitor` sends a HEAD to every Stremio plugin's domains every `plugin_health_interval_seconds` (30 min, the first check 60 s after the start, 5 at a time) and to the unreachable ones every 5 minutes. A site is unreachable when a check and its retry 30 s later get no answer (DNS, connect or read error, 5 s timeout) or a server error and no challenge page (522: Cloudflare cannot reach it); a challenge page means it is up behind Cloudflare. One answer from any of the plugin's domains brings it back (`plugin_unreachable`, `plugin_reachable`). A check in which no site answers changes nothing (`plugin_health_no_answer`: the own network or DNS is down). Such plugins used to run in every search until the deadline, since their fetch errors end as empty answers, which the circuit breaker does not count. The search runs as its own task until every plugin is done (at most `plugin_timeout_seconds`, 30 s): each plugin's results pass the title filter when they arrive and go into the search's `SearchProgress`, which the requests on the search read while it runs; the cache entry is stored when it ends. The answer does not wait for it (Resolution below), so plugins still running at the answer fill the cache for the next request. The search holds its share of the concurrency pool until it ends: a concurrent request gets half the plugin slots meanwhile. App shutdown cancels it.
1. **Episode filtering** — for series requests, results are filtered by season/episode (guessit on the result title; a title without an episode, such as a show page, a season page ("Staffel 1") or a season pack (`S01`), falls back to episode labels such as `1x5` or `S01E05` in `download_links`). Multi-episode and multi-season releases (`S01E01-E03`, `S01-S03`) are kept when they contain the requested episode/season.
1. **Link validation** — Python plugin results are validated by the search engine.
1. **Title matching** — false positives (sequels, spin-offs) are filtered via fuzzy scoring.
1. **Stream conversion** — `SearchResult` objects become `RankedStream` objects with parsed quality/language.
1. **Ranking** — sort by language, quality, and hoster bonus.
1. **Resolution** — starts while the plugins still search: each batch of results is ranked with the ones before, and the request's `HosterResolution` resolves, among the top `max_probe_count` streams, each hoster's best link via `HosterResolverRegistry.resolve` (bounded by `probe_concurrency`): the streams of one hoster and language in rank order, the next one only after the better one failed, none while one runs or after one resolved; a better-ranked link that arrives later resolves too, and each URL resolves once. A link that better ones push out of the top `max_probe_count` stops resolving (cancelled): the answer does not wait for it, and its slot frees. With `verify_streams` every resolved URL must also pass a playback check. **The answer** goes out once `resolve_target_count` (5) hosters have a video, or when the search is done and no resolution runs or is due, at the latest `stream_deadline_seconds` (60 s) after the request started (`stremio_resolve_complete` with `reason` target, done or deadline). Unfinished resolutions are cancelled, also when the request itself is cancelled (shutdown). The first answers of the fifth end-to-end round waited for a 7 s soft deadline and a 4 s grace although titles with many streams had 5 after about 5 s, and titles with few streams got fewer because slow plugins (kinoking 8.5 s, the s.to gate 20 s) were not waited for. Without a resolver (or without a base URL) the answer waits for the search, at most until `stream_deadline_seconds`. Results from the search cache answer at once when the resolver's cache holds a stream for one of their hosters (`stremio_resolve_from_cache`): each hoster contributes its best link with a cached stream, past links cached as dead, echoed embed URLs and links not resolved yet (a plugin that answered after the first answer can rank a new link of the hoster first; an echo is dropped from the answer and left the hoster out for an hour), and the answer does not wait for the links without one. Those resolve in the background, one title at a time (`_BACKGROUND_RUNS`; a title queues behind the others), without the target, until `stream_deadline_seconds` after its run started (or shutdown), and fill the resolver's cache for the next request. 17 cached titles asked within seconds had started 17 runs at once, and 12 Filemoon resolutions queued for the stealth browser's 2 pages until their 10 s timeout, which opened its breaker (dev-server end-to-end run, 2026-10-05). Without a cached stream the results resolve as above. Plugin pages behind Cloudflare and hoster captures share the stealth browser's pages (`PageGate`): the next free page goes to a resolution at play time, then to the earliest due work (a search's plugin pages, due when the search ends, before the captures of its answer, due at `stream_deadline_seconds`), and to background resolutions only while nobody else waits; a running page is never taken away. In the sixth production round kinoger's pages queued behind captures until its 30 s timeout on 13 of 13 searches ([Browser Page Budget](../plans/browser-page-budget.md)).
1. **Dedup** — this yields the best *working* stream per hoster and language (`hoster_key()`: dub and sub on one hoster are different content, e.g. anime), so a hoster whose best-ranked link is dead still contributes its next one. Resolving every candidate at once instead opened dozens of connections to distinct CDNs within a second, which the home router blocked like a port scan (the whole machine lost network for ~30–60 s).
1. **Caching + formatting** — every answered stream points at Scavengarr and gets a `CachedStreamLink` (hoster URL, resolved video URL and headers, `resolved_at`), so the stream can resolve again when Stremio plays the kept stream object later: autoplay plays the next episode's stream about an hour after it was fetched, "Continue Watching" days later. HLS goes through the HLS proxy, a file through `/play/` (both below). The link's id comes from the hoster URL (`stream_link_id`): one link per stream, refreshed by every answer that has it, kept for `stream_link_ttl_seconds` (7 days). Links are saved in parallel. The repository (`CacheStreamLinkRepository`) also keeps the links of the latest answers in memory (4096), so a failing cache neither empties the answers nor makes `/play` and the HLS proxy answer 404: it logs `stream_link_save_failed` or `stream_link_load_failed`, and the cache keeps the links across restarts (diskcache raised on every write when locked or full, Redis lost the writes; code review, 2026-10-06). A save that still raises (another repository) is logged as `stremio_stream_link_save_failed` and drops those streams. Saving a link for every ranked stream (73 for one film) cost seconds per answer; one per answered stream does not. Streams that are not resolved (failed, only echoed the embed URL, beyond `max_probe_count`, or cancelled at the answer) are dropped.

---

## Endpoints

All endpoints are prefixed with `/api/v1/stremio/`. All responses except `/health` carry `Access-Control-Allow-Origin: *`.

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
- `url` — `/api/v1/stremio/play/{stream_id}` for a file, `/api/v1/stremio/proxy/{stream_id}/scavengarr.m3u8` for every HLS stream
- `behaviorHints` — `bingeGroup` on every stream, `filename` when the site gives a release name, plus the playback hints (see below)

### Autoplay of the next episode (bingeGroup)

Stremio's binge watching (Settings → Player → auto-play next episode) requests the next episode's streams when an episode starts and later plays the **first** of them whose `behaviorHints.bingeGroup` equals the current stream's (exact string match; no group, no autoplay). Scavengarr sets `bingeGroup` to `scavengarr|<language code>` (`scavengarr|de`, `scavengarr|de-sub`, `scavengarr|unknown`): a German dub continues in German, with the best-ranked stream the next episode has, whatever its hoster or quality. Groups with hoster or quality would often find no match in the next episode, and autoplay would stop.

`behaviorHints.filename` carries the release name when the site has one; Stremio passes it to subtitle addons (OpenSubtitles matches releases by name).

A stream whose link does not resolve (or only echoes the embed page) is left out of the answer: `/play/` would fail on it too (502).

### Stream behaviorHints (proxyHeaders)

A resolved file points at `/play/{stream_id}` (a redirect to its video URL) with `behaviorHints` holding a browser `User-Agent` merged with the resolver's headers:

```json
{
  "name": "Scavengarr\n1080p",
  "description": "Movie.Title.2021.German.DL.1080p.WEB.x264\nGerman Dub · 1.4 GB\nVOE · kinoger",
  "url": "https://scavengarr.example/api/v1/stremio/play/3f2a…",
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
- `proxyHeaders.request` tells Stremio which HTTP headers to send when fetching the video. Stremio's streaming server follows `/play/`'s redirect with them (checked 2026-10-05 against the maintainer's server: the redirected request still carried the `Referer`).
- The streaming server sets only those headers and passes the browser's other ones on, `Accept-Language` among them. A CDN that binds the video URL to such a header (VEEV, a `ClientBoundResolverPort` in [Hoster Resolvers](./hoster-resolvers.md)) refused the stored URL in Stremio Web (Firefox), while ffmpeg's probe, which sends no `Accept-Language`, played it (production, 2026-10-06). `/play` therefore resolves such a hoster again for a player whose bound headers differ from the stored link's and redirects there; the player sends the CDN the same headers after the redirect. The player's link is kept apart from the stored one (`<stream id>-<digest of the headers>`, fresh for an hour), its requests share one resolution, and a failed one falls back to the stored link.
- Most hoster CDNs reject requests without a valid `Referer` header.

**Platform support:**

| Platform | Status |
|---|---|
| Desktop (Electron) | Full support |
| Android | Partial (some Referer bugs with specific hosters) |
| iOS | Partial (KSPlayer engine only) |
| Web | With a streaming server: plays through the server's `/proxy` (checked 2026-10-03 with DoodStream, Vinovo, FireStream); without one not supported (CORS) |

> **Known issue (IP-bound streams):** DoodStream and Vinovo bind a stream URL to the IP that resolved it; another IP gets `200 error_wrong_ip` (DoodStream's CDN) or 403 (Vinovo). A file stream (a redirect from `/play/`) therefore plays only when the player (Stremio's streaming server) reaches the internet through the same IP as Scavengarr: with Scavengarr behind a VPN and the player at home it fails. Streams through the HLS proxy are fetched by Scavengarr and are not affected. `scripts/stremio_playcheck.py` shows it from the player's machine.

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

**When is it used?** For every resolved HLS stream (`is_hls`), with or without headers: a redirect to an HLS playlist fails on Android (stremio-bugs #1574), and the stream must be able to resolve again later. Streams with headers need it on every sub-request anyway (all XFS video hosters set `Referer`, StreamUp, Vidsonic). Their only playback hint is `notWebReady: true` (no `proxyHeaders`); `bingeGroup` and `filename` stay. A stream's URL is `/proxy/{stream_id}/scavengarr.m3u8` (`HLS_MASTER`): under this fixed name the proxy serves the current playlist, whatever the CDN calls it.

**How it works:**

1. Look up the `CachedStreamLink` (`video_url`, `video_headers`, `is_hls`); `404` if missing, `400` if it is not an HLS proxy stream. For the playlist (`scavengarr.m3u8`) the link is the current one: a video URL resolved more than an hour ago resolves again (`StremioLinks`, shared by concurrent requests, saved; when the hoster gives no video, the stale URL is tried, since its CDN can still serve it), and when the CDN refuses the playlist with 403, 404 or 410 (an expired token) the hoster URL resolves once more past the resolver's cache (`StremioLinks.after_refusal`) and the playlist is fetched again (`502` when the hoster has no video anymore). Such a refresh joins no running resolution that can answer from the cache (with the refused URL), but every request for the link joins a running refresh. Variants and segments follow a playlist fetched moments before and keep the link.
1. The playlist is `video_url` itself; other paths are the CDN base of `video_url` + `path`, with the request query string or, if empty, the original `video_url` query (auth tokens). A `path` that would leave the stream's CDN (absolute URL, `//host`, other scheme) is answered with `400`: the path comes from the client, and without this check the endpoint was an open proxy into the server's network (SSRF).
1. Paths not ending in `.m3u8` are streamed from the CDN as segments (`StreamingResponse`); a failed segment request closes its connection before the error is returned, so CDN errors cannot drain the shared HTTP connection pool. Segments go out in 64 KiB pieces: VOE's CDN sends 4 KiB TLS records, and passed through one by one, each a response write, they cost 41 ms of CPU per MB on x86 against 25 ms in 64 KiB pieces (dev-server end-to-end run, 2026-10-05). An encoded body (`Content-Encoding`, which the proxy does not forward) is decoded. TLS runs in the event loop (the shared client's `AsyncioNetworkBackend`): on the Raspberry Pi 4 a relayed MB costs 75–88 ms of CPU, against 103–112 ms with httpx's default anyio backend; on x86 the backend alone saves 14% (43 against 49 ms per MB with the chunks passed through).
1. `.m3u8` manifests are fetched with the stored headers (cached for 60 s, at most 512 manifests: when full, expired ones go first, then the oldest), and the CDN's URIs are rewritten to proxy URLs, in URI lines and in the `URI="…"` attributes of tags (audio renditions, keys, init segments). They point at a copy of the link the playlist came from (`StremioLinks.pinned`: `<stream-id>.<digest of its video URL>`, one per resolution): all answers and devices share one link per hoster URL, and a later resolution under its id moved a running playback's segments to another CDN node with the old token (403, then 502; code review, 2026-10-06). A relative URI therefore becomes `<proxy>/<copy-id>/<playlist directory><uri>` instead of resolving against the request URL. A URI from the CDN's root (`/secure/98/<id>/video.m3u8`, Vidsonic's variants), a protocol-relative one or an absolute URL of the CDN outside the stream's directory becomes `<proxy>/<copy-id>//<path>`; the proxy joins that absolute path with the CDN origin. URIs of other origins and schemes (`data:`, `skd:`) stay direct, since the proxy fetches from the stream's own CDN only.
1. CDN fetches share a global semaphore (50); CDN errors return `502`.
1. `HEAD` answers like `GET`; the server drops the body. When its streaming server cannot probe a stream, Stremio Web reads the content type with `HEAD` (stremio-video's `getContentType`) before it plays. A `405` without CORS headers ended these streams in error 83, "Video is not supported". For a segment `HEAD` takes the CDN's status and content type only: the segment is not downloaded or counted as sent.
1. The playlist is refused (`403`, `hls_proxy_converter_refused`) to a streaming server's ffmpeg (User-Agent `Lavf/`) unless `stremio.allow_hls_transcoding` is on. Stremio Web has its streaming server probe every stream; an HLS source (format `hls`) then goes through the server's converter (`/hlsv2/…`), which re-encodes the video with libx264, since it repackages MP4 and Matroska only. On a Raspberry Pi 4 without a usable hardware encoder ("no viable acceleration profiles detected" in the server log) a 1480×620 stream took 1–2 cores and 1080p stuttered (2026-10-05). The failed probe makes Stremio Web play the playlist itself with hls.js after a `HEAD` for its content type (`getPlayability` in stremio-video falls back to `canPlayStream`). Variants and segments are not refused. Native players (Android, desktop) never ask the server's converter.

### Play

```http
GET  /api/v1/stremio/play/{stream_id}
HEAD /api/v1/stremio/play/{stream_id}
```

The URL of every resolved file stream (and of all streams without a resolver):

1. Look up `stream_id` in the stream link cache (`404` if missing; links are kept `stream_link_ttl_seconds`, 7 days).
1. Take the stored video URL while it is fresh (resolved less than an hour ago: every working link of the measurement still played after 92 minutes), else resolve the hoster URL again (`StremioLinks`: concurrent requests for one link share one resolution, one tap on Android sent 11; the new video URL is saved).
1. Return a **302 redirect** to the video URL.
1. When the hoster gives no video (or only echoes the embed page), the stored video URL is still tried: its CDN may still serve it. **502** only without a stored video URL (never a redirect to an embed page).
1. `HEAD` answers like `GET`: a streaming server asks with `HEAD` first.

### Health

```http
GET /api/v1/stremio/health
```

Reports component status (`tmdb_configured`, `stream_plugin_count`, `stream_plugins`, use case/resolver/link-cache flags), `supported_hosters` (resolver names from `HosterResolverRegistry.supported_hosters`), and under `metrics` the plugin and event-loop statistics of `/api/v1/stats/metrics` (`uptime_seconds`, `plugins`, `event_loop`). Returns `200` when healthy and `503` otherwise; healthy requires a title client (`tmdb_configured` is also `true` for the IMDB fallback client), both use cases, the resolver registry, the stream link cache, and at least one `stream` plugin.

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
| `max_concurrent_plugins` | 5 | httpx slots of the global concurrency pool |
| `max_concurrent_playwright` | 5 | Playwright slots of the global concurrency pool |
| `max_results_per_plugin` | 100 | Per-plugin result limit in Stremio searches |
| `plugin_timeout_seconds` | 30 | Plugin search budget, counted from the request start (queueing for a slot included; a stale entry's refresh: from its own start); the answer does not wait for it |
| `plugin_health_interval_seconds` | 1800 | Health check of every Stremio plugin's site; searches skip the unreachable ones, which are checked again every 5 minutes (`0` = off) |
| `stream_deadline_seconds` | 60 | Latest answer, from the request start; resolution stops here |
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
| `resolve_target_count` | 5 | The answer goes out once this many hosters have a video, also while plugins search (`0` = wait until everything is done or the deadline) |
| `allow_hls_transcoding` | `false` | Let Stremio's streaming server transcode HLS streams; off, the HLS proxy refuses its ffmpeg the playlist and Stremio Web plays HLS itself (see HLS Proxy) |
| `verify_streams` | `true` | Playback check of every resolved URL: first bytes with the playback headers; error status, HTML or a non-playlist HLS answer drops the stream (result cached like a failed resolution). Off, only resolvers without a check of their own (SuperVideo) are checked |
| `stream_link_ttl_seconds` | 604800 | How long the links behind `/play/` and the HLS proxy are kept (`streamlink:{stream_id}`, 7 days); stale ones resolve again |
| `probe_stealth_timeout_seconds` | 15 | Page timeout of the `StealthPool` (browser-based resolvers, Cloudflare fallback) |

Resolution is the liveness check: a stream is only returned when its hoster link resolves (and passes the playback check).

### Timings

Defaults, with production's values (`data/config.yaml`) where they differ:

| Timing | Default (production) | Bounds | Effect |
|---|---|---|---|
| `stremio.plugin_timeout_seconds` | 30 s | Each plugin search, from the request start (queueing for a slot included; a stale entry's refresh: from its own start) | Running plugins are cut then, queued ones skipped; their results until then are cached |
| `stremio.resolve_target_count` | 5 | — | The answer goes out once 5 hosters have a video |
| `stremio.stream_deadline_seconds` | 60 s | The answer, from the request start | Latest answer: running resolutions are cancelled, the search goes on |
| `cache.search_ttl_seconds` | 900 s (1800 s), plus 6 h stale | A search cache entry | Fresh: no search; stale: answers while one background search refreshes it (one title at a time) |
| Resolution cache (fixed) | streams 1 h, dead links 15 min, redirects 1 h | The resolver registry's results | A cached search answers at once with them, the rest resolves in the background |
| `http_timeout_resolve_seconds` | 15 s (10 s) | One hoster resolution, without the wait for a stealth browser page | Timeouts and cut resolutions are not cached; a timeout counts for the hoster breaker, a cut neither counts nor resets it, nor does a capture that got no browser page before its request was due (`busy`) |
| `stremio.probe_stealth_timeout_seconds` | 15 s (10 s) | A page of the stealth browser (browser hosters, Cloudflare fallback) | |
| `http_timeout_seconds` | 30 s (15 s), connect 5 s | Requests of the shared HTTP client without a timeout of their own (TMDB lookups) | httpx plugins use `_timeout` (15 s, connect included), most resolvers 15 s |
| `http_retry_*` | 3 retries, backoff from 1 s to 30 s (2, 0.5 s, 10 s) | Retries of 429 and 503 answers | |
| Keep-alive (fixed) | 60 s, 20 connections | Idle connections of the shared client | Saves TLS handshakes between requests |
| Circuit breakers (fixed) | 5 failures, then 60 s doubling to 1 h | A plugin per category, a hoster resolver | Skipped while open; one half-open probe |
| `stremio.plugin_health_interval_seconds` | 1800 s; unreachable ones every 5 min, the first check 60 s after the start; a site without an answer is tried again after 30 s | The plugin site checks | Unreachable plugins are skipped |
| `stremio.stream_link_ttl_seconds` | 7 days | Stored links of `/play` and the HLS proxy | Older links answer 404; a video URL older than 1 h (fixed) resolves again at playback |
| HLS proxy (fixed) | manifests cached 60 s and fetched within 15 s; segments within `http_timeout_seconds` | Manifest and segment requests | |
| SSRF guard (fixed) | DNS answers cached 60 s | Checked addresses | |

Scored plugin selection keys (`scoring_enabled`, `max_plugins_scored`, `exploration_probability`, …) are documented in [Plugin Scoring & Probing](./plugin-scoring-and-probing.md#configuration).

### Circuit Breaker

`PluginCircuitBreaker` is created in the composition root with hardcoded values (`failure_threshold=5`, `cooldown_seconds=60.0`, `max_cooldown_seconds=3600.0`); they are not configurable. The breaker tracks each plugin per requested category (`kinoking:2000` for its movies, `kinoking:5000` for its series): a site can be too slow for one content type only (kinoking's movie pages take 12–17 s to answer, its series pages 1 s), and its movies must not cost every movie request the search budget while its series keep coming. After 5 consecutive failures (exceptions or timeouts) a plugin is skipped for that category for 60 s (`stremio_plugin_circuit_open`). An answer without results neither counts nor resets the breaker: kinoking answers a search without hits at once, and those answers kept closing the breaker between its timeouts. After the cooldown a single probe request is allowed (half-open): concurrent requests skip the plugin until the probe reports, and a probe that never reports (cancelled, or a timeout that is not counted) is replaced by the next request after one more cooldown. Success resets the breaker and its cooldown, failure reopens it with twice the previous cooldown (60 s → 2 min → 4 min … capped at 1 h). Stremio requests are usually minutes apart, so a fixed 60 s cooldown would let an unreachable plugin cost almost every request its full timeout. A timeout counts as a failure when the plugin had at least half of `plugin_timeout_seconds`; a plugin cut by the search deadline after queueing for most of the budget is not blamed.

Hoster resolution has a breaker of its own, per resolver: a hoster whose resolutions keep timing out or giving unplayable streams is skipped the same way, except that its half-open probe runs to its end even when the request is cut ([Registry Features](hoster-resolvers.md#registry-features)).

### Mirror Groups

Some sites front one database: hdfilme, streamcloud and streamkiste list the same titles under the same news ids (Oppenheimer is 23684 on all three), in the same order, with the same texts and the same devideosrc player, only in three themes. A plugin declares such a group with `mirror_group` (the three set `"hdfilme"`); the composition root collects them (`_mirror_groups`) for `PluginSearchRunner`. A Stremio request asks one member per group: among the reachable members whose breaker for the category is closed (`PluginCircuitBreaker.is_closed`, which unlike `allow` starts no probe), the one with the best plugin score of the scoring subsystem (`final_score` of the `current` snapshot, from its health and search probes; scores with a confidence up to 0.1 count as none, and a member without one ranks as a new snapshot, 0.5), the first in the request's plugin order on a tie or without a score store (`scoring.enabled: false`). A failing score store leaves the order (`plugin_scores_unreadable`). The others are skipped (`stremio_mirrors_skipped`); the next one with a closed breaker is the member's standby. When that member's breaker opens, the next one takes over; when no member's breaker is closed, every member goes to its breaker, so a half-open probe can bring one back. Torznab searches are not affected: each plugin stays its own indexer.

A member that gives nothing for a query (no hits, an error, a timeout) hands it to its standby (`stremio_mirror_standby`, within the request's search deadline). Empty answers do not trip the breaker, so a member whose parser broke after a theme change would otherwise keep being asked while the others still deliver (code review, 2026-10-06). When the standby delivers, the member ranks behind the other members (per category, in memory) until it delivers again (as a standby, or alone while the other members' breakers are open); when the standby gives nothing too, the title is not in the database and the ranking stays. A title the database lacks costs two searches instead of one (in production 7 of 35 hdfilme searches were empty, 2026-10-06), never three. The parser tests on real pages (`tests/unit/infrastructure/test_real_pages.py`) and the live smoke tests still watch for broken parsers.

### Behind AIOStreams

AIOStreams can include Scavengarr as a `custom` addon (`manifestUrl` = `<base>/api/v1/stremio/manifest.json`). Its per-addon `timeout` (ms, default 7000) must exceed `stream_deadline_seconds`, otherwise every Scavengarr answer is cut: use `stream_deadline_seconds` + 2 s (62000 with the defaults). AIOStreams checks the manifest when a user config is saved, so Scavengarr must be reachable from the AIOStreams host at that moment. Recommended user settings and measurements: [`docs/plans/stremio-latency.md`](../plans/stremio-latency.md#aiostreams).

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
| `tests/unit/application/test_search_progress.py` | `SearchProgress` (results while the search runs) |
| `tests/unit/application/test_search_cache.py` | `SearchCache` (stale-while-revalidate) |
| `tests/unit/application/test_hoster_resolution.py` | `HosterResolution` (resolution while the search runs) |
| `tests/unit/application/test_stremio_links.py` | `StremioLinks` (resolving again, refused playlists, pinned copies, per-player links) |
| `tests/unit/infrastructure/test_plugin_health.py` | `PluginHealthMonitor` |
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
| Search progress | `src/scavengarr/application/stremio/search_progress.py` |
| Search cache | `src/scavengarr/application/stremio/search_cache.py` |
| Hoster resolution | `src/scavengarr/application/stremio/resolution.py` |
| Stream links (`/play`, HLS proxy) | `src/scavengarr/application/use_cases/stremio_links.py` |
| Plugin health monitor | `src/scavengarr/infrastructure/plugins/health_monitor.py` |
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
