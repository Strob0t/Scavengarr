# Plan: Playwright Engine Adapter

**Status:** Planned
**Priority:** High
**Related:** `plugins/boerse.py`, `src/scavengarr/infrastructure/scraping/`

## Problem

Scavengarr's dual-engine architecture (Scrapy for static HTML, Playwright for JS-heavy sites)
is a core design principle, but only the Scrapy engine has a proper adapter implementation.
The boerse.py Python plugin manages its own Playwright lifecycle directly, which:

- Bypasses the `SearchEnginePort` protocol entirely
- Duplicates browser management logic that should be centralized
- Prevents YAML plugins from declaring `mode: "playwright"`
- Makes resource cleanup fragile (no shared pool, no lifecycle hooks)

A proper `PlaywrightAdapter` that implements `SearchEnginePort` would unify both engines
under the same interface, enabling YAML plugins to target JS-heavy sites declaratively.

## Design

### Adapter Interface

The `PlaywrightAdapter` implements the same `SearchEnginePort` protocol as the existing
`ScrapyAdapter`, so the application layer remains engine-agnostic:

```python
class PlaywrightAdapter:
    """SearchEnginePort implementation using Playwright for JS-heavy sites."""

    async def search(
        self, plugin: PluginRegistryPort, query: str
    ) -> list[SearchResult]: ...

    async def validate_results(
        self, results: list[SearchResult]
    ) -> list[SearchResult]: ...
```

### Browser Pool

Central browser pool to avoid per-request Chromium launches:

- Single `Playwright` instance per process
- Configurable pool of `BrowserContext` instances (default: 3)
- Semaphore-bounded concurrency to prevent RAM spikes
- Contexts are recycled between requests (cookies cleared)
- Graceful shutdown via `aclose()` lifecycle hook

```
PlaywrightPool
  |-- Playwright instance (singleton)
  |-- Browser instance (one Chromium)
  |-- Context pool [ctx_1, ctx_2, ctx_3]
  |-- Semaphore(max_concurrent_pages)
```

### Configuration

New configuration section in `config.yaml`:

```yaml
playwright:
  headless: true
  max_contexts: 3
  max_concurrent_pages: 6
  navigation_timeout_ms: 30000
  cloudflare_wait_ms: 15000
  user_agent: "Mozilla/5.0 ..."
```

### Multi-Stage Execution

YAML plugins with `mode: "playwright"` follow the same stage pipeline as Scrapy plugins:

1. For each stage, navigate to the URL and wait for content
2. Extract data using `page.query_selector_all()` mapped from YAML selectors
3. Intermediate stages produce URLs for the next stage
4. Terminal stages produce `SearchResult` entities

Selector syntax remains CSS-based (same as Scrapy), so plugins can switch modes
without rewriting selectors in most cases.

## Checklist

### Phase 1: Core Adapter
- [ ] Create `PlaywrightAdapter` implementing `SearchEnginePort`
- [ ] Implement browser pool with configurable concurrency
- [ ] Add selector execution (CSS selectors via `page.query_selector_all`)
- [ ] Add Cloudflare/JS challenge detection and wait logic
- [ ] Register adapter in composition root alongside ScrapyAdapter

### Phase 2: YAML Plugin Support
- [ ] Route `mode: "playwright"` plugins to `PlaywrightAdapter`
- [ ] Map YAML stage selectors to Playwright locator queries
- [ ] Support `wait_for` directives in stage config (network idle, selector visible)
- [ ] Add stage-level timeout configuration

### Phase 3: Boerse Migration
- [ ] Refactor `boerse.py` to use `PlaywrightAdapter` instead of managing its own browser
- [ ] Move Cloudflare bypass logic into the adapter (reusable)
- [ ] Move authentication flow into a plugin-level auth hook
- [ ] Verify identical results with integration test fixtures

### Phase 4: Resource Management
- [ ] Implement context recycling (clear cookies/storage between requests)
- [ ] Add health check endpoint for Playwright status
- [ ] Add graceful shutdown (close all contexts on SIGTERM)
- [ ] Memory usage monitoring and automatic context eviction

### Phase 5: Testing
- [ ] Unit tests for adapter with mocked Playwright API
- [ ] Integration tests with fixture HTML served locally
- [ ] Stress test for pool exhaustion and recovery
- [ ] Compare Scrapy vs Playwright output for same site (dual-mode validation)

## Dependencies

- `playwright` package (already in `pyproject.toml`)
- Browser binaries installed in container (`playwright install chromium`)
- Sufficient shared memory for Chromium (`shm_size: "1gb"` in Docker)

## Risk Considerations

- **Memory:** Each Chromium context uses ~50-100MB RAM. The pool must be bounded.
- **Flakiness:** JS-rendered pages are inherently less deterministic. Tests need retries or fixture HTML.
- **Cloudflare:** Challenge bypass may break with Cloudflare updates. Isolate this logic for easy maintenance.
- **Startup time:** First Chromium launch adds ~2-5s. The pool should pre-warm on application start.
