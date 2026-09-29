[← Back to Index](../features/README.md)

# Plan: Stremio Response Time and Playable Streams

**Status:** In progress (started 2026-09-29)
**Priority:** High (Stremio is the main use case; cold answers take 17–38 s)
**Related:** `src/scavengarr/application/use_cases/stremio_stream.py`, `application/stremio/plugin_search.py`, `infrastructure/circuit_breaker.py`, `infrastructure/hoster_resolvers/`, `data/config.yaml`

## Problem

Stremio asks an addon once per title and shows what arrives before its timeout. Measured with a Stremio-style harness (18 titles: new releases, German movies/series, popular titles, anime; every returned stream actually played, i.e. first video bytes or HLS playlist + first segment), production config (`plugin_timeout_seconds: 15`), dev container network, 2026-09-29:

| Metric | Cold | Warm (same titles again) |
|---|---|---|
| Response time median / p90 / max | 19.8 / 31.2 / 38.5 s | ~17 / 22 / 24 s |
| Streams / playable | 80 / 61 (76 %) | similar |
| Titles with a playable stream | 17 / 18 | 17 / 18 |

Causes found:

1. **No overall deadline.** The plugin phase waits up to `plugin_timeout_seconds`; the resolve phase (hoster embed → video URL) runs afterwards without any time limit, so one slow hoster (browser capture, slow CDN) delays the whole answer. `stremio.stremio_deadline_ms` exists in the schema but nothing reads it.
2. **Unreachable plugins cost every request the full plugin timeout.** cineby, megakino_to and movie4k timed out on 8 of 8 movie requests with 0 results (their hosts are unreachable from this network). The circuit breaker opens after 5 failures but only for 60 s; Stremio requests are minutes apart, so it is half-open again for almost every request.
3. **Deduplication before resolution drops working streams.** `deduplicate_by_hoster` keeps only the best-ranked stream per hoster *before* resolving. When that one fails to resolve, the hoster disappears although other streams of it would work. Search results after title filtering (e.g. Reacher 40, Attack on Titan 28) end as 3–7 streams.
4. **Resolved but unplayable URLs are returned.** Dropload 0/11 (CDN 502), SuperVideo 0/4 (HTML instead of video): the resolver found a URL, nobody checked it plays.

## Design

1. **`stremio.stream_deadline_seconds`** (new, default 15.0): overall budget per stream request, measured from the request start. The resolve phase stops at the deadline (at least `_MIN_RESOLVE_WINDOW_S` = 2 s after the plugin phase) and returns what is resolved; unfinished resolutions are cancelled. `plugin_timeout_seconds` stays the plugin cap; defaults/recommended config keep plugin timeout < deadline so resolution gets a window. The unused `stremio_deadline_ms` stays accepted (compatibility) but is documented as unused.
2. **Circuit breaker backoff**: the cooldown doubles with every failed half-open trial (60 s → 2 min → … capped at 1 h) and resets on success.
3. **Deduplicate after resolution**: resolve the top `max_probe_count` candidates in rank order, then keep the first *playable* stream per hoster.
4. **Playability check** (`stremio.verify_streams`, default true): a resolved URL is fetched with `Range: bytes=0-0` (HLS: the playlist) using its playback headers, within the deadline; 4xx/5xx or an HTML answer drops the stream.

## Tests

Unit tests per change (use case with fake resolver/clock, circuit breaker backoff, verification with respx). Re-run the harness cold + warm; acceptance: median ≤ 12 s, max ≤ 16 s (Scavengarr alone), playable rate ≥ 90 %, no title loses its last playable stream compared to the baseline.

## Documentation

`docs/features/stremio-addon.md`, `docs/features/configuration.md`, `CHANGELOG.md`, `data/config.yaml` comments, results recorded here.
