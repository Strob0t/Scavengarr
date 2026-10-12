## Context

`HosterResolverRegistry` holds `_result_cache` and `_redirect_cache` (`dict[str, _CacheEntry]`, `expires_at` as a monotonic deadline, 1 h alive, 15 min dead, 1 h redirects, 10,000 entries) and the use case reads them synchronously (`registry.cached(url)`) on the cached-answer fast path. `PluginCircuitBreaker` (one instance for plugins per category, one for hosters) keeps `_states`, `_failures`, `_opened_at` (monotonic) and the per-name cooldown; `snapshot()` is diagnostics only. `CachePort` offers `get`/`set`/`delete`/`exists`/`clear`, pickles values, and has no key scan. The Redis adapter returns `None` on unpickle errors.

## Goals / Non-Goals

- Goals: the state survives restarts without touching the request path; expired entries never come back; a snapshot from another code version is ignored; the feature is inert without a cache backend.
- Non-Goals: sharing state between instances (single process); persisting failure counts below the threshold or closed breakers; changing TTLs or thresholds; a key scan in `CachePort`.

## Decisions

- **Decision: one snapshot key, not write-through per entry.** `CachePort` has no scan, so per-entry keys would need an index or a new port method in both adapters; the snapshot needs neither, costs one write per 30 s when dirty, and keeps the synchronous in-memory read path. Alternative considered: write-through under `hoster:v1:<digest>` with a `keys(prefix)` port method — more code, a write per resolution, and a startup scan.
- **Decision: lifetimes travel as remaining seconds.** `export_state()` converts `expires_at - monotonic()` to `remaining`; `import_state()` subtracts the snapshot's age (`time.time() - taken_at`) and drops what is left at or below zero. The breakers export `remaining_cooldown` and `cooldown` (the doubled length) the same way; a breaker whose cooldown ran out during the downtime comes back open with its probe due, not closed (implementation, 2026-10-06: closing it lost the doubled cooldown and let every call through).
- **Decision: versioned pickle.** The snapshot is a dict with `version` (an integer constant in `state_store.py`), `taken_at`, `results`, `redirects`, `breakers`; `ResolvedStream` values are stored as `dataclasses.asdict` dicts and rebuilt, so a field added later gets its default instead of an unpickle error. A different `version` or a malformed snapshot is logged (`hoster_state_discarded`) and deleted.
- **Decision: the store owns the cadence.** `HosterStateStore.start()` spawns the periodic task (30 s; writes only when the registry or a breaker reported a change: change counters, which the store records after a successful write, so a failed write is tried again), `aclose()` cancels it and writes a last snapshot; composition calls `restore()` before the first request and `aclose()` in the shutdown sequence before the cache backend closes. Alternative considered: writing from the registry on every change — couples the registry to the backend and multiplies writes.
- **Decision: open breakers only.** Restoring an open breaker with its remaining cooldown keeps the half-open probe schedule; a closed breaker is the default and needs no record; failure counts below the threshold are noise that would make a healthy hoster open sooner after a restart.

## Risks / Trade-offs

- A breaker opened before a deploy that fixed its resolver stays open until the probe (60 s to 1 h) → accepted; the probe reopens it, and `scavengarr_circuit_breaker_open` shows it.
- A stale "dead" entry blocks a link for up to 15 min after a restart that would have healed it today → accepted (same as without a restart).
- Snapshot size at the cap (10,000 entries) → about 3 MB per write; the dirty flag and the 30 s cadence bound it; diskcache on the Pi's card writes it in well under a second.
- Clock jumps on the Pi (no RTC; NTP corrects the time after boot) → an age computed from a wrong wall clock can expire everything or nothing; expire at most to zero and never extend (negative age counts as zero).

## Migration Plan

Deploy; the first start finds no snapshot and logs `hoster_state_restored` with zeros. Rollback: the previous code ignores the key. Deleting `hoster_state:v1` from the backend resets the state.

## Open Questions

- Whether the plugin breaker keys (`<plugin>:<category>`) should be restored at all, or only the hoster breaker: both are cheap; the plan restores both and the review decides from a week of logs.
