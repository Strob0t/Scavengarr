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

---

# AGENTS.md

Instructions for developers and AI assistants working on Scavengarr. Details live in `docs/`; this file holds the rules that apply to every change.

This is the single instruction file for every coding agent (the AGENTS.md convention); there is no `CLAUDE.md`. Claude Code reads `AGENTS.md` automatically when no `CLAUDE.md` exists (v2.1.277 or newer).

---

## 1. Workflow (IMPORTANT!)

### Branches
- `staging`: development branch (commit here). `main`: production (merge via PR only).
- Never commit to `main`. Never merge into `main` without an explicit user request.

### Before every commit

```bash
poetry run pre-commit run --all-files
poetry run pytest
```

pre-commit runs ruff and basedpyright (`standard` mode, `[tool.basedpyright]` in `pyproject.toml`; `src/` and `plugins/` are clean, keep them so). `poetry run pytest` excludes live tests (`addopts = -m "not live"`) and benchmarks. Live smoke tests hit real websites and run with `poetry run pytest -m live`; their failures signal broken plugins/resolvers, not a commit blocker. CI (`.github/workflows/ci.yml`) runs the same two commands on every push to `staging` and on pull requests; a red run after a push is fixed before the next change.

Rules:
- Fix all errors before committing (warnings can be acceptable depending on the check).
- Small, atomic commits; commit after each isolated subtask, never batch unrelated changes.
- Commit messages follow Conventional Commits: `<type>(<scope>): <subject>` (`feat`, `fix`, `docs`, `refactor`, `test`, `chore`, ...), lower-case subject, no trailing period.
- **Docs ship with the code**: if behavior, features, architecture or configuration change, update `CHANGELOG.md`, `docs/features/`, `docs/architecture/`, `docs/plans/`, `AGENTS.md`, `README.md` or `openspec/changes/...` in the same commit.
- Push after each successful change: `git push origin staging`.
- Larger refactors: write a brief Markdown plan (problem, design, affected files, tests) first.

### Merge to main (only on explicit user request)
1. Bump the version in `pyproject.toml` (PATCH +1 unless MINOR/MAJOR is warranted). It is the only place: the app, the Stremio manifest, Torznab caps and the default User-Agent read it through `infrastructure/version.py` (package metadata; `poetry install` refreshes it in the dev venv).
2. Update `CHANGELOG.md` (newest entry on top with version, date, changes; current bugs under `KNOWN_ISSUES`).
3. Commit & push to `staging`.
4. `gh pr create --base main --head staging --title "..." --body "..."` then `gh pr merge --merge`.
5. Sync back: `git fetch origin && git merge origin/main && git push origin staging`.

---

## 2. Project overview

Scavengarr is a self-hosted Torznab/Newznab indexer for Prowlarr and other Arr apps, plus a Stremio addon. Plugins scrape sites with httpx (static HTML) or Playwright (JS-heavy sites); results are served via Torznab endpoints (`caps`, `search`) and Stremio streams.

Request flow: request (HTTP/CLI) → use case loads plugin from registry (lazy) → plugin runs multi-stage search (search page → detail pages → links) → links validated in parallel → optional `.crawljob` bundle → presenter renders Torznab XML.

Feature docs: `docs/features/README.md` (index). Architecture: `docs/architecture/clean-architecture.md`.

---

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
- Stremio search results are cached per title (`cache.search_ttl_seconds`, stale-while-revalidate, single-flight; `application/stremio/search_cache.py`); hoster resolution runs on every request. Background searches end in `StremioStreamUseCase.aclose()` at shutdown.
- CrawlJobs contain only validated links, in deterministic order; job IDs are stable, TTL configurable.
- Config precedence (high → low): CLI args → `SCAVENGARR_*` env → YAML → `.env` → defaults. See `docs/features/configuration.md`.
- Logging: `structlog`, structured, with context fields (`plugin`, `stage`, `duration_ms`, `results_count`; every HTTP request binds `request_id`); never log secrets.
- Metrics: the core records its steps through `TelemetryPort` (`stage()`: duration and outcome; plugin search runner, hoster resolver registry, Stremio use case, HLS proxy), never plugins or resolvers; label values only from fixed sets (no titles, ids, URLs, domains). With `telemetry.tracing_endpoint` the stages are also OpenTelemetry spans (no URLs or titles in attributes). See `docs/features/observability.md`.

---

## 4. Dependencies

- Source of truth: `pyproject.toml`. For package APIs, read the installed source in `.venv` or the official docs.
- Prefer stdlib, then established libraries, then custom code. No internal mini-frameworks. New dependencies need explicit justification.

---

## 5. Python rules (MUST READ!)

