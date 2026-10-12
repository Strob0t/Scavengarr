# Design: Continue cut searches

## Context

Today (`application/stremio/`): `title_search.py` spawns one search task per
cache key and keeps it in a running-search map until it ends; the task runs
`PluginSearchRunner` (`plugin_search.py`), which gives each plugin
`min(plugin_timeout, deadline - now)` and cancels it at the search deadline
(`_run_plugin_with_timeout`, `asyncio.wait_for`); a plugin queued for a slot
is skipped once it gets one past the deadline. Results flow into a shared
`SearchProgress` as each plugin returns; the entry (`CachedSearch`: results,
total, stored_at) is written once at the search's end. A request answers at
`resolve_target_count` videos, at search end, or at
`stream_deadline_seconds`; a second request finds the entry ("cache",
"stale") or the running search ("joined"). A cached request answers at once
from the resolver registry's cached outcomes and resolves the uncached links
in the background; a fresh search resolves nothing in the background. The
episode filter runs per plugin batch inside the runner; the health monitor
marks a plugin unreachable from its periodic HEAD checks only.

## Goals / Non-Goals

Goals: a cut never loses a plugin's work; a partial entry is visibly partial
and completes itself; a retry answers at once with everything resolved so
far; dead sites leave the next search immediately; no wrong episodes; the
plugins' series hit rate is measured and does not fall.

Non-goals: waiting for the continuation in the retry (the maintainer chose
the immediate answer); a new configuration field; changes to the stale
refresh's trigger (age at the TTL); changes to resolver caching lifetimes;
parser fixes themselves (they follow from the audit as their own steps).

## Decisions

1. **The deadline bounds the answer, not the plugins.** The runner gives each
   plugin its full `plugin_timeout_seconds` from the moment it holds a slot,
   and a plugin waiting for a slot runs when it gets one. The search task
   ends when every plugin has ended. The request's wait for the search ends
   at `plugin_timeout_seconds` after the request start (the same number as
   today's cut, now the *answer budget*), at the resolve target, or at the
   stream deadline. Alternative rejected: a separate "search budget" setting;
   one number already has this meaning for every reader of the logs.
   Worst case for one search: the selected plugins divided by the search
   concurrency, times the plugin timeout; the unreachable marking of decision
   5 keeps dead sites out of that product.

2. **Partial entries and per-plugin merge.** `CachedSearch` gains
   `missing: tuple[str, ...] = ()` (plugin names not finished at the time of
   the write; a cancelled, failed or timed-out plugin stays missing) and the
   results keep their `source_plugin` metadata. `SearchProgress` writes the
   entry when the answer budget expires with results so far and `missing`,
   and again whenever a late plugin finishes: that plugin's results replace
   its results in the entry, `missing` loses its name. The same rule serves
   the completion search and the stale refresh: a plugin's finished run
   replaces that plugin's results, an unfinished one keeps the old results;
   an entry therefore never gets thinner (the comment at
   `title_search.py:38-42` records that defect). `stored_at` is the search's
   start, so the entry's age and the stale threshold do not move with late
   writes. Entries written before the change have no `missing` and read as
   complete. The write condition "at least one matching result" stays for the
   first write.

3. **Retry answers at once.** A request that finds a running search whose
   answer budget has passed, or an entry (complete or partial), answers from
   the results known so far with the resolver registry's cached outcomes,
   exactly as a cached request does today, and spawns the background
   resolution of the uncached links. "joined" keeps its meaning for a request
   that arrives within the first request's answer budget: it shares the wait.
   The `stremio_stream_response` log and the `X-Search-Complete` header say
   whether the answer was complete.

4. **Late results are resolved in the background.** When a plugin finishes
   after the answer budget, the search task hands its title-filtered results
   to the background resolution (the one that exists for cached requests:
   one per key, one process-wide, under the stream deadline counted from the
   hand-over) so their best link per hoster has a cached outcome before the
   next request. Completion and refresh results take the same path.

5. **Unreachable at search time.** `HttpxPluginBase._verify_domain` (and the
   Playwright base's equivalent) raises `PluginUnreachableError`
   (`domain/plugins/base.py`) instead of falling back to the primary domain
   when no domain answers. The runner maps it: `PluginHealthMonitor.
   mark_unreachable(name)` (new, the same set the periodic check maintains,
   so the recheck every `health_recheck_seconds` clears it), the plugin
   record's `unreachable` counter, telemetry outcome `unreachable`. The
   plugin's own log event `<name>_no_domain_reachable` stays. Alternative
   rejected: a search-time HEAD check before every plugin; the plugin already
   makes that request.

6. **Episode filter.** For a series request (`episode` given): a result whose
   release name gives no episode passes only when `_narrow_links` keeps at
   least one link labelled with the requested episode; a result with no
   labelled link at all is dropped (today it passes with every link). A
   result with an episode but no season passes only when the requested
   season is 1 or the result's metadata names the season. Plugins may set
   `metadata["season"]` and `metadata["episode"]` (ints) on a `SearchResult`;
   the filter reads them before the release-name guess, so a plugin that
   knows the episode from the page keeps its hits without a release name
   that guessit can read. The runner counts the results the filter drops per
   plugin in the plugin record (`dropped`) and the `stremio_search_complete`
   log carries the dropped count.

7. **The hit-rate condition.** `scripts/probes/series_episodes.py` runs the
   plugin searches of a dev server (or the plugins directly through the
   registry) for the series ids of `docs/plans/round-titles.txt` plus
   Severance S01E05 and S02E05, and prints per plugin: results, with season
   and episode, with episode only, without either, kept, narrowed, dropped,
   and the kept results whose labels name another episode (leaks). It runs
   before the filter change (baseline) and after it; both tables go to
   `docs/plans/series-episodes.md`. Acceptance: leaks are zero after, and no
   plugin's kept count with the right episode is lower than before. A plugin
   whose dropped count is not zero after the change gets a parser follow-up
   (metadata season and episode from its page), one step per plugin.

8. **Headers.** The stream route sets `X-Cache` from the search source
   (`search` → `MISS`, `cache` → `HIT`, `stale` → `STALE`, `joined` →
   `JOINED`) and `X-Search-Complete` (`true` when `missing` is empty and no
   continuation runs). Fixed value sets, no titles.

## Risks / Trade-offs

- More background work per search: bounded by the plugin timeout and the
  search concurrency, and smaller in practice once dead sites are marked at
  search time. `StremioStreamUseCase.aclose()` still cancels every spawned
  search at shutdown; the entry written at the answer budget survives with
  its `missing` list, and the next request completes it once.
- A stricter episode filter can drop real hits from plugins whose titles
  carry no episode and whose links carry no labels. The audit names them
  before the change ships, and the metadata keys give them a cheap fix.
- The completion search runs once per entry; a site that is down for an
  hour is tried twice (continuation and completion), then rests until the
  stale refresh.

## Migration

None. Old cache entries load as complete; the new field has a default.
Production check after the deploy: `prodctl.py logs --grep
stremio_search_complete` shows `missing=[]` for a search that ran past its
budget, and the digest's Plugins section shows `dropped` and `unreachable`
counts.
