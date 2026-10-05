# Optimization options after the performance plan

Status: proposal (2026-10-04). Nothing here is decided. It follows the performance plan (`pi-performance.md`) and the end-to-end rounds in `stremio-latency.md`; evidence is from production (Raspberry Pi 4 behind a VPN), its logs and probes, and web research (sources inline).

## Where the time goes

First requests (the plugins search), production, fourth round, before the fixes of that evening:

| Phase | Median | Range | What decides it |
|---|---|---|---|
| Search | 7.0 s | 7.0–7.0 s | The soft deadline (`search_soft_deadline_seconds`): every request had a plugin still running. The slow ones rarely deliver: kinoking 13.4 s on average, movie4k 13.4 s, megakino_to 13.1 s, kinoger 17.0 s. The plugins that deliver average 1.0–4.7 s (aniworld 1.0, fireani 1.5, megakino 1.5, filmpalast 2.0, moflix 2.6, hdfilme 4.0, movie2k 4.1, s.to 4.7). |
| Resolve | 5.8 s | 3.5–8.2 s | The first playable stream 1.4 s after the search (median), then `resolve_grace_seconds` (4 s) for the others. |
| Answer | 12.4 s | max 15.3 s | Search + resolve. From the search cache: 4.6 s (resolution only). |

- CPU per request before the hoster breaker: Chromium about 10 s (hoster pages run in the browser, most of them never delivered), Python 2.5 s, of it 1.1 s on the GIL.
- Plugin yield over two hours of production (`/api/v1/stats/metrics`): no result from kinoger (17 searches), megakino_to (21), movie4k (27), kinox (39) and einschalten (50); haschcon 2 of 50.

## Since the fourth round

**Fixed on `staging`, not yet in production** (each with tests; found in the fourth round and in the production logs of the same evening):

| Commit | Finding |
|---|---|
| fb3ad00 | The circuit breaker counted the early answer's cut against plugins that run on (kinox opened for movies). |
| fa2e58f | Circuit breaker per hoster resolver: 50 DoodStream and Dropload browser captures in an hour, none delivered. |
| 7600a06, 4af38f5 | moflix's own players failed 15 of 15: moflix-stream.click has no `/e/` route and packs its URLs as `links={"hls2":…}`; moflix-stream.link is a Byse (Filemoon) player. |
| e9b1ba1 | moflix fetched title details for people (19 of 20 hits for "Oppenheimer"; 184 answers 404 in three hours). |
| 65077ba | FireStream ids with `-` were rejected; their pages play. |
| 5bd2e43 | Playmate player-frame links (`/embed/`) were rejected. |
| c4a6b6c | veev redirects links to another file code; the API answered the old one "malformed request" (3 of 3 failed links resolve now). |

