# OpenSpec Instructions

These instructions are for AI assistants working in this project.

Always open `@/openspec/AGENTS.md` when the request:
- Mentions planning or proposals (words like proposal, spec, change, plan)
- Introduces new capabilities, breaking changes, architecture shifts, or big performance/security work
- Sounds ambiguous and you need the authoritative spec before coding

Use `@/openspec/AGENTS.md` to learn:
- How to create and apply change proposals
- Spec format and conventions
- Project structure and guidelines

Keep this managed block so 'openspec update' can refresh the instructions.

<!-- OPENSPEC:END -->

***

# Agent Instructions for Scavengarr

## Repository Context
- **Repository**: `Strob0t/Scavengarr`
- **Core Stack**: Python 3.12, FastAPI, Scrapy, Playwright, `diskcache` (Redis optional/future), Docker, Poetry
- **Python Version**: Always use **Python 3.12** for development and production. The `pyproject.toml` enforces this via `requires-python = ">=3.12"`.
- **Tooling**: `ruff` (lint+format), `mypy`, `pytest`, `pre-commit`, `structlog`
- **Critical Documentation**:
  - 📖 **Read `README.md`** first for project overview, setup, and usage.
  - 🏗️ **Read `CLAUDE.md`** before making any design‑level changes (updated 2026-01-25).
  - 📋 **Check `openspec/changes/<change-id>/`** when implementing features – OpenSpec changes are the single source of truth.

***

## OpenSpec Workflow (CRITICAL)

### When to Read OpenSpec Changes
**ALWAYS** read `openspec/changes/<change-id>/` **BEFORE** writing code when:
1. User mentions a change name (e.g., "implement add-config-system")
2. Request introduces new capabilities (plugin system, config loading, engines)
3. Request modifies core architecture (new modules, new dependencies)
4. Request is ambiguous and you need authoritative requirements

### OpenSpec Change Structure
Each change has 4 files:
```
openspec/changes/<change-id>/
├── proposal.md         # Why, What, Impact (read first)
├── tasks.md            # Implementation checklist (your TODO)
├── design.md           # Architectural decisions, trade-offs
└── specs/<capability>/spec.md  # BDD scenarios (acceptance criteria)
```

### Implementation Rules
1. **Read `proposal.md` → `tasks.md` → `design.md` → `spec.md`** in that order
2. **Tasks are your implementation contract** – check off tasks as you complete them
3. **Every scenario in `spec.md` MUST have a corresponding test** (test filename should reference scenario)
4. **Never invent assumptions** – if info is missing, ask user or check `design.md` for defaults
5. **Validate before PR**: Run `openspec validate <change-id> --strict --no-interactive`

### Known Contracts (Canonical Decisions)
These decisions are **non-negotiable** across all changes:

| Contract | Value | Source |
|----------|-------|--------|
| **Entry Point** | `poetry run scavengarr` (CLI via Typer at `src/scavengarr/application/cli.py:start`) | `pyproject.toml` `[tool.poetry.scripts]` |
| **Config Prefix** | `SCAVENGARR_` (all env vars) | `add-config-system` change |
| **Plugin Directory** | Configurable via `SCAVENGARR_PLUGIN_DIR` (default: `./plugins`) | `add-plugin-loader` change |
| **Plugin Formats** | YAML (declarative) + Python (imperative) | `add-plugin-loader` change |
| **Scraping Engines** | ScrapyEngine (httpx + parsel), PlaywrightEngine (future) | `add-scrapy-engine` change |
| **Cache Backend** | `diskcache` (default), Redis optional (future) | `add-config-system` design decision |
| **Logging** | `structlog` with JSON (prod) or console (dev) output | `add-config-system` change |
| **Config Precedence** | CLI args > ENV vars > YAML file > `.env` > defaults | `add-config-system` spec |
| **Python Version** | 3.12 only (no 3.11, no 3.13+) | `pyproject.toml` |

***

## Development Workflow

### Key Commands
| Task | Command | Description |
|------|---------|-------------|
| **Setup** | `poetry install` | Install dependencies in a virtual environment. |
| **Run API** | `poetry run scavengarr` | Starts the FastAPI server (Unified mode). |
| **Run with custom config** | `poetry run scavengarr --config custom.yaml --log-level DEBUG` | Load custom config and set log level. |
| **Run Worker** | `SCAVENGARR_WORKER_URL=http://localhost:8000 poetry run scavengarr --worker` | Starts the scraper worker (Distributed mode, future). |
| **Lint** | `poetry run ruff check .` | Static analysis and style enforcement. |
| **Auto‑fix** | `poetry run ruff format .` | Automatic code formatting (PEP 8 compliant). |
| **Type‑check** | `poetry run mypy src/` | Run static type checking (must pass before PR). |
| **Test All** | `poetry run pytest` | Run the entire test suite (must pass before PR). |
| **Test Single** | `poetry run pytest tests/unit/config/test_load.py` | Run a specific test file. |
| **Test with Coverage** | `poetry run pytest --cov=src --cov-report=term-missing` | Run tests with coverage report. |
| **Validate OpenSpec** | `openspec validate <change-id> --strict --no-interactive` | Validate change before implementation. |

