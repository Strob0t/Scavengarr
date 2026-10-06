[← Back to Index](./features/README.md)

# Python Performance Best Practices

> Performance rules for high-throughput async Python backends and scrapers (FastAPI, httpx, diskcache, structlog, Playwright).

**Note:** This document is mainly for agents and LLMs to follow when maintaining, generating, or refactoring Python codebases with async I/O (FastAPI, httpx, scraping pipelines). Humans may also find it useful, but guidance here is optimized for automation and consistency by AI-assisted workflows. Notes marked **Scavengarr** describe how the project actually implements a rule; the code is the source of truth. Where a generic example differs from `AGENTS.md` or a Scavengarr note, `AGENTS.md` and the note apply. The typing and coding rules are in `AGENTS.md` section 5; the examples follow them (`from __future__ import annotations`, `collections.abc`).

---

## Abstract

This guide collects performance best practices for Python services that are primarily **I/O-bound**: HTTP APIs, web scrapers, and multi-stage crawling pipelines. The rules are grouped by **impact** (CRITICAL → HIGH → MEDIUM → LOW) and focus on:

- Keeping the **async event loop non-blocking**
- Sharing and reusing **HTTP clients and connection pools** (`httpx.AsyncClient`)
- Designing FastAPI apps with efficient **lifespan and dependency wiring**
- Using **diskcache** and in-memory caching to avoid redundant network calls
- Structuring scraping engines for **bounded concurrency**, **backoff**, and **short-circuiting**
- Applying Python-level micro-optimizations only where they matter

Each rule includes:

- A clear **intent** and **impact level**
- One or more **Incorrect** vs **Correct** examples
- Concrete hints for stacks similar to **Scavengarr** (FastAPI + httpx + diskcache + structlog, optional Redis)

