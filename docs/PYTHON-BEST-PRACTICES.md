# Python Performance Best Practices

**Version 1.0.0**
Target: High‑throughput Python backends and scrapers (FastAPI, httpx, diskcache, structlog, Playwright/scraping engines)
February 2026

> **Note**
> This document is mainly for agents and LLMs to follow when maintaining,
> generating, or refactoring Python codebases with async I/O (FastAPI, httpx,
> scraping pipelines). Humans may also find it useful, but guidance here is
> optimized for automation and consistency by AI‑assisted workflows.

***

## Abstract

This guide collects performance best practices for Python services that are primarily **I/O‑bound**: HTTP APIs, web scrapers, and multi‑stage crawling pipelines. The rules are grouped by **impact** (CRITICAL → HIGH → MEDIUM → LOW) and focus on:

- Keeping the **async event loop non‑blocking**
- Sharing and reusing **HTTP clients and connection pools** (`httpx.AsyncClient`)
- Designing FastAPI apps with efficient **lifespan and dependency wiring**
- Using **diskcache** and in‑memory caching to avoid redundant network calls
- Structuring scraping engines for **bounded concurrency**, **backoff**, and **short‑circuiting**
- Applying Python‑level micro‑optimizations only where they matter

Each rule includes:

- A clear **intent** and **impact level**
- One or more **Incorrect** vs **Correct** examples
- Concrete hints for stacks similar to **Scavengarr** (FastAPI + httpx + diskcache + structlog)

