## ADDED Requirements

### Requirement: Resolver State Survives Restarts
The system SHALL keep a snapshot of the hoster resolver registry's unexpired resolutions and redirects and of the open circuit breakers in the cache backend, written when the state changed (at most every 30 s) and at graceful shutdown, and SHALL restore it at startup with each entry's lifetime reduced by the downtime.

#### Scenario: Cached answer after a restart
- **WHEN** a hoster URL was resolved to a video 10 minutes before a restart and the same title is requested 5 minutes after the start
- **THEN** the registry answers the resolution from the restored entry, with 45 minutes of lifetime left
- **AND** the cached search answer goes out without waiting for the resolve grace

#### Scenario: Expired entry
- **WHEN** a "dead" entry had 2 minutes left and the process was down for 10 minutes
- **THEN** the entry is not restored

#### Scenario: Open breaker restored
- **WHEN** a hoster's breaker was open with 40 s of a 120 s cooldown left and the process was down for 15 s
- **THEN** the breaker is open after the start with 25 s left and reopens with a 240 s cooldown if its probe fails

#### Scenario: No cache backend
- **WHEN** no cache backend is configured
- **THEN** nothing is written or restored and the registry behaves as before this change

### Requirement: Snapshot Integrity
The snapshot SHALL carry a version and the time it was taken; a snapshot of another version or one that cannot be read SHALL be discarded and deleted, with a log event, and the start SHALL continue with empty state.

#### Scenario: Snapshot from another code version
- **WHEN** the stored snapshot's version differs from the running code's
- **THEN** the log has `hoster_state_discarded`, the key is deleted, and the registry starts empty

#### Scenario: Clock moved backwards
- **WHEN** the snapshot's time lies in the future at startup
- **THEN** the downtime counts as zero and the entries keep their stored lifetimes

### Requirement: Restore Is Observable
The startup SHALL log `hoster_state_restored` with the counts of resolutions, redirects and breakers restored and the snapshot's age, and `/api/v1/stats/metrics` SHALL report the restore in its JSON.

#### Scenario: First start
- **WHEN** no snapshot exists
- **THEN** `hoster_state_restored` reports zero entries and no error
