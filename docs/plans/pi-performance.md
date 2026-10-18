# Performance on the Raspberry Pi: Baseline

Status: baseline measured 2026-10-04 (v0.2.3). The decided measures are implemented and were measured again the same day (staging cfb4c6e, see [After the measures](#after-the-measures-2026-10-04-staging-cfb4c6e)). Purpose: a baseline to judge optimization proposals against (for example from a literature review), the candidate measures the numbers point to, and their effect.

## Setup

- Raspberry Pi 4 (4× Cortex-A72, 8 GB RAM, aarch64), Python 3.12, Scavengarr in Docker. All traffic goes through a VPN container; DNS is its resolver on 127.0.0.1.
- Not installed: uvloop, httptools, h2, orjson, lxml, selectolax, brotli.
- Cache (`/app/cache`, diskcache/SQLite) and config live on the SD card (`/dev/mmcblk0p2`, ext4). Chromium's `/dev/shm` is a 1 GB tmpfs.
- Shared httpx client: `AsyncHTTPTransport()` with httpx defaults:
  - HTTP/1.1 only;
  - at most 100 connections and 20 keep-alive connections;
  - keep-alive expiry 5 s.

  It is wrapped by `RetryTransport` with a per-domain rate limiter (`composition.build_http_client`).

## Production measurements (warm instance)

Each measurement covers one Stremio stream request. Python and Chromium CPU come from `/proc` before and after the request, read through Portainer exec. The outbound requests are the httpx log lines of the request.

| Request | Streams | Wall | Python CPU | Chromium CPU | httpx requests | Hosts |
|---|---|---|---|---|---|---|
| Interstellar | 6 | 15.2 s | 8.8 s | 3.8 s | 105 | 42 |
| Breaking Bad S01E02 | 1 | 15.2 s | 5.2 s | 9.1 s | 47 | 18 |
| The Matrix | 3 | 15.7 s | 12.8 s | 7.2 s | 205 | 39 |

- **Python CPU:** the process runs one event loop on one core and was busy for 34–82% of the request's wall time. The wall time itself is set by the 10 s search budget plus the resolve phase. Every I/O callback waits behind whatever CPU work is running.
- **Memory after the requests:**
  - Chromium: 15 processes, 1.7 GB RSS (shared pages are counted once per process);
  - Python: about 0.27 GB;
  - container cgroup: 625 MB;
  - host: 5.0 of 7.8 GB available, swap unused, load average 2.4 (37 containers on the production host).
- **DNS through the VPN container:** 2 ms when cached, 34–42 ms uncached.

## Where the Python CPU goes

Profiled with py-spy (100 Hz) on a local server (x86) running production's config. The run covered 4 stream requests. Startup imports are excluded, which leaves 428 samples, about 4.3 s of CPU.

| Self time | Share |
|---|---|
| TLS handshakes (`ssl.do_handshake`) | 20.8% |
| httpx/httpcore/h11 | 14.7% |
| asyncio/anyio/selectors (event loop) | 13.6% |
| `html.parser` (stdlib, used by 29 plugin and resolver files) | 8.9% |
| `http.cookiejar` | 5.6% |
| thread pool (`getaddrinfo`, `to_thread`) | 5.6% |
| diskcache/SQLite | 5.1% |
| socket | 3.7% |
| logging | 0.9% |
| other (guessit/rebulk, json, urllib.parse, …) | ~21% |

Limits of this profile:

- The sample is small.
- It ran on x86, not ARM. The production host 4's Cortex-A72 has no ARMv8 crypto extensions, so TLS handshakes and AES should weigh more there.
- Chromium is not included.

## Candidate measures

Ordered by measured share and effort.

1. **Connection reuse.** Raise the keep-alive expiry from 5 s to minutes and allow more keep-alive slots; optionally add HTTP/2 (`h2`).
   - Target: the 21% handshake share, plus 1–2 VPN round trips per new connection. One stream request talks to 18–42 hosts, and every pause of more than 5 s between requests closes all connections.
   - Measure: handshakes per request, Python CPU per request.
2. **Event loop.** Add uvloop and httptools (`uvicorn[standard]`).
   - Target: the 14% event-loop share and the server's own HTTP parsing.
3. **HTML parsing.** Use selectolax (lexbor) for the hottest parsers, and move big pages off the event loop (AGENTS.md already asks for a worker thread).
   - Target: the 9% parsing share, plus loop stalls.
4. **Requests per stream request** (47–205 today). Known waste:
   - kinoger fetches 5 search pages for one title;
   - moflix loads details of unrelated titles;
   - fireani gets a 404 on `GetEpisode` for every result;
   - hdfilme's search takes a 301 each time (`hdfilme.cafe` → `.ceo`);
   - Torznab searches on s.to send about 950 link-outs (see stremio-latency.md, third round).
5. **SD card.** Move the cache to tmpfs or the Redis profile. Also cut log volume: httpx writes one JSON line per outbound request at INFO.
6. **Stream cache and prefetch.** Torznab searches are cached (`search-caching.md`); Stremio stream requests are not. Cache stream lists per id, and prefetch the next episode on an episode request: Stremio's autoplay asks for it, and the prefetch spreads s.to's quota of 3 link-outs per gate pass.
7. **Chromium.** It holds 1.7 GB and uses 4–9 s CPU per request. Run the browser less often: session takeover already covers kinoger and moflix, the resolver captures remain. Also limit renderer processes.

## Review of the literature research (2026-10-04)

A literature review proposed measures for fan-out, tail latency, caching, Python and the production host. It is the gitignored `.devdata/research/performance-deep-research.md`: Tail at Scale, Kwiken, SRE Book, RFC 5861, Mercator, uvloop/httptools, selectolax and Raspberry Pi/Docker sources. Its proposals, checked against this baseline, production and the code:

| Proposal | Verdict |
|---|---|
| `httpx.Limits` (keep-alive 60 s, 100 slots) and a separate connect timeout | Confirmed: handshakes are 21% of the CPU; the client uses the defaults |
| `uvicorn[standard]` (uvloop, httptools) | Confirmed: the event loop takes 14% |
| Stremio stream cache with stale-while-revalidate and single-flight | Confirmed: production served the same title twice within 17–18 s (Avengers: Endgame, Severance), a full fan-out each time |
| Partial results when a plugin hits its timeout | Confirmed in code: `_run_plugin_with_timeout` returns `[]`, so a slow plugin's finished detail pages are lost (kinoking is often cut at 10 s) |
| Fewer requests per stream request | Confirmed: two title variants double the fan-out ("Matrix" and "The Matrix": 205 requests against 105 for Interstellar) |
| Resolve DNS once and connect to the IP the address guard checked | Confirmed in code: the guard and httpcore both resolve. The time saved is small, because the VPN container caches (2 ms). It does close a DNS-rebinding gap in the SSRF guard |
| selectolax instead of `html.parser`, parsing off the event loop | Confirmed at 9%; profile on the production host first |
| Early stop (`resolve_target_count > 0`) | Conflicts with the decision of 2026-10-01 (complete answers over an earlier end); needs the maintainer |
| Token bucket "serializes every request per domain" | Overstated: it sleeps only when the bucket is empty, and then the spacing is the configured rate (10 rps, burst 10, AIMD up to 50). The `co.uk` key problem is real but hits no current site |
| `cf_clearance` does not work with httpx (TLS fingerprint), use curl_cffi | Refuted for kinoger and moflix: httpx with the browser's cookies and User-Agent passes both from the VPN IP, and curl_cffi did no better (antibot-patchright.md Phase 4) |
| Memory cgroup disabled on the production host | Already fixed: `cgroup_enable=memory cgroup_memory=1` in the kernel command line, the cgroup reports 625 MB |
| Cooler and CPU governor | No throttling seen: 54 °C at 1.8 GHz (the maximum), governor `ondemand`; one sample, Raspberry Pi 4 |
| Docker's embedded DNS | Not used: Scavengarr runs in the VPN container's network namespace and uses its caching resolver |
| SSD instead of the SD card | Applies: cache and config are on the SD card (`mmcblk0p2`) |

## Baseline before the measures (2026-10-04, staging 7ead19f)

Measured in production with `scripts/stremio_profile.py --py-spy` on the three baseline titles. The first pass ran right after a restart (cold), the later passes warm.

| Pass | Wall | Python CPU | Chromium CPU | httpx requests | Streams |
|---|---|---|---|---|---|
| Cold (fresh container) | 15.1 s | 8.1 s | (not valid¹) | 144 | 5.3 |
| Warm 1 | 14.8 s | 3.8 s | 7.4 s | 67 | 5.3 |
| Warm 2 | 12.9 s | 4.4 s | 18.6 s | 53 | 5.0 |

¹ Chromium closes renderers all the time, and their CPU dropped out of the sum (one request showed −12.7 s). The script now also counts what reaped children used (`cutime`/`cstime`); the warm passes were measured that way.

- **Spread between requests:** large. The Breaking Bad episode gave 0–2 streams and used 8–39 s of Chromium CPU (gate passes, challenge solves). Compare means, not single requests.
- **Event-loop lag** over the passes: p50 1.1–1.3 ms, p99 ~130 ms, max 384 ms.

**Python CPU on the production host** (py-spy, threads holding the GIL, 1499 samples in the warm passes, ~15 s CPU):

| Inclusive | Warm | Cold + warm | Biggest callers |
|---|---|---|---|
| guessit/rebulk (release-name parsing) | 32.9% | 24.7% | `title_matcher._extract_title_candidates` 11.1%, `_extract_result_year` 3.7%, `release_parser.parse_quality` 3.6%, `_language_from_guessit` 2.9%: the same release title parsed several times |
| `html.parser` | 26.6% | 20.9% | filmpalast detail pages 8.4%, hdfilme 5.9%, kinox 2.6%, megakino 2.0%, movie2k 1.5%, s.to 1.4% |
| event loop (asyncio/anyio/selectors) | 12.6% | 15.5% | |
| httpx/httpcore/h11 | 8.7% | 13.5% | |
| `http.cookiejar` | 4.7% | 6.2% | |

- TLS shows up with only 0.7–1.5% here: in GIL mode py-spy misses the handshakes, because OpenSSL releases the GIL. They still block the event loop thread; locally, without GIL mode, they were 21%.
- The ranking on the production host differs from the x86 profile above: release-name parsing and HTML parsing take about 60% of the Python CPU.
- That puts the cheapest gain in parsing each release title only once. This was not in the decided plan; it is added because the profile shows it.

## Decided plan (maintainer, 2026-10-04)

The maintainer chose one option per area, from at least three each.

Order: build the measurement tools first and measure the current state, then implement every measure, then measure again with the same titles.

| Area | Decision |
|---|---|
| Measurement | Script in `scripts/` (wall time, Python and Chromium CPU, httpx requests per host per stream request) and an event-loop lag metric in the app. py-spy also runs in production: `cap_add: SYS_PTRACE` on the container, py-spy run as root via exec |
| Connections | `httpx.Limits` (keep-alive 60 s, 100 keep-alive slots), a separate connect timeout, HTTP/2 as a switch, measured with and without. The keep-alive slots went back to httpx's 20: httpcore's pool scan is quadratic in idle connections and held the GIL 10–20% of the time with 100 (production, 2026-10-04) |
| Event loop | `uvicorn[standard]` (uvloop, httptools), eager tasks and Python 3.14, all in one step. The eager tasks never ran in production (uvloop 0.23 made asyncio's factory start them lazily) and are off since 2026-10-06: under a factory of the app's own, anyio lost its cancel scopes (`CHANGELOG.md`) |
| Stremio cache | Search results per title or episode, with stale-while-revalidate and single-flight, in the `CachePort` (diskcache unless Redis is configured). Resolution stays fresh, because hoster links expire |
| Answer | Partial results when a plugin hits its timeout. Once the cache exists, an early answer; the search goes on in the background and fills the cache |
| Requests | Central only: title variants by plugin language, Stremio's `max_results_per_plugin` lowered after a recall check on a title set, base classes remember a site's redirect target. The check (below) kept the cap at 50: lowering it changed nothing |
| s.to | While the gate is active and no pass is possible, return link-outs unresolved |
| Parsing | Big pages off the event loop first, then selectolax for the hotspots the production host profile shows |
| Browser | `serviceWorkers="block"`, blocking by resource type everywhere, `--renderer-process-limit=2`, restart the browser after N solves. Done; the restart counts stealth pages (200), and site isolation stays on (check below) |
| DNS/SSRF | Connect to the IP the address guard checked (one lookup, closes the DNS-rebinding gap). Done: `GuardedNetworkBackend` |
| production host | No change |

## Recall check: title variants and the result cap (2026-10-04)

Before the request measures changed what is searched, a probe ran in the production container (VPN, staging 7ead19f, with the browser fallback) over 12 titles: 8 films (four whose German title differs from the original, two with a colon) and 4 series episodes. Each of the 13 Stremio plugins searched every query variant on its own, with the result cap 50, and the localised title also with 24. s.to and aniworld were left out (link-out gate quota). "Streams" counts distinct (title, hoster) pairs, since the answer has one stream per hoster.

| Queries | Search requests | Title-matching links | Streams |
|---|---|---|---|
| Every variant (localised title, base before a colon, TMDB original title and its base) | 909 | 81 | 58 |
| Titles of the plugin's languages and their base (no original titles) | 700 (−23%) | 69 | 56 |
| The same, original titles only when they found nothing | 778 | 69 | 56 |
| Every variant only for a plugin whose title found nothing | 658 | 67 | 55 |
| Localised title only | 534 (−41%) | 64 | 54 |

- **Original titles:** as queries they cost 209 requests and added 2 streams, both for one title (filmpalast found "Pirates of the Caribbean" but not "Fluch der Karibik"). They are no longer queries; the title matching still accepts results under them.
- **Base titles** stay: "Avengers" found Avengers: Endgame on four sites where "Avengers Endgame" did not (3 more streams).
- **Result cap 24 instead of 50:** no effect. No plugin returned more than 24 results for an exact title (the plugins keep the relevant hits only), no title-matching result came after position 24, and the requests fell by 1.3% (534 → 527). The two links missing with 24 came from a timeout and a failed request. The cap stays at 50.
- Timeouts at 30 s: kinoger in 18 of 31 searches (partly the probe's own load: its browser served two pages at a time) and kinoking in 21 of 31. megakino_to and movie4k answered with nothing (Cloudflare 522, see `stremio-latency.md`). A timed-out search counts as finding nothing, in every row of the table.

## Browser check (2026-10-04)

A probe in the production container opened 5 pages (kinoger, moflix, filmpalast, hdfilme, s.to) in its own Chromium and counted that browser's processes:

| Flags | Renderers | Renderer RSS (sum) |
|---|---|---|
| none | 7 | 1072 MB |
| `--renderer-process-limit=2` | 6 | 1011 MB |
| `--renderer-process-limit=2 --disable-site-isolation-trials` | 2 | 441 MB |

Frames of other sites keep their own process under site isolation, so the limit alone saves little. Without site isolation s.to's Turnstile gate failed in 2 of 2 tries (it passed in 2 of 2 with the limit alone), so site isolation stays on. kinoger's and moflix's Cloudflare challenges passed with every set. Playwright plugins with type-based resource blocking and blocked service workers returned what they returned before (ddlvalley 12 results, scnsrc 19, ddlspot none either way).

## After the measures (2026-10-04, staging cfb4c6e)

Measured the same way as the baseline, on Python 3.14.8 with uvloop, HTTP/2 on (`SCAVENGARR_HTTP_HTTP2=true`) and no other traffic.
- The first pass ran right after a restart.
- Before each warm pass the three titles' search-cache entries were deleted, so the plugins searched again, as in the baseline's warm passes.
- The last pass answered from the search cache.

| Pass | Wall | Python CPU | Chromium CPU | httpx requests | Streams |
|---|---|---|---|---|---|
| Cold (fresh container) | 13.9 s | 7.1 s | 29.8 s | 122 | 2.7 |
| Warm 1 (searched again) | 11.4 s | 2.5 s | 11.2 s | 61 | 4.7 |
| Warm 2 (searched again) | 11.2 s | 2.6 s | 11.5 s | 45 | 4.7 |
| From the search cache | 4.2 s | 0.9 s | 10.1 s | 2 | 4.7 |

Against the baseline's warm passes (means of 2 passes over 3 titles, so 6 requests each side):

| | Before | After |
|---|---|---|
| Wall per request | 13.9 s | 11.3 s (−18%); 4.2 s from the search cache (−70%) |
| Python CPU per request (process, native code included) | 4.1 s | 2.55 s (−38%) |
| Python CPU on the GIL per request (py-spy) | 2.49 s | 1.13 s (−55%) |
| Event-loop lag, p99 / max | ~130 / 384 ms | 40 / 60 ms |
| httpx requests per request | 60 | 53 |
| Streams per request | 5.15 | 4.7 |

The streams are within the spread: Breaking Bad gave 0–2 in both series. The cold pass went from 15.1 s, 8.1 s Python CPU and 144 requests to 13.9 s, 7.1 s and 122.

**Where the GIL time per warm request went** (py-spy, inclusive, 1496 samples before and 677 after; the rows overlap and do not add up):

| | Before | After |
|---|---|---|
| guessit/rebulk (release names) | 0.82 s | 0.00 s |
| `html.parser` | 0.67 s | 0.20 s |
| httpx/httpcore/h11/h2 | 0.40 s | 0.40 s |
| `http.cookiejar` | 0.13 s | 0.08 s |
| patchright (browser driver) | 0.18 s | 0.17 s |

**Regression found and fixed:** the first build after the measures kept 100 idle connections for 60 s. httpcore 1.0 scans its whole pool for every request and every finished response, and the scan is quadratic in the idle connections. It took 20% of the GIL samples of the first pass (2% before). With at most 20 idle connections (httpx's default count, commit cfb4c6e) it took 1.9–2.4%.

**HTTP/1.1 against HTTP/2** (A/B in the production container on the same build): the Stremio plugins searched 2 titles without the browser fallback, 3 runs per protocol in alternating order.

| | HTTP/1.1 | HTTP/2 |
|---|---|---|
| CPU of the run | 1.66–1.71 s | 2.03–2.11 s (+22%) |
| New connections | 48–49 | 43–44 |
| Wall | 35–40 s | 40 s (bounded by the slowest plugins' timeouts) |

HTTP/2's framing (h2, hpack, hyperframe) runs in Python and costs more CPU on the production host than the few handshakes it saves, and it is not faster. `http.http2` stays off.

**What is left**, by size:
- **Chromium:** ~10–11 s CPU per request even from the search cache, because hoster resolution runs on every request (stream URLs expire and some are IP-bound). A cold request also launches the browser and solves challenges (~30 s).
- **httpx/httpcore:** 0.4 s on the GIL per request. Part of that is HTTP/2, which is now off.
- **`html.parser`:** 0.2 s per request in plugins that still use it. They parse big pages in a worker thread (`_feed`).
- **patchright:** the driver records a stack trace (`traceback.extract_stack`) for every message it sends to the browser.
- **Log noise** in 15 min of tests: moflix logged 51 `moflix_http_error`; megakino_to and movie4k timed out (Cloudflare 522, origin down).

The moflix noise was people in its search answer (fixed in e9b1ba1). What could come next, evaluated with research on Stremio clients, other addons, HTTP clients, Chromium and Cloudflare: `optimization-options.md`.

## Playback under search load (2026-10-10, row 56)

**Question.** Does a search slow a playback that runs through the proxy on the production host? N12 of the ideas backlog proposed that the search runner lower its plugin and browser concurrency while a playback is active; the row asked for the evidence first.

**Setup.** Production (the container started 10:27 UTC from the 09:15 rebuild of staging 8a1dc84), measured with `scripts/playback_under_load.py` from the dev container through the reverse proxy, as a LAN player reaches it. The stream: the strmup HLS link of Der Schuh des Manitu that the tenth round had resolved minutes before (`prodctl.py probe links`), one variant, segments of 8 s and 1.3 to 4.9 MB (about 3.1 Mbit/s of video). Each run plays 20 segments at the playback pace (160 s of video); the proxy's read-ahead follows it (19 hits of 20 in every run, the first segment is the cold fetch). The load: one stream request 10 s into the playback, which goes on until 30 s after the answer. CPU: `docker stats` through Portainer about every 5 s (26 to 30 samples per run). Server view: the `/metrics` deltas of the run. Runs 1 to 3 between about 12:50 and 13:02 UTC, run 4 from 13:05:24 to 13:08:06 UTC. Runs 2 and 3 overlapped the four stream requests of another session's smoke test of `prodctl.py check` (about 12:55 to 13:05 UTC: three series, one movie, answered in 0.1 to 3.9 s), so run 2's plugin searches are not its own title's alone; run 4 ran on an idle production.

| Run | Request (`X-Cache`, answer, streams) | Segments (video) | Stalls | Headers p50 / p90 (ms) | Segment p50 / p90 / max (ms) | Mbit/s | CPU p50 / max (cores) |
|---|---|---|---|---|---|---|---|
| 1 idle | none | 20 (160 s) | 0 | 47 / 117 | 652 / 741 / 5201 | 30.3 | 0.00 / 0.27 |
| 2 Oppenheimer | HIT, 0.0 s, 7 | 20 (160 s) | 0 | 67 / 148 | 637 / 1069 / 4177 | 26.5 | 0.14 / 3.12 |
| 3 Dune: Part Two (control) | HIT, 0.1 s, 5 | 20 (160 s) | 0 | 57 / 112 | 660 / 828 / 5028 | 29.3 | 0.00 / 0.27 |
| 4 Everything Everywhere All at Once | MISS, 4.0 s, 5 | 20 (160 s) | 0 | 37 / 51 | 473 / 732 / 2056 | 42.5 | 0.01 / 0.62 |

| Run | `plugin_search` n / mean / p90 | Proxy segment stage n / mean / p90 | Read-ahead fetches n / mean | ok / hit / miss / failed | Loop lag p50 / p90 / max |
|---|---|---|---|---|---|
| 1 idle | 0 | 20 / 8 ms / ≤50 ms | 21 / 1.94 s | 21 / 19 / 1 / 0 | ≤5 / ≤5 / ≤5 ms |
| 2 Oppenheimer | 54 / 2.4 s / ≤4 s | 20 / 14 ms / ≤50 ms | 21 / 1.94 s | 21 / 19 / 1 / 0 | ≤5 ms / ≤5 ms / ≤0.5 s |
| 3 Dune: Part Two (control) | 2 / 15.3 s / over 15 s | 20 / 9 ms / ≤50 ms | 21 / 1.77 s | 21 / 19 / 1 / 0 | ≤5 / ≤5 / ≤10 ms |
| 4 Everything Everywhere All at Once | 12 / 3.1 s / ≤7 s | 20 / 9 ms / ≤50 ms | 21 / 1.88 s | 21 / 19 / 1 / 0 | ≤5 / ≤5 / ≤50 ms |

The quantiles of the server view are histogram bucket bounds; "max" is the highest bucket with an observation. Segment "max" of the player view is the first segment in every run, the cold fetch of 4.55 MB (2.1 to 5.2 s, the CDN's pace for a first request), the only segment near its duration; it is the start of a playback, and the loaded runs did not change it.

**What the load was.** Oppenheimer and Dune: Part Two were cache hits with `X-Search-Complete: false`, so their load came as completion searches in the background, not as a waiting request. Run 2 saw 54 plugin searches (mean 2.4 s, p90 ≤4 s) and the container at 3.12 cores peak (the browser plugins), its own completion and the smoke test's four requests together: the heavy case. Run 3 saw two plugin searches of 15 s, a second near-idle control. Run 4 is the clean case: a miss on an idle production, the request waited for the plugins (12 plugin searches, mean 3.1 s, p90 ≤7 s; 0.62 cores peak) and answered at 4.0 s with 5 streams.

**Reading.** The search does not slow the playback. Under the clean full search (run 4) the segments were as fast as idle (p50/p90 473/732 ms against 652/741 ms, headers 37/51 against 47/117 ms, loop lag ≤50 ms). Under the heavy burst (run 2) the playback paid about a third of a second at p90 (1.07 against 0.74 s), the slowest segment after the first took 2.64 s (headers after 0.39 s), the event loop lagged once between 0.25 and 0.5 s, and no segment came near its 8 s; the proxy's own segment stage stayed at 14 ms mean. The segments come from memory: the read-ahead had fetched them (19 hits of 20) while its CDN fetches took 1.8 to 1.9 s mean on their own, so the event loop's busy phases cost the player only the copy of a buffered segment. A player's buffer (Stremio keeps tens of seconds) hides a third of a second entirely. N12 is closed without a scheduling change: nothing in the runner's concurrency needs a playback signal.

**Limits.** One machine on the server's network as the player (the dev container, through the reverse proxy); one hoster's HLS (strmup) with 8 s segments, where the read-ahead hits; the first segment's cold fetch excluded; two loaded runs, one of them clean; CPU from one-second `docker stats` samples every 5 s (peaks between samples are missed). What would change the picture: segments of 2 to 4 s (the same third of a second is a larger share), a playback the read-ahead does not cover (a player jumping, a CDN that answered 429 and switched the read-ahead off for ten minutes), and a file through `/proxy/<id>/file`, where every byte passes the event loop while it is busy (not measured: the instrument plays HLS). A user report of stutter during a search would start there, with a `--file` mode of the instrument.

## Read-ahead depth (2026-10-11, row 68)

**Question.** Does the read-ahead keep enough segments in flight for FireStream's throughput from the production host? The maintainer's 1080p FireStream playback in Stremio Web (2026-10-11, 11:33 to 11:58 UTC) had no audio dropout in 25 minutes and after a seek, but its picture stalled now and then. Production's numbers for it: one connection carried about 0.44 of real time, the three in flight (the player's request and two segments ahead) about 1.3, below N1's rule of 1.5 times the bitrate.

**Before.** Production's read-ahead counters since the 10:58 UTC start, mostly that playback: 456 hits (from memory or awaited in flight), 111 misses, 123 dropped, 13 failed, 569 fetches in 8.45 s on average (`scavengarr_hls_readahead_seconds`). The player's answers through the proxy: p50 2.2 s, p90 9.6 s, max 27 s.

**Measurement.** `prodctl.py probe hls_throughput -- --imdb movie/tt0816692 --hoster firestream --in-flight 3,4,5 --segments 10`, from the production host at about 13:18 UTC (production on 69dcc83, nothing playing), one run of 183.8 s. The title: Interstellar on FireStream (CDN `firestream.to`), a media playlist with no variants (2561 segments of 4.0 s, the runs from segment 1250), bitrate 1.6 Mbit/s. No 429 and no failed request.

| Run | Connections | Bytes | Time | Mbit/s | × real time | Ranges |
|---|---|---|---|---|---|---|
| one connection | 1 | 8.7 MiB | 84.2 s | 0.9 (0.1–2.0 per segment) | 0.55 | – |
| read-ahead 2 | 3 | 7.1 MiB | 18.3 s | 3.2 | 2.04 | – |
| read-ahead 3 | 4 | 7.9 MiB | 14.0 s | 4.8 | 3.01 | – |
| read-ahead 4 | 5 | 6.9 MiB | 10.8 s | 5.4 | 3.39 | – |
| 3 ranges | 3 | 7.4 MiB | 40.4 s | 1.5 (1.2–2.1 per segment) | 0.97 | 30 of 30 answered 206 |
| read-ahead 2 × 3 ranges | 9 | 6.8 MiB | 11.7 s | 4.9 | 3.07 | 30 of 30 answered 206 |

**Reading.**
- The fourth connection adds about 1.0 times real time, the fifth only about 0.4. The sum flattens near 5 Mbit/s from the production host, and nine connections carry no more (4.9): a cap of the VPN or the CDN. A stream above about 3.3 Mbit/s (5 / 1.5) therefore reaches 1.5 times real time at no depth.
- FireStream lists no lower variants, so hls.js has nothing to step down to.
- N1's rule, connections = ⌈1.5 × bitrate / one connection's rate⌉: this title needs ⌈1.5 × 1.6 / 0.9⌉ = 3 (read-ahead 2, the old depth), the maintainer's playback ⌈1.5 / 0.44⌉ = 4 (read-ahead 3).
- One constant fits both measured FireStream playbacks: three segments ahead, four connections in flight. That is 3.01 times real time here and about 1.76 at 0.44 per connection there.
- Filemoon's CDN (one connection 34 times real time from the dev container, 2026-10-11) needs no depth of its own: a deeper read-ahead costs it one more segment in memory. So the depth stays one constant, not one per playback from its fetch times.

**Change.** `_AHEAD = 3` in `src/scavengarr/infrastructure/stremio/hls_proxy.py` (was 2). `_MAX_PLAYBACKS` (8) and `_MAX_AHEAD_BYTES` (16 MiB a segment) stay, so the worst case grows from 256 to 384 MiB (8 × 3 × 16 MiB; production used 407 MiB of 7.6 GiB). A 429 keeps its 10-minute pause. The probe's read-ahead runs follow the proxy's depth (`READ_AHEAD = 3`).

**After (2026-10-12, 7c23863, read-ahead 3).** The maintainer's Lanterns S01E02 on FireStream from 12:38 UTC to the rebuild at 13:16 (CDN `firestream.to`, a media playlist without variants, 781 segments of 4.0 s, bitrate 2.8 Mbit/s): per 5 minutes from 12:40, 42 to 78 segment requests, median 140 to 1229 ms (2.2 s before), p90 10.3 to 15.0 s (9.6 s before), max 19.5 to 28.6 s, 16 to 28 of them over 3 s. From 12:58:27 to 13:13:23 (896 s) the requests went from segment name 355 to 736; the names step by 2 per 4 s segment (181 of 226 steps), so 762 s of video in 896 s, 0.85 times real time, and 26 of the 227 requests repeated a segment about 10 s after its first request (the player gave up and asked again); 2 answered 502. `hls_readahead_total` since 10:39 (this and the earlier S01E01): hit 889, ok 915, miss 129, dropped 30, failed 32, cut 3. The probe on the same link right after the rebuild (13:22 to 13:26, nothing playing; `probe hls_throughput --stream-id <id> --segments 10 --in-flight 3,4,6`):

| Run | Connections | Bytes | Time | Mbit/s | × real time | Ranges |
|---|---|---|---|---|---|---|
| one connection | 1 | 13.4 MiB | 71.2 s | 1.6 (1.0–5.2 per segment) | 0.56 | – |
| read-ahead 2 | 3 | 13.4 MiB | 16.1 s | 7.0 | 2.46 | – |
| read-ahead 3 | 4 | 13.5 MiB | 21.5 s | 5.3 | 1.86 | – |
| read-ahead 5 | 6 | 13.2 MiB | 11.8 s | 9.4 | 3.33 | – |
| 3 ranges | 3 | 13.4 MiB | 49.5 s | 2.3 (0.6–4.1 per segment) | 0.80 | 30 of 30 answered 206 |
| read-ahead 3 × 3 ranges | 12 | 13.4 MiB | 30.1 s | 3.7 | 1.32 | 30 of 30 answered 206 |

**Reading (Mr. Fable).** N1's rule asks for ⌈1.5 × 2.8 / 1.6⌉ = 3 connections, and read-ahead 3 carried 1.86 times real time in the probe, yet the playback got 0.85: unless the maintainer paused, it stalled about 9 s a minute. The probe's runs vary (read-ahead 2 beat read-ahead 3), and six connections carried 9.4 Mbit/s, above 2026-10-11's flat 5 Mbit/s. Not known: whether the repeated requests cost a CDN fetch each (`take()` hands an in-flight fetch to the first request only), and how much the slowest of the segments in flight holds back the player, which needs them in order. The maintainer's answer whether it stuttered decides between a deeper read-ahead and a closer look at the repeats.

The paragraph that waited for this (the read-ahead metrics and the player's answer times during the maintainer's next FireStream playback after the rebuild) is the "After" above; a 1080p title is still to measure.

**Limits.** One title and one run from the production host, 10 segments per run; one connection's pace varied from 0.1 to 2.0 Mbit/s per segment. The maintainer's playback numbers come from production's logs and metrics, not from the probe.

**A 1080p playback without read-ahead (2026-10-12, 503a0eb, Mr. Fable).** The maintainer's 1080p playback through the proxy from 17:52:47 UTC (CDN `97bf1` as `prodctl` masks it, media playlist `index_1920x1080.m3u8`; 37 to 38 segment requests per 5 minutes with a full buffer, so segments of about 8 s): the player loaded 11 segments and paused (17:53:09 to 17:57:43). In the pause row 96's round fetched the media playlists of 12 other streams (17:55:57 to 17:57:41), so `SegmentReadAhead` forgot the playback with its segment order (more than `_MAX_PLAYBACKS`, 8, followed), and the player did not fetch the VOD playlist again until it opened the stream anew at 19:06:24. The 497 segment requests from 17:57:43 to 19:06:20 were fetched on demand: none was answered in under 46 ms (from memory: 9 ms), and `hls_readahead_total` since 17:42:29 read miss 537, hit 117, ok 199, dropped 87, failed 10, cut 1. Until 18:42 that held: 37 to 38 requests per 5 minutes, median 96 to 109 ms, one answer over 3 s (3.0 s at 18:26:50). Then the CDN slowed (only this stream used the proxy from 18:35 to 19:06): median 200 to 222 ms from 18:43, from 18:51:21 to 19:04:52 an answer of 2.0 to 4.4 s about once a minute (15), two answered 502 at the 15 s bound (18:55:10, 18:59:26, `hls_proxy_network_error`), and four were cut mid-body (18:52:08, 19:01:41, 19:03:27, 19:04:16: `httpx.ReadTimeout` out of `_body`, row 102). After the new start (19:06:21, the converter refused first) the read-ahead knew the playback: five slow answers in its first 1.5 minutes (1.8 to 14.2 s, two fetches ahead failed with `ReadTimeout`), then from 19:08 on 51 requests at a median of 8.6 ms, at most 0.8 s, none failed.

**Reading (Mr. Fable).** Three segments ahead give each fetch about 24 s before the player needs it, so the answers of 2.0 to 4.4 s would most likely have come from memory; whether the cuts and the 502s would have too is not known (a failed fetch ahead leaves the segment to the player's own request). The trigger was our own round during a pause, and a second viewer would do the same. This playback does not answer the 1080p question above: the read-ahead followed it for its first 11 segments and from 19:06:24 only. Fix: row 104 of `docs/plans/ideas-backlog.md` (the segment order outlives the fetches ahead).

**The picture's jumps (the maintainer's report of 2026-10-13, read by Mr. Fable).** The maintainer saw the picture jump ahead by one or more segments three times while the sound ran on, in an evening stream ("Obsession" over FireStream, from about 18:00 UTC). In the 1080p playback above four segments were cut mid-body: seg 416 (asked for at 18:51:44, cut at 18:52:08), 487 (19:01:14, 19:01:41), 494 (19:03:00, 19:03:27) and 498 (19:04:00, 19:04:16). None of them was asked for again: the player asked for the next segment at once, played the part it had and skipped the rest (up to 8 s each). The only segments asked for twice were 440 and 472 after their 502s, 505, 518 and seg 0 at the reopen. The segments' names (`seg_1920x1080_NNN.ts`) are those of a video rendition, so the sound most likely came from an audio playlist of its own whose segments were whole, which is why it ran on. The hoster is not certain: the playback's CDN (97bf1) and its segment names differ from FireStream's earlier ones (`seg-N-v1-a1.ts`, `cdn=firestream`).

**Reading (Mr. Fable).** A cut segment is a jump, not a stall: the player does not ask again. Row 102 (deployed 2026-10-13 07:10) resumes such a body once with a `Range` request when its answer promised a length, row 104 (07:24) keeps the read-ahead this playback had lost, and row 105 bounds a stall and restarts a segment that cannot be resumed with a range.

**The segments' stall bound (row 105, 2026-10-13, Mr. Normal).** A segment's request read with the shared client's 30 s (`http_timeout_seconds`): a CDN that fell silent held the player's segment, or a fetch ahead, 30 s before anything happened. httpx's read timeout is the wait for each read, the response's headers and the body's first byte included, so the bound to pick lies above the longest wait of a healthy CDN. Measurement from the dev container, 08:08 to 08:28 UTC: the links the dev server resolved for 13 titles, 25 consecutive segments per CDN and run fetched straight from the CDN one after another, as a player does; per segment the wait for the headers, for the body's first byte and the largest gap between two network reads (`aiter_raw()`), 474 segments on 11 CDNs (two runs; a CDN in both counts 50):

| CDN (hoster) | Segments | Headers, median / max | First byte, max | Largest gap, max |
|---|---|---|---|---|
| cloudwindow-route (VOE) | 50 | 0.04–0.05 / 0.25 s | 0.00 s | 0.30 s |
| gxplayer (GXPlayer) | 50 | 0.04–0.05 / 2.30 s | 0.02 s | 0.05 s |
| dramiyos-cdn (VidHide) | 25 | 0.16 / 0.33 s | 0.00 s | 0.14 s |
| acek-cdn (VidHide) | 24 of 25 (one 502) | 0.47 / 1.01 s | 0.00 s | 1.82 s |
| owphbf24 (Filemoon) | 50 | 0.04–0.07 / 0.63 s | 0.00 s | 0.19 s |
| r66nv9ed (Filemoon) | 50 | 0.04–0.05 / 0.36 s | 0.01 s | **16.51 s** (one segment), else 0.68 s |
| s1q2105 (Strmup) | 50 | 0.07 / 0.63 s | 0.00 s | 0.12 s |
| 97bf1 (Strmup) | 25 | 0.09 / 0.40 s | 0.00 s | 0.10 s |
| firestream (FireStream) | 50 | 0.32–2.23 / 6.83 s | 0.00 s | 0.06 s |
| vidsonic (Vidsonic) | 50 | 0.06 / 6.31 s | 0.00 s | 0.08 s |
| plauymito (Playmate) | 50 | 0.06–0.11 / 1.33 s | 0.00 s | 1.13 s |

Production beside it (41d6958, from 07:24:15 to about 08:10 UTC, `prodctl.py metrics --grep hls`): of 611 segment answers 598 started within 4 s and 13 later (the histogram ends at 4 s, so their maximum is not known); all 626 fetches ahead of whole segments ended within 15 s, 567 within 7 s.

**Reading.** A flowing body leaves at most 1.82 s between two reads, and the first byte follows the headers at once; the long waits are the headers, up to 6.83 s at FireStream. The one exception is a stall: r66nv9ed fell silent for 16.51 s in one segment's body and then finished it. The bound is 15 s (`_SEGMENT_READ_TIMEOUT_S`; Mr. Fable's pick against 20 s): 8 times the longest gap of a flowing body, 2.2 times the longest wait for headers, and it catches the 16.51 s silence, which a resume with a `Range` request ends about when it would have ended by itself. Files keep 60 s (`_FILE_READ_TIMEOUT_S`). Open: a segment whose headers take longer than 15 s now fails (`hls_proxy_network_error` with `error=ReadTimeout`) and the player asks again; production's 13 answers above 4 s show after the deploy whether that happens.

Live proof (dev server, 2026-10-13 08:30 to 08:41 UTC): two real HLS playbacks of 11 minutes each in parallel, each segment asked for at its playing time with 30 s of buffer, FireStream's Inception in 1080p (175 segments) and VOE's Dune: Part Two (58 segments): all 233 answered 200, read-ahead 231 hits and 2 misses (each playback's first segment), no resume, cut, retry or failed fetch ahead; FireStream's slowest answer, 14.15 s, was a hit awaiting a fetch ahead of a whole segment that flowed slowly and was not cut. FireStream's playback ran out of its 30 s buffer 3 times (when is not recorded; the first segments come from the CDN on demand, with FireStream's headers taking up to 6.83 s); VOE's never.

**Limits.** One morning, from the dev container's network (not the production host's VPN); 25 consecutive segments per link, one link per CDN; the gaps are those of `aiter_raw()`, which hands over what one socket read brought (at most 64 KiB).

## The capture's autoplay wait (2026-10-12, row 103)

**Question.** Every browser capture first waits `_MEDIA_AUTOPLAY_WAIT_S` (3 s, `_start_player` in `src/scavengarr/infrastructure/browser/stealth_pool.py`) for a player that starts itself, then clicks the player. A click the player does not take costs the next wait, `_MEDIA_CLICK_WAIT_S` (5 s). Row 96's production round (`docs/plans/challenge-reactions.md` → R7) had 10 Filemoon captures end at the 10 s bound without a mark, and 6 of its 7 streams at 5.9 to 10.3 s. Per hoster: does a shorter wait bring the stream sooner, without more clicks, popups or missed clicks?

**Measurement (dev container, 18:43 to 18:56 UTC).** `scripts/probes/capture_timing.py --autoplay-wait S --runs 2 --diagnose`, each invocation in a browser of its own: run 1 cold (a fresh context), run 2 warm (the same context). One link per capture resolver from `scripts/probes/resolver_urls.py` (collected at 18:42; Filemoon's is a Byse link) and one filemoon.to link. The waits 3, 1.5 and 0.5 s ran in two passes of opposite order; Filemoon's two hosts ran twice more at 3 and 1.5 s, and once each with `--restore` at 3 and 1.5 s (a run, then a cold control and a context restored from the kept state, each in a relaunched browser, as after a restart). A screening at 3 s found these six captures: VidHide's and Vidoza's links went no further than HTTP, and the other resolvers do not capture. The wait starts when the page's navigation ends (0.4 to 2.2 s in). Times are seconds after the resolution's start:

| Hoster | Wait | Cold: first click | Cold: stream | Warm: first click | Warm: stream | First click missed |
|---|---|---|---|---|---|---|
| Filemoon (Byse) | 3 s | 3.5 to 3.7 s | 4.4, 4.8, 5.4, 5.4, 6.6, 7.0 s | none | 1.3 to 1.4 s (4 runs); restored 1.8 s | 0 of 6 |
| | 1.5 s | 2.0 to 2.2 s | 2.9, 2.9, 3.5, 3.5, 4.3, 4.4 s | none | 1.3 to 1.4 s (4 runs); restored 1.9 s | 0 of 6 |
| | 0.5 s | 1.1 s | 7.3, 7.6 s | 0.9 s | 1.4, 1.4 s | 2 of 2 cold |
| Filemoon (filemoon.to) | 3 s | 3.9 to 4.0 s | 5.0, 5.8, 5.9, 6.5, 7.7, 8.4 s | none | 1.6 to 2.5 s (4 runs); restored 2.2 s | 0 of 6 |
| | 1.5 s | 2.4 to 2.5 s | 3.5, 4.3, 6.4, 7.0, 7.1, 13.0 s | none | 1.6 to 1.8 s (4 runs); restored 2.2 s | 0 of 6 |
| | 0.5 s | 1.4 s | 7.7, 8.7 s | 1.1 s | 1.7, 1.7 s | 2 of 2 cold |
| DoodStream | 3 s | 3.8 s | 3.8, 3.8 s | 3.5 s | 3.5, 3.6 s | 0 |
| | 1.5 s | 2.3 to 2.4 s | 2.3, 2.4 s | 2.0 s | 2.1, 2.1 s | 0 |
| | 0.5 s | 1.3 s | 1.3, 1.3 s | 1.0 to 1.1 s | 1.1, 1.1 s | 0 |
| Vixeo | 3 s | 3.7, 5.2 s | 3.7, 5.2 s | 3.6, 4.1 s | 3.6, 4.1 s | 0 |
| | 1.5 s | 2.2, 3.2 s | 2.3, 3.2 s | 2.2, 2.4 s | 2.2, 2.4 s | 0 |
| | 0.5 s | 1.3, 1.4 s | 1.3, 1.4 s | 1.1, 1.2 s | 1.1, 1.2 s | 0 |
| Dropload | 3 s | 3.5 s | 4.6, 4.6 s | none | 0.6, 0.6 s | 0 |
| | 1.5 s | 2.0 s | 8.0, 8.1 s | none | 0.6, 0.6 s | 2 of 2 cold |
| | 0.5 s | 1.0 s | 7.1, 7.1 s | none | 0.6, 0.6 s | 2 of 2 cold |
| SuperVideo | each | none | 0.7 to 0.9 s (6 runs) | none | 0.5 to 0.6 s (6 runs) | – |

No popup in the 88 runs with `--diagnose` (the 12 `--restore` runs ran without it).

**Reading.**
- **DoodStream and Vixeo take the first click whenever it comes.** The stream request follows the click within 0.1 s, cold and warm, so the wait is the stream's delay. With 0.5 s the stream came at 1.1 to 1.4 s, against 3.5 to 5.2 s with 3 s, without a missed click or a popup.
- **Filemoon's player starts itself when warm**, in the same context or restored from a kept state (production's case after row 96's restart). None of these 20 runs at 3 and 1.5 s clicked, and the stream came at 1.3 to 2.5 s, inside the wait; at 0.5 s a warm click at 0.9 to 1.1 s came first, with the stream at 1.4 to 1.7 s, as without it.
- **A cold Filemoon capture needs the click for its captcha**, which follows it 0.2 to 0.4 s later. With 1.5 s the first click came 1.5 s sooner and was taken in 12 of 12 cold runs. Byse's stream came at a median 3.5 s instead of 5.4 s. filemoon.to's came at 6.7 s instead of 6.2 s: its captcha's verify came at about the same time after the start whenever the click came (median 6.2 s with 1.5 s, 5.8 s with 3 s), once only at 12.5 s after two more clicks. With 0.5 s the click at 1.1 to 1.4 s came before the player took it (4 of 4 cold runs); the next click came 5 s later, and the stream at 7.3 to 8.7 s.
- **Dropload needs about 3 s when cold.** Its Turnstile frame shows at 0.6 s; a click at 2.0 or 1.0 s went unanswered (4 of 4 cold runs), and the stream came at 7.1 to 8.1 s against 4.6 s. Warm, its player starts itself at 0.6 s.
- **SuperVideo starts itself** at 0.5 to 0.9 s: its stream comes before the wait ends.

**What it means for the change (after the production host runs and Mr. Fable's go).** A wait per resolver, from the dev numbers: 0.5 s for DoodStream and Vixeo, 3 s kept for Dropload and SuperVideo. For Filemoon, 1.5 s helps a cold capture on one of its two hosts, and in the dev container it does not matter warm, production's case. Whether it matters on the production host is the production host runs' question: the idle production host's warm filemoon.to run of 17:49 (`docs/plans/challenge-reactions.md` → R4) clicked at 5.1 s and found the stream at 6.5 s, so there the warm player had not started itself 3 s after its page. Whether a click 1.5 s after the page brings it sooner there, or comes too early, the dev numbers cannot tell.

**Limits.** The dev container: a fast CPU, page loads of 0.2 to 2.1 s. One link per hoster, two to six runs per cell, within 13 minutes. A Raspberry Pi's page and player are slower, so a wait the player takes in the dev container can come too early there, and a missed click costs 5 s.

**On the production host (production 503a0eb, 19:51:08 to 19:52:58 UTC).** Ultracode's four calls on Mr. Fable's go, through `scripts/prodctl.py probe`, `--runs 2 --diagnose` each (run 1 cold, run 2 warm), with production's `capture_timing.py` of 503a0eb and only `--autoplay-wait` added: today's probe needs row 97's capture state, which 503a0eb lacks. The links are the dev runs'. Before each call the container had been up for 2 hours, its CPU was at 0.00 to 0.27 of 4 cores, and its log had no `/play`, `/proxy` or HLS line in the last 2 minutes; the maintainer's playback held the calls back from 19:04 until 19:49. Times are seconds after the resolution's start, a page as its start and its load:

| Hoster, wait | Run | Page | Clicks | `captcha`, `captcha/verify` | Stream | At 3 s (an earlier call) |
|---|---|---|---|---|---|---|
| Filemoon (Byse), 1.5 s | 1 (cold) | `/d/` 3.5 s, 1.9 s | 6.9, 13.0, 18.1 s | 14.1 s, 14.6 s | 22.7 s | 17:48: page 1.3 s, 0.8 s; clicks 5.2, 10.8, 15.9 s; captcha 7.2 s, no verify; no stream by 21.1 s |
| | 2 (warm) | `/d/` 2.0 s, 3.5 s | 7.1, 12.2, 17.4 s | – | 18.9 s | 17:48: page 0.6 s, 0.8 s; click 4.5 s; captcha 5.5 s, verify 6.8 s; stream 8.0 s |
| Filemoon (filemoon.to), 1.5 s | 1 (cold) | `/e/` 1.5 s, 1.8 s | 4.9, 9.9 s | 10.7 s, 11.3 s | 13.3 s | 17:49: page 1.3 s, 1.4 s; click 5.8 s; captcha 6.6 s, verify 8.0 s; stream 9.2 s |
| | 2 (warm) | `/e/` 0.6 s, 1.2 s | 3.4 s | – | 6.2 s | 17:49: page 0.8 s, 1.1 s; click 5.1 s; stream 6.5 s |
| DoodStream, 0.5 s | 1, 2 | none | – | – | none in the browser: resolved over HTTP in 1.0 and 0.3 s | none on the production host |
| Vixeo, 0.5 s | 1 (cold) | `/e/` 0.7 s, 1.2 s | 2.6 s | – | 3.0 s | 06:33: click 5.3 s, stream 5.5 s |
| | 2 (warm) | `/e/` 0.8 s, 2.3 s | 4.1 s | – | 5.9 s | 06:33: click 4.8 s, stream 5.0 s |

The 3 s calls are row 101's (`docs/plans/challenge-reactions.md` → R4, production 503a0eb) and row 83's (`docs/plans/header-bound-hosters.md` → Vixeo with the own-scripts rule, production 39f4790). No popup in the eight runs. The Filemoon and DoodStream calls overlapped no app request. Vixeo's call overlapped a real Stremio request from 19:52:46 on (two stream requests, a kinoger search page in the app's browser at 19:52:48, then Dropload's open breaker and six VOE resolutions over HTTP), most likely in its run 2. No app capture line fell into the calls' windows; the request's Byse capture timed out at 19:53:32, after them.

**Reading (production).**
- **Filemoon keeps 3 s.** At 1.5 s the player left the first click unanswered in 3 of 4 runs: no captcha and no stream before the next click, 5 s later. At 3 s, row 101's four runs answered it with the captcha or the stream. filemoon.to's pages loaded as at 17:49 (1.8 and 1.2 s against 1.4 and 1.1 s). Its cold capture's missed click put the stream at 13.3 s instead of 9.2 s, past production's 10 s resolve bound. Its warm capture's click was answered 1.7 s sooner, but the stream came only 0.3 s sooner (6.2 s against 6.5 s). Byse's pages started 1.4 to 2.2 s later and loaded 1.1 to 2.7 s slower than at 17:48, and its warm run left its second click unanswered too, 6.7 s after its page: its two runs also measure a slower site or host. Unlike in the dev container, Filemoon's warm player did not start itself on the production host, at 1.5 s here or at 3 s at 17:49, so production's warm captures need their click.
- **Vixeo takes the first click at 0.5 s on the production host**, in 2 of 2 runs, without a popup. Run 1, before the app's request, found its stream at 3.0 s, against 5.5 and 5.0 s at 3 s in the morning. Run 2's page loaded in 2.3 s instead of 1.2 s, and its stream came 1.8 s after the click (run 1: 0.4 s; the morning's runs: 0.2 s), at 5.9 s; it most likely ran during the app's request.
- **DoodStream did not capture on the production host.** The site sent the production host's request no Cloudflare challenge, so the resolver resolved the link over HTTP, opened no browser page, and the wait never began. Production's log since 503a0eb's deploy, to about 20:00 UTC, shows no `doodstream_cloudflare_browser_fallback` either (8 `doodstream_offline`, 2 `doodstream_captcha_required`, 1 `doodstream_invalid_video_base`). The dev container's requests meet the challenge, so the dev numbers are the only numbers for DoodStream's capture. A 3 s call would have taken the same HTTP path, so none was added.

**For the change (Mr. Fable's go per resolver).** By Mr. Fable's rule: 0.5 s for Vixeo, whose first click the production host answered; 3 s kept for Filemoon, Dropload and SuperVideo. DoodStream's click was not measured on the production host; while the production host's requests meet no challenge, its wait changes nothing in production.

**Limits (production).** One link per hoster and one call each, a few minutes after the maintainer's playback. The 3 s calls are other calls, 2 hours earlier for Filemoon and 13 hours earlier for Vixeo, perhaps with other links (the earlier links are not recorded), at other loads of the production host and the sites, as Byse's page times show. Vixeo's run 2 overlapped an app request.

**Production proof (4429aff, the change deployed with 41d6958).** One `capture_timing.py` call on the production host with the resolver's own wait (no `--autoplay-wait`), through `scripts/prodctl.py probe`, `--runs 2 --diagnose`, with the deployed probe (9466e3f) and the Vixeo link of the runs above. It waited for a quiet production host: the container up for 2 hours, its CPU at 0.00 of 4 cores, no `/play`, `/proxy` or HLS line in the last 2 minutes. The call took 18 s, 5.6 s of it the browser's launch, and its report named `autoplay wait: 0.5 s (the resolver's)`. Times are seconds after the resolution's start, a page as its start and its load:

| Run | Page | Click | Stream | At 3 s (row 83's call, production 39f4790) |
|---|---|---|---|---|
| 1 (cold) | `/e/` 0.9 s, 1.1 s | 2.7 s | 2.9 s (vidsonic.net) | click 5.3 s, stream 5.5 s |
| 2 (warm) | `/e/` 0.5 s, 1.2 s | 2.4 s | 2.6 s | click 4.8 s, stream 5.0 s |

No challenge, no popup, no leftover process, and no app line in the call's window.

**The app's Vixeo resolutions.** The registry's `duration_ms` covers the resolver and the playback check (`check_playable` runs before `hoster_resolve_success`), so it does not compare with the probe's stream times. The capture's own part is the `capture_state_saved` line's time less its read, against the resolution's start. One resolution on 4429aff, during a playback: its state's read (143 ms) was saved 3.36 s after the resolution began, so its stream came within 3.22 s (an upper bound: the store's write and any wait for a page are inside it), and `hoster_resolve_success` followed 3.11 s later (6472 ms, `challenge=None`). A missed first click could not have streamed before 5.5 s after the player's start. Two more on 4429aff streamed within 5.03 and 5.73 s (reads of 129 and 113 ms, 6713 and 7832 ms in total, both `challenge=None`): the first inside those 5.5 s, the second too unless its page had loaded within 0.23 s of the resolution's start (the probe's pages loaded at 1.1 and 1.2 s). An earlier one on 41d6958 took 5075 ms in total (`challenge=None`); its container's log is gone, so its capture part is unknown.

**Reading (production).** Vixeo's player takes the first click at 0.5 s on the production host, cold and warm: the stream followed the click by 0.2 s, 2.6 and 2.4 s sooner than with the 3 s wait. No capture missed its first click: 2 of 2 probe runs, and the app's three captures with a save line. The app's streams came within 3.22 to 5.73 s of their resolution's start, slower than the probe's 2.6 and 2.9 s on a quiet production host; the cause is not measured.

**Limits (production).** One call of two runs on one link, and three app captures with a save line, as upper bounds, their player's start unknown (the third's first click is inferred from the probe's page loads). The 3 s numbers come from another call, a day earlier, perhaps with another link.

## How to re-measure

`scripts/stremio_profile.py` measures the baseline titles, or the ids given, against a running container. Per request it reports:

- wall time;
- CPU of the Python and Chromium processes, read from `/proc` before and after;
- httpx requests per host, counted from the `HTTP Request:` lines of the JSON container log. httpx writes them at `logging.level: DEBUG` only (since I1, 2026-10-06), so the counts need a run at that level.

At the end it reports the event-loop lag from `/api/v1/stats/metrics`. `--repeat 2` runs a cold and a warm pass. `--py-spy` samples the app process on the production host and prints the CPU share per category; it needs `cap_add: [SYS_PTRACE]` on the container, and py-spy is installed into `/tmp` and run as root.

```bash
PORTAINER_URL=http://<pi-address>:9000 PORTAINER_API_KEY=… \
  poetry run python scripts/stremio_profile.py --portainer \
  --base https://scavengarr.example.lan --insecure \
  --connect-to scavengarr.example.lan:<pi-address> --repeat 2 --py-spy
```

On the Docker host, `--docker` uses the docker CLI instead of Portainer. A local server can be profiled with `py-spy record -r 100 -f raw -- python -m scavengarr.interfaces.cli …`. Compare runs only when they use the same titles and the same pass (cold or warm).
`scripts/playback_under_load.py` measures a playback through the proxy while the server searches: a paced player (one segment per `EXTINF` duration, as a player with a full buffer asks) fetches a stored HLS stream's segments through `/api/v1/stremio/proxy/<id>/scavengarr.m3u8` and times each (headers, last byte; a segment slower than its own duration is a stall), `--search` ids are requested during the playback (the searches' wall time, streams and cache headers), `--portainer` samples the container's CPU about every 5 s, and the `/metrics` deltas of the run give the server's view (the proxy's segment stage, the read-ahead's fetches and outcomes, the plugin searches' durations, the event-loop lag; quantiles are histogram bucket bounds). It runs from a machine on the server's network (the dev container reaches production through its reverse proxy) and prints no URLs:

```bash
poetry run python scripts/playback_under_load.py --base https://scavengarr.example.lan --insecure \
  --stream-id <id from prodctl.py probe links> --segments 20 --portainer --label idle
poetry run python scripts/playback_under_load.py --base https://scavengarr.example.lan --insecure \
  --stream-id <id> --segments 20 --portainer --label search --search movie/tt15398776
```

The rows are those of the tables under "Playback under search load"; compare runs of the same stream only (its segment length sets the pace), and run them while nothing else plays or searches.
