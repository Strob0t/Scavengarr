# Plan: Playwright Engine -- Browser Pool & Resource Management

**Status:** Partially Implemented (base class exists, pool not yet centralized)
**Priority:** Medium
**Related:** `src/scavengarr/infrastructure/plugins/playwright_base.py`, `plugins/boerse.py`

## Current State

Scavengarr has a `PlaywrightPluginBase` that provides browser lifecycle management
for 9 Playwright plugins. Each plugin manages its own browser instance. The original
plan for a centralized `PlaywrightAdapter` implementing `SearchEnginePort` is no longer
needed since all plugins are Python-based and manage their own scraping.

The remaining opportunity is to centralize the browser pool for better resource
management across multiple Playwright plugins.

## Design

### Browser Pool (optional future improvement)

Central browser pool to avoid per-plugin Chromium launches:

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

Existing configuration section in `config.yaml`:

```yaml
playwright:
  headless: true
  timeout_ms: 30000
```

Potential additions for pool management:

```yaml
playwright:
  max_contexts: 3
  max_concurrent_pages: 6
  cloudflare_wait_ms: 15000
```

## Checklist

### Phase 1: Pool Infrastructure
- [ ] Create shared `PlaywrightPool` for cross-plugin browser management
- [ ] Implement context recycling (clear cookies/storage between requests)
- [ ] Register pool in composition root alongside httpx client

### Phase 2: Plugin Migration
- [ ] Refactor `PlaywrightPluginBase` to use shared pool instead of per-plugin browser
- [ ] Verify all 9 Playwright plugins work with pooled contexts
- [ ] Move Cloudflare bypass logic into shared utility (reusable)

### Phase 3: Resource Management
- [ ] Add health check endpoint for Playwright status
- [ ] Add graceful shutdown (close all contexts on SIGTERM)
- [ ] Memory usage monitoring and automatic context eviction
- [ ] Pre-warm pool on application start

### Phase 4: Testing
- [ ] Unit tests for pool with mocked Playwright API
- [ ] Integration tests with fixture HTML served locally
- [ ] Stress test for pool exhaustion and recovery

## Dependencies

- `playwright` package (already in `pyproject.toml`)
- Browser binaries installed in container (`playwright install chromium`)
- Sufficient shared memory for Chromium (`shm_size: "1gb"` in Docker)

## Risk Considerations

- **Memory:** Each Chromium context uses ~50-100MB RAM. The pool must be bounded.
- **Flakiness:** JS-rendered pages are inherently less deterministic. Tests need retries or fixture HTML.
- **Cloudflare:** Challenge bypass may break with Cloudflare updates. Isolate this logic for easy maintenance.
- **Startup time:** First Chromium launch adds ~2-5s. The pool should pre-warm on application start.
