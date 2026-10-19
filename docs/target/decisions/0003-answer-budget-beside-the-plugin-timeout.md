# 0003: The answer budget bounds the answer, not the plugins

> **Purpose:** Records why the answer budget bounds the answer, not the plugins.
> **Status:** target
> **As of:** 01a39f90

## Context

A Stremio request's plugin search was cut at the plugin timeout, counted from the request's start.
Every plugin still running then was cancelled and gave nothing, and the results of the others went into the search cache as if they were the whole answer.
The requests that followed for the same title got that partial entry until it expired.
The maintainer asked that a request shortly after a cut search continue the search instead of answering from the partial entry.
A player waits for the first answer, so the answer needs a bound; a slow site needs its plugin's whole time.

## Decision

A request must answer at its answer budget (`stremio.answer_budget_seconds` after its start) with the results known by then.
The answer budget must cut no plugin: each plugin runs to its own plugin timeout, and its later results reach the cache entry and the next request.
Unset, the answer budget is the plugin timeout, and a value above the plugin timeout counts as the plugin timeout.
An entry written before every plugin has finished must name its missing plugins, and one completion search must fill it.
A request that joins a running search after its answer budget, a retry, must answer at once with the results known then.

## Consequences

- The response tells a partial answer from a complete one (`X-Cache`, `X-Search-Complete`).
- Results that arrive after the answer budget resolve in the background, so the next request finds their streams resolved.
- A budget below the plugin timeout gives a sooner first answer with fewer streams, and the next request has the rest.
- The production deployment keeps the setting unset, the maintainer's choice: the first answer waits up to the plugin timeout.
- The plugin timeout stays the only bound on a plugin's search (see [0007](0007-plugin-timeout-of-30-s.md)).

## Alternatives

- A retry past the answer budget that waits for the continuation: the maintainer chose the immediate answer.
- A check of each plugin's site before its search: the plugin's own first request already makes it.

## Status

accepted

## References

- `src/scavengarr/application/stremio/title_search.py`: `answer_budget_s()`, the partial entries and the completion search.
- `src/scavengarr/application/stremio/plugin_search.py`: the plugin timeout per plugin.
- [`openspec/specs/stremio-search-continuation/spec.md`](../../../openspec/specs/stremio-search-continuation/spec.md): the requirements.
- [`openspec/changes/archive/2026-10-12-continue-cut-searches/design.md`](../../../openspec/changes/archive/2026-10-12-continue-cut-searches/design.md): the design.
- [`docs/plans/stremio-latency.md`](../../plans/stremio-latency.md): the answer budget's effect on the first and the cached answer.
- [`docs/archive/backlog-2026-10.md`](../../archive/backlog-2026-10.md): the maintainer's choice to keep the setting unset in production.
