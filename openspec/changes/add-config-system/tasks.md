## 1. Dependencies
- [x] 1.1 Add runtime deps (if missing) to `pyproject.toml`:
  - [x] `pydantic-settings`
  - [x] `python-dotenv`
  - [x] `typer` (CLI)
  - [x] `structlog` (logging)
- [x] 1.2 Add dev deps (if missing):
  - [x] `pytest`
  - [x] `pytest-asyncio` (if async tests are needed later; optional for pure config tests)
  - [x] `pytest-cov`

## 2. Config Schema
- [ ] 2.1 Create `src/scavengarr/config/defaults.py` defining default values (pure constants).
- [ ] 2.2 Create `src/scavengarr/config/schema.py` with `AppConfig` (Pydantic Settings):
  - [ ] General:
    - [ ] `app_name: str` (default "scavengarr")
    - [ ] `environment: Literal["dev","test","prod"]` (default "dev")
  - [ ] Plugins:
    - [ ] `plugin_dir: Path` (default "./plugins")
  - [ ] HTTP (Scrapy engine):
    - [ ] `http_timeout_seconds: float` (default 30)
    - [ ] `http_follow_redirects: bool` (default True)
    - [ ] `http_user_agent: str` (default "Scavengarr/0.1.0 (+https://github.com/Strob0t/Scavengarr)")
  - [ ] Playwright:
    - [ ] `playwright_headless: bool` (default True)
    - [ ] `playwright_timeout_ms: int` (default 30_000)
  - [ ] Logging:
    - [ ] `log_level: Literal["DEBUG","INFO","WARNING","ERROR"]` (default "INFO")
    - [ ] `log_format: Literal["json","console"]` (default "console" in dev, "json" in prod)
  - [ ] Cache (disk-only placeholder):
    - [ ] `cache_dir: Path` (default "./.cache/scavengarr")
    - [ ] `cache_ttl_seconds: int` (default 3600)
- [ ] 2.3 Ensure strict validation:
  - [ ] timeouts > 0
  - [ ] TTL >= 0
  - [ ] directories are normalized (absolute optional), but never auto-created here (avoid side effects)

## 3. Load Logic
- [ ] 3.1 Create `src/scavengarr/config/load.py` exposing `load_config(...) -> AppConfig`:
  - [ ] Inputs:
    - [ ] `config_path: Path | None` (optional YAML)
    - [ ] `dotenv_path: Path | None` (optional `.env`)
    - [ ] `cli_overrides: dict[str, Any]` (already parsed flags)
  - [ ] Precedence:
    - [ ] defaults < YAML file < env vars < cli overrides
  - [ ] Parse YAML safely using `yaml.safe_load` (no object constructors).
  - [ ] Load `.env` (if present) before reading env vars.
  - [ ] Return validated `AppConfig`.
- [ ] 3.2 Define environment variable mapping (prefix `SCAVENGARR_`):
  - [ ] Examples:
    - [ ] `SCAVENGARR_PLUGIN_DIR`
    - [ ] `SCAVENGARR_HTTP_TIMEOUT_SECONDS`
    - [ ] `SCAVENGARR_PLAYWRIGHT_HEADLESS`
    - [ ] `SCAVENGARR_LOG_LEVEL`
- [ ] 3.3 Add a helper `redact_config_for_logging(AppConfig) -> dict`:
  - [ ] MUST never include secrets in plaintext (even if later added).

## 4. CLI Integration
- [ ] 4.1 Ensure the CLI entrypoint calls `load_config()` exactly once at start.
- [ ] 4.2 Add CLI flags (do not implement business logic; only wiring):
  - [ ] `--config PATH` (YAML config)
  - [ ] `--dotenv PATH` (optional)
  - [ ] `--plugin-dir PATH`
  - [ ] `--log-level LEVEL`
  - [ ] `--log-format json|console`
- [ ] 4.3 Print a minimal “effective config” view in debug mode ONLY (and redacted).

## 5. Logging Setup (minimal, config-driven)
- [ ] 5.1 Create `src/scavengarr/logging/setup.py` with `configure_logging(config: AppConfig) -> None`:
  - [ ] Use `structlog`
  - [ ] Console renderer in dev; JSON renderer in prod (based on config)
  - [ ] Ensure Uvicorn/FastAPI logs are compatible (no duplicate log lines)
- [ ] 5.2 Call `configure_logging()` right after `load_config()` in the CLI entrypoint.

## 6. Tests
- [ ] 6.1 Create `tests/unit/config/test_load_precedence.py`:
  - [ ] defaults only
  - [ ] YAML overrides defaults
  - [ ] env overrides YAML
  - [ ] CLI overrides env
- [ ] 6.2 Create `tests/unit/config/test_validation.py`:
  - [ ] invalid timeout (<=0) rejected
  - [ ] invalid log level rejected
  - [ ] invalid env value types rejected (e.g. "abc" for timeout)
- [ ] 6.3 Create `tests/unit/config/test_redaction.py`:
  - [ ] `redact_config_for_logging()` does not leak secrets (prepare a fake secret field inside a test-only model or dict)
- [ ] 6.4 Add test helper fixtures:
  - [ ] Temporary YAML config file writer
  - [ ] Temporary `.env` file writer
  - [ ] Environment isolation (pytest `monkeypatch`)

## 7. Docs
- [ ] 7.1 Add `docs/configuration.md` documenting:
  - [ ] config precedence
  - [ ] env var names
  - [ ] example `config.yaml`
  - [ ] example `.env`
- [ ] 7.2 Update README with minimal config quickstart (link to docs).

## 8. Validation
- [ ] 8.1 Run `openspec validate add-config-system --strict --no-interactive`
- [ ] 8.2 Ensure every requirement has at least one `#### Scenario:`
