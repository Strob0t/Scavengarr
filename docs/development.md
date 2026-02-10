# Development Guide

## Prerequisites

- Python 3.12+
- [Poetry](https://python-poetry.org/)
- Docker (for dev container or services)
- Git

## Setup

### Dev Container (Recommended)

1. Install VS Code + the **Dev Containers** extension
2. Clone the repository
3. Open in VS Code and run: **Dev Containers: Reopen in Container**
4. The setup script installs all dependencies automatically

### Local Setup

```bash
# Install dependencies
poetry install

# Install pre-commit hooks
poetry run pre-commit install

# Start the server
poetry run start --host 0.0.0.0 --port 7979 --log-level DEBUG
```

## Project Structure

```
src/scavengarr/
  domain/          # Entities, value objects, protocols (framework-free)
  application/     # Use cases, factories
  infrastructure/  # Adapters: scraping, cache, plugins, config, logging
  interfaces/      # FastAPI router, CLI, composition root

tests/
  unit/
    domain/        # Domain entity tests
    application/   # Use case tests (mocked ports)
    infrastructure/# Adapter tests (mocked dependencies)
  integration/     # (future) End-to-end with HTTP mocking
  e2e/             # (future) Real plugin fixtures

plugins/           # YAML/Python plugin files
docs/              # This documentation
```

## Running Tests

```bash
# Run all tests
poetry run pytest

# Run with verbose output
poetry run pytest -v

# Run specific test file
poetry run pytest tests/unit/domain/test_crawljob.py

# Run with coverage
poetry run pytest --cov
```

Tests use `pytest-asyncio` with `asyncio_mode = "auto"`, so async test methods
are detected automatically without the `@pytest.mark.asyncio` decorator.

### Test Organization

- **Domain tests:** Pure unit tests, no mocks needed
- **Application tests:** Use `MagicMock` for sync ports, `AsyncMock` for async ports
- **Infrastructure tests:** Mock external dependencies (HTTP, cache)

Key fixture pattern from `tests/conftest.py`:

```python
# Synchronous port -> MagicMock
@pytest.fixture()
def mock_plugin_registry() -> MagicMock:
    registry = MagicMock()
    registry.list_names.return_value = ["filmpalast"]
    registry.get.return_value = fake_plugin
    return registry

# Asynchronous port -> AsyncMock
@pytest.fixture()
def mock_search_engine() -> AsyncMock:
    engine = AsyncMock()
    engine.search = AsyncMock(return_value=[])
    return engine
```

## Code Quality

### Linting and Formatting

```bash
# Run all pre-commit hooks
poetry run pre-commit run --all-files

# Ruff lint only
poetry run ruff check .

# Ruff format only
poetry run ruff format .
```

Pre-commit hooks (in order):
1. `end-of-file-fixer` - Ensure trailing newline
2. `trailing-whitespace` - Remove trailing whitespace
3. `check-yaml` - Validate YAML syntax
4. `ruff check --select I` - Import sorting
5. `ruff check --fix` - Linting (E, F, W, C90, I rules)
6. `ruff format` - Code formatting

### Ruff Configuration

```toml
[tool.ruff]
target-version = "py312"
line-length = 88

[tool.ruff.lint]
select = ["E", "F", "W", "C90", "I"]
```

## Coding Standards

### Typing

Always use modern Python 3.10+ type syntax:

```python
from __future__ import annotations

# Correct
def process(items: list[str], default: int | None = None) -> dict[str, int]:
    ...

# Wrong - do not use
from typing import List, Optional, Dict
def process(items: List[str], default: Optional[int] = None) -> Dict[str, int]:
    ...
```

Import from `typing` only: `Any`, `Protocol`, `Literal`, `TypeVar`, `runtime_checkable`

### Protocols over ABC

Use `typing.Protocol` for port definitions instead of `abc.ABC`:

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class CachePort(Protocol):
    async def get(self, key: str) -> bytes | None: ...
    async def set(self, key: str, value: bytes, *, ttl: int | None = None) -> None: ...
```

### Async/Await

All I/O operations must be non-blocking:

```python
# Correct: parallel execution
tasks = [fetch(url) for url in urls]
results = await asyncio.gather(*tasks)

# Wrong: sequential blocking
results = []
for url in urls:
    results.append(await fetch(url))
```

## Branch Workflow

| Branch | Purpose |
|--------|---------|
| `staging` | Development (all commits go here) |
| `main` | Production (merge via PR only) |

### Commit Process

```bash
# 1. Run quality checks
poetry run pre-commit run --all-files
poetry run pytest

# 2. Stage and commit
git add <files>
git commit -m "description"

# 3. Push
git push origin staging
```

Rules:
- Never commit to `main` directly
- Run pre-commit and tests before every commit
- Make small, atomic commits
- Push after each commit

### Merge to Main

Only when explicitly requested:

1. Bump version in `pyproject.toml` (PATCH +1 by default)
2. Update changelog
3. Commit and push to `staging`
4. Create and merge PR via `gh pr create --base main --head staging`

## Dependencies

### Core

| Package | Purpose |
|---------|---------|
| `fastapi` | HTTP framework |
| `uvicorn` | ASGI server |
| `httpx` | Async HTTP client |
| `scrapy` | Web scraping (static HTML) |
| `playwright` | Browser automation (JS-heavy sites) |
| `structlog` | Structured logging |
| `diskcache` | SQLite-based cache |
| `redis` | Redis cache backend (optional) |
| `typer` | CLI framework |
| `pydantic-settings` | Configuration management |
| `python-dotenv` | `.env` file loading |

### Dev

| Package | Purpose |
|---------|---------|
| `pytest` | Test framework |
| `pytest-asyncio` | Async test support |
| `pytest-mock` | Mocking utilities |
| `pytest-cov` | Coverage reporting |
| `respx` | httpx response mocking |
| `ruff` | Linter + formatter |
| `pre-commit` | Git hook management |
