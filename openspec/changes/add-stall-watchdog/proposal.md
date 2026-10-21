## Why

Docker restarts a container only when its process exits.
`restart: unless-stopped` acts on an exit, and a failing healthcheck only marks the container `unhealthy`: Docker restarts nothing on it and leaves that to an orchestrator (moby issue 28400).
Production runs plain Docker Compose, so a Scavengarr whose process lives but hangs stays hung until the maintainer restarts it.

What Scavengarr has heals failures of single requests and sites, not of the process:
- Time bounds: a Stremio stream answer by its deadline (60 s by default), plugins cut at their timeout, the HTTP client's timeouts, the resolution bound.
- The shared browser relaunches after a disconnect; circuit breakers route around failing sites and hosters; `PageBudget` cuts browser pages when the host runs short.
- `/api/v1/healthz` answers 200 while the loop runs; `/api/v1/readyz` answers 503 only before the start, at shutdown and after 5 min without cache and DNS.

What nobody notices today (read from the code, Mr. Fable's research of 2026-10-20):
- **A blocked or deadlocked event loop.** `monitor_loop_lag` runs on the loop itself, so it logs a stall only after the stall ends; a stall that never ends leaves no log line, and the healthcheck's `unhealthy` restarts nothing.
- **A long-lived background task that ends with an exception.** `monitor_loop_lag` has no guard, and the hoster state and plugin history writers guard only their write; such a task ends without a log line until shutdown.
- **A request that hangs while the loop runs** (a lock never released, a slot never returned): both health routes answer 200.

No such hang is on record in production: the hangs found so far were single resolutions and a check script, and time bounds fixed them.
So the change is insurance that gathers evidence first.
It names the cause of a hang in the log, and it can end the process on a stalled loop so the restart policy restarts it: a switch that stays off until readings show how long the loop's real stalls are.

The maintainer's choice of 2026-10-20: diagnosis with an exit switch that is off, over a diagnosis without an exit, an exit from the first day, an autoheal container and an orchestrator.

## What Changes

- **A stall monitor outside the event loop.**
  - `monitor_loop_lag`'s tick (every 0.5 s) also stamps a heartbeat; a daemon thread reads it every 5 s.
  - When the heartbeat is older than `watchdog.stall_seconds` (default 120, 0 turns the monitor off), it logs `event_loop_stalled` once per stall with the stall's age and the main thread's stack, and dumps every thread's traceback to standard error (`faulthandler`).
  - When the loop runs again, `event_loop_recovered` names the stall's length.
- **A backstop for a held interpreter.** Code that holds the GIL starves the monitor thread too; a `faulthandler.dump_traceback_later` timer at twice the limit, re-armed at every check of the monitor thread, dumps the tracebacks from C.
- **The exit switch, off.**
  - With `watchdog.exit_on_stall: true` the monitor ends the process after the log line and the dump (`os._exit(3)`), so `restart: unless-stopped` restarts the container.
  - The exit never waits on a lock or a write the stalled thread may hold: the structured line and the log's flush get bounded waits, the dump and the exit do not depend on them.
  - A restart budget: after 3 exits within 30 min, kept in a file of the cache directory (a container restart keeps its file system), a further stall is only logged.
- **Background tasks log their death.** Every long-lived task the service starts gets a done-callback that logs `background_task_died` with the task's name and the exception when it ends with one (a cancellation is no death), counted in `scavengarr_background_task_deaths_total{task}`.
- **Stuck requests are logged.**
  - The request middleware notes each request's task and start.
  - A check on the loop every 30 s logs `request_stuck` once for a Stremio request older than 5 min or a Torznab request older than 10 min, with its route class, its age and its task's stack, counted in `scavengarr_requests_stuck_total{route}`.
  - Play and proxy requests run long by design and are not watched.
- **The loop lag histogram reaches the stall range.** `scavengarr_event_loop_lag_seconds` gets buckets up to 120 s, so a stall the loop survived stays in the scraped metrics after the container's logs are gone with a deploy.

## Impact

- **Affected specs:** new `service-watchdog`.
- **Affected code:**
  - a new module `watchdog.py` in `src/scavengarr/infrastructure/telemetry/` (the monitor thread, the stall decision, the exit and its budget);
  - `src/scavengarr/infrastructure/telemetry/loop_lag.py` (the heartbeat) and `src/scavengarr/infrastructure/telemetry/metrics.py` (the histogram's buckets, two counters);
  - `src/scavengarr/domain/ports/telemetry.py` (two recordings with fixed label sets);
  - `src/scavengarr/infrastructure/graceful_shutdown.py` (it already counts the requests in flight; it notes their task and start) and `src/scavengarr/interfaces/app.py` (the middleware);
  - `src/scavengarr/interfaces/composition.py` (the done-callbacks on the lifespan's tasks; the monitor starts after `mark_ready()` and stops first at shutdown);
  - `src/scavengarr/infrastructure/hoster_resolvers/state_store.py` and `src/scavengarr/infrastructure/plugins/history.py` (their own tasks get the callback);
  - `src/scavengarr/infrastructure/config/schema.py` (the section `watchdog`);
  - their tests.
- **Affected docs:** `docs/current/observability.md`, `docs/current/architecture.md` (the lifespan), `docs/how-to/operations.md` (reading a stall), `docs/reference/configuration.md` and `docs/reference/metrics.md` (generated), a decision record in `docs/target/decisions/`, `CHANGELOG.md`.
- **Unchanged:**
  - The health routes and the image's healthcheck.
  - The Compose file: the restart policy is already `unless-stopped`.
  - No new dependency: `threading`, `faulthandler` and `sys._current_frames()` are stdlib.
- **Order:** the task deaths first (group 1, small), then the monitor and the stuck requests (groups 2 and 3); the switch stays off until the readings of group 4 and the maintainer's decision.
- **Approval:** the maintainer approved the whole change on 2026-10-21; group 1 is row 156 of `docs/backlog.md`, groups 2, 3 and 4.1 are row 157.