Use this as a checklist when creating or modifying code. For non-trivial changes, prefer measuring with a profiler before and after the refactor to confirm impact. [blog.poespas](https://blog.poespas.me/posts/2024/04/27-optimizing-python-asyncio-for-high-performance/)

---

## Table of Contents

1. [Eliminating I/O Bottlenecks (Async & HTTP)](#1-eliminating-io-bottlenecks-async--http) — **CRITICAL**
   - 1.1 [Keep the Event Loop Non-Blocking](#11-keep-the-event-loop-non-blocking)
   - 1.2 [Use Shared Async HTTP Clients](#12-use-shared-async-http-clients)
   - 1.3 [Use asyncio.gather With Concurrency Limits](#13-use-asynciogather-with-concurrency-limits)
   - 1.4 [Avoid Per-Call DNS/Connection Overheads](#14-avoid-per-call-dnsconnection-overheads)
2. [FastAPI Application Performance](#2-fastapi-application-performance) — **CRITICAL**
   - 2.1 [Initialize Heavy Resources in Lifespan, Not Per Request](#21-initialize-heavy-resources-in-lifespan-not-per-request)
   - 2.2 [Keep Endpoints Thin and Delegate to Use Cases](#22-keep-endpoints-thin-and-delegate-to-use-cases)
   - 2.3 [Return Lightweight Responses](#23-return-lightweight-responses)
3. [Scraping & Multi-Stage Pipelines](#3-scraping--multi-stage-pipelines) — **HIGH**
   - 3.1 [Deduplicate URLs and Short-Circuit Early](#31-deduplicate-urls-and-short-circuit-early)
   - 3.2 [Use Bounded Parallelism per Target Site](#32-use-bounded-parallelism-per-target-site)
   - 3.3 [Parse Once, Off the Event Loop](#33-parse-once-off-the-event-loop)
   - 3.4 [Bound and Close Browser Pages](#34-bound-and-close-browser-pages)
4. [Caching Strategies (diskcache & In-Memory)](#4-caching-strategies-diskcache--in-memory) — **HIGH**
   - 4.1 [Cache Expensive but Stable Responses](#41-cache-expensive-but-stable-responses)
   - 4.2 [Use diskcache for Cross-Process and Long-Lived Caches](#42-use-diskcache-for-cross-process-and-long-lived-caches)
   - 4.3 [Use LRU Caching for Pure Functions](#43-use-lru-caching-for-pure-functions)
5. [Async Design Patterns & Error Handling](#5-async-design-patterns--error-handling) — **MEDIUM**
   - 5.1 [Design Coroutines to be Truly Asynchronous](#51-design-coroutines-to-be-truly-asynchronous)
   - 5.2 [Apply Timeouts and Retries With Backoff](#52-apply-timeouts-and-retries-with-backoff)
   - 5.3 [Fail Fast on Irrecoverable Errors](#53-fail-fast-on-irrecoverable-errors)
6. [Logging, Metrics, and Observability](#6-logging-metrics-and-observability) — **MEDIUM**
   - 6.1 [Use Structured Logging With Sampling](#61-use-structured-logging-with-sampling)
   - 6.2 [Log at the Edges, Not in Tight Loops](#62-log-at-the-edges-not-in-tight-loops)
7. [Profiling and Code Quality](#7-profiling-and-code-quality) — **MEDIUM**
   - 7.1 [Profile Before Micro-Optimizing](#71-profile-before-micro-optimizing)
   - 7.2 [Automate Style and Type Checks](#72-automate-style-and-type-checks)
8. [Python Micro-Optimizations](#8-python-micro-optimizations) — **LOW**
   - 8.1 [Use Appropriate Data Structures](#81-use-appropriate-data-structures)
   - 8.2 [Prefer Comprehensions and Built-ins](#82-prefer-comprehensions-and-built-ins)

---

## 1. Eliminating I/O Bottlenecks (Async & HTTP)

### 1.1 Keep the Event Loop Non-Blocking

**Impact: CRITICAL (enables true concurrency for I/O-bound workloads)**

Any **blocking** operation inside an `async def` will block the entire event loop, reducing throughput and increasing tail latency. [discuss.python](https://discuss.python.org/t/asyncio-best-practices/12576)

**Incorrect: blocking inside an async endpoint**

```python
import time
from fastapi import APIRouter

router = APIRouter()

@router.get("/items")
async def list_items():
    # Blocks the entire event loop for 2s
    time.sleep(2)
    return {"items": [1, 2, 3]}
```

**Correct: use `asyncio.sleep` or `to_thread`**

```python
import asyncio
from fastapi import APIRouter

router = APIRouter()

@router.get("/items")
async def list_items():
    await asyncio.sleep(2)  # non-blocking delay
    return {"items": [1, 2, 3]}
```

For **CPU-bound** work, offload to a worker thread:

```python
import asyncio

def compute_something_heavy(x: int) -> int:
    # CPU-heavy logic here
    return x * x

async def compute_endpoint(x: int) -> dict[str, int]:
    result = await asyncio.to_thread(compute_something_heavy, x)
    return {"result": result}
```

> **Scavengarr hint**
> Never use `loop.run_in_executor`: its worker thread loses the log context. `asyncio.to_thread` copies the context variables, so the thread's log lines keep structlog's `request_id`. Plugin parsers parse every page through `await parse_page(parser, html)` (`src/scavengarr/infrastructure/plugins/dom.py`), which parses pages from 32 KiB in a worker thread; that is a fixed rule, not a profiling decision.

---

### 1.2 Use Shared Async HTTP Clients

**Impact: CRITICAL (avoids repeated DNS lookups, TLS handshakes, and connection setup)**

Creating a new `httpx.AsyncClient` for every request is expensive. The recommended pattern is a **single shared client** per service, created in FastAPI’s lifespan and reused across all requests. [kisspeter.github](https://kisspeter.github.io/fastapi-performance-optimization/)

**Incorrect: new client per call**

```python
import httpx

async def fetch_page(url: str) -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text
```

**Correct: shared client with connection pooling**

```python
# app factory and lifespan — generic example
# (Scavengarr: src/scavengarr/interfaces/app.py, src/scavengarr/interfaces/composition.py)
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

import httpx
from fastapi import FastAPI
from starlette.datastructures import State

class AppState(State):
    http_client: httpx.AsyncClient

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    state = cast(AppState, app.state)  # create_app() set it
    state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0),
        follow_redirects=True,
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        headers={"User-Agent": "MyService/1.0"},
    )
    try:
        yield
    finally:
        await state.http_client.aclose()

def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.state = AppState()
    return app
```

```python
# usage in endpoints
from __future__ import annotations

from typing import cast

from fastapi import Request

async def fetch_page(request: Request, url: str) -> str:
    client = cast(AppState, request.app.state).http_client
    resp = await client.get(url)
    resp.raise_for_status()
    return resp.text
```

> **Scavengarr hint**
> Ensure all scraping engines and reachability checks use the **injected** `httpx.AsyncClient` from your `AppState`, never create a new client inside scraping functions. Scavengarr: `AppState` extends Starlette `State` (`src/scavengarr/interfaces/app_state.py`) and is attached in `create_app()` (`src/scavengarr/interfaces/app.py`); `lifespan()` creates the shared client and injects it into httpx plugins via `HttpxPluginBase.set_shared_http_client()`.

---

### 1.3 Use `asyncio.gather` With Concurrency Limits

**Impact: CRITICAL (maximizes parallelism while respecting target limits)**

Running many independent I/O operations sequentially wastes time; running **too many** concurrently can overwhelm the remote service or your own resources. [pythonprograming](https://pythonprograming.com/blog/using-pythons-asyncio-for-concurrency-best-practices-and-real-world-applications)

**Incorrect: fully sequential scraping**

```python
async def fetch_many(urls: list[str], client: httpx.AsyncClient) -> list[str]:
    results = []
    for url in urls:
        resp = await client.get(url)
        resp.raise_for_status()
        results.append(resp.text)
    return results
```

**Correct: bounded parallelism with a semaphore**

```python
import asyncio

import httpx
import structlog

log = structlog.get_logger(__name__)

async def fetch_one(url: str, client: httpx.AsyncClient, sem: asyncio.Semaphore) -> str | None:
    async with sem:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            # One failed page leaves a partial result, not an aborted stage
            log.warning("fetch_failed", url=url, error=str(exc))
            return None
        return resp.text

async def fetch_many(urls: list[str], client: httpx.AsyncClient, max_concurrency: int = 10) -> list[str]:
    sem = asyncio.Semaphore(max_concurrency)
    pages = await asyncio.gather(*(fetch_one(url, client, sem) for url in urls))
    return [page for page in pages if page is not None]
```

> **Scavengarr hint**
> Apply **per-site** concurrency limits and per-process global limits. This is especially important in multi-stage scraping. Scavengarr: the shared client uses `RetryTransport` + `DomainRateLimiter` for per-domain rate limiting and 429/503 retries (`src/scavengarr/infrastructure/common/`); each plugin bounds its own parallelism via `_new_semaphore()` (`_max_concurrent`, default `5`) and fetches through `_safe_fetch()`, which logs a failure and returns `None`; `ConcurrencyPool` (`src/scavengarr/infrastructure/concurrency.py`) shares httpx and Playwright slots fairly across concurrent Stremio stream requests (Torznab searches take no slots).

---

### 1.4 Avoid Per-Call DNS/Connection Overheads

**Impact: HIGH**

Even with a shared client, patterns that **prevent connection reuse** are costly: changing hosts per request unnecessarily, not enabling keep-alive, or disabling connection pooling. [blog.poespas](https://blog.poespas.me/posts/2024/04/27-optimizing-python-asyncio-for-high-performance/)

**Recommendations**

- Prefer **one base URL per target site**, reuse across calls.
- Keep `follow_redirects=True` for scraping workflows that expect redirects.
- Set `httpx.Limits(max_connections=..., max_keepalive_connections=...)` according to expected concurrency. Keep idle connections open across the pauses between requests (`keepalive_expiry`), but keep `max_keepalive_connections` small: httpcore scans its whole pool on every request.
- Avoid using query parameters that **defeat HTTP caching/CDN** if you rely on upstream caching.

> **Scavengarr hint**
> `build_http_client()` (`src/scavengarr/interfaces/composition.py`) sets `httpx.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=60)` and a 5 s connect timeout. One stream request talks to 18-42 hosts, and with httpx's default 5 s keep-alive every pause between two requests closed them all (TLS handshakes were 21% of the Python CPU on a Raspberry Pi). With 100 idle connections kept, httpcore's pool scan held the GIL 10-20% of the time on the Pi.

---

## 2. FastAPI Application Performance

### 2.1 Initialize Heavy Resources in Lifespan, Not Per Request

**Impact: CRITICAL**

All heavyweight resources should be initialized **once per process**, not in endpoint handlers:

- `httpx.AsyncClient`
- `diskcache.Cache`
- plugin registries, scraping engines
- DB connections, pools, or browser instances (if not per-request by design) [blog.stackademic](https://blog.stackademic.com/optimizing-performance-with-fastapi-c86206cb9e64)

**Incorrect: per-request initialization**

```python
from fastapi import FastAPI

app = FastAPI()

@app.get("/healthz")
async def healthz():
    from diskcache import Cache  # import and init on every request
    cache = Cache(".cache")
    value = cache.get("health")
    return {"ok": True, "cached": bool(value)}
```

**Correct: initialize in lifespan and reuse**

```python
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI, Request
from starlette.datastructures import State

from myservice.cache import CachePort, create_cache  # async port: diskcache or Redis

class AppState(State):
    cache: CachePort

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    state = cast(AppState, app.state)
    state.cache = create_cache(directory=".cache/my-service")
    async with state.cache:  # opens the store once, closes it at shutdown
        yield

app = FastAPI(lifespan=lifespan)
app.state = AppState()

@app.get("/healthz")
async def healthz(request: Request) -> dict[str, bool]:
    cache = cast(AppState, request.app.state).cache
    return {"ok": True, "cached": bool(await cache.get("health"))}
```

> **Scavengarr hint**
> Your composition root should create shared resources once and attach them to `AppState`. Endpoints should only read `request.app.state`. Scavengarr: `lifespan()` in `src/scavengarr/interfaces/composition.py` creates the `CachePort` via `create_cache()` (diskcache or Redis), the shared `httpx.AsyncClient`, `PluginRegistry`, `HttpxSearchEngine`, repositories, `HosterResolverRegistry`, browser/concurrency pools and the Stremio use cases. `AppConfig` is loaded by the CLI (`load_config()`) and stored in `create_app()`. Code never opens `diskcache.Cache` itself: diskcache does synchronous SQLite I/O, and the `CachePort` adapter runs every call in a worker thread.

---

### 2.2 Keep Endpoints Thin and Delegate to Use Cases

**Impact: HIGH**

FastAPI endpoints should **validate input** and delegate to **use-case functions** (application layer). This:

- Keeps endpoints cheap to execute
- Improves testability
- Makes performance issues visible at the use-case level rather than tangled in routing logic

**Incorrect: heavy logic in endpoint**

```python
from fastapi import APIRouter

router = APIRouter()

@router.get("/search")
async def search(q: str):
    # multiple HTTP calls, parsing, business rules
    ...
```

**Correct: delegate to a dedicated use-case**

```python
from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Request

router = APIRouter()

class SearchUseCase:
    def __init__(self, *, plugins: PluginRegistryPort, engine: SearchEnginePort, cache: CachePort) -> None:
        self._plugins = plugins
        self._engine = engine
        self._cache = cache

    async def execute(self, query: str) -> list[SearchResult]:
        # heavy logic here: parallel plugin searches, caching
        ...

@router.get("/search")
async def search(request: Request, q: str) -> dict[str, list[SearchResult]]:
    state = cast(AppState, request.app.state)
    use_case = SearchUseCase(plugins=state.plugins, engine=state.search_engine, cache=state.cache)
    return {"results": await use_case.execute(q)}
```

This separation makes it much easier for an LLM or human to optimize the inner logic (e.g. parallelization, caching) without touching the HTTP surface.

> **Scavengarr hint**
> Use cases know ports, not adapters: they get their dependencies through the constructor and never see `app.state`, a `Request` or the httpx client. The Torznab router builds `TorznabSearchUseCase` per request; `lifespan()` builds the Stremio use cases once.

---

### 2.3 Return Lightweight Responses

**Impact: HIGH**

Avoid unnecessary overhead in response serialization:

- Use **Pydantic models** where schema validation matters, but avoid over-nesting.
- Prefer returning dicts/lists directly when DTOs are simple and validation is already upstream.
- For XML (e.g., Torznab), build the XML once and return it as `Response(content=..., media_type="application/xml")` instead of multiple transformations.

**Incorrect: unnecessary double serialization**

```python
@app.get("/rss")
async def rss():
    xml = build_xml()  # returns str
    return {"xml": xml}  # FastAPI JSON-encodes the wrapper: the client gets JSON with escaped XML
```

**Correct: return final serialized payload**

```python
from fastapi import Response

@app.get("/rss")
async def rss():
    xml = build_xml()  # returns str
    return Response(content=xml, media_type="application/xml")
```

---

## 3. Scraping & Multi-Stage Pipelines

### 3.1 Deduplicate URLs and Short-Circuit Early

**Impact: HIGH**

When scraping, repeatedly visiting the same URL wastes network, CPU, and target-site goodwill. Deduplicate URLs and **short-circuit** if a URL or result has already been processed. [fyld](https://www.fyld.pt/blog/python-performance-guide-writing-code-25/)

**Pattern**

- Deduplicate URLs **within one run** (a `set`, or `dict.fromkeys` to keep the order) before fetching.
- Answer a repeated run from a **result cache** with a TTL (see [4.1](#41-cache-expensive-but-stable-responses)), not from a visited-URL marker: a marker makes a later run skip the page and lose its results.

**Example**

```python
async def fetch_details(urls: list[str], client: httpx.AsyncClient, sem: asyncio.Semaphore) -> list[str]:
    unique = list(dict.fromkeys(urls))  # a page listed twice is fetched once
    pages = await asyncio.gather(*(fetch_one(url, client, sem) for url in unique))
    return [page for page in pages if page is not None]
```

> **Scavengarr hint**
> There is no central multi-stage engine: each plugin implements its own search → detail → links stages and deduplicates URLs within one search. Repeated searches are answered from the search caches (Torznab and Stremio); there is no shared visited-URL cache, which would drop results.

---

### 3.2 Use Bounded Parallelism per Target Site

**Impact: HIGH**

Multi-stage scraping (list → detail → mirrors) can explode into many requests. Use **stage-specific** and **global** limits:

- Bound the **work**, not the results: fetch detail pages only for the hits that match the query; cutting the list to its first links drops results.
- global concurrency via semaphores as in [1.3](#13-use-asynciogather-with-concurrency-limits). [pythonprograming](https://pythonprograming.com/blog/using-pythons-asyncio-for-concurrency-best-practices-and-real-world-applications)

**Incorrect: unbounded fan-out**

```python
async def crawl(urls: list[str], client: httpx.AsyncClient):
    tasks = [client.get(url) for url in urls]  # no limit
    responses = await asyncio.gather(*tasks)
    ...
```

**Correct: relevant hits only, bounded fetches**

```python
async def crawl_stage(hits: list[Hit], query: str, client: httpx.AsyncClient, sem: asyncio.Semaphore) -> list[str]:
    wanted = [hit.url for hit in hits if matches(hit.title, query)]  # skip loose matches
    pages = await asyncio.gather(*(fetch_one(url, client, sem) for url in wanted))
    return [page for page in pages if page is not None]
```

> **Scavengarr hint**
> Plugins collect up to 1000 results over the site's pages (`effective_max_results`), scrape detail pages only for relevant hits (`relevant_hits()` in `src/scavengarr/infrastructure/plugins/relevance.py`) and bound the fetches with `self._new_semaphore()`.

---

### 3.3 Parse Once, Off the Event Loop

**Impact: MEDIUM-HIGH**

Parsing a large HTML page is CPU work that blocks the event loop:

- Avoid building huge intermediate Python objects if only a few fields are needed.
- Use a fast parser and only extract required fields. [fyld](https://www.fyld.pt/blog/python-performance-guide-writing-code-25/)

**Guidelines**

- Limit selectors to only necessary nodes.
- Normalize text as early as possible (strip, convert to int) to avoid repeated work downstream.
- Avoid re-parsing the same HTML string multiple times; reuse the parsed object.

> **Scavengarr hint**
> Plugin parsers use selectolax (lexbor, CSS selectors only; helpers and pitfalls in `src/scavengarr/infrastructure/plugins/dom.py`). Every page goes through `await parse_page(parser, html)`: pages from 32 KiB parse in a worker thread.

---

### 3.4 Bound and Close Browser Pages

**Impact: HIGH**

A browser page costs far more RAM and CPU than an HTTP request:

- Wait for conditions or locators, never with `sleep()`.
- Close contexts and pages deterministically (`try`/`finally` or `async with`).
- Limit browser parallelism with a semaphore.

> **Scavengarr hint**
> Plugins and hoster resolvers share one Chromium process (`SharedBrowserPool`, `src/scavengarr/infrastructure/browser/shared_browser.py`), driven through patchright, a Playwright fork. Playwright plugins bound their pages with `_new_semaphore()`; challenge fallbacks and hoster captures share the page limit of `StealthPool` (`src/scavengarr/infrastructure/browser/stealth_pool.py`, at most 2 pages).

---

## 4. Caching Strategies (diskcache & In-Memory)

### 4.1 Cache Expensive but Stable Responses

**Impact: HIGH**

Cache responses that are:

- **Expensive** to compute (multi-stage scraping, complex queries)
- **Relatively stable** over time (e.g., tracker capabilities, category lists, health-check results) [fyld](https://www.fyld.pt/blog/python-performance-guide-writing-code-25/)

**Example: cache tracker capabilities**

```python
async def get_tracker_caps(tracker_id: str, cache: CachePort) -> dict[str, str]:
    key = f"caps:{tracker_id}"
    if (caps := await cache.get(key)) is not None:
        return caps
    caps = await fetch_caps(tracker_id)  # network
    await cache.set(key, caps, ttl=60)
    return caps
```

**Scavengarr hint**

- Candidates: per-plugin **caps** responses (Torznab `t=caps`) and health-check reachability results for a short TTL (e.g. 30–60 seconds). Scavengarr does not cache caps or the Torznab health probes (both are cheap). Stremio searches skip sites that failed the periodic reachability check (`PluginHealthMonitor`).
- Scavengarr caches Torznab search results (`cache.search_ttl_seconds`, default 900 s; a plugin's `cache_ttl` overrides it) and link-validation outcomes in memory (valid 6 h, invalid 15 min). Stremio search results are cached per title with the same TTL (`src/scavengarr/application/stremio/search_cache.py`; a stale entry answers for up to 6 h while one background search refreshes it), and hoster resolutions in memory (1 h, dead links 15 min). Keep search TTLs short so results reflect current site state.

---

### 4.2 Use `diskcache` for Cross-Process and Long-Lived Caches

**Impact: HIGH**

`diskcache` stores data on disk and by default evicts the least recently stored entries (size limit 1 GiB), suitable for:

- Shared caches across worker processes
- Large numbers of entries
- Longer-lived caches that would exceed RAM if kept in memory only [fyld](https://www.fyld.pt/blog/python-performance-guide-writing-code-25/)

**Pattern**

- Instantiate one `Cache` per service (directory such as `.cache/my-service`).
- Control `size_limit` to avoid unbounded growth.
- Use **TTL (`expire`)** aggressively for temporary results.
- diskcache calls do synchronous SQLite I/O: in async code, run them in a worker thread.

```python
from diskcache import Cache

cache = Cache(".cache/my-service", size_limit=1e9)  # ~1GB

cache.set("search:dune", results, expire=900)  # sync: from async code, via asyncio.to_thread
```

> **Scavengarr hint**
> Code never opens `diskcache.Cache` itself. It uses the `CachePort` on `AppState` (`create_cache()`: diskcache or Redis); the diskcache adapter runs every call in a worker thread, bounds parallel calls and runs writes one at a time.

---

### 4.3 Use LRU Caching for Pure Functions

**Impact: MEDIUM**

For CPU-only, deterministic functions (e.g., small template rendering, config lookups), Python’s `functools.lru_cache` can avoid repeated computations. [realpython](https://realpython.com/python-code-quality/)

```python
from functools import lru_cache
from urllib.parse import quote_plus, urljoin

@lru_cache(maxsize=128)
def build_search_url(base_url: str, query: str) -> str:
    # quote_plus in a query string; a path segment takes quote(query, safe="")
    return urljoin(base_url, f"/search?q={quote_plus(query)}")
```

> Do **not** use `lru_cache` for functions that depend on time, random input, or external I/O side effects.

---

## 5. Async Design Patterns & Error Handling

### 5.1 Design Coroutines to be Truly Asynchronous

**Impact: MEDIUM-HIGH**

Async functions should **await** real I/O, not wrap synchronous code just for the sake of using `async`. [discuss.python](https://discuss.python.org/t/asyncio-best-practices/12576)

**Incorrect: fake async**

```python
async def parse_and_enrich(data: str) -> dict:
    # purely CPU-bound, implemented synchronously
    result = parse_sync(data)   # no await at all
    return enrich_sync(result)
```

**Better: keep it sync, or offload when needed**

```python
def parse_and_enrich_sync(data: str) -> dict:
    result = parse_sync(data)
    return enrich_sync(result)

async def parse_and_enrich(data: str) -> dict:
    return await asyncio.to_thread(parse_and_enrich_sync, data)
```

Use this only if profiling shows that CPU cost justifies the overhead of the executor.

---

### 5.2 Apply Timeouts and Retries With Backoff

**Impact: MEDIUM**

Network calls will fail. Robust pipelines:

- Apply **per-request timeouts** at the HTTP client level
- Use **retries with exponential backoff** for **transient** errors (5xx, network errors)
- **Do not** retry on 4xx client errors (e.g. 404, 401), except 429 [blog.poespas](https://blog.poespas.me/posts/2024/04/27-optimizing-python-asyncio-for-high-performance/)
- Retry in **one** layer: a retry loop on top of a retrying client multiplies the requests.

> **Scavengarr hint**
> The shared client already retries 429 and 503 (`RetryTransport`: `Retry-After`, exponential backoff with jitter, capped; a 429/503 that Cloudflare served from its cache is not retried). Other 5xx and network errors are not retried. Plugins and resolvers add no generic retry loop on top (the sketch below would turn one 503 into up to 12 requests); site-specific retries stay the exception.

**Sketch**

```python
import asyncio
import httpx
import structlog

log = structlog.get_logger(__name__)

async def fetch_with_retry(
    client: httpx.AsyncClient,
    url: str,
    max_retries: int = 3,
    backoff_base: float = 2.0,
) -> httpx.Response | None:
    for attempt in range(max_retries):
        try:
            resp = await client.get(url)
            if 400 <= resp.status_code < 500:
                log.warning("client_error_no_retry", url=url, status=resp.status_code)
                return resp
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if 500 <= status < 600 and attempt < max_retries - 1:
                backoff = backoff_base ** attempt
                log.info("server_error_retrying", url=url, status=status, backoff=backoff)
                await asyncio.sleep(backoff)
                continue
            log.error("server_error_giving_up", url=url, status=status)
            return None
        except httpx.RequestError as e:
            if attempt < max_retries - 1:
                backoff = backoff_base ** attempt
                log.info("request_error_retrying", url=url, error=str(e), backoff=backoff)
                await asyncio.sleep(backoff)
                continue
            log.error("request_error_giving_up", url=url, error=str(e))
            return None
```

---

### 5.3 Fail Fast on Irrecoverable Errors

**Impact: MEDIUM**

For errors that indicate **configuration issues** (e.g. a plugin module without a `plugin` object, `name` or `search` — Scavengarr raises `PluginLoadError` in `src/scavengarr/infrastructure/plugins/loader.py` — or a missing base URL), fail fast:

- Raise explicit exceptions
- Return **422/400** responses for invalid client input
- Avoid infinite retry loops for bad configuration

This prevents wasting CPU/network on patterns that cannot succeed.

---

## 6. Logging, Metrics, and Observability

### 6.1 Use Structured Logging With Sampling

**Impact: MEDIUM**

Structured logging (e.g. `structlog` + stdlib logging) is essential for diagnosing performance issues but can itself become a bottleneck if overused. [realpython](https://realpython.com/python-code-quality/)

**Guidelines**

- Configure a **single logging pipeline** at startup.
- Log **one structured event per request** (method, path, latency, status).
- Sample logs for high-volume endpoints if necessary (e.g. log 1% of successful search requests but all 4xx/5xx).

**Example: request logging middleware**

```python
from __future__ import annotations

import secrets
import time
from collections.abc import Awaitable, Callable

import structlog
from fastapi import FastAPI, Request, Response

log = structlog.get_logger("http")

app = FastAPI()

@app.middleware("http")
async def log_requests(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    start = time.perf_counter()
    # Every log line of the request carries its id
    tokens = structlog.contextvars.bind_contextvars(request_id=secrets.token_hex(6))
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        log.info(
            "http_request",
            method=request.method,
            path=request.url.path,
            query=masked_query(request.url.query),  # values can hold API keys and tokens
            status=status,
            duration_ms=round((time.perf_counter() - start) * 1000.0, 2),
        )
        structlog.contextvars.reset_contextvars(**tokens)
```

> **Scavengarr hint**
> `log_requests` in `src/scavengarr/interfaces/app.py` writes one `http_request` line per request, with `request_id` and masked query values (`loggable_query()`: only the Torznab parameters keep their values). It does not sample.

---

### 6.2 Log at the Edges, Not in Tight Loops

**Impact: MEDIUM**

Logging inside inner loops (per element, per row) can dominate runtime. Instead:

- Count processed items and log **aggregated metrics**.
- Use debug logs sparingly and only when needed.

**Incorrect: logging per scraped row**

```python
for row in rows:
    log.debug("parsed_row", title=title, url=url)
```

**Better: log summary**

```python
log.info("parsed_rows", count=len(rows), source_url=url)
```

---

## 7. Profiling and Code Quality

### 7.1 Profile Before Micro-Optimizing

**Impact: MEDIUM**

Do not guess performance problems. Use profiling tools:

- `cProfile`, `snakeviz`, `yappi` for CPU profiling
- Custom logging around **scraping stages** and **HTTP calls** for latency
- Benchmarks for hot paths (e.g., search pipeline, health check) [realpython](https://realpython.com/python-code-quality/)

**Pattern**

- Add micro-timers around:
  - HTTP fetch stages
  - HTML parsing
  - result normalization
- Record metrics such as `duration_ms`, counts, and error rates in logs.

> **Scavengarr hint**
> The core times its steps with `TelemetryPort.stage()` (served at `GET /metrics`), never plugins or resolvers; label values come only from fixed sets. Profile a running instance with `scripts/stremio_profile.py` (wall time, CPU, outbound requests per host, optional py-spy sampling); benchmarks live in `tests/benchmark/`. See `docs/features/observability.md`.

---

### 7.2 Automate Style and Type Checks

**Impact: MEDIUM**

While not directly a performance booster, consistent style and type safety reduce the risk of introducing performance regressions during refactors. [realpython](https://realpython.com/python-code-quality/)

Recommended tools:

- **Ruff** for formatting and linting
- A type checker (basedpyright, pyright or mypy), especially across `AppState`, async boundaries, and DI

> **Scavengarr hint**
> Scavengarr runs **Ruff** (lint and format) and **basedpyright** (`standard` mode, `[tool.basedpyright]` in `pyproject.toml`, `src/` and `plugins/`) through `pre-commit`; CI runs the same.

These make it safer for LLMs and humans to apply aggressive optimizations.

---

## 8. Python Micro-Optimizations

### 8.1 Use Appropriate Data Structures

**Impact: LOW-MEDIUM**

Use data structures that match the operation:

- `set` / `dict` for O(1) membership and lookup
- `list` for ordered iteration
- `deque` for queues
- Avoid repeatedly scanning large lists for membership; use sets instead. [geeksforgeeks](https://www.geeksforgeeks.org/python/tips-to-maximize-your-python-code-performance/)

**Incorrect: linear membership checks**

```python
visited_urls: list[str] = []

def mark_visited(url: str):
    if url not in visited_urls:
        visited_urls.append(url)
```

**Correct: use a set**

```python
visited_urls: set[str] = set()

def mark_visited(url: str):
    visited_urls.add(url)
```

---

### 8.2 Prefer Comprehensions and Built-ins

**Impact: LOW**

Python’s built-ins and comprehensions are implemented in C and are generally faster than manual loops. [geeksforgeeks](https://www.geeksforgeeks.org/python/tips-to-maximize-your-python-code-performance/)

**Incorrect: manual loop accumulation**

```python
result = []
for item in items:
    if item.is_valid():
        result.append(transform(item))
```

**Correct: list comprehension**

```python
result = [transform(item) for item in items if item.is_valid()]
```

Use built-ins like `sum`, `min`, `max`, `any`, and `all` instead of handwritten loops where clarity is preserved.

---

## How to Use This Guide

For any performance work on a FastAPI + httpx + diskcache + structlog service:

1. **Start with Section 1 and 2 (CRITICAL)**
   - Ensure event loop is non-blocking.
   - Share HTTP clients and caches.
   - Move heavy initialization to lifespan or composition root.

2. **For scraping pipelines**, apply Section 3 and 4:
   - Deduplicate URLs and bound concurrency; retries belong to the shared client (5.2).
   - Cache expensive but stable results with a TTL, through an async cache port.

3. **Instrument and profile** before micro-optimizing:
   - Add structured logs with timings.
   - Use profilers to confirm hotspots.

4. **Only then** consider micro-optimizations in Section 8.

When an LLM refactors code, it should:

- Prioritize **high-impact rules** first.
- Avoid introducing blocking calls in async contexts.
- Prefer **shared, injected resources** over ad-hoc instantiation.
- Keep changes minimal and focused, verifying behavior through tests and (where available) benchmarks.
