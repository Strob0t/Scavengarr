## ADDED Requirements

### Requirement: Stalled Event Loop Detection
The service SHALL detect from outside the event loop when the loop has not run its heartbeat for `watchdog.stall_seconds` (default 120; 0 SHALL turn the detection off). Once per stall it SHALL log `event_loop_stalled` with the stall's age and the main thread's stack, and dump the traceback of every thread to standard error. When the heartbeat runs again it SHALL log `event_loop_recovered` with the stall's length.

#### Scenario: A blocked loop is named
- **WHEN** a synchronous call holds the event loop for longer than `watchdog.stall_seconds`
- **THEN** one `event_loop_stalled` line names the stall's age and the main thread's stack
- **AND** standard error holds the traceback of every thread

#### Scenario: A busy loop is no stall
- **WHEN** the host's CPU is saturated and the loop's lag rises while its heartbeat still runs within the limit
- **THEN** no `event_loop_stalled` line is logged

#### Scenario: The loop recovers
- **WHEN** a stall ends
- **THEN** `event_loop_recovered` names the stall's length
- **AND** the next stall is logged again

#### Scenario: Detection off
- **WHEN** `watchdog.stall_seconds` is 0
- **THEN** no monitor thread runs

### Requirement: Held Interpreter Backstop
While the detection is on, the service SHALL keep a `faulthandler` timer armed at twice `watchdog.stall_seconds` and re-armed at every check of the monitor thread, so that a stall in which no Python thread can run still dumps the traceback of every thread. The timer SHALL end the process only while `watchdog.exit_on_stall` is true and the exit budget has an exit left. A failed check and the end of the monitor thread SHALL leave no timer armed.

#### Scenario: No Python thread runs
- **WHEN** a call holds the interpreter for more than twice `watchdog.stall_seconds`
- **THEN** standard error holds the traceback of every thread

#### Scenario: The monitor thread runs during the stall
- **WHEN** a call that lets other threads run holds the event loop for more than twice `watchdog.stall_seconds`
- **THEN** standard error holds one dump, the monitor's

#### Scenario: A failed check
- **WHEN** one check of the monitor thread raises
- **THEN** the thread keeps running and its next check arms the timer again

#### Scenario: The monitor stops
- **WHEN** the monitor stops at shutdown
- **THEN** no timer stays armed

### Requirement: Exit On Stall Is Opt-In
The service SHALL end its process on a detected stall only when `watchdog.exit_on_stall` is true (default false). It SHALL then exit with code 3 after the dump, without waiting on a lock or a write the stalled thread may hold, so that the container's restart policy restarts it. After 3 exits within 30 minutes, counted in a file that outlives a restart of the container, a further stall SHALL only be logged, with its exit skipped.

#### Scenario: Switch off
- **WHEN** a stall is detected and `watchdog.exit_on_stall` is false
- **THEN** the process keeps running after the log line and the dump

#### Scenario: Switch on
- **WHEN** a stall is detected and `watchdog.exit_on_stall` is true
- **THEN** the process exits with code 3 after the dump

#### Scenario: Budget spent
- **WHEN** the process exited on 3 stalls within the last 30 minutes and a fourth stall is detected
- **THEN** the stall is logged with its exit skipped
- **AND** the process keeps running

### Requirement: Background Task Deaths Are Logged
Every long-lived background task the service starts SHALL, when it ends with an exception other than a cancellation, log `background_task_died` with the task's name and the exception, and count it in `scavengarr_background_task_deaths_total` by the task's name from a fixed set.

#### Scenario: A task raises
- **WHEN** the loop lag monitor's task ends with an exception
- **THEN** `background_task_died` names `loop_lag` and the exception
- **AND** `scavengarr_background_task_deaths_total{task="loop_lag"}` rises by 1

#### Scenario: A task is cancelled at shutdown
- **WHEN** a background task is cancelled
- **THEN** nothing is logged or counted

### Requirement: Stuck Requests Are Logged
The service SHALL log `request_stuck` once for a Stremio request running longer than 5 minutes and for a Torznab request running longer than 10 minutes, with the request's route class from a fixed set, its age and its task's stack, and count it in `scavengarr_requests_stuck_total` by route class. Play and proxy requests SHALL NOT be watched.

#### Scenario: A hanging stream request
- **WHEN** a Stremio stream request has run for more than 5 minutes
- **THEN** one `request_stuck` line names its route class, its age and its stack

#### Scenario: A long playback
- **WHEN** a proxy request has streamed for an hour
- **THEN** nothing is logged or counted

### Requirement: The Loop Lag Histogram Covers Stalls
`scavengarr_event_loop_lag_seconds` SHALL have buckets up to 120 seconds, so that a stall the loop survived remains in the scraped metrics after the container's logs are gone.

#### Scenario: A long stall is counted
- **WHEN** the loop stalls for 40 seconds and recovers
- **THEN** the bucket `le="60"` counts the stall
- **AND** the bucket `le="30"` does not
