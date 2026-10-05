# Measures after the fifth end-to-end round

Status: decided 2026-10-05, in progress. Evidence: the fifth round in `stremio-latency.md`, the options in `optimization-options.md`, production logs and probes. Metrics and on-demand tracing follow these measures (`observability.md`, to be written).

## Decisions (maintainer, 2026-10-05)

The maintainer chose one solution per finding, from at least three each.

| # | Finding | Decision | Effort |
|---|---|---|---|
| 1 | Plugins whose site is down (megakino_to, movie4k) run in every search and hold the first answer to the soft deadline; their breaker never opens, because fetch errors end as empty answers | Periodic health check of the plugin sites; unreachable sites are skipped until a later check finds them back | M |
| 2 | Every first answer waits for the soft deadline (7 s), then 4.1 s for the resolution | Resolve links while the plugins still search | M–L |
| 3 | A half-open hoster probe (Filemoon) is cut by the grace before half the resolve timeout, reports nothing and repeats after every cooldown, which never doubles (about 6 s of Chromium each) | The probe runs to its end in the background and reports | S–M |
| 4 | 5 of 17 cached answers waited for the grace (4.1–4.4 s) because one link was resolved for the first time or again | A cached answer goes out at once with the resolutions in the cache; the other links resolve in the background for the next request | M |
| 5 | SuperVideo: 13 of 13 links failed; its CDN answers the playlist URL with a script redirect and a HEAD with a redirect to an ad domain | First choice, following the script redirect, does not work (below). Decided instead: the hoster breaker pauses SuperVideo | S |
| 6 | The HLS proxy used about 0.3 s of CPU per segment during playback (5–7% of a core), mostly TLS decryption: the Pi 4 has no AES instructions | Prefer ChaCha20 for TLS when the CPU has no AES instructions | S |
| 7 | `html.parser` took 0.43 s on the GIL per first request (0.20 s after the performance plan) | Move every plugin parser to selectolax (lexbor) | XL |
| 8 | VOE denies some files to the VPN address | No change: every title still had streams | – |
| 9 | The first answer goes out at the soft deadline even when slow plugins would deliver more: 6 of 17 titles had fewer than 5 streams in the first answer, 4 still after the late plugins | Answer once 5 playable streams are resolved, else when every plugin and resolution is done, at most 60 s | M |
| 10 | Autoplay plays the next episode's stream about an hour after Stremio fetched it, "Continue Watching" days later; Scavengarr hands out direct CDN URLs with the hosters' tokens and proxy links that live 2 h | Re-resolvable stream links | M |

Decisions 9 and 10 came the same day, after the maintainer asked for more time per search and for the binge case (episodes of about an hour with autoplay).

Order: 3, 5, 6 (small, independent), then 4, 1, 2 with 9 (the request flow), then 10, then 7, then the metrics. Each measure is test-driven, committed on its own and documented with the code. A sixth round measures 1–6, 9 and 10 in production.

Done: 3 (1b7e01f), 5.

## 1. Plugin health check

**Problem.** megakino_to and movie4k are down (Cloudflare 522 in the fourth round, fetch timeouts in the fifth): no result in 36 and 23 searches, 15 s per search on average; megakino_to was still running at the soft deadline in all 17 first requests, and in 4 of them it and movie4k were the only plugins left. `HttpxPluginBase._safe_fetch()` and `_fetch_text()` log fetch errors and return `None`, the plugin answers `[]`, and `PluginSearchRunner._record_outcome()` ignores empty answers, so the circuit breaker never opens.

**Design.**
- `PluginHealthMonitor` (infrastructure) reuses the scoring subsystem's `HealthProber` (HEAD on the plugin's `base_url`, GET when HEAD is refused, challenge detection) without the scoring scores. It probes every Stremio plugin (`provides` stream or both): a reachable site every 30 minutes, an unreachable one every 5 minutes, the first round 60 s after the start (the Pi is busy with the first requests and the browser warm-up), 5 probes at a time.
- Unreachable: no answer (DNS, connect or read error, timeout) or a status of 500 and up without a challenge page. A challenge page means the site is up behind Cloudflare (kinoger). One failed probe marks a site unreachable, one good probe brings it back; both are logged (`plugin_unreachable`, `plugin_reachable`).
- `PluginSearchRunner.search_with_fallback()` drops unreachable plugins before it picks one per mirror group (`PluginHealthPort.is_reachable()`, a Protocol in the application layer), and logs `stremio_plugins_unreachable`.
- Off with `stremio.plugin_health_interval_seconds: 0` (default 1800).
- Cost: about 40–60 HEAD requests an hour, 1–2 s of CPU an hour on the Pi.
- Limit: kinox stays. Its site answers; only its link-outs sit behind an image captcha. It held no answer alone in the fifth round.

**Tests.** Monitor: classification (522, timeout, DNS error, 403 with Cloudflare challenge, 200), intervals for reachable and unreachable sites, recovery. Runner: an unreachable plugin is not searched; a mirror group picks a reachable member.

## 2. Resolve while plugins search

**Problem.** The resolution starts when the search ends: first answers took 7.0 s of search and 4.1 s of resolution (median), the first stream 1.2 s after the search.

