# Performance on the Raspberry Pi: Baseline

Status: measured 2026-10-04 (v0.2.3). Purpose: a baseline to judge optimization proposals against (for example from a literature review), and the candidate measures the numbers point to. Nothing here is implemented yet.

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
3. **HTML parsing.** Use selectolax (lexbor) for the hottest parsers, and move big pages off the event loop (AGENTS.md already asks for `run_in_executor`).
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
| Event loop | `uvicorn[standard]` (uvloop, httptools), eager tasks and Python 3.14, all in one step |
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

## How to re-measure

`scripts/stremio_profile.py` measures the baseline titles, or the ids given, against a running container. Per request it reports:

- wall time;
- CPU of the Python and Chromium processes, read from `/proc` before and after;
- httpx requests per host, counted from the `HTTP Request:` lines of the JSON container log.

At the end it reports the event-loop lag from `/api/v1/stats/metrics`. `--repeat 2` runs a cold and a warm pass. `--py-spy` samples the app process on the Pi and prints the CPU share per category; it needs `cap_add: [SYS_PTRACE]` on the container, and py-spy is installed into `/tmp` and run as root.

```bash
PORTAINER_URL=http://192.168.88.2:9000 PORTAINER_API_KEY=… \
  poetry run python scripts/stremio_profile.py --portainer \
  --base https://scavengarr.lan --insecure \
  --connect-to scavengarr.lan:192.168.88.2 --repeat 2 --py-spy
```

On the Docker host, `--docker` uses the docker CLI instead of Portainer. A local server can be profiled with `py-spy record -r 100 -f raw -- python -m scavengarr.interfaces.cli …`. Compare runs only when they use the same titles and the same pass (cold or warm).
