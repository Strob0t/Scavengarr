## Why

A VEEV file plays through `/play`, a redirect to its CDN, and Stremio's streaming server fetches it through a proxy of its own whose HLS converter reads at the pace of the playback.
When the player's buffer is full the reading stops, the CDN's connection sits with a closed receive window, and the VEEV CDN resets it.
The streaming server's proxy does not reconnect inside a response: its front proxy cuts the request after 60 s without a byte, and the converter then produces segments without sound.

Measured from the production host (probes in the maintainer's untracked notes, no title or time here):
- A steady read at 2.4 times the bitrate, 2 × 20 minutes: no wait of 5 s or more, so the CDN does not stall a connection that is read.
- Pauses of 30, 60, 90, 120 and 300 s without reading, two each at offsets spread over a 5.2 GB file: 10 of 10 connections were reset (TCP state closed at the pause's end, only the kernel's buffer delivered after it).
- A new connection with `Range` at the same offset after each pause: 206 in 0.25 to 1.3 s, 10 of 10, with the URL in use and no new resolution, no 403 or 429.
- The file proxy's own leg (a client of Scavengarr pausing 120 s and 300 s on an address-bound Vinovo file): the client's connection held, data came within 5 s after the resume, and the Vinovo CDN had reset the proxy's upstream connection in both pauses, which the proxy's one resume per response carried on.
- Caddy's defaults (no read or write timeout, no transport timeouts, `encode` limited to text-like types) and uvicorn's flow control (a write waits without a limit; the keep-alive timer only between requests) cut no paused response.

The maintainer chose option A: VEEV through the file proxy, which resumes at the current offset after every interruption ([0017](../../../docs/target/decisions/0017-idle-reset-files-resumed.md)).

## What Changes

- **A new resolver flag.** `idle_reset = True` declares a CDN that resets a connection that stops reading. The registry stamps it on `ResolvedStream`, `CachedStreamLink` keeps it, and the stream builder sends such a file through `/api/v1/stremio/proxy/<id>/file`. VEEV declares it.
- **Resumes per interruption.** The file proxy resumes a proxied file after every reset, early end and stall, for the whole response, instead of once:
  - a reset or an early end: a `Range` request from the next byte at once;
  - a stall: no byte for `stremio.file_stall_timeout_seconds` (default 10 s, 1 to 30) once the answer has sent its first byte;
  - at one offset at most 3 attempts, after waits of 0, 1 and 2 s, each bounded by the stall timeout; the count starts again once bytes flow;
  - a refusal (403, 404, 410) during a resume: one new resolution per interruption, past the resolver's cache, then the new URL and headers;
  - the third failed attempt aborts the player's connection (`FileCut`).
- **Telemetry.** `scavengarr_file_resume_total{hoster,reason,outcome}` (`reason`: `reset`, `eof`, `stall`; `outcome`: `resumed`, `gave_up`) and `scavengarr_file_resume_seconds{hoster,reason}`; `hoster` is a resolver name or `other`.
- **Unchanged.** HLS segments keep their one resume or restart; the opening request of a file keeps its 60 s read timeout; address-bound files keep the proxy.

## Impact

- Affected specs: `stremio-file-proxy`.
- Affected code: `src/scavengarr/infrastructure/stremio/hls_proxy.py`, `src/scavengarr/interfaces/api/stremio/router.py`, `src/scavengarr/application/stremio/stream_builder.py`, `src/scavengarr/domain/entities/stremio.py`, `src/scavengarr/infrastructure/persistence/stream_link_cache.py`, `src/scavengarr/infrastructure/hoster_resolvers/registry.py`, `src/scavengarr/infrastructure/hoster_resolvers/veev.py`, `src/scavengarr/infrastructure/config/schema.py`, `src/scavengarr/domain/ports/telemetry.py`, `src/scavengarr/infrastructure/telemetry/metrics.py`.
- Operation: the production host carries every VEEV playback's bytes; a restart or deploy of Scavengarr cuts a running VEEV playback.
