# Change: Add Observability (Prometheus Metrics, Request Id, On-Demand Tracing)

## Why

The only metrics today are the JSON of `/api/v1/stats/metrics`: averages per plugin since the start, the event-loop lag of the last 5 minutes and the circuit breaker states. They have no distributions (p95), no phases of a Stremio request, no hoster outcomes and no reason why an answer went out. Every tuning decision of the last rounds (answer policy, timeouts, breakers, dead plugins and hosters) needed a manual end-to-end round on the Raspberry Pi plus a log analysis. Such a round costs the Pi minutes of CPU (17 first searches took about 2.6 min) and still shows a few dozen requests only.

Metrics from real use answer the same questions all the time, without a test load. One request can only be reconstructed from log time windows; a request id in the log context makes that direct.

The user set a hard limit: recording, collecting and showing the metrics must cost far less compute than the service itself. On the Pi a first stream request costs 9.2 s CPU (2.5 s Python, 6.7 s Chromium) and playing through the HLS proxy about 4.4 s CPU per minute.

## What Changes

- **Prometheus endpoint** `GET /metrics` with `prometheus-client` (new dependency): about 12 metric families, labels only from small fixed value sets (plugin names, resolver names, outcomes), never titles, ids, URLs or domains.
- **One recording point in the core**: a `TelemetryPort` with `stage()`. A stage times a step, records its duration and its outcome (`cut` for a cancellation, `error` for an exception) and, with tracing on, is a span. Five core places use it: the Stremio stream use case (request, phases, streams per answer), the plugin search runner (every plugin search), the hoster resolver registry (every resolution), the HLS proxy route and the event-loop monitor. No plugin or resolver changes.
- **Collected at scrape time**: open circuit breakers (plugins and hosters), container CPU and memory (cgroup v2: Python, Chromium and the rest of the container), the Python process.
- **`/api/v1/stats/metrics`** keeps its JSON shape and reads the same metrics; `MetricsCollector` and its plugin counters are replaced.
- **Request id**: every HTTP request gets a `request_id` in the structlog context (all its log lines, also of the searches it starts) and an `X-Request-ID` response header.
- **On-demand tracing**: `telemetry.tracing_endpoint` (OTLP/HTTP) turns OpenTelemetry spans on for the same stages. Off by default; the OpenTelemetry SDK is imported only when it is on. Tempo comes as the compose profile `tracing`.
- **Grafana dashboard** (JSON in the repo) and the Prometheus scrape snippet in the docs.

## Impact

- Affected specs: new capability `observability`.
- Affected code:
  - `src/scavengarr/domain/ports/telemetry.py` (new): `TelemetryPort`, `Stage`, stage and value names.
  - `src/scavengarr/infrastructure/telemetry/` (new): Prometheus families, the port implementation, scrape-time collectors, OpenTelemetry setup, event-loop monitor; replaces `infrastructure/metrics.py`.
  - `src/scavengarr/application/use_cases/stremio_stream.py`, `application/stremio/plugin_search.py`: stages instead of `record_plugin_search`.
  - `src/scavengarr/infrastructure/hoster_resolvers/registry.py`: one stage per resolution, events for cache hits and open breakers.
  - `src/scavengarr/interfaces/api/stremio/router.py` (HLS proxy), `interfaces/app.py` (`/metrics`, request id), `interfaces/composition.py` (wiring, shutdown), `interfaces/api/stats/router.py` (JSON from the new metrics), `infrastructure/config/schema.py` (`telemetry` section).
- Dependencies: `prometheus-client` (pure Python); `opentelemetry-sdk` and `opentelemetry-exporter-otlp-proto-http` (bring `protobuf`), imported only with tracing on.
- Deployment: Prometheus scrapes `<scavengarr host>:7979/metrics` every 60 s; Tempo only when tracing is wanted.
- Cost (estimates, checked by a benchmark): about 10 µs per recorded stage (under 1 ms per first stream request); a scrape of about 650 series takes a few ms, about 20-30 s CPU per day at 60 s on the Pi, under 1 % of what the container uses.
