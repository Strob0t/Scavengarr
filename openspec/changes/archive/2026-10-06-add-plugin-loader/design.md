## Context

Scavengarr requires a plugin system to support diverse sites without modifying core code. Plugins encapsulate site-specific scraping logic (URL patterns, selectors, authentication) in imperative Python modules; declarative YAML files were part of the original design and are superseded (removed in `42fced9`). The system must support both static HTML scraping (originally Scrapy, now httpx via `HttpxPluginBase`) and JavaScript-rendered sites (Playwright via `PlaywrightPluginBase`).

## Goals / Non-Goals

### Goals

- **Dual-mode flexibility**: YAML for simple declarative configs (superseded), Python for complex imperative logic
- **Type safety**: Pydantic schema validation (YAML, superseded) and Protocol validation (Python) catch errors at load-time
- **httpx + Playwright support**: static HTML/JSON via httpx, JS-rendered content via Playwright (originally: Scrapy CSS selectors and Playwright locators)
- **Developer experience**: Clear error messages guide plugin authors to fix validation issues
- **Performance**: Lazy-loading avoids importing all plugins on startup (only when accessed)
- **Security awareness**: Document risks of Python plugins and mitigation strategies

### Non-Goals

- **Sandboxing**: Phase 1 assumes trusted plugins (maintainer-controlled repo)
- **Plugin versioning**: No backward compatibility layer for schema changes
- **Hot-reloading**: Plugin changes require app restart
- **Plugin dependencies**: No inter-plugin imports (shared code lives in the base classes `HttpxPluginBase`/`PlaywrightPluginBase`)
- **Runtime plugin installation**: No downloading plugins from external sources

## Decisions

### Decision 1: Dual-Mode Plugin System (YAML + Python) (superseded)

Outcome: the YAML mode was implemented and later removed (`42fced9`); all 42 plugins are Python plugins on shared base classes.

**Rationale**:
- **YAML**: Covers 80% of trackers with standard HTML tables and simple auth
- **Python**: Handles edge-cases requiring dynamic logic:
  - OAuth token refresh flows
  - JSON API endpoints (some sites expose REST instead of HTML)
  - Complex parsing (regex extraction from JavaScript variables)
  - Conditional scraping (different URL patterns per search category)

**Real-World Examples**:
- **YAML-suitable**: 1337x, RARBG, TorrentGalaxy (static HTML tables, CSS selectors)
- **Python-required**: mygully (dynamic token auth), sites with Cloudflare challenges

**Alternatives considered**:
- YAML-only: Too limiting; forces forking Scavengarr for complex sites
- Python-only: Overkill for simple sites; higher barrier to entry (chosen in the end, mitigated by the shared base classes)
- Lua/JavaScript DSL: Adds language complexity; worse DX than Python for Python developers

### Decision 2: Pydantic for YAML Validation (superseded)

**Rationale**:
- Auto-generated JSON schema for documentation
- Field validators for URL/regex/semver validation
- Rich error messages with field paths (`scraping.selectors.title: field required`)
- Native FastAPI integration (future admin UI can reuse models)

**Alternatives considered**:
- `jsonschema`: More verbose, less Pythonic error messages
- `marshmallow`: More boilerplate than Pydantic 2.x
- Manual validation: Error-prone, no type hints

### Decision 3: Protocol-Based Python Plugin Validation

**Rationale**:
- Python `typing.Protocol` provides structural typing without inheritance
- Plugins don't need to import/extend base classes (loose coupling)
- Duck-typing validation: "If it has a `search` method with correct signature, it's valid"

Implementation: `load_python_plugin()` (`src/scavengarr/infrastructure/plugins/loader.py`) checks that the module exports `plugin`, that it has a `search` method and a non-empty `name`. In practice all plugins inherit `HttpxPluginBase` or `PlaywrightPluginBase`.
