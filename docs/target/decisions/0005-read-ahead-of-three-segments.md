# 0005: The HLS proxy reads three segments ahead

> **Purpose:** Records why the HLS proxy reads three segments ahead.
> **Status:** target
> **As of:** 9d2fc4c7

## Context

The HLS proxy passes a playback's segments from the hoster's CDN to the player.
One connection from the production host to some CDNs carries less than the playback needs in real time, so a playback fed one segment at a time stalls.
Fetching the next segments while the player plays one keeps more connections in flight, at the cost of memory per playback.
From the production host one connection to a FireStream CDN carried 0.55× real time.
Three segments ahead (4 connections) carried 3.01×, 5 connections 3.39×, three byte ranges per segment 0.97×, and two segments ahead of three ranges each (9 connections) 3.07×.
The sum flattens near 5 Mbit/s, and a playback at 0.44× per connection needs 4 connections by the rule below.
Filemoon's CDN carried 34× on one connection from the dev container.
A later playback with three segments ahead carried 0.85×, and why is not known.
The options were one constant depth for every playback, or a depth per playback derived from its fetch times.

## Decision

The proxy must fetch a constant three segments ahead of the player per playback (`_AHEAD`), so four connections are in flight with the player's own.
The depth follows the rule connections = ⌈1.5 × bitrate / one connection's rate⌉ for the slowest playbacks that were measured.
A CDN that is fast on one connection needs no depth of its own: the read-ahead costs it a few segments in memory.

## Consequences

- The read-ahead is bounded per process: `_MAX_PLAYBACKS` playbacks read ahead at once, and a segment above `_MAX_AHEAD_BYTES` is left to the player's own request.
- A CDN that answers a fetch ahead with 429 gets no fetches ahead for its host for `_THROTTLE_SECONDS`.
- A CDN that carries less per connection than the rule assumes can still stall a playback; a deeper or adaptive depth needs a new record.

## Alternatives

- A depth per playback from its fetch times: the fastest CDN needs no depth of its own, and a deeper read-ahead costs it only one more segment in memory.
- Parallel byte ranges per segment: a first range per segment must arrive before the rest can start, nothing is gained on CDNs that ignore ranges, and the read-ahead delivered more.
- Both together: nine connections per viewer for nothing beyond the read-ahead.
- Nothing: the stutter would have stayed, documented as a limit of the client.

## Status

accepted

## References

- `src/scavengarr/infrastructure/stremio/hls_proxy.py`: `SegmentReadAhead` and its constants.
- [`docs/reference/metrics.md`](../../reference/metrics.md): the read-ahead's metrics.
