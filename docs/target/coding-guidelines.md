# Coding guidelines

> **Purpose:** The rules the code follows for the event loop, shared resources, concurrency, scraping, browser pages, caching, retries and logging.
> **Status:** target
> **As of:** d8f375bc

The performance and robustness rules the code of `src/` and `plugins/` follows, beside the typing and style rules of `AGENTS.md` (section 5).

## Rule

### The event loop

- Nothing may block the event loop: every wait is an `await`, never `time.sleep()` or synchronous I/O in an `async def`.
- CPU-bound work must go to a worker thread with `asyncio.to_thread`, never `loop.run_in_executor`, whose thread loses the log context and with it `request_id`.
- Every plugin page must be parsed through `await parse_page(parser, html)` (`src/scavengarr/infrastructure/plugins/dom.py`), which parses pages from 32 KiB in a worker thread.
- A coroutine must await real I/O; a pure computation stays a plain function.

### Shared resources

- Heavy resources must be created once, in `lifespan()` of `src/scavengarr/interfaces/composition.py`: the cache, the shared `httpx.AsyncClient`, the plugin registry, the hoster resolver registry, the browser and concurrency pools and the Stremio use cases.
- Routers must only read `request.app.state` (`AppState`, `src/scavengarr/interfaces/app_state.py`) and delegate to a use case; use cases know ports, never `app.state`, a `Request` or the httpx client.
- Plugins and resolvers must use the injected client (`HttpxPluginBase.set_shared_http_client()`), never a client of their own.
- The shared client's limits are fixed in `build_http_client()`: at most 100 connections, at most 20 kept idle for 60 s, a 5 s connect timeout.
- Code must not open `diskcache.Cache` itself: the `CachePort` (`create_cache()`: diskcache or Redis) runs every diskcache call in a worker thread, bounds parallel calls and runs writes one at a time.

### Concurrency

- Independent I/O must run in parallel with `asyncio.gather`, bounded by a semaphore.
- A plugin bounds its own fetches with `self._new_semaphore()` (`_max_concurrent`, default 5).
- `ConcurrencyPool` (`src/scavengarr/infrastructure/concurrency.py`) shares the httpx and Playwright slots fairly across concurrent Stremio stream requests; Torznab searches take no slots.
- Per-domain rate limits belong to the shared client (`DomainRateLimiter`, `src/scavengarr/infrastructure/common/`), not to plugins.

### Scraping

- A plugin must deduplicate URLs within one search (a `set`, or `dict.fromkeys` to keep the order).
- A repeated search must be answered from the search caches, never from a visited-URL marker, which would drop the results of a later search.
- A plugin must bound the work, not the results: it collects up to 1000 results over the site's pages (`effective_max_results`) and fetches detail pages only for the relevant hits (`relevant_hits()` in `src/scavengarr/infrastructure/plugins/relevance.py`).
- A parser must select only the nodes it needs, normalize text once, and reuse a parsed page instead of parsing it again.
- A fetch that fails must give a partial result and a log line (`_safe_fetch()` returns `None`), never abort the search.

### Browser pages

- A browser wait must wait for a condition or a locator, never `sleep()`.
- Contexts and pages must be closed deterministically (`try`/`finally` or `async with`).
- Browser parallelism must be bounded: Playwright plugins with `_new_semaphore()`, challenge fallbacks and hoster captures through the page limit of `StealthPool` (`src/scavengarr/infrastructure/browser/stealth_pool.py`), which `PageBudget` adapts.
- Plugins and resolvers share one Chromium process (`SharedBrowserPool`, `src/scavengarr/infrastructure/browser/shared_browser.py`).

### Caching

- An expensive and stable answer must be cached with a TTL through the `CachePort` or an in-memory cache of its owner, never kept without a bound.
- Search TTLs stay short, so results follow the sites: `cache.search_ttl_seconds` for Torznab and Stremio searches, a plugin's `cache_ttl` beside it.
- `functools.lru_cache` is only for pure functions, never for one that depends on time, randomness or I/O.

### Timeouts and retries

- Every outgoing request must have a timeout.
- Retries must happen in one layer: the shared client's `RetryTransport` retries 429 and 503 (`Retry-After`, exponential backoff with jitter, capped; a 429 or 503 that Cloudflare served from its cache is not retried).
- Plugins and resolvers must not add a generic retry loop on top; site-specific retries stay the exception.
- A configuration error must fail fast with an explicit exception (`PluginLoadError` in `src/scavengarr/infrastructure/plugins/loader.py`), and invalid client input must answer 400 or 422.

### Logging and metrics

- One logging pipeline is configured at startup, and every request ends with one `http_request` line with its `request_id` and masked query values (`log_requests` in `src/scavengarr/interfaces/app.py`).
- Inner loops must count and log a summary, not a line per item.
- The core must record its steps through `TelemetryPort.stage()`, never plugins or resolvers, with label values from fixed sets ([Observability](../current/observability.md)).

### Changes

- A performance change must be measured before and after on the same titles and the same pass ([Measure performance](../how-to/performance-measurement.md)); hot paths have benchmarks in `tests/benchmark/`.
- ruff and basedpyright (`standard` mode) must pass on every commit through pre-commit, as in CI.

## Rationale

- The workload is I/O-bound: one Stremio stream request talks to 18 to 42 hosts, and a blocked event loop delays every timeout and deadline at once.
- With httpx's default 5 s keep-alive every pause between two requests closed the connections, and TLS handshakes took 21 % of the Python CPU on the production host.
- With 100 idle connections kept, httpcore's pool scan held the GIL 10 to 20 % of the time on the production host, which is why the idle pool is small.
- A retry loop on top of the retrying client turns one 503 into up to 12 requests.
- A browser page costs far more memory and CPU than an HTTP request, so its count decides what the host can serve.

## Consequences

- Components get their dependencies through constructors and factories, which keeps them testable with stand-ins (`NO_TELEMETRY`, a real `ConcurrencyPool`, `respx` for HTTP).
- A plugin that follows the base classes inherits the bounds, the shared client and the challenge handling without code of its own.
- A measurable change carries its numbers before and after in the plan of the change.

## Exceptions

- Site-specific retries in a plugin or resolver, where a site's own behaviour needs them, with the reason in the code.
- Torznab searches take no slots of the `ConcurrencyPool`: they run outside the Stremio requests' fairness.
- Scripts and probes under `scripts/` run synchronous code where it is simpler; they are not served by the event loop.

## References

- `AGENTS.md`, sections 3 and 5: the invariants and the Python rules.
- [Architecture rules](architecture.md): the layers and the ports.
- [Observability](../current/observability.md), [Measure performance](../how-to/performance-measurement.md).
