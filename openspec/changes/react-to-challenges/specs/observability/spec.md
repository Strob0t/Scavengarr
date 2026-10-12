## MODIFIED Requirements

### Requirement: Hoster Resolution Metrics
The system SHALL record each hoster resolution in the hoster resolver registry, labeled with the resolver's name. It SHALL record each background completion of a challenged capture as the stage `hoster_challenge_completion`, labeled with the resolver's name, with the outcome `stream`, `none` or `cancelled` and its duration from the cut.

#### Scenario: Unplayable stream
- **WHEN** a resolver returns a stream that fails the playback check
- **THEN** `scavengarr_hoster_resolve_total{resolver, outcome="unplayable"}` grows by 1

#### Scenario: Cached resolution
- **WHEN** a URL's resolution comes from the registry's cache
- **THEN** `outcome="cached"` is counted for the resolver that resolved it, without a duration

#### Scenario: Open hoster breaker
- **WHEN** a resolver is skipped because its breaker is open
- **THEN** `outcome="breaker_open"` is counted for it

#### Scenario: Challenge
- **WHEN** a resolution ends as `challenge`
- **THEN** `scavengarr_hoster_resolve_total{resolver, outcome="challenge"}` grows by 1

#### Scenario: Challenge cooldown
- **WHEN** a link is skipped because its hoster's challenge cooldown runs
- **THEN** `outcome="challenge_cooldown"` is counted for the resolver, without a duration

#### Scenario: Background completion
- **WHEN** a completion finds a stream 7 s after the cut
- **THEN** `scavengarr_hoster_challenge_completion_total{resolver, outcome="stream"}` grows by 1
- **AND** 7 s is observed in `scavengarr_hoster_challenge_completion_seconds{resolver}`
