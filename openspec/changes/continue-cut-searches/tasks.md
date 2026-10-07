# Tasks: continue-cut-searches

TDD throughout (AGENTS.md §6); each numbered group is one commit with its
tests and docs. Mock patterns: `CachePort` → `AsyncMock`; `TelemetryPort` →
`NO_TELEMETRY`; the health monitor and the plugin record are synchronous
fakes. Order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9; 6 and 7 before 8, as the
audit's baseline (7.1) must run before the filter change (6) ships.

## 1. Audit baseline

- [x] 1.1 `scripts/probes/series_episodes.py`: runs the plugin searches for the series ids of `docs/plans/round-titles.txt` plus Severance (`tt11280740`) S01E05 and S02E05 against a dev server or through the plugin registry; per plugin: results, with season and episode, with episode only, without either, kept, narrowed, dropped, leaks. Markdown table; `--json`.
- [x] 1.2 Baseline run on the dev server with today's filter, table into `docs/plans/series-episodes.md` ("Before").

## 2. Runner: no cut at the deadline

- [x] 2.1 `application/stremio/plugin_search.py`: `_run_plugin_with_timeout` gives every plugin `self._plugin_timeout` from its own start; the `deadline` parameter and `stremio_plugin_skipped_deadline` go; a plugin waiting for a slot runs when it gets one. Telemetry outcome `late` for a plugin that returns after the answer budget (the runner gets the budget's monotonic time to label it). The `stremio_plugin_timeout` log loses `cut_by_deadline`.
- [x] 2.2 Tests in `tests/unit/application/test_plugin_search_runner.py`: a plugin that holds its slot past the budget returns its results; a plugin queued at the budget runs; outcome `late`.

## 3. Entry: missing plugins and per-plugin merge

- [x] 3.1 `application/stremio/search_cache.py`: `CachedSearch.missing: tuple[str, ...] = ()`; `merge(entry, plugin, results)` replaces that plugin's results (by `source_plugin` metadata) and drops the name from `missing`; `stored_at` is the search's start.
- [x] 3.2 `application/stremio/search_progress.py`: the progress knows the selected plugins, which have finished, and writes the entry at the answer budget (with `missing`) and after every late plugin (merge), through the search cache; the first write keeps the "at least one matching result" condition.
- [x] 3.3 Tests in `tests/unit/application/test_search_cache.py` and `test_search_progress.py`: write at the budget with `missing`; late merge; failed plugin stays missing; an old entry without the field is complete; age from the search start; a merge never thins an entry.

## 4. Title search: budget, completion, merging refresh

- [x] 4.1 `application/stremio/title_search.py`: the request's wait ends at `plugin_timeout_seconds` after the request start (the answer budget), the search task runs on; a request that finds a running search past its budget gets source `cache` with the progress's results (no wait); `joined` only within the budget.
- [x] 4.2 Completion search: on a hit of an entry with `missing` and no running search for the key, start a search for the missing plugins only (single-flight per key, the process-wide background slot shared with refreshes); merge per plugin; clear `missing` at its end. Log `stremio_search_completion` with `plugins` and the outcome.
- [x] 4.3 Stale refresh merges per plugin (replace finished plugins' results, keep the others'); the comment at `title_search.py:38-42` is replaced by the new rule.
- [x] 4.4 Tests in `tests/unit/application/test_title_search.py`: budget without cancellation; retry answers at once; completion starts once and clears `missing`; completion waits for the slot; refresh keeps a cut plugin's old results.

## 5. Resolution: late results in the background, retry at once

- [x] 5.1 `application/stremio/resolution.py`: the search task hands late, title-filtered results to the background resolution (one per key, one process-wide, under the stream deadline from the hand-over); a request answering from a partial entry or a running search past its budget takes the cached outcomes and starts the background resolution of the uncached links, like a cached request today.
- [x] 5.2 `application/use_cases/stremio_stream.py`: the answer carries `complete` (no missing plugin and no running search for the key) and the source; `stremio_stream_response` logs `complete` and `missing_count`.
- [x] 5.3 Tests in `tests/unit/application/test_stremio_resolution.py` and the use-case tests: late results resolved in the background appear on the next request; the retry does not wait.

## 6. Unreachable at search time

- [x] 6.1 `domain/plugins/base.py`: `PluginUnreachableError`. `infrastructure/plugins/httpx_base.py` `_verify_domain` and the Playwright base's domain check raise it when no domain answers (the `<name>_no_domain_reachable` warning stays).
- [x] 6.2 `infrastructure/plugins/health_monitor.py`: `mark_unreachable(name)` adds to the monitor's set (the recheck clears it as today); the runner gets the monitor (`interfaces/composition.py`) and maps the error: mark, plugin record `unreachable`, telemetry outcome `unreachable`.
- [x] 6.3 Tests: `test_plugin_health.py` (mark and recheck), the runner test (error → mark and count), a base-class test (no domain → error).

## 7. Episode filter

- [x] 7.1 `infrastructure/stremio/episode_filter.py`: metadata `season`/`episode` before the guess; no episode → links labelled with the requested episode only, dropped without any labelled link; episode without season → season 1 or metadata only. `domain/plugins/base.py` documents the metadata keys.
- [x] 7.2 `application/stremio/plugin_search.py`: count dropped results per plugin in the plugin record (`PluginCounter` gains `dropped`); `stremio_search_complete` carries `dropped`.
- [x] 7.3 Tests in `tests/unit/infrastructure/test_episode_filter.py`: the four spec scenarios; runner test for the count.
- [ ] 7.4 Audit run after the change ("After" table in `docs/plans/series-episodes.md`): leaks zero, no plugin's right-episode kept count lower than before; plugins with dropped > 0 listed under "Parser follow-ups" with their page evidence (one backlog Order row per plugin, step 22).

## 8. Stream response headers

- [x] 8.1 `interfaces/api/stremio/router.py`: `X-Cache` from the source (`MISS`, `HIT`, `STALE`, `JOINED`) and `X-Search-Complete` (`true`/`false`) on the stream response.
- [x] 8.2 Router tests for both headers; `scripts/stremio_round.py` records both (its `X-Cache` column exists).

## 9. Docs and production check

- [x] 9.1 `docs/features/stremio-addon.md`: the flow (budget, continuation, partial entries, completion, retry at once, headers); the sentence about cut plugins' results replaced. `docs/features/observability.md`: outcomes `late` and `unreachable`, counters `dropped` and `unreachable`, the `stremio_search_completion` event, `complete` in the response log. `docs/features/configuration.md`: `plugin_timeout_seconds` is also the answer budget. `AGENTS.md` §3: the search invariant sentence. `CHANGELOG.md`.
- [ ] 9.2 After the next deploy: `prodctl.py logs --grep stremio_search_complete` shows a search with `missing` emptied by late plugins; `prodctl.py digest` shows `dropped` and `unreachable`; a Severance episode request answered from a partial entry carries `X-Search-Complete: false` and the retry a minute later more streams. Record the numbers in `docs/plans/ideas-backlog.md` (Order row 21).
