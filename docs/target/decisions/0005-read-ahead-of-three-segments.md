# 0005: The HLS proxy reads three segments ahead

> **Purpose:** Records why the HLS proxy reads three segments ahead.
> **Status:** target
> **As of:** 5b4303e4

## Context

The HLS proxy passes a playback's segments from the hoster's CDN to the player.
One connection from the production host to some CDNs carries less than the playback needs in real time, so a playback fed one segment at a time stalls.
Fetching the next segments while the player plays one keeps more connections in flight, at the cost of memory per playback.
The options were one constant depth for every playback, or a depth per playback derived from its fetch times.

## Decision

The proxy must fetch a constant three segments ahead of the player per playback (`_AHEAD`), so four connections are in flight with the player's own.
The depth follows the rule connections = ⌈1.5 × bitrate / one connection's rate⌉ for the slowest playbacks that were measured.
A CDN that is fast on one connection needs no depth of its own: the read-ahead costs it a few segments in memory.

## Consequences

- The read-ahead is bounded per process: `_MAX_PLAYBACKS` playbacks read ahead at once, and a segment above `_MAX_AHEAD_BYTES` is left to the player's own request.
- A CDN that answers a fetch ahead with 429 gets no fetches ahead for its host for `_THROTTLE_SECONDS`.
- A CDN that carries less per connection than the rule assumes can still stall a playback; a deeper or adaptive depth needs a new record.

## Status

accepted

## References

- `src/scavengarr/infrastructure/stremio/hls_proxy.py`: `SegmentReadAhead` and its constants.
- [`docs/reference/metrics.md`](../../reference/metrics.md): the read-ahead's metrics.
- [`docs/archive/plans/pi-performance.md`](../../archive/plans/pi-performance.md): the throughput per connection, the rule and what is not known.
