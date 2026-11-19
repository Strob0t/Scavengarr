## MODIFIED Requirements

### Requirement: Measured Stream Quality
The playback check SHALL read the resolution of an HLS master playlist and the total size of a direct file from the bytes it fetches anyway, and SHALL also read the bitrate from those bytes: the chosen HLS variant's `AVERAGE-BANDWIDTH` (else `BANDWIDTH`), or an MP4 file's total size over the duration its `mvhd` names. The resolved stream SHALL carry them as `quality`, `size_bytes` and `bitrate_bps`; a measurement never costs an extra request.

#### Scenario: HLS master playlist
- **WHEN** the first 4 KiB of a resolved HLS stream list variants with `RESOLUTION=1280x720` and `RESOLUTION=1920x1080`
- **THEN** the resolved stream's quality is `HD_1080P`

#### Scenario: Letterboxed encode
- **WHEN** the largest variant is `RESOLUTION=1920x800`
- **THEN** the quality is `HD_1080P`

#### Scenario: Media playlist
- **WHEN** the playlist lists segments only, without `#EXT-X-STREAM-INF`
- **THEN** the quality and bitrate stay as the resolver reported them

#### Scenario: HLS bitrate
- **WHEN** the chosen variant carries `AVERAGE-BANDWIDTH=4500000` (else `BANDWIDTH=6000000`)
- **THEN** `bitrate_bps` is `4500000` (else `6000000`)

#### Scenario: Equal resolutions
- **WHEN** two variants are `1920x1080` at 3 and 6 Mbps
- **THEN** the chosen variant is the 6 Mbps one

#### Scenario: CRLF and cut playlists
- **WHEN** a master playlist uses CRLF, or the 4 KiB read cuts its last line
- **THEN** the complete variants (and their bitrates) are read and the cut line is ignored

#### Scenario: Direct file
- **WHEN** the range response carries `Content-Range: bytes 0-4095/1500000000` and the `mvhd` names 600 s
- **THEN** `size_bytes` is `1500000000` and `bitrate_bps` is `20000000`

#### Scenario: Unknown total
- **WHEN** the range response carries `Content-Range: bytes 0-4095/*` or no `Content-Range`
- **THEN** `size_bytes` is `None`

### Requirement: Measurement Over Badge
The Stremio answer SHALL show a measured quality in place of the one parsed from the release name or badge when the measurement is known, SHALL show the measured size when the plugin gave none, SHALL show the measured bitrate, and SHALL sort the streams with the merged values.

#### Scenario: Measured quality ranks the stream
- **WHEN** two streams of the same language have no badge quality and one is measured at 1080p
- **THEN** the measured stream is listed first and its name carries `1080p`

#### Scenario: Badge kept without a measurement
- **WHEN** a stream's badge says `720p` and the measurement is unknown
- **THEN** the name carries `720p`

#### Scenario: Plugin size wins
- **WHEN** the plugin reported `1.5 GB` and the measurement says 1 400 000 000 bytes
- **THEN** the description shows the plugin's `1.5 GB`

#### Scenario: Bitrate tie-break
- **WHEN** two streams have the same quality and language and one carries the higher measured bitrate
- **THEN** the higher-bitrate stream is listed first, and neither a different language nor a better quality is outranked by the bitrate

## ADDED Requirements

### Requirement: Probe Window Keeps Each Hoster's Links
The ranked candidates SHALL be capped at `stremio.max_links_per_hoster` links per hoster and language before `stremio.max_probe_count` truncates them, and links whose resolution is already known (cached alive, or resolved before a better-labelled newcomer arrived) SHALL be exempt from the cap and the truncation. `0` SHALL keep every link.

#### Scenario: A site's link flood
- **WHEN** one site returns 40 links of the same hoster and another site's unlabelled link ranks below them
- **THEN** the cap keeps the first two links of that hoster and the other site's link stays in the probe window

#### Scenario: Cached-live third link
- **WHEN** a hoster's third link has a cached video and its first two do not
- **THEN** the cached answer still answers with that third link

#### Scenario: Resolved before a newcomer
- **WHEN** a link resolved and a later, better-labelled result of the same hoster arrives
- **THEN** the resolved stream stays in the answer

### Requirement: One Playable Stream per Hoster
The registry SHALL stamp a resolved stream's hoster with the name of the resolver that produced it, and the measured answer SHALL keep one resolved direct video per hoster, language and episode. An unresolved link or an echoed embed page SHALL NOT suppress another link of its hoster.

#### Scenario: Shortener redirect
- **WHEN** `rb.gy` and `bit.ly` links both redirect to the same `voe.sx` upload
- **THEN** both are hoster `voe`, and the answer carries one of them

#### Scenario: Echo and next link
- **WHEN** a hoster's best link only echoes its embed page and its next link resolves to a video
- **THEN** the video is answered

#### Scenario: Wrong season
- **WHEN** a wrong-season result of the same hoster outranks the requested episode's stream
- **THEN** both are kept for their own episode labels

### Requirement: Stub Files Are Dropped
A resolved file whose measured total is below `stremio.min_stream_size_mb` (default 20, `0` disables) SHALL be dropped and cached dead like an unplayable stream, SHALL log `hoster_resolve_too_small`, and SHALL NOT count against the resolver's circuit breaker. A playlist body, a content-type probe and a run with `stremio.verify_streams` off SHALL NOT be dropped by the floor.

#### Scenario: A 1 MB placeholder
- **WHEN** a resolver answers a 1 033 738-byte MP4 for a feature film and the floor is 20
- **THEN** the stream is left out of the answer and the link is cached dead

#### Scenario: Playlist body
- **WHEN** a resolved file's first bytes start with `#EXTM3U` and the URL has no `.m3u8` suffix
- **THEN** it is treated as a playlist (no size) and the floor does not apply

#### Scenario: Floor off
- **WHEN** `stremio.min_stream_size_mb` is 0
- **THEN** a small file is kept
