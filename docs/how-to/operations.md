# Operate production

> **Purpose:** Reading production with `scripts/prodctl.py`: the container, its logs, metrics, the digest, the post-deploy check and the probes.
> **Status:** how-to
> **As of:** 4a898049

## Goal

Read the production container from a development machine without changing it: its state, logs, metrics and state endpoint, a report of a window and the check after a deploy, and run probes against the sites or, by the maintainer's hand, inside the container.

## Prerequisites

- In `.env.devcontainer` (`scripts/komodo.py` reads them; the file wins over the environment):
  - `KOMODO_URL`: Komodo Core's address;
  - `KOMODO_API_KEY` and `KOMODO_API_SECRET`: a Komodo key that may read the `scavengarr` container;
  - `KOMODO_SERVER`: the production host's name in Komodo;
  - `PRODCTL_BASE_URL`: production's public base URL, without a path.
- For the production host's local CA: `--insecure` before the command (`prodctl.py --insecure check`) skips the TLS checks of Komodo and of the public URL.

`scripts/prodctl.py` reads the container `scavengarr` with two of Komodo Core's read requests and nothing else: `POST {KOMODO_URL}/read` with `InspectContainer` (the container's state and limits) or `GetContainerLog` (its log).
It uses no other path of Komodo's API (`/execute`, `/write`, `/terminal`), so it runs no command in the container and changes nothing, although the key reaches every container of the host.
Docker's inspect object holds the container's environment with its secrets: `komodo.py` keeps the status, health, start, restarts, OOM kill and the CPU and memory limits, and prints, logs and stores nothing else of it.
The app's own endpoints (`/metrics`, `/api/v1/stats/metrics`, `/api/v1/stats/plugins` and the stream requests of `check`) come through `PRODCTL_BASE_URL`.

Everything it prints passes `komodo.mask()`: URLs keep only scheme and host, IP addresses and secret-like values are replaced.
Its errors too: a refused request ends with one line on stderr and exit code 1 instead of a traceback, such as `logs failed: GetContainerLog answered 403` with Komodo's reason when its answer names one, and an error that quotes a URL names neither Komodo's host nor production's (`https://<host>/…`).
A budget shared by all processes on the machine allows 100 Komodo requests per minute.
A request that Komodo answers with 429, 502, 503 or 504, or that finds it unreachable, is sent again with backoff, three attempts in all: every request is a read.
Rebuilds and restarts stay with the maintainer.

### The log's window

`GetContainerLog` answers the log's last 5000 lines, Komodo's cap (a larger request gets 5000 without an error), with stdout and stderr apart; `komodo.py` merges them by their times, as `docker logs` interleaves them.
At production's usual rate the 5000 lines cover about 4 h.
`logs` and `digest` filter them by `--since`; when the log begins after the requested start, `logs` ends with the time it reaches back to (`[the log reaches back to … UTC only: Komodo answers its last 5000 lines]`), and the digest's first line names the shorter window.
A record older than the window is out of reach, such as the start's `config_effective` of a container that has run for hours.

## Steps

### 1. Look at the container

```bash
poetry run python scripts/prodctl.py ps        # status, health, start, restarts, OOM kill
poetry run python scripts/prodctl.py stats     # the app process's CPU, memory, open files
```

- `ps` is one `InspectContainer` request: `scavengarr | running (healthy) | started … UTC | restarts 0 | OOM-killed no`.
- `stats` is one `InspectContainer` request for the limits and two `GET /metrics` 5 s apart: `process_cpu_seconds_total` as the cores in use between the two, `process_resident_memory_bytes` and `process_open_fds`, against the container's CPU and memory limits.
- The process counters are the app's Python process: Chromium's processes are not in them, as the last line says.

### 2. Read the logs

```bash
poetry run python scripts/prodctl.py logs --since 30m --grep kinoger --fields plugin,duration_ms
poetry run python scripts/prodctl.py logs --since 4h --grep config_ --width 0   # the config it runs with
poetry run python scripts/prodctl.py logs --since 1h --grep hoster_state_       # what a restart restored
```

- `logs` is one `GetContainerLog` request.
- It renders JSON records as `time level event key=value ...` and drops the liveness and readiness checks; `--health` keeps them, and `--raw` prints the masked lines as logged.
- `--since` defaults to 15 minutes and `--limit` to the last 60 lines.
- Records are cut to 400 characters; `--width 0` prints them whole, for a traceback's last lines (`--fields exception --width 0`).
- `--grep` is a case-insensitive regex over the raw JSON record.
- The configuration lines come at the start: they are in the log while its 5000 lines reach back to the start ([Configuration](../reference/configuration.md)).

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

