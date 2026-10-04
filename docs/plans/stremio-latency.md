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
- **IP-bound streams:** production reaches the sites through a VPN (stream tokens name 185.107.94.2, AS43350 NForce), the home network is 92.209.223.18. DoodStream and Vinovo bind their stream URLs to the resolving IP, so a player on another IP gets `error_wrong_ip` or 403. Streams through Scavengarr's HLS proxy are fetched from Scavengarr's IP and are not affected; VEEV, Playmate and FireStream played from the other IP.
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
- **Torznab on s.to** with the VPN IP: a search for "Dark" took 83 s and sent about 950 link-out requests (all matching series, every episode and hoster); 97 of 100 results kept the s.to link-out because the gate allowed 3.
- **Play check** (`scripts/stremio_playcheck.py` from the home network): 6 of 9 streams playable, the HLS-proxy streams (VOE, Vidsonic, StreamUp) included; `HEAD` on the proxy answers 200 with CORS. The 3 failures are IP-bound (DoodStream `error_wrong_ip`, Vinovo 403, FSST 410) and play through Stremio's server in the same VPN.
- megakino_to and movie4k: every domain answers Cloudflare 522 (origin down). kinoking is often cut at 10 s.

## AIOStreams

Goal was an AIOStreams test user on `aiostreams.lan` with Scavengarr as addon, measured end to end. Not done: AIOStreams validates the addon manifest when a user is created or updated, and it can reach neither the dev instance (Docker NAT on the workstation) nor `scavengarr.lan` (502, backend down). Recommended user settings, from the AIOStreams v2.35.3 source (`packages/core/src/presets/custom.ts`, `packages/core/src/db/schemas.ts`):

- Scavengarr as `custom` preset with `manifestUrl: https://scavengarr.lan/api/v1/stremio/manifest.json`, `resources: ["stream"]`, `mediaTypes: ["movie", "series"]`.
- `timeout: 17000` (ms, per addon; AIOStreams default 7000 would cut every Scavengarr answer): `stream_deadline_seconds` + 2 s headroom.
- `preferredLanguages: ["German", "Multi", "Dual Audio", "English", "Unknown"]`, `sortCriteria.global`: language, resolution, quality (all `desc`).
- Scavengarr already returns one working stream per hoster, so AIOStreams dedup and result limits need no special handling for it. Whether AIOStreams parses language and resolution from Scavengarr's stream names reliably is unverified; if not, `formatPassthrough: true` keeps Scavengarr's own labels.

Deferred (2026-09-29): without other sources AIOStreams adds nothing Scavengarr does not already do (per-hoster dedup, German-first ranking, playback check) but costs latency and risks metadata misparsing, so Scavengarr is used directly in Stremio. If AIOStreams comes back (e.g. with debrid sources): create the test user, measure end to end, and check whether language/resolution of Scavengarr streams are parsed (else `resultPassthrough: true`).
