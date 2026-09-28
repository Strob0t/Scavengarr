# CLAUDE.md

Instructions for developers and AI assistants working on Scavengarr. Details live in `docs/`; this file holds the rules that apply to every change.

***

## 1. Workflow (IMPORTANT!)

### Branches
- `staging`: development branch (commit here). `main`: production (merge via PR only).
- Never commit to `main`. Never merge into `main` without an explicit user request.

### Before every commit

```bash
poetry run pre-commit run --all-files
poetry run pytest
```

`poetry run pytest` excludes live tests (`addopts = -m "not live"`) and benchmarks. Live smoke tests hit real websites and run with `poetry run pytest -m live`; their failures signal broken plugins/resolvers, not a commit blocker.

Rules:
- Fix all errors before committing (warnings can be acceptable depending on the check).
- Small, atomic commits; commit after each isolated subtask, never batch unrelated changes.
- **Docs ship with the code**: if behavior, features, architecture or configuration change, update `CHANGELOG.md`, `docs/features/`, `docs/architecture/`, `docs/plans/`, `CLAUDE.md`, `README.md` or `openspec/changes/...` in the same commit.
- Push after each successful change: `git push origin staging`.
- Larger refactors: write a brief Markdown plan (problem, design, affected files, tests) first.

### Merge to main (only on explicit user request)
1. Bump the version in `pyproject.toml` (PATCH +1 unless MINOR/MAJOR is warranted).
2. Update `CHANGELOG.md` (newest entry on top with version, date, changes; current bugs under `KNOWN_ISSUES`).
3. Commit & push to `staging`.
4. `gh pr create --base main --head staging --title "..." --body "..."` then `gh pr merge --merge`.
5. Sync back: `git fetch origin && git merge origin/main && git push origin staging`.

***

## 2. Project overview

Scavengarr is a self-hosted Torznab/Newznab indexer for Prowlarr and other Arr apps, plus a Stremio addon. Plugins scrape sites with httpx (static HTML) or Playwright (JS-heavy sites); results are served via Torznab endpoints (`caps`, `search`) and Stremio streams.

Request flow: request (HTTP/CLI) → use case loads plugin from registry (lazy) → plugin runs multi-stage search (search page → detail pages → links) → links validated in parallel → optional `.crawljob` bundle → presenter renders Torznab XML.

Feature docs: `docs/features/README.md` (index). Architecture: `docs/architecture/clean-architecture.md`.

***

## 3. Clean Architecture (dependency rule)

Layers under `src/scavengarr/`, outer depends on inner only:

| Layer | Contains | Rule |
|---|---|---|
| `interfaces/` | FastAPI routers, CLI, composition root (DI wiring) | I/O only, no business rules |
| `infrastructure/` | plugins, hoster resolvers, link validation, cache, Torznab presenter, config, logging | implements domain ports |
| `application/` | use cases, factories, policies (limits, timeouts, retries) | knows ports, not adapters |
| `domain/` | entities, value objects, `Protocol` ports | framework-free, I/O-free |

Domain never imports FastAPI, httpx or diskcache.

Invariants:
- I/O dominates runtime: nothing may block the event loop. Independent URLs (detail pages, link validation) run in parallel with bounded concurrency.
- Link validation: `HEAD` with redirects first, `GET` fallback only when needed; short timeouts, semaphore-limited.
- CrawlJobs contain only validated links, in deterministic order; job IDs are stable, TTL configurable.
- Config precedence (high → low): CLI args → `SCAVENGARR_*` env → YAML → `.env` → defaults. See `docs/features/configuration.md`.
- Logging: `structlog`, structured, with context fields (`plugin`, `stage`, `duration_ms`, `results_count`); never log secrets.

***

## 4. Dependencies

- Source of truth: `pyproject.toml`. For package APIs, read the installed source in `.venv` or the official docs.
- Prefer stdlib, then established libraries, then custom code. No internal mini-frameworks. New dependencies need explicit justification.

***

## 5. Python rules (MUST READ!)

- `from __future__ import annotations` in every file.
- Modern typing only: `T | None`, `list[T]`, `dict[K, V]`, `collections.abc.Iterable`; never `Optional`/`List`/`Dict`/`typing.Iterable`. From `typing` import only `Any`, `Protocol`, `Literal`, `TypeVar`, `runtime_checkable`.
- Fully typed signatures. Ports use `Protocol` (not `ABC`). Entities/value objects are `@dataclass` (`frozen=True` for immutables). `Literal` for fixed values; casts only with runtime checks.
- No mutable default arguments (use `None` + create inside). Never swallow exceptions (`except: pass`); log and re-raise or map cleanly.
- Async: `asyncio.gather` over sequential `await` in loops; CPU-bound parsing goes to `run_in_executor`.
- Prefer small functions/modules over deep class hierarchies; dependencies injected explicitly via constructors/factories.
- Scraping: specific but robust selectors, `urljoin` for URLs, missing fields → partial result + warning instead of abort.
- Playwright: no `sleep()` waits (use conditions/locators), close contexts/pages deterministically, limit browser parallelism with a semaphore.

