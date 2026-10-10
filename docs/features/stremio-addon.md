[← Back to Index](./README.md)

# Stremio Addon

> Stream resolution from Scavengarr plugins directly into the Stremio media player.

---

## Overview

Scavengarr includes a **Stremio addon** that provides catalog browsing, catalog search, and stream resolution. It bridges plugin search results with Stremio's stream protocol and resolves hoster embed URLs into direct video playback links via the [Hoster Resolver System](./hoster-resolvers.md).

The addon accepts IMDb (`tt*`), TMDB (`tmdb:*`) and Kitsu (`kitsu:*`, the anime catalogs; see [Anime Ids](#anime-ids)) identifiers and ranks streams by language, quality, and hoster reliability.

---

## Architecture

```text
Stremio App
  ├── GET /manifest.json                     → addon metadata + catalogs
  ├── GET /catalog/{type}/{id}.json          → TMDB trending
  ├── GET /catalog/{type}/{id}/search={q}.json → TMDB search
  ├── GET /stream/{type}/{id}.json           → plugin search → resolved, ranked streams
  ├── GET /play/{stream_id}                  → hoster resolution → 302 video URL
  ├── GET|HEAD /proxy/{stream_id}/{path:path} → HLS proxy (manifests + segments) and file proxy (address-bound files)
  └── GET /health                            → component status
```

### Request Flow (Stream Resolution)

```text
IMDb/TMDB ID → title lookup per plugin language, series meta (Cinemeta: kind, episode list)
  → search cache, or plugin search (with the episode reference for plugins that locate episodes)
  → episode filter → link validation → title matching (cached per title)
  → quality/language parsing → ranking
  → hoster resolution, one stream per hoster and language + playback check (deadline, early stop)
  → link cache → StremioStream list
```

`StremioStreamUseCase` (`application/use_cases/stremio_stream.py`) runs the steps in this order; the modules named in parentheses live in `src/scavengarr/application/stremio/`.

1. **Anime ids** (`AnimeIdResolverPort`, `infrastructure/anime/`) — a `kitsu:` request (the Anime Kitsu addon's catalogs) becomes the IMDb request with the episode as IMDb counts it, from the addon's meta or the public id list ([Anime Ids](#anime-ids)); an untranslatable id answers no streams.
1. **Plugin selection** (`plugin_selection.py`) — all plugins with `provides` = `stream` or `both`, or the scored top-N when scored selection is active (see [Plugin Scoring & Probing](./plugin-scoring-and-probing.md)).
1. **Title resolution** (`title_resolution.py`) — per plugin language, look up title + year via TMDB `/find` (or the IMDB Suggest/Wikidata fallback); `tmdb:` IDs are resolved via the TMDB ID.
1. **Series meta** (`title_resolution.py`; `SeriesMetaPort`, wired to `CinemetaClient` in `infrastructure/stremio/cinemeta.py`) — the request's Cinemeta meta once per request (genres and the episode list, cached 7 days; the `series_meta` phase records `found`, `not_found` or `error`): it gives every language's reference its identity for the matcher (`imdb_id`, whether the genres name animation; [Title Matching](#title-matching)) and, for a series whose list places the episode, the episode reference (`EpisodeRef`: the request's season and episode, the entry's English title and air date, and the absolute number: a `kitsu:` request's episode number when it lies within 5 of the entry's position among the regular seasons, else that position). The reference travels with the search to the plugins that declare `locates_episodes` (aniworld, sto for its anime results): they place the episode on the site's own season pages ([Python Plugins](./python-plugins.md)), answer the located page with `site_season`, `site_episode` and `episode_located_by` in the result's metadata, and answer nothing for the series when no row matches, never the request's numbers in the site's own Staffel numbering. Every other plugin is searched as before.
1. **Search cache** (`title_search.py`, `search_cache.py`) — the title-matching search results of a request are cached per title, season, episode and placement (`stremio:search:v3:{content_type}:{imdb_id}:{season}:{episode}:{absolute}`, the episode reference's absolute number or `-` without one, computed in the use case once the reference exists; the content type keeps a TMDB movie and series with the same number apart, the absolute number keeps a Kitsu request, which places the episode by its own number, apart from the IMDb request for the same season and episode, which places it by the list's position: neither shares the other's entry nor joins its running search, `CachePort`: diskcache or Redis) for `cache.search_ttl_seconds`; resolution and ranking run on every request, since hoster stream URLs expire and some are bound to the resolving IP. A fresh entry skips the plugin search. An older one still answers for 6 h while one background search refreshes it (stale-while-revalidate). Refreshes run one title at a time, each with the whole plugin time from its own start: a burst of stale titles would split the plugin slots with the requests' own searches. A refresh merges per plugin: a plugin that finished replaces its own earlier results in the entry, one that timed out or failed keeps them and is `missing`, so no refresh thins an entry; the refreshed entry's age counts from the refresh's start. Requests for the same title share one running search (single-flight): one arriving within the search's answer budget shares the wait (`joined`), one arriving after it answers at once with what the search found so far (source `cache`, no wait). A search goes on when the request that started it goes away. Its entry goes into the cache at the answer budget (`plugin_timeout_seconds` after the request's start) naming the plugins still to come as `missing`, again after each of them that finishes (its results replace its earlier ones, its name leaves `missing`; one that timed out or failed stays), and at the search's end; the entry's age counts from the search's start, and an entry stored before the field existed loads as complete. A request that finds an entry with plugins `missing` and no running search for its key answers from the entry and starts one completion search for those plugins only (single-flight per key, one title at a time with the refreshes; `stremio_search_completion` logs the plugins and the outcome): it merges per plugin like a refresh, keeps the entry's age and clears `missing` at its end whatever each plugin's outcome, so no entry is completed twice. Entries without results are not stored. `cache.search_ttl_seconds: 0` turns the cache off; the search and the answer work the same without it.
1. **Plugin search** (`plugin_search.py`) — `PluginSearchRunner` searches each language group with the full title and, if the title contains `:`, the base title before the colon; bounded by the global `ConcurrencyPool`, with circuit breaker. Every plugin gets its full `plugin_timeout_seconds` from the moment it holds a concurrency slot, a plugin queued for a slot runs when it gets one, and the request's answer budget (`plugin_timeout_seconds` after the request started; a stale entry's refresh: after its own start) cuts no plugin: one that returns after it is `late` in the metrics, and its results reach the cache and the next request. Each result goes on once (`result_key`: plugin, title, release, links), so the title filter scores a result both queries find once. Plugins whose site failed the periodic health check are skipped (`stremio_plugins_unreachable`), before a mirror group picks its member: `PluginHealthMonitor` sends a HEAD to every Stremio plugin's domains every `plugin_health_interval_seconds` (30 min, the first check 60 s after the start, 5 at a time) and to the unreachable ones every 5 minutes. A site is unreachable when a check and its retry 30 s later get no answer (DNS, connect or read error, 5 s timeout) or a server error and no challenge page (522: Cloudflare cannot reach it); a challenge page means it is up behind Cloudflare. One answer from any of the plugin's domains brings it back (`plugin_unreachable`, `plugin_reachable`). A check in which no site answers changes nothing (`plugin_health_no_answer`: the own network or DNS is down). A plugin whose own domain check finds no reachable domain during a search (`PluginUnreachableError` from `_verify_domain()`, `stremio_plugin_unreachable`) is marked unreachable at once (`plugin_unreachable` with `source` search), counts `unreachable` in its record and reports the outcome `unreachable`; the recheck clears it like a failed periodic check. Such plugins used to run in every search until the deadline, since their fetch errors end as empty answers, which the circuit breaker does not count. The search runs as its own task until every plugin is done (at most the selected plugins over the concurrency slots, times `plugin_timeout_seconds`): each plugin's results pass the title filter when they arrive and go into the search's `SearchProgress`, which the requests on the search read while it runs; the entry goes into the cache as the Search cache above says. The answer waits for it until the answer budget at most (Resolution below), so plugins still running then fill the cache for the next request. The search holds its share of the concurrency pool until it ends: a concurrent request gets half the plugin slots meanwhile. App shutdown cancels it.
1. **Episode filtering** — for series requests, results are filtered by season/episode: the result's `metadata` (`season`, `episode`, ints set by plugins that know the episode from the page) decides first, else guessit on the result title. A result of another season or episode is dropped. A result without an episode number (a show page, a season page ("Staffel 1"), a season pack (`S01`)) passes only through its links labelled with the requested episode (`1x5`, `S01E05`, `Folge 5`, `Episode 5`, `E05` in `download_links`); without any such link it is dropped, so a show page cannot leak other episodes (before, it passed with every link). A result whose title names the episode keeps the links labelled with it or with no episode, and is dropped when its labelled links all name other episodes: a page titled after the episode but linking the whole season passed with every link before. A result with an episode but no season passes for season 1 only. Multi-episode and multi-season releases (`S01E01-E03`, `S01-S03`) are kept when they contain the requested episode/season. The runner counts the dropped results per plugin in the [plugin record](./observability.md#plugin-record) (`dropped`), and `stremio_search_complete` carries the search's `dropped`. The audit before and after the change: [Series Episodes](../plans/series-episodes.md).
1. **Link validation** — Python plugin results are validated by the search engine.
1. **Title matching** (`title_resolution.py`) — false positives (sequels, spin-offs) are filtered via fuzzy scoring.
1. **Stream conversion** (`answer.py`) — `SearchResult` objects become `RankedStream` objects with parsed quality/language.
1. **Ranking** (`answer.py`) — sort by language, quality, and hoster bonus.
1. **Resolution** (`resolution.py`) — starts while the plugins still search: each batch of results is ranked with the ones before, and the request's `HosterResolution` resolves, among the top `max_probe_count` streams, each hoster's best link via `HosterResolverRegistry.resolve` (bounded by `probe_concurrency`): the streams of one hoster and language in rank order, the next one only after the better one failed, none while one runs or after one resolved; a better-ranked link that arrives later resolves too, and each URL resolves once. A link that better ones push out of the top `max_probe_count` stops resolving (cancelled): the answer does not wait for it, and its slot frees. With `verify_streams` every resolved URL must also pass a playback check. **The answer** goes out once `resolve_target_count` (5) hosters have a video, or when the search is done, or its answer budget (`plugin_timeout_seconds` after the request started) has passed, and no resolution runs or is due, at the latest `stream_deadline_seconds` (60 s) after the request started (`stremio_resolve_complete` with `reason` target, done, budget or deadline). Results arriving after the budget are not taken into the answer: they go to the cache for the next request. Unfinished resolutions are cancelled, also when the request itself is cancelled (shutdown). The first answers of the fifth end-to-end round waited for a 7 s soft deadline and a 4 s grace although titles with many streams had 5 after about 5 s, and titles with few streams got fewer because slow plugins (kinoking 8.5 s, the s.to gate 20 s) were not waited for. Without a resolver (or without a base URL) the answer waits for the search, at most until the answer budget. Results from the search cache answer at once when the resolver's cache holds a stream for one of their hosters (`stremio_resolve_from_cache`): each hoster contributes its best link with a cached stream, past links cached as dead, echoed embed URLs and links not resolved yet (a plugin that answered after the first answer can rank a new link of the hoster first; an echo is dropped from the answer and left the hoster out for an hour), and the answer does not wait for the links without one. Those resolve in the background, one title at a time (`_BACKGROUND_RUNS`; a title queues behind the others), without the target, until `stream_deadline_seconds` after its run started (or shutdown), and fill the resolver's cache for the next request. 17 cached titles asked within seconds had started 17 runs at once, and 12 Filemoon resolutions queued for the stealth browser's 2 pages until their 10 s timeout, which opened its breaker (dev-server end-to-end run, 2026-10-05). Without a cached stream the results resolve as above. Results arriving after an answer take the same background path (one run per cache key, a search handed over while the key's run runs starts when it ends, cached answers wait once; one title at a time, until `stream_deadline_seconds` after the run started): a search continuing past its budget hands its progress over at the budget, a refresh and a completion at their start, so the next request finds their outcomes cached. The use case's `answer()` returns the streams with the search's source (`StreamAnswer`) and whether the answer is complete: no plugin missing from it and no search running for its key; `stremio_search_complete` logs `source`, `complete` and `missing`, the route's `stremio_stream_response` `source`, `complete` and `missing_count`. Plugin pages behind Cloudflare and hoster captures share the stealth browser's pages (`PageGate`): the next free page goes to a resolution at play time, then to the earliest due work (a search's plugin pages, due when the search ends, before the captures of its answer, due at `stream_deadline_seconds`), and to background resolutions only while nobody else waits; a running page is never taken away. The number of pages starts at 2 and follows the waits for a page, the CPU and the free memory (`PageBudget`, between 1 and `max_concurrent_playwright`). In the sixth production round kinoger's pages queued behind captures until its 30 s timeout on 13 of 13 searches ([Browser Page Budget](../plans/browser-page-budget.md)).
1. **Dedup** — this yields the best *working* stream per hoster and language (`hoster_key()`: dub and sub on one hoster are different content, e.g. anime), so a hoster whose best-ranked link is dead still contributes its next one. Resolving every candidate at once instead opened dozens of connections to distinct CDNs within a second, which the home router blocked like a port scan (the whole machine lost network for ~30–60 s).
1. **Caching + formatting** (`stream_builder.py`, `answer.py`) — every answered stream points at Scavengarr and gets a `CachedStreamLink` (hoster URL, resolved video URL and headers, `resolved_at`), so the stream can resolve again when Stremio plays the kept stream object later: autoplay plays the next episode's stream about an hour after it was fetched, "Continue Watching" days later. HLS goes through the HLS proxy, a file through `/play/`, an address-bound file through the file proxy (all below). The link's id comes from the hoster URL (`stream_link_id`): one link per stream, refreshed by every answer that has it, kept for `stream_link_ttl_seconds` (7 days). Links are saved in parallel. The repository (`CacheStreamLinkRepository`) also keeps the links of the latest answers in memory (4096), so a failing cache neither empties the answers nor makes `/play` and the HLS proxy answer 404: it logs `stream_link_save_failed` or `stream_link_load_failed`, and the cache keeps the links across restarts (diskcache raised on every write when locked or full, Redis lost the writes; code review, 2026-10-06). A save that still raises (another repository) is logged as `stremio_stream_link_save_failed` and drops those streams. Saving a link for every ranked stream (73 for one film) cost seconds per answer; one per answered stream does not. Streams that are not resolved (failed, only echoed the embed URL, beyond `max_probe_count`, or cancelled at the answer) are dropped.

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
- ID prefixes: `tt` (IMDb), `tmdb:` (TMDB), `kitsu:` (Kitsu, the Anime Kitsu addon's catalogs)
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

- Movie: `stream_id` = `tt1234567`, `tmdb:12345` or `kitsu:11614`
- Series: `stream_id` = `tt1234567:1:5` or `tmdb:12345:1:5` (season 1, episode 5), `kitsu:41982:3` (episode 3 as Kitsu counts, no season; [Anime Ids](#anime-ids))
- Response: `{"streams": [StremioStream, ...]}`
- Response headers: `X-Cache` (`MISS` for a fresh search, `HIT` for a cache entry, `STALE` for a stale entry refreshed in the background, `JOINED` for a shared wait for the title's running search) and `X-Search-Complete` (`true` when no plugin is missing from the answer's entry and no search runs for it, else `false`: a retry a minute later finds more). `scripts/stremio_round.py` records both.

Each stream contains:

- `name` — `Scavengarr` and the quality on a second line (`4K`, `1080p`, `720p`, `SD`, `TS`, `CAM`; no line for unknown quality; see [Quality and Size](#quality-and-size)). Stremio shows `name` in a narrow column, like other addons' `Torrentio\n1080p`.
- `description` — one short line per fact, since Stremio cuts long lines: the site's own title (release name, else the site's title, else the reference title with the year; series titles get ` SxxEyy` unless they are release names), then `language · size` (the site's size, else the measured one), then `HOSTER · plugin`. The site's title shows a wrong match that the reference title would hide.
- `url` — `/api/v1/stremio/play/{stream_id}` for a file, `/api/v1/stremio/proxy/{stream_id}/scavengarr.m3u8` for every HLS stream, `/api/v1/stremio/proxy/{stream_id}/file` for a file whose CDN plays it only for the address that resolved it (DoodStream, MixDrop, Vinovo, FSST; File Proxy below)
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

> **Address-bound streams:** DoodStream, MixDrop, Vinovo and FSST bind a stream URL to the address that resolved it; another address gets `200 error_wrong_ip` (DoodStream's CDN), 403 (MixDrop, Vinovo) or 410 (FSST). A `/play/` redirect to such a URL played only when the player (Stremio's streaming server) reached the internet through the same address as Scavengarr: from the dev container 36 of 37 redirects of production's cached streams failed on them (seventh round, `docs/plans/stremio-latency.md`). Their resolvers declare `address_bound`, and their files go through the file proxy (below), fetched by Scavengarr like HLS. A stored link from before the flag keeps `/play` until its hoster resolves again. `scripts/stremio_playcheck.py` shows playability from the player's machine.

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
1. `.m3u8` manifests are fetched with the stored headers (cached for 60 s, at most 512 manifests: when full, expired ones go first, then the oldest), and the CDN's URIs are rewritten to proxy URLs, in URI lines and in the `URI="…"` attributes of tags (audio renditions, keys, init segments). They point at a copy of the link the playlist came from (`StremioLinks.pinned`: `<stream-id>.<digest of its video URL>`, one per resolution): all answers and devices share one link per hoster URL, and a later resolution under its id moved a running playback's segments to another CDN node with the old token (403, then 502; code review, 2026-10-06). A relative URI therefore becomes `<proxy>/<copy-id>/<playlist directory><uri>` instead of resolving against the request URL. A URI from the CDN's root (`/secure/98/<id>/video.m3u8`, Vidsonic's variants), a protocol-relative one or an absolute URL of the CDN outside the stream's directory becomes `<proxy>/<copy-id>//<path>`; the proxy joins that absolute path with the CDN origin. Hosts compare without case: VidHide names its CDN host in mixed case in the stream URL and in lower case in its playlists, and compared as written its segments went to the player directly, which got 403 for a token bound to the server's address (step 27, `docs/plans/vidhide-segments.md`). URIs of other origins and schemes (`data:`, `skd:`) stay direct, since the proxy fetches from the stream's own CDN only.
1. **Read-ahead** (`SegmentReadAhead`): a player loads the segments one after another over one connection, and a CDN that throttles each connection starves it (FireStream: 1.5 to 3.9 Mbit/s for a 2.9 Mbit/s title; from the Pi one connection carried 1.8 Mbit/s, two segments ahead 7.3, `scripts/probes/hls_throughput.py`, 2026-10-07). When the proxy rewrites a media playlist it remembers the CDN URLs of its segments in order (`listed_segments`, keyed by the playlist's URL; a playlist fetched again keeps the fetches of segments it still lists). A segment request starts the fetches of the next two into memory (under the CDN semaphore) and is answered from memory when its own segment was fetched ahead, a fetch in flight awaited; the segments the player passed or jumped away from are dropped. Bounds: two segments ahead per playback, each at most 16 MiB (a larger one is left to the player's own request: a declared length costs the headers only, a body without one that grows past the cap is cut and ends that playback's read-ahead), at most 8 playbacks (the least recently used goes), a playback dropped 60 s after its last request (swept by the next proxy request, no timer: the fetches that finish after a playback's last request stay in memory until then, at worst 8 playbacks x 2 x 16 MiB = 256 MiB), and a CDN host that answered a fetch ahead with 429 gets none for 10 minutes (`hls_readahead_throttled`; a failed fetch logs `hls_readahead_failed` with the CDN's domain, never a URL). `HEAD` requests touch nothing. Metrics: `scavengarr_hls_readahead_seconds` and `_total{outcome}` ([Observability](./observability.md)).
1. CDN fetches share a global semaphore (50); CDN errors return `502`.
1. `HEAD` answers like `GET`; the server drops the body. When its streaming server cannot probe a stream, Stremio Web reads the content type with `HEAD` (stremio-video's `getContentType`) before it plays. A `405` without CORS headers ended these streams in error 83, "Video is not supported". For a segment `HEAD` takes the CDN's status and content type only: the segment is not downloaded or counted as sent.
1. The playlist is refused (`403`, `hls_proxy_converter_refused`) to a streaming server's ffmpeg (User-Agent `Lavf/`) unless `stremio.allow_hls_transcoding` is on. Stremio Web has its streaming server probe every stream; an HLS source (format `hls`) then goes through the server's converter (`/hlsv2/…`), which re-encodes the video with libx264, since it repackages MP4 and Matroska only. On a Raspberry Pi 4 without a usable hardware encoder ("no viable acceleration profiles detected" in the server log) a 1480×620 stream took 1–2 cores and 1080p stuttered (2026-10-05). The failed probe makes Stremio Web play the playlist itself with hls.js after a `HEAD` for its content type (`getPlayability` in stremio-video falls back to `canPlayStream`). Variants and segments are not refused. Native players (Android, desktop) never ask the server's converter.

### File Proxy

```http
GET  /api/v1/stremio/proxy/{stream_id}/file
HEAD /api/v1/stremio/proxy/{stream_id}/file
```

A byte-range pass-through for the direct files of hosters whose CDN plays a URL only for the address that resolved it (DoodStream, MixDrop, Vinovo, FSST: `address_bound = True` on the resolver, stamped on `ResolvedStream` and kept in the stored link, see [Hoster Resolvers](./hoster-resolvers.md)). The stream builder gives such a file this URL with the HLS proxy stream's hints (`notWebReady: true`, no `proxyHeaders`); every other file keeps `/play/`, HLS its playlist proxy. The route shares the HLS proxy's path, so its rate-limit exemption and query masking apply.

**How it works:**

1. Look up the current `CachedStreamLink` as for the stream's playlist (`404` if missing; a video URL older than an hour resolves again, shared by concurrent requests); `400` when the link is HLS or not address-bound (a record from before the flag: it plays through `/play/`).
1. Request the video URL with the stored headers over the player's browser User-Agent and `Accept-Encoding: identity` (byte offsets must hold, so nothing is decoded), with the player's `Range` and `If-Range` when it sent them; redirects followed, the CDN semaphore shared with the HLS proxy, a read timeout of 60 s (Vinovo's first byte takes about 30 s), the connect timeout the HTTP client's.
1. Answer with the CDN's status (`200`, `206`, or `416` for a range it cannot serve) and its `Content-Type`, `Content-Range`, `Accept-Ranges`, `Content-Encoding` and, when it frames the body, `Content-Length` (not with `Transfer-Encoding: chunked`: the header is not the body's length then, and uvicorn refuses a body shorter than it) plus the CORS headers; the body goes out undecoded in 64 KiB pieces without buffering the file. `HEAD` answers the status and headers with no body; the CDN's answer is closed before its bytes.
1. A body the CDN ends before the promised length (the `Content-Range` span, or a framing `Content-Length`; a transport error mid-body, or a clean end short of it) is resumed once with a `Range` request from the next byte, the request's other headers kept, continuing the same answer (`hls_proxy_file_resumed`); when the resume fails, answers from another byte, or ends short too, the transfer aborts (`FileCut` out of the body, one `hls_proxy_file_cut` warning with the CDN's domain, the bytes sent, the bytes expected and whether a resume ran) and the player asks again with a range of its own. An answer without a promised length ends where the CDN ends it.
1. When the CDN refuses the file with `403`, `404` or `410` (an expired URL), the hoster URL resolves once more past the resolver's cache (`StremioLinks.after_refusal`) and the file is fetched from the new URL; `502` when that fails too, when the hoster gives no video anymore, or when the CDN cannot be reached.
1. Each request is recorded on the HLS proxy's metrics with `kind="file"` and the answer's status, the bytes sent under `hls_proxy_bytes_total{kind="file"}`, an aborted transfer's included ([Observability](./observability.md)). Log lines name the CDN's domain only.

Players read a file in one connection, so the CDN's per-connection throttle applies (MixDrop 5.4 Mbit/s on one connection against 22 on three ranges from the Pi, DoodStream 3.8 and 19; `scripts/probes/hls_throughput.py --file`); a parallel-range read-ahead for files is a change of its own, decided by a title that needs it.

### Play

```http
GET  /api/v1/stremio/play/{stream_id}
HEAD /api/v1/stremio/play/{stream_id}
```

The URL of every resolved file stream (and of all streams without a resolver):

1. Look up `stream_id` in the stream link cache (`404` if missing; links are kept `stream_link_ttl_seconds`, 7 days, in the cache backend; the in-memory copy of the latest 4096 links answers without an age check until a restart clears it).
1. Take the stored video URL while it is fresh (resolved less than an hour ago: every working link of the measurement still played after 92 minutes), else resolve the hoster URL again (`StremioLinks`: concurrent requests for one link share one resolution, one tap on Android sent 11; the new video URL is saved).
1. Return a **302 redirect** to the video URL.
1. When the hoster gives no video (or only echoes the embed page), the stored video URL is still tried: its CDN may still serve it. **502** only without a stored video URL (never a redirect to an embed page).
1. `HEAD` answers like `GET`: a streaming server asks with `HEAD` first.

### Health

```http
GET /api/v1/stremio/health
```

Reports component status (`tmdb_configured`, `anime_ids_configured`, `stream_plugin_count`, `stream_plugins`, use case/resolver/link-cache flags), `supported_hosters` (resolver names from `HosterResolverRegistry.supported_hosters`), and under `metrics` the plugin and event-loop statistics of `/api/v1/stats/metrics` (`uptime_seconds`, `plugins`, `event_loop`). Returns `200` when healthy and `503` otherwise; healthy requires a title client (`tmdb_configured` is also `true` for the IMDB fallback client), both use cases, the resolver registry, the stream link cache, and at least one `stream` plugin.

---

## Title Matching

Title matching prevents false positives when plugin results include sequels, spin-offs, or unrelated titles.

| Feature | Details |
|---|---|
| Scoring | `max(token_sort_ratio, token_set_ratio) / 100` via `rapidfuzz` on normalised strings (lowercase, Unicode → ASCII, punctuation stripped) |
| Extra-words penalty | `-title_extra_words_penalty` (0.35) on the `token_set_ratio` when the result adds words to the reference: the set ratio rates "Dark Matter", "Dark Gathering" or "Naruto Shippuden" 1.0 against "Dark" or "Naruto". A result that only drops words ("Dune" for "Dune: Part One") is not penalised; one that adds words passes with a matching year (0.85) and fails without one (0.65) or with a wrong one (0.35). Release tags are no extra words: the guessit candidate carries the clean title |
| Year bonus | `+title_year_bonus` (0.2) if the result year is within tolerance |
| Year gate | A known result year outside the tolerance drops the result (reason `year`), whatever the text score; the year comes from the title, the release name, guessit or `metadata["year"]` (an int or a string of digits), and a year the reference title itself carries ("Blade Runner 2049") is a title word |
| Sequel penalty | `-title_sequel_penalty` (0.35) if the trailing sequel numbers differ (either side) |
| Threshold | `title_match_threshold` (0.7) minimum score |
| Year tolerance | Movies ±1 year, series ±3 years |
| Title candidates | Up to 4 deduplicated candidates: raw title, guessit title of `title`, guessit title of `release_name`, raw `release_name` |
| Reference titles | Primary (localised) title plus `alt_titles` (TMDB original title when it differs). Original titles are no search queries: a plugin is searched with the titles of its languages only (recall check in `docs/plans/pi-performance.md`) |
| IMDb id | A result whose metadata names `imdb` or `imdb_id` (`tt` plus digits, the zeros ignored) is kept (1.2) or dropped by the id alone (reason `imdb`), before any text is compared |
| Category | With the reference's kind known (`animation`: the Cinemeta genres name "Animation"), a result whose kind contradicts it is dropped (reason `category`): an anime label (5070) means animation, a series label (5000) with genres of its own means what the genres say (`names_anime` of `infrastructure/plugins/categories.py`, the words the plugins label 5070 by, so kinoking's One Piece anime page, 5000 with TMDB's genres, is the anime), and a result without genres, or an unknown kind, is decided by the text and year rules |
| Drop reasons | `title_match_filtered` logs `reason` (`score`, `year`, `imdb`, `category`); `title_match_summary` counts the drops per reason (`reasons`) |

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

### Quality and Size

A stream's quality comes from its release name (guessit) or the site's quality badge, its size from the site. The playback check every resolution passes ([hoster-resolvers.md](hoster-resolvers.md#playback-check)) also reads them from the stream itself, in the 4 KiB it fetches anyway: the largest `RESOLUTION` of an HLS master playlist and the total size of a file. A measured quality replaces the site's (`apply_resolution` in `application/stremio/stream_builder.py`): sites label a release once, a hoster can serve another copy. A measured size shows only where the site gave none (`1.4 GB`, `700 MB`; binary units, as the sites write them). When a measurement changes a quality, the answer is sorted again with the same weights, so a stream measured at 1080p ranks above an unmeasured one of the same language, and the name carries `1080p`. A media playlist (segments only), variants past the first 4 KiB and a server that answers without `Content-Range` measure nothing: the site's values stay. Cached answers take the measurement from the resolver's cache. With `stremio.verify_streams` off there is no check, so no measurement either, except for resolvers that check anyway (`needs_playback_check`).

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

## Anime Ids

Stremio's anime catalogs open a title as `kitsu:<id>` and an episode as `kitsu:<id>:<episode>`, the episode counted absolutely (One Piece's episode 1000), so an anime opened there never reached Scavengarr although four anime plugins exist. The manifest announces the `kitsu:` prefix (the MyAnimeList and AniList catalogs' `mal:` and `anilist:` ids are not accepted), and the stream use case translates such a request before anything else (`AnimeIdResolverPort`, implemented in `infrastructure/anime/`); the translated IMDb request then runs the usual path: titles, plugins, the search cache (shared with the same episode asked by its IMDb id), links.

| Step | Source | What it gives | Cache |
|---|---|---|---|
| 1 | The Anime Kitsu addon's meta (`/meta/<type>/kitsu:<id>.json`; `KitsuAddonClient`) | The title's IMDb id and, per episode, `imdbSeason` and `imdbEpisode`: the numbering Cinemeta and Torrentio use (a split cour's episode 3 is S3E15, One Piece's 1000 is S21E109); a video without them keeps its own season and number | 30 days per title (`anime_ids:addon:v1:<type>:<id>`); an episode newer than the cached record fetches once more |
| 2 | Fribb's anime-lists (`anime-list-full.json`; `AnimeIdLists`) | The IMDb id, TheTVDB season and episode offset: season = `season.tvdb` (else 1), episode = the Kitsu number + `episode_offset.tvdb`; no long-runners | 7 days (`anime_ids:lists:v1`, in memory and in the cache backend; reduced off the event loop); a failed download is tried again after an hour |
| 3 | nothing | `{"streams": []}`, no plugin searched | |

The content type comes from the source, because the catalogs list a movie as a series too: a `kitsu:` movie is searched as a movie (category 2000). The lookups cost one addon request per title (5 s timeout, one attempt) and one list download a week (60 s), nothing per episode. Log events: `anime_id_translated` (Kitsu id and episode, IMDb id, content type, season, episode, `source`: `addon` for IMDb's numbering, `kitsu` for the video's own, `lists`), `anime_id_lookup_failed` (`reason` `malformed` or `unmapped`, `addon_answered`), `anime_id_addon_failed` (`status` or `reason`), `anime_id_lists_failed` (`reason`) and `anime_id_lists_loaded` (`entries`). The phase `anime_ids` of `stremio_phase_seconds` times the translation (`found`, `not_found`), and `/health` reports `anime_ids_configured`.

Limits: the addon is a community service behind Cloudflare (it refuses httpx's default User-Agent and accepts Scavengarr's); while it is down, titles not in the cache fall back to the list, which places no long-runner. The German sites split some titles differently from TheTVDB (split cours as separate seasons, recap seasons): the episode filter drops the other seasons as for any series, so such an episode may answer nothing. `scripts/probes/anime_ids.py` shows what a `kitsu:` request maps to through every source, the resolver's translation and what aniworld and fireani find for it ([anime-ids-spike.md](../plans/anime-ids-spike.md)). The episode itself is placed on aniworld and s.to by the episode reference (Request Flow above): a `kitsu:` request's Kitsu number counts as the sites do, an IMDb request's reference counts Cinemeta's regular episodes instead, which is one short of the site at One Piece S22E4; aniworld corrects that by the title, s.to's anime rows carry no English title and place the neighbouring episode ([Series Episodes](../plans/series-episodes.md), the run of 2026-10-09). `scripts/probes/series_episodes.py` lists the reference and the placement per request (`series/kitsu:<id>:<episode>` ids are translated first).

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

### Plugin Search

| Setting | Default | Description |
|---|---|---|
| `max_concurrent_plugins` | 5 | httpx slots of the global concurrency pool |
| `max_concurrent_playwright` | 5 | Playwright slots of the global concurrency pool; also the most pages of the stealth browser |
| `max_results_per_plugin` | 100 | Per-plugin result limit in Stremio searches |
| `plugin_timeout_seconds` | 30 | Plugin search budget, counted from the request start (queueing for a slot included; a stale entry's refresh: from its own start); the answer does not wait for it |
| `plugin_health_interval_seconds` | 1800 | Health check of every Stremio plugin's site; searches skip the unreachable ones, which are checked again 5 minutes after the check that found them down, then at doubling pauses up to the interval (`0` = off) |
| `stream_deadline_seconds` | 60 | Latest answer, from the request start; resolution stops here |
| `max_concurrent_plugins_auto` | `true` | Auto-tune `max_concurrent_plugins` (superseded by `auto_tune_all`) |
| `auto_tune_all` | `true` | Auto-tune all concurrency parameters from container resources |

### Title Matching

| Setting | Default | Description |
|---|---|---|
| `title_match_threshold` | 0.7 | Minimum score to keep a result |
| `title_year_bonus` | 0.2 | Added when the year matches |
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
| `stremio.plugin_timeout_seconds` | 30 s | Each plugin search, from the moment the plugin holds a slot; also the request's answer budget, from the request start (a stale entry's refresh: from its own start): the request stops waiting for the search then | A plugin past its own timeout is cut (`stremio_plugin_timeout`) and counts for its breaker; one that returns after the answer budget is `late`, its results reach the cache |
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
| `stremio.plugin_health_interval_seconds` | 1800 s; an unreachable one again after 5 min, then 10, 20 and the interval (one check per interval for a dead site, back within one pause when it returns), the first check 60 s after the start; a site without an answer is tried again after 30 s | The plugin site checks | Unreachable plugins are skipped |
| `stremio.stream_link_ttl_seconds` | 7 days | Stored links of `/play` and the HLS proxy | Older links answer 404 once they have also left the in-memory set of the latest 4096 links (a restart clears it); a video URL older than 1 h (fixed) resolves again at playback |
| HLS proxy (fixed) | manifests cached 60 s and fetched within 15 s; segments within `http_timeout_seconds` | Manifest and segment requests | |
| SSRF guard (fixed) | DNS answers cached 60 s | Checked addresses | |

Scored plugin selection keys (`scoring_enabled`, `max_plugins_scored`, `exploration_probability`, …) are documented in [Plugin Scoring & Probing](./plugin-scoring-and-probing.md#configuration).

### Circuit Breaker

`PluginCircuitBreaker` is created in the composition root with hardcoded values (`failure_threshold=5`, `cooldown_seconds=60.0`, `max_cooldown_seconds=3600.0`); they are not configurable. The breaker tracks each plugin per requested category (`kinoking:2000` for its movies, `kinoking:5000` for its series): a site can be too slow for one content type only (kinoking's movie pages take 12–17 s to answer, its series pages 1 s), and its movies must not cost every movie request the search budget while its series keep coming. After 5 consecutive failures (exceptions or timeouts) a plugin is skipped for that category for 60 s (`stremio_plugin_circuit_open`). An answer without results neither counts nor resets the breaker: kinoking answers a search without hits at once, and those answers kept closing the breaker between its timeouts. After the cooldown a single probe request is allowed (half-open): concurrent requests skip the plugin until the probe reports, and a probe that never reports (cancelled, or a timeout that is not counted) is replaced by the next request after one more cooldown. Success resets the breaker and its cooldown, failure reopens it with twice the previous cooldown (60 s → 2 min → 4 min … capped at 1 h). Stremio requests are usually minutes apart, so a fixed 60 s cooldown would let an unreachable plugin cost almost every request its full timeout. A timeout counts as a failure when the plugin had at least half of `plugin_timeout_seconds`; a plugin cut by the search deadline after queueing for most of the budget is not blamed.

Hoster resolution has a breaker of its own, per resolver: a hoster whose resolutions keep timing out or giving unplayable streams is skipped the same way, except that its half-open probe runs to its end even when the request is cut ([Registry Features](hoster-resolvers.md#registry-features)).

Both breakers outlive a restart: the open ones go into the hoster state snapshot with their cooldown, and the start restores them with it shortened by the downtime; one whose cooldown ran out meanwhile comes back with its probe due, so a hoster that stays down keeps its doubled cooldown instead of costing five failures again. The snapshot also holds the resolver cache, so the first cached answers after a deploy go out with the resolutions of the run before ([State across restarts](hoster-resolvers.md#registry-features)).

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
| `tests/unit/application/test_stremio_stream.py` | Stream use case (the request flow, cached answers, mirror scores, telemetry) |
| `tests/unit/infrastructure/test_episode_index.py` | The episode index of the locating plugins (season page parser, cached index, the locate rules) |
| `tests/unit/application/test_title_resolution.py` | Title match, titles per language and the plugins' default languages (`TitleResolver`, mostly through the use case; factories in `stremio_support.py`) |
| `tests/unit/application/test_plugin_selection.py` | Stream plugins and scored selection (`PluginSelector`: top N, cold start, exploration slot, failing score store) |
| `tests/unit/application/test_title_search.py` | Search per title (`TitleSearch`: search cache, single-flight, one refresh at a time, plugin timeout, `aclose()`; the browser warm-up; through the use case) |
| `tests/unit/application/test_resolve_flow.py` | When streams resolve and the answer is due (`ResolveFlow`: per-hoster resolution, echoed URLs, answer rule, background resolutions; through the use case) |
| `tests/unit/application/test_answer.py` | The answer's streams (stream links and proxy URLs, failing link saves, measured quality and size, conversion in a worker thread) |
| `tests/unit/infrastructure/test_check_playable.py` | Playback check of resolved URLs (`verify_streams`) |
| `tests/unit/application/test_stremio_catalog.py` | Catalog use case |
| `tests/unit/application/test_plugin_search_runner.py` | `PluginSearchRunner` (fan-out, timeout, search deadline, circuit breaker, mirror groups) |
| `tests/unit/application/test_search_progress.py` | `SearchProgress` (results while the search runs) |
| `tests/unit/application/test_search_cache.py` | `SearchCache` (stale-while-revalidate) |
| `tests/unit/application/test_hoster_resolution.py` | `HosterResolution` (resolution while the search runs) |
| `tests/unit/application/test_stremio_links.py` | `StremioLinks` (resolving again, refused playlists, pinned copies, per-player links) |
| `tests/unit/infrastructure/test_plugin_health.py` | `PluginHealthMonitor` |
| `tests/unit/infrastructure/test_plugin_history.py` | `PluginHistory` (the plugins' long-term record: counters, day boundary, the 180-day window, restarts) |
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
| `tests/unit/domain/test_anime_ids_port.py` | `AnimeIdResolverPort`, `NO_ANIME_IDS` |
| `tests/unit/infrastructure/test_kitsu_addon_client.py` | `KitsuAddonClient` (the addon's meta reduced to a record and cached; refusals, timeouts) |
| `tests/unit/infrastructure/test_anime_id_lists.py` | `AnimeIdLists` (download, reduction, cache, hold-off after a failure) |
| `tests/unit/infrastructure/test_anime_id_resolver.py` | `KitsuAnimeIdResolver` (addon record, refetch, list fallback, nothing) |
| `tests/unit/infrastructure/test_cinemeta.py` | `CinemetaClient` (the catalog's meta reduced to a record and cached; unknown ids, failures) |
| `tests/unit/infrastructure/test_stream_link_cache.py` | Stream link cache repository (incl. HLS proxy fields) |
| `tests/unit/infrastructure/test_hls_proxy.py` | HLS manifest rewriting, CDN fetch, query resolution |
| `tests/unit/infrastructure/test_circuit_breaker.py` | `PluginCircuitBreaker` |
| `tests/unit/infrastructure/test_concurrency.py` | `ConcurrencyPool` + budgets |
| `tests/unit/interfaces/test_stremio_router.py` | Router endpoints |
| `tests/e2e/test_stremio_endpoint.py` | Full HTTP flow (manifest, catalog, stream, play, HLS proxy, health; `kitsu:` ids through the addon's meta, respx) |
| `tests/e2e/test_stremio_series_e2e.py` | Series season/episode filtering |
| `tests/e2e/test_stremio_streamable_e2e.py` | Streamable link verification |
| `tests/unit/infrastructure/test_stremio_playcheck_script.py` | `scripts/stremio_playcheck.py`, which fetches every stream of a running instance like a player (HLS to the first segments, files with a seek) |

---

## Source Code References

| Component | Path |
|---|---|
| Domain entities | `src/scavengarr/domain/entities/stremio.py` |
| TMDB port | `src/scavengarr/domain/ports/tmdb.py` |
| Anime id port | `src/scavengarr/domain/ports/anime_ids.py` |
| Anime ids (Kitsu addon client, id list, resolver) | `src/scavengarr/infrastructure/anime/` |
| Series meta (Cinemeta client) | `src/scavengarr/infrastructure/stremio/cinemeta.py` |
| Episode index (the locating plugins' season pages) | `src/scavengarr/infrastructure/plugins/episode_index.py` |
| Stream link port | `src/scavengarr/domain/ports/stream_link_repository.py` |
| Concurrency port | `src/scavengarr/domain/ports/concurrency.py` |
| Stream use case | `src/scavengarr/application/use_cases/stremio_stream.py` |
| Catalog use case | `src/scavengarr/application/use_cases/stremio_catalog.py` |
| Title resolution | `src/scavengarr/application/stremio/title_resolution.py` |
| Plugin selection | `src/scavengarr/application/stremio/plugin_selection.py` |
| Title search (single-flight, search cache) | `src/scavengarr/application/stremio/title_search.py` |
| Plugin search runner | `src/scavengarr/application/stremio/plugin_search.py` |
| Search progress | `src/scavengarr/application/stremio/search_progress.py` |
| Search cache | `src/scavengarr/application/stremio/search_cache.py` |
| Hoster resolution, answer rule | `src/scavengarr/application/stremio/resolution.py` |
| Answer (ranking, measured quality, stream links, proxy URLs) | `src/scavengarr/application/stremio/answer.py` |
| Stream links (`/play`, HLS proxy) | `src/scavengarr/application/use_cases/stremio_links.py` |
| Plugin health monitor | `src/scavengarr/infrastructure/plugins/health_monitor.py` |
| Plugin long-term record | `src/scavengarr/infrastructure/plugins/history.py` ([Observability](./observability.md#plugin-record)) |
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
