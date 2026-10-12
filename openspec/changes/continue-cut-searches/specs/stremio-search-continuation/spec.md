## ADDED Requirements

### Requirement: Plugins Run To Their Own Timeout
The plugin search of a Stremio request SHALL give every selected plugin its full `stremio.plugin_timeout_seconds`, counted from the moment the plugin holds a search slot, and SHALL NOT cancel a plugin because the request's answer budget has passed. A plugin waiting for a slot when the budget passes SHALL still run when it gets one. The search SHALL end when every plugin has ended.

#### Scenario: Plugin still running at the answer budget
- **WHEN** a plugin got its slot 25 s after the request started and the answer budget is 30 s
- **THEN** the plugin keeps running until it returns or its own 30 s timeout ends
- **AND** the `plugin_search` telemetry counts its outcome as `late` when it returns after the budget

#### Scenario: Plugin waiting for a slot at the answer budget
- **WHEN** the answer budget passes while a plugin waits for a search slot
- **THEN** the plugin runs when it gets the slot, with its full timeout

### Requirement: The Answer Waits For The Budget Only
A request SHALL stop waiting for its search at `stremio.plugin_timeout_seconds` after the request started, at `stremio.resolve_target_count` resolved videos, or when the search has ended, whichever comes first, and SHALL answer with the results known by then and their resolved streams within `stremio.stream_deadline_seconds`.

#### Scenario: Answer at the budget with the search running
- **WHEN** two plugins have returned 2 results and three plugins are still running when the budget passes
- **THEN** the request answers with the streams resolved from those 2 results
- **AND** the search goes on

### Requirement: Partial Entries Name Their Missing Plugins
The search cache entry SHALL carry the names of the plugins that had not finished when it was written (`missing`). The entry SHALL be written when the answer budget passes with at least one matching result, and SHALL be written again whenever a plugin finishes afterwards: that plugin's results replace its earlier results in the entry and its name leaves `missing`. The entry's age SHALL count from the search's start. An entry written before this change SHALL read as complete.

#### Scenario: Entry written at the budget
- **WHEN** the budget passes with sto and kinoger still running
- **THEN** the entry holds the results so far and `missing` is `("kinoger", "sto")`

#### Scenario: Late plugin merged
- **WHEN** sto finishes 6 s after the budget with 4 results
- **THEN** the entry holds the earlier results and sto's 4 results and `missing` is `("kinoger",)`
- **AND** the entry's age still counts from the search's start

#### Scenario: Plugin failed or timed out
- **WHEN** kinoger ends with a timeout
- **THEN** kinoger stays in `missing`

#### Scenario: Entry from before the change
- **WHEN** a cached entry has no `missing` field
- **THEN** it is treated as complete

### Requirement: Merges Never Thin An Entry
A completion search and a stale refresh SHALL replace a plugin's results in the entry only when that plugin finished; a plugin that did not finish SHALL keep its earlier results in the entry.

#### Scenario: Refresh with one plugin cut
- **WHEN** a stale refresh ends with hdfilme finished with 1 result and sto timed out, and the entry held 2 hdfilme results and 4 sto results
- **THEN** the entry holds hdfilme's 1 new result and sto's 4 earlier results

### Requirement: Late Results Are Resolved In The Background
Results that arrive after a request's answer, from the continuing search, a completion search or a refresh, SHALL have their best link per hoster resolved in the background through the hoster resolver registry, one background resolution per cache key and one at a time process-wide, within `stremio.stream_deadline_seconds` from the hand-over, so that a later request finds their outcomes cached.

#### Scenario: Late results before the next request
- **WHEN** sto's 4 late results arrived and their links were resolved in the background
- **THEN** the next request for the episode answers at once with sto's streams among its answer

### Requirement: A Retry Answers At Once
A request that finds a running search past its answer budget, or a cache entry with plugins missing, SHALL answer immediately from the results known so far with the resolver registry's cached outcomes, SHALL start the background resolution of the uncached links, and SHALL NOT wait for the continuing search. A request that arrives within the first request's answer budget SHALL share that wait (`joined`).

#### Scenario: Retry during the continuation
- **WHEN** a request for the episode arrives 20 s after the first request answered, while two plugins are still running
- **THEN** it answers at once with every stream resolved so far
- **AND** its `X-Search-Complete` header is `false`

#### Scenario: Second request within the budget
- **WHEN** a request for the episode arrives 2 s after the first one, before any answer
- **THEN** it shares the first request's wait and its `X-Cache` header is `JOINED`

