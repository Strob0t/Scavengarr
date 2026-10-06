[← Back to Index](../features/README.md)

# Plan: Browser Page Budget (Who Gets a Stealth Page, and How Many There Are)

**Status:** In progress (2026-10-06). Decided by the maintainer: options 2 and 1 for the order of the pages, option 3 fully adaptive ("up and down"), bounded by the container's CPUs and RAM; a `browser_page` metric first; measured in a seventh round.
**Priority:** High (titles with few streams wait 30 s for kinoger, which no longer finishes in production)
**Related:** `src/scavengarr/infrastructure/browser/stealth_pool.py`, `infrastructure/hoster_resolvers/registry.py`, `application/stremio/plugin_search.py`, `application/stremio/resolution.py`, `application/use_cases/stremio_links.py`, [Stremio latency, sixth round](stremio-latency.md#sixth-round-2026-10-06-production-on-staging-f0b9d18)

## Problem

The stealth browser (`StealthPool`) loads at most 2 pages at a time through one FIFO semaphore (`fetch_concurrency = min(max_concurrent_playwright, 2)`). Plugin pages (kinoger binds its Cloudflare clearance to the browser, so every kinoger page goes through it), hoster captures (Filemoon, Dropload, SuperVideo, DoodStream's fallback, Vixeo) and link-outs share these pages. Since links resolve while plugins search, the sixth round (2026-10-06) found:

1. **kinoger starves.** 13 of 13 kinoger searches hit the 30 s plugin timeout; alone in the production container, with a browser of its own, the same searches took 7.9–17.3 s. Each of its page loads queues behind the captures that came first. Titles with few streams (Good Bye Lenin, Lola rennt, Dark, ...) waited the full 30 s for it.
2. **The resolve clock runs while waiting.** A resolution gets `resolve_timeout` (10 s in production) from its start, including the wait for a page. A capture that waited 9 s has 1 s left, times out, and the timeout counts for the hoster's circuit breaker: the breakers of Filemoon, Dropload and SuperVideo opened in the sixth round although the hosters were healthy.
3. **Work runs that cannot finish.** A page is opened for work whose request is cut seconds later.
4. **The page count is fixed.** 2 pages on every host; a larger host cannot use its CPUs, the Pi under the load of other containers has no brake.

## Design

Three parts with one task each: **3** sets how many pages there are, **1** who gets the next one, **2** keeps the wait out of the work's time.

### The claim (who wants a page, by when)

A domain value object `PageClaim(kind, due)` in `domain/ports/browser_fetcher.py`, with `kind` from a fixed set and `due` a `time.monotonic()` time, carried by the context variable `page_claim` next to it (plugins and resolvers sit between the caller and the browser; tasks inherit the variable). The application sets it where the work starts:

| Kind | Set by | Due |
|---|---|---|
| `play` | `StremioLinks` (resolution at `/play` and the HLS proxy) | now + 15 s |
| `plugin` | `PluginSearchRunner._run_plugin_with_timeout` | the plugin's end: now + its timeout (the search deadline) |
| `capture` | `HosterResolution` of an answer (`claim=` from `_resolve_as_results_arrive`) | the answer deadline |
| `background` | `HosterResolution` of a background run | the run's deadline |

Without a claim (Torznab searches, the scoring probes, so their measured times stay comparable) a page request counts as `plugin` (page loads) or `capture` (`capture_media`) due after the operation's own timeout, and it waits until a page is free (no `PageBusy`).

The variable lives in the domain port module rather than being injected like `search_max_results`: it is part of the browser port's contract, and three use cases set it, so injecting it would add constructor parameters and wiring for nothing else.

### Page gate (replaces the semaphore)

`PageGate` in `infrastructure/browser/page_gate.py`, plain asyncio, owned by `StealthPool` (all four page operations: `fetch_text`, `capture_media`, `resolve_redirect`, `click_through`).

- **Order (option 1, earliest deadline first):** waiters are served by `(rank, due, arrival)`: `play` rank 0, `plugin` and `capture` rank 1, `background` rank 2. A search's plugin pages (due at its 30 s) go before the captures of its answer (due at 60 s); background work gets a page only while nobody else waits. A running page is never interrupted.
- **Deadline-aware waiting (option 2):**
  - A waiter whose `due` comes closer than `_MIN_WORK_S` (3 s; Filemoon's capture, the shortest browser work, takes 1.5–2 s) gives up: `PageBusy`. A request that arrives that late gets no page either.
  - The resolve clock stops while waiting: the registry publishes its `asyncio.timeout` in the context variable `work_clock`; the gate pauses it (`reschedule(None)`) for the wait and gives it its remaining time back once the page is granted. `resolve_timeout` then measures the work only.
  - `PageBusy` from a capture reaches the registry: outcome `busy`, not cached, not counted by the breaker. The page loads of plugins (`fetch_text`, `resolve_redirect`, `click_through`) answer `None` as on any failure (the chained solver sidecar can still answer).
  - Not built: the plugin page timeout `min(30 s, remaining)`. The plugin's `wait_for` cancels its page at the search deadline anyway, and the one page that outlives it on purpose (the shielded Cloudflare solve of `HttpxPluginBase._solve_and_adopt`, which keeps the session for the next request) must keep its full time.
- **Metrics:** stages `browser_page_wait` (outcomes `ok`, `busy`, `cut`) and `browser_page` (the work while the page is held), both labelled by `kind`; traced, so a request's trace shows its queueing. A collector gauge `scavengarr_browser_pages{state="limit|in_use|waiting"}`. The start budget is logged (`browser_pages_budget`).

### Page count (option 3, fully adaptive)

`PageBudget` in `infrastructure/browser/page_budget.py`, a background task started and stopped by the composition root, changes the gate's limit every 5 s:

- **Bounds:** at least 1 page; at most `stremio.max_concurrent_playwright` (auto-tuned at startup from the container's CPU and memory limits, else the host's: `min(cpu, mem_gb / 0.15, 10)`); starts at `min(2, ceiling)`, today's value.
- **Signals** (`ResourceSampler` in `infrastructure/resource_detector.py`, which already reads the cgroup limits; the own-cgroup helpers of the telemetry collector move there): CPU busy share since the last sample, the larger of the host's (`/proc/stat`) and, with a CPU limit, the container's share of its quota (`cpu.stat`, `cpu.max`); free memory, the smaller of the host's `MemAvailable` and, with a memory limit, the cgroup's room (`memory.max` minus `memory.current` plus `inactive_file`); the longest page wait since the last sample (gate). PSI would be better but the Pi's kernel has none.
- **Up** (one page) when a page request waited at least 1 s, the CPU was at most 70 % busy, at least two pages' worth of memory (2 × 200 MB) is free, and the last change is 30 s ago (a new page's own CPU shows only after it ran).
- **Down** (one page) when free memory is below one page (at once), or the CPU was at least 90 % busy on two samples in a row and the last change is 10 s ago; also back towards the start value after 2 minutes without waits, so an old burst leaves no large limit behind.
- The band between 70 and 90 % holds the count; changes are logged (`browser_pages_changed`: from, to, reason, CPU, free memory, wait).

On the Pi (4 cores, no cgroup limits, 1.4 cores taken by other containers) the ceiling is 4 and the count will mostly stay at 1–2; the kinoger fix there comes from parts 1 and 2. Part 3 pays on larger hosts and protects playback (the Stremio server's CPU) under pressure.

### Not changed

`_BACKGROUND_RUNS = 1`; the Playwright plugins' own page limits (`pw_slots`); the capture code itself (option 5, keeping kinoger's page open, needs a probe first).

## Affected files

- Domain: `domain/ports/browser_fetcher.py` (`PageClaim`, `page_claim`), `domain/ports/telemetry.py` (stage names).
- Application: `stremio/plugin_search.py`, `stremio/resolution.py` (`claim=`), `use_cases/stremio_stream.py` (one argument), `use_cases/stremio_links.py`.
- Infrastructure: `browser/page_gate.py`, `browser/page_budget.py` (new), `browser/stealth_pool.py`, `hoster_resolvers/registry.py`, `resource_detector.py`, `telemetry/metrics.py`, `telemetry/collectors.py`.
- Interfaces: `composition.py` (gate, budget task, collector, start log).
- The stream-split plan (I14) is not affected beyond the one `claim=` argument in `_resolve_as_results_arrive`.

## Tests

- Gate: FIFO among equals, rank and due order, a page freed by a cancelled waiter, `PageBusy` before the due time, limit raised and lowered with waiters, the paused clock, metrics by kind.
- Stealth pool: page loads answer `None` on `PageBusy`, `capture_media` raises it.
- Registry: `busy` neither cached nor counted; a capture that waited longer than `resolve_timeout` still resolves.
- Claims: the plugin runner, `HosterResolution` and `StremioLinks` run their work under the right claim.
- Budget: each rule as a pure decision on given samples; the sampler on fake `/proc` and cgroup files.

## Documentation

`docs/features/stremio-addon.md`, `hoster-resolvers.md`, `observability.md`, `configuration.md` (`max_concurrent_playwright` is also the page ceiling), `docs/architecture/clean-architecture.md`, `docs/PYTHON-BEST-PRACTICES.md` 3.4, `AGENTS.md` (metrics list), `CHANGELOG.md`.

## Acceptance (seventh round, production on the Pi)

- kinoger finds results again, p50 below 20 s; titles with few streams answer below 30 s.
- No breaker of Filemoon, Dropload or SuperVideo opens from timeouts; `browser_page_wait{kind="plugin"}` p90 below 5 s.
- Answers at the target stay at a median of at most 8.9 s; CPU and RAM per title unchanged.
