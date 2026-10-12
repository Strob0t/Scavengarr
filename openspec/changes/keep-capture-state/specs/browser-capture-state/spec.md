## ADDED Requirements

### Requirement: A Capture Hoster's State Is Kept
After a capture found its stream, the stealth pool SHALL keep the storage state (cookies, localStorage, IndexedDB) of the capture page's host and its frames' hosts in the cache backend, at most once per host per 10 minutes, for at most 24 hours, the cache write in the background. A cookie SHALL be kept only when its domain is such a host or a parent domain of one.

#### Scenario: Kept after a stream
- **WHEN** Dropload's capture finds its stream after the Turnstile widget
- **THEN** the cookies of Dropload's host are kept (the widget's frame is gone by then)
- **AND** `capture_state_saved` names the hosts, the counts and how long the read took, without values

#### Scenario: Other hosts stay out
- **WHEN** an ad domain without a frame on the capture page set a cookie in the context
- **THEN** that cookie is not kept

#### Scenario: Once per host per 10 minutes
- **WHEN** captures on the same host find a stream twice within 10 minutes
- **THEN** its state is kept once

#### Scenario: No stream
- **WHEN** a capture ends without a stream
- **THEN** nothing is kept for its hosts

### Requirement: The State Is Read From The Capture's Own Page
The stealth pool SHALL read the state it keeps from the capture's own page before the page closes, each origin in one of its frames, visiting no other origin. The resolution SHALL wait for the read at most 0.5 seconds, and not at all when its resolve bound has less time left. An IndexedDB database SHALL be kept whole or not at all: one with a record that plain JSON cannot hold SHALL NOT be kept.

#### Scenario: Read from the capture's own page
- **WHEN** a capture finds its stream
- **THEN** each frame on a kept host reads its own origin's storage before the page closes
- **AND** no other page and no other origin is visited

#### Scenario: Close to the resolve bound
- **WHEN** a capture finds its stream with less than 0.5 seconds of its resolve bound left
- **THEN** the stream is returned at once and nothing is read or kept

#### Scenario: A database plain JSON cannot hold
- **WHEN** a frame's IndexedDB database holds a record with a date, a file or a typed array
- **THEN** that database is not kept, and the rest of the state is

### Requirement: Every New Context Starts With The Kept State
The stealth pool SHALL create each new browser context (at the start, after a recycle, after a relaunch) with the unexpired clearance cookies and every kept host's unexpired cookies, localStorage and IndexedDB, the newest winning per cookie, whether the clearance cookies or a kept state hold it, and per origin. A kept state older than 24 hours SHALL NOT be restored. The restore SHALL be logged once the context exists.

#### Scenario: After a restart
- **WHEN** the app restarts and a Dropload link is captured whose host's state was kept before the restart
- **THEN** the context was created with that state
- **AND** the capture meets no Turnstile widget

#### Scenario: After a recycle
- **WHEN** the shared browser recycles after 200 pages
- **THEN** the next context is created with the kept states and the clearance cookies

#### Scenario: A newer clearance
- **WHEN** a capture kept Dropload's `cf_clearance` A, and a challenge within the host's 10-minute keep interval stored B among the clearance cookies
- **THEN** the next context is created with B

#### Scenario: Expired
- **WHEN** a host's state was kept 25 hours ago
- **THEN** the next context is created without it

### Requirement: A Refused State Keeps The Cookies
When the browser refuses a context with the kept state, the stealth pool SHALL create it with the cookies alone and the store SHALL drop the kept origins, so that later contexts do not fail the same way. When the browser refuses the cookies too, the context SHALL be created without the state. Each refusal SHALL be logged and SHALL NOT fail a capture.

#### Scenario: A failed restore
- **WHEN** the creation of a context with the kept state fails (an IndexedDB record the browser cannot restore)
- **THEN** the context is created with the clearance cookies and the kept cookies, and `capture_state_restore_failed` is logged
- **AND** the kept origins are dropped and their cookies stay (`capture_state_origins_dropped`), so the next context is created with the cookies

#### Scenario: The cookies refused too
- **WHEN** the creation with the cookies alone fails as well
- **THEN** the context is created without the state, and the kept origins stay

### Requirement: The Browser Starts With The App
Once the app has reported ready, it SHALL launch the shared browser and create the stealth pool's context, with the kept state, in the background, so the first capture after a start does not wait for the launch. Readiness SHALL NOT wait for it. A failed warm-up SHALL be logged with its error type and leave the launch to the first capture. Shutdown SHALL end it.

#### Scenario: A breaker trial after a restart
- **WHEN** the app restarts and a capture hoster's breaker trial comes after the warm-up ended
- **THEN** the trial's resolve bound runs for the capture alone, without the browser's launch

#### Scenario: The kept state at the start
- **WHEN** the app starts with a kept capture state
- **THEN** `capture_state_restored` is logged by the warm-up once its context exists, before any capture

#### Scenario: A failed warm-up
- **WHEN** the browser cannot be launched at the start
- **THEN** `browser_warmup_failed` is logged with the error type, and the first capture launches the browser as before

### Requirement: The Browser Restarts Warm
The shared browser's restart after 200 pages SHALL launch the new browser before the operations that wait for it go on, so that no operation launches it inside its own resolve bound. An operation SHALL wait for the restart with its resolve bound stopped. A restart that fails or takes longer than 30 s SHALL be logged with its error type and SHALL leave the launch to the next operation.

#### Scenario: A capture after a recycle
- **WHEN** the shared browser restarted after 200 pages and a capture comes after the restart
- **THEN** the capture's resolve bound runs for its context and its page, without the browser's launch

#### Scenario: A capture during the restart
- **WHEN** a capture asks for the browser while the restart runs
- **THEN** it waits with its resolve bound stopped, and keeps the time it had left

#### Scenario: A failed relaunch
- **WHEN** the new browser cannot be launched within 30 s
- **THEN** `shared_browser_restart_failed` is logged with the error type, the waiting operations go on, and the next one launches the browser

### Requirement: The Kept State Is Bounded And Quiet
A host's state larger than 512 KiB once serialized SHALL NOT be kept and SHALL be logged with its host and size; a corrupt stored entry SHALL be dropped with a warning; no log line SHALL carry a cookie's or a storage entry's value or a URL.

#### Scenario: Too large
- **WHEN** a host's filtered state serializes to 600 KiB
- **THEN** it is not kept and `capture_state_too_large` names the host and the size

#### Scenario: Corrupt entry
- **WHEN** the stored capture state is not valid JSON
- **THEN** it is dropped, `capture_state_corrupt` is logged, and the context is created with the clearance cookies alone
