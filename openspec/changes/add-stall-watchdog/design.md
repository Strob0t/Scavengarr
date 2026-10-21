## Context

Production runs Scavengarr in Docker Compose on one host: `restart: unless-stopped`, `init: true` (tini as PID 1), and a healthcheck that asks `/api/v1/readyz` every 30 s.
What Docker does with it (its documentation, read 2026-10-20):
- A restart policy acts when the container's process exits; the delay doubles from 100 ms up to 1 min and resets after 10 s up (https://docs.docker.com/engine/containers/start-containers-automatically/).
- A failing healthcheck marks the container `unhealthy` after its retries; nothing else follows (https://docs.docker.com/reference/dockerfile/#healthcheck).
  The request to restart on `unhealthy` is open since 2016 and left to an orchestrator (https://github.com/moby/moby/issues/28400).
- Swarm replaces a task whose health check fails, and Kubernetes restarts a container whose liveness probe fails (https://kubernetes.io/docs/concepts/configuration/liveness-readiness-startup-probes/).

What the code answers in each failure (Mr. Fable's research, read from the code; "a restart heals" is inference):

| Failure | Heals today | `healthz` / `readyz` | A restart heals |
|---|---|---|---|
| The loop blocked or deadlocked | no | both time out; the container turns `unhealthy`, nothing restarts it | yes, unless a retried request blocks it again |
| The loop runs, requests hang | partly, through the time bounds | 200 / 200 | yes |
| A background task died | no | 200 / 200 | yes, and hides the cause |
| Chromium hangs without crashing | no; each capture times out, the breakers open | 200 / 200 | yes |
| The process exits | yes: tini exits, the restart policy restarts | refused | yes |

## Goals / Non-Goals

- **Goals:**
  - A hang names its cause in the log: the stalled thread's stack, the dead task, the stuck request.
  - A stalled loop is detected from outside the loop.
  - A stalled loop can end the process, so the restart policy restarts it, once the maintainer switches it on.
  - The loop's cost stays at one timestamp per tick.
- **Non-Goals:**
  - No restart by default.
  - No autoheal container: it needs the Docker socket, sees only what `readyz` sees, restarts in a loop during a network loss that a restart does not mend, and logs no cause.
  - No orchestrator: k3s beside Chromium on the production host is a new deployment for one probe, and Swarm stacks ignore `network_mode`, which the VPN setup needs.
  - No healing of single components (a browser recycle, a new HTTP client): each guards one failure guessed in advance, and this change is about the ones not guessed.
  - The health routes stay as they are: a deeper liveness check heals nothing on plain Docker.

## Decisions

- **D1, the heartbeat is the loop lag tick.** `monitor_loop_lag` already runs every 0.5 s on the loop; it stamps a monotonic time that the monitor thread reads. No second timer on the loop.
- **D2, a daemon thread, stdlib only.** It wakes every 5 s, compares the stamp with `watchdog.stall_seconds` and keeps the decision in a pure function (the stamp, the clock, the limit, the stall in progress), so tests drive it with a fake clock.
- **D3, the dump goes to standard error.** `faulthandler.dump_traceback(all_threads=True)` writes from C without the logging path; `docker logs` keeps standard error across a restart. The structured `event_loop_stalled` line carries the main thread's stack from `sys._current_frames()`, size-bounded.
- **D4, the exit is code 3 through tini.** `os._exit(3)` skips the graceful shutdown, which needs the stalled loop; tini exits with the child's code and the restart policy restarts the container. The structured line runs in a helper thread joined with a timeout, the log's flush likewise; then the exit follows regardless.
- **D5, the budget lives in a file.** A restart keeps the container's file system, so a small JSON file in `cache.dir` with the last exits' times survives it; a recreate starts it empty, and a corrupt file counts as empty. After 3 exits within 30 min a stall is logged with its exit skipped.
- **D6, the switch is off.** `watchdog.exit_on_stall` defaults to false; turning it on is one setting in production's Compose, with the limit chosen from the readings of group 4.
- **D7, stuck requests are checked on the loop.** In that failure the loop still runs, and `Task.get_stack()` is read on the loop's own thread. The middleware notes each request's task and start beside the in-flight count of `GracefulShutdown`.
- **D8, fixed label sets.** The task names (`loop_lag`, `page_budget`, `plugin_health`, `scoring`, `browser_warmup`, `pass_keeper`, `hoster_state_store`, `plugin_history`) and the route classes (`stremio_stream`, `stremio_catalog`, `torznab`, `other`) are `Literal` sets; no path, title or id goes into a label.
- **D9, the monitor's lifetime.** It starts after `mark_ready()`, so plugin loading and the browser's start are no stall, and it stops first at shutdown, so the bounded shutdown is none either.

## Risks / Trade-offs

- **A false stall under a saturated host.** Under a saturated CPU the loop still ticks, only late: the largest lag read so far is 0.6 s. A stall of 120 s means no tick at all. The switch stays off until the readings show the real stalls.
- **A restart loop.** A request that blocks the loop and is retried by its client would stall the loop again after each restart; the budget ends that after 3 exits.
- **State lost at an exit.**
  - Up to 30 s of hoster state and up to 1 min of plugin history are lost.
  - A proxied playback is cut, but a stalled loop has already stopped it. A direct stream plays from the hoster's CDN and does not notice.
- **The warm-up after a restart.** About 30 s until the browser is up (Chromium about 13 s, then the stealth context).
- **A held interpreter.** A C call that holds the GIL starves the monitor thread; the `faulthandler` timer dumps from C at twice the limit.
  - The monitor thread arms the timer again at every check, so in a stall it sees the timer never fires: its dump stays the only one.
  - With the switch on and an exit left in the budget the timer exits too (`exit=True`), with `faulthandler`'s code 1; the budget does not count that exit, since C code cannot write its file.
  - Armed by the heartbeat instead, the timer would exit a stall the budget had spared, at twice the limit, and start a thread on the loop at every tick.
  - A failed check and the thread's end cancel the timer, so it never fires on a healthy loop.
- **A blocked log output.** If the log driver blocks standard output, the structured line may not get out and the dump may block; the exit does not wait on either (D4).

## Migration Plan

None: two new settings with defaults, the switch off.

## Open Questions

- Whether 120 s is the right limit: the readings decide before the switch is turned on.
- Whether 5 and 10 min fit as the stuck limits: a Stremio stream request answers by its deadline (`stremio.stream_deadline_seconds`, 60 s by default), so 5 min is a clear defect; Torznab searches have no overall bound, so 10 min is generous.
