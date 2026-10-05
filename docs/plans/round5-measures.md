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
| 5 | SuperVideo: 13 of 13 links failed; its CDN answers the playlist URL with a script redirect and a HEAD with a redirect to an ad domain | Follow the script redirect without a browser and require a real playlist | S |
| 6 | The HLS proxy used about 0.3 s of CPU per segment during playback (5–7% of a core), mostly TLS decryption: the Pi 4 has no AES instructions | Prefer ChaCha20 for TLS when the CPU has no AES instructions | S |
| 7 | `html.parser` took 0.43 s on the GIL per first request (0.20 s after the performance plan) | Move every plugin parser to selectolax (lexbor) | XL |
| 8 | VOE denies some files to the VPN address | No change: every title still had streams | – |

Order: 3, 5, 6 (small, independent), then 4, 1, 2 (the request flow), then 7, then the metrics. Each measure is test-driven, committed on its own and documented with the code. A sixth round measures 1–6 in production.

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

## 5. SuperVideo behind its CDN's script redirect

**Problem.** The CDN (`serversicuro.cc`) answers the playlist URL with a "Loading..." page whose script calls `window.location.replace(<the same URL with a js token>)` and sets a `sid` cookie; a HEAD gets a 302 to an ad domain. The resolver's HEAD check fails (`supervideo_video_verify_error`).

**Design.** Replace the HEAD check by a GET with the playback headers: a body starting with `#EXTM3U` is the playlist; a script redirect is followed (GET, redirects included) and must end in a playlist. The stream uses the final URL, with the cookie in its headers if the CDN needs it (to be checked with one careful probe from production; the CDN answered repeated probes with 429).

**Tests.** respx: direct playlist; script redirect to a playlist; redirect to a non-playlist (fails); HEAD no longer used.

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

## 8. VOE

No change. Some VOE files answer "File access denied" to the VPN address; every title of the fifth round still had other streams. A different VPN exit stays the maintainer's infrastructure decision (option 15 in `optimization-options.md`).