### Git Workflow
- **Branch naming**: `<type>/<issue>-<short‑desc>` (e.g., `feat/add-config-system`, `fix/123-plugin-crash`)
- **Pre‑commit hooks**: Managed by `pre‑commit` – runs `ruff check`, `ruff format`, `mypy`, and `pytest`
  - **CRITICAL**: **Never** skip the hook (`--no-verify`) unless build is broken by external cause
  - If pre-commit fails, **fix the issue** before committing
- **Security**: Never commit secrets, `.env` files, `config.yaml` with real credentials, or API keys

### Commit Messages
Enforced by `commitlint` (configured in `pyproject.toml`).
- **Format**: `<type>(<scope>): <subject>` (scope optional but recommended)
- **Allowed types**: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`
- **Subject rules**:
  - Lower‑case only
  - No trailing period
  - ≤ 100 characters
- **Examples**:
  - `feat(config): add pydantic settings loader`
  - `fix(plugins): handle missing plugin.py export gracefully`
  - `test(engines): add scrapy engine CSS selector tests`

***

## Code Style & Conventions

### Python
- **Formatter**: `ruff format` (line length 88, Black-compatible)
- **Linter**: `ruff check` – **no `# noqa`** unless absolutely necessary (document why if used)
- **Import order**: `isort`-style – standard library → third‑party → local imports, one blank line between groups
- **Naming**:
  - Classes & Exceptions: `PascalCase` (e.g., `PluginRegistry`, `NetworkError`)
  - Functions, methods, variables: `snake_case` (e.g., `load_config`, `plugin_dir`)
  - Constants: `UPPER_SNAKE_CASE` (e.g., `DEFAULT_TIMEOUT`, `CACHE_TTL`)
- **Type hints**:
  - Use full type annotations **everywhere** (functions, methods, class attributes)
  - Prefer PEP 585 (`list[str]`) over typing module (`List[str]`)
  - Use `from __future__ import annotations` for forward references
- **Docstrings**:
  - Google style, mandatory for all **public** modules, classes, functions, and methods
  - Include examples for complex functions (see `add-plugin-loader` tasks)
- **Error handling**:
  - Catch exceptions at API/CLI boundaries, log with `structlog.get_logger().error()`, re‑raise or return HTTP error
  - Never silently swallow exceptions (`except: pass` is forbidden)

### Example (Good Style)
```python
from __future__ import annotations

import structlog
from pathlib import Path
from pydantic import BaseModel

logger = structlog.get_logger()

class PluginDefinition(BaseModel):
    """YAML plugin schema definition.

    Attributes:
        name: Plugin identifier (lowercase, alphanumeric + hyphens).
        base_url: Site base URL for scraping.
    """
    name: str
    base_url: str

def load_yaml_plugin(path: Path) -> PluginDefinition:
    """Load and validate a YAML plugin file.

    Args:
        path: Absolute path to .yaml file.

    Returns:
        Validated PluginDefinition instance.

    Raises:
        PluginLoadError: If file is missing or malformed.
        PluginValidationError: If schema validation fails.

    Example:
        >>> plugin = load_yaml_plugin(Path("./plugins/1337x.yaml"))
        >>> plugin.name
        '1337x'
    """
    logger.info("loading_plugin", file=str(path))
    # Implementation...
```

***

## Plugin System (Updated per `add-plugin-loader`)

### Plugin Discovery
- Plugins are discovered from the directory specified by `SCAVENGARR_PLUGIN_DIR` (default: `./plugins`)
- **Supported formats**: `.yaml` (declarative) and `.py` (imperative)
- Discovery happens at **application startup** via `PluginRegistry.discover()`
- Plugins are **lazy-loaded** (parsed only on first access via `PluginRegistry.get(name)`)

### YAML Plugins (Declarative)
- **Location**: Any `.yaml` file in plugin directory (e.g., `plugins/1337x.yaml`)
- **Schema**: Validated against `PluginDefinition` Pydantic model (see `src/scavengarr/plugins/schema.py`)
- **Required fields**: `name`, `description`, `version`, `author`, `base_url`, `scraping` (with `mode`, `selectors`/`locators`)
- **Modes**:
  - `scrapy`: Static HTML scraping (CSS selectors)
  - `playwright`: JavaScript-rendered sites (Playwright locators, future)

