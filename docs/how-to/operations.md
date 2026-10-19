# Operate production

> **Status:** how-to
> **As of:** abec3c31

## Goal

Read a production deployment behind Portainer from a development machine without changing it: its containers, logs, metrics and state, a report of a window, the check after a deploy, and probes that run inside the container or against the sites.

## Prerequisites

- `PORTAINER_URL` and `PORTAINER_API_KEY` in `.env.devcontainer` (`scripts/portainer.py` reads them).
- Portainer access to the production containers; the container is `scavengarr` unless `-c`/`--container` names another.
- For `check`: the public addon URL, up to `/api/v1/stremio`.

`scripts/prodctl.py` prints everything through `portainer.mask()`: URLs keep only scheme and host, IP addresses and secret-like values are replaced.
Its errors too: an HTTP error from Portainer (a 403 for a container outside the label access, a 500 for an unknown one) ends with one masked line on stderr, such as `logs failed: Client error '403 Forbidden' for url 'http://<ip>:9000/…'`, and exit code 1 instead of a traceback with Portainer's address and the request's path.
A budget shared by all processes on the machine allows 100 Portainer requests per minute.
Overloaded or unreachable Portainer requests are retried with backoff, an exec only when Portainer refused it before it ran.
Rebuilds and restarts stay with the maintainer.

## Steps

### 1. Look at the containers

```bash
poetry run python scripts/prodctl.py ps                       # containers and their state
poetry run python scripts/prodctl.py stats                    # CPU cores, memory, processes, network
```

### 2. Read the logs

```bash
poetry run python scripts/prodctl.py logs --since 30m --grep kinoger --fields plugin,duration_ms
poetry run python scripts/prodctl.py logs --since 7d --grep config_ --width 0   # the config it runs with
poetry run python scripts/prodctl.py logs --since 1h --grep hoster_state_       # what a restart restored
```

- `logs` renders JSON records as `time level event key=value ...` and drops the liveness and readiness checks; `--health` keeps them, and `--raw` prints the masked lines as logged.
- `--since` defaults to 15 minutes and `--limit` to the last 60 lines.
- Records are cut to 400 characters; `--width 0` prints them whole, for a traceback's last lines (`--fields exception --width 0`).
- `--grep` is a case-insensitive regex over the raw JSON record.

