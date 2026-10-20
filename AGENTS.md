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
poetry run pytest -n auto
```

pre-commit runs ruff and basedpyright (`standard` mode; `src/` and `plugins/` are clean, keep them so). `poetry run pytest` excludes live tests and benchmarks, and `-n auto` runs it on pytest-xdist workers; a single test file runs faster without `-n`. Live smoke tests (`poetry run pytest -m live`) hit real websites: their failures signal broken plugins or resolvers, not a commit blocker. CI (`.github/workflows/ci.yml`) runs the same two commands on every push to `staging` and on pull requests; a red run after a push is fixed before the next change. `.github/workflows/image.yml` publishes the production image to `ghcr.io/strob0t/scavengarr` (tags: `docs/how-to/deploy.md`, step 1).

Rules:
- Fix all errors before committing (warnings can be acceptable depending on the check).
- Small, atomic commits; commit after each isolated subtask, never batch unrelated changes.
- Commit messages follow Conventional Commits: `<type>(<scope>): <subject>` (`feat`, `fix`, `docs`, `refactor`, `test`, `chore`, ...), lower-case subject, no trailing period.
- **Docs ship with the code**: a change updates its documents in the same commit:

  | Change | Documents to update |
  |---|---|
  | Behaviour | its `docs/current/` doc and every how-to whose steps change (`docs/reference/metrics.md` regenerated for a metric), `CHANGELOG.md` |
  | Configuration | `docs/reference/configuration.md` (`scripts/generate_config_doc.py`), `CHANGELOG.md` |
  | Architecture decision | a record in `docs/target/decisions/`, the `docs/target/` doc it changes, this file if a rule changes, `CHANGELOG.md` |
  | Dependency | its line in `docs/reference/tech-stack.md`, `CHANGELOG.md` |
  | Plugin or resolver | the generated `docs/reference/plugins.md` or `docs/reference/resolvers.md`, `CHANGELOG.md` |
  | Plan | its plan in `docs/plans/` or its row in `docs/backlog.md`, `openspec/changes/` for a spec change, and the state paragraph of `docs/README.md` when an area's open work changes |

  `README.md` is the front page and stays current: a change of a feature, a setting, the setup, the deployment, a count it names or the document layout updates it in the same commit (`tests/unit/infrastructure/test_readme.py` checks its documentation table and counts). `tests/unit/infrastructure/test_docs_paths.py` fails when an instruction file or a doc of the target layout names a repository path that does not exist.
- **Docs and commit texts carry no private traces**: durations, counts and build hashes are the evidence, never clock times or dates of playbacks and observations, request ids, titles or IMDb/Kitsu ids of the maintainer's requests (a placeholder such as "Series A"), LAN hostnames (`*.example.lan` in examples), ISP, VPN or device names, or household references; test data and fixtures keep their titles. `tests/unit/infrastructure/test_docs_privacy.py` fails on clock times, request ids, LAN hostnames, network numbers, time zones and the production machine's device name in the docs (the full rule: `docs/agents/orchestration.md` → Working rules).
- Push after each successful change: `git push origin staging`.
- Larger refactors: write a brief Markdown plan (problem, design, affected files, tests) first.

### Working efficiently (agents)
- Find code by symbol, then read only the lines you need: the `LSP` tool (definitions, references; `basedpyright-lsp`, §9) or `grep -n`, then a ranged read. Whole-file reads of large modules cost the most context.
- Package APIs: the installed source in `.venv` (§4); the Context7 MCP server serves current library docs where it is configured.
- Keep tool output short: pipe pytest, pre-commit and git output through `tail` or `grep`. Full test runs use `-n auto`; a single test file runs faster without it.
- Run slow work in the background and wait for its notification instead of polling with `sleep`: after a push `gh run watch <run-id> --exit-status`, likewise live tests and benchmarks.
- Production only through `scripts/prodctl.py` (§9). A probe needed twice belongs in `scripts/probes/`, not in a throwaway script.

### Merge to main (only on explicit user request)
1. Bump the version in `pyproject.toml` (PATCH +1 unless MINOR/MAJOR is warranted). It is the only place: the app, the Stremio manifest, Torznab caps and the default User-Agent read it through `infrastructure/version.py` (package metadata; `poetry install` refreshes it in the dev venv).
2. Update `CHANGELOG.md` (newest entry on top with version, date, changes; current bugs under `KNOWN_ISSUES`).
3. Commit & push to `staging`.
4. `gh pr create --base main --head staging --title "..." --body "..."` then `gh pr merge --merge`. When the merge waits for the maintainer, push the release commit to `release/vX.Y.Z` and open the PR from there instead, so that later pushes to `staging` stay out of the release; delete the branch after the merge.
5. Tag the release on the merge commit: `git fetch origin && git tag vX.Y.Z origin/main && git push origin vX.Y.Z`; the image workflow publishes `vX.Y.Z` and `latest`.
6. Sync back: `git fetch origin && git merge origin/main && git push origin staging`.

---

## 2. Project overview

Scavengarr is a self-hosted Torznab/Newznab indexer for Prowlarr and other Arr apps, plus a Stremio addon. Plugins scrape sites with httpx (static HTML) or Playwright (JS-heavy sites); results are served via Torznab endpoints (`caps`, `search`) and Stremio streams.

Torznab request flow: HTTP request → use case loads plugin from registry (lazy) → plugin runs multi-stage search (search page → detail pages → links; results cached) → links validated in parallel until the requested page is full → one `.crawljob` per item → presenter renders Torznab XML. Stremio stream flow: IMDb id (a `kitsu:` id of the anime catalogs is translated first: the Anime Kitsu addon's meta, Fribb's id list as the fallback; `infrastructure/anime/`) → title(s) and the Cinemeta meta (kind, episode list; the episode reference for plugins that locate episodes, numbered by Kitsu for an anime episode where one Kitsu entry counts the series) → cached or shared plugin search per title → title/episode filter, rank → each hoster's best link resolved (resolver cache) → play/proxy links.

Docs index: `docs/README.md` (every document with its status, and the documentation rules). Architecture: `docs/target/architecture.md` (the rules) and `docs/current/architecture.md` (the module map and the request flows).

---

## 3. Clean Architecture (dependency rule)

The rules in full: `docs/target/architecture.md` (the layer graph, the layer contracts and the invariants; `infrastructure/` never imports `application/`, and `tests/unit/test_dependency_rule.py` fails on an import against the graph), the decisions behind them in `docs/target/decisions/`, the module map and the request flows in `docs/current/architecture.md`.

Layers under `src/scavengarr/`, outer depends on inner only:

| Layer | Contains | Rule |
|---|---|---|
| `interfaces/` | FastAPI routers, CLI, composition root (DI wiring) | I/O only, no business rules |
| `infrastructure/` | plugins, hoster resolvers, link validation, cache, Torznab presenter, config, logging, concurrency pool, retries, circuit breakers, telemetry | implements domain ports |
| `application/` | use cases, CrawlJob factory, Stremio search and resolution helpers (deadlines, search cache) | knows ports, not adapters |
| `domain/` | entities, value objects, `Protocol` ports | framework-free, I/O-free |

Domain never imports FastAPI, httpx or diskcache.

Invariants:
- I/O dominates runtime: nothing may block the event loop. Independent URLs (detail pages, link validation) run in parallel with bounded concurrency.
- Link validation: `HEAD` with redirects first, `GET` fallback only when needed; short timeouts, semaphore-limited (`docs/current/link-validation.md`).
- Stremio search results are cached per title (single-flight, stale-while-revalidate with one refresh at a time). A request answers at its answer budget with the results known by then while the plugins run to their own timeout; a partial entry names its missing plugins and one completion search fills it. Resolved streams are not cached with the results: the hoster resolver registry caches its outcomes, and those, its redirects, its last lookups and the open circuit breakers outlive a restart. Background searches and resolutions end at shutdown. How: `docs/current/stremio-addon.md` (the search cache, the answer budget), `docs/current/architecture.md` (the Stremio search timing, shutdown), `docs/current/hoster-resolvers.md` (caches and state across restarts).
- The plugins' long-term record (`PluginHistory`, `GET /api/v1/stats/plugins`) is the evidence for keeping, disabling or removing a plugin whose site died: the maintainer decides from it, no threshold does (`docs/current/observability.md`, the plugin record).
- CrawlJobs carry the links that passed validation (with `validate_download_links` on), in deterministic order; grab-time links (`GrabResolvingPlugin`) are stored as the plugin returns them. Job ids are random UUID4s (`docs/current/torznab-and-crawljobs.md`).
- Config precedence (high → low): CLI args → `SCAVENGARR_*` env (a `--dotenv` file's values included; the real environment wins) → YAML → defaults. A secret setting needs `password`, `token`, `key` or `secret` in its name (or the `SecretStr` type), so the startup line `config_effective` shows it as `***`. Every setting is read in `src/` outside the config package or declares itself unused in its description (`tests/unit/infrastructure/test_config_settings_read.py` fails otherwise). See `docs/reference/configuration.md`.
- Logging: `structlog`, structured, with context fields (`plugin`, `stage`, `duration_ms`, `results_count`; every HTTP request binds `request_id`); never log secrets.
- Metrics: the core records its steps through `TelemetryPort`, never plugins or resolvers; label values only from fixed sets (no titles, ids, URLs, domains), and no URLs or titles in span attributes. See `docs/current/observability.md`.

---

## 4. Dependencies

- Source of truth: `pyproject.toml`. For package APIs, read the installed source in `.venv` or the official docs.
- Prefer stdlib, then established libraries, then custom code. No internal mini-frameworks. New dependencies need explicit justification. A commit that adds, removes or replaces a dependency updates its line in `docs/reference/tech-stack.md` (purpose, reason, the alternatives not used), which `tests/unit/infrastructure/test_tech_stack_doc.py` checks against `pyproject.toml`.
- Dependabot (`.github/dependabot.yml`) opens weekly update PRs for Poetry, GitHub Actions and the base images of `Dockerfile.prod` against `staging`; they are merged into `staging` after a green CI run, and a major update gets a look at the package's changelog first. Dependabot reads the file from the default branch `main`, so a change to it takes effect with the release that carries it.

---

## 5. Python rules (MUST READ!)

- `from __future__ import annotations` in every file.
- Modern typing only: `T | None`, `list[T]`, `dict[K, V]`, `collections.abc.Iterable`; never `Optional`/`List`/`Dict`/`typing.Iterable`. From `typing` import only `Any`, `Protocol`, `Literal`, `TypeVar`, `runtime_checkable`, `cast` and `TYPE_CHECKING` (for imports only annotations need: browser types, or a module that would import back).
- Fully typed signatures. Ports use `Protocol` (not `ABC`). Entities/value objects are `@dataclass` (`frozen=True` for immutables). `Literal` for fixed values; `cast` only where a runtime check or the program guarantees the type (the app sets `app.state = AppState()`, so routers read `cast(AppState, request.app.state)`).
- No mutable default arguments (use `None` + create inside). Never swallow exceptions (`except: pass`); log and re-raise or map cleanly.
- Async: `asyncio.gather` over sequential `await` in loops; CPU-bound work goes off the event loop with `asyncio.to_thread` (`run_in_executor` drops the log context, `request_id` included). Plugin parsers use `selectolax` (lexbor, CSS selectors; helpers and pitfalls in `infrastructure/plugins/dom.py`), and every page goes through `await parse_page(parser, html)`, which parses pages from 32 KiB in a worker thread.
- Prefer small functions/modules over deep class hierarchies; dependencies injected explicitly via constructors/factories.
- Scraping: specific but robust selectors, `urljoin` for URLs, search terms encoded (`quote_plus`, or `quote(term, safe="")` in a path segment), missing fields → partial result + warning instead of abort.
- Playwright: no `sleep()` waits (use conditions/locators), close contexts/pages deterministically, limit browser parallelism with a semaphore.

Coding guidelines (the event loop, shared resources, concurrency, scraping, caching, retries): `docs/target/coding-guidelines.md`; measuring a change: `docs/how-to/performance-measurement.md`.

---

## 6. Testing (TDD mandatory)

Loop: write test → run (red) → implement minimally (green) → refactor (green) → checkpoint commit.

Layout: `tests/unit/{domain,application,infrastructure,interfaces}`, `tests/integration`, `tests/e2e`, `tests/benchmark`, `tests/live` (opt-in). Plugin tests: `tests/unit/infrastructure/test_<name>_plugin.py`; resolver tests: `test_<name>_resolver.py`. E2E tests are deterministic (no external sites).

Mock patterns:
- `PluginRegistryPort` is **synchronous** → `MagicMock` (not `AsyncMock`).
- `SearchEnginePort`, `CrawlJobRepository`, `CachePort`, `PluginScoreStorePort` are **async** → `AsyncMock`.
- `TelemetryPort` is **synchronous** → pass `NO_TELEMETRY`; the concurrency ports hand out async context managers → a real `ConcurrencyPool`.
- Hoster resolver tests use `respx` (httpx-native HTTP mocking), not `AsyncMock`/`MagicMock`.

---

## 7. Plugins & hoster resolvers

- 43 plugins in `plugins/`, all inheriting from `HttpxPluginBase` (`src/scavengarr/infrastructure/plugins/httpx_base.py`) or `PlaywrightPluginBase` (`playwright_base.py`). Never duplicate base-class boilerplate: sites on the same backend share one base in `src/scavengarr/infrastructure/plugins/` instead of copied plugin code, and sites that front one database share a `mirror_group`. A plugin that locates episodes (`locates_episodes = True`) answers nothing for a series when no row of the site's season pages matches, never the request's numbers. How: `docs/current/plugins.md` (base classes, episodes, plugin selection), `docs/current/plugin-domains.md` (mirror groups, official domain lists).
- Every plugin MUST do category filtering (label results from site data, never with the requested category; answer requests with `served_category()` / `filter_by_category()` of `infrastructure/plugins/categories.py`, `[]` without a request for categories the site lacks), pagination up to 1000 items and bounded-concurrency detail scraping, of relevant search hits only (`relevant_hits()` of `infrastructure/plugins/relevance.py`; site searches also list loose matches). Site analysis with `playwright-mcp` comes before any code; parsers are tested on the site's own pages too (`scripts/capture_pages.py`, `tests/unit/infrastructure/test_real_pages.py`). `_domains` holds genuine domains only (check the JDownloader plugin's dead and fake/scam domain notes). A site that publishes an official list of its addresses gets `domain_list_url` and a `domain_marker` (`docs/current/plugin-domains.md`).
- Hoster resolvers live in `src/scavengarr/infrastructure/hoster_resolvers/`: individual streaming/DDL resolvers, 10 generic DDL hosters (`generic_ddl.py`, `GenericDDLConfig`), 25 XFS hosters (`xfs.py`, `XFSConfig`). New XFS/DDL hoster = new config constant appended to `ALL_XFS_CONFIGS` / `ALL_DDL_CONFIGS` (raise the count in its test and here), no other tests or wiring. A resolver declares what its CDN needs: `ClientBoundResolverPort` when the video URL is bound to the player's own headers, `needs_playback_check = True` without a check of its own, `address_bound = True` when the CDN plays only for the resolving address, and `challenge_requests` of `capture_stream()` when its hoster runs a captcha of its own. A challenge is an outcome of its own, outside the circuit breaker. A shorter `autoplay_wait` than the 3 s default is measured on the production host with `capture_timing.py --autoplay-wait` first. How: `docs/current/hoster-resolvers.md` (playback headers, bound CDNs, outcomes, the circuit breaker, Filemoon's pass keeper) and `docs/current/browser-and-challenges.md` (captures, challenges).
- JDownloader plugin sources for resolver work: `.devdata/JDownloader2/` (synced on container start).
- `docs/reference/plugins.md` is generated from the plugin metadata: after adding, removing or renaming a plugin or changing its `provides`, `_domains`, `languages` or base class, run `poetry run python scripts/generate_plugin_list.py` (`test_plugin_list_doc.py` fails otherwise). The README names no sites; the list carries the disclaimer.
- `docs/reference/resolvers.md` is generated from the resolver registrations (`build_hoster_resolvers()` in `src/scavengarr/interfaces/composition.py`): after adding, removing or changing a resolver, its domains or its module docstring's first sentence, run `PYTHONPATH=src poetry run python scripts/generate_resolver_list.py` (`test_resolver_list_doc.py` fails otherwise).

Step-by-step guides:
- New plugin: `docs/how-to/new-plugin.md`.
- New resolver: `docs/how-to/new-resolver.md`.

---

## 8. Subagents

- Only for mechanical, fully specified tasks (same attribute in many files, renames, boilerplate from a precise spec). Anything needing architecture understanding (use cases, ports, DI wiring, cross-layer refactors, mock-pattern test updates) is done directly.
- 1 file = 1 agent; tight scope (what to do and what not); include conventions (`structlog`, typing rules, naming) in the prompt.
- Review every agent output; run the full test suite + pre-commit afterwards.

---

## 9. Dev container

How to set it up and work in it, with the details behind each rule: [`docs/how-to/dev-container.md`](docs/how-to/dev-container.md).

- **Git access**: `git push` and `gh` use `GH_TOKEN` from `.env.devcontainer` (not versioned, filled in from `.env.devcontainer.example`); a change to it needs a container rebuild.
- **Port 7979** is published on all host interfaces: a dev server started with `--host 0.0.0.0` is reachable by everyone on the LAN.
- **No Docker**: `playwright-mcp` runs over stdio from `.mcp.json`, and `PLAYWRIGHT_MCP_VERSION` in `.devcontainer/setup.sh` stays in sync with it; headful browser tests run under `xvfb-run -a`.
- **Skills**: every installed skill costs context in every session, so a skill is added deliberately and recorded in `docs/agents/skills.md`.
- **Checks in a worktree**: run `scripts/worktree_venv.sh` once; everything outside pytest that imports the package runs with `PYTHONPATH=src`, else it runs the main checkout's code and a live proof proves nothing.
- **Line endings and executable bits**: text files are LF, and directly run scripts are stored as 100755 (`tests/unit/infrastructure/test_repository_files.py`).
- **Editor and formatter**: extensions come from Open VSX only, type checking is basedpyright in `standard` mode, and ruff in `pyproject.toml` is pinned to the `rev` of `ruff-pre-commit` (bump both together).
- **Claude Code hooks and skills**: `.claude/hooks/`, `.claude/skills/` and `.claude/plugins/` are versioned, the rest of `.claude/` is not; run `bash -n` on a changed hook, since a syntax error blocks every Bash command.
- **Production diagnostics**: production is read only through `scripts/prodctl.py`, whose output is masked; probes must not change state, and rebuilds and restarts stay with the user (`docs/how-to/operations.md`).
- **Code intelligence**: the `basedpyright-lsp` plugin of `.claude/plugins/` and Serena give type diagnostics, definitions and references (`docs/agents/mcp-servers.md`).

---

## 10. Navigation

| Area | Path |
|---|---|
| Dependencies & tooling | `pyproject.toml`, `.pre-commit-config.yaml`, `docs/reference/tech-stack.md` (every dependency with its reason) |
| Domain / use cases / adapters | `src/scavengarr/{domain,application,infrastructure}/` |
| HTTP router, CLI, composition root | `src/scavengarr/interfaces/` (`composition.py`) |
| Stremio addon | `src/scavengarr/interfaces/api/stremio/` |
| Plugins | `plugins/` (generated list: `docs/reference/plugins.md`, `scripts/generate_plugin_list.py`) |
| Contributor guide (project layout), Docker Compose | `CONTRIBUTING.md`, `docker-compose.yml` (profiles `solver`, `redis`, `tracing`) |
| Production diagnostics | `scripts/prodctl.py`, `scripts/probes/`, `scripts/portainer.py`, `scripts/digest.py` |
| Docs (index: `docs/README.md`) | `docs/current/` (architecture: `docs/current/architecture.md`), `docs/target/` (`docs/target/architecture.md`, decisions: `docs/target/decisions/`), `docs/how-to/`, `docs/reference/` (settings: `docs/reference/configuration.md`), `docs/plans/`, `docs/agents/`, the queue `docs/backlog.md` |
| OpenSpec change specs | `openspec/changes/` |
