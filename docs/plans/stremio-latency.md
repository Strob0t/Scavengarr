[← Back to Index](../features/README.md)

# Plan: Stremio Response Time and Playable Streams

**Status:** Done (2026-09-29). AIOStreams deferred by decision: Scavengarr is added to Stremio directly; the [AIOStreams](#aiostreams) notes stay for later.
**Priority:** High (Stremio is the main use case; cold answers take 17–38 s)
**Related:** `src/scavengarr/application/use_cases/stremio_stream.py`, `application/stremio/plugin_search.py`, `infrastructure/circuit_breaker.py`, `infrastructure/hoster_resolvers/`, `data/config.yaml`

## Problem

Stremio asks an addon once per title and shows what arrives before its timeout. Measured with a Stremio-style harness (18 titles: new releases, German movies/series, popular titles, anime; every returned stream actually played, i.e. first video bytes or HLS playlist + first segment), production config (`plugin_timeout_seconds: 15`), dev container network, 2026-09-29:

| Metric | Cold | Warm (same titles again) |
|---|---|---|
| Response time median / p90 / max | 19.8 / 31.2 / 38.5 s | ~17 / 22 / 24 s |
| Streams / playable | 80 / 61 (76 %) | similar |
| Titles with a playable stream | 17 / 18 | 17 / 18 |

Causes found:

1. **No overall deadline.** The plugin phase waits up to `plugin_timeout_seconds`; the resolve phase (hoster embed → video URL) runs afterwards without any time limit, so one slow hoster (browser capture, slow CDN) delays the whole answer. `stremio.stremio_deadline_ms` exists in the schema but nothing reads it.
2. **Unreachable plugins cost every request the full plugin timeout.** cineby, megakino_to and movie4k timed out on 8 of 8 movie requests with 0 results (their hosts are unreachable from this network). The circuit breaker opens after 5 failures but only for 60 s; Stremio requests are minutes apart, so it is half-open again for almost every request.
3. **Deduplication before resolution drops working streams.** `deduplicate_by_hoster` keeps only the best-ranked stream per hoster *before* resolving. When that one fails to resolve, the hoster disappears although other streams of it would work. Search results after title filtering (e.g. Reacher 40, Attack on Titan 28) end as 3–7 streams.
4. **Resolved but unplayable URLs are returned.** Dropload 0/11 (CDN 502), SuperVideo 0/4 (HTML instead of video): the resolver found a URL, nobody checked it plays.

## Design

1. **`stremio.stream_deadline_seconds`** (new, default 15.0): overall budget per stream request, measured from the request start. The resolve phase stops at the deadline (at least `_MIN_RESOLVE_WINDOW_S` = 2 s after the plugin phase) and returns what is resolved; unfinished resolutions are cancelled. `plugin_timeout_seconds` stays the plugin cap; defaults/recommended config keep plugin timeout < deadline so resolution gets a window. The unused `stremio_deadline_ms` stays accepted (compatibility) but is documented as unused.
2. **Circuit breaker backoff**: the cooldown doubles with every failed half-open trial (60 s → 2 min → … capped at 1 h) and resets on success.
3. **Deduplicate after resolution**: resolve the top `max_probe_count` candidates in rank order, then keep the first *playable* stream per hoster.
4. **Playability check** (`stremio.verify_streams`, default true): a resolved URL is fetched with `Range: bytes=0-0` (HLS: the playlist) using its playback headers, within the deadline; 4xx/5xx or an HTML answer drops the stream.

## Tests

Unit tests per change (use case with fake resolver/clock, circuit breaker backoff, verification with respx). Re-run the harness cold + warm; acceptance: median ≤ 12 s, max ≤ 16 s (Scavengarr alone), playable rate ≥ 90 %, no title loses its last playable stream compared to the baseline.

## Documentation

`docs/features/stremio-addon.md`, `docs/features/configuration.md`, `CHANGELOG.md`, `data/config.yaml` comments, results recorded here.

## Results

Implemented as designed, plus three changes the measurements forced:

- **Search budget from the request start.** Each title runs two query variants over all plugins, so plugins queue for concurrency slots, and a plugin's timeout started only when it got a slot: with `plugin_timeout_seconds: 15` the search alone took up to 35 s. `plugin_timeout_seconds` now ends the search that long after the request start (default 10 s, was 30 s per plugin).
- **Per-hoster resolution in rank order** instead of resolving all `max_probe_count` candidates at once and deduplicating afterwards. The burst (dozens of new connections to distinct CDNs within a second, plus the playback checks) made the network path from the dev container block new connections for 30–60 s ("No route to host", intermittent, every run 2–2.5 min in); the baseline never triggered it.
- **Link validator backoff.** An unreachable host was skipped for a flat 15 min, so one such network blip removed VOE, vinovo, kinoger, fsst, … from every request of the next 15 minutes. Now 60 s, doubling per further failure up to 15 min.

Final run (G: code of this plan, `plugin_timeout_seconds: 10`, `stream_deadline_seconds: 15`, `verify_streams: true`, cold cache, the four title groups with 3 min pauses so the network path stays clean):

| Metric | Baseline (cold) | Final (cold) | Variant 12 s / 17 s |
|---|---|---|---|
| Response median / p90 / max | 19.8 / 31.2 / 38.5 s | 15.0 / 15.1 / 15.1 s | 16.5 / 17.1 / 17.1 s |
| Returned streams / playable | 80 / 61 (76 %) | 46 / 44 (96 %) | 46 / 44 (96 %) |
| Titles with a playable stream | 17 / 18 | 17 / 18 | 17 / 18 |

- Acceptance: max ≤ 16 s and playable ≥ 90 % met; no title lost its last playable stream (the one title without a stream, *Der Schuh des Manitu*, had none in the baseline either: the set's id tt0248667 is *Ali* (2001); the film is tt0248408, see the follow-up). Median 15.0 s instead of ≤ 12 s: resolution uses the budget up to the deadline for most titles.
- Trade-off: fewer playable streams per title (44 vs 61). The extra baseline streams came mostly from kinoking (needs 6–14 s, often cut at 10 s). A 12 s search / 17 s answer gave exactly the same result at +2 s, so 10 / 15 s is the recommended setting (new defaults, `data/config.yaml`).
- Unreachable plugins from this network (cineby, megakino_to, movie4k) still start every request until their circuit breaker opens; with the backoff they stay closed for up to 1 h.

## Follow-up 2026-10-01 (live test with Stremio)

Same harness and title set (17 titles: German films, popular films, series, anime; `scripts/stremio_measure.py --pause 150` against a running server), `plugin_timeout_seconds: 10`, `stream_deadline_seconds: 15`, cold, dev container, groups 150 s apart. Each run after the commits named; runs differ by site state (s.to's link-out gate, kinoger's Cloudflare, moflix and kinoking timeouts), so single titles move by several seconds between runs.

| Run | Change | Median / max | Streams | Titles without stream |
|---|---|---|---|---|
| 1 | baseline of the day | 14.9 / 16.4 s | 44 | 5 / 17 |
| 2 | relevant search hits only, s.to link-outs, hoster+language dedup | 15.1 / 21.5 s | 79 | 0 / 17 |
| 3 | stream links saved only for proxied streams (Dune: 21.5 → 15.0 s) | 15.0 / 15.1 s | 85 | 0 / 17 |
| 5 | circuit breaker per category, s.to search parser, aniworld, language labels, moflix HLS | 15.0 / 15.1 s | 97 | 0 / 17 |
| 6 | mixdrop resolver, player User-Agent for checks and proxy | 15.0 / 15.1 s | 100 | 0 / 17 |
| 7 | `resolve_grace_seconds: 3` | 13.8 / 15.0 s | 81 | 0 / 17 |
| 8 | `resolve_grace_seconds: 4` | 14.8 / 15.0 s | 78 | 0 / 17 |
| 9 | title filter extra-words penalty, kinoger episodes, guessit 4, one devideosrc fetch per title; the right id for *Der Schuh des Manitu* | 14.6 / 15.6 s | 82 | 1 / 17 |
| 10 | exact hit only for episode requests, mirror group (hdfilme for streamcloud/streamkiste), movie2k series and relevance, filmpalast relevance | 14.6 / 15.1 s | 83 | 0 / 17 |
| 11 | kinoger films and single-player pages, resolvers for gxplayer, kinoger.pw (Vidara) and fsst streams | 14.4 / 15.0 s | 105 | 2 / 17 |
| 12 | HLS proxy for CDN-root URIs (Vidsonic), moflix without its paid player, kinoger behind its new WAF page | 14.6 / 15.0 s | 90 | 1 / 17 |

- The search phase decides the answer time: it ends 10 s after the request whenever one plugin still runs, which happened on most requests (kinoking's 12–17 s movie pages until its breaker opened, dead hosts in half-open probes, kinoger's Cloudflare solve, moflix). With the search done early (kinoking's movie breaker open), Inception and Interstellar were answered after 7–8 s instead of 15 s.
- The resolve grace cuts the browser-resolved stragglers (DoodStream mirrors, Byse/Filemoon, Dropload's captcha) once a stream is there; it costs streams mostly on anime (aniworld 36 → 22–24 in runs 7/8), where those hosters carry sub variants. 4 s keeps the DoodStream mirrors of the measured requests; set 0 to wait until the deadline.
- The set's id for *Der Schuh des Manitu* was wrong (tt0248667 is *Ali*, 2001), so runs 1–8 measured *Ali*; tt0248408 gets 4 streams (filmpalast, hdfilme, movie2k). A title-filter audit on the plugins' answers for the 17 titles found 22 results of other titles that passed the filter ("Dark Matter" for "Dark"); fixed with the extra-words penalty (`title_extra_words_penalty`), so later runs serve fewer, right streams.
- Run 9 serves fewer wrong titles (the extra-words penalty dropped e.g. "Dark Matter" for "Dark"), so its stream count is not comparable one to one. Its title without a stream was Dark S01E01: s.to started 3 s late (every plugin slot busy), scraped "Dark", "Dark Matter" and "Dark Winds", and was cut by the search deadline. Episode requests now scrape an exact hit alone; afterwards Dark S01E01 was answered with s.to's stream in 10.8 s, and kinoger served S01E05 and S04E01 instead of S01E01.
- Run 10: every title has streams again (Dark S01E01: s.to's stream after 10.7 s). movie2k serves series now (9 streams instead of 5, One Piece S01E01 among them) and s.to 5 instead of 3. hdfilme answers for its mirror group (9 streams); streamcloud and streamkiste are skipped. The median stays at 14.6 s: the search still ends 10 s after the request whenever one plugin keeps running.
- Run 11: kinoger serves 18 streams instead of 1 (films again, fsst and kinoger.pw streams, single-player pages) and megakino 7 instead of 4 (gxplayer). The two titles without a stream are site state: *Good Bye, Lenin!* has only hdfilme's DoodStream link, whose browser resolve was unfinished at the deadline (0 streams in earlier runs too), and s.to needed more than the 10 s search budget for Dark S01E01 (as in run 9).
- Run 12 (2026-10-03, after the end-to-end test below): kinoger had returned nothing that day (its new WAF page, read as the search result) and serves 14 streams again; moflix 5 instead of 10, the dropped five were its paid player that no player could play. Dark S01E01 again without a stream: s.to's link-outs were gated and the browser pass ran past the search budget.
- An earlier search end (a soft deadline once most plugins answered, resolving while plugins still search, or a lower `plugin_timeout_seconds`) is the next lever for the median; each trades streams of slow sites for time. **Decision 2026-10-01: no**, the 10 s search budget stays for completeness.

## End-to-end test with Stremio Web (2026-10-03)

Production (`https://scavengarr.lan`, v0.2.0) as addon in the maintainer's Stremio Web (`https://stremio.lan`), driven with Playwright; then every stream followed to its media bytes with `scripts/stremio_playcheck.py`, the way a player fetches it.

| Step | Result |
|---|---|
| Install the addon | OK (manifest v0.2.0) |
| Search | OK: rows "Scavengarr Movies" and "Scavengarr Series" |
| Stream list (film, episode) | OK: names, languages, sources, `bingeGroup` |
| Playback in Stremio Web | **blocked**: stremio.lan's streaming server answers `/settings`, `/proxy/…` and the other server routes with nginx 400 "Request Header Or Cookie Too Large" even for a bare curl request, `/hlsv2/probe` with 502. Stremio Web plays `notWebReady` streams only through that server, so every stream ends in "Video is not supported" (code 83). A 400 for tiny requests points at a proxy loop: in tsaridas/stremio-docker nginx proxies these routes to `127.0.0.1:11470` (`server.js`); with its listen port also 11470 (e.g. `WEBUI_INTERNAL_PORT=11470`) nginx forwards to itself |

Play check (master, variant, first segments; file start and a seek):

| Instance, checked from | Playable | Failures |
|---|---|---|
| dev with the fixes, same machine | 79 of 81 | firestream segments 502, vidmoly variant 504 (CDN errors at that moment) |
| production, from the home network | 41 of 72 | DoodStream 15 (`200 error_wrong_ip`), Vinovo 8 (403), Vidsonic 4 (proxy bug), moflix paid player 2 (variant 403), vidmoly 2 |

- Fixed on `staging`: Vidsonic's variant comes from the CDN root and the HLS proxy left that URI to the player (404); moflix's "Premium (No Ads)" video is its paid player (variants 403 without a paid session); kinoger's new WAF page ("Verification...") was read as the search result (0 hits on every search); kinox ran its search and detail pages for episode requests it cannot answer.
- **IP-bound streams:** production reaches the sites through a VPN (the stream tokens name its exit address, AS43350 NForce), not through the home network's address. DoodStream and Vinovo bind their stream URLs to the resolving IP, so a player on another IP gets `error_wrong_ip` or 403. Streams through Scavengarr's HLS proxy are fetched from Scavengarr's IP and are not affected; VEEV, Playmate and FireStream played from the other IP.
- Production only: kinoger returned nothing (the WAF page; the fix is on `staging`), and s.to gave no episode streams while dev did.

### Second round (2026-10-03 evening, production v0.2.2)

stremio.lan's server loop was the maintainer's setup. The Stremio container shares the VPN container's network, where 8080 (qBittorrent) and 8090 (TorrServer) are taken, so nginx had been moved to the server's own port 11470; `WEBUI_INTERNAL_PORT=8095` with Caddy on 8095 fixed it. Playback in Stremio Web, *The Matrix* (9 streams):

| Stream | Result | Cause |
|---|---|---|
| FireStream (filmpalast), FSST (kinoger) | plays | |
| DoodStream (hdfilme), Vinovo (movie2k) | plays | IP-bound, but Stremio's server uses the same VPN as Scavengarr |
| Vidsonic (filmpalast), VOE (megakino), StreamUp (moflix) | error 83 | HLS proxy: Stremio Web's `HEAD` content-type check got 405 without CORS headers (fixed on `staging`) |
| Playmate (filmpalast) | error 81 | tsaridas/stremio-docker's nginx answers the disguised segments (`…_000.css`, `…_001.js`) as web player files: 404 |
| VEEV (moflix) | error 83 | CDN 403 through the server's `/proxy` at play time; `HEAD`/`GET` through the same proxy answered 200/206 minutes later |

- Every `/hlsv2/probe` answered 500: the server, in the VPN container's network, resolves no `*.lan` name and the VPN firewall blocks the LAN. It probes `http://127.0.0.1:11470/…` and internet URLs fine. Streams the browser can play directly still play (Stremio Web falls back to a `HEAD` content-type check); transcoding cannot work until `extra_hosts` and `FIREWALL_OUTBOUND_SUBNETS` are set on the VPN container.
- Production answered in 32–39 s (4 requests) instead of about 15 s. Its `config.yaml` was still the first seed (cineby, disabled since 2026-10-01, was searching); kinoger averaged 24.5 s with 8 of 9 searches failed, moflix 21 s with 5 of 9.

### Third round (2026-10-04, production on `staging`)

After the config reseed production answers in 10.5–15.4 s. Measured with a script that polls `/api/v1/stats/metrics` during one stream request (when each plugin's search ends), logs and probes read through Portainer (exec into the container):

| Request (warm) | Streams | Time | s.to |
|---|---|---|---|
| Inception | 5 (kinoger, filmpalast, movie2k, moflix ×2) | 15.3 s | — |
| Interstellar | 8 (kinoger ×2, filmpalast ×2, megakino ×2, movie2k, moflix) | 15.2 s | — |
| The Godfather | 4 (kinoger, movie2k ×2, einschalten) | 15.2 s | — |
| Dark S02E01 | 1 (VOE · kinoking) | 10.5 s | link-out resolved (302), VOE ranked below kinoking's |
| Breaking Bad S02E01 | 3 (kinoking, kinoger ×2) | 15.1 s | link-out resolved (302) |

- **kinoger, moflix**: both challenge the VPN IP. httpx takes over the browser's session after one solve (Phase 4 of `antibot-patchright.md`); the first request after a restart misses them (the solve takes 10–20 s on the Pi and finishes in the background), later ones get them in 4–10 s. moflix returned nothing until its API, loaded directly by the browser with a stored clearance, answered 401 without the site's Referer; it is now asked by in-page `fetch()`.
- **s.to**: three causes, one after the other. The ad layers took the clicks on the link box and the gate's checkbox; the gate's Turnstile widget took 16–70 s on the Pi; and for the VPN IP the gate is the tier `turnstile_altcha`, whose ALTCHA widget (required checkbox) blocked the form. With all three fixed the gate passes in about 20 s in the background. A pass unlocks exactly 3 link-outs for this IP (2 passes observed: 3 redirects each, then the gate again), so Stremio gets s.to for about three episode requests per pass. One stream per hoster is resolved, so an s.to VOE link loses to a better-ranked VOE stream of another site.
- **Torznab on s.to** with the VPN IP: a search for "Dark" took 83 s and sent about 950 link-out requests (all matching series, every episode and hoster); 97 of 100 results kept the s.to link-out because the gate allowed 3. Since 2026-10-04 whole seasons do not pass the gate, and after a gated episode the next episodes return their link-outs unresolved for 5 min (`_GATE_RETRY_S`) without requesting them.
- **Play check** (`scripts/stremio_playcheck.py` from the home network): 6 of 9 streams playable, the HLS-proxy streams (VOE, Vidsonic, StreamUp) included; `HEAD` on the proxy answers 200 with CORS. The 3 failures are IP-bound (DoodStream `error_wrong_ip`, Vinovo 403, FSST 410) and play through Stremio's server in the same VPN.
- megakino_to and movie4k: every domain answers Cloudflare 522 (origin down). kinoking is often cut at 10 s.

### Fourth round (2026-10-04 evening, production on `staging` 53f2f88)

The measurement harness (`scripts/stremio_measure.py --pause 30`, the 17 titles) against `https://scavengarr.lan` after the performance plan (`docs/plans/pi-performance.md`), HTTP/2 on, no other traffic. Pass 1 searched (the titles' search-cache entries were older than the cache); pass 2 ran 8–13 minutes later and answered from the search cache, late plugins' results included. Logs and probes through Portainer (exec into the container: the same VPN address, DNS and code).

| Pass | Median / max | Streams | Titles without stream |
|---|---|---|---|
| 1 (plugins search) | 12.4 / 15.3 s | 48 | 5 / 17 |
| 2 (from the search cache) | 4.6 / 10.4 s | 64 | 3 / 17 |

- Streams per plugin, pass 1 → 2: aniworld 18 → 20, filmpalast 5 → 11, megakino 9 → 9, movie2k 5 → 7, moflix 6 → 6, s.to 0 → 6, fireani 3 → 3, hdfilme 2 → 2.
- Series: pass 1 had streams for 1 of 5 (The Last of Us), pass 2 for 3 of 5 (Dark: s.to; Stranger Things S04E01: filmpalast, s.to; The Last of Us: filmpalast, movie2k, s.to). s.to's gate pass (about 20 s on the Pi) does not fit a first request; its results reach the next one through the late plugins.

**Fixed on `staging`** (TDD, deployed with the next build):

| Finding | Evidence | Fix |
|---|---|---|
| The circuit breaker counted the early answer's cut (7 s) against plugins that run on | kinox (movies) opened after cuts it answered late without hits | A late plugin fails only when it is still running at the end of its extra time (fb3ad00) |
| Browser captures that never deliver from the VPN address | 50 DoodStream and Dropload captures in an hour, no stream. Alone in the container: DoodStream's Turnstile unsolved after 31–34 s, Dropload's captcha player without a stream after 19 s. The final measurement's pass from the search cache still cost 10.1 s of Chromium CPU per request, all of it hoster resolution | Circuit breaker per hoster resolver: timeouts, cuts after half the resolve timeout and unplayable streams count (fa2e58f) |
| moflix's own players failed 15 of 15 | moflix-stream.click (VidHide) answers `/e/<id>` with 404, its player is under `/embed/<id>`; moflix-stream.link is a Byse (Filemoon) player | XFS asks the link's own URL after a 404 on `/e/`; Filemoon claims moflix-stream.link: 4.7 s (warm browser) and 15 s (cold) in production (7600a06) |
| moflix asked the title API for people | 19 of 20 hits for "Oppenheimer" are people: 46 answers 404; a person's id fetched an unrelated title | Titles only, relevant hits only (e9b1ba1) |

**Site and environment state** (no code change):
- **mixdrop:** its CDN `*.mxcontent.net` does not resolve in production. gluetun's DNS answers REFUSED, while 1.1.1.1 and 9.9.9.9 answer 168.80.32.64 through the same tunnel. The name is on no block list, but gluetun's malicious IP list (`BLOCK_MALICIOUS`, on by default) holds 168.80.0.0/15, and `DNS_UNBLOCK_HOSTNAMES` does not lift address blocks (gluetun applies it to its hostname list only). In 90 minutes 10 mixdrop resolutions gave an unplayable stream (its CDN unreachable) and 12 a player without a stream URL (`mixdrop_file_offline`). Chosen for production (2026-10-05): every lookup of the VPN stack stays in the tunnel (gluetun's own DNS server, DNS over TLS), with `BLOCK_MALICIOUS=off`. Handing gluetun's DNS to the LAN's Pi-hole, tried first, sends the lookups outside the tunnel; the VPN container was unhealthy with it (cause not confirmed), and the stack that shares its network went down.
- **VOE:** 4 of 18 VOE links failed: the files answer "File access denied" (restricted by the uploader), on `/e/` as well.
- **kinoger:** Cloudflare's Turnstile is not solved from the VPN address (`cloudflare_unsolved` after 33 s), so kinoger gives nothing in production; its breaker opens, and each half-open probe costs a browser solve.
- **kinoking:** answers a series search in 1.2 s when run alone in the container, but stalled for more than 17 s in pass 1 without an answer. Its server serializes requests behind a slow movie page: after a cancelled movie page (12–17 s to build) the next search took 10.3 s instead of 0.3 s, over HTTP/1.1 and HTTP/2 alike.
- **megakino_to, movie4k:** Cloudflare 522 (origin down); their breakers keep them out. **kinox.to** answered 503 (13 retries in 70 min).
- The titles still without a stream after pass 2 are site state: *Good Bye, Lenin!* and Breaking Bad S01E01 have only DoodStream, Dropload and access-denied VOE links; Haus des Geldes S01E01 only a DoodStream link.
- `SCAVENGARR_HTTP_HTTP2` was still on (the A/B in `pi-performance.md`: +22% CPU, not faster).

**Play check** (`scripts/stremio_playcheck.py` inside the container, so from the VPN address like Stremio's server; the 17 titles once more, served from the search cache while it refreshed): 61 of 64 streams playable. The failures were CDN errors at that moment: moflix's FireStream and StreamUp segments 502 (the CDN did not answer the HLS proxy within 15 s), one fireani VOE segment without media bytes. MP4 streams answered in 0.45 s (median, seek included). HLS streams took 4.3 s to master, variant and two segment heads (median), but 15 of 56 took 15 s or more (aniworld's Vidmoly and VOE up to 46 s, a fireani VOE 84 s). Those times are mostly the CDNs answering through the VPN: Scavengarr's HLS proxy answered playlists in 0.6 s and segment heads in 0.23 s (median, its access log).

The production logs of that evening showed four more resolver defects (FireStream ids with `-`, Playmate `/embed/` links, veev's redirect to another file code, moflix-stream.click's packed `hls2` URL), all fixed on `staging`; kinox's link-outs now sit behind an image captcha, and VOE denies some files to the VPN address. Details and the next options: `optimization-options.md`; the fifth round below measured the fixes.

### Fifth round (2026-10-05, production on `staging` c4a6b6c)

The harness and the 17 titles of the fourth round, after its fixes were deployed (each checked in the container). Changed environment: HTTP/2 off (`SCAVENGARR_HTTP_HTTP2=false`), every lookup of the VPN stack through gluetun's own DNS server over TLS with `BLOCK_MALICIOUS=off` (`stremio-addon.md`), and another exit address of the VPN (the same provider network, AS43350). A first attempt right after the stack's restart was dropped (load average 4.4, warm-up without streams); the round ran at a load below 1.2. Pass 1 searched (the titles' search-cache entries were deleted first), pass 2 followed at once from the search cache.

| Pass | Median / max | Streams | Titles without stream |
|---|---|---|---|
| 1 (plugins search) | 11.1 / 14.0 s (fourth round 12.4 / 15.3 s) | 102 (48) | 0 / 17 (5 / 17) |
| 2 (from the search cache) | 1.0 / 4.4 s (4.6 / 10.4 s) | 110 (64) | 0 / 17 (3 / 17) |

- **Pass 1 phases** (from the log): the search ended at the soft deadline (7.0 s) in all 17 requests. megakino_to, whose site is down, was still running in every one, movie4k in 12; a plugin that delivers (kinoking, kinoger, s.to, hdfilme, fireani) in 13. The first stream came 1.2 s after the search (median of the 12 requests that resolved a new link; fourth round 1.4 s), the resolution took 4.1 s (median; 5.8 s).
- **Streams per plugin**, pass 1 → 2: aniworld 27 → 27, filmpalast 15 → 15, kinoger 11 → 18, hdfilme 11 → 10, movie2k 10 → 10, moflix 10 → 10, megakino 6 → 6, einschalten 4 → 4, kinoking 3 → 3, fireani 3 → 3, s.to 2 → 4. kinoger, einschalten and moflix's own players delivered nothing in the fourth round.
- **Late plugins** added results to 8 of the 17 cache entries (kinoking for movies, kinoger and s.to for series): pass 2's extra streams.
- **Pass 2** answered in 0.0–1.8 s, except 5 requests at 4.1–4.4 s: a link resolved for the first time or again (a VOE link, a Filemoon half-open probe) held the answer for `resolve_grace_seconds`.

**Hosters** (both passes):
- **Circuit breaker open:** Dropload (since the warm-up; 28 skipped links) and Filemoon (24). No Filemoon or Byse capture finished inside the grace. Production's `http.timeout_resolve_seconds` is 10 s (`data/config.yaml`), so a cut counts as a failure from 5 s on: two Byse captures cut at 6.0 s in one request completed the five failures (the others came from requests before the round). moflix-stream.link's Byse player (7600a06) therefore delivers nothing on the Pi. A half-open probe is cut by the grace before 5 s, reports nothing and runs again after each cooldown, which never doubles; in the CPU measurement below one probe cost 6.3 s of Chromium.
- **DoodStream** resolves from the new exit: 9 streams (dood 2, playmogo 7), 4 offline files, 2 unplayable. **mixdrop** resolves again (its CDN's address is no longer blocked): 1 stream, 5 offline files.
- **VOE:** 18 resolved, 12 failed (7 with no working method, among them the files denied in the fourth round; 5 dead files).
- **SuperVideo:** 13 of 13 failed. Its CDN (serversicuro.cc) answers the playlist URL with a "Loading..." page whose script redirects to the same URL with a `js` token (and sets a `sid` cookie), and a HEAD with a 302 to an ad domain whose connection fails; the resolver's HEAD check logs `supervideo_video_verify_error`. Following the redirect without a browser led to another CDN host (302); further steps are untested, because the CDN then answered the probes with 429.
- veev: 1 resolved, 3 offline files. Vidmoly: 12 resolved.

**Plugins without results:** megakino_to and movie4k (Cloudflare 522) and kinox (503 retries, the captcha gate before its links) gave 0 results in 36, 23 and 36 searches (`/api/v1/stats/metrics`), on average 15.0, 15.0 and 3.8 s per search; kinox sent 6–14 requests per search. megakino_to's breaker stayed closed (4 failures): its searches end empty after the plugin's own fetch timeouts, and an empty answer neither counts nor resets. kinoking took 8.5 s on average, and 10 of its 36 searches did not finish; its breaker for movies was open at the end of the round.

**Play check** (inside the container): 107 of 110 streams playable. The failures were CDN errors: segments answered 502 for one moflix FireStream and two StreamUp streams (kinoger, moflix). MP4 streams answered in 0.40 s (median of 42), HLS streams in 2.2 s (median of 68; fourth round 4.3 s); 4 HLS streams took 15 s or more (fourth round 15 of 56).

**CPU per request** (`scripts/stremio_profile.py --py-spy`, the three titles of `pi-performance.md`, two passes with their search-cache entries deleted before each, as in its final measurement):

| | Final measurement of `pi-performance.md` | Fifth round |
|---|---|---|
| Wall | 11.3 s | 8.0 s |
| Python CPU | 2.55 s | 2.47 s |
| Chromium CPU | 11.3 s | 6.7 s (13.3 s in the first pass, 0 s in the second, whose links all came from the resolution cache) |
| Python on the GIL (py-spy) | 1.13 s | 1.26 s |
| Event-loop lag p99 / max | 40 / 60 ms | 73 / 224 ms |
| httpx requests | 53 | 49 |
| Streams | 4.7 | 6.7 |
| From the search cache: wall, Python, Chromium | 4.2 s, 0.9 s, 10.1 s | 1.4 s, 0.22 s, 2.6 s (one Filemoon probe 6.3 s, the other answers 0–1.6 s) |

- `html.parser` took 34% of the GIL samples (0.43 s per request; 0.20 s in the final measurement), almost all of it in worker threads (`_feed()`); 0.8% ran on the event loop. More working plugins parse more pages.
- A single pass right after the play check measured more (4.2 s Python, 16.2 s Chromium per request, three requests); the repeat above follows the final measurement's method.

**Evaluation:** the fixes doubled the streams of a first request, left no title without a stream, cut the cached answer from 4.6 to 1.0 s and the Chromium CPU per request by 40%. A first answer is now held by the structure: the soft deadline (7 s, reached in every request) and the resolve grace (4 s). The options that follow from it: `optimization-options.md`.

### Dev-server A/B round (2026-10-05, after the round-5 measures)

The measures of `round5-measures.md` against the fifth round's code, side by side in the dev container (x86, 16 cores, the home connection without the VPN, so absolute times are not the Pi's): each server on 127.0.0.1 under Xvfb with a fresh cache and its commit's `data/config.yaml`. The old code is b003bd2 (plugin timeout 10 s, soft deadline 7 s, grace 4 s, every link resolved), the new one `staging` at 661104b (the fixes below came out of this round). The fifth round's harness and titles (`stremio_measure.py`, 90 s between groups; play check with `stremio_playcheck.py`'s fetches on the stored stream objects), plus the server's CPU from `/proc` (its process, and its child processes for Chromium) and the event-loop lag from `/api/v1/stats/metrics`. Run 2 is a second fresh instance an hour after run 1; kinoking, down during run 1, was back.

| | Old code | `staging` run 1 | `staging` run 2 |
|---|---|---|---|
| Pass 1: median / max | 11.3 / 11.8 s | 6.0 / 30.0 s | 4.6 / 24.8 s |
| Pass 1: streams; titles without; below 5 | 103; 1; 4 | 70; 0; 4 | 73; 0; 4 |
| Pass 1: CPU of Python / Chromium (17 titles) | 13.5 / 53.6 s | 10.1 / 49.0 s | 10.9 / 46.2 s |
| Pass 1: event-loop lag p99 / max | 6 / 11 ms | 2 / 3 ms | 2 / 3 ms |
| Pass 2 (search cache): median / max | 4.0 / 10.0 s | 0.03 / 0.96 s | 0.03 / 0.55 s |
| Play check of pass 1's streams | 99 of 103 | 64 of 70 | 72 of 73 |

- **Answer** (measures 2 and 9): in run 2, 13 first answers went out at the target of 5 streams (2.3–19.5 s, median 4.3 s), 4 when the search was done (3.4–24.8 s). The titles with few streams (Good Bye Lenin, Breaking Bad, Dark, Haus des Geldes) got as many streams as with the old code, one more for Good Bye Lenin, but waited for the slowest plugin. 22 more titles traced on demand (17:10–17:35) show it: kinoking ran into the 30 s plugin timeout in the 8 films it searched (then its breaker skipped it; series: hits after 1–8 s in 5 of 12), kinoger answered after 17–29 s or was cut, and its streams completed the target of 4 series at 23–30 s. Cuts after half the plugin timeout open a plugin's breaker; kinoking's opened for films and series. Fewer streams per answer (70–73 against 103) are the target rule's intent.
- **Cached answers** (measure 4): 0.03 s instead of 4.0 s. They lost a hoster in 3 of 17 titles (fixed, below); after the fix they had the 73 streams of the first answers. After the search cache's TTL, stale entries answered at once too and revalidated (median 0.03 s).
- **Health check** (measure 1): 60 s after the start, megakino_to, movie4k and kinoking were unreachable (kinoking's site then took TLS connections but sent no HTTP answer in 15 s); all 17 searches skipped them. kinoking was found back 26 minutes later.
- **Half-open probe, SuperVideo** (measures 3 and 5): SuperVideo's breaker opened after 5 unplayable links. After the cooldown, one request's probe ran to its end (7 s, unplayable) and reopened it, while the request's three other SuperVideo links were skipped.
- **Filemoon**: on its own, 4 of 4 embeds resolved in 1.5–2 s, but faster hosters reach the target first: in 9 traced titles, 17 of 20 Filemoon resolutions were cut at the answer (all before 5 s, so none counted for the breaker) and 3 found a stream. When 17 cached titles were asked within seconds (pass 2, and again after the TTL), their background resolutions ran at once, and 12 and 16 Filemoon resolutions hit the 10 s timeout, which opened its breaker (the stealth browser loads 2 pages at a time). The old code's answers, which resolved every link, had 8 playing Filemoon streams.
- **HLS proxy** (measure 6, the 1080p fix): ffmpeg (`Lavf/`) was refused the playlist of 57 of 57 HLS streams (the old code served it), a player got it with HEAD and GET for 57 of 57. CPU per relayed MB of one VOE stream (rounds of 40 segments, about 40 MB, idle-corrected): old code 34–36 ms, `staging` 39–46 ms, the same with httpx's anyio backend 47–52 ms, with 64 KiB pieces (fixed, below) 25 ms. VOE's CDN sends 4 KiB TLS records, and passing them through cost a response write each; on x86 the asyncio backend saves 14% on its own.
- **Links** (measure 10): after a restart and 63–73 minutes, run 1's stored stream objects resolved again (67 links, `stremio_link_resolved_again`) and 64 of 70 played. 3 FireStream links whose hoster gave no video failed although their stored playlists still answered (fixed, below). At 2.0–2.2 hours, the old code's 67 proxied HLS links all answered 404 (the stored link's 2 h TTL; 35 of its 36 direct CDN links still played). `staging` played 60 of 70 links of run 1 (2.5 hours, resolved again a second time) and 70 of 73 of run 2 (1.5 hours); the failures were re-resolutions that failed (DoodStream's browser fallback timed out 3 times, Vidmoly 3 times; both instances predate the stale-URL fallback) and CDN timeouts.
- **Metrics and tracing**: `/metrics` answered 46 KB, 610 series, at 5.6 ms of CPU per scrape (0.01% of a core at a 60 s interval). With `telemetry.tracing_endpoint`, a local OTLP receiver got one trace per request (phases, plugin searches, resolutions; `/play` and proxy re-resolutions as traces of their own with the request id), without URLs or titles in attributes. The access log held the CDN's tokens and the client's address in proxy queries (fixed, below).
- **Play check failures** were CDN timeouts: StreamUp's CDN took 15–40 s per segment in the dev network, and FireStream's answered some segments after more than 15 s; fetched directly moments later, both delivered.

Fixes from the round, each with tests: cached answers keep a hoster's cached stream (fe58559), the HLS proxy sends 64 KiB pieces (bac6583), the access log masks query values (577e7cd), a stale link keeps its video URL when the hoster fails (930f21f).

## AIOStreams

Goal was an AIOStreams test user on `aiostreams.lan` with Scavengarr as addon, measured end to end. Not done: AIOStreams validates the addon manifest when a user is created or updated, and it can reach neither the dev instance (Docker NAT on the workstation) nor `scavengarr.lan` (502, backend down). Recommended user settings, from the AIOStreams v2.35.3 source (`packages/core/src/presets/custom.ts`, `packages/core/src/db/schemas.ts`):

- Scavengarr as `custom` preset with `manifestUrl: https://scavengarr.lan/api/v1/stremio/manifest.json`, `resources: ["stream"]`, `mediaTypes: ["movie", "series"]`.
- `timeout: 62000` (ms, per addon; AIOStreams default 7000 would cut most Scavengarr answers): `stream_deadline_seconds` (60 s since the round-5 measures) + 2 s headroom.
- `preferredLanguages: ["German", "Multi", "Dual Audio", "English", "Unknown"]`, `sortCriteria.global`: language, resolution, quality (all `desc`).
- Scavengarr already returns one working stream per hoster, so AIOStreams dedup and result limits need no special handling for it. Whether AIOStreams parses language and resolution from Scavengarr's stream names reliably is unverified; if not, `formatPassthrough: true` keeps Scavengarr's own labels.

Deferred (2026-09-29): without other sources AIOStreams adds nothing Scavengarr does not already do (per-hoster dedup, German-first ranking, playback check) but costs latency and risks metadata misparsing, so Scavengarr is used directly in Stremio. If AIOStreams comes back (e.g. with debrid sources): create the test user, measure end to end, and check whether language/resolution of Scavengarr streams are parsed (else `resultPassthrough: true`).
