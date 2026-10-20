# Change: Take Stream Quality and Size From the Stream Itself

## Why

Stremio shows a stream's quality in its name (`Scavengarr\n1080p`) and its size in the description, both taken from the release name and the badges a plugin scraped (`infrastructure/stremio/release_parser.py`). Streaming sites rarely carry either: in production most streams go out as `Scavengarr` without a quality line, and the sort (`_StreamSorter`: language, quality, hoster score) ranks them by hoster score alone. The information is already on the wire and thrown away: `check_playable` (`infrastructure/hoster_resolvers/_verify.py`) fetches the first 1 KiB of every resolved stream with a `Range` header before it is served, and that answer carries the resolution of an HLS master playlist (`#EXT-X-STREAM-INF:...RESOLUTION=1920x1080`) and the total length of a file (`Content-Range: bytes 0-1023/1234567890`). No resolver sets a quality today (`StreamQuality.UNKNOWN` everywhere, `_judge` stamps the result as it is).

## What Changes

- **`check_playable` returns what it saw**, not only a boolean: a small result with `playable`, the highest `RESOLUTION` height in the sniffed playlist (HLS, sniff grows to 4 KiB so a master playlist with several variants fits) and the total size from `Content-Range` (files). Signature and call site in `HosterResolverRegistry._judge` change together; the log event `playback_check_failed` stays.
- **`ResolvedStream` carries the measurement**: `quality` is set from the resolution (`height >= 2160` → `UHD_4K`, `>= 1080` → `HD_1080P`, `>= 720` → `HD_720P`, lower → `SD`; none → unchanged) and a new `size_bytes: int | None` from `Content-Range`. `_judge` applies both with `dataclasses.replace` to the stream it stamps, so the registry's result cache and the stream link store carry them too.
- **The use case prefers the measurement**: before `format_stream`, `StremioStreamUseCase._answer` merges each resolved stream's quality and size into its `RankedStream` (`dataclasses.replace`; measured quality replaces the badge quality when known, `size_bytes` fills `size` as a human-readable string only when the plugin gave none) and re-sorts the list with `_StreamSorter` when anything changed. The cached-answer path (`_cache_and_proxy` and the stream link store) sees the same values because they live on `ResolvedStream`.
- Media playlists without variants, direct files without `Content-Range` and resolvers whose CDN refuses the probe keep `UNKNOWN`/no size: the badge-based values stay as the fallback, nothing is removed.

## Impact

- Affected specs: new capability `stremio-stream-quality`.
- Affected code: `infrastructure/hoster_resolvers/_verify.py`, `infrastructure/hoster_resolvers/registry.py` (`_judge`), `domain/entities/stremio.py` (`ResolvedStream.size_bytes`), `application/use_cases/stremio_stream.py` (`_answer`, a small merge helper; coordinate with item I14, the module split — this change goes first or lands in the extracted answer-assembly module), `application/stremio/stream_builder.py` (size formatting helper if none exists), tests beside each, `docs/features/stremio-addon.md`, `docs/features/hoster-resolvers.md`, `CHANGELOG.md`.
- Compatibility: `ResolvedStream` is not persisted today (the search cache holds `SearchResult`s, the stream link store its own JSON fields), so the new field needs no migration; the `persist-resolver-state` snapshot has a version key for exactly this.
- Cost: no extra request; the sniff reads at most 3 KiB more per playback check.
- Risk: letterboxed encodes (`1920x800`) are 1080p by width and SD by height; the mapping classifies by width and height and takes the higher class, and the tests pin `1920x1080`, `1920x800`, `1280x720`, `3840x2160`, `854x480`.
