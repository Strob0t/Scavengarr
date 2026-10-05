## 1. Metrics in the core

- [x] 1.1 Add `prometheus-client` to `pyproject.toml`.
- [x] 1.2 `domain/ports/telemetry.py`: `Stage`, `TelemetryPort`, stage and value names, `NO_TELEMETRY` (tests first).
- [x] 1.3 `infrastructure/telemetry/`: family table, `Telemetry` (stage, count, record, render), event-loop monitor moved from `infrastructure/metrics.py`; tests for outcomes, cut, error, labels set late.
- [x] 1.4 Scrape-time collectors: circuit breakers, cgroup v2 container usage, build info; tests with a temporary cgroup directory.
- [x] 1.5 Plugin search runner: stage per search, counts for breaker, deadline skip and health check, results; replace `record_plugin_search`.
- [x] 1.6 Stremio stream use case: request stage with source, phases, end reasons, streams per answer.
- [x] 1.7 Hoster resolver registry: stage per resolution and probe, counts for cache hits and open breakers.
- [x] 1.8 HLS proxy route: stage per request, bytes per segment.
- [x] 1.9 `GET /metrics` (rendered in a thread); `/api/v1/stats/metrics` from the new metrics; remove `MetricsCollector`.
- [x] 1.10 Wiring in `composition.py`; e2e test of `/metrics` after a Stremio request.

## 2. Request id and tracing

- [x] 2.1 Request id in the `http_request` middleware: structlog contextvar, `X-Request-ID` header; tests.
- [x] 2.2 `telemetry.tracing_endpoint` config (YAML, env), docs.
- [x] 2.3 OpenTelemetry setup imported only when on; spans for stages except the HLS proxy; root span with `request_id`; flush at shutdown; tests with an in-memory exporter.
- [x] 2.4 Compose profile `tracing` with Tempo and its config.

## 3. Dashboard, docs, overhead

- [x] 3.1 Benchmark `tests/benchmark/test_telemetry_overhead.py` (stage cost, scrape of 650 series).
- [x] 3.2 Grafana dashboard JSON and the Prometheus scrape snippet.
- [x] 3.3 Docs: `docs/features/observability.md`, configuration, architecture, README, CHANGELOG, AGENTS.md.
- [ ] 3.4 After the deploy: check `/metrics` on the Pi and the scrape cost.
