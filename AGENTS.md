# OpenSpec Instructions

<!-- OPENSPEC:START -->
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

# Agent Instructions for Scavengarr
## Repository Context
- **Repository**: `Strob0t/Scavengarr`
- **Core Stack**: Python 3.12, FastAPI, Playwright, Redis (or diskcache), Docker, Poetry
- **Python Version**: Always use **Python 3.12** for development and production. The `pyproject.toml` enforces this via `requires-python = ">=3.12"`.
- **Tooling**: `black`, `ruff`, `isort`, `mypy`, `pytest`, `pre-commit`
- **Critical Documentation**:
  - 📖 **Read `README.md`** first for project overview, setup, and usage.
  - 🏗️ **Read `ARCHITECTURE.md`** before making any design‑level changes.

## Development Workflow
### Key Commands
| Task | Command | Description |
|------|---------|-------------|
| **Setup** | `poetry install` | Install dependencies in a virtual environment. |
| **Run API** | `poetry run scavengarr` or `poetry run python -m scavengarr.main` | Starts the FastAPI server (Unified mode). |
| **Run Worker** | `SCAVENGARR_WORKER_URL= http://localhost:8000 poetry run scavengarr --worker` | Starts the scraper worker for Distributed mode. |
| **Lint** | `poetry run ruff check .` | Static analysis and style enforcement. |
| **Auto‑fix** | `poetry run ruff format .` | Automatic code formatting (PEP 8 compliant). |
| **Type‑check** | `poetry run mypy src/` | Run static type checking. |
| **Test All** | `poetry run pytest` | Run the entire test suite. |
| **Test Single** | `poetry run pytest tests/path/to/test_file.py` | Run a specific test file. |

### Git Workflow
- **Branch naming**: `<type>/<issue>-<short‑desc>` (e.g., `feat/123-add-cache`).
- **Pre‑commit**: Managed by `pre‑commit` – runs `ruff`, `ruff format`, `mypy`, and `pytest`. **Never** skip the hook.
- **Security**: Never commit secrets, `.env` files, or any credentials.

### Commit Messages
Enforced by `commitlint` (configured in `pyproject.toml`).
- **Format**: `<type>(<scope>): <subject>` (scope optional but recommended).
- **Allowed types**: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`.
- **Subject rules**:
  - Lower‑case only.
  - No trailing period.
  - ≤ 100 characters.
- **Body/footer**: Separate from header with a blank line. No line‑length limit.

## Code Style & Conventions
### Python
- **Formatter**: `black` (line length 88).
- **Linter**: `ruff` – no `# noqa` unless absolutely necessary.
- **Import order**: `isort` – standard library → third‑party → local imports, one blank line between groups.
- **Naming**:
  - Classes & Exceptions: `PascalCase`
  - Functions, methods, variables: `snake_case`
  - Constants: `UPPER_SNAKE_CASE`
- **Type hints**: Use full type annotations everywhere. Prefer `list[str]` over `List[str]` (PEP 585).
- **Docstrings**: Google style, mandatory for all public modules, classes, functions, and methods.
- **Error handling**: Catch exceptions at API/CLI boundaries, log with `logger.error` and re‑raise or return a proper HTTP error.

## Plugin System
- Plugins can be provided **either** as **YAML** files (Cardigann‑style) **or** as **Python modules**.
- YAML plugins reside in `src/scavengarr/adapters/plugins/*.yaml` and are loaded by `PluginManager`.
- Python plugins must be importable packages placed under `src/scavengarr/adapters/plugins/` (e.g., `my_plugin/__init__.py`).
- Any additional third‑party libraries required by a Python plugin **must** be declared in the project's `pyproject.toml` under `[tool.poetry.dependencies]`.
- When a Python plugin is added, run `poetry update` to install its dependencies.

## Documentation Guidelines
### File Targets
- `README.md`: User‑facing (installation, configuration, usage).
- `ARCHITECTURE.md`: Developer‑facing (system design, component interaction).
- `docs/*.md`: In‑depth technical write‑ups for subsystems (e.g., plugin loading, caching).
### Writing Principles
- **Tone**: Declarative, present tense.
- **Focus**: Explain *what* the component does, not what it doesn’t.
- **Diagrams**: Use Mermaid for workflows and state machines. Keep diagram titles plain (no extra markdown formatting).

## Logging Strategy
- **User‑visible output**: `print`/`typer.echo` for CLI, FastAPI response messages for HTTP.
- **Application events**: `logger.info`, `logger.warning`, `logger.error` – include contextual information (plugin name, request ID, etc.).
- **Debug**: `logger.debug` – disabled by default, enable via `SCAVENGARR_LOG_LEVEL=DEBUG`.
- **Formatting**: JSON lines for log aggregation; **no emojis**.

## Testing Approach
### Philosophy
- **Behavior‑driven**: Tests verify observable behavior (HTTP responses, cache hits, plugin loading) rather than internal implementation details.
- **Layers**: End‑to‑end > Integration > Unit.
- **File layout**: Tests reside in `tests/` mirroring the source layout (`tests/unit/...`, `tests/integration/...`, `tests/e2e/...`).
### Best Practices
- **Isolation**: Each test checks a single behavior.
- **Performance**: Unit tests < 100 ms; use fixtures to share expensive setup (Playwright browser, Redis). 
- **Mocks**: Prefer real dependencies; use `pytest-mock` only when external services cannot be started.
- **CI**: Run `poetry run pytest` in CI; linting and type‑checking are separate steps.

---
*This document follows Python‑specific conventions to ensure consistency across the Scavengarr codebase.*