- `from __future__ import annotations` in every file.
- Modern typing only: `T | None`, `list[T]`, `dict[K, V]`, `collections.abc.Iterable`; never `Optional`/`List`/`Dict`/`typing.Iterable`. From `typing` import only `Any`, `Protocol`, `Literal`, `TypeVar`, `runtime_checkable`.
- Fully typed signatures. Ports use `Protocol` (not `ABC`). Entities/value objects are `@dataclass` (`frozen=True` for immutables). `Literal` for fixed values; casts only with runtime checks.
- No mutable default arguments (use `None` + create inside). Never swallow exceptions (`except: pass`); log and re-raise or map cleanly.
- Async: `asyncio.gather` over sequential `await` in loops; CPU-bound work goes off the event loop (`asyncio.to_thread`/`run_in_executor`). Plugin parsers use `selectolax` (lexbor, CSS selectors; helpers and pitfalls in `infrastructure/plugins/dom.py`), and every page goes through `await parse_page(parser, html)`, which parses pages from 32 KiB in a worker thread.
- Prefer small functions/modules over deep class hierarchies; dependencies injected explicitly via constructors/factories.
- Scraping: specific but robust selectors, `urljoin` for URLs, search terms encoded (`quote_plus`, or `quote(term, safe="")` in a path segment), missing fields → partial result + warning instead of abort.
- Playwright: no `sleep()` waits (use conditions/locators), close contexts/pages deterministically, limit browser parallelism with a semaphore.

Performance guide: `docs/PYTHON-BEST-PRACTICES.md`.

---

## 6. Testing (TDD mandatory)

Loop: write test → run (red) → implement minimally (green) → refactor (green) → checkpoint commit.

Layout: `tests/unit/{domain,application,infrastructure,interfaces}`, `tests/integration`, `tests/e2e`, `tests/benchmark`, `tests/live` (opt-in). Plugin tests: `tests/unit/infrastructure/test_<name>_plugin.py`; resolver tests: `test_<name>_resolver.py`. E2E tests are deterministic (no external sites).

Mock patterns:
- `PluginRegistryPort` is **synchronous** → `MagicMock` (not `AsyncMock`).
- `SearchEnginePort`, `CrawlJobRepository`, `CachePort`, `PluginScoreStorePort` are **async** → `AsyncMock`.
- Hoster resolver tests use `respx` (httpx-native HTTP mocking), not `AsyncMock`/`MagicMock`.

---

## 7. Plugins & hoster resolvers

- 41 plugins in `plugins/`, all inheriting from `HttpxPluginBase` (`src/scavengarr/infrastructure/plugins/httpx_base.py`) or `PlaywrightPluginBase` (`playwright_base.py`). Never duplicate base-class boilerplate. Sites on the same backend share one base in `src/scavengarr/infrastructure/plugins/` instead of copied plugin code (`DataApiPluginBase` in `data_api.py` for megakino_to and movie4k, `XenForoPluginBase` in `xenforo.py` for the XenForo forums dataload and myboerse). Sites that front one database (same ids and links in other themes) share a `mirror_group`, so a Stremio request asks only one of them, the reachable one with the best plugin score (hdfilme, streamcloud, streamkiste).
- Every plugin MUST do category filtering (label results from site data, never with the requested category; answer requests with `served_category()` / `filter_by_category()` of `infrastructure/plugins/categories.py`, `[]` without a request for categories the site lacks), pagination up to 1000 items and bounded-concurrency detail scraping, of relevant search hits only (`relevant_hits()` of `infrastructure/plugins/relevance.py`; site searches also list loose matches). Site analysis with `playwright-mcp` comes before any code; parsers are tested on the site's own pages too (`scripts/capture_pages.py`, `tests/unit/infrastructure/test_real_pages.py`). `_domains` holds genuine domains only (check the JDownloader plugin's dead and fake/scam domain notes).
- Hoster resolvers live in `src/scavengarr/infrastructure/hoster_resolvers/`: individual streaming/DDL resolvers, 10 generic DDL hosters (`generic_ddl.py`, `GenericDDLConfig`), 25 XFS hosters (`xfs.py`, `XFSConfig`). New XFS/DDL hoster = new config constant, no new tests or wiring. A resolver whose CDN binds the video URL to headers the player sends itself (VEEV: User-Agent, Accept-Language) implements `ClientBoundResolverPort`; `/play` then resolves it per player.
- JDownloader plugin sources for resolver work: `.devdata/JDownloader2/` (synced on container start).
- `docs/plugins.md` is generated from the plugin metadata: after adding, removing or renaming a plugin or changing its `provides`, `_domains`, `languages` or base class, run `poetry run python scripts/generate_plugin_list.py` (`test_plugin_list_doc.py` fails otherwise). The README names no sites; the list carries the disclaimer.

Step-by-step guides:
- New plugin: `docs/features/python-plugins.md` → "Adding a New Plugin".
- New resolver: `docs/features/hoster-resolvers.md` → "Adding a New Resolver".

---

## 8. Subagents

- Only for mechanical, fully specified tasks (same attribute in many files, renames, boilerplate from a precise spec). Anything needing architecture understanding (use cases, ports, DI wiring, cross-layer refactors, mock-pattern test updates) is done directly.
- 1 file = 1 agent; tight scope (what to do and what not); include conventions (`structlog`, typing rules, naming) in the prompt.
- Review every agent output; run the full test suite + pre-commit afterwards.

