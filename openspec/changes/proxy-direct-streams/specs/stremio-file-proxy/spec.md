## ADDED Requirements

### Requirement: Address-Bound Resolvers Declare It
A hoster resolver whose CDN binds the video URL to the address that resolved it SHALL declare this with the class attribute `address_bound = True`, and the resolver registry SHALL stamp `address_bound` on every `ResolvedStream` it returns from that resolver. DoodStream, MixDrop, Vinovo and FSST SHALL declare it.

#### Scenario: DoodStream stream
- **WHEN** the registry resolves a DoodStream link
- **THEN** the resolved stream's `address_bound` is `True`

#### Scenario: Resolver without the attribute
- **WHEN** the registry resolves a link of a resolver that does not declare `address_bound`
- **THEN** the resolved stream's `address_bound` is `False`

### Requirement: Address-Bound Direct Streams Are Proxied
The stream builder SHALL give an address-bound direct stream the URL `/api/v1/stremio/proxy/<id>/file` with the behaviour hints of the HLS proxy stream and no proxy headers, SHALL keep the playlist proxy for HLS streams, and SHALL keep `/play/<id>` for every other direct stream, client-bound ones included.

#### Scenario: MixDrop file
- **WHEN** a resolved MixDrop stream is a direct file with `address_bound` `True`
- **THEN** the Stremio answer's URL ends in `/proxy/<id>/file`
- **AND** the answer carries no `proxyHeaders`

#### Scenario: Unbound direct file
- **WHEN** a resolved direct stream has `address_bound` `False`
- **THEN** the answer's URL ends in `/play/<id>`

#### Scenario: VEEV stays per player
- **WHEN** a VEEV stream is resolved
- **THEN** the answer's URL ends in `/play/<id>`

### Requirement: The Stored Link Carries The Flag
`CachedStreamLink` SHALL carry `address_bound`, written with the resolution, and a stored link from before the change SHALL read as not bound.

#### Scenario: Old record
- **WHEN** a stored link has no `address_bound` field
- **THEN** it reads as `address_bound` `False` and keeps `/play`

### Requirement: Byte-Range Pass-Through
`GET` and `HEAD /api/v1/stremio/proxy/{id}/file` SHALL request the stored video URL with the stored headers over the default browser User-Agent and `Accept-Encoding: identity`, SHALL forward the player's `Range` and `If-Range` headers when present, SHALL answer with the CDN's status and its `Content-Type`, `Content-Length`, `Content-Range` and `Accept-Ranges` headers plus the CORS headers, SHALL stream the body in pieces without buffering the file, and SHALL send no body for HEAD.

#### Scenario: Whole file
- **WHEN** a player requests the file without a `Range` header and the CDN answers 200 with `Content-Length: 1500000000`
- **THEN** the proxy answers 200 with `Content-Length: 1500000000`, `Accept-Ranges: bytes` and the CDN's content type

#### Scenario: Range request
- **WHEN** a player sends `Range: bytes=1000000-` and the CDN answers 206 with `Content-Range: bytes 1000000-1499999999/1500000000`
- **THEN** the proxy answers 206 with that `Content-Range` and the matching `Content-Length`

#### Scenario: HEAD
- **WHEN** a player sends HEAD
- **THEN** the proxy answers with the CDN's status and headers and an empty body

#### Scenario: Compressed answer refused
- **WHEN** the proxy requests the file
- **THEN** the request carries `Accept-Encoding: identity`

### Requirement: Refusals And Errors
The file route SHALL answer 404 without a stored link, 400 for a link that is HLS or not address-bound, 503 without the link repository, SHALL resolve a stale link again before fetching, SHALL re-resolve once and retry when the CDN answers 403, 404 or 410, SHALL answer 502 when the retry fails or the CDN cannot be reached, and SHALL pass a CDN 416 through.

#### Scenario: Expired URL
- **WHEN** the CDN answers 410 and the re-resolution gives a new URL that answers 206
- **THEN** the proxy answers 206 from the new URL

#### Scenario: Refusal persists
- **WHEN** the CDN answers 403 before and after the re-resolution
- **THEN** the proxy answers 502

#### Scenario: HLS link on the file route
- **WHEN** the stored link is HLS
- **THEN** the proxy answers 400

### Requirement: File Proxy Telemetry
The file route SHALL record the `hls_proxy` stage with `kind="file"` and the status as its outcome, and SHALL count the bytes sent as `hls_proxy_bytes` with `kind="file"`, aborted transfers included; its logs SHALL carry the CDN's domain at most, never a URL.

#### Scenario: Metrics after a file
- **WHEN** a 206 file answer of 3 MiB completes
- **THEN** `scavengarr_hls_proxy_total{kind="file",outcome="206"}` rises by one and `scavengarr_hls_proxy_bytes_total{kind="file"}` by the bytes sent

### Requirement: File Throughput Measured
`scripts/probes/hls_throughput.py --file <stream id>` SHALL fetch the first 32 MiB of a stored direct file from the Pi in one connection and then as three parallel byte ranges, and SHALL print Mbit/s for both and whether the CDN honoured the ranges, so that a read-ahead for files is decided by numbers in a change of its own.

#### Scenario: Probe run
- **WHEN** the probe runs on the Pi with a stored MixDrop stream id
- **THEN** it prints the two Mbit/s figures and the range verdict without printing the URL

### Requirement: Production Acceptance
After the deploy, `scripts/stremio_round.py` run from the dev container SHALL show the DoodStream, MixDrop, Vinovo and FSST streams of the round's titles as playable, recorded under the seventh round in `docs/plans/stremio-latency.md`.

#### Scenario: Round after the deploy
- **WHEN** the round runs from the dev container against production with the change deployed
- **THEN** the four hosters' streams count as playable
