## 1. Tests first

- [x] 1.1 Probe window: a hoster's third link is dropped by the cap; a cached-live third link and a stream that resolved before a better-labelled newcomer arrive stay in the answer.
- [x] 1.2 Bitrate: HLS `AVERAGE-BANDWIDTH`/`BANDWIDTH` (attribute order, quoted `CODECS`, CRLF, a cut last line, I-frame variants) and MP4 `mvhd` v0/v1 (sentinels, timescale 0, unknown version).
- [x] 1.3 Ranking: higher bitrate wins the same quality and language; it never outranks a different language, including the tightest configured gap, nor a better quality.
- [x] 1.4 Redirects and dedup: a redirect-followed resolver names the hoster; one resolved video per hoster, language and episode; an echo or a wrong-season result does not suppress the real stream; a suffix-less playlist body is not a stub.
- [x] 1.5 Stub floor: below the floor dropped and cached dead, at the floor kept, `0` off, unknown size kept, a playlist body untouched, the `too_small` outcome does not count as a breaker failure; a hoster's third link replaces two dead ones.

## 2. Implementation

- [x] 2.1 `cap_links_per_hoster()` with an exempt set; applied before the cached path's `max_probe_count` cut and to the live ranking; resolved URLs survive the top-limit truncation.
- [x] 2.2 HLS variant parser and per-variant bitrate; `movie_duration()`; `_file_video_info()`; bitrate on `ResolvedStream`/`RankedStream`; `apply_resolution()` and `format_stream()`.
- [x] 2.3 `StreamSorter`: one point per Mbps, derived cap from the configured language gaps and quality step.
- [x] 2.4 `_judge()` sets `hoster = resolver.name`; `_deduplicate()` keys on hoster, language and episode for a resolved direct video only; `min_stream_size_mb` wiring.
- [x] 2.5 Docs: `stremio-addon.md`, `hoster-resolvers.md`, generated `configuration.md`, `CHANGELOG.md`.

## 3. Rollout and proof

- [x] 3.1 Ruff, format, basedpyright and the offline unit suite on a clean `staging`.
- [x] 3.2 Production check: `tt0304141` answers five streams, `1080p · dropload · hdfilme` first, no RB/BIT/BIGWARP.
- [ ] 3.3 CI green and the independent review of the pull request.
