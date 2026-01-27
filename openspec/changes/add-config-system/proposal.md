# Change: Add Configuration System

## Why
Scavengarr needs a consistent configuration system to run reproducibly across local development, Docker, and later production deployments.
Configuration must cover plugin discovery, HTTP/scraping defaults (Scrapy + Playwright), FastAPI server settings, and logging behavior.

Without a typed config, every subsequent change (plugin loader, engines, API) risks hidden implicit defaults and unclear precedence rules.

## What Changes
- Add a typed application configuration model using Pydantic Settings (`AppConfig`).
- Add deterministic config precedence: CLI args > environment variables > YAML config file > defaults.
- Add `.env` support for local development convenience.
- Add a single `load_config()` entrypoint used by CLI and FastAPI startup.
- Add safe handling of secrets: secrets are never logged in plaintext.
- Provide a minimal default config surface that matches near-term roadmap:
  - Plugin directory
  - HTTP client defaults for Scrapy engine
  - Playwright defaults (timeouts, headless)
  - Logging config (level, json vs console)
  - Cache settings placeholder (disk-only; Redis explicitly out of scope)

## Impact
- Affected specs: new capability `configuration`
- Affected code (new modules):
  - `src/scavengarr/config/schema.py` (AppConfig)
  - `src/scavengarr/config/load.py` (load_config)
  - `src/scavengarr/config/defaults.py` (defaults)
  - `src/scavengarr/logging/setup.py` (configure_logging; minimal, config-driven)
- Affected entrypoints:
  - CLI start function (current Poetry script entry) MUST call `load_config()` before doing anything else.
- Tests:
  - Unit tests for precedence, validation, and redaction behavior.

## Non-Goals
- No Redis configuration (explicitly excluded for now).
- No implementation of scraping engines or plugin execution (handled in separate changes).
- No config hot-reload (restart required).
