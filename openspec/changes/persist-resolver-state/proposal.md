# Change: Persist Hoster Resolutions and Circuit Breakers

## Why

The hoster resolver registry keeps what it learned in process memory: the outcome per hoster URL (a video URL for an hour, "dead" for 15 minutes, at most 10,000 entries), the redirect map, and the circuit breakers of hosters and plugins (open after five failures, closed again after a cooldown of 60 s that doubles up to an hour). A restart forgets all of it. A cached search answer goes out at once only when one of its hoster URLs is in that memory (`StremioStreamUseCase._resolve`); after a restart such titles wait for the resolve grace once more, and every dead hoster (Dropload, Filemoon, SuperVideo and Vixeo were open in production on 2026-10-06) costs five browser captures of 5–8 s each before its breaker opens again. The search cache and the stream links already live in the cache backend (Redis in production, diskcache otherwise); this change gives the resolver state the same home. With the container image pipeline (`add-container-image`) deploys become automatic, so restarts get more frequent.

Honest limit: the entries live an hour at most, so the persisted resolutions help restarts during active use (test rounds, deploys while watching), not the next evening's first request. The breakers are the part with daily value. The maintainer chose both (variant B, `docs/plans/ideas-backlog.md`).

## What Changes

- **One state snapshot** in the cache backend (`hoster_state:v1`): the registry's unexpired results and redirects (each with its remaining lifetime), and the open breakers of hosters and plugins (state, remaining cooldown, current cooldown length), with the wall-clock time the snapshot was taken. Written when the state changed, at most every 30 s, and once more at graceful shutdown; read once at startup.
- **Startup restore**: entries get their lifetime back minus the downtime; expired ones and snapshots of another version are dropped. Open breakers are restored as open with the remaining cooldown and the same cooldown length, so the half-open probes continue where they were. The log has `hoster_state_restored` with the counts (resolutions, redirects, breakers) and the snapshot's age.
- **No behaviour change** while the process runs: same TTLs, thresholds and probes; the in-memory structures stay the only read path. Without a cache backend the registry behaves as today.
- The `_CacheEntry` and breaker clocks stay monotonic; only the snapshot converts to remaining seconds (monotonic deadlines do not survive a restart).

## Impact

- Affected specs: new capability `hoster-resolution-persistence`.
- Affected code:
  - `src/scavengarr/infrastructure/hoster_resolvers/registry.py`: `export_state()` (results, redirects with remaining lifetimes) and `import_state()`; a dirty flag set by `_cache_result` and the redirect cache.
  - `src/scavengarr/infrastructure/circuit_breaker.py`: `export_state()` (open names with remaining cooldown and cooldown length) and `import_state()`, beside the existing `snapshot()` diagnostics.
  - `src/scavengarr/infrastructure/hoster_resolvers/state_store.py` (new): `HosterStateStore` that gathers the exports into one versioned, pickled snapshot, writes it through `CachePort.set` (30 s cadence when dirty, and on `aclose()`), and restores at startup; it owns the periodic task (registered with the task registry so it ends at shutdown).
  - `src/scavengarr/interfaces/composition.py`: create the store with the cache backend, the registry and both breakers; restore after the cache is ready; close it in the shutdown sequence before the cache closes.
  - `docs/features/hoster-resolvers.md` (resolution cache section), the circuit breaker section of `docs/features/stremio-addon.md`, `docs/features/observability.md` (the new log event), `CHANGELOG.md`.
- Dependencies: none new.
- Cost: one pickled write of the state at most every 30 s when it changed (typically tens of KB, 3 MB at the 10,000-entry cap), one read at startup; nothing on the request path.
