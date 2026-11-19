# 0016: A thread in the process names a stalled loop, its exit switch off

> **Purpose:** Records why a thread in the app's own process names a stalled event loop and why its exit is a switch that is off.
> **Status:** target
> **As of:** 86d497b5

## Context

Production runs the app in one Docker Compose container with `restart: unless-stopped`, tini as PID 1 and a healthcheck that asks `/api/v1/readyz` every 30 s.
The restart policy acts only when the process exits; a failing healthcheck marks the container unhealthy, and plain Docker restarts nothing for it.
A synchronous call that holds the event loop stops every request, and the health routes time out with it.
Such a hang lasts until someone restarts the container, and the log names no cause.
The options weighed were a diagnosis in the process with or without an exit, an exit from the first day, an autoheal container and an orchestrator's liveness probe.

## Decision

A thread of the app's own process must read the event loop's heartbeat, the loop lag monitor's tick, and name a stall past `watchdog.stall_seconds` with the loop thread's stack and a dump of every thread.
The loop must pay no more for it than one timestamp per tick.
The exit is a switch, `watchdog.exit_on_stall`, that must stay off until the readings of real stalls choose the limit.
Switched on, the monitor must end the process with code 3 without waiting on a lock or a write the stalled thread may hold, at most 3 times within 30 min.
A `faulthandler` timer at twice the limit, armed again at every check of the monitor's thread, must dump the tracebacks of a stall in which no Python thread runs.

## Consequences

- A stall names its cause in the log, `event_loop_stalled` with the stack and the dump on standard error, whether or not the process exits.
- With the switch off a stall lasts until the loop recovers or someone restarts the container.
- With the switch on the restart policy restarts a stalled process, and the budget's file in the cache directory ends a restart loop after 3 exits.
- An exit loses up to 30 s of hoster state and up to 1 min of plugin history, cuts a proxied playback, and the browser needs about 30 s to warm up again.
- A stall that holds the interpreter exits through `faulthandler` with code 1 when the switch is on, and the budget does not count that exit, since C code cannot write its file.
- The timer never fires while the monitor's thread runs, so a stall the thread names has one dump, and a failed check or the thread's end cancels the timer.

## Alternatives

- A diagnosis without an exit: it names the cause but leaves every stall to a manual restart; the switch keeps that as its default.
- An exit from the first day: no reading stands behind the limit of 120 s yet, and an exit cuts a proxied playback and loses state.
- An autoheal container: it needs the Docker socket, sees only what `readyz` sees, restarts in a loop during a network loss that a restart does not mend, and logs no cause.
- An orchestrator's liveness probe: k3s beside Chromium on the production host is a new deployment for one probe, and Swarm stacks ignore `network_mode`, which the VPN setup needs.
- The timer armed by the heartbeat: it would start a thread on the loop at every tick, and with the switch on it would exit a stall whose exit the budget had skipped.

## Status

accepted

## References

- `src/scavengarr/infrastructure/telemetry/watchdog.py`: the monitor, its timer and the exit budget.
- `src/scavengarr/infrastructure/telemetry/loop_lag.py`: the heartbeat.
- [Observability](../../current/observability.md#stalls): the lines, the switch and the budget.
- [Operations](../../how-to/operations.md#2-read-the-logs): reading a stall on production.
- [`openspec/changes/add-stall-watchdog/design.md`](../../../openspec/changes/add-stall-watchdog/design.md): the design and its risks.
