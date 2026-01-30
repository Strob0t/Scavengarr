## Context
Scavengarr requires a plugin system to support diverse torrent trackers without modifying core code. Plugins encapsulate site-specific scraping logic (URL patterns, selectors, authentication) in declarative YAML files or imperative Python modules. The system must support both static HTML scraping (Scrapy) and JavaScript-rendered sites (Playwright).

## Goals / Non-Goals

### Goals
- **Dual-mode flexibility**: YAML for simple declarative configs, Python for complex imperative logic
- **Type safety**: Pydantic schema validation (YAML) and Protocol validation (Python) catch errors at load-time
- **Scrapy + Playwright support**: CSS selectors for static HTML, locators for JS-rendered content
- **Developer experience**: Clear error messages guide plugin authors to fix validation issues
- **Performance**: Lazy-loading avoids parsing all files on startup (only when accessed)
- **Security awareness**: Document risks of Python plugins and mitigation strategies

### Non-Goals
- **Sandboxing**: Phase 1 assumes trusted plugins (maintainer-controlled repo)
- **Plugin versioning**: No backward compatibility layer for schema changes
- **Hot-reloading**: Plugin changes require app restart
- **Plugin dependencies**: No inter-plugin imports or shared code modules (can revisit later)
- **Runtime plugin installation**: No downloading plugins from external sources

## Decisions

### Decision 1: Dual-Mode Plugin System (YAML + Python)
**Rationale**:
- **YAML**: Covers 80% of trackers with standard HTML tables and simple auth
- **Python**: Handles edge-cases requiring dynamic logic:
  - OAuth token refresh flows
  - JSON API endpoints (some trackers expose REST instead of HTML)
  - Complex parsing (regex extraction from JavaScript variables)
  - Conditional scraping (different URL patterns per search category)

**Real-World Examples**:
- **YAML-suitable**: 1337x, RARBG, TorrentGalaxy (static HTML tables, CSS selectors)
- **Python-required**: my-gully (dynamic token auth), private trackers with Cloudflare challenges

**Alternatives considered**:
- YAML-only: Too limiting; forces forking Scavengarr for complex trackers
- Python-only: Overkill for simple sites; higher barrier to entry
- Lua/JavaScript DSL: Adds language complexity; worse DX than Python for Python developers

### Decision 2: Pydantic for YAML Validation
**Rationale**:
- Auto-generated JSON schema for documentation
- Field validators for URL/regex/semver validation
- Rich error messages with field paths (`scraping.selectors.title: field required`)
- Native FastAPI integration (future admin UI can reuse models)

**Alternatives considered**:
- `jsonschema`: More verbose, less Pythonic error messages
- `marshmallow`: More boilerplate than Pydantic 2.x