A stall of the event loop ([Stalls](../current/observability.md#stalls)):

```bash
poetry run python scripts/prodctl.py logs --since 4h --grep 'event_loop_(stalled|recovered)|stall_monitor' --fields age_s,exit,length_s
poetry run python scripts/prodctl.py logs --since 4h --grep event_loop_stalled --fields stack --width 0
poetry run python scripts/prodctl.py logs --since 4h --grep 'most recent call first|File "|Timeout \(' --limit 0 --width 0
```

- `event_loop_stalled` names the stall's age and its `exit` (`on`, `skipped` or `off`), `event_loop_recovered` its length; a stall the process exited on has no recovery line.
- `stack` holds the innermost part of the loop thread's stack: its last `File` line is the call that held the loop.
- The dump is plain text on stderr, which the log merges by time: a `Thread 0x… (most recent call first):` line per thread with its frames below, innermost first, and `Current thread` for the monitor's own.
- A dump headed `Timeout (0:04:00)!` comes from the `faulthandler` timer: no Python thread ran for twice the limit.
- After an exit `ps` counts one restart more, and the log keeps the run before it, since a restart keeps the container's log.

A request stuck while the loop runs ([Stuck requests](../current/observability.md#stuck-requests)):

```bash
poetry run python scripts/prodctl.py logs --since 4h --grep request_stuck --fields route,age_s,request_id,stack --width 0
```

- `request_id` finds the request's other lines (`--grep` with the id), its `http_request` line among them once the request ends.
- The last line of `stack` is where the request waits.

### 3. Read the metrics and the state

```bash
poetry run python scripts/prodctl.py metrics --grep plugin_search_seconds_count
poetry run python scripts/prodctl.py state --keys circuit_breaker,event_loop
```

`metrics` and `state` are one `GET /metrics` and one `GET /api/v1/stats/metrics` through `PRODCTL_BASE_URL` ([Metrics](../reference/metrics.md)).

### 4. Read a window in one report

```bash
poetry run python scripts/prodctl.py digest --since 24h       # --json: its data
```

`digest` costs one `GetContainerLog` request, `GET /metrics` and `GET /api/v1/stats/plugins`; the computation lives in `scripts/digest.py`.
The window defaults to 24 h, more than the log's last 5000 lines usually reach: the first line then names the window the log covers (`# scavengarr: the last 24.0 h (the log reaches back 3.9 h only)`).
The metrics count since the start, so a restart inside the window shows as a short uptime in the second line.
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
poetry run python scripts/prodctl.py --insecure check
```

`check` is the post-deploy check as one command, judged in `scripts/deploy_check.py` (tested from fixture records and answers, no network) and printed as one table; it exits 1 when a check failed.
It costs two Komodo requests (`InspectContainer` for the state and the start, `GetContainerLog` after the stream requests) and, through the public URL, four stream requests and `GET /metrics`.
`--base` names another addon URL up to `/api/v1/stremio`; the default is `PRODCTL_BASE_URL` with `/api/v1/stremio`.

1. The container is running and healthy; its restarts and an OOM kill are shown.
2. The newest `app_startup_complete` record is younger than the container's start and names the version.
3. `config_unknown_keys` of that start is shown as a warning, not a failure.
4. `hoster_state_restored` and `plugin_history_restored` of that start are shown with their counts.
5. Three anime ids (`series/<Anime A>:5:2`, `series/kitsu:<Anime A>:1089`, `series/<Anime H>:2:1`) answer streams whose description carries `S05E02`, `S22E04` and `S02E01`; one stream of another episode fails the check.
6. The log holds each request's `http_request` record (`X-Request-ID`), and the placement record `stremio_episode_ref` is shown when the log level has it (it is DEBUG, so production prints `no placement record (DEBUG)`).
7. `movie/<Movie F>` answers at least one stream within `--deadline` (default 60 s, `stremio.stream_deadline_seconds`); `--timeout` bounds each stream request (default 120 s).
   Each stream row ends with the client's time to the response headers and to the body's end beside the server's `duration_ms` of that `http_request` record (`client 4.1 s to headers, 4.2 s to the end, server 3.9 s`); a client more than 5 s slower than the server turns a passed row into a warning that names the gap (`27.0 s more on the way`): the time went into the proxy or the network, not the search.
8. The readiness probe's gauge `scavengarr_network_ok` reads 1 for the cache backend and the DNS ([Readiness](../current/observability.md#readiness)); a check at 0 fails, and no sample yet (nothing asked `/api/v1/readyz` since the start) is a warning.

Checks 2 to 4 read the log from the container's start on: a restart keeps the container's log, so the lines before the start do not count.
When Komodo's 5000 lines begin after the start, the start's records are gone, and checks 2 to 4 are warnings that the log no longer reaches the start, not failures; a check right after the deploy reads them.
The stream requests are its only writes to production (four searches).
The stream descriptions are the only site data in the table, and the output passes `komodo.mask()`.
A container replaced during the check needs the check again.

### 6. Run a probe

The probes in `scripts/probes/` change nothing.
The dev-container ones run there; in a worktree with `PYTHONPATH=src`, so they import the worktree's code:

```bash
PYTHONPATH=src xvfb-run -a poetry run python scripts/probes/series_episodes.py
```

A container probe reads the app's code, config and cache, so it runs inside the container.
Komodo's read requests run no command there: the maintainer runs it on the Docker host, with the probe's file on standard input (`-`) and its arguments behind it, `-e KEY=VALUE` for a variable:

```bash
docker exec -i scavengarr python - [ARGS] < scripts/probes/NAME.py
```

That is also how a shorter `autoplay_wait` is timed on the production host first (`capture_timing --autoplay-wait`).

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
| `header_binding` | dev container | Which CDNs need a resolution's playback headers, for the live suite's hoster links resolved there. |
| `anime_offsets [--aniworld]` | dev container | How far an IMDb request's absolute episode number lies from the Kitsu number, against Cinemeta, the Anime Kitsu addon and Fribb's id list; `--aniworld` adds where `locate()` places each episode. |
| `sto_linkout` | both | Whether sto's hoster link-outs (`/r?t=<token>`) leave the site. |
| `vidhide_segments` | both | Which headers VidHide's CDN wants behind the HLS proxy; in the dev container with `--imdb` and the dev server's `PORT`. |
| `domains` | both | Plugin domains from where the probe runs: DNS, connect, `HEAD`, `GET`; the two places compared show what the VPN exit changes. |
| `capture_timing URL [--hoster H] [--runs N] [--diagnose] [--block DOMAIN] [--restore] [--autoplay-wait S]` | both | A browser-capture resolver's capture timed in a Chromium and profile of its own: page loads, clicks, challenges, the stream and the whole resolve. |

`capture_timing` details:

- It runs without the registry's bound and caches nothing: no clearance store, breaker or resolver cache, a temporary directory of its own, 90 s per run, its processes killed by parent PID at exit.
- It counts challenges by the app's rule: a captcha provider's frame such as `turnstile`, or a request the resolver names as its hoster's captcha, split into `captcha` and `captcha/verify`.
- `--restore` keeps the runs' captures' state as the app does (in a temporary `ClearanceStore`), prints how long each read took and the state's make-up (cookie names per domain with ids masked, key and database counts per origin, no values), and resolves the link twice more: in a fresh context (`control`) and in one created from the store (`restored`).
- `--diagnose` adds per run the page's requests (domain, the path with its ids masked, resource type, status or error, when the request went out and how long its answer took, all in seconds after the run's start), what each frame can play after the clicks and how many popups the page opened.
- `--block DOMAIN` (repeatable) aborts every request to a domain and its subdomains, such a request reading `blocked`.
- `--autoplay-wait S` sets the capture's wait before its first click for every resolver alike; the report names the wait the captures used.
- It first reads the memory, `/dev/shm` and whether the app's Chromium runs with `--disable-dev-shm-usage`, and launches nothing when they are short.

`hls_throughput` details:

- It takes the hoster's newest stored HLS link and its highest variant, and runs four times 10 segments: one connection, the read-ahead of 3 (the proxy's depth), 3 byte ranges, both at once.
- `--in-flight` replaces the read-ahead run with one run per count of segments in flight.
- It prints the master's variants, Mbit/s per run and its multiple of real time, the title's bitrate, the failed requests by status or error (a 429 ends the runs), whether the CDN honoured the ranges and the connections a stutter-free playback needs.
- `--imdb ID` asks the app for the title's streams first, which fills its caches like a Stremio request.

## Verify

- `prodctl.py ps` prints the container's line, and `prodctl.py check` ends with exit code 0, every row passed or warned.
- A probe prints its report; it reads only, so production's state is the same afterwards.

## Troubleshooting

- **A `logs --grep` that finds nothing:** the pattern runs over the raw JSON record, so `path=/metrics` matches nothing while `/metrics` does; verify a zero with a broader pattern before reporting it. A record older than the log's last 5000 lines is gone: the last line names how far back the log reaches.
- **`missing in .env.devcontainer and the environment: …`:** the line names the variables to set.
- **`… failed: InspectContainer answered 403` (or 500):** the key may not read the container, or `KOMODO_SERVER` names no server of Komodo's; Komodo's reason follows when its answer names one.
- **A TLS error such as `CERTIFICATE_VERIFY_FAILED`:** the production host's local CA; `--insecure` goes before the command.
- **A command hangs before its first line:** the shared budget of 100 Komodo requests per minute is spent, and a full budget waits; other processes on the machine share it.
- **Komodo answers 429 or 5xx:** the command sends the request again with backoff, three attempts in all.
- **`check` warns that the log no longer reaches the start:** the container has logged more than 5000 lines since; the check right after a deploy reads the start's records.
- **`capture_timing` launches nothing:** the container's memory or `/dev/shm` is short, as its first lines say.
- **A dev-container probe runs the main checkout's code:** in a worktree it needs `PYTHONPATH=src`.
