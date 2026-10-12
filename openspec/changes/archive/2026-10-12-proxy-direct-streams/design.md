# Design: Proxy the direct streams of address-bound hosters

## Context

`build_stream_from_resolved` (`application/stremio/stream_builder.py`)
decides on `resolved.is_hls` alone: HLS gets `/proxy/<id>/scavengarr.m3u8`,
everything else `/play/<id>` with `proxyHeaders`. `/play` (`interfaces/api/
stremio/router.py`) looks the link up in `StremioLinks`, re-resolves a stale
one, and answers a 302 to the CDN; it never sees the CDN's answer. The HLS
proxy fetches playlists and segments with the stored headers over a default
browser User-Agent (`_player_headers` in `infrastructure/stremio/
hls_proxy.py`), forwards nothing from the player, answers 200 with the
content type and CORS only, and counts bytes through `_counted`. Playlist
refusals (403, 404, 410) get one re-resolution (`StremioLinks.after_refusal`).
The stored record `CachedStreamLink` holds the video URL, its headers as
JSON, `is_hls` and `resolved_at`, keyed by the first 32 hex characters of the
hoster URL's SHA-256, for `stremio.stream_link_ttl_seconds`. Resolvers
declare properties as duck-typed class attributes (`needs_playback_check`).

## Goals / Non-Goals

Goals: the four hosters' streams play from any address; the proxy honours
byte ranges so players seek; no new configuration; the metrics keep their
names; a number for the per-connection throughput of files before any
read-ahead for them.

Non-goals: proxying every direct stream (the flag is set by evidence, hoster
by hoster); read-ahead or parallel ranges for files (measured first, then
its own change); per-player resolution through the proxy (VEEV stays on
`/play`); transcoding.

## Decisions

1. **The flag lives on the resolver and travels with the stream.** A
   resolver class sets `address_bound = True`; the registry reads it where it
   reads `needs_playback_check` and stamps `ResolvedStream.address_bound`
   (default `False`) as it stamps `resolved_at`. `with_resolution` stores it
   in `CachedStreamLink.address_bound` (default `False`), so `/proxy/<id>/
   file` and the builder agree without a second lookup, and a record from
   before the change reads as not bound. Alternative rejected: a set of
   hoster names in the router; the resolver owns what its CDN does.

2. **The builder's branch.** `is_hls` → playlist proxy (unchanged);
   `address_bound` and not HLS → `/api/v1/stremio/proxy/<id>/file` with the
   same behaviour hints as the HLS proxy stream and no `proxyHeaders` (the
   proxy adds the headers); else `/play` (unchanged). The fixed file name
   `file` sits next to `HLS_MASTER` so `_proxy_link` can tell the two apart.

3. **`stream_file` next to `stream_hls_segment`.** It builds the CDN request
   with `_player_headers(stored)`, `Accept-Encoding: identity` (byte offsets
   must hold, so no decoding), and the player's `Range` and `If-Range` when
   present; sends it with `stream=True` and `follow_redirects=True` under
   `_CDN_SEMAPHORE` until the headers arrive; returns the CDN's status, the
   headers to pass (`Content-Type`, `Content-Length`, `Content-Range`,
   `Accept-Ranges`) and the chunk iterator (`_SEGMENT_CHUNK`). HEAD: the GET
   is closed after the headers, as for segments. The first byte of a Vinovo
   file can take about 30 s (its resolver's docstring): the read timeout of
   the file request is 60 s (a constant), the connect timeout the client's.

4. **The route.** `GET`/`HEAD /api/v1/stremio/proxy/{stream_id}/file`: 404
   without a link, 400 when the link is HLS or not address-bound (the
   builder never emits the URL for those), 503 without the repository;
   `links.current(link)` for a stale link, as the master does; on a CDN
   403, 404 or 410 one `after_refusal` re-resolution and one retry, then
   502; a 416 passes through; a `StreamingResponse` with the CDN's status
   and the passed headers plus CORS, the body through `_counted`. The route
   is under `/proxy/`, so the rate-limit exemption and the query masking
   already cover it.

5. **Telemetry.** The `hls_proxy` stage with `kind="file"` and the outcome
   as the status string; `hls_proxy_bytes{kind="file"}`. The stage's name is
   historical; renaming the metrics would break the dashboard and the
   alerts, so the docs name the kind instead.

6. **Measuring before any read-ahead for files.** `scripts/probes/
   hls_throughput.py --file <stream id>` fetches 32 MiB of a stored direct
   file from the Pi in one connection, then as three parallel 1 MiB-step
   ranges, and prints Mbit/s and whether the CDN honoured the ranges. The
   numbers go to the backlog's row of this change. Players read files
   sequentially in one connection, so the per-connection throttle of
   finding 7 applies here too; a parallel-range read-ahead for files would
   be a change of its own, decided by these numbers.

## Risks / Trade-offs

- Bandwidth and CPU on the Pi for four more hosters' streams: bounded by one
  connection per player request and the shared semaphore; measured for HLS
  at 25 ms CPU per MB.
- A CDN that ignores `Range` answers 200 with the whole file; the proxy
  passes that through and the player reads from the start. Seeking then
  costs a full re-read; the probe's "honoured" column says which CDNs do it.
- Stremio's streaming server fetches the proxy URL through its own client;
  if it sends no `Range`, the proxy sends none, which is today's `/play`
  behaviour minus the address problem.

## Migration

None. The flag defaults to `False` in both entities; old stored links keep
`/play` until the hoster is resolved again. After the deploy: the round
runner from the dev container (`scripts/stremio_round.py`) shows the four
hosters' streams playing, recorded under the seventh round's note in
`docs/plans/stremio-latency.md`.
