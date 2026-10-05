## ADDED Requirements

### Requirement: Prometheus Metrics Endpoint
The system SHALL serve its metrics in the Prometheus text format at `GET /metrics`, rendered outside the event loop.

#### Scenario: Scrape
- **WHEN** Prometheus requests `GET /metrics`
- **THEN** the response is 200 with prometheus-client's text exposition content type
- **AND** it contains the `scavengarr_*` families, `process_*` and `scavengarr_build_info` with the app version

#### Scenario: No metrics recorded yet
- **WHEN** `/metrics` is requested before any Stremio request
- **THEN** the response is 200 and lists the families without samples for stages that never ran

### Requirement: Stage Recording
The system SHALL record a timed step (a stage) as one duration observation in `scavengarr_<stage>_seconds` and one count in `scavengarr_<stage>_total` with its outcome, through `TelemetryPort.stage()`.

#### Scenario: Outcome set by the code
- **WHEN** a plugin search stage ends with `stage.outcome = "hits"`
- **THEN** `scavengarr_plugin_search_seconds{plugin}` has one more observation
- **AND** `scavengarr_plugin_search_total{plugin, outcome="hits"}` grows by 1

#### Scenario: Cancelled stage
- **WHEN** a stage is cancelled (search deadline, answer due, shutdown) before the code set an outcome
- **THEN** its outcome is `cut`
- **AND** the `CancelledError` propagates

#### Scenario: Failed stage
- **WHEN** an exception other than a cancellation leaves a stage without an outcome
- **THEN** the outcome is `timeout` for a `TimeoutError` and `error` otherwise
- **AND** the exception propagates unchanged

#### Scenario: Outcome without a run
- **WHEN** a plugin is skipped because its circuit breaker is open
- **THEN** `scavengarr_plugin_search_total{outcome="breaker_open"}` grows by 1
- **AND** no duration is observed

### Requirement: Bounded Label Values
Metric label values SHALL come only from fixed sets: plugin names, resolver names, breaker keys and the documented sources, phases, kinds and outcomes. Titles, IMDb or TMDB ids, URLs, domains and stream ids SHALL NOT be label values.

#### Scenario: Hoster with rotating domains
- **WHEN** VOE links on hundreds of domains are resolved
- **THEN** all of them count under `resolver="voe"`

### Requirement: Stremio Request Metrics
The system SHALL record every Stremio stream request with the state of its search results and the reason its answer went out.

#### Scenario: Answer from the search cache
- **WHEN** a request is answered from a fresh cache entry
- **THEN** `scavengarr_stremio_request_seconds{source="cache"}` gets its duration
- **AND** `scavengarr_stremio_streams` gets its stream count

#### Scenario: Answer at the resolve target
- **WHEN** an answer goes out because `resolve_target_count` hosters have a video
- **THEN** `scavengarr_stremio_phase_total{phase="resolve", outcome="target"}` grows by 1

#### Scenario: Request joins a running search
- **WHEN** a request for a title finds that title's search running
- **THEN** its source is `joined`
- **AND** the search phase is recorded once, by the search task

#### Scenario: Title not found
- **WHEN** TMDB knows no title for the id
- **THEN** the request's outcome is `no_title` and the metadata phase's outcome `not_found`

### Requirement: Plugin Search Metrics
The system SHALL record each plugin search of a Stremio request in the plugin search runner, without code in the plugins.

#### Scenario: Plugin cut by the search deadline
- **WHEN** a plugin's search runs into the search deadline
- **THEN** its outcome is `cut` and its duration is observed

#### Scenario: Plugin without slot
- **WHEN** a plugin gets no concurrency slot before the search deadline
- **THEN** `outcome="skipped"` is counted without a duration

#### Scenario: Results per plugin
- **WHEN** a plugin returns 12 validated results
- **THEN** `scavengarr_plugin_results_total{plugin}` grows by 12

### Requirement: Hoster Resolution Metrics
The system SHALL record each hoster resolution in the hoster resolver registry, labeled with the resolver's name.

#### Scenario: Unplayable stream
- **WHEN** a resolver returns a stream that fails the playback check
- **THEN** `scavengarr_hoster_resolve_total{resolver, outcome="unplayable"}` grows by 1

