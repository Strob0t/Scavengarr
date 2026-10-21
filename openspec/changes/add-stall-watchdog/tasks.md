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
- [ ] 4.3 The maintainer decides on the switch and its limit with those numbers; turning it on is a setting in production's Compose.
