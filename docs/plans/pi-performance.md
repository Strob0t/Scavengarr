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

## How to re-measure

- **Wall time per plugin:** poll `/api/v1/stats/metrics` during one stream request. A plugin's search count rises when its search ends.
- **CPU:** read `/proc/<pid>/stat` for the Python and Chromium processes before and after the request (Portainer exec into the container).
- **Request counts:** count the `HTTP Request:` log lines of the request window.
- **Profile:** `py-spy record -r 100 -f raw -- python -m scavengarr.interfaces.cli …` on a local server. Startup imports are filtered out of the stacks.
- Use the same three titles before and after a change, so the numbers stay comparable.