A start logs what it restored of the run before ([State across restarts](../current/hoster-resolvers.md#caches-and-state-across-restarts)):

- `hoster_state_restored` with the resolutions, redirects and breakers restored and the snapshot's `age_s` (zeros on the first start);
- or `hoster_state_discarded` with its `reason` (`version`, `malformed`, `unreadable`); a read that fails ends as `unreadable` and the snapshot is deleted;
- `hoster_state_restore_failed` only when that delete fails too, and `hoster_state_save_failed` for a failed write;
- with the Redis backend, which swallows `RedisError`, `redis_get_error` and `redis_set_error` instead (a failed read then logs a zero `hoster_state_restored`).

The search's log lines ([Stremio addon](../current/stremio-addon.md#observability)):

- `stremio_search_complete` per request with `source`, `complete` (no plugin missing from the answer's entry, no search running for it), `missing` (the plugins still to come), `dropped` (results the episode filter dropped) and the counts;
- `stremio_search_completion` when the background search for an entry's missing plugins ends (`plugins`, `outcome` `complete` or `partial`, `unfinished`, `result_count`);
- `stremio_stream_response` with the route's `source`, `complete` and `missing_count`, the values of its `X-Cache` and `X-Search-Complete` headers.

`prodctl.py logs --grep stremio_search_complete` shows whether late plugins empty `missing`.

### 3. Read the metrics and the state

```bash
poetry run python scripts/prodctl.py metrics --grep plugin_search_seconds_count
poetry run python scripts/prodctl.py state --keys circuit_breaker,event_loop
```

`metrics` and `state` fetch `GET /metrics` and `GET /api/v1/stats/metrics` inside the container ([Metrics](../reference/metrics.md)).

### 4. Read a window in one report

```bash
poetry run python scripts/prodctl.py digest --since 24h       # --json: its data
```

`digest` costs one log request and one exec in the container (`/metrics` and `/api/v1/stats/plugins` together); the computation lives in `scripts/digest.py`.
The log window defaults to 24 h, while the metrics count since the start, so a restart inside the window shows as a short uptime in the first line.
Its sections:

- **Requests:** the stream requests of the window joined over `request_id`, by source (`search`, `joined`, `stale`, `cache`, `none`), with their count, the p50 and p90 of the answer time, the streams per answer and the answers without a stream; one line counts the requests per id scheme of the request path (`tt`, `kitsu`, `tmdb`).
- **Plugins:** from the [plugin record](../current/observability.md#the-plugin-record), per plugin the searches, results, timeouts, dropped results, challenges, checks and unreachable marks of the record's days that overlap the window, the last result and the unreachable shares over 30, 90 and 180 days, sorted by results.
- Below the plugin table, one line names the plugins whose searches met a challenge with their counts, unsolved ones beside them, and the rule for a solver trial over the record's last 7 days: only a plugin whose unsolved challenges exceed 5 % of its searches.
- Another line names the plugins whose unreachable marks exceed their checks: days recorded before the searches' own counter.
- **Hosters:** per resolver the resolutions, their mean duration and the outcomes from `scavengarr_hoster_resolve_*`, since the process start.
- **Breakers:** the breakers not closed now from `circuit_breaker_open`, the openings in the window from `circuit_breaker_opened` and the searches and resolutions an open one skipped.
- **Noise:** the five most frequent events and their share of the records, health checks excluded.
- **Errors:** the records at `error` and above, with their count and the three most frequent messages with the exception's last line.

### 5. Check a deploy

```bash
poetry run python scripts/prodctl.py check --base https://scavengarr.example/api/v1/stremio --insecure
```

`check` is the post-deploy check as one command, judged in `scripts/deploy_check.py` (tested from fixture records and answers, no network) and printed as one table; it exits 1 when a check failed.
It costs ten Portainer requests (the container list, its inspection for the start time, the log since that start, `/metrics` through one exec of three requests, and the endpoint lookup with its container listing for each of its two clients) and four stream requests through the public addon URL; `--insecure` skips the TLS check for a local CA.

1. The container is running and healthy.
2. The newest `app_startup_complete` record is younger than the container's start and names the version.
3. `config_unknown_keys` of that start is shown as a warning, not a failure.
4. `hoster_state_restored` and `plugin_history_restored` of that start are shown with their counts.
5. Three anime ids (`series/<Anime A>:5:2`, `series/kitsu:<Anime A>:1089`, `series/<Anime H>:2:1`) answer streams whose description carries `S05E02`, `S22E04` and `S02E01`; one stream of another episode fails the check.
6. The log holds each request's `http_request` record (`X-Request-ID`), and the placement record `stremio_episode_ref` is shown when the log level has it (it is DEBUG, so production prints `no placement record (DEBUG)`).
7. `movie/<Movie F>` answers at least one stream within `--deadline` (default 60 s, `stremio.stream_deadline_seconds`); `--timeout` bounds each stream request (default 120 s).
8. The readiness probe's gauge `scavengarr_network_ok` reads 1 for the cache backend and the DNS ([Readiness](../current/observability.md#readiness)); a check at 0 fails, and no sample yet (nothing asked `/api/v1/readyz` since the start) is a warning.

The stream requests are its only writes to production (four searches).
The stream descriptions are the only site data in the table, and the output passes `portainer.mask()`.
A container replaced during the check needs the check again.

### 6. Run a probe

```bash
poetry run python scripts/prodctl.py probe tasks              # where the asyncio tasks wait
poetry run python scripts/prodctl.py probe resources          # cgroup limits and pressure, memory, top processes
poetry run python scripts/prodctl.py probe links              # stream links of the last hour: hoster, CDN, age
PYTHONPATH=src xvfb-run -a poetry run python scripts/probes/series_episodes.py   # in the dev container
```

`probe NAME [ARGS]` runs a file of `scripts/probes/` inside the container (`python -c`, so it sees the app's code, config and cache), `probe path/to/file.py` an ad-hoc one; `--timeout` (default 300 s) bounds it and `--env KEY=VALUE` passes a variable.
The probes change nothing.
In a worktree the dev-container probes run with `PYTHONPATH=src`, so they import the worktree's code.

| Probe | Runs in | Shows |
|---|---|---|
| `resources` | container | Cgroup limits and pressure, CPUs, memory, load, and the processes with the most memory. |
| `tasks` | container | Where the app's asyncio tasks wait (`python -m asyncio pstree` of the app process). |
| `links [ID ...]` | container | Stored stream links: hoster, release title, HLS, CDN domain, age. |
| `redis_cache keys\|ttl\|get` | container | The app's Redis cache through read-only commands (`SCAN`, `TYPE`, `TTL`, `STRLEN`, `GET`). |
| `anime_ids [KITSU_ID[:EPISODE] ...]` | container | What a `kitsu:` request maps to through Kitsu's API, the public anime id lists and the Anime Kitsu addon, and what aniworld and fireani find for it. |
| `hls_throughput [--hoster H] [--segments N] [--in-flight 3,4,5] [--imdb ID]` | container | A CDN's HLS throughput from the production host: one connection, the read-ahead, byte ranges, both at once. |
| `stream_quality` | container | The stored resolutions of the snapshot `hoster_state:v1` per resolver: streams, dead links, HLS, quality classes, sizes; names and counts only. |
| `capture_state` | container | The browser's kept capture states and clearance cookies as names, lengths and lifetimes, never a value. |
| `series_episodes [--json FILE] [ID ...]` | dev container | Series episodes per plugin: what the episode filter keeps, and its leaks. |
| `title_match [--json FILE]` | dev container | Title matching per plugin: the queries sent, the raw titles, the verdicts. |
| `resolver_urls [--avoid FILE]` | dev container | The hoster page URLs of the live resolver tests (`.cache/live/resolver-urls.json`); `--avoid` takes each resolver's next link. |
| `header_binding [--source production]` | dev container | Which CDNs need a resolution's playback headers; `--source production` takes production's stored resolutions through Portainer. |
| `anime_offsets [--aniworld]` | dev container | How far an IMDb request's absolute episode number lies from the Kitsu number, against Cinemeta, the Anime Kitsu addon and Fribb's id list; `--aniworld` adds where `locate()` places each episode. |
| `sto_linkout` | both | Whether sto's hoster link-outs (`/r?t=<token>`) leave the site. |
| `vidhide_segments` | both | Which headers VidHide's CDN wants behind the HLS proxy; in the dev container with `--imdb` and the dev server's `PORT`. |
| `domains` | both | Plugin domains from where the probe runs: DNS, connect, `HEAD`, `GET`; the two places compared show what the VPN exit changes. |
| `capture_timing URL [--hoster H] [--runs N] [--diagnose] [--block DOMAIN] [--restore] [--autoplay-wait S]` | both | A browser-capture resolver's capture timed in a Chromium and profile of its own: page loads, clicks, challenges, the stream and the whole resolve. |

`capture_timing` details:

- It runs without the registry's bound and caches nothing: no clearance store, breaker or resolver cache, a temporary directory of its own, 90 s per run, its processes killed by parent PID at exit.
- It counts challenges by the app's rule: a captcha provider's frame such as `turnstile`, or a request the resolver names as its hoster's captcha, split into `captcha` and `captcha/verify`.
- `--restore` keeps the runs' captures' state as the app does (in a temporary `ClearanceStore`), prints how long each read took and the state's make-up (cookie names per domain with ids masked, key and database counts per origin, no values), and resolves the link twice more: in a fresh context (`control`) and in one created from the store (`restored`).
- `--diagnose` adds per run the page's requests (domain, the path with its ids masked, resource type, status or error), what each frame can play after the clicks and how many popups the page opened.
- `--block DOMAIN` (repeatable) aborts every request to a domain and its subdomains, such a request reading `blocked`.
- `--autoplay-wait S` sets the capture's wait before its first click for every resolver alike; the report names the wait the captures used.
- It first reads the memory, `/dev/shm` and whether the app's Chromium runs with `--disable-dev-shm-usage`, and launches nothing when they are short.

`hls_throughput` details:

- It takes the hoster's newest stored HLS link and its highest variant, and runs four times 10 segments: one connection, the read-ahead of 3 (the proxy's depth), 3 byte ranges, both at once.
- `--in-flight` replaces the read-ahead run with one run per count of segments in flight.
- It prints the master's variants, Mbit/s per run and its multiple of real time, the title's bitrate, the failed requests by status or error (a 429 ends the runs), whether the CDN honoured the ranges and the connections a stutter-free playback needs.
- `--imdb ID` asks the app for the title's streams first, which fills its caches like a Stremio request.

## Verify

- `prodctl.py ps` lists the containers and their state, and `prodctl.py check` ends with exit code 0 and every row passed.
- A probe prints its report; it reads only, so production's state is the same afterwards.

## Troubleshooting

- **A `logs --grep` that finds nothing:** the pattern runs over the raw JSON record, so `path=/metrics` matches nothing while `/metrics` does; verify a zero with a broader pattern before reporting it.
- **A command hangs before its first line:** the shared budget of 100 Portainer requests per minute is spent, and a full budget waits; other processes on the machine share it.
- **Portainer answers 429 or 5xx:** the command retries with backoff; an exec is retried only when Portainer refused it before it ran.
- **`capture_timing` launches nothing:** the container's memory or `/dev/shm` is short, as its first lines say.
- **A dev-container probe runs the main checkout's code:** in a worktree it needs `PYTHONPATH=src`.
