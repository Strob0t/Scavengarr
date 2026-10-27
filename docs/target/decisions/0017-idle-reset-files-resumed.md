# 0017: Files of CDNs that reset idle connections go through the file proxy, which resumes them

> **Purpose:** Records why a direct file whose CDN resets a connection that stops reading goes through Scavengarr's file proxy, and why that proxy resumes every proxied file after each interruption.
> **Status:** target
> **As of:** b6d2cc9b

## Context

A VEEV file reached the player as a `/play` redirect to the CDN ([0006](0006-direct-streams-proxied.md) kept it there, as a client-bound stream).
Stremio's streaming server fetches such a file through a proxy of its own, and its HLS converter reads the file at the pace of the playback.
A player whose buffer is full stops reading, the streaming server stops reading the CDN, and the CDN's connection sits with a closed receive window.
The VEEV CDN resets such a connection: 10 of 10 connections that stopped reading for 30, 60, 90, 120 or 300 s were reset, two at each length, at offsets spread over a 5.2 GB file.
A new connection with `Range` at the same offset answered 206 in 0.25 to 1.3 s in 10 of 10 cases, with the URL the connection had used and no new resolution.
Read without a pause, the same CDN delivered 2 × 20 minutes at 2.4 times the bitrate with no wait of 5 s or more: it does not stall a connection that is read.
The streaming server's proxy does not reconnect inside a response: its front proxy cut the request after 60 s without a byte, and the converter then produced segments without sound.
An address-bound file through the file proxy had the same reset in a pause of 120 s and of 300 s (Vinovo's CDN), and the proxy's one resume per response carried it on.

## Decision

A hoster resolver whose CDN resets a connection that stops reading must declare it (`idle_reset = True`), and the registry stamps the flag on the resolved stream.
Stremio gets such a file through `/api/v1/stremio/proxy/<id>/file`, as an address-bound one, and VEEV is the first hoster that declares it.
The file proxy resumes a proxied file after every interruption for as long as the response runs: a reset or a body that ends before the promised length is asked for again at once from the next byte, and a body that sends no byte for `stremio.file_stall_timeout_seconds` (10 s) is asked for again the same way.
A resume must start at the next byte (206 from that offset), otherwise it counts as a failed attempt.
At one offset at most three attempts run, after waits of 0, 1 and 2 s, each bounded by the stall timeout; the count starts again once bytes flow.
A refusal (403, 404, 410) during a resume resolves the hoster URL again once per interruption, past the resolver's cache, and the transfer goes on from the new URL with its headers.
After the third failed attempt the player's connection aborts (`FileCut`), and the player asks again with a range of its own.
The proxy keeps the headers the link was resolved with (the bound `User-Agent` and `Accept-Language` among them) for every request of the transfer.
Segments of HLS streams keep their one resume or restart.

## Consequences

- The flag lives on the resolver, as `address_bound` does: the resolver owns what its CDN does.
- The production host carries the bytes of every VEEV playback; a playback is bounded by one connection per player request, and the CDN semaphore is held only until an answer's headers.
- A restart or a deploy of Scavengarr, and an exit of the stall monitor with its switch on, cut a running VEEV playback, which the redirect left to the CDN before.
- Every proxied file gets the resume rules, address-bound ones included; a resume whose CDN needs longer than the stall timeout for its first byte gives up after three attempts, and the player's own new request gets the opening request's 60 s.
- The stall timeout must stay well under the 60 s a player's proxy waits for a byte: three attempts of 10 s and 3 s of waits end within 33 s.
- Resumes and give-ups are counted per hoster and reason (`scavengarr_file_resume_total`), with the time a resume took.
- A link stored before its resolver declared `idle_reset` keeps `/play` until its hoster resolves again.

## Alternatives

- A longer read timeout in the streaming server's front proxy: the CDN's connection is dead, so waiting longer delays the same cut.
- A reconnect in the streaming server's proxy: not this project's code.
- Reading ahead into memory during the pause: a 5-minute pause of a 6.6 Mbit/s file is about 250 MB per playback.
- One resume per response, as before: every pause longer than 30 s needs one.

## Status

accepted

## References

- `src/scavengarr/infrastructure/stremio/hls_proxy.py`: `stream_file`, `_cdn_body`.
- `src/scavengarr/infrastructure/hoster_resolvers/veev.py`: the VEEV resolver.
- `src/scavengarr/interfaces/api/stremio/router.py`: the `/proxy/<id>/file` route.
- [`openspec/changes/resume-idle-reset-files/proposal.md`](../../../openspec/changes/resume-idle-reset-files/proposal.md): the change, its evidence and its tasks.
- [`openspec/specs/stremio-file-proxy/spec.md`](../../../openspec/specs/stremio-file-proxy/spec.md): the file proxy's requirements.
- [0006](0006-direct-streams-proxied.md): address-bound files through the proxy.