### Python Plugins (Imperative)
- **Location**: Any `.py` file in plugin directory (e.g., `plugins/my_gully.py`)
- **Protocol**: Must export a `plugin` variable with an `async def search(query: str, category: int | None) -> list[SearchResult]` method
- **Validation**: At load-time, checks for `plugin` export and `search` method (duck-typing via `hasattr`)
- **Dependencies**: Any third-party libs must be declared in `pyproject.toml` `[tool.poetry.dependencies]` and installed via `poetry install`

### Plugin Registry API
```python
from scavengarr.domain.plugins import PluginRegistry, PluginNotFoundError

# Initialize (done once at app startup)
registry = PluginRegistry(plugin_dir=Path("./plugins"))
registry.discover()

# Get plugin by name (lazy-loads if needed)
plugin = registry.get("1337x")  # PluginDefinition | object

# List all plugin names
names = registry.list_names()  # ['1337x', 'rarbg', 'my-gully']

# Filter by mode (YAML only)
scrapy_plugins = registry.get_by_mode("scrapy")  # [PluginDefinition, ...]
```

***

## Configuration System (Updated per `add-config-system`)

### Config Loading (Single Entrypoint)
**CRITICAL**: `load_config()` is called **exactly once** at application startup in `src/scavengarr/application/cli.py:start`.

```python
from scavengarr.config.load import load_config

config = load_config(
    config_path=Path("./config.yaml"),  # Optional YAML file
    dotenv_path=Path("./.env"),         # Optional .env file
    cli_overrides={"log_level": "DEBUG"}  # From CLI flags
)
```

### Config Precedence (Highest → Lowest)
1. **CLI arguments** (`--config`, `--plugin-dir`, `--log-level`, etc.)
2. **Environment variables** (`SCAVENGARR_PLUGIN_DIR`, `SCAVENGARR_LOG_LEVEL`, etc.)
3. **YAML config file** (`config.yaml`, path via `--config`)
4. **`.env` file** (loaded via `python-dotenv`)
5. **Built-in defaults** (`src/scavengarr/config/defaults.py`)

### Environment Variable Naming
All env vars use the `SCAVENGARR_` prefix:
- `SCAVENGARR_PLUGIN_DIR` → `AppConfig.plugin_dir`
- `SCAVENGARR_HTTP_TIMEOUT_SECONDS` → `AppConfig.http_timeout_seconds`
- `SCAVENGARR_LOG_LEVEL` → `AppConfig.log_level`
- `SCAVENGARR_CACHE_DIR` → `AppConfig.cache_dir`

### Config Schema (MVP Fields)
See `src/scavengarr/config/schema.py:AppConfig` for canonical fields:
- `app_name: str`
- `environment: Literal["dev", "test", "prod"]`
- `plugin_dir: Path`
- `http_timeout_seconds: float`
- `http_user_agent: str`
- `playwright_headless: bool` (future)
- `log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"]`
- `log_format: Literal["json", "console"]`
- `cache_dir: Path`
- `cache_ttl_seconds: int`

### Config Best Practices
- **No side effects**: `load_config()` never creates directories, writes files, or starts network activity
- **Secrets redaction**: Use `redact_config_for_logging(config)` before logging config (never log secrets in plaintext)
- **Validation**: All fields are validated via Pydantic (timeouts > 0, paths normalized, enums strict)

***

## Scraping Engines (Updated per `add-scrapy-engine`)

### ScrapyEngine (Static HTML)
- **Purpose**: Fast scraping for static HTML pages (no JavaScript execution)
- **Dependencies**: `httpx` (async HTTP client), `parsel` (CSS selector extraction)
- **Input**: YAML plugin with `scraping.mode: "scrapy"`
- **Process**:
  1. Build search URL from `base_url` + `search_path.format(query=...)`
  2. HTTP GET with `httpx.AsyncClient()` (30s timeout, custom User-Agent)
  3. Parse HTML with `parsel.Selector(text=response.text)`
  4. Extract fields using CSS selectors (`selectors.title`, `selectors.download_link`, etc.)
  5. Return `list[SearchResult]`

### PlaywrightEngine (JavaScript-rendered, Future)
- **Purpose**: Scraping for sites requiring JavaScript execution (e.g., infinite scroll, dynamic content)
- **Status**: Future change (`add-playwright-engine`)

***

## Documentation Guidelines