Use this as a checklist when creating or modifying code. For non‑trivial changes, prefer measuring with a profiler before and after the refactor to confirm impact. [blog.poespas](https://blog.poespas.me/posts/2024/04/27-optimizing-python-asyncio-for-high-performance/)

***

## Table of Contents

1. [Eliminating I/O Bottlenecks (Async & HTTP)](#1-eliminating-io-bottlenecks-async--http) — **CRITICAL**
   - 1.1 [Keep the Event Loop Non‑Blocking](#11-keep-the-event-loop-non-blocking)
   - 1.2 [Use Shared Async HTTP Clients](#12-use-shared-async-http-clients)
   - 1.3 [Use asyncio.gather With Concurrency Limits](#13-use-asyncio-gather-with-concurrency-limits)
   - 1.4 [Avoid Per‑Call DNS/Connection Overheads](#14-avoid-per-call-dnsconnection-overheads)
2. [FastAPI Application Performance](#2-fastapi-application-performance) — **CRITICAL**
   - 2.1 [Initialize Heavy Resources in Lifespan, Not Per Request](#21-initialize-heavy-resources-in-lifespan-not-per-request)
   - 2.2 [Keep Endpoints Thin and Delegate to Use Cases](#22-keep-endpoints-thin-and-delegate-to-use-cases)
   - 2.3 [Return Lightweight Responses](#23-return-lightweight-responses)
3. [Scraping & Multi‑Stage Pipelines](#3-scraping--multi-stage-pipelines) — **HIGH**
   - 3.1 [Deduplicate URLs and Short‑Circuit Early](#31-deduplicate-urls-and-short-circuit-early)
   - 3.2 [Use Bounded Parallelism per Target Site](#32-use-bounded-parallelism-per-target-site)
   - 3.3 [Prefer Streaming and Incremental Parsing](#33-prefer-streaming-and-incremental-parsing)
4. [Caching Strategies (diskcache & In‑Memory)](#4-caching-strategies-diskcache--in-memory) — **HIGH**
   - 4.1 [Cache Expensive but Stable Responses](#41-cache-expensive-but-stable-responses)
   - 4.2 [Use diskcache for Cross‑Process and Long‑Lived Caches](#42-use-diskcache-for-cross-process-and-long-lived-caches)
   - 4.3 [Use LRU Caching for Pure Functions](#43-use-lru-caching-for-pure-functions)
5. [Async Design Patterns & Error Handling](#5-async-design-patterns--error-handling) — **MEDIUM**
   - 5.1 [Design Coroutines to be Truly Asynchronous](#51-design-coroutines-to-be-truly-asynchronous)
   - 5.2 [Apply Timeouts and Retries With Backoff](#52-apply-timeouts-and-retries-with-backoff)
   - 5.3 [Fail Fast on Irrecoverable Errors](#53-fail-fast-on-irrecoverable-errors)
6. [Logging, Metrics, and Observability](#6-logging-metrics-and-observability) — **MEDIUM**
   - 6.1 [Use Structured Logging With Sampling](#61-use-structured-logging-with-sampling)
   - 6.2 [Log at the Edges, Not in Tight Loops](#62-log-at-the-edges-not-in-tight-loops)
7. [Profiling and Code Quality](#7-profiling-and-code-quality) — **MEDIUM**
   - 7.1 [Profile Before Micro‑Optimizing](#71-profile-before-micro-optimizing)
   - 7.2 [Automate Style and Type Checks](#72-automate-style-and-type-checks)
8. [Python Micro‑Optimizations](#8-python-micro-optimizations) — **LOW**
   - 8.1 [Use Appropriate Data Structures](#81-use-appropriate-data-structures)
   - 8.2 [Prefer Comprehensions and Built‑ins](#82-prefer-comprehensions-and-built-ins)

***

## 1. Eliminating I/O Bottlenecks (Async & HTTP)

### 1.1 Keep the Event Loop Non‑Blocking

**Impact: CRITICAL (enables true concurrency for I/O‑bound workloads)**

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

For **CPU‑bound** work, offload to a thread/process pool:

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor

executor = ThreadPoolExecutor(max_workers=4)

def compute_something_heavy(x: int) -> int:
    # CPU-heavy logic here
    return x * x

async def compute_endpoint(x: int):
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(executor, compute_something_heavy, x)
    return {"result": result}
```

> **Scavengarr hint**
> Any HTML parsing or RSS serialization that is CPU‑heavy should either be:
> - fast enough to stay in the event loop, **or**
> - moved into `run_in_executor` if profiling shows it dominates request time.

***

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
# app_lifespan.py
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import FastAPI

class AppState:
    http_client: httpx.AsyncClient

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state = AppState()  # for type checkers
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0),
        follow_redirects=True,
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        headers={"User-Agent": "MyService/1.0"},
    )
    try:
        yield
    finally:
        await app.state.http_client.aclose()

app = FastAPI(lifespan=lifespan)
```

```python
# usage in endpoints or use cases
import httpx
from fastapi import Depends, Request

async def fetch_page(request: Request, url: str) -> str:
    client: httpx.AsyncClient = request.app.state.http_client
    resp = await client.get(url)
    resp.raise_for_status()
    return resp.text
```

> **Scavengarr hint**
> Ensure all scraping engines and reachability checks use the **injected** `httpx.AsyncClient` from your `AppState`, never create a new client inside scraping functions.

***

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

async def fetch_one(url: str, client: httpx.AsyncClient, sem: asyncio.Semaphore) -> str:
    async with sem:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text

async def fetch_many(urls: list[str], client: httpx.AsyncClient, max_concurrency: int = 10) -> list[str]:
    sem = asyncio.Semaphore(max_concurrency)
    tasks = [
        asyncio.create_task(fetch_one(url, client, sem))
        for url in urls
    ]
    return await asyncio.gather(*tasks)
```

> **Scavengarr hint**
> Apply **per‑site** concurrency limits (e.g. 5–10 parallel requests per tracker) and per‑process global limits (e.g. `max_connections=100` in httpx). This is especially important in multi‑stage scraping engines.

***

### 1.4 Avoid Per‑Call DNS/Connection Overheads

**Impact: HIGH**

Even with a shared client, patterns that **prevent connection reuse** are costly: changing hosts per request unnecessarily, not enabling keep‑alive, or disabling connection pooling. [blog.poespas](https://blog.poespas.me/posts/2024/04/27-optimizing-python-asyncio-for-high-performance/)

**Recommendations**

- Prefer **one base URL per target site**, reuse across calls.
- Keep `follow_redirects=True` for scraping workflows that expect redirects.
- Set `httpx.Limits(max_connections=..., max_keepalive_connections=...)` according to expected concurrency.
- Avoid using query parameters that **defeat HTTP caching/CDN** if you rely on upstream caching.

***

## 2. FastAPI Application Performance

### 2.1 Initialize Heavy Resources in Lifespan, Not Per Request

**Impact: CRITICAL**

All heavyweight resources should be initialized **once per process**, not in endpoint handlers:

- `httpx.AsyncClient`
- `diskcache.Cache`
- plugin registries, scraping engines
- DB connections, pools, or browser instances (if not per‑request by design) [blog.stackademic](https://blog.stackademic.com/optimizing-performance-with-fastapi-c86206cb9e64)

**Incorrect: per‑request initialization**

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
from contextlib import asynccontextmanager
from typing import AsyncIterator

from diskcache import Cache
from fastapi import FastAPI

class AppState:
    cache: Cache

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state = AppState()
    app.state.cache = Cache(".cache/my-service", size_limit=1e9)
    try:
        yield
    finally:
        app.state.cache.close()

app = FastAPI(lifespan=lifespan)

@app.get("/healthz")
async def healthz():
    cache: Cache = app.state.cache
    return {"ok": True, "cached": bool(cache.get("health"))}
```

> **Scavengarr hint**
> Your composition root should create:
> - `AppConfig`
> - shared `httpx.AsyncClient`
> - `Cache` (diskcache)
> - plugin registry + scraping engine
> and attach them to `AppState` once. Endpoints should only read `request.app.state`.

***

### 2.2 Keep Endpoints Thin and Delegate to Use Cases

**Impact: HIGH**

FastAPI endpoints should **validate input** and delegate to **use‑case functions** (application layer). This:

- Keeps endpoints cheap to execute
- Improves testability
- Makes performance issues visible at the use‑case level rather than tangled in routing logic

**Incorrect: heavy logic in endpoint**

```python
from fastapi import APIRouter

router = APIRouter()

@router.get("/search")
async def search(q: str):
    # multiple HTTP calls, parsing, business rules
    ...
```

**Correct: delegate to a dedicated use‑case**

```python
from fastapi import APIRouter, Request

router = APIRouter()

class SearchUseCase:
    async def execute(self, query: str, app_state) -> list[dict]:
        # heavy logic here, using app_state.http_client, app_state.cache, etc.
        ...

@router.get("/search")
async def search(request: Request, q: str):
    state = request.app.state
    use_case = SearchUseCase()
    results = await use_case.execute(q, state)
    return {"results": results}
```

This separation makes it much easier for an LLM or human to optimize the inner logic (e.g. parallelization, caching) without touching the HTTP surface.

***

### 2.3 Return Lightweight Responses

**Impact: HIGH**

Avoid unnecessary overhead in response serialization:

- Use **Pydantic models** where schema validation matters, but avoid over‑nesting.
- Prefer returning dicts/lists directly when DTOs are simple and validation is already upstream.
- For XML (e.g., Torznab), build the XML once and return it as `Response(content=..., media_type="application/xml")` instead of multiple transformations.

**Incorrect: unnecessary double serialization**

```python
from fastapi import Response

@app.get("/rss")
async def rss():
    xml = build_xml()            # returns str
    data = {"xml": xml}
    return Response(content=data)  # FastAPI will JSON-encode this wrapper
```

**Correct: return final serialized payload**

```python
from fastapi import Response

@app.get("/rss")
async def rss():
    xml = build_xml()  # returns str
    return Response(content=xml, media_type="application/xml")
```

***

## 3. Scraping & Multi‑Stage Pipelines

### 3.1 Deduplicate URLs and Short‑Circuit Early

**Impact: HIGH**

When scraping, repeatedly visiting the same URL wastes network, CPU, and target‑site goodwill. Deduplicate URLs and **short‑circuit** if a URL or result has already been processed. [fyld](https://www.fyld.pt/blog/python-performance-guide-writing-code-25/)

**Pattern**

- Maintain a **visited URL cache** (e.g. `diskcache.Cache` with a TTL).
- Before fetching, check whether the URL is marked visited.
- On success, mark it visited with TTL (e.g. 1h).

**Example**

```python
from diskcache import Cache
import httpx
import structlog

log = structlog.get_logger(__name__)

class Scraper:
    def __init__(self, client: httpx.AsyncClient, cache: Cache):
        self.client = client
        self.cache = cache

    async def fetch_page(self, url: str) -> str | None:
        cache_key = f"visited:{url}"
        if self.cache.get(cache_key):
            log.debug("url_already_visited", url=url)
            return None

        resp = await self.client.get(url)
        resp.raise_for_status()

        self.cache.set(cache_key, True, expire=3600)  # 1 hour
        return resp.text
```

> **Scavengarr hint**
> Apply this pattern both in **list pages** and **detail pages** within your multi‑stage scraping engine to avoid revisiting the same torrents/releases across queries.

***

### 3.2 Use Bounded Parallelism per Target Site

**Impact: HIGH**

Multi‑stage scraping (list → detail → mirrors) can explode into many requests. Use **stage‑specific** and **global** limits:

- e.g. per‑stage max links: process first 10–20 links, log if truncated.
- global concurrency via semaphores as in [1.3](#13-use-asyncio-gather-with-concurrency-limits). [pythonprograming](https://pythonprograming.com/blog/using-pythons-asyncio-for-concurrency-best-practices-and-real-world-applications)

**Incorrect: unbounded recursion**

```python
async def crawl(urls: list[str], client: httpx.AsyncClient):
    tasks = [client.get(url) for url in urls]  # no limit
    responses = await asyncio.gather(*tasks)
    ...
```

**Correct: integrate stage limits and truncation**

```python
MAX_LINKS_PER_STAGE = 10

async def crawl_stage(urls: list[str], client: httpx.AsyncClient, sem: asyncio.Semaphore):
    limited_urls = urls[:MAX_LINKS_PER_STAGE]
    tasks = [
        asyncio.create_task(fetch_one(url, client, sem))
        for url in limited_urls
    ]
    return await asyncio.gather(*tasks)
```

***

### 3.3 Prefer Streaming and Incremental Parsing

**Impact: MEDIUM‑HIGH**

For large HTML pages or RSS feeds, prefer **incremental** parsing where feasible:

- Avoid building huge intermediate Python objects if only a few fields are needed.
- Use efficient parsers (`lxml`, `parsel`, `BeautifulSoup` with appropriate parser) and only extract required fields. [fyld](https://www.fyld.pt/blog/python-performance-guide-writing-code-25/)

**Guidelines**

- Limit CSS/XPath selectors to only necessary nodes.
- Normalize text as early as possible (strip, convert to int) to avoid repeated work downstream.
- Avoid re‑parsing the same HTML string multiple times; reuse the parsed object.

***

## 4. Caching Strategies (diskcache & In‑Memory)

### 4.1 Cache Expensive but Stable Responses

**Impact: HIGH**

Cache responses that are:

- **Expensive** to compute (multi‑stage scraping, complex queries)
- **Relatively stable** over time (e.g., tracker capabilities, category lists, health‑check results) [fyld](https://www.fyld.pt/blog/python-performance-guide-writing-code-25/)

**Example: cache tracker capabilities**

```python
from functools import lru_cache

@lru_cache(maxsize=256)
def get_tracker_caps(tracker_id: str) -> dict:
    # This might internally call plugin definitions or hit the network once
    ...
```

**Scavengarr hint**

- Cache:
  - Per‑plugin **caps** responses (Torznab `t=caps`)
  - Health‑check reachability results for a short TTL (e.g. 30–60 seconds)
- Do **not** over‑cache search queries that must reflect current tracker state.

***

### 4.2 Use `diskcache` for Cross‑Process and Long‑Lived Caches

**Impact: HIGH**

`diskcache` stores data on disk with an LRU eviction policy, suitable for:

- Shared caches across worker processes
- Large numbers of visited URLs
- Longer‑lived caches that would exceed RAM if kept in memory only [fyld](https://www.fyld.pt/blog/python-performance-guide-writing-code-25/)

**Pattern**

- Instantiate one `Cache` per service (directory such as `.cache/my-service`).
- Control `size_limit` to avoid unbounded growth.
- Use **TTL (`expire`)** aggressively for visited URLs and temporary results.

```python
from diskcache import Cache

cache = Cache(".cache/my-service", size_limit=1e9)  # ~1GB

cache.set("visited:https://example.org/page/1", True, expire=3600)
```

***

### 4.3 Use LRU Caching for Pure Functions

**Impact: MEDIUM**

For CPU‑only, deterministic functions (e.g., small template rendering, config lookups), Python’s `functools.lru_cache` can avoid repeated computations. [realpython](https://realpython.com/python-code-quality/)

```python
from functools import lru_cache

@lru_cache(maxsize=128)
def build_search_url(base_url: str, path_template: str, query: str) -> str:
    from urllib.parse import quote_plus, urljoin
    path = path_template.format(query=quote_plus(query))
    return urljoin(base_url, path)
```

> Do **not** use `lru_cache` for functions that depend on time, random input, or external I/O side effects.

***

## 5. Async Design Patterns & Error Handling

### 5.1 Design Coroutines to be Truly Asynchronous

**Impact: MEDIUM‑HIGH**

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
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, parse_and_enrich_sync, data)
```

Use this only if profiling shows that CPU cost justifies the overhead of the executor.

***

### 5.2 Apply Timeouts and Retries With Backoff

**Impact: MEDIUM**

Network calls will fail. Robust pipelines:

- Apply **per‑request timeouts** at the HTTP client level
- Use **retries with exponential backoff** for **transient** errors (5xx, network errors)
- **Do not** retry on 4xx client errors (e.g. 404, 401) [blog.poespas](https://blog.poespas.me/posts/2024/04/27-optimizing-python-asyncio-for-high-performance/)

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

***

### 5.3 Fail Fast on Irrecoverable Errors

**Impact: MEDIUM**

For errors that indicate **configuration issues** (e.g. invalid plugin definition, missing base URL, invalid schema), fail fast:

- Raise explicit exceptions
- Return **422/400** responses for invalid client input
- Avoid infinite retry loops for bad configuration

This prevents wasting CPU/network on patterns that cannot succeed.

***

## 6. Logging, Metrics, and Observability

### 6.1 Use Structured Logging With Sampling

**Impact: MEDIUM**

Structured logging (e.g. `structlog` + stdlib logging) is essential for diagnosing performance issues but can itself become a bottleneck if overused. [realpython](https://realpython.com/python-code-quality/)

**Guidelines**

- Configure a **single logging pipeline** at startup.
- Log **one structured event per request** (method, path, latency, status).
- Sample logs for high‑volume endpoints if necessary (e.g. log 1% of successful search requests but all 4xx/5xx).

**Example: request logging middleware**

```python
import time
import structlog
from fastapi import FastAPI, Request

log = structlog.get_logger("http")

app = FastAPI()

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        duration_ms = (time.perf_counter() - start) * 1000.0
        log.info(
            "http_request",
            method=request.method,
            path=request.url.path,
            query=str(request.url.query),
            status=status if "status" in locals() else 500,
            duration_ms=round(duration_ms, 2),
            client_ip=request.client.host if request.client else None,
        )
```

***

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

***

## 7. Profiling and Code Quality

### 7.1 Profile Before Micro‑Optimizing

**Impact: MEDIUM**

Do not guess performance problems. Use profiling tools:

- `cProfile`, `snakeviz`, `yappi` for CPU profiling
- Custom logging around **scraping stages** and **HTTP calls** for latency
- Benchmarks for hot paths (e.g., search pipeline, health check) [realpython](https://realpython.com/python-code-quality/)

**Pattern**

- Add micro‑timers around:
  - HTTP fetch stages
  - HTML parsing
  - result normalization
- Record metrics such as `duration_ms`, counts, and error rates in logs.

***

### 7.2 Automate Style and Type Checks

**Impact: MEDIUM**

While not directly a performance booster, consistent style and type safety reduce the risk of introducing performance regressions during refactors. [realpython](https://realpython.com/python-code-quality/)

Recommended tools:

- **Black** or **Ruff** for formatting
- **Ruff**, **Flake8**, or similar for linting
- **mypy** for type checking (especially across `AppState`, async boundaries, and DI)

These make it safer for LLMs and humans to apply aggressive optimizations.

***

## 8. Python Micro‑Optimizations

### 8.1 Use Appropriate Data Structures

**Impact: LOW‑MEDIUM**

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

***

### 8.2 Prefer Comprehensions and Built‑ins

**Impact: LOW**

Python’s built‑ins and comprehensions are implemented in C and are generally faster than manual loops. [geeksforgeeks](https://www.geeksforgeeks.org/python/tips-to-maximize-your-python-code-performance/)

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

Use built‑ins like `sum`, `min`, `max`, `any`, and `all` instead of handwritten loops where clarity is preserved.

***

## How to Use This Guide

For any performance work on a FastAPI + httpx + diskcache + structlog service:

1. **Start with Section 1 and 2 (CRITICAL)**
   - Ensure event loop is non‑blocking.
   - Share HTTP clients and caches.
   - Move heavy initialization to lifespan or composition root.

2. **For scraping pipelines**, apply Section 3 and 4:
   - Deduplicate URLs, bound concurrency, and implement retries/backoff.
   - Use diskcache and targeted caching for expensive but stable operations.

3. **Instrument and profile** before micro‑optimizing:
   - Add structured logs with timings.
   - Use profilers to confirm hotspots.

4. **Only then** consider micro‑optimizations in Section 8.

When an LLM refactors code, it should:

- Prioritize **high‑impact rules** first.
- Avoid introducing blocking calls in async contexts.
- Prefer **shared, injected resources** over ad‑hoc instantiation.
- Keep changes minimal and focused, verifying behavior through tests and (where available) benchmarks.