**Design.** Speculative resolution during the search, the answer logic unchanged:
- The runner reports each plugin's results when they arrive (an optional `on_results` callback of `search_with_fallback()`).
- The use case filters them by title (as it does per language group today), ranks them, and starts the resolution of the best link of each hoster it has not started yet, with the request's resolve concurrency (`probe_concurrency`).
- These resolutions belong to the request (`url → task`). When the search ends, `_resolve_top_streams()` waits on a running one instead of starting the same URL again; finished ones come from the resolver registry's cache. What is still running when the answer goes out is cancelled, as today.
- The hoster breaker, the resolution cache and the deadlines apply unchanged.
- Expected: most links are resolved when the search ends; the first answer about 8–9 s instead of 11 s. A later plugin's better link of the same hoster can mean one more resolution for that hoster.

**Tests.** Use case with a fake runner that reports results at set times and a fake resolver: a link resolves during the search; the final step reuses the running resolution (one call per URL); the request cancels unfinished speculative resolutions; the answer logic (deadline, grace) is unchanged.

## 3. Half-open probes report

**Problem.** `HosterResolverRegistry._try_resolver()` counts a cut as a failure only after half of `http.timeout_resolve_seconds` (production: 5 s). Filemoon's half-open probe is cut by the grace earlier, reports nothing, and `PluginCircuitBreaker.allow()` lets the next probe through after the cooldown, which stays at 60 s.

**Design.** When `allow()` grants the half-open probe (state `half_open` afterwards), the registry runs the attempt as its own task, shielded from the request: the request stops waiting at its cut, the probe runs to its end (at most the resolve timeout) and records its outcome. A stream closes the breaker and goes into the resolution cache for the next request; a failure or timeout reopens it with twice the cooldown (up to 1 h). The registry gets `aclose()`, called at shutdown before the browser closes. At most one probe per hoster runs at a time (the breaker's half-open rule).

**Tests.** A probe cut by its caller still finishes: success closes the breaker and caches the stream; a failure doubles the cooldown; `aclose()` cancels a running probe. Normal (closed) resolutions are still cancelled with their request.

## 4. Cached answers do not wait for new links

**Problem.** A cached search answers in 0.0–1.8 s when every link it needs is in the resolution cache (1 h for streams), but 4.1–4.4 s when one has to be resolved: the answer waits for `resolve_grace_seconds`.

**Design.**
- The resolver registry gets `cached(url)`: the cached resolution of a URL (stream, dead, or not cached), without resolving.
- For a search from the cache, `_resolve_top_streams()` first takes each hoster's best link whose resolution is cached, in rank order (a dead one moves on to the next). With at least one stream that way, the answer goes out at once. The hosters without a cached stream resolve their next link in the background (the use case's `_spawn`, ended at shutdown), at most `probe_concurrency` at a time and each URL once, and fill the registry's cache for the next request.
- Without a cached stream, the request resolves as today.

**Tests.** Cache hit with a cached stream: answers without calling the resolver, background resolution started once per URL; without a cached stream: resolves and waits as today; background tasks end with `aclose()`.

## 5. SuperVideo: the breaker pauses it

**Problem.** The CDN (`serversicuro.cc`) answers the playlist URL with a "Loading..." page whose script calls `window.location.replace(<the same URL with a js token>)` and sets a `sid` cookie; a HEAD gets a 302 to an ad domain. The resolver's own HEAD check marked each link dead (`supervideo_video_verify_error`), which the hoster breaker ignores, so every request tried the links again.

**Probes from production (2026-10-05).** Following the script's target, with the cookie and with a browser's navigation headers, ends on a parked ad page (`ww547.serversicuro.cc`, "Directory Index"). The app's browser capture (`StealthPool.capture_media`, 34.6 s on the Pi) got the playlist URL, but a player gets the script page under it too: SuperVideo plays only inside a browser session. The first choice (follow the redirect) therefore cannot work. `hoster-resolvers.md` already listed the CDN behavior as a known issue.

**Decision and design.** The resolver drops its own HEAD check. The registry's playback check (`check_playable`, on in production) reads the body, counts the page as unplayable, and the hoster breaker pauses SuperVideo (60 s, doubling up to 1 h); its half-open probe (measure 3) finds out when the CDN serves a playlist again.

**Tests.** The resolver sends no HEAD; registry plus resolver with the script page: `hoster_resolve_unplayable`, breaker open.

## 6. ChaCha20 when the CPU has no AES instructions

**Problem.** The Pi 4's Cortex-A72 has no ARMv8 crypto extensions (no `aes` flag in production's `/proc/cpuinfo`): OpenSSL decrypts AES-256-GCM at about 25 MB/s and ChaCha20-Poly1305 at about 80 MB/s (source in `optimization-options.md`). Production's Python 3.14.8 with OpenSSL 3.5.7 offers `TLS_AES_256_GCM_SHA384` first, and the HLS proxy decrypts every segment it relays.