### File Targets
- **`README.md`**: User‑facing (installation, configuration, usage examples)
- **`CLAUDE.md`**: Developer‑facing (system design, component interaction, OpenSpec integration)
- **`AGENTS.md`** (this file): AI assistant instructions (contracts, workflows, quality gates)
- **`docs/*.md`**: In‑depth technical write‑ups for subsystems (e.g., plugin validation, caching strategy)

### Writing Principles
- **Tone**: Declarative, present tense ("The system loads plugins..." not "The system will load...")
- **Focus**: Explain **what** the component does and **why**, not what it doesn't do
- **Diagrams**: Use Mermaid for workflows and state machines; keep titles plain (no extra markdown)
- **Examples**: Include code examples for complex APIs (plugin loading, config precedence)

### When to Update Docs
- **README.md**: When user-facing behavior changes (new CLI flags, config options, setup steps)
- **CLAUDE.md**: When new modules/components are added, or core architecture changes
- **AGENTS.md**: When canonical contracts change (new entry point, new env var prefix, new dependency)

***

## Logging Strategy (Updated per `add-config-system`)

### Logging Framework
- **Library**: `structlog` (structured logging with context fields)
- **Output formats**:
  - **Console** (dev): Human-readable with colors (`log_format: "console"`)
  - **JSON** (prod): Machine-parseable for log aggregation (`log_format: "json"`)
- **Log levels**: `DEBUG`, `INFO`, `WARNING`, `ERROR` (configured via `SCAVENGARR_LOG_LEVEL`)

### What to Log
- **User-visible output**: Use `typer.echo()` for CLI, FastAPI response messages for HTTP (not logs)
- **Application events** (`INFO`): Plugin loaded, scraping started/completed, config loaded
  - **Always include context**: `plugin_name`, `query`, `results_count`, `duration_ms`
- **Debug** (`DEBUG`): Selector matching details, HTTP request/response bodies, plugin discovery paths
- **Warnings** (`WARNING`): Selector no match, missing optional fields, slow requests (>5s)
- **Errors** (`ERROR`): Plugin load failures, network errors, parsing errors, validation failures

### Example (Structured Logging)
```python
import structlog

logger = structlog.get_logger()

# Good: Structured context
logger.info("scraping_completed",
    plugin_name="1337x",
    query="ubuntu",
    results_count=25,
    duration_ms=342
)

# Bad: String interpolation
logger.info(f"Scraping completed for 1337x with query ubuntu, got 25 results in 342ms")
```

### Secrets Redaction (CRITICAL)
- **Never log secrets in plaintext**: passwords, API keys, cookies, tokens
- Use `redact_config_for_logging(config)` before logging config objects
- For plugin auth, log `auth_type` but never `username`, `password`, `cookie_value`

***

## Testing Approach (Updated TDD Requirements)

### Test-Driven Development (TDD)
When implementing an OpenSpec change:
1. **Read scenarios in `spec.md`** (e.g., "Scenario: Defaults only")
2. **Write failing test first** (RED) – test should reference the scenario in its name
3. **Implement minimal code** (GREEN) to make test pass
4. **Refactor** (REFACTOR) while keeping tests green

### Test Layers
1. **Unit Tests** (`tests/unit/`) – Test individual components in isolation
   - Config loading, precedence, validation
   - Plugin loading (YAML parsing, Python imports, protocol validation)
   - Engine logic (URL building, selector extraction, error handling)
   - **Coverage target**: 80%+ for core modules
2. **Integration Tests** (`tests/integration/`) – Test component interactions
   - Plugin → Engine → Results
   - FastAPI routes → SearchService → TorznabRenderer
   - **Coverage target**: 60%+
3. **End-to-End Tests** (`tests/e2e/`, future) – Test full request flow with real plugins

### Test File Naming
- Mirror source structure: `src/scavengarr/config/load.py` → `tests/unit/config/test_load.py`
- Test name should reference scenario: `test_defaults_only_loads_builtin_config()`

### Test Fixtures
- **Temporary files**: Use `pytest` `tmp_path` fixture for YAML configs, `.env` files
- **Environment isolation**: Use `monkeypatch` to set/unset env vars
- **Sample data**: Store in `tests/fixtures/` (HTML files, valid/invalid plugins)

### Example (Good Test)
```python
import pytest
from pathlib import Path
from scavengarr.config.load import load_config

def test_defaults_only_loads_builtin_config(tmp_path, monkeypatch):
    """Scenario: Defaults only (add-config-system spec.md)

    WHEN no CLI args, env vars, YAML, or .env are provided
    THEN the system uses built-in defaults for all config fields
    """
    # Arrange: Clear all env vars
    monkeypatch.delenv("SCAVENGARR_PLUGIN_DIR", raising=False)

    # Act
    config = load_config(config_path=None, dotenv_path=None, cli_overrides={})

    # Assert
    assert config.plugin_dir == Path("./plugins")
    assert config.log_level == "INFO"
    assert config.http_timeout_seconds == 30
```

