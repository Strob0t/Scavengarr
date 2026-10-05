## Context

Scavengarr runs as one uvicorn process on a Raspberry Pi 4 (4 cores, 8 GB) next to Prometheus, Grafana and node-exporter. A Stremio stream request fans out into about 17 plugin searches and 20-40 hoster resolutions; it answers at `resolve_target_count` hosters with a video, when the search and every resolution are done, or at `stream_deadline_seconds` (`docs/plans/round5-measures.md`). Searches are shared per title (single-flight) and cached; a cached answer resolves the other links in the background.

The existing `MetricsCollector` (`infrastructure/metrics.py`) keeps per-plugin averages and the event-loop lag for the JSON endpoint. Analyses of rounds 4 and 5 were built by hand from logs.

## Goals / Non-Goals

### Goals
- Answer the open tuning questions from real use: p95 per plugin and resolver, outcomes per plugin and resolver, why an answer went out, answer time by cache state, streams per answer, Chromium CPU, HLS proxy load.
- One recording point in the core; plugins and resolvers stay unchanged.
- Cost far below the service's: recording in microseconds, a scrape in milliseconds, nothing that runs while nobody uses the service except the 60 s scrape.
- Traces of single requests on demand, without a permanent backend.

### Non-Goals
- Torznab request metrics (Stremio is the main use; Torznab searches keep their logs).
- Per-HTTP-request spans of plugins (httpx instrumentation) and GIL profiling (py-spy stays on demand).
- Alerting rules.
- Metrics over OpenTelemetry (push to Prometheus' OTLP receiver).

## Decisions

### Decision 1: prometheus-client for metrics, OpenTelemetry for traces only
`prometheus-client` is the reference library, pure Python, and Prometheus already pulls on the Pi. OpenTelemetry metrics would need Prometheus' OTLP receiver (`--web.enable-otlp-receiver`, no authentication) or a collector. A dedicated `CollectorRegistry` per `Telemetry` instance keeps tests independent of the global registry. One process, so no multiprocess mode.

### Decision 2: A stage records duration and outcome
`TelemetryPort.stage(name, **labels)` is a context manager around one step. On exit it observes the duration in the histogram `scavengarr_<name>_seconds{labels}` and counts the outcome in `scavengarr_<name>_total{labels, outcome}`. The code sets the outcome (`stage.outcome = "hits"`); an exception sets it when the code did not: `CancelledError` gives `cut` (a deadline or shutdown ended the step), `TimeoutError` gives `timeout`, any other exception `error`. The exception always propagates. Labels known only later (the request's cache state) are set with `stage.label(...)`.

Outcomes without a run (a plugin skipped by its open breaker, a resolution from the cache) are counted with `TelemetryPort.count(name, outcome, **labels)`; they have no duration. Values that are not durations (streams per answer, bytes, the event-loop lag) go through `TelemetryPort.record(name, value, **labels)`.

Durations and outcomes are separate families on purpose: a histogram per plugin and outcome would multiply the series by the outcomes.

### Decision 3: The port lives in the domain, with a no-op default
`domain/ports/telemetry.py` holds the `Protocol`s, the `Literal` stage and value names and `NO_TELEMETRY`, a stateless no-op implementation. Components take `telemetry: TelemetryPort = NO_TELEMETRY`, so tests and other callers need no telemetry and the code needs no `None` checks. The adapter declares each name's labels, buckets and help text in one table; prometheus-client rejects a wrong label name, and the tests run the real adapter.

### Decision 4: Families and labels

| Family | Labels | Outcomes |
|---|---|---|
| `stremio_request` | `source`: `cache`, `stale`, `search`, `joined`, `none` | `streams`, `empty`, `no_title`, `no_plugins` |
| `stremio_phase` | `phase`: `metadata`, `search`, `resolve`, `background_resolve` | metadata `found`/`not_found`; search `ok`; resolve `target`/`done`/`deadline`/`cached` |
| `stremio_streams` (value) | none | streams per answer |
| `plugin_search` | `plugin` | `hits`, `empty`, `error`, `cut`; without run `breaker_open`, `skipped`, `unreachable` |
| `plugin_results_total` (value) | `plugin` | results after validation |
| `hoster_resolve` | `resolver` (name, `direct` for a playlist or content-type probe) | `stream`, `dead`, `unplayable`, `timeout`, `network_error`, `http_error`, `error`, `cut`; without run `cached`, `breaker_open` |
| `hls_proxy` | `kind`: `master`, `playlist`, `segment` | HTTP status of the answer |
| `hls_proxy_bytes_total` (value) | `kind` | bytes sent |
| `event_loop_lag_seconds` (value) | none | lag of a 0.5 s timer |
| `circuit_breaker_open` (scrape) | `breaker`: `plugin`/`hoster`, `name`, `state`: `open`/`half_open` | only breakers that are not closed |
| `container_cpu_seconds_total`, `container_memory_bytes` (scrape) | none | cgroup v2 `cpu.stat`, `memory.current` |
| `process_*`, `python_gc_*`, `python_info`, `scavengarr_build_info` | prometheus-client standard; `version` | |

`error` and `cut` can end every stage. Plugin outcomes after a cut: `stremio_plugin_search_cancelled` stays the log event. `skipped` is a plugin that got no slot before the search deadline; `unreachable` a plugin whose site failed the periodic health check.

Buckets follow the deadlines: requests and phases 0.5, 1, 2, 4, 7, 10, 15, 30, 60 s (answer at the latest after 60 s); plugin searches 1, 2, 4, 7, 10, 15, 30 s (search ends 30 s after the request); resolutions 0.5, 1, 2, 4, 7, 15 s (resolve timeout 15 s); HLS proxy 0.05, 0.1, 0.25, 0.5, 1, 2, 4 s; event-loop lag 0.005 to 2.5 s; streams per answer 0, 1, 2, 3, 5, 8, 13, 21, 34.

About 650 series with 20 stream plugins and 12 resolvers in use; label combinations exist only once they occurred.

### Decision 5: Where the stages sit
- `StremioStreamUseCase.execute()`: the request stage; `metadata` around plugin selection and title lookup; `search` in the shared search task (once per search, not per request); `resolve`/`background_resolve` in `_resolve_as_results_arrive` with the end reason as outcome; the cache answer path counts `resolve` with outcome `cached`.
- `PluginSearchRunner`: the stage in `_search_single_plugin` (from slot to result, as `record_plugin_search` measured); counts for breaker, deadline skip and health check.
- `HosterResolverRegistry`: the stage in `_try_resolver` and around content-type probes; counts for cache hits (the cache entry keeps the resolver name) and open breakers.
- HLS proxy route: one stage per request; its duration ends when the response starts (time to first byte for the player); segment bytes are counted when the body ends.
- `monitor_loop_lag()`: records every sample.

### Decision 6: Scrape-time collectors
Breaker states and container usage are read when Prometheus scrapes, not kept up to date: no cost between scrapes. The breaker collector copies each breaker's state under the GIL. The container collector reads `/sys/fs/cgroup/cpu.stat` and `memory.current` and yields nothing without cgroup v2. Container CPU minus `process_cpu_seconds_total` is Chromium, its driver, Xvfb and the health check.

`/metrics` renders in a worker thread (`asyncio.to_thread`), so a scrape does not hold the event loop.

### Decision 7: The JSON endpoint reads the same metrics
`/api/v1/stats/metrics` keeps its keys. `plugins` comes from the plugin families (`searches` = timed searches, `successes` = `hits` + `empty`, `failures` = `error` + `cut`, `total_results`, `avg_duration_ms` = sum / count). `event_loop` keeps its 5-minute window of samples (p50/p99/max need the samples; the histogram is cumulative).

### Decision 8: Request id
The HTTP middleware that logs `http_request` binds a 12-hex-digit `request_id` with `structlog.contextvars` and sets `X-Request-ID` on the response. Tasks started by the request (a shared search, background resolutions) copy the context and log with the same id. The id is generated; an incoming header is ignored (no untrusted input in logs).

### Decision 9: Tracing on demand
`telemetry.tracing_endpoint` (OTLP/HTTP base URL, `/v1/traces` appended; env `SCAVENGARR_TELEMETRY_TRACING_ENDPOINT`) turns tracing on. Then a `TracerProvider` with a `BatchSpanProcessor` and the OTLP/HTTP exporter is created at startup and flushed at shutdown; otherwise nothing of OpenTelemetry is imported. Every stage except the HLS proxy (a span per segment would bury the requests) is a span named after the stage and its first label (`plugin_search kinoger`), with the outcome as attribute and an error status for `error`. Background tasks copy the context, so a shared search and its resolutions are children of the request that started them. The root span carries the `request_id`. Span attributes never carry URLs (stream URLs carry tokens) or titles.

The backend is Tempo (local storage on disk, TraceQL in Grafana), started on demand with the compose profile `tracing`. Jaeger's in-memory storage would lose all traces at every container update.

## Risks / Trade-offs

- New dependencies: `prometheus-client` is small and pure Python. The OpenTelemetry packages and `protobuf` make the image a few MB bigger; in exchange tracing needs no rebuild.
- A stage name or label typo fails at runtime; the tests use the real adapter, so a typo fails a test.
- Cost estimates come from x86 measurements scaled to the Pi (±50 %); a benchmark in `tests/benchmark/` measures recording and scrape cost, and the Pi's container CPU is visible in the dashboard itself.
- Tempo needs 0.2-0.7 GB RAM while it runs; therefore only on demand.

## Migration Plan

1. Metrics in the core, `/metrics`, JSON compatibility.
2. Request id and tracing (off).
3. Dashboard, docs, scrape snippet; the user adds the scrape job to Prometheus and imports the dashboard.
Rollback: the endpoint and stages are additive; disabling tracing is the default.

## Open Questions

- None blocking. Exemplars (trace ids on histogram buckets) need Prometheus' exemplar storage and the OpenMetrics format; left out until tracing is used.
