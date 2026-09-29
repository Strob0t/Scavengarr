[← Back to Index](../features/README.md)

# Plan: Stremio Response Time and Playable Streams

**Status:** Done (2026-09-29). AIOStreams deferred by decision: Scavengarr is added to Stremio directly; the [AIOStreams](#aiostreams) notes stay for later.
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

## Results

Implemented as designed, plus three changes the measurements forced:

- **Search budget from the request start.** Each title runs two query variants over all plugins, so plugins queue for concurrency slots, and a plugin's timeout started only when it got a slot: with `plugin_timeout_seconds: 15` the search alone took up to 35 s. `plugin_timeout_seconds` now ends the search that long after the request start (default 10 s, was 30 s per plugin).
- **Per-hoster resolution in rank order** instead of resolving all `max_probe_count` candidates at once and deduplicating afterwards. The burst (dozens of new connections to distinct CDNs within a second, plus the playback checks) made the network path from the dev container block new connections for 30–60 s ("No route to host", intermittent, every run 2–2.5 min in); the baseline never triggered it.
- **Link validator backoff.** An unreachable host was skipped for a flat 15 min, so one such network blip removed VOE, vinovo, kinoger, fsst, … from every request of the next 15 minutes. Now 60 s, doubling per further failure up to 15 min.

Final run (G: code of this plan, `plugin_timeout_seconds: 10`, `stream_deadline_seconds: 15`, `verify_streams: true`, cold cache, the four title groups with 3 min pauses so the network path stays clean):

| Metric | Baseline (cold) | Final (cold) | Variant 12 s / 17 s |
|---|---|---|---|
| Response median / p90 / max | 19.8 / 31.2 / 38.5 s | 15.0 / 15.1 / 15.1 s | 16.5 / 17.1 / 17.1 s |
| Returned streams / playable | 80 / 61 (76 %) | 46 / 44 (96 %) | 46 / 44 (96 %) |
| Titles with a playable stream | 17 / 18 | 17 / 18 | 17 / 18 |

- Acceptance: max ≤ 16 s and playable ≥ 90 % met; no title lost its last playable stream (the one title without a stream, *Der Schuh des Manitu*, had none in the baseline either). Median 15.0 s instead of ≤ 12 s: resolution uses the budget up to the deadline for most titles.
- Trade-off: fewer playable streams per title (44 vs 61). The extra baseline streams came mostly from kinoking (needs 6–14 s, often cut at 10 s). A 12 s search / 17 s answer gave exactly the same result at +2 s, so 10 / 15 s is the recommended setting (new defaults, `data/config.yaml`).
- Unreachable plugins from this network (cineby, megakino_to, movie4k) still start every request until their circuit breaker opens; with the backoff they stay closed for up to 1 h.

## AIOStreams

Goal was an AIOStreams test user on `aiostreams.lan` with Scavengarr as addon, measured end to end. Not done: AIOStreams validates the addon manifest when a user is created or updated, and it can reach neither the dev instance (Docker NAT on the workstation) nor `scavengarr.lan` (502, backend down). Recommended user settings, from the AIOStreams v2.35.3 source (`packages/core/src/presets/custom.ts`, `packages/core/src/db/schemas.ts`):

- Scavengarr as `custom` preset with `manifestUrl: https://scavengarr.lan/api/v1/stremio/manifest.json`, `resources: ["stream"]`, `mediaTypes: ["movie", "series"]`.
- `timeout: 17000` (ms, per addon; AIOStreams default 7000 would cut every Scavengarr answer): `stream_deadline_seconds` + 2 s headroom.
- `preferredLanguages: ["German", "Multi", "Dual Audio", "English", "Unknown"]`, `sortCriteria.global`: language, resolution, quality (all `desc`).
- Scavengarr already returns one working stream per hoster, so AIOStreams dedup and result limits need no special handling for it. Whether AIOStreams parses language and resolution from Scavengarr's stream names reliably is unverified; if not, `formatPassthrough: true` keeps Scavengarr's own labels.

Deferred (2026-09-29): without other sources AIOStreams adds nothing Scavengarr does not already do (per-hoster dedup, German-first ranking, playback check) but costs latency and risks metadata misparsing, so Scavengarr is used directly in Stremio. If AIOStreams comes back (e.g. with debrid sources): create the test user, measure end to end, and check whether language/resolution of Scavengarr streams are parsed (else `resultPassthrough: true`).
