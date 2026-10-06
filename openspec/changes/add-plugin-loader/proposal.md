# Change: Add Plugin Loader

> **Status (2026-09-28):** Python part implemented under `src/scavengarr/domain/plugins/` (`base.py`, `exceptions.py`) and `src/scavengarr/infrastructure/plugins/` (`loader.py`, `registry.py`). YAML part superseded: it was implemented and then removed together with the Scrapy adapter in `42fced9`. `get_by_mode()` never existed (the registry offers `get_mode(name)` and `get_by_provides(provides)`), `load_all()` and `PluginValidationError` were removed later, and `DuplicatePluginError` is defined but never raised (duplicate names are skipped, first wins).

## Why

Scavengarr needs a dynamic plugin system to support arbitrary sites without hardcoding scraper logic. Plugins define site-specific scraping rules (URLs, selectors, auth) enabling maintainers to add new sites without modifying core code.

## What Changes

- **Dual-mode plugin system**: YAML (declarative, superseded) and Python (imperative)
- YAML plugins for simple CSS/XPath scraping with Scrapy or Playwright (superseded)
- Python plugins for complex auth flows, JSON APIs, custom parsing logic
- Plugin discovery: auto-load all `.yaml` (superseded) and `.py` files from `plugins/` directory
- Plugin registry with lazy-loading and in-memory caching
- Pydantic schema validation for YAML plugins (superseded)
- Protocol-based validation for Python plugins
- Detailed error messages for validation failures

## Details

- YAML plugins define `scraping.mode: "scrapy"` or `"playwright"` with selectors (superseded)
- Python plugins implement `async def search(query, category, season, episode)` and export a non-empty `name`
- Plugin loader auto-detects format via file extension (superseded: only `.py` is discovered)
- Registry provides `get(name)`, `list_names()`, `get_mode(name)`, `get_by_provides(provides)` and `get_languages(name)` (the planned `get_by_mode()` was never built)

## Impact

- Affected specs: New capability `plugin-system`
- Affected code:
  - `src/scavengarr/domain/plugins/`:
    - `base.py` — `PluginProtocol` and `SearchResult` dataclass
    - `plugin_schema.py` — plugin config dataclasses (`AuthConfig`, `HttpOverrides`); the planned Pydantic YAML schema is superseded, and the dataclasses were removed unused (2026-10-06)
    - `exceptions.py` — plugin-specific errors
  - `src/scavengarr/infrastructure/plugins/`:
    - `loader.py` — Python loading logic (`load_python_plugin`)
    - `registry.py` — `PluginRegistry` (discovery, lazy loading, cache)
  - `src/scavengarr/interfaces/composition.py` initializes the `PluginRegistry` on startup (planned: `src/scavengarr/main.py`)
- Affected files:
  - `plugins/*.yaml` — YAML plugin definitions (superseded)
  - `plugins/*.py` — Python plugins must implement `PluginProtocol`
  - Dependencies: `pydantic>=2.0` and `pyyaml>=6.0` (already installed; `pyyaml` is still used for the config file)
