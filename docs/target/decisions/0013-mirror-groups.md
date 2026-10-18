# 0013: Mirror sites form a group, and a Stremio request asks one of them

> **Status:** target
> **As of:** 5b4303e4

## Context

hdfilme, streamcloud and streamkiste turned out to be one database in three themes: the same ids, titles, order and links.
Asking all three in a Stremio request triples the work for the same streams, while each one stays a useful Torznab indexer of its own.

## Decision

Sites that front one database must share a `mirror_group`, and they stay separate plugins, each one its own Torznab indexer.
A Stremio request must ask one member of the group: the reachable one with a closed breaker and the best plugin score, without scores the first one.
The next member is the standby and must take a query the asked member gives nothing for.

## Consequences

- A member that gave nothing while its standby delivered ranks behind the others.
- With no member's breaker closed, all members stay in and their breakers decide, so a half-open probe can bring one back.
- The parsers stay per theme, so a theme change breaks one plugin, and the real-page tests catch it.
- A Torznab search asks every member, since each one is a separate indexer.

## Status

accepted

## References

- `src/scavengarr/application/stremio/plugin_search.py`: `_one_per_mirror_group()`.
- `src/scavengarr/interfaces/composition.py`: the groups, read from the plugins' `mirror_group`.
- [`docs/current/plugins.md`](../../current/plugins.md#plugin-selection-for-a-stremio-request): the mirror groups as a Stremio request applies them.
- [`docs/archive/plans/next-steps.md`](../../archive/plans/next-steps.md): the evidence of one database and the decision.
