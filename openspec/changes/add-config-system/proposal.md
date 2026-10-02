# Change: Add Configuration System

> **Status (2026-09-28):** Implemented, except the redaction helper (`redact_config_for_logging()`), the effective-config debug print and wrapped YAML errors (invalid YAML raises `yaml.YAMLError` directly). Code lives in `src/scavengarr/infrastructure/config/` and `src/scavengarr/infrastructure/logging/setup.py`; tests in `tests/integration/test_config_loading.py`.

## Why

Scavengarr needs a consistent configuration system to run reproducibly across local development, Docker, and later production deployments. Configuration must cover plugin discovery, HTTP/scraping defaults (httpx + Playwright), FastAPI server settings, and logging behavior.

Without a typed config, every subsequent change (plugin loader, engines, API) risks hidden implicit defaults and unclear precedence rules.

## What Changes

- Add a typed application configuration model (`AppConfig`, a Pydantic model) plus `EnvOverrides` (Pydantic Settings, prefix `SCAVENGARR_`) for environment variables.
- Add deterministic config precedence: CLI args > environment variables > YAML config file > defaults.
- Add `.env` support for local development convenience (`--dotenv PATH`; values are loaded into the environment without overriding existing variables).
- Add a single `load_config()` entrypoint used by the CLI, which passes the config to the FastAPI app factory.
- Add safe handling of secrets: secrets are never logged in plaintext (redaction helper not implemented yet).
- Provide a minimal default config surface that matches near-term roadmap:
  - Plugin directory
  - HTTP client defaults for httpx plugins
  - Playwright defaults (timeouts, headless)
  - Logging config (level, json vs console)
  - Cache settings placeholder (disk-only; Redis explicitly out of scope — later added as `cache.backend: redis`)

## Impact

- Affected specs: new capability `configuration`
- Affected code (new modules):
  - `src/scavengarr/infrastructure/config/schema.py` (`AppConfig`, `EnvOverrides`)
  - `src/scavengarr/infrastructure/config/load.py` (`load_config`)
  - `src/scavengarr/infrastructure/config/defaults.py` (`DEFAULT_CONFIG`)
  - `src/scavengarr/infrastructure/logging/setup.py` (`configure_logging`; minimal, config-driven)
- Affected entrypoints:
  - CLI start function (`scavengarr.interfaces.cli:start`, Poetry script `start`) MUST call `load_config()` before doing anything else.
- Tests:
  - Tests for precedence, validation, and redaction behavior (implemented: precedence in `tests/integration/test_config_loading.py`).

## Non-Goals

- No Redis configuration (explicitly excluded for now; later added via `cache.backend`).
- No implementation of scraping engines or plugin execution (handled in separate changes).
- No config hot-reload (restart required).
