# Observability

Scavengarr records the steps of its core as Prometheus metrics: every Stremio stream request, its phases, every plugin search of a Stremio request, every hoster resolution and every HLS proxy request. Prometheus scrapes them from `GET /metrics`, Grafana shows them. The metrics come from real use, so a change of the answer policy, a timeout or a breaker can be judged the next day without a test round. A request id ties the log lines of one request together, and on demand the same steps go to Tempo as traces.

Spec: `openspec/specs/observability/spec.md` (from the change `openspec/changes/archive/2026-10-06-add-observability/`).

## Endpoints

| Endpoint | Content |
|---|---|
| `GET /metrics` | All metrics below, Prometheus text format (0.0.4), rendered in a worker thread |
| `GET /api/v1/stats/metrics` | JSON for a quick look: plugin statistics (from the same metrics), event-loop lag of the last 5 minutes (p50/p99/max), plugin circuit breakers (hoster breakers only in `/metrics`), the 20 hosters probed most often for want of a resolver (`unresolved_hosters`, an open set of names and so no Prometheus label; [Hoster Resolvers](./hoster-resolvers.md#architecture)), what the start restored of the run before (`hoster_state`: `restored_at`, the snapshot's `age_s`, `resolutions`, `redirects`, `breakers`), concurrency pool, shutdown state |
| `GET /api/v1/stats/plugins` | The plugins' long-term record ([Plugin Record](#plugin-record)): per plugin and UTC day `searches`, `results`, `timeouts`, `dropped`, `checks` and `unreachable` marks of the last 180 days, with `last_result_day` and the `unreachable_share` over 30, 90 and 180 days |

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

## Grafana

Import `docker/grafana-dashboard.json` (Dashboards, New, Import) and pick the Prometheus data source. Its panels:

- **Stremio answers:** answers in the range, version, why answers went out (target, done, budget, deadline, cached), search state (cache, stale, search, joined), streams per answer, answer time by search state (p50, p95) and phase times (p95).
- **Plugins:** search time per plugin (p95), outcomes per plugin (a table of plugins by outcome), results per plugin.
- **Hoster resolutions:** outcomes per resolver, resolve time per resolver (p95), the breakers that are open now.
- **HLS proxy:** throughput, time to first byte (p95), answers by status.
- **Resources:** CPU of the container, of Python and of the rest (Chromium), memory, event-loop lag (p99).

Range panels count over the selected time range: pick a day or a week to judge a change. Traffic is low (a few requests per hour), so the time series use 1-hour windows for answer times.

## Alerts

`docker/prometheus-alerts.yml` holds six alert rules. Mount the file into the Prometheus container and name it in `prometheus.yml`:

```yaml
rule_files:
  - /etc/prometheus/scavengarr-alerts.yml
```

Prometheus evaluates the rules and lists the firing alerts under Alerts; a notification (mail, push) needs an Alertmanager (`alerting:` in `prometheus.yml`). Only `ScavengarrDown` names the scrape job (`job="scavengarr"` as in [Prometheus](#prometheus)); rename it there when your job is called otherwise. Check the file after a change with promtool, which ships with Prometheus:

```bash
promtool check rules docker/prometheus-alerts.yml
# without a local Prometheus:
docker run --rm --entrypoint promtool -v ./docker:/rules prom/prometheus check rules /rules/prometheus-alerts.yml
```

`tests/unit/infrastructure/test_alert_rules.py` checks that every family the rules query is one the app exports, so a renamed metric fails the tests instead of silencing its alert.

| Alert | Fires when | Look at |
|---|---|---|
| `ScavengarrDown` (critical) | Prometheus has not scraped the target for 5 min, or the target is gone | `prodctl.py ps`, `prodctl.py logs --since 30m` |
| `ScavengarrPluginWithoutYield` | A plugin was searched in the last 24 h without one hit while at least five plugins had hits (for 1 h): a dead site, a changed layout, a block | the plugin's outcomes (Plugins panels), `prodctl.py logs --grep <plugin>`, its live smoke test |
| `ScavengarrSlowFirstAnswers` | Half of the last hour's answers that waited for a new search (at least 3) took longer than 20 s (for 15 min) | answer and phase times (Stremio answers panels), the reasons of `stremio_resolve_complete` |
| `ScavengarrHosterBreakerOpen` | A hoster's breaker has not closed for 6 h (open or half-open) | `prodctl.py logs --grep hoster_resolve`, the resolver's live smoke test |
| `ScavengarrEventLoopLag` | The event loop's lag p99 is above 100 ms for 15 min: CPU work on the loop delays every timeout | CPU panels, `scripts/stremio_profile.py --py-spy` |
| `ScavengarrNoStreams` | More than half of the last hour's Stremio requests (at least 3) ended `empty` (for 15 min; requests that ended `no_title`, `no_plugins`, `error` or `cut` count only in the denominator) | search state and plugin outcomes |

Limits: the counters restart with the container, and `increase()` sees no growth in a series that appeared with its first count, so a plugin whose only hit in 24 h came right after a restart counts as without yield. A plugin for content the household rarely asks for (an anime site on a day without anime) alerts too; leave it out with a matcher such as `plugin!~"fireani|aniworld"` in all three `scavengarr_plugin_search_total` selectors of the rule (the first counts every outcome, so a plugin that was only skipped, unreachable or behind an open breaker counts as asked).

## How Recording Works

The core records through one port, `TelemetryPort` (`domain/ports/telemetry.py`); `Telemetry` (`infrastructure/telemetry/`) implements it with prometheus-client.

- `stage(name, **labels)` times a step (`with` block). When it ends, the duration goes into the histogram `scavengarr_<stage>_seconds{labels}` and the outcome into the counter `scavengarr_<stage>_total{labels, outcome}`. The code sets the outcome (`stage.outcome = "hits"`); otherwise it is `ok`, `cut` for a cancellation (a deadline, an answer that is due, shutdown), `timeout` for a `TimeoutError` and `error` for any other exception. Exceptions always propagate. `stage.label(...)` sets a label known only later.
- `count(name, outcome, **labels)` counts an outcome without a run (a plugin skipped by its open breaker, a resolution from the cache): no duration.
- `record(name, value, **labels)` records a value that is not a duration (streams per answer, bytes).

The composition root builds it with `create_telemetry(config.telemetry.tracing_endpoint)` and closes it at shutdown. Seven places record: `StremioStreamUseCase` (its search and resolve phases through `TitleSearch` and `ResolveFlow`), `PluginSearchRunner`, `HosterResolverRegistry`, the stealth browser's page gate (`PageGate`), the HLS proxy route with its read-ahead (`SegmentReadAhead`) and the event-loop monitor. Plugins and resolvers contain no metrics code; a new one is recorded without changes. Components get `NO_TELEMETRY` (records nothing) when none is wired in, as in most tests.

Durations and outcomes are separate families on purpose: a histogram per outcome would multiply the series. Label values come only from fixed sets (plugin names, resolver names, breaker keys, the outcomes below), never titles, IMDb ids, URLs, domains or stream ids.

## Metrics

All names start with `scavengarr_`. Outcomes in *italics* are counted without a duration.

| Family | Labels | Values |
|---|---|---|
| `stremio_request_seconds`, `stremio_request_total` | `source`, `outcome` | `source`: `cache` (fresh cache entry), `stale` (stale entry, refreshed in the background), `search` (new search), `joined` (the title's search was running), `none` (ended before the search). `outcome`: `streams`, `empty`, `no_title`, `no_plugins`, `error`, `cut` |
| `stremio_phase_seconds`, `stremio_phase_total` | `phase`, `outcome` | `anime_ids` (a `kitsu:` id translated into the IMDb request): `found`, `not_found`. `series_meta` (the request's Cinemeta meta): `found`, `not_found`, `error`. `metadata` (plugin selection and TMDB titles): `found`, `not_found`. `search` (the shared search, once per search): `ok`, `cut`. `resolve` (resolution while the search runs): `target`, `done`, `budget` (the answer budget passed, the resolutions of the results known by then done), `deadline`, *`cached`* (a cached answer). `background_resolve` (the other links of a cached answer): `done`, `deadline` |
| `stremio_streams` | | Streams per answer (histogram) |
| `plugin_search_seconds`, `plugin_search_total` | `plugin`, `outcome` | Stremio searches only; Torznab searches are not recorded. `hits`, `empty`, `late` (returned after the request's answer budget: its results reach the cache and the next request), `error`, `cut` (shutdown), *`breaker_open`*, *`unreachable`* (failed the periodic health check, or found no reachable domain in this search) |
| `plugin_results_total` | `plugin` | Validated results |
| `hoster_resolve_seconds`, `hoster_resolve_total` | `resolver`, `outcome` | Resolver name, `direct` for content-type probes (playlists, URLs without a resolver). `stream`, `dead`, `unplayable`, `check_error` (the playback check failed), `timeout`, `network_error`, `http_error`, `error`, `cut`, `busy` (a capture got no stealth browser page before its request was due), *`cached`*, *`breaker_open`* |
| `hls_proxy_seconds`, `hls_proxy_total` | `kind`, `outcome` | `master` (the stream's playlist), `playlist`, `segment`, `file` (an address-bound direct file through the file proxy, byte ranges: mostly `206`); outcome is the answer's HTTP status. The duration ends when the answer starts (time to first byte for the player) |
| `hls_proxy_bytes_total` | `kind` | Bytes sent (playlists, segments and files; an aborted transfer's bytes included) |
| `hls_readahead_seconds`, `hls_readahead_total` | `outcome` | The HLS proxy's fetches of segments ahead of the player: `ok`, `failed`, `too_large` (over 16 MiB, left to the player), `throttled` (the CDN answered 429), `cut` (dropped while in flight); and its answers: `hit` (served from memory), `miss`, `dropped` (fetched or in flight, the player did not ask). `hit` over `hit` plus `miss` says how often the read-ahead was there in time |
| `browser_page_wait_seconds`, `browser_page_wait_total` | `kind`, `outcome` | The wait for a stealth browser page, by what it is for (`kind`): `play` (a stream resolved again at play time), `plugin` (a plugin's page, also from Torznab searches and the scoring probes), `capture` (a hoster capture for an answer), `background` (a capture for later requests). `ok` (got the page), `busy` (none came free 3 s before the work was due; work without a due time, from Torznab searches and the scoring probes, waits), `cut` |
| `browser_page_seconds`, `browser_page_total` | `kind`, `outcome` | The work on a stealth browser page, from the grant to the page's end: `ok`, `cut`, `error` |
| `browser_pages` | `state` (`limit`, `in_use`, `waiting`) | The stealth browser's page limit, the pages in use and the page requests waiting, read at scrape time. `PageBudget` moves the limit; each change is logged as `browser_pages_changed` with its reason (`wait`, `memory`, `cpu`, `idle`) |
| `event_loop_lag_seconds` | | How late a 0.5 s timer fired: CPU work on the event loop delays every timeout and deadline by as much |
| `circuit_breaker_open` | `breaker` (`plugin`, `hoster`), `name`, `state` (`open`, `half_open`) | 1 per breaker that is not closed, read at scrape time. Plugin breakers are keyed `plugin:category` |
| `container_cpu_seconds_total`, `container_memory_bytes` | | The container's CPU and memory (cgroup v2 `cpu.stat`, `memory.current`), read at scrape time; absent outside a cgroup v2 container |
| `build_info` | `version`, `commit`, `built` | Which build runs: the app version, the commit and the build time. The image build passes the last two (`build_info.json` beside the package, written by `docker/build_info.py` at image build time from the `SCAVENGARR_COMMIT` and `SCAVENGARR_BUILT` build arguments or the checkout's `.git/HEAD` and the clock; the environment variables of the same names override it; `docs/features/configuration.md` → Build Identity); without either they are `unknown`. `/api/v1/healthz`, `/api/v1/stremio/health` and the startup line `app_startup_complete` report the same three |

prometheus-client adds `process_*` (the Python process), `python_gc_*` and `python_info`. The container's CPU minus `process_cpu_seconds_total` is Chromium, its driver, Xvfb and the health checks.

Bucket bounds follow the deadlines: requests and phases 0.5, 1, 2, 4, 7, 10, 15, 30, 60 s (answer at the latest after 60 s); plugin searches 1, 2, 4, 7, 10, 15, 30 s (the search ends 30 s after the request); resolutions 0.5, 1, 2, 4, 7, 15 s (resolve timeout 15 s); HLS proxy 0.05 to 4 s; browser page waits 0.1, 0.5, 1, 2, 4, 7, 10, 15, 30, 60 s and work 0.5 to 60 s; event-loop lag 5 ms to 2.5 s; streams per answer 0, 1, 2, 3, 5, 8, 13, 21, 34.

## Queries

```promql
# p95 answer time by search state
histogram_quantile(0.95, sum by (le, source) (rate(scavengarr_stremio_request_seconds_bucket[1d])))

# Why answers went out (target, done, budget, deadline, cached)
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

# p90 wait for a stealth browser page, by what it is for
histogram_quantile(0.9, sum by (le, kind) (rate(scavengarr_browser_page_wait_seconds_bucket[1h])))
```

## Request Id

Every HTTP request gets a 12-hex-digit `request_id` in the structlog context: all its log lines carry it, and so do the lines of the tasks it starts (the shared search, background resolutions), which copy the context. The response has it as the `X-Request-ID` header. The id is generated; a client's own `X-Request-ID` is ignored (untrusted input in the logs).

Each request ends with one `http_request` line: method, path, query, status, duration and client address. The query keeps its values only for Torznab's own parameters on Torznab paths (`t`, `q`, `cat`, `extended`, `offset`, `limit`); every other value is logged as `***`. Prowlarr sends its `apikey`, and proxied HLS paths carry the CDN's tokens and the client's address (`i=`, the VPN's exit address in production), also under the names `t` and `q`.

Log lines name a CDN by its second-level domain (`cdn=dropcdn`, `extract_domain()`), never by its URL: the path and the query of a video URL carry tokens and the client's address. That holds for the HLS proxy's CDN errors (`hls_proxy_cdn_error`, `hls_proxy_network_error`), `/play`'s redirect (`stremio_play_resolved`), the playback check (`playback_check_failed`) and the resolvers' results (code review, 2026-10-06). Third-party records show URLs by their origin only (`_shorten_urls` in `infrastructure/logging/setup.py`): httpx's line for each outgoing request, written at `logging.level: DEBUG` only, reads `HTTP Request: GET https://cdn.example.net "HTTP/1.1 200 OK"`, and the same holds for URLs in their tracebacks. uvicorn's own access log is off (`access_log=False`): it repeated each request with its query unmasked.

## Plugin Record

The metrics restart with the process, and the scoring snapshots age out in weeks; whether a plugin whose site died stays, is disabled by default or is removed needs months. `PluginHistory` (`infrastructure/plugins/history.py`) counts per plugin and UTC day:

| Counter | Counted by | When |
|---|---|---|
| `searches` | `PluginSearchRunner` | Every plugin search of a Stremio request that ran (a plugin skipped by its breaker or the health check is not counted) |
| `results` | `PluginSearchRunner` | The validated results a search gave |
| `timeouts` | `PluginSearchRunner` | A search cut by the plugin timeout (its full `plugin_timeout_seconds` from the moment it held a slot) |
| `dropped` | `PluginSearchRunner` | The results of a series request the episode filter dropped: another season or episode, or no episode number and no link labelled with the requested episode (a show page) |
| `checks` | `PluginHealthMonitor` | Every check of the plugin's site with a verdict (a check in which no site answered counts nothing: the own network was down) |
| `unreachable` | `PluginHealthMonitor`, `PluginSearchRunner` | The checks that found the site down (after the 30 s retry), and the Stremio searches in which the plugin's domain check found no domain |

The record is one value `plugin_history:v1` in the cache backend (diskcache or Redis; a year's TTL renewed with every write), written every minute when it changed and at shutdown, with the days older than 180 dropped; the start restores it (`plugin_history_restored` with the plugins and days, `plugin_history_discarded` with a `reason` for another version or an unreadable record, `plugin_history_restore_failed`, `plugin_history_save_failed`). `GET /api/v1/stats/plugins` reports `today`, `window_days` and per plugin `last_result_day`, `unreachable_share` over `30`, `90` and `180` days (`null` without a check) and the `days` with their counters; the production digest (`prodctl.py digest`, below) prints the same per plugin. The record is evidence, not a rule: the lifecycle decision for a dead site stays the maintainer's.

## Production Diagnostics

`scripts/prodctl.py` reads a deployment behind Portainer from a development machine (credentials: `PORTAINER_URL` and `PORTAINER_API_KEY` in `.env.devcontainer`). It prints everything through `portainer.mask()`: URLs keep only scheme and host, IP addresses and secret-like values are replaced. A budget shared by all processes on the machine allows 100 Portainer requests per minute; overloaded or unreachable Portainer requests are retried with backoff, an exec only when Portainer refused it before it ran.

```bash
poetry run python scripts/prodctl.py ps                       # containers and their state
poetry run python scripts/prodctl.py stats                    # CPU cores, memory, processes, network
poetry run python scripts/prodctl.py logs --since 30m --grep kinoger --fields plugin,duration_ms
poetry run python scripts/prodctl.py logs --since 7d --grep config_ --width 0  # the config it runs with
poetry run python scripts/prodctl.py metrics --grep plugin_search_seconds_count
poetry run python scripts/prodctl.py state --keys circuit_breaker,event_loop
poetry run python scripts/prodctl.py logs --since 1h --grep hoster_state_   # what a restart restored
poetry run python scripts/prodctl.py probe tasks              # where the asyncio tasks wait
poetry run python scripts/prodctl.py digest --since 24h       # one report of the window (--json: its data)
```

`digest` is the round's table in one command (the numbers the maintainer read from `metrics` and `logs` by hand before): one log request and one exec in the container (`/metrics` and `/api/v1/stats/plugins` together), so two Portainer requests plus the exec's own; the computation lives in `scripts/digest.py`. Its sections: *Requests* (the stream requests of the window joined over `request_id`: by source `search`, `joined`, `stale`, `cache` or `none`, with their count, the p50 and p90 of the answer time from the access record, the streams per answer and the answers without a stream; one line below counts the requests per id scheme of the request path, `tt`, `kitsu` and `tmdb`, so the maintainer sees which catalogs the players use), *Plugins* (from the [Plugin Record](#plugin-record): per plugin the searches, results, timeouts, checks and unreachable marks of the record's days that overlap the window, the last result and the unreachable shares over 30, 90 and 180 days, sorted by results), *Hosters* (per resolver the resolutions, their mean duration and the outcomes from `scavengarr_hoster_resolve_*`, since the process start), *Breakers* (the breakers not closed now from `circuit_breaker_open`, the openings in the window from `circuit_breaker_opened` and the searches and resolutions an open one skipped), *Noise* (the five most frequent events and their share of the records, health checks excluded) and *Errors* (the records at `error` and above: their count and the three most frequent messages with the exception's last line). The log window defaults to 24 h; the metrics count since the start, so a restart inside the window shows as a short uptime in the first line.

A start logs what it restored of the run before ([State across restarts](hoster-resolvers.md#registry-features)): `hoster_state_restored` with the resolutions, redirects and breakers restored and the snapshot's `age_s` (zeros on the first start), or `hoster_state_discarded` with its `reason` (`version`, `malformed`, `unreadable`); a read that fails ends as `hoster_state_discarded` (`unreadable`) and the snapshot is deleted, `hoster_state_restore_failed` follows only when that delete fails too, and `hoster_state_save_failed` reports a failed write — with the Redis backend, which swallows `RedisError`, look for `redis_get_error`/`redis_set_error` instead (a failed read then logs a zero `hoster_state_restored`).

`logs` renders JSON records as `time level event key=value ...` and drops the liveness and readiness checks (`--health` keeps them, `--raw` prints the masked lines as logged). Records are cut to 400 characters; `--width 0` prints them whole, for a traceback's last lines (`--fields exception --width 0`). `metrics` and `state` fetch `GET /metrics` and `GET /api/v1/stats/metrics` inside the container.

The search's log lines ([Stremio Addon](stremio-addon.md#request-flow-stream-resolution)): `stremio_search_complete` per request with `source`, `complete` (no plugin missing from the answer's entry, no search running for it), `missing` (the plugins still to come), `dropped` (results the episode filter dropped) and the counts; `stremio_search_completion` when the background search for an entry's missing plugins ends (`plugins`, `outcome` `complete` or `partial`, `unfinished`, `result_count`); `stremio_stream_response` with the route's `source`, `complete` and `missing_count`, the values of its `X-Cache` and `X-Search-Complete` headers. `prodctl.py logs --grep stremio_search_complete` shows whether late plugins empty `missing`.

`probe` runs a Python file inside the container (`python -c`, so it sees the app's code, config and cache): `resources` (cgroup limits and pressure, memory, the processes with the most memory), `tasks` (`python -m asyncio pstree` of the app process), `links ID ...` (stored stream links: hoster, title, CDN domain, age), `redis keys|ttl|get` (read-only Redis commands) `anime_ids [KITSU_ID[:EPISODE] ...]` (what a `kitsu:` request maps to through Kitsu's API, the public anime id lists and the Anime Kitsu addon, and what aniworld and fireani find for it; the spike in `docs/plans/anime-ids-spike.md`) and `hls_throughput [--hoster firestream] [--segments 10]` (the CDN's throughput from the Pi: the hoster's newest stored HLS link, its highest variant, four runs of 10 segments each, one connection, read-ahead 2, 3 byte ranges, both at once; prints Mbit/s per run, the title's bitrate from the segment bytes and `EXTINF` durations, whether the CDN honoured the ranges and the connections a stutter-free playback needs; `--imdb ID` asks the app for the title's streams first, which fills its caches like a Stremio request). Probes live in `scripts/probes/` and change nothing; `probe path/to/file.py` runs an ad-hoc one.

## Tracing on Demand

Traces show one request as a tree: the request, its phases, each plugin search and each hoster resolution, with durations and outcomes. They cost a backend that runs around the clock, so they are off by default and meant for looking into a problem.

1. Start Tempo: `docker compose --profile tracing up -d tempo` (`docker/tempo.yaml`: OTLP/HTTP on 4318, query API on 3200, traces kept 3 days, 512 MB memory limit).
2. Set `telemetry.tracing_endpoint` (or `SCAVENGARR_TELEMETRY_TRACING_ENDPOINT`) to `http://tempo:4318` and restart Scavengarr. Behind a VPN container, publish Tempo's port 4318 (`- "4318:4318"`) and use the host's IP: name lookups would go through the VPN.
3. Add Tempo to Grafana as a data source (`http://<host>:3200`) and search with TraceQL, for example `{ name = "stremio_request" && duration > 10s }` or `{ span.request_id = "a1b2c3d4e5f6" }` with the id from a log line.

Every stage but the HLS proxy (a span per segment would bury the requests) is a span named after the stage and its subject: `stremio_request`, `stremio_phase search`, `plugin_search kinoger`, `hoster_resolve voe`, `browser_page_wait capture` (a request's queueing for the stealth browser). Spans carry the stage's labels, its outcome and a few details (IMDb id, content type); an error sets the span's status to the exception type, not its message. They never carry URLs (stream URLs hold tokens) or titles. The root span carries the `request_id`. Spans go out in batches every 5 s from a background thread; at shutdown the rest gets at most 3 s, also when the endpoint does not answer, and what has not gone out by then is dropped.

Without an endpoint the trace SDK, the exporter and protobuf are not loaded and no export thread runs. FastAPI's own OpenTelemetry integration (switched on by `OTEL_EXPORTER_OTLP_ENDPOINT`) is separate; Scavengarr does not use it.

## Cost

Measured on x86 with `tests/benchmark/test_telemetry_overhead.py` (run manually: `poetry run pytest tests/benchmark/test_telemetry_overhead.py -s`); the Raspberry Pi 4 is about 3-4 times slower:

| | Cost |
|---|---|
| One stage | 5.6 µs (a first stream request records about 60: about 1 ms on the Pi, against 9.2 s of CPU for the request) |
| One count | 2.2 µs |
| One stage with tracing on | 32 µs |
| One scrape of 672 series (a busy day: 20 plugins, 12 resolvers) | 4.4 ms (about 15 ms on the Pi; at 60 s about 20 s of CPU per day) |

The benchmark fails above 50 µs per stage or 20 ms per scrape.

Label combinations exist only once they occurred, so the series grow with the plugins and resolvers in use. Nothing runs between scrapes except the event-loop timer that already ran before.

Tracing, when on, adds a span per stage (about 60-100 per first stream request) and Tempo's 0.2-0.7 GB of memory while it runs. The OpenTelemetry packages add about 5 MB to the image; installed, they cost about 2 MB of memory even with tracing off, as redis-py loads the SDK's metric types when they are there.
