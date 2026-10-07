# Change: Proxy the direct streams of address-bound hosters

## Why

A resolved direct file (MP4 and the like) reaches the player as a `/play/`
redirect to the CDN, while HLS goes through Scavengarr's proxy. Step 24 of
the round-2 handoff played production's 97 cached streams from the dev
container (`docs/plans/stremio-latency.md`, seventh round): the HLS proxy
played 52 of 54, the `/play/` redirects failed 36 of 37. DoodStream answered
`error_wrong_ip` 13 times, FSST HTTP 410 11 times, Vinovo 403 7 times,
MixDrop 403 5 times; only VEEV, resolved per player, played. These CDNs bind
the URL to the address that resolved it, the Pi's VPN exit. A player that
fetches the redirect from another address, a native Stremio app or a browser
on the home connection, gets a dead stream from four hosters. The maintainer
decided on 2026-10-07: "proxy sie auch durch scavengarr".

## What Changes

- **A resolver declares an address-bound CDN** with the class attribute
  `address_bound = True`, like `needs_playback_check` today; the registry
  stamps it on the resolved stream, the stored link record keeps it.
  DoodStream, MixDrop, Vinovo and FSST set it.
- **Address-bound direct streams are proxied.** The stream builder gives them
  `/api/v1/stremio/proxy/<id>/file` instead of `/play/<id>`; HLS keeps its
  playlist proxy, every other direct stream and VEEV keep `/play`.
- **A byte-range pass-through.** The new route forwards the player's `Range`
  and `If-Range`, sends the stored headers over the default browser
  User-Agent with identity encoding, and passes the CDN's status, content
  type, `Content-Length`, `Content-Range` and `Accept-Ranges` back with CORS,
  streaming the body in 64 KiB pieces, HEAD without a body. A CDN refusal
  (403, 404, 410) gets one re-resolution, as playlists do, then 502.
- **Telemetry** on the existing `hls_proxy` stage with the kind `file` and
  the bytes counter; the metric names stay (dashboards and alerts).
- **A file mode of the throughput probe** measures one connection against
  three byte ranges on a stored direct file from the Pi, so a read-ahead for
  files, if the numbers ask for it, is its own later change.
- **Acceptance in production:** after the deploy, the round runner from the
  dev container plays the four hosters' streams.

No new configuration field. The `/play/` route is unchanged.

## Impact

- Affected specs: `stremio-file-proxy` (new capability).
- Affected code: `domain/entities/stremio.py` (`ResolvedStream.address_bound`,
  `CachedStreamLink.address_bound`), `infrastructure/hoster_resolvers/
  registry.py` (stamping), `doodstream.py`, `mixdrop.py`, `vinovo.py`,
  `fsst.py` (the attribute), `application/stremio/stream_builder.py` (the
  URL), `infrastructure/stremio/hls_proxy.py` (`stream_file`),
  `interfaces/api/stremio/router.py` (the route, the counted body, the
  refusal path), `scripts/probes/hls_throughput.py` (`--file`), tests (unit
  with respx, router, e2e), docs (`stremio-addon.md`, `hoster-resolvers.md`,
  `observability.md`, `AGENTS.md` §7, `CHANGELOG.md`).
- Load: the Pi carries the bytes of those streams, as it does for HLS; one
  CDN connection per player request, under the shared per-domain rate
  limiter and `_CDN_SEMAPHORE`. CPU per MB as measured for the HLS proxy
  (25 ms per MB in 64 KiB pieces).
- Compatibility: stored links from before the change have no flag and keep
  `/play`; the next resolution of such a hoster stores the flag.
