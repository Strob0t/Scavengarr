# 0006: Address-bound direct files go through Scavengarr's proxy

> **Purpose:** Records why address-bound direct files go through Scavengarr's proxy.
> **Status:** target
> **As of:** 5b4303e4

## Context

A resolved direct file (MP4 and the like) reached the player as a `/play` redirect to the CDN, while HLS went through Scavengarr's proxy.
The CDNs of DoodStream, MixDrop, Vinovo and FSST bind the file's URL to the address that resolved it.
A player that fetches the redirect from another address, a native Stremio app or a browser on another network, gets a refusal from these CDNs.
The maintainer decided that Scavengarr proxies these files too.

## Decision

A resolver whose CDN plays the URL only for the resolving address must declare it (`address_bound = True`), and the registry stamps the flag on the resolved stream.
Stremio gets such a file as `/api/v1/stremio/proxy/<id>/file`, a byte-range pass-through, instead of `/play`.
HLS keeps its playlist proxy, and every other direct file and VEEV keep `/play`.

## Consequences

- The flag lives on the resolver, not in a hoster list of the router: the resolver owns what its CDN does.
- The proxy passes the player's `Range` and `If-Range` and the CDN's status and range headers, so players can seek.
- A CDN refusal (403, 404, 410) gets one re-resolution and one retry, then 502.
- The production host carries these files' bytes, bounded by one connection per player request and the shared CDN semaphore.
- A file response in progress delays the app's stop until its connection ends (the shutdown limits of [`docs/current/architecture.md`](../../current/architecture.md)).
- Proxying every direct file is ruled out: the flag is set hoster by hoster, on evidence.

## Status

accepted

## References

- `src/scavengarr/infrastructure/stremio/hls_proxy.py`: `stream_file`.
- `src/scavengarr/interfaces/api/stremio/router.py`: the `/proxy/<id>/file` route.
- [`openspec/specs/stremio-file-proxy/spec.md`](../../../openspec/specs/stremio-file-proxy/spec.md): the requirements.
- [`openspec/changes/archive/2026-10-12-proxy-direct-streams/design.md`](../../../openspec/changes/archive/2026-10-12-proxy-direct-streams/design.md): the design and its alternatives.
- [`openspec/changes/archive/2026-10-12-proxy-direct-streams/proposal.md`](../../../openspec/changes/archive/2026-10-12-proxy-direct-streams/proposal.md): the evidence and the maintainer's decision.
