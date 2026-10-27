## ADDED Requirements

### Requirement: Idle-Reset Resolvers Declare It
A hoster resolver whose CDN resets a connection that stops reading SHALL declare this with the class attribute `idle_reset = True`, and the resolver registry SHALL stamp `idle_reset` on every `ResolvedStream` it returns from that resolver. VEEV SHALL declare it.

#### Scenario: VEEV stream
- **WHEN** the registry resolves a VEEV link
- **THEN** the resolved stream's `idle_reset` is `True`

#### Scenario: Resolver without the attribute
- **WHEN** the registry resolves a link of a resolver that does not declare `idle_reset`
- **THEN** the resolved stream's `idle_reset` is `False`

### Requirement: Resume After Every Interruption
The file proxy SHALL resume a proxied file with a promised length after every interruption for as long as the response runs: after a transport error (`reset`) or a body that ends before the promised length (`eof`) at once, and after no byte for `stremio.file_stall_timeout_seconds` once the answer has sent its first byte (`stall`). HLS segments SHALL keep their one resume or restart.

#### Scenario: Reset after a pause
- **WHEN** the CDN resets the connection while the player pauses, and the player reads again
- **THEN** the proxy asks for the rest of the span from the next byte at once

#### Scenario: Several interruptions in one response
- **WHEN** the CDN ends a response early three times, each time after bytes flowed
- **THEN** the proxy resumes all three times and the response completes

#### Scenario: Silent CDN
- **WHEN** the CDN sends no byte for the stall timeout after the first byte
- **THEN** the proxy asks again from the next byte

### Requirement: Resumes Keep The Bytes Exact
A resume SHALL ask for `bytes=<next byte>-<last byte of the span>` with the transfer's headers and `Accept-Encoding: identity`, SHALL go on only with a 206 that starts at that byte, and the bytes sent SHALL equal the file's bytes of the span, none doubled or skipped.

#### Scenario: A resume from another byte
- **WHEN** the CDN answers a resume with a 206 that starts at another byte, or with 200
- **THEN** the answer is closed and the attempt counts as failed

#### Scenario: Bytes across a resume
- **WHEN** a file is reset, ended early and stalled once each before it completes
- **THEN** the player receives exactly the file's bytes

### Requirement: Resume Attempts Are Bounded
At one offset at most three resume attempts SHALL run, after waits of 0, 1 and 2 s, each with one stall timeout for its answer and that answer's first byte; the count SHALL start again once bytes flow, and after the third failed attempt the body SHALL raise `FileCut`.

#### Scenario: Resumes refused
- **WHEN** three attempts at one offset fail
- **THEN** the body raises `FileCut` after waits of 0, 1 and 2 s

#### Scenario: A CDN that resets every new connection at once
- **WHEN** every resume answers 206 and ends before its first byte
- **THEN** the body raises `FileCut` after three attempts

### Requirement: A Refused Resume Resolves Again
A refusal (403, 404, 410) of a resume SHALL resolve the hoster URL again once per interruption, past the resolver's cache, and the transfer SHALL go on from the new URL with the new resolution's headers.

#### Scenario: Expired URL mid-body
- **WHEN** a resume is answered 403 and the new resolution gives a URL that answers 206 from the next byte
- **THEN** the transfer goes on from the new URL with the new resolution's headers

### Requirement: File Stall Timeout Setting
`stremio.file_stall_timeout_seconds` SHALL set the stall timeout of proxied files, default 10 s, between 1 and 30 s, read by the file route.

#### Scenario: Default
- **WHEN** the setting is not configured
- **THEN** a proxied file is asked again after 10 s without a byte

#### Scenario: Out of bounds
- **WHEN** the setting is 60
- **THEN** the configuration is refused at startup

### Requirement: File Resume Telemetry
The file proxy SHALL record every resume as the stage `file_resume` with the labels `hoster` (a resolver name, otherwise `other`) and `reason` (`reset`, `eof`, `stall`) and the outcome `resumed` or `gave_up`, and SHALL log `hls_proxy_file_resumed` with the CDN's domain, the bytes sent, the reason and the attempt, never a URL.

#### Scenario: A resume after a reset
- **WHEN** a VEEV file is resumed after a reset
- **THEN** `scavengarr_file_resume_total{hoster="veev",reason="reset",outcome="resumed"}` rises by one

#### Scenario: A give-up
- **WHEN** three attempts at one offset fail after a stall
- **THEN** `scavengarr_file_resume_total{reason="stall",outcome="gave_up"}` rises by one

### Requirement: Idle-Reset Production Proof
After the deploy, the title's VEEV stream SHALL be a `/proxy/<id>/file` URL, and a pause test through it from the production container (pauses of 60 s and 180 s without reading) SHALL show data within 5 s after each resume, the bytes after the resume identical to a direct range fetch of the CDN, and the resume counter raised.

#### Scenario: Pause test after the deploy
- **WHEN** the probe pauses on the VEEV file proxy URL for 60 s and for 180 s
- **THEN** data comes within 5 s after each resume and the hashes of the spans match

### Requirement: Address-Bound And Idle-Reset Direct Streams Are Proxied
The stream builder SHALL give an address-bound or idle-reset direct stream the URL `/api/v1/stremio/proxy/<id>/file` with the behaviour hints of the HLS proxy stream and no proxy headers, SHALL keep the playlist proxy for HLS streams, and SHALL keep `/play/<id>` for every other direct stream, other client-bound ones included.

#### Scenario: MixDrop file
- **WHEN** a resolved MixDrop stream is a direct file with `address_bound` `True`
- **THEN** the Stremio answer's URL ends in `/proxy/<id>/file`
- **AND** the answer carries no `proxyHeaders`

#### Scenario: Unbound direct file
- **WHEN** a resolved direct stream has neither `address_bound` nor `idle_reset`
- **THEN** the answer's URL ends in `/play/<id>`

#### Scenario: VEEV through the proxy
- **WHEN** a VEEV stream is resolved
- **THEN** the answer's URL ends in `/proxy/<id>/file`

## MODIFIED Requirements

### Requirement: The Stored Link Carries The Flag
`CachedStreamLink` SHALL carry `address_bound` and `idle_reset`, written with the resolution, and a stored link from before either flag SHALL read as not flagged.

#### Scenario: Old record
- **WHEN** a stored link has no `address_bound` or no `idle_reset` field
- **THEN** the missing flag reads as `False`, and a link with neither keeps `/play`

### Requirement: Refusals And Errors
The file route SHALL answer 404 without a stored link, 400 for a link that is HLS or neither address-bound nor idle-reset, 503 without the link repository, SHALL resolve a stale link again before fetching, SHALL re-resolve once and retry when the CDN answers 403, 404 or 410 to the opening request, SHALL answer 502 when the retry fails or the CDN cannot be reached, and SHALL pass a CDN 416 through.

#### Scenario: Expired URL
- **WHEN** the CDN answers 410 and the re-resolution gives a new URL that answers 206
- **THEN** the proxy answers 206 from the new URL

#### Scenario: Refusal persists
- **WHEN** the CDN answers 403 before and after the re-resolution
- **THEN** the proxy answers 502

#### Scenario: HLS link on the file route
- **WHEN** the stored link is HLS
- **THEN** the proxy answers 400

## REMOVED Requirements

### Requirement: Address-Bound Direct Streams Are Proxied
**Reason**: VEEV no longer stays on `/play`: idle-reset files go through the file proxy too.
**Migration**: replaced by "Address-Bound And Idle-Reset Direct Streams Are Proxied".
