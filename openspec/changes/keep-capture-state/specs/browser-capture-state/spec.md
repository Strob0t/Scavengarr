## ADDED Requirements

### Requirement: A Capture Hoster's State Is Kept
After a capture found its stream, the stealth pool SHALL keep the storage state (cookies, localStorage, IndexedDB) of the capture page's host and its frames' hosts in the cache backend, in the background so that the resolution does not wait, at most once per host per 10 minutes, for at most 24 hours. A cookie SHALL be kept only when its domain is such a host or a parent domain of one.

#### Scenario: Kept after a stream
- **WHEN** Dropload's capture finds its stream after the Turnstile widget
- **THEN** the cookies of Dropload's host and the localStorage of the widget frame's host are kept
- **AND** `capture_state_saved` names the hosts and counts, without values

#### Scenario: Other hosts stay out
- **WHEN** an ad domain without a frame on the capture page set a cookie in the context
- **THEN** that cookie is not kept

#### Scenario: Once per host per 10 minutes
- **WHEN** captures on the same host find a stream twice within 10 minutes
- **THEN** its state is kept once

#### Scenario: No stream
- **WHEN** a capture ends without a stream
- **THEN** nothing is kept for its hosts

### Requirement: Every New Context Starts With The Kept State
The stealth pool SHALL create each new browser context (at the start, after a recycle, after a relaunch) with the unexpired clearance cookies and every kept host's unexpired cookies, localStorage and IndexedDB, the newest winning per cookie and per origin. A kept state older than 24 hours SHALL NOT be restored. A failure to restore SHALL leave a context without the state, logged, never a failed capture.

#### Scenario: After a restart
- **WHEN** the app restarts and a Dropload link is captured whose host's state was kept before the restart
- **THEN** the context was created with that state
- **AND** the capture meets no Turnstile widget

#### Scenario: After a recycle
- **WHEN** the shared browser recycles after 200 pages
- **THEN** the next context is created with the kept states and the clearance cookies

#### Scenario: Expired
- **WHEN** a host's state was kept 25 hours ago
- **THEN** the next context is created without it

#### Scenario: A failed restore
- **WHEN** the creation of a context with the kept state fails
- **THEN** the context is created without it and `capture_state_restore_failed` is logged

### Requirement: The Browser Starts With The App
Once the app has reported ready, it SHALL launch the shared browser and create the stealth pool's context, with the kept state, in the background, so the first capture after a start does not wait for the launch. Readiness SHALL NOT wait for it. A failed warm-up SHALL be logged with its error type and leave the launch to the first capture. Shutdown SHALL end it.

#### Scenario: A breaker trial after a restart
- **WHEN** the app restarts and a capture hoster's breaker trial comes after the warm-up ended
- **THEN** the trial's resolve bound runs for the capture alone, without the browser's launch

#### Scenario: The kept state at the start
- **WHEN** the app starts with a kept capture state
- **THEN** `capture_state_restored` is logged by the warm-up, before any capture

#### Scenario: A failed warm-up
- **WHEN** the browser cannot be launched at the start
- **THEN** `browser_warmup_failed` is logged with the error type, and the first capture launches the browser as before

### Requirement: The Kept State Is Bounded And Quiet
A host's state larger than 512 KiB once serialized SHALL NOT be kept and SHALL be logged with its host and size; a corrupt stored entry SHALL be dropped with a warning; no log line SHALL carry a cookie's or a storage entry's value or a URL.

#### Scenario: Too large
- **WHEN** a host's filtered state serializes to 600 KiB
- **THEN** it is not kept and `capture_state_too_large` names the host and the size

#### Scenario: Corrupt entry
- **WHEN** the stored capture state is not valid JSON
- **THEN** it is dropped, `capture_state_corrupt` is logged, and the context is created with the clearance cookies alone
