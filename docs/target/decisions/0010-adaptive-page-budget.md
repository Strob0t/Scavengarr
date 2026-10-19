# 0010: Browser pages go by their work's claim, and their count adapts

> **Purpose:** Records why browser pages go by their work's claim, and their count adapts.
> **Status:** target
> **As of:** 5b4303e4

## Context

Plugin pages behind Cloudflare, hoster captures and link-outs share one headful stealth browser, and a page costs much memory and CPU.
A fixed limit of two pages served the page requests first come, first served, so a plugin's pages queued behind captures and ran into the plugin timeout.
The resolve bound ran while a capture waited for a page, so a capture that waited timed out and counted against a healthy hoster's circuit breaker.
Pages opened for work whose request was cut moments later, and the fixed count left a larger host's CPUs idle and gave a loaded host no brake.
The maintainer chose the page order by the work's due time, waiting that respects it, and a fully adaptive count, up and down, bounded by the container's CPUs and memory.

## Decision

Every page request must carry its work's claim (`PageClaim`: its kind and when it is due).
The page gate serves playback first, then the earliest due work, and background work only while nobody else waits.
A running page must never be taken away.
Work that cannot start a few seconds before it is due must get no page (`PageBusy`), and a resolution's timeout must stop while it waits for a page.
The number of pages (`PageBudget`) starts at two, or at the ceiling when that is lower, grows by one when a page request waited and the machine has room, and shrinks by one under memory or CPU pressure.
The count stays between one page and `stremio.max_concurrent_playwright`, which is auto-tuned from the container's CPUs and memory.

## Consequences

- A capture's wait for a page no longer counts for its hoster's breaker: `PageBusy` is its own outcome, neither cached nor counted.
- A search's plugin pages, due at its plugin timeout, go before the captures of its answer.
- The count falls back to its start value after two quiet minutes, so an old burst leaves no large limit behind.
- Background work, the pass keeper's capture among it, waits while requests need the pages.
- The waits for a page and the work on it are recorded per kind (`browser_page_wait`, `browser_page`).

## Status

accepted

## References

- `src/scavengarr/infrastructure/browser/page_gate.py`: `PageGate` and its order.
- `src/scavengarr/infrastructure/browser/page_budget.py`: `PageBudget` and its thresholds.
- `src/scavengarr/domain/ports/browser_fetcher.py`: `PageClaim` and `page_claim`.
- [`docs/archive/plans/browser-page-budget.md`](../../archive/plans/browser-page-budget.md): the problem, the options and the maintainer's choice.
