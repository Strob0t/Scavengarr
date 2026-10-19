# 0014: The resolver registry's state outlives a restart as one snapshot

> **Purpose:** Records why the resolver registry's state outlives a restart as one snapshot.
> **Status:** target
> **As of:** 5b4303e4

## Context

A restart forgot every resolution, every redirect and every open circuit breaker of the hoster resolver registry.
The first requests after a deploy resolved all their links again and waited for hosters known to be down.
The cache backend (`CachePort`, diskcache or Redis) gets, sets and deletes a key, but cannot list keys.
The options were one key per entry, written through on every change, or one snapshot of the whole state.

## Decision

`HosterStateStore` must keep one snapshot of the registry's state in the cache backend (`hoster_state:v1`): the resolution outcomes, the redirects, each resolver's last look-up by a request and the open circuit breakers.
The snapshot must be restored before the app reports ready, with every lifetime shortened by the downtime.
Lifetimes travel as remaining seconds, so an entry whose time ran out during the downtime does not come back.
The store owns the cadence: a write every 30 s when something changed, and a last write at shutdown.
A snapshot of another format version must be discarded and deleted.

## Consequences

- The request path keeps reading the registry's memory, and the snapshot never stands in a request's way.
- Only open breakers are kept: a closed breaker is the default, and failure counts below the threshold would open a healthy hoster sooner.
- An open breaker comes back open with its remaining cooldown, so its half-open probe stays on schedule.
- A breaker opened before a deploy that fixed its resolver stays open until its probe.
- The snapshot's age never counts below zero, so a wrong wall clock can expire entries but never extend them.
- A stop that skips the shutdown (SIGKILL) leaves the last periodic snapshot, which misses at most the last 30 s of changes.

## Status

accepted

## References

- `src/scavengarr/infrastructure/hoster_resolvers/state_store.py`: the store and its cadence.
- `src/scavengarr/infrastructure/hoster_resolvers/registry.py`: `export_state()` and `import_state()`.
- [`openspec/specs/hoster-resolution-persistence/spec.md`](../../../openspec/specs/hoster-resolution-persistence/spec.md): the requirements.
- [`openspec/changes/archive/2026-10-12-persist-resolver-state/design.md`](../../../openspec/changes/archive/2026-10-12-persist-resolver-state/design.md): the design and its alternatives.
