## 1. Background tasks log their death

- [x] 1.1 Tests first:
  - a task that raises logs `background_task_died` with its name and the exception, once, and counts it;
  - a cancelled task and a task that returns log and count nothing;
  - every long-lived task of the lifespan and the two writers' own tasks carry the callback.
- [x] 1.2 One done-callback helper with the task names as a `Literal` set (design D8), attached in `composition.py` to `loop_lag`, `page_budget`, `plugin_health`, `scoring`, `browser_warmup` and `pass_keeper`, and in `state_store.py` and `history.py` to their own tasks.
- [x] 1.3 `TelemetryPort.record_background_task_death(task)`, `NO_TELEMETRY` and `scavengarr_background_task_deaths_total{task}`; `docs/reference/metrics.md` regenerated.
- [x] 1.4 Docs: `docs/current/observability.md` (the line and the counter), `CHANGELOG.md`.

## 2. The stall monitor

- [x] 2.1 Tests first, the decision as a pure function with a fake clock:
  - no stall below the limit;
  - one `event_loop_stalled` per stall, with its age;
  - `event_loop_recovered` with the stall's length, and the next stall logged again;
  - a limit of 0 starts no thread.
- [x] 2.2 The heartbeat in `monitor_loop_lag` and the daemon thread (design D1, D2, D9); the lag histogram's buckets up to 120 s.
- [x] 2.3 A subprocess test: a loop blocked by `time.sleep` beyond a short limit logs `event_loop_stalled` with the main thread's stack and dumps every thread's traceback to standard error; with the switch off the process lives on and logs `event_loop_recovered`.
- [x] 2.4 The backstop: the `faulthandler` timer is armed at twice the limit and re-armed at every check of the monitor thread, with `exit` following the switch and the budget; a failed check and the thread's end cancel it; subprocess tests prove its dump where a test can hold the interpreter, and one dump only where the monitor thread runs.
- [x] 2.5 The exit switch (design D4, D5):
  - with `exit_on_stall` on, a stall ends the process with code 3 after the dump (subprocess test);
  - the structured line and the flush wait a bounded time, the exit follows regardless;
  - the fourth stall within 30 min only logs, with its exit skipped;
  - a corrupt budget file counts as empty.
- [x] 2.6 The settings `watchdog.stall_seconds` and `watchdog.exit_on_stall` with their descriptions; `docs/reference/configuration.md` regenerated.

## 3. Stuck requests

- [x] 3.1 Tests first:
  - a request older than its route class's limit is logged once as `request_stuck` with its route class, its age and its task's stack, and counted;
  - play and proxy requests are never logged;
  - a request that ends is forgotten.
- [x] 3.2 The middleware notes each request's task and start beside `GracefulShutdown`'s in-flight count; the check runs on the loop every 30 s and stops at shutdown.
- [x] 3.3 `TelemetryPort.record_request_stuck(route)`, `NO_TELEMETRY` and `scavengarr_requests_stuck_total{route}`; `docs/reference/metrics.md` regenerated.

## 4. Docs and the readings

- [x] 4.1 Docs:
  - `docs/current/observability.md` (the monitor, the switch, the lines);
  - `docs/current/architecture.md` (the lifespan);
  - `docs/how-to/operations.md` (reading a stall from `prodctl logs`, the dump on standard error);
  - a decision record in `docs/target/decisions/` (the in-process monitor with its switch off, over an autoheal container and an orchestrator);
  - `CHANGELOG.md`.
- [ ] 4.2 Production after the deploy (Mr. Fable): the start line names the monitor and its settings. Over two weeks, after each deploy, the stalls, the task deaths, the stuck requests and the lag histogram's top buckets are read and recorded here.
  - The deploy of 5321c32f (version 0.5.1): `stall_monitor_started` follows `app_startup_complete` with `stall_seconds=120.0`, `exit_on_stall=False` and `backstop_seconds=240.0`; the post-deploy check passed 10 of 10; the nine series of `scavengarr_background_task_deaths_total` (with `request_watch`) and the four of `scavengarr_requests_stuck_total` read 0; the lag histogram's 1693 samples of the first minutes all lie at or below 2.5 s, its new buckets up to 120 s empty.
  - The second reading: 5321c32f's containers in the log archive (about 7 h of their log over four starts) and eac28a35's first 4.3 h. No lag warning, no task death, no traceback and no error line; the day's container replacements came without a stall or an error before them (recreates, a rebuild, a restart with a clean shutdown). One `request_stuck`, the first: a Torznab search 625 s old, its stack in the link validation's batch (`validate_batch`'s gather); it answered after 632.7 s, its 130 links having drawn 87 HTTP 429 answers whose `Retry-After` the retry transport waited out (backlog row 162). eac28a35's counters read 0 (the nine task deaths, the four stuck routes), and its lag histogram's 30824 samples all lie at or below 10 ms.
  - The third reading, at the deploy of 69785026: eac28a35 read shortly before its replacement, after 5.6 h: 40135 lag samples, 40129 at or below 10 ms, 6 between 10 and 50 ms, none above; its counters read 0. 69785026's first 15.9 h: `stall_monitor_started` with the same settings follows `app_startup_complete` (`commit=6978502687e0`), and the post-deploy check passed 10 of 10; the nine task deaths and the four stuck routes read 0; of the lag histogram's 114416 samples 114408 lie at or below 10 ms, 6 between 10 and 50 ms and 2 between 50 and 100 ms, none above. No lag warning, no `request_stuck` and no line at error level.
  - The fourth and final reading, at the deploy of efa212e6 (version 0.5.1; the image carries the dependency bump on top of the same watchdog code): its first 17.0 h, container healthy with no restart. `stall_monitor_started` follows `app_startup_complete` with the same settings (`stall_seconds=120.0`, `exit_on_stall=False`, `backstop_seconds=240.0`). The nine task deaths and the four stuck routes read 0. Of the lag histogram's 122450 samples 122445 lie at or below 10 ms (122418 at or below 5 ms), 5 between 10 and 50 ms, none between 50 and 100 ms and none above; the mean lag is 0.32 ms (sum 38.94 s). The container's whole log (4586 lines, read in full) holds no `event_loop_stalled`, `event_loop_recovered`, `request_stuck` or `background_task_died` line and none at error level; its 194 warning-level lines are the sites' and hosters' own (resolve failures and timeouts, HTTP errors of single hosters, plugin timeouts, four opened circuit breakers), none from the watchdog.
  - Over the four readings: the final readings of the last three containers (40135, 114416 and 122450 lag samples, 277001 together) hold 19 above 10 ms, 2 of them above 50 ms and none above 100 ms, far under the 120 s limit (the first container's 1693 early samples were read only against the coarser top buckets, all at or below 2.5 s). The task-death and stuck counters read 0 at every reading; the one `request_stuck` (second reading) was a slow link validation behind HTTP 429 answers (backlog row 162), not a stalled loop. No stall, no recovery, no backstop dump and no exit has occurred in production.
  - Limits: counters and histogram are read since each container's start, so the windows run from 5.6 h to 17.0 h and the readings are separate containers, not one continuous series; the lag histogram samples every 0.5 s, so it shows the loop's lateness at those instants, and the stall monitor's own thread is what would catch a block between samples (it logged nothing); the load in each window is not measured (searches and plays varied); the exit switch and the backstop were proven only by the subprocess tests (2.3 to 2.5), never by a real stall in production, so these readings say the stall limit of 120 s was never approached, not how the switch behaves under a stall there.
- [ ] 4.3 The maintainer decides on the switch and its limit with those numbers; turning it on is a setting in production's Compose.
