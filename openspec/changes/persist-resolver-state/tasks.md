## 1. Exports and imports (tests first)

- [ ] 1.1 `PluginCircuitBreaker.export_state()` → open names with `remaining_cooldown` and `cooldown`; `import_state(entries, age_s)` restores them as open with the remaining cooldown (dropping entries at or below zero); tests: open breaker round-trips, cooldown shortened by the age, closed breakers absent, half-open probe runs after the restored cooldown.
- [ ] 1.2 `HosterResolverRegistry.export_state()` → unexpired results (as dicts: value fields, `remaining`, `resolver`) and redirects; `import_state(state, age_s)`; a `dirty` flag set by `_cache_result` and the redirect cache, cleared by the export; tests: alive and dead entries round-trip with shortened lifetimes, expired ones are dropped, `cached(url)` answers from a restored entry, the cap still holds after an import.

## 2. Store

- [ ] 2.1 `infrastructure/hoster_resolvers/state_store.py`: `HosterStateStore(cache, registry, breakers, interval_s=30)` with `restore()`, `start()`, `aclose()`; snapshot format `{version, taken_at, results, redirects, breakers: {kind: [...]}}`; `hoster_state_restored` (counts and age) and `hoster_state_discarded` (reason) log events; tests with an in-memory fake `CachePort` (`AsyncMock`): restore after a simulated restart, version mismatch discarded and deleted, no write while nothing changed, final write on `aclose()`, negative age treated as zero.
- [ ] 2.2 Benchmark note (not a test): export and pickle of 10,000 entries on x86 in `docs/plans/round5-measures.md`-style one line in the CHANGELOG entry (target: under 100 ms).

## 3. Wiring

- [ ] 3.1 `composition.py`: create the store when a cache backend exists; `restore()` after the cache and the registry exist and before the app reports ready; `start()`; `aclose()` in the shutdown sequence before the cache closes (see `graceful_shutdown.py` ordering); e2e test: a stream request, a simulated restart of the composition with the same fake backend, the cached answer uses the restored resolution.
- [ ] 3.2 `/api/v1/stats/metrics` (JSON) reports `hoster_state: {restored_at, resolutions, breakers}` for a quick check with `prodctl.py state`.

## 4. Docs

- [ ] 4.1 `docs/features/hoster-resolvers.md` (resolution cache: what persists, the snapshot key, how to reset), the circuit breaker section of `docs/features/stremio-addon.md`, `docs/features/observability.md` (the two events), `CHANGELOG.md`.
- [ ] 4.2 Production check after deploy: `prodctl.py logs --grep hoster_state_restored` after a restart shows non-zero counts; record in `docs/plans/ideas-backlog.md` (I10 row).