#### Scenario: Cached resolution
- **WHEN** a URL's resolution comes from the registry's cache
- **THEN** `outcome="cached"` is counted for the resolver that resolved it, without a duration

#### Scenario: Open hoster breaker
- **WHEN** a resolver is skipped because its breaker is open
- **THEN** `outcome="breaker_open"` is counted for it

### Requirement: HLS Proxy Metrics
The system SHALL record each HLS proxy request by kind (`master`, `playlist`, `segment`) with the HTTP status of its answer, the time until the answer starts, and the bytes sent.

#### Scenario: Segment
- **WHEN** a 1.5 MB segment is proxied
- **THEN** `scavengarr_hls_proxy_total{kind="segment", outcome="200"}` grows by 1
- **AND** `scavengarr_hls_proxy_bytes_total{kind="segment"}` grows by its size once the body is sent

#### Scenario: Converter refused
- **WHEN** a streaming server's ffmpeg requests the master playlist
- **THEN** `scavengarr_hls_proxy_total{kind="master", outcome="403"}` grows by 1

### Requirement: Runtime State Metrics
The system SHALL expose the circuit breakers that are not closed, the event-loop lag and the container's CPU and memory, the first and last read at scrape time.

#### Scenario: Open breaker
- **WHEN** the hoster breaker of `filemoon` is open at scrape time
- **THEN** `scavengarr_circuit_breaker_open{breaker="hoster", name="filemoon", state="open"}` is 1
- **AND** closed breakers are not listed

#### Scenario: Container usage
- **WHEN** the process runs in a cgroup v2 container
- **THEN** `scavengarr_container_cpu_seconds_total` and `scavengarr_container_memory_bytes` come from `cpu.stat` and `memory.current`
- **AND** outside a cgroup v2 container both are absent

#### Scenario: Event-loop lag
- **WHEN** the 0.5 s lag timer fires late
- **THEN** `scavengarr_event_loop_lag_seconds` observes the lag

### Requirement: JSON Metrics Compatibility
`GET /api/v1/stats/metrics` SHALL keep its keys (`uptime_seconds`, `plugins`, `event_loop`, `circuit_breaker`, `concurrency_pool`, `shutdown`), with `plugins` read from the plugin search metrics.

#### Scenario: Plugin statistics
- **WHEN** a plugin had 3 searches with hits, 1 empty and 1 cut
- **THEN** its entry shows `searches` 5, `successes` 4 and `failures` 1

### Requirement: Request Id
The system SHALL give every HTTP request a generated `request_id`, bind it to the structlog context of the request and the tasks it starts, and return it in the `X-Request-ID` header.

#### Scenario: Logs of one request
- **WHEN** a Stremio request starts a search
- **THEN** the request's log lines and the search's log lines carry the same `request_id`
- **AND** the response has the header `X-Request-ID` with that id

#### Scenario: Incoming header
- **WHEN** a client sends its own `X-Request-ID`
- **THEN** the id is generated anyway

### Requirement: On-Demand Tracing
The system SHALL export the stages as OpenTelemetry spans over OTLP/HTTP when `telemetry.tracing_endpoint` is set, and SHALL NOT import OpenTelemetry otherwise.

#### Scenario: Tracing off (default)
- **WHEN** `telemetry.tracing_endpoint` is not set
- **THEN** no span is created and no OpenTelemetry module is imported

#### Scenario: Tracing on
- **WHEN** the endpoint is set and a Stremio request runs
- **THEN** one trace holds the request span with its metadata, search, plugin search, resolve and hoster resolution spans
- **AND** the root span carries the `request_id`
- **AND** no span attribute holds a URL or a title

#### Scenario: HLS proxy
- **WHEN** tracing is on and a stream plays through the HLS proxy
- **THEN** the proxy requests are recorded as metrics but not as spans

### Requirement: Metrics Overhead
Recording SHALL cost under 1 % of the work it measures: under 50 µs per stage on the development machine, and a scrape of the expected series under 20 ms.

#### Scenario: Benchmark
- **WHEN** `tests/benchmark/test_telemetry_overhead.py` runs
- **THEN** it reports the cost per stage and per scrape of 650 series and fails above these limits
