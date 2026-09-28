[← Back to Index](../features/README.md)

# Plan: Playwright Engine — Browser Pool & Resource Management

**Status:** Implemented (verified 2026-09-28)
**Priority:** Medium
**Related:** `src/scavengarr/infrastructure/browser/shared_browser.py`, `src/scavengarr/infrastructure/plugins/playwright_base.py`

## Implementation Summary

Scavengarr now has a `SharedBrowserPool` that manages a single Chromium process shared by all 9 Playwright plugins. Each plugin gets its own `BrowserContext` for isolation while sharing the underlying browser — eliminating per-plugin ~1-2s browser startup overhead.

### Key components

- **SharedBrowserPool** (`src/scavengarr/infrastructure/browser/shared_browser.py`): singleton Chromium process, pre-warmed on first Stremio request
- **Composition-time pool injection**: plugins receive the shared pool via `set_shared_pool()` at startup
- **Per-request BrowserContext isolation**: `isolated_search()` creates a fresh `BrowserContext` per request, preventing state corruption
- **`_serialize_search` mode**: plugins that rely on persistent page state (streamworld, moflix) serialize via `asyncio.Lock`
- **Cookie-based session transfer**: authenticated plugins (boerse, mygully) login in temporary contexts and inject cookies into per-request contexts
- **Ownership-aware cleanup**: `PlaywrightPluginBase.cleanup()` only closes the browser when the plugin owns it (standalone mode)
- **Disconnection recovery**: `_ensure_browser()` checks `browser.is_connected()` and relaunches transparently
- **Browser relaunch retry**: `_launch_standalone(*, retries=1)` retries browser launch once on failure

### Configuration

```yaml
playwright:
  headless: true
  timeout_ms: 30000

stremio:
  max_concurrent_playwright: 5  # upper bound for parallel PW plugin searches (auto-tuned when stremio.auto_tune_all=true)
```

### Testing

- Unit tests for `PlaywrightPluginBase` shared base class
- Unit tests for `isolated_search()` and `_serialize_search` modes
- E2E tests covering concurrent Playwright plugin search via `ConcurrencyPool`
- 169 E2E tests total (including Stremio streamable link verification)

## Dependencies

- `playwright` package (already in `pyproject.toml`)
- Browser binaries installed in container (`python -m patchright install chromium` in `Dockerfile.prod`)