Performance guide: `docs/PYTHON-BEST-PRACTICES.md`.

***

## 6. Testing (TDD mandatory)

Loop: write test → run (red) → implement minimally (green) → refactor (green) → checkpoint commit.

Layout: `tests/unit/{domain,application,infrastructure,interfaces}`, `tests/integration`, `tests/e2e`, `tests/benchmark`, `tests/live` (opt-in). Plugin tests: `tests/unit/infrastructure/test_<name>_plugin.py`; resolver tests: `test_<name>_resolver.py`. E2E tests are deterministic (no external sites).

Mock patterns:
- `PluginRegistryPort` is **synchronous** → `MagicMock` (not `AsyncMock`).
- `SearchEnginePort`, `CrawlJobRepository`, `CachePort`, `PluginScoreStorePort` are **async** → `AsyncMock`.
- Hoster resolver tests use `respx` (httpx-native HTTP mocking), not `AsyncMock`/`MagicMock`.

***

## 7. Plugins & hoster resolvers

- 42 plugins in `plugins/`, all inheriting from `HttpxPluginBase` (`src/scavengarr/infrastructure/plugins/httpx_base.py`) or `PlaywrightPluginBase` (`playwright_base.py`). Never duplicate base-class boilerplate.
- Every plugin MUST do category filtering, pagination up to 1000 items and bounded-concurrency detail scraping. Site analysis with `playwright-mcp` comes before any code.
- Hoster resolvers live in `src/scavengarr/infrastructure/hoster_resolvers/`: individual streaming/DDL resolvers, 12 generic DDL hosters (`generic_ddl.py`, `GenericDDLConfig`), 27 XFS hosters (`xfs.py`, `XFSConfig`). New XFS/DDL hoster = new config constant, no new tests or wiring.
- JDownloader plugin sources for resolver work: `.devdata/JDownloader2/` (synced on container start).

Step-by-step guides:
- New plugin: `docs/features/python-plugins.md` → "Adding a New Plugin".
- New resolver: `docs/features/hoster-resolvers.md` → "Adding a New Resolver".

***

## 8. Subagents

- Only for mechanical, fully specified tasks (same attribute in many files, renames, boilerplate from a precise spec). Anything needing architecture understanding (use cases, ports, DI wiring, cross-layer refactors, mock-pattern test updates) is done directly.
- 1 file = 1 agent; tight scope (what to do and what not); include conventions (`structlog`, typing rules, naming) in the prompt.
- Review every agent output; run the full test suite + pre-commit afterwards.

***

## 9. Dev container

- **Git access**: `git push` and `gh` use `GH_TOKEN` from `.env.devcontainer` (not versioned); `.devcontainer/setup.sh` runs `gh auth setup-git` on attach. Git identity: `GIT_AUTHOR_NAME`/`GIT_AUTHOR_EMAIL` in the same file. Changes need a container rebuild/restart.
- **No Docker**: `playwright-mcp` runs over stdio from `.mcp.json` (`npx @playwright/mcp@<pinned>`). `setup.sh` installs the matching Chromium (`playwright install --with-deps chromium`); keep `PLAYWRIGHT_MCP_VERSION` in `setup.sh` in sync with `.mcp.json`.
- **Node 22** comes from the devcontainer `node` feature (nvm, `/usr/local/share/nvm/current/bin`), not apt (Debian's Node 18 breaks `npx skills`). `setup.sh` installs npm globals without `sudo` (openspec, `@caveman-ai/cli`) and the caveman skills on every attach.
- **Broken `.venv` shebangs** (`Command not found: pytest`) after a workspace path change: `poetry env remove --all && poetry install --with dev`.
- **Line endings**: `.md` files are LF (enforced via `.gitattributes`). `.py` files are still mixed (most CRLF, some LF): preserve each file's EOL when editing with scripts.

***

## 10. Navigation

| Area | Path |
|---|---|
| Dependencies & tooling | `pyproject.toml`, `.pre-commit-config.yaml` |
| Domain / use cases / adapters | `src/scavengarr/{domain,application,infrastructure}/` |
| HTTP router, CLI, composition root | `src/scavengarr/interfaces/` (`composition.py`) |
| Stremio addon | `src/scavengarr/interfaces/api/stremio/` |
| Plugins | `plugins/` |
| Feature docs / architecture / plans | `docs/features/`, `docs/architecture/`, `docs/plans/` |
| Refactor history | `docs/refactor/COMPLETED/` |
| OpenSpec change specs | `openspec/changes/` |
