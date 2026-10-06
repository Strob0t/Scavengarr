## Context
Scavengarr will support multiple execution paths (CLI, FastAPI), multiple scraping backends (httpx for static pages, Playwright for JS-heavy pages), and a plugin system that loads Python plugins (YAML plugins were planned at the time and later removed in `42fced9`). A single, typed configuration object is required to keep behavior consistent and testable.

## Goals / Non-Goals
### Goals
- Deterministic precedence (no surprises).
- Strong validation early (fail-fast on bad config).
- Minimal side effects: loading config must not create directories, write files, or start network activity.
- Works well for local dev (`.env`) and production (env vars).
- Redaction built in (security baseline) — not implemented yet.

### Non-Goals
- Redis support (later added as `cache.backend: redis`).
- Hot reload.
- Persisting config back to disk by default.

## Decisions
### Decision 1: Pydantic Settings as backbone
Pydantic Settings is chosen to provide typed parsing + env var integration and good error messages for invalid config.

### Decision 2: YAML config is optional
YAML is convenient for “stable” setups (docker-compose), but env vars remain the production-friendly option.

### Decision 3: Config loading is pure
`load_config()` must be referentially transparent: same inputs => same output, no IO beyond reading config files and environment.

### Decision 4: Explicit redaction
Even though we have no secrets today, the config system must assume secrets will be added (cookies, login credentials for plugins). Redaction is a first-class utility so logs never leak. Status: `redact_config_for_logging()` has not been implemented.

## Risks / Trade-offs
- More upfront code than ad-hoc env parsing, but it pays back immediately in tests and stable behavior.
- YAML adds another input surface; mitigated by safe_load + strict schema validation.

## Open Questions
- Should `log_format` default differ by `environment` automatically, or be explicit always?
  - Resolved: automatic default (dev=console, prod=json), still overridable via `logging.format` / `--log-format` (`AppConfig._derive_defaults`).