**Site and environment state** (no code fix):
- **kinox** puts its link-outs (`/redirect/<hash>`) behind a "Verifizierung" page: a reload loader (an XOR-obfuscated `setTimeout(reload, 100)`), then an image-selection captcha. The plugin finds titles and hosters but no links: 0 results in 39 searches, `kinox_no_hoster_links` 23 times in three hours.
- **VOE** denies 7 files to the VPN address ("File access denied"); all 7 resolve from the home network.
- **mixdrop's CDN** is refused by gluetun's DNS (its malicious IP list holds 168.80.0.0/15); the production compose turns `BLOCK_MALICIOUS` off and keeps every lookup in the tunnel (gluetun's own DNS server over TLS; a LAN resolver as upstream would send them outside the tunnel) (`stremio-addon.md`, "Behind a VPN container").
- moflix-stream.click's CDN (dramiyos-cdn.com) did not answer from the home network within 20 s; from production untested.

**Fifth round: pending.** Production lost its network on the evening of 2026-10-04: the VPN container was recreated, the 17 containers that share its network were not (they kept the dead namespace: `lo` only). It was still offline on 2026-10-05 at 07:08 UTC, so the fixes above and the option estimates below are not measured in production yet. The round, once the containers are recreated with a build of `staging`: clear the 17 titles' search-cache entries, `scripts/stremio_measure.py --pause 30` twice (searching, then from the cache), `scripts/stremio_playcheck.py` inside the container, `scripts/stremio_profile.py --py-spy` for the CPU per request.

## Research findings

**Stremio clients and other addons** (research 2026-10-04):

- **Cache hints:** stremio-core ignores the `cacheMaxAge`/`staleRevalidate`/`staleError` fields of an addon response; only the HTTP `Cache-Control` header counts. Web and desktop fetch through the browser's HTTP cache (stale-while-revalidate works there, stale-if-error does not), the archived Android binding through an on-disk HTTP cache. AIOStreams and NuvioTV ignore the hints. ([response.rs](https://github.com/Stremio/stremio-core/blob/development/src/types/addon/response.rs), [env.rs](https://github.com/Stremio/stremio-core/blob/development/stremio-core-web/src/env.rs), [fetch.rs](https://github.com/Stremio/stremio-core-kotlin/blob/master/stremio-core-kotlin/src/commonMain/rust/env/fetch.rs))
- **Waiting:** no client timeout was found; stremio-web shows each addon's streams as soon as that addon answers, all or nothing per addon ([StreamsList.js](https://github.com/Stremio/stremio-web/blob/development/src/routes/MetaDetails/StreamsList/StreamsList.js)).
- **Binge and resume:** the player asks the addon that served the current stream for the next episode's streams when it loads, and at the end opens the stream object with the same `bingeGroup`, without a new request. "Continue Watching" replays the stored stream object, which has no expiry. A binge URL is one episode old when it plays, a resume URL can be days old ([player.rs](https://github.com/Stremio/stremio-core/blob/development/src/models/player.rs), [Player.js](https://github.com/Stremio/stremio-web/blob/development/src/routes/Player/Player.js), [LibItem.js](https://github.com/Stremio/stremio-web/blob/development/src/components/LibItem/LibItem.js)).
- **Other addons** resolve on click and cache the redirect (Torrentio `/resolve` 3 h, MediaFusion `/playback` 1 h, StremThru 3 h keyed by client IP, Jackettio 1 h; WebStreamr, which scrapes German sites with VOE-type hosters, only for hosters that resolved recently). Background work: MediaFusion re-scrapes requested titles, Comet scrapes Cinemeta's top lists hourly, Jackettio and AIOStreams ("Pre-cache Next Episode") search episode N+1 after N. Concurrent requests for one title are merged (Torrentio, StremThru). Sources: [Torrentio](https://github.com/TheBeastLT/torrentio-scraper/blob/master/addon/addon.js), [MediaFusion](https://github.com/mhdzumair/MediaFusion/blob/main/backend/src/routes/playback.rs), [StremThru](https://github.com/MunifTanjim/stremthru/blob/main/internal/stremio/wrap/playback.go), [Jackettio](https://github.com/arvida42/jackettio/blob/master/src/lib/jackettio.js), [WebStreamr](https://github.com/webstreamr/webstreamr/blob/main/src/extractor/ExtractorRegistry.ts), [Comet](https://github.com/g0ldyy/comet/blob/main/comet/background_scraper/cinemata_client.py), [AIOStreams](https://github.com/Viren070/AIOStreams/blob/main/packages/core/src/main/resources.ts).
- **Resolve on click, caveats:** one tap on Android caused 11 requests to the stream URL (a HEAD from the streaming server, GETs from ExoPlayer and Lavf), so resolutions must be cached and merged; redirects to HLS playlists fail on Android (stremio-bugs [#1574](https://github.com/Stremio/stremio-bugs/issues/1574), open), so HLS has to stay a proxied playlist; an addon can only show errors as an error video ([#2091](https://github.com/Stremio/stremio-bugs/issues/2091)).

**Runtime, browser and HTTP** (research 2026-10-04; nothing measured on a Pi unless stated):

- **httpx has stalled** (last release 0.28.1, Dec 2024; issues closed since Feb 2026). **httpx2** is Pydantic Services' fork; its httpcore2 fixes the quadratic pool scan that cost production 20% of its GIL samples with 100 idle connections (2% with the 20 kept since; httpcore2 2.3.0) and the assignment of one released connection to several queued requests (2.8.0). Not drop-in for the tests: respx rejects `httpx2.Response` objects (respx [#324](https://github.com/lundberg/respx/issues/324), open), and the resolver tests are respx-based. ([httpx](https://pypi.org/project/httpx/), [httpx2](https://pypi.org/project/httpx2/), [httpcore2](https://pypi.org/project/httpcore2/), [migration](https://pydantic.dev/docs/httpx2/get-started/migration/))
- **Faster clients** exist (pyreqwest ships an httpx transport; aiohttp, niquests, curl_cffi, wreq), but no independent CPU benchmark, none on aarch64. ([pyreqwest benchmarks](https://github.com/MarkusSintonen/pyreqwest/blob/main/docs/benchmarks.md))
- **TLS on the Pi 4:** no ARMv8 crypto extensions; AES-256-GCM ran at 24.5 MB/s, ChaCha20-Poly1305 at 82 MB/s. OpenSSL offers AES-256-GCM first for TLS 1.3, and Python 3.14 cannot reorder TLS 1.3 suites (3.15 adds `set_ciphersuites()`); an OpenSSL config can. ([PR 3772](https://github.com/raspberrypi/linux/pull/3772), [ssl docs](https://docs.python.org/3.14/library/ssl.html))
- **Chromium** used 81–92% of the CPU of a stream request in the final measurement of `pi-performance.md` (10–30 s against 0.9–7 s of Python). New headless mode used about half the CPU of headful in a vendor test (setup unpublished); headful under Xvfb is what patchright recommends against bot checks. `page.route()` disables the HTTP cache (and with it V8's code cache); CDP's `Network.setBlockedURLs` blocks without interception. Turnstile caught CDP clicks until Chromium fixed the click coordinates in version 142. ([headless shell](https://developer.chrome.com/docs/automation-and-testing/headless-chrome-shell), [Browser Use](https://browser-use.com/posts/what-is-a-headless-browser), [route()](https://playwright.dev/docs/api/class-page), [patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python), [Turnstile CDP fix](https://webscraper.io/blog/google-patches-100-precise-cloudflare-turnstile-bot-check))
- **Cloudflare from a VPN:** datacenter and known VPN addresses have poor trust scores; no independent solver success rates from such addresses exist. FlareSolverr 3.5 added a Turnstile step, Byparr 3 moved to a stealth Firefox (ARM experimental); neither changes the address's reputation. `cf_clearance` lives 30 minutes by default. ([ZenRows](https://www.zenrows.com/blog/bypass-cloudflare), [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr), [Byparr](https://github.com/ThePhaseless/Byparr), [clearance](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/challenge-passage/))
- **Python 3.14:** the JIT is often slower than the interpreter in 3.14 (about 2% faster on a Pi 5 in 3.15); free-threading needs every C extension to be ready (orjson is not). Production already runs uvloop 0.23, selectolax 1.0 (lexbor, releases the GIL) and patchright 1.63. ([JIT](https://blog.python.org/2026/03/jit-on-track/), [free-threading](https://docs.python.org/3.14/howto/free-threading-python.html))

## Options

| # | Option | Effect | Effort | Risk | Recommendation |
|---|---|---|---|---|---|
| 1 | Production config: disable the plugins that deliver nothing from the VPN (kinox, kinoger, megakino_to, movie4k) | No stream lost (0 results in 2 h); saves kinoger's browser solve per half-open probe (about 30 s of Chromium), the 13 s timeouts and kinox's 2.2 s plus its gated link-outs | config | A site that comes back stays off until re-enabled | Do |
| 2 | Plugins predicted to miss the soft deadline do not hold the answer | Search phase 7 s → about 4–5 s (the delivering plugins' averages): answer about 2–3 s sooner | S–M | A slow plugin's results reach only the next request, as today for most | Do |
| 3 | Re-resolvable stream links for binge and resume | Binge plays the next episode's stream object fetched one episode earlier, resume replays objects days old; proxy links expire after 2 h (`stream_link_ttl_seconds`), direct CDN links with their tokens | M | Longer-lived link entries in the cache | Do |
| 4 | `Cache-Control: private, max-age` on stream answers (shorter than the shortest link lifetime; 60 s for empty answers) | Reopening a title in Stremio web or desktop comes from the client's cache | S | None with a short max-age; no effect through AIOStreams | Do |
| 5 | Persist the circuit breakers across restarts | No relearning after each deploy or watchtower update (5 timeouts per dead plugin and category, each up to the hard deadline) | S | A stale open state; the half-open probes still run | Do |
| 6 | Shorter resolve grace (4 → 2–3 s) | 1–2 s per first request | config | Fewer streams per answer; A/B in production after the hoster breaker | Measure |
| 7 | Headless Chromium for hoster pages without a challenge, headful only for challenges | Up to about half of the browser CPU (vendor figure) | M | A hoster may detect headless; measure on the Pi first | Measure |
| 8 | Block resources through CDP instead of `page.route()` | Keeps the HTTP and V8 code cache (inference) | S–M | Little; measure first | Measure |
| 9 | Resolve while plugins still search | First stream at the search end instead of 1.4 s after it; with 2 an answer at about 7–8 s | M–L | More resolutions per request (load on hosters), complexity in the use case | Later |
| 10 | Lazy play links: answer after the search, resolve on click (hybrid: only for hosters that resolved recently) | First answer at the search end (4–7 s), from the cache near-instant; binge and resume links stay valid | L | Dead links listed; resolution at click (browser hosters 5–15 s); HLS must stay a proxied playlist (Android redirect bug); every client request must hit a cached, merged resolution | Design spike |
| 11 | VPN exit with a better reputation (German or residential) | VOE files denied to the VPN play; Turnstile of kinoger and DoodStream may pass | user infrastructure | Cost and privacy | User decision |
| 12 | httpx2 (httpcore2 without the quadratic pool scan) | Allows more idle connections again; GIL share of the pool unmeasured since keep-alive 20 | S | respx cannot mock it yet: the resolver tests break | Wait |
| 13 | ChaCha20 first for TLS 1.3 (OpenSSL config in the image) | Less decryption CPU on the Pi 4 where servers honor the client's order; matters for the HLS proxy | S | Untested | Measure |
| 14 | Next-episode background search | Stremio's player already asks for the next episode when the current one starts; helps manual picks and AIOStreams' 7 s window | S–M | One more fan-out per episode | Optional |
| 15 | Remaining `html.parser` pages to lexbor | 0.2 s on the GIL per request | M | Parser rewrites per plugin | Low priority |

Not recommended now: a scheduled warm-up of top lists (Comet's approach) costs the Pi a fan-out per title for one viewer; a Byparr/FlareSolverr sidecar does not fix the VPN address's reputation and burns the same Chromium CPU; Python's JIT and free-threading give a few percent at most on ARM.
