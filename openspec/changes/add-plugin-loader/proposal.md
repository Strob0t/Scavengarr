# Change: Add Plugin Loader

## Why
Scavengarr needs a dynamic plugin system to support arbitrary torrent trackers without hardcoding scraper logic. Plugins define site-specific scraping rules (URLs, selectors, auth) enabling maintainers to add new trackers without modifying core code.

## What Changes
- **Dual-mode plugin system**: YAML (declarative) and Python (imperative)
- YAML plugins for simple CSS/XPath scraping with Scrapy or Playwright
- Python plugins for complex auth flows, JSON APIs, custom parsing logic
- Plugin discovery: auto-load all `.yaml` and `.py` files from `plugins/` directory
- Plugin registry with lazy-loading and in-memory caching
- Pydantic schema validation for YAML plugins
- Protocol-based validation for Python plugins
- Detailed error messages for validation failures

## What Changes
- YAML plugins define `scraping.mode: "scrapy"` or `"playwright"` with selectors
- Python plugins implement `async def search(query, category)` interface
- Plugin loader auto-detects format via file extension
- Registry provides `get(name)`, `list_names()`, `get_by_mode()` methods

## Impact
- Affected specs: New capability `plugin-system`
- Affected code:
  - New `src/scavengarr/plugins/` module:
    - `base.py` - Protocol definitions and SearchResult model
    - `schema.py` - Pydantic models for YAML plugins
    - `loader.py` - YAML/Python loading logic
    - `registry.py` - Plugin cache and discovery
    - `exceptions.py` - Plugin-specific errors
  - Update `src/scavengarr/main.py` to initialize PluginRegistry on startup
- Affected files:
  - `plugins/*.yaml` - YAML plugin definitions must conform to schema
  - `plugins/*.py` - Python plugins must implement PluginProtocol
  - Dependencies: `pydantic>=2.0` and `pyyaml>=6.0` (already installed)