### Requirement: One Completion For Missing Plugins
A request that finds an entry whose search has ended with plugins in `missing` SHALL start one completion search for those plugins only, single-flight per cache key, sharing the process-wide background search slot with stale refreshes. The completion SHALL merge per plugin and SHALL clear `missing` when it ends, whatever each plugin's outcome, so that no entry is completed twice.

#### Scenario: Completion after a cut
- **WHEN** a request finds an entry with `missing` `("kinoger", "sto")` and no running search for the key
- **THEN** a search for kinoger and sto only starts in the background
- **AND** the request answers at once from the entry

#### Scenario: Completion fails again
- **WHEN** the completion ends with sto timed out again
- **THEN** the entry keeps its results, `missing` is empty and no further completion starts before the entry is stale

#### Scenario: Completion while a refresh runs
- **WHEN** the background search slot is taken by a refresh for another key
- **THEN** the completion waits for the slot

### Requirement: Unreachable At Search Time
A plugin whose domain check finds no reachable domain during a search SHALL raise a plugin-unreachable error, and the search runner SHALL mark the plugin unreachable in the health monitor at once, count `unreachable` in the plugin record and report the telemetry outcome `unreachable`. The plugin SHALL stay skipped until the health monitor's recheck finds its site answering.

#### Scenario: Dead domains during a search
- **WHEN** movie4k's domain check fails for every domain during a search
- **THEN** movie4k is marked unreachable and the next search skips it
- **AND** the plugin record counts one `unreachable` for movie4k

#### Scenario: Site back
- **WHEN** the health monitor's recheck finds movie4k answering
- **THEN** the next search includes movie4k again

### Requirement: Episode Filter Without Leaks
For a series request the episode filter SHALL read season and episode from the result's metadata (`season`, `episode`) before the release-name guess, SHALL pass a result without an episode number only through the links labelled with the requested episode, SHALL pass a result with an episode but no season only for season 1 or a metadata season, and SHALL count the dropped results per plugin in the plugin record (`dropped`).

#### Scenario: Show page without labelled links
- **WHEN** a result's title is `Severance` with no episode and none of its links carries an `SxxExx` label
- **THEN** the result is dropped and the plugin record counts one `dropped`

#### Scenario: Season pack with labelled links
- **WHEN** a result's title is `Severance S01` and its links carry `S01E04` and `S01E05` labels, for a request of S01E05
- **THEN** the result passes with the `S01E05` link only

#### Scenario: Episode without season for season 2
- **WHEN** a result's title is `Severance E05` with no season and no metadata, for a request of S02E05
- **THEN** the result is dropped

#### Scenario: Metadata trusted
- **WHEN** a result's title carries no episode and its metadata says season 2 and episode 5, for a request of S02E05
- **THEN** the result passes

### Requirement: Series Hit Audit
`scripts/probes/series_episodes.py` SHALL run the plugin searches for the series of `docs/plans/round-titles.txt` plus Severance S01E05 and S02E05 and print per plugin: results, with season and episode, with episode only, without either, kept, narrowed, dropped, and leaks (kept results whose labels name another episode). The audit SHALL run before and after the filter change, with both tables recorded in `docs/plans/series-episodes.md`.

#### Scenario: Audit before the change
- **WHEN** the audit runs on the dev server with today's filter
- **THEN** its table is the "Before" table of `docs/plans/series-episodes.md`

#### Scenario: Audit after the change
- **WHEN** the audit runs on the dev server after the filter change
- **THEN** every plugin's leaks are zero
- **AND** no plugin's count of kept results with the right episode is lower than before
- **AND** a plugin with a dropped count above zero is listed for a parser follow-up

### Requirement: Stream Response Headers
The Stremio stream route SHALL set `X-Cache` to `MISS` for a fresh search, `HIT` for a cache entry, `STALE` for a stale entry and `JOINED` for a shared wait, and `X-Search-Complete` to `true` when the answer's entry has no missing plugin and no search continues for the key, else `false`.

#### Scenario: Complete cached answer
- **WHEN** a request is answered from an entry with no missing plugin and no running search
- **THEN** the response carries `X-Cache: HIT` and `X-Search-Complete: true`

#### Scenario: Fresh search cut at the budget
- **WHEN** a request answers at the budget with plugins still running
- **THEN** the response carries `X-Cache: MISS` and `X-Search-Complete: false`
