## ADDED Requirements

### Requirement: Measured Stream Quality
The playback check SHALL read the resolution of an HLS master playlist and the total size of a direct file from the bytes it fetches anyway, and the resolved stream SHALL carry them as `quality` and `size_bytes`; a measurement never costs an extra request.

#### Scenario: HLS master playlist
- **WHEN** the first 4 KiB of a resolved HLS stream list variants with `RESOLUTION=1280x720` and `RESOLUTION=1920x1080`
- **THEN** the resolved stream's quality is `HD_1080P`

#### Scenario: Letterboxed encode
- **WHEN** the largest variant is `RESOLUTION=1920x800`
- **THEN** the quality is `HD_1080P`

#### Scenario: Media playlist
- **WHEN** the playlist lists segments only, without `#EXT-X-STREAM-INF`
- **THEN** the quality stays as the resolver reported it

#### Scenario: Direct file
- **WHEN** the range response carries `Content-Range: bytes 0-4095/1500000000`
- **THEN** `size_bytes` is `1500000000`

#### Scenario: Unknown total
- **WHEN** the range response carries `Content-Range: bytes 0-4095/*` or no `Content-Range`
- **THEN** `size_bytes` is `None`

### Requirement: Measurement Over Badge
The Stremio answer SHALL show a measured quality in place of the one parsed from the release name or badge when the measurement is known, SHALL show the measured size when the plugin gave none, and SHALL sort the streams with the merged values.

#### Scenario: Measured quality ranks the stream
- **WHEN** two streams of the same language have no badge quality and one is measured at 1080p
- **THEN** the measured stream is listed first and its name carries `1080p`

#### Scenario: Badge kept without a measurement
- **WHEN** a stream's badge says `720p` and the measurement is unknown
- **THEN** the name carries `720p`

#### Scenario: Plugin size wins
- **WHEN** the plugin reported `1.5 GB` and the measurement says 1 400 000 000 bytes
- **THEN** the description shows the plugin's `1.5 GB`