### Quality Gates (Must Pass Before PR)
1. ✅ `poetry run ruff check .` – No linting errors
2. ✅ `poetry run ruff format --check .` – Code is formatted
3. ✅ `poetry run mypy src/` – No type errors
4. ✅ `poetry run pytest` – All tests pass
5. ✅ `poetry run pytest --cov=src --cov-report=term-missing` – 80%+ coverage for changed modules
6. ✅ `openspec validate <change-id> --strict --no-interactive` – Change is valid

***

## OpenSpec Change Lifecycle (Detailed)

### Phase 1: Understanding (Before Coding)
1. **Read `proposal.md`**: Understand **why** the change exists, **what** it changes, **impact** on other components
2. **Read `tasks.md`**: Identify all tasks, note dependencies (blocked tasks), understand order
3. **Read `design.md`**: Understand architectural decisions, trade-offs, open questions
4. **Read `spec.md`**: Identify all scenarios (these become tests), note acceptance criteria

### Phase 2: Implementation (TDD Cycle)
For each task in `tasks.md`:
1. **Find related scenarios** in `spec.md`
2. **Write failing tests** for all scenarios (RED)
3. **Implement minimal code** to pass tests (GREEN)
4. **Refactor** code while keeping tests green (REFACTOR)
5. **Check off task** in `tasks.md` (add `[x]`)
6. **Update docs** if task modifies contracts (CLAUDE.md, README.md, AGENTS.md)

### Phase 3: Validation (Before PR)
1. Run all quality gates (ruff, mypy, pytest, coverage)
2. Run `openspec validate <change-id> --strict --no-interactive`
3. Verify all tasks in `tasks.md` are checked off
4. Verify every scenario in `spec.md` has a corresponding test

### Phase 4: Documentation (After Implementation)
1. Update `CLAUDE.md` if new components were added
2. Update `AGENTS.md` if canonical contracts changed
3. Update `README.md` if user-facing behavior changed
4. Add inline code comments for complex logic (not obvious logic)

***

## Common Pitfalls (AVOID THESE)

### ❌ Inventing Information
- **DON'T**: Assume file paths, module names, function signatures not in OpenSpec change
- **DO**: Ask user or check `design.md` for defaults

### ❌ Skipping Tests
- **DON'T**: Write code without corresponding tests
- **DO**: Write test first (TDD), ensure scenario coverage

### ❌ Changing Canonical Contracts
- **DON'T**: Change env var prefix, entry point, config precedence without OpenSpec change
- **DO**: Propose a new change if contract needs to change

### ❌ Side Effects in Pure Functions
- **DON'T**: Create directories, write files, or start network activity in `load_config()`, `load_plugin()`
- **DO**: Keep load functions pure (same input → same output, no I/O beyond reading)

### ❌ Silent Failures
- **DON'T**: `except: pass` or swallow exceptions
- **DO**: Log with `logger.error()`, raise custom exception with context

### ❌ Hardcoded Values
- **DON'T**: Hardcode timeouts, paths, URLs in business logic
- **DO**: Use config values (`config.http_timeout_seconds`, `config.plugin_dir`)

***

## AI Assistant Checklist (Before Submitting Code)

Before providing code to the user, verify:

- [ ] I read the OpenSpec change (`proposal.md`, `tasks.md`, `design.md`, `spec.md`)
- [ ] My code matches the task checklist in `tasks.md`
- [ ] Every scenario in `spec.md` has a corresponding test
- [ ] My code uses canonical contracts (env prefix, entry point, precedence)
- [ ] My code is type-annotated (full `mypy` compliance)
- [ ] My code is formatted (`ruff format`)
- [ ] My code has no side effects in pure functions (`load_config`, `load_plugin`)
- [ ] My code logs structured events with `structlog` (no plaintext secrets)
- [ ] My code handles errors gracefully (custom exceptions, logging)
- [ ] I updated docs if contracts changed (CLAUDE.md, AGENTS.md, README.md)

***

**Last Updated**: 2026-01-25
**Author**: Scavengarr Team
**OpenSpec Changes Integrated**: `add-config-system`, `add-plugin-loader`, `add-scrapy-engine`

***

*This document is the single source of truth for AI assistants implementing Scavengarr features. When in doubt, refer to OpenSpec changes and this document—never invent assumptions.*