**Design.**
- TLS 1.3 (most CDNs): `docker/entrypoint.sh` sets `OPENSSL_CONF` to a shipped config with ChaCha20 first in `Ciphersuites`, only when `/proc/cpuinfo` lists no `aes` flag. Python 3.14's `ssl` has no `set_ciphersuites()` and cannot reorder TLS 1.3 suites itself. Chromium (BoringSSL) already prefers ChaCha20 on such CPUs.
- TLS 1.2: the shared client's SSL context (`GuardedTransport`) puts ChaCha20 first under the same condition.
- A server that picks its own order keeps AES. One probe from production shows which CDNs honor the client's order and the CPU per MB before and after.

**Tests.** Cipher order only without AES instructions (CPU flags injected); entrypoint check in `test_repository_files.py` style (`sh -n`, the condition).

## 7. Plugin parsers on selectolax

**Problem.** 28 plugins parse with `html.parser` subclasses (pure Python, holds the GIL); only filmpalast and hdfilme use selectolax for their biggest pages.

**Design.** Rewrite each plugin's parsing with selectolax (`LexborHTMLParser`, CSS selectors) and identical results: the plugin's unit tests and the real-page tests (`tests/unit/infrastructure/test_real_pages.py`) are the specification. Big pages parse in a worker thread as `_feed()` does today. Order: the Stremio plugins with the biggest pages first (kinoger, s.to, megakino, movie2k, aniworld, kinoking, the DLE mirrors, kinox, burningseries), then the Torznab-only plugins. The first two set the pattern and any shared helper; the others follow it one plugin per commit. `AGENTS.md` §5 and `docs/features/python-plugins.md` change with the last one.

**Tests.** Unchanged plugin tests and real-page tests pass for every migrated plugin; a parse benchmark on the real pages before and after.

## 9. Answer at 5 streams, when done, at most 60 s

**Problem.** The first answer goes out at the soft deadline (7 s) plus the resolve grace, also when slow plugins would deliver (kinoking about 8.5 s, s.to's gate about 20 s, kinoger with a Cloudflare solve): in the fifth round 6 of 17 titles had fewer than 5 streams in the first answer (Good Bye Lenin, Breaking Bad, Dark and Haus des Geldes 1 each, Stranger Things and The Last of Us 4), 4 still after the late plugins. Titles with many streams wait for the deadline all the same.

**Design.**
- The answer goes out when 5 playable streams of different hosters are resolved (`stremio.resolve_target_count`, default 5), or when every plugin and every resolution is done, at the latest at `stremio.stream_deadline_seconds` (60 s).
- Each plugin gets more time (`plugin_timeout_seconds` 30 s instead of 10 s); plugins still running at the answer go on as late plugins and fill the search cache, as today. The soft deadline and the resolve grace give way to the target and the completion rule.
- Measure 2 resolves during the search, so the target is often met before the slow plugins finish.
- Expected: titles with many streams about 4–8 s instead of 11 s; titles with few streams when their plugins are done (about 20–30 s) instead of 11 s with fewer streams. The binge case does not wait for the answer (measure 10).
- Risks: a worst case of 60 s; Stremio Web shows other addons' streams meanwhile (no client timeout found), other clients are untested with long waits.

**Tests.** Use case: the answer goes out at the target while plugins still search; without enough streams it goes out when everything is done; the deadline caps it; late plugins still fill the cache.

## 10. Re-resolvable stream links (binge and resume)

**Problem.** When episode N starts, Stremio asks for episode N+1 and at the end of N plays that answer's stream object, about an hour later, without asking again; "Continue Watching" replays a stored stream object days later (research in `optimization-options.md`). Scavengarr's answers hold direct CDN URLs (MP4, and HLS without headers) with the hosters' tokens, and proxy links for HLS with headers whose stored link lives 2 h (`stream_link_ttl_seconds`) and is never resolved again. A measurement of how long the links stay playable is running (18 links of 7 titles, checked every 30 minutes for 2.5 h).

**Design.**
- Every stream in the answer points to Scavengarr: MP4 to `/play/{id}` (a 302 to the current video URL), HLS to the proxy.
- The stored link keeps the hoster URL. `/play` and the proxy resolve it again when the stored video URL is older than a freshness bound (from the measurement) or when the CDN answers 403, 404 or 410 (once, past the registry's cache).
- Stored links live for days (for "Continue Watching"); one entry per stream.
- Constraints from the research: one tap on Android sent 11 requests (a HEAD from the streaming server, GETs from ExoPlayer and Lavf), so a re-resolution is shared per link and cached; HLS stays a proxied playlist (Android's HLS redirect bug); IP-bound streams (DoodStream, Vinovo) resolve from Scavengarr's address, which the maintainer's Stremio server shares.
- Cost: one redirect per MP4 start; a re-resolution (1–5 s, browser hosters longer) when a link is stale at playback.

**Tests.** `/play` re-resolves a stale link and redirects to the new URL and leaves a fresh one alone; the proxy re-resolves when the CDN refuses the master playlist; concurrent requests share one re-resolution; links outlive the old 2 h.

## 8. VOE

No change. Some VOE files answer "File access denied" to the VPN address; every title of the fifth round still had other streams. A different VPN exit stays the maintainer's infrastructure decision (option 15 in `optimization-options.md`).
