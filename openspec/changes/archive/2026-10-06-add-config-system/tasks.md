## 1. Dependencies

- [x] 1.1 Add runtime deps (if missing) to `pyproject.toml`:
  - [x] `pydantic-settings`
  - [x] `python-dotenv`
  - [x] CLI: stdlib `argparse` (no `typer` dependency)
  - [x] `structlog` (logging)
- [x] 1.2 Add dev deps (if missing):
  - [x] `pytest`
  - [x] `pytest-asyncio` (if async tests are needed later; optional for pure config tests)
  - [x] `pytest-cov`

## 2. Config Schema

- [x] 2.1 Create `src/scavengarr/infrastructure/config/defaults.py` defining default values (pure constants, `DEFAULT_CONFIG`).
- [x] 2.2 Create `src/scavengarr/infrastructure/config/schema.py` with `AppConfig` (Pydantic model) and `EnvOverrides` (Pydantic Settings):
  - [x] General:
    - [x] `app_name: str` (default "scavengarr")
    - [x] `environment: Literal["dev","test","prod"]` (default "dev")
  - [x] Plugins:
    - [x] `plugin_dir: Path` (default "./plugins")
  - [x] HTTP (httpx plugins):
    - [x] `http_timeout_seconds: float` (default 30)
    - [x] `http_follow_redirects: bool` (default True)
    - [x] `http_user_agent: str` (default "Scavengarr/0.1.0 (+https://github.com/Strob0t/Scavengarr)")
  - [x] Playwright:
    - [x] `playwright_headless: bool` (default True)
    - [x] `playwright_timeout_ms: int` (default 30_000)
  - [x] Logging:
    - [x] `log_level: Literal["DEBUG","INFO","WARNING","ERROR"]` (default "INFO")
    - [x] `log_format: Literal["json","console"]` (default "console" in dev, "json" in prod)
  - [x] Cache (disk-only placeholder):
    - [x] `cache_dir: Path` (default "./.cache/scavengarr")
    - [x] `cache_ttl_seconds: int` (default 3600)
- [x] 2.3 Ensure strict validation:
  - [x] timeouts > 0
  - [x] TTL >= 0
  - [x] directories are normalized (absolute optional), but never auto-created here (avoid side effects)

## 3. Load Logic

- [x] 3.1 Create `src/scavengarr/infrastructure/config/load.py` exposing `load_config(...) -> AppConfig`:
  - [x] Inputs:
    - [x] `config_path: Path | None` (optional YAML)
    - [x] `dotenv_path: Path | None` (optional `.env`)
    - [x] `cli_overrides: dict[str, Any]` (already parsed flags)
  - [x] Precedence:
    - [x] defaults < YAML file < env vars < cli overrides
  - [x] Parse YAML safely using `yaml.safe_load` (no object constructors).
  - [x] Load `.env` (if given) before reading env vars.
  - [x] Return validated `AppConfig`.
- [x] 3.2 Define environment variable mapping (prefix `SCAVENGARR_`, `EnvOverrides` in `schema.py`):
  - [x] Examples:
    - [x] `SCAVENGARR_PLUGIN_DIR`
    - [x] `SCAVENGARR_HTTP_TIMEOUT_SECONDS`
    - [x] `SCAVENGARR_PLAYWRIGHT_HEADLESS`
    - [x] `SCAVENGARR_LOG_LEVEL`
- [ ] 3.3 Add a helper `redact_config_for_logging(AppConfig) -> dict` (not implemented):
  - [ ] MUST never include secrets in plaintext (even if later added).

## 4. CLI Integration

- [x] 4.1 Ensure the CLI entrypoint (`src/scavengarr/interfaces/cli/__main__.py:start`) calls `load_config()` exactly once at start.
- [x] 4.2 Add CLI flags (do not implement business logic; only wiring):
  - [x] `--config PATH` (YAML config; fallback: `SCAVENGARR_CONFIG` env var)
  - [x] `--dotenv PATH` (optional)
  - [x] `--plugin-dir PATH`
  - [x] `--log-level LEVEL`
  - [x] `--log-format json|console`
- [ ] 4.3 Print a minimal “effective config” view in debug mode ONLY (and redacted) — not implemented.

## 5. Logging Setup (minimal, config-driven)

- [x] 5.1 Create `src/scavengarr/infrastructure/logging/setup.py` with `configure_logging(config: AppConfig) -> None`:
  - [x] Use `structlog`
  - [x] Console renderer in dev; JSON renderer in prod (based on config)
  - [x] Ensure Uvicorn/FastAPI logs are compatible (no duplicate log lines)
- [x] 5.2 Call `configure_logging()` right after `load_config()` in the CLI entrypoint.

## 6. Tests

- [x] 6.1 Precedence tests (implemented in `tests/integration/test_config_loading.py` instead of `tests/unit/config/test_load_precedence.py`):
  - [x] defaults only
  - [x] YAML overrides defaults
  - [x] env overrides YAML
  - [x] CLI overrides env
- [ ] 6.2 Validation tests (`tests/unit/config/test_validation.py` does not exist; behavior verified manually):
  - [ ] invalid timeout (<=0) rejected
  - [ ] invalid log level rejected
  - [ ] invalid env value types rejected (e.g. "abc" for timeout)
- [ ] 6.3 Redaction tests (`tests/unit/config/test_redaction.py`; blocked by 3.3):
  - [ ] `redact_config_for_logging()` does not leak secrets (prepare a fake secret field inside a test-only model or dict)
- [ ] 6.4 Add test helper fixtures:
  - [x] Temporary YAML config file writer (`yaml_config` fixture / `tmp_path`)
  - [ ] Temporary `.env` file writer
  - [x] Environment isolation (pytest `monkeypatch`)

## 7. Docs

- [x] 7.1 Add `docs/features/configuration.md` documenting:
  - [x] config precedence
  - [x] env var names
  - [x] example `config.yaml`
  - [x] example `.env`
- [x] 7.2 Update README with minimal config quickstart (link to docs).

## 8. Validation

- [ ] 8.1 Run `openspec validate add-config-system --strict --no-interactive`
- [x] 8.2 Ensure every requirement has at least one `#### Scenario:`
