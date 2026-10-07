# Change: Continue cut searches and keep wrong episodes out

## Why

A Stremio request's plugin search is cut at `stremio.plugin_timeout_seconds`
(30 s from the request start): every plugin still running is cancelled and
contributes nothing, and the results of the plugins that finished are cached
as if they were the whole answer, for `cache.search_ttl_seconds` (30 minutes
in production). Production, 2026-10-07, Severance S01E05: the dead domains of
kinoger, megakino_to and movie4k held search slots for up to 26 s, sto was
cancelled at the 30 s mark, the entry was stored with 2 results and 1 stream,
and every request for that episode in the following minutes was answered from
it (`stremio_search_cache_hit stale=False age_s=50 … 144`). Two minutes later
the same search for S01E03, with the dead sites marked unreachable by then,
found 4 results in 2 s. The maintainer saw too few sources and wrong episodes
and asked that a request shortly after a cut search continues the search
instead of answering from the partial cache.

Two more causes sit next to the cut:

- The episode filter lets a series result without an episode number pass
  (a show page or a season pack); when none of its links carries an
  `SxxExx` label, all its links pass and the stream is labelled with the
  requested episode. A result with an episode but no season skips the season
  check. These are the wrong episodes.
- A plugin whose domain check finds no reachable domain is marked unreachable
  only by the health monitor's periodic check (every 1800 s), never by the
  search that just found it dead, so it keeps taking a slot and its timeout.

## What Changes

- **Plugins run to their own timeout.** The search deadline bounds the
  answer, not the plugins: a plugin's timeout counts from the moment it has a
  slot, a plugin waiting for a slot still runs, and results that arrive after
  the answer are merged into the cache entry.
- **The entry knows what is missing.** `CachedSearch` names the plugins that
  have not finished (`missing`); it is written at the answer budget and
  rewritten as late plugins finish. Merges replace per plugin, so no entry
  gets thinner. Entries written before this change read as complete.
- **A retry answers at once** with the entry's resolved streams, as a cached
  request does today, and resolves the uncached links in the background. The
  maintainer chose the immediate answer over waiting for the continuation.
- **Late results are resolved in the background** right after they arrive,
  so the next request answers at once with them.
- **One completion.** A request that hits an entry whose search has ended
  with plugins still missing starts one completion search for those plugins
  only, merged per plugin; the missing list is cleared afterwards whatever the
  outcome. The stale refresh merges the same way.
- **Unreachable at search time.** A plugin whose domain check finds no
  reachable domain during a search is marked unreachable immediately and
  skipped until the health monitor's recheck finds it answering.
- **Episode filter without leaks.** A series result without an episode
  number passes only through links labelled with the requested episode; one
  with an episode but no season passes only for season 1. Plugins can state
  season and episode in the result's metadata, which the filter trusts over
  the release-name guess. The plugin record counts the results the filter
  drops, and a series audit probe shows per plugin what passes, what is
  narrowed and what is dropped, before and after the change. The maintainer's
  condition: the plugins' hit rate must rise, not fall, so plugins the audit
  shows losing hits get a parser follow-up.
- **Stream response headers** `X-Cache` (`MISS`, `HIT`, `STALE`, `JOINED`)
  and `X-Search-Complete` (`true`, `false`) on the Stremio stream route, for
  the round runner and for debugging with curl.

No new configuration field: `stremio.plugin_timeout_seconds` keeps bounding
one plugin's search and becomes, explicitly, the time the answer waits for the
search; `stremio.stream_deadline_seconds` stays the answer's hard bound.

## Impact

- Affected specs: `stremio-search-continuation` (new capability).
- Affected code: `application/stremio/plugin_search.py` (no cut at the
  deadline, `late` and `unreachable` outcomes, the `dropped` count),
  `search_cache.py` and `search_progress.py` (`missing`, merge per plugin,
  age from the search start), `title_search.py` (answer budget, completion
  search, merging refresh), `resolution.py` (background resolution of late
  results, immediate retry answer), `use_cases/stremio_stream.py` (the
  answer's source and completeness), `infrastructure/plugins/health_monitor.py`
  (`mark_unreachable`), `infrastructure/plugins/httpx_base.py` and
  `playwright_base.py` (the unreachable signal), `domain/plugins/base.py`
  (the error type, metadata keys), `infrastructure/stremio/episode_filter.py`,
  `interfaces/api/stremio/router.py` (headers), `interfaces/composition.py`
  (health monitor into the runner), `scripts/probes/series_episodes.py`
  (new), docs (`stremio-addon.md`, `observability.md`, `configuration.md`,
  `AGENTS.md` §3, `CHANGELOG.md`).
- Cache format: `CachedSearch` gains a field with a default; entries from
  before the change load as complete. No migration.
- Load: a search now runs every selected plugin to its end in the background,
  bounded by the plugin timeout and the search concurrency; dead sites drop
  out of the next searches at once, which lowers the load the cut used to
  hide.