---

## 9. Dev container

- **Git access**: `git push` and `gh` use `GH_TOKEN` from `.env.devcontainer` (not versioned); `.devcontainer/setup.sh` runs `gh auth setup-git` on attach. Git identity: `GIT_AUTHOR_NAME`/`GIT_AUTHOR_EMAIL` in the same file. On the first start `initializeCommand` copies `.env.devcontainer.example` to `.env.devcontainer` if it is missing (fill it in, then rebuild). Changes need a container rebuild/restart.
- **Port 7979** is published on all host interfaces (`runArgs`: `-p 0.0.0.0:7979:7979`; `forwardPorts` would only tunnel it to the editor's machine), so LAN clients such as Stremio reach a server started in the container with `--host 0.0.0.0 --port 7979` at `http://<workstation>:7979`, and so does everyone else on the LAN.
- **No Docker**: `playwright-mcp` runs over stdio from `.mcp.json` (`npx @playwright/mcp@<pinned>`). `setup.sh` installs the matching Chromium (`playwright install --with-deps chromium`), the app's Patchright Chromium with its system libraries (`python -m patchright install --with-deps chromium`, independent of the optional MCP step) and `xvfb` with `xauth` (headful browser tests: `xvfb-run -a <cmd>`; without `xauth` it fails with "xauth command not found"); keep `PLAYWRIGHT_MCP_VERSION` in `setup.sh` in sync with `.mcp.json`.
- **Node 22** comes from the devcontainer `node` feature (nvm, `/usr/local/share/nvm/current/bin`), not apt (Debian's Node 18 breaks `npx skills`). `setup.sh` installs npm globals without `sudo` (openspec, `@caveman-ai/cli`) and a fixed subset of the caveman skills (`CAVEMAN_SKILLS`) on every attach; every installed skill's description costs context in every session, so add skills deliberately.
- **Broken `.venv` shebangs** (`Command not found: pytest`) after a workspace path change: `poetry env remove --all && poetry install --with dev`.
- **Line endings**: all text files are LF, enforced editor-independently by `.gitattributes` (`* text=auto eol=lf`) and the `mixed-line-ending --fix=lf` pre-commit hook; VS Code also saves new files with LF (`files.eol` in `.vscode/settings.json`). Shell scripts with CRLF fail at the shebang (exit 127): a CRLF Claude Code hook silently allows everything. The same goes for a missing executable bit (exit 126): scripts that are run directly (`docker/entrypoint.sh`, `.claude/hooks/*.sh`, `.devcontainer/*.sh`) must be stored as 100755 (`git update-index --chmod=+x`; `core.fileMode=false` hides it locally), guarded by `tests/unit/infrastructure/test_repository_files.py`.
- **Editor extensions**: `customizations.vscode.extensions` in `.devcontainer/devcontainer.json` lists only extensions available on Open VSX, so VS Code and VSCodium (DevPod) install the same set; type checking uses `detachhead.basedpyright` in `standard` mode (`[tool.basedpyright]` in `pyproject.toml`, so the editor and `poetry run basedpyright` apply the same rules), not Pylance, formatting uses ruff.
- **ruff version**: `pyproject.toml` pins ruff to exactly the `rev` of `ruff-pre-commit` in `.pre-commit-config.yaml` (the edit hook uses the venv's ruff, pre-commit its own); bump both together, otherwise the two formatters can undo each other.
- **Claude Code hooks and skills**: `.claude/hooks/` (`block-dangerous.sh` PreToolUse guard; `format-and-lint.sh` runs ruff on each edited `.py` file and reports unfixable errors back to Claude via exit 2) and `.claude/skills/` (`commit`, `test`, `new-plugin`, `new-resolver`; thin checklists pointing to `docs/`) are versioned; the rest of `.claude/` (e.g. `settings.local.json`, which wires the hooks, and `worktrees/`) stays gitignored. After changing a hook, run `bash -n` on it: a syntax error exits 2 and blocks every Bash command.

---

## 10. Navigation

| Area | Path |
|---|---|
| Dependencies & tooling | `pyproject.toml`, `.pre-commit-config.yaml` |
| Domain / use cases / adapters | `src/scavengarr/{domain,application,infrastructure}/` |
| HTTP router, CLI, composition root | `src/scavengarr/interfaces/` (`composition.py`) |
| Stremio addon | `src/scavengarr/interfaces/api/stremio/` |
| Plugins | `plugins/` (generated list: `docs/plugins.md`, `scripts/generate_plugin_list.py`) |
| Contributor guide, Docker Compose | `CONTRIBUTING.md`, `docker-compose.yml` (profiles `solver`, `redis`, `tracing`) |
| Feature docs / architecture / plans | `docs/features/`, `docs/architecture/`, `docs/plans/` |
| Refactor history | `docs/refactor/COMPLETED/` |
| OpenSpec change specs | `openspec/changes/` |
