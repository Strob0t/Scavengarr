# Observability

Scavengarr records the steps of its core as Prometheus metrics: every Stremio stream request, its phases, every plugin search, every hoster resolution and every HLS proxy request. Prometheus scrapes them from `GET /metrics`, Grafana shows them. The metrics come from real use, so a change of the answer policy, a timeout or a breaker can be judged the next day without a test round.

Change spec: `openspec/changes/add-observability/`.

## Endpoints

| Endpoint | Content |
|---|---|
| `GET /metrics` | All metrics below, Prometheus text format (0.0.4), rendered in a worker thread |
| `GET /api/v1/stats/metrics` | JSON for a quick look: plugin statistics (from the same metrics), event-loop lag of the last 5 minutes (p50/p99/max), circuit breakers, concurrency pool, shutdown state |

## Prometheus

Add a scrape job to `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: scavengarr
    scrape_interval: 60s
    static_configs:
      - targets: ["scavengarr:7979"]
```

The target is the host or container where Prometheus reaches port 7979. When Scavengarr shares the network of a VPN container (`network_mode: service:<vpn>`), the target is that container's name. Keep the interval at 60 s: the cost of the metrics is the cost of the scrapes (see [Cost](#cost)), 15 s would be four times as much.

## How Recording Works

The core records through one port, `TelemetryPort` (`domain/ports/telemetry.py`); `Telemetry` (`infrastructure/telemetry/`) implements it with prometheus-client.

- `stage(name, **labels)` times a step (`with` block). When it ends, the duration goes into the histogram `scavengarr_<stage>_seconds{labels}` and the outcome into the counter `scavengarr_<stage>_total{labels, outcome}`. The code sets the outcome (`stage.outcome = "hits"`); otherwise it is `ok`, `cut` for a cancellation (a deadline, an answer that is due, shutdown), `timeout` for a `TimeoutError` and `error` for any other exception. Exceptions always propagate. `stage.label(...)` sets a label known only later.
- `count(name, outcome, **labels)` counts an outcome without a run (a plugin skipped by its open breaker, a resolution from the cache): no duration.
- `record(name, value, **labels)` records a value that is not a duration (streams per answer, bytes).

Five places record: `StremioStreamUseCase`, `PluginSearchRunner`, `HosterResolverRegistry`, the HLS proxy route and the event-loop monitor. Plugins and resolvers contain no metrics code; a new one is recorded without changes. Components get `NO_TELEMETRY` (records nothing) when none is wired in, as in most tests.

Durations and outcomes are separate families on purpose: a histogram per outcome would multiply the series. Label values come only from fixed sets (plugin names, resolver names, breaker keys, the outcomes below), never titles, IMDb ids, URLs, domains or stream ids.

## Metrics

All names start with `scavengarr_`. Outcomes in *italics* are counted without a duration.

| Family | Labels | Values |
|---|---|---|
| `stremio_request_seconds`, `stremio_request_total` | `source`, `outcome` | `source`: `cache` (fresh cache entry), `stale` (stale entry, refreshed in the background), `search` (new search), `joined` (the title's search was running), `none` (ended before the search). `outcome`: `streams`, `empty`, `no_title`, `no_plugins`, `error`, `cut` |
| `stremio_phase_seconds`, `stremio_phase_total` | `phase`, `outcome` | `metadata` (plugin selection and TMDB titles): `found`, `not_found`. `search` (the shared search, once per search): `ok`, `cut`. `resolve` (resolution while the search runs): `target`, `done`, `deadline`, *`cached`* (a cached answer). `background_resolve` (the other links of a cached answer): `done`, `deadline` |
| `stremio_streams` | | Streams per answer (histogram) |
| `plugin_search_seconds`, `plugin_search_total` | `plugin`, `outcome` | `hits`, `empty`, `error`, `cut` (search deadline or shutdown), *`breaker_open`*, *`skipped`* (no slot before the search deadline), *`unreachable`* (failed the periodic health check) |
| `plugin_results_total` | `plugin` | Validated results |
| `hoster_resolve_seconds`, `hoster_resolve_total` | `resolver`, `outcome` | Resolver name, `direct` for content-type probes (playlists, URLs without a resolver). `stream`, `dead`, `unplayable`, `timeout`, `network_error`, `http_error`, `error`, `cut`, *`cached`*, *`breaker_open`* |
| `hls_proxy_seconds`, `hls_proxy_total` | `kind`, `outcome` | `master` (the stream's playlist), `playlist`, `segment`; outcome is the answer's HTTP status. The duration ends when the answer starts (time to first byte for the player) |
| `hls_proxy_bytes_total` | `kind` | Bytes sent (playlists and segments) |
| `event_loop_lag_seconds` | | How late a 0.5 s timer fired: CPU work on the event loop delays every timeout and deadline by as much |
| `circuit_breaker_open` | `breaker` (`plugin`, `hoster`), `name`, `state` (`open`, `half_open`) | 1 per breaker that is not closed, read at scrape time. Plugin breakers are keyed `plugin:category` |
| `container_cpu_seconds_total`, `container_memory_bytes` | | The container's CPU and memory (cgroup v2 `cpu.stat`, `memory.current`), read at scrape time; absent outside a cgroup v2 container |
| `build_info` | `version` | App version |

prometheus-client adds `process_*` (the Python process), `python_gc_*` and `python_info`. The container's CPU minus `process_cpu_seconds_total` is Chromium, its driver, Xvfb and the health checks.

Bucket bounds follow the deadlines: requests and phases 0.5, 1, 2, 4, 7, 10, 15, 30, 60 s (answer at the latest after 60 s); plugin searches 1, 2, 4, 7, 10, 15, 30 s (the search ends 30 s after the request); resolutions 0.5, 1, 2, 4, 7, 15 s (resolve timeout 15 s); HLS proxy 0.05 to 4 s; event-loop lag 5 ms to 2.5 s; streams per answer 0, 1, 2, 3, 5, 8, 13, 21, 34.

## Queries

```promql
# p95 answer time by search state
histogram_quantile(0.95, sum by (le, source) (rate(scavengarr_stremio_request_seconds_bucket[1d])))

# Why answers went out (target, done, deadline, cached)
sum by (outcome) (increase(scavengarr_stremio_phase_total{phase="resolve"}[1d]))

# p95 search time and outcomes per plugin
histogram_quantile(0.95, sum by (le, plugin) (rate(scavengarr_plugin_search_seconds_bucket[1d])))
sum by (plugin, outcome) (increase(scavengarr_plugin_search_total[1d]))

# Outcomes per resolver
sum by (resolver, outcome) (increase(scavengarr_hoster_resolve_total[1d]))

# Chromium and the rest of the container, in cores
rate(scavengarr_container_cpu_seconds_total[5m]) - rate(process_cpu_seconds_total[5m])

# HLS proxy throughput
sum(rate(scavengarr_hls_proxy_bytes_total[5m]))
```

## Cost

Measured on x86 (the Raspberry Pi 4 is about 3-4 times slower):

| | Cost |
|---|---|
| One stage | 5.8 µs (a first stream request records about 60: about 1 ms on the Pi, against 9.2 s of CPU for the request) |
| One count | 2.1 µs |
| One scrape of 637 series | 4.2 ms (about 15 ms on the Pi; at 60 s about 20 s of CPU per day) |

Label combinations exist only once they occurred, so the series grow with the plugins and resolvers in use. Nothing runs between scrapes except the event-loop timer that already ran before.
