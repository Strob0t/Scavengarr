# Measure performance

> **Purpose:** Measuring stream requests, a playback under load and the title set before and after a change.
> **Status:** how-to
> **As of:** de34a922

## Goal

Measure a running instance the way its users load it: the stream requests' wall time, CPU and outbound requests, the first and the cached answer per title with playable streams, and a playback through the HLS proxy while the server searches.
Compare the numbers before and after a change.

## Prerequisites

- A running instance: the dev server (`poetry run start --port 7979`, with `PYTHONPATH=src` in a worktree) or production behind its reverse proxy.
- For the app's CPU: `/metrics` of the instance (`--cpu`), which counts the app's Python process and not Chromium's processes; for the CPU of every process in the container, the docker CLI on the Docker host (`--docker`).
- For the title set of step 3: the local, untracked ids file `.cache/live/round-titles.txt`, one `movie/<imdb id>` or `series/<imdb id>` per line with an optional `# title`, so the maintainer's titles stay out of the repository.
- For outbound request counts: the JSON log format (the production default) and `logging.level: DEBUG`, since httpx writes its `HTTP Request:` lines at that level only.
- For a CPU profile: `cap_add: [SYS_PTRACE]` on the container.

The scripts print no URLs; `--insecure` skips the TLS check for a local CA.

## Steps

### 1. Profile stream requests

`scripts/stremio_profile.py` measures the ids given, or its baseline set of three titles, against a running container.
Per request it reports the wall time, the CPU of the Python and Chromium processes (read from `/proc` before and after) and the httpx requests per host from the container log.
At the end it reports the event-loop lag from `/api/v1/stats/metrics`.
It runs on the Docker host: `--docker` runs its commands in the container through the docker CLI, which Komodo's read requests cannot.

```bash
poetry run python scripts/stremio_profile.py --docker \
  --base https://scavengarr.example.lan --insecure \
  --connect-to scavengarr.example.lan:<production host address> --repeat 2 --py-spy
```

- `--repeat 2` runs a cold and a warm pass over the ids.
- `--py-spy` samples the app process during all requests (100 Hz, threads holding the GIL only) and prints the CPU share per category; py-spy is installed into the container's `/tmp` and runs as root.
- `--connect-to HOST:IP` resolves the host to that address, as curl's option does, for a name the machine's DNS does not know.

A local server is profiled with `py-spy record -r 100 -f raw -- python -m scavengarr.interfaces.cli …`.

### 2. Time a playback under search load

`scripts/playback_under_load.py` plays a stored HLS stream through `/api/v1/stremio/proxy/<id>/scavengarr.m3u8` at the playback pace while the server searches.

```bash
poetry run python scripts/playback_under_load.py --base https://scavengarr.example.lan --insecure \
  --stream-id <id from the links probe> --segments 20 --cpu --label idle
poetry run python scripts/playback_under_load.py --base https://scavengarr.example.lan --insecure \
  --stream-id <id> --segments 20 --cpu --label search --search movie/<Movie B>
```

- The player takes the variant with the highest bandwidth and fetches `--segments` media segments one after another, the next when the previous segment's duration (`EXTINF`) has passed since its fetch began.
- Per segment it times the response headers and the last byte; a segment slower than its own duration is a stall.
- The `--search` ids are requested one after another from `--search-at` seconds into the playback, which goes on until `--tail` seconds after the last answer; each answer's wall time, streams and `X-Cache` and `X-Search-Complete` headers are reported.
- Without `--search` the run is the idle reference.
- `/metrics` before and after the run gives the server's view: the proxy's segment stage, the read-ahead's fetches and outcomes, the plugin searches' durations and the event-loop lag; the quantiles are histogram bucket bounds.
- `--cpu` samples the app process's CPU from `/metrics` about every 5 s (`process_cpu_seconds_total`: the Python process, not Chromium's), and `--out FILE` appends the two Markdown rows (the player's view, the server's view).

It runs from a machine on the server's network; the dev container reaches production through its reverse proxy.

### 3. Run the title set

`scripts/stremio_round.py` requests each id of the ids file, one after another: the first answer, the cached answer right after it, then the play check of the first answer's streams.

```bash
poetry run python scripts/stremio_round.py --base https://scavengarr.example.lan --insecure \
  --out <notes file> --cpu
```

- Per title it prints the wall time, the streams and the `X-Cache` and `X-Search-Complete` headers of both answers.
- Under each title it names the failing streams as `hoster: verdict` and counts the Range requests answered with 200 instead of 206.
- It ends with a summary row of medians and totals and a table of the streams per hoster and codec pair.
- `--ids-file` names another ids file, `--no-playcheck` skips the play check, and `--cpu` adds the CPU seconds the app's Python process used during the run (from `/metrics` before and after; Chromium's processes are not in it).

### 4. Check every stream like a player

`scripts/stremio_playcheck.py` fetches every stream of the ids given, or of `stremio_measure.py`'s title set, with its `proxyHeaders`, from the machine it runs on.

```bash
poetry run python scripts/stremio_playcheck.py --base https://scavengarr.example.lan --insecure movie/tt15398776
```

- For HLS it fetches the master playlist, the first variant and its first two segments; for a file the first KiB and one in the middle (a seek).
- It prints a verdict per stream with its codecs as `video/audio`, and one per source (`HOSTER · plugin`).
- A stream bound to the resolving address fails from another machine.

### 5. Measure the latency per title group

`scripts/stremio_measure.py` requests each title's streams like Stremio for its built-in title groups (`german-movies`, `popular-movies`, `series`, `anime`) and prints one line per title and a summary.

```bash
poetry run python scripts/stremio_measure.py --base http://127.0.0.1:7979 --groups series anime --pause 120
```

The groups run one after another with a pause in between (default 120 s), since home routers block bursts of new connections.

## Verify

- Two runs are comparable only with the same titles and the same pass (cold or warm).
- Playback runs are comparable only for the same stream, since its segment length sets the pace, and only while nothing else plays or searches.
- The numbers before and after a change go into the plan of the change, with the commit each ran on.

## Troubleshooting

- **The outbound request counts are empty:** the instance logs at `INFO`; httpx writes its request lines at `DEBUG` only.
- **`--py-spy` fails:** the container lacks `cap_add: [SYS_PTRACE]`.
- **`stremio_round.py` stops at once:** the ids file is missing or empty; it is local and untracked.
- **Production slows down during the title set:** each run asks every plugin's site for every title, so it runs against production at most once an hour.

## References

- `scripts/stremio_profile.py`, `scripts/playback_under_load.py`, `scripts/stremio_round.py`, `scripts/stremio_playcheck.py`, `scripts/stremio_measure.py`.
- [Operations](operations.md): the links probe for a stream id, a container probe that the maintainer runs on the Docker host.
