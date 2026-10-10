# Performance on the Raspberry Pi: Baseline

Status: baseline measured 2026-10-04 (v0.2.3). The decided measures are implemented and were measured again the same day (staging cfb4c6e, see [After the measures](#after-the-measures-2026-10-04-staging-cfb4c6e)). Purpose: a baseline to judge optimization proposals against (for example from a literature review), the candidate measures the numbers point to, and their effect.

## Setup

- Raspberry Pi 4 (4× Cortex-A72, 8 GB RAM, Debian 13, aarch64), Python 3.12.15, Scavengarr in Docker. All traffic goes through a gluetun VPN container; DNS is gluetun's resolver on 127.0.0.1.
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
  - host: 5.0 of 7.8 GB available, swap unused, load average 2.4 (37 containers on the Pi).
- **DNS through gluetun:** 2 ms when cached, 34–42 ms uncached.

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
- It ran on x86, not ARM. The Pi 4's Cortex-A72 has no ARMv8 crypto extensions, so TLS handshakes and AES should weigh more there.
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

A literature review proposed measures for fan-out, tail latency, caching, Python and the Pi. It is the gitignored `.devdata/research/performance-deep-research.md`: Tail at Scale, Kwiken, SRE Book, RFC 5861, Mercator, uvloop/httptools, selectolax and Pi/Docker sources. Its proposals, checked against this baseline, production and the code:

| Proposal | Verdict |
|---|---|
| `httpx.Limits` (keep-alive 60 s, 100 slots) and a separate connect timeout | Confirmed: handshakes are 21% of the CPU; the client uses the defaults |
| `uvicorn[standard]` (uvloop, httptools) | Confirmed: the event loop takes 14% |
| Stremio stream cache with stale-while-revalidate and single-flight | Confirmed: production served the same title twice within 17–18 s (Avengers: Endgame, Severance), a full fan-out each time |
| Partial results when a plugin hits its timeout | Confirmed in code: `_run_plugin_with_timeout` returns `[]`, so a slow plugin's finished detail pages are lost (kinoking is often cut at 10 s) |
| Fewer requests per stream request | Confirmed: two title variants double the fan-out ("Matrix" and "The Matrix": 205 requests against 105 for Interstellar) |
| Resolve DNS once and connect to the IP the address guard checked | Confirmed in code: the guard and httpcore both resolve. The time saved is small, because gluetun caches (2 ms). It does close a DNS-rebinding gap in the SSRF guard |
| selectolax instead of `html.parser`, parsing off the event loop | Confirmed at 9%; profile on the Pi first |
| Early stop (`resolve_target_count > 0`) | Conflicts with the decision of 2026-10-01 (complete answers over an earlier end); needs the maintainer |
| Token bucket "serializes every request per domain" | Overstated: it sleeps only when the bucket is empty, and then the spacing is the configured rate (10 rps, burst 10, AIMD up to 50). The `co.uk` key problem is real but hits no current site |
| `cf_clearance` does not work with httpx (TLS fingerprint), use curl_cffi | Refuted for kinoger and moflix: httpx with the browser's cookies and User-Agent passes both from the VPN IP, and curl_cffi did no better (antibot-patchright.md Phase 4) |
| Memory cgroup disabled on the Pi | Already fixed: `cgroup_enable=memory cgroup_memory=1` in the kernel command line, the cgroup reports 625 MB |
| Cooler and CPU governor | No throttling seen: 54 °C at 1.8 GHz (the maximum), governor `ondemand`; one sample, Pi 4 |
| Docker's embedded DNS | Not used: Scavengarr runs in gluetun's network namespace and uses its caching resolver |
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

**Python CPU on the Pi** (py-spy, threads holding the GIL, 1499 samples in the warm passes, ~15 s CPU):

| Inclusive | Warm | Cold + warm | Biggest callers |
|---|---|---|---|
| guessit/rebulk (release-name parsing) | 32.9% | 24.7% | `title_matcher._extract_title_candidates` 11.1%, `_extract_result_year` 3.7%, `release_parser.parse_quality` 3.6%, `_language_from_guessit` 2.9%: the same release title parsed several times |
| `html.parser` | 26.6% | 20.9% | filmpalast detail pages 8.4%, hdfilme 5.9%, kinox 2.6%, megakino 2.0%, movie2k 1.5%, s.to 1.4% |
| event loop (asyncio/anyio/selectors) | 12.6% | 15.5% | |
| httpx/httpcore/h11 | 8.7% | 13.5% | |
| `http.cookiejar` | 4.7% | 6.2% | |

- TLS shows up with only 0.7–1.5% here: in GIL mode py-spy misses the handshakes, because OpenSSL releases the GIL. They still block the event loop thread; locally, without GIL mode, they were 21%.
- The ranking on the Pi differs from the x86 profile above: release-name parsing and HTML parsing take about 60% of the Python CPU.
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
| Parsing | Big pages off the event loop first, then selectolax for the hotspots the Pi profile shows |
| Browser | `serviceWorkers="block"`, blocking by resource type everywhere, `--renderer-process-limit=2`, restart the browser after N solves. Done; the restart counts stealth pages (200), and site isolation stays on (check below) |
| DNS/SSRF | Connect to the IP the address guard checked (one lookup, closes the DNS-rebinding gap). Done: `GuardedNetworkBackend` |
| Pi host | No change |

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

HTTP/2's framing (h2, hpack, hyperframe) runs in Python and costs more CPU on the Pi than the few handshakes it saves, and it is not faster. `http.http2` stays off.

**What is left**, by size:
- **Chromium:** ~10–11 s CPU per request even from the search cache, because hoster resolution runs on every request (stream URLs expire and some are IP-bound). A cold request also launches the browser and solves challenges (~30 s).
- **httpx/httpcore:** 0.4 s on the GIL per request. Part of that is HTTP/2, which is now off.
- **`html.parser`:** 0.2 s per request in plugins that still use it. They parse big pages in a worker thread (`_feed`).
- **patchright:** the driver records a stack trace (`traceback.extract_stack`) for every message it sends to the browser.
- **Log noise** in 15 min of tests: moflix logged 51 `moflix_http_error`; megakino_to and movie4k timed out (Cloudflare 522, origin down).

The moflix noise was people in its search answer (fixed in e9b1ba1). What could come next, evaluated with research on Stremio clients, other addons, HTTP clients, Chromium and Cloudflare: `optimization-options.md`.

## How to re-measure

`scripts/stremio_profile.py` measures the baseline titles, or the ids given, against a running container. Per request it reports:

- wall time;
- CPU of the Python and Chromium processes, read from `/proc` before and after;
- httpx requests per host, counted from the `HTTP Request:` lines of the JSON container log. httpx writes them at `logging.level: DEBUG` only (since I1, 2026-10-06), so the counts need a run at that level.

At the end it reports the event-loop lag from `/api/v1/stats/metrics`. `--repeat 2` runs a cold and a warm pass. `--py-spy` samples the app process on the Pi and prints the CPU share per category; it needs `cap_add: [SYS_PTRACE]` on the container, and py-spy is installed into `/tmp` and run as root.

```bash
PORTAINER_URL=http://<pi-address>:9000 PORTAINER_API_KEY=… \
  poetry run python scripts/stremio_profile.py --portainer \
  --base https://scavengarr.lan --insecure \
  --connect-to scavengarr.lan:<pi-address> --repeat 2 --py-spy
```

On the Docker host, `--docker` uses the docker CLI instead of Portainer. A local server can be profiled with `py-spy record -r 100 -f raw -- python -m scavengarr.interfaces.cli …`. Compare runs only when they use the same titles and the same pass (cold or warm).
`scripts/playback_under_load.py` measures a playback through the proxy while the server searches: a paced player (one segment per `EXTINF` duration, as a player with a full buffer asks) fetches a stored HLS stream's segments through `/api/v1/stremio/proxy/<id>/scavengarr.m3u8` and times each (headers, last byte; a segment slower than its own duration is a stall), `--search` ids are requested during the playback (the searches' wall time, streams and cache headers), `--portainer` samples the container's CPU about every 5 s, and the `/metrics` deltas of the run give the server's view (the proxy's segment stage, the read-ahead's fetches and outcomes, the plugin searches' durations, the event-loop lag; quantiles are histogram bucket bounds). It runs from a machine on the server's network (the dev container reaches production through its reverse proxy) and prints no URLs:

```bash
poetry run python scripts/playback_under_load.py --base https://scavengarr.lan --insecure \
  --stream-id <id from prodctl.py probe links> --segments 20 --portainer --label idle
poetry run python scripts/playback_under_load.py --base https://scavengarr.lan --insecure \
  --stream-id <id> --segments 20 --portainer --label search --search movie/tt15398776
```

The rows are those of the tables under "Playback under search load"; compare runs of the same stream only (its segment length sets the pace), and run them while nothing else plays or searches.
