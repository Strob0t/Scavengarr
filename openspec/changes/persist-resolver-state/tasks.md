## 1. Exports and imports (tests first)

- [x] 1.1 `PluginCircuitBreaker.export_state()` → open names with `remaining_cooldown` and `cooldown`; `import_state(entries, age_s)` restores them as open with the remaining cooldown (dropping entries at or below zero); tests: open breaker round-trips, cooldown shortened by the age, closed breakers absent, half-open probe runs after the restored cooldown.
  Done with one change: an entry whose cooldown ran out during the downtime comes back open with its probe due instead of being dropped (a dropped one is closed: every call goes through, five failures open it again at 60 s, and the doubled cooldown is lost); a half-open breaker exports no cooldown left (its probe ends with the process); a restored cooldown keeps to the running maximum, and a restored breaker reports the threshold as its failures.
- [x] 1.2 `HosterResolverRegistry.export_state()` → unexpired results (as dicts: value fields, `remaining`, `resolver`) and redirects; `import_state(state, age_s)`; a `dirty` flag set by `_cache_result` and the redirect cache, cleared by the export; tests: alive and dead entries round-trip with shortened lifetimes, expired ones are dropped, `cached(url)` answers from a restored entry, the cap still holds after an import.
  Done with a change counter (`changes`) instead of a flag the export clears, on the registry and the breakers: the store records the counters only after a successful write, so a failed write is tried again with the next one. The streams are stored with `StreamQuality` as its number and rebuilt from the fields this version knows.

## 2. Store

- [x] 2.1 `infrastructure/hoster_resolvers/state_store.py`: `HosterStateStore(cache, registry, breakers, interval_s=30)` with `restore()`, `start()`, `aclose()`; snapshot format `{version, taken_at, results, redirects, breakers: {kind: [...]}}`; `hoster_state_restored` (counts and age) and `hoster_state_discarded` (reason) log events; tests with an in-memory fake `CachePort` (`AsyncMock`): restore after a simulated restart, version mismatch discarded and deleted, no write while nothing changed, final write on `aclose()`, negative age treated as zero.
- [x] 2.2 Benchmark note (not a test): export and pickle of 10,000 entries on x86 in `docs/plans/round5-measures.md`-style one line in the CHANGELOG entry (target: under 100 ms).
  Measured 2026-10-06 (10,000 resolutions, 2.9 MiB): export 10 ms, pickling 9 ms; unpickling 10 ms, import 29 ms. `asdict` alone took 39 ms, so the export reads the fields directly.

## 3. Wiring

- [x] 3.1 `composition.py`: create the store when a cache backend exists; `restore()` after the cache and the registry exist and before the app reports ready; `start()`; `aclose()` in the shutdown sequence before the cache closes (see `graceful_shutdown.py` ordering); e2e test: a stream request, a simulated restart of the composition with the same fake backend, the cached answer uses the restored resolution.
  A backend always exists (diskcache or Redis), so the store is always wired; a registry without a store (tests, the CLI) behaves as before. E2E: `tests/e2e/test_hoster_state_restart_e2e.py`.
- [x] 3.2 `/api/v1/stats/metrics` (JSON) reports `hoster_state: {restored_at, resolutions, breakers}` for a quick check with `prodctl.py state`.

## 4. Docs

- [x] 4.1 `docs/features/hoster-resolvers.md` (resolution cache: what persists, the snapshot key, how to reset), the circuit breaker section of `docs/features/stremio-addon.md`, `docs/features/observability.md` (the two events), `CHANGELOG.md`.
- [ ] 4.2 Production check after deploy: `prodctl.py logs --grep hoster_state_restored` after a restart shows non-zero counts; record in `docs/plans/ideas-backlog.md` (I10 row).
