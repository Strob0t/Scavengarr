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

pre-commit runs ruff and basedpyright (`standard` mode, `[tool.basedpyright]` in `pyproject.toml`; `src/` and `plugins/` are clean, keep them so). `-n auto` runs the suite on pytest-xdist workers (at most 8, `--maxprocesses` in `addopts`: 15 s instead of 47 s); single test files run faster without `-n`. `poetry run pytest` excludes live tests (`addopts = -m "not live"`) and benchmarks. Live smoke tests hit real websites and run with `poetry run pytest -m live`; their failures signal broken plugins/resolvers, not a commit blocker. CI (`.github/workflows/ci.yml`) runs the same two commands on every push to `staging` and on pull requests; a red run after a push is fixed before the next change. `.github/workflows/image.yml` publishes the production image to `ghcr.io/strob0t/scavengarr` (`linux/amd64` and `linux/arm64`): tag `staging` on every push to `staging`, `vX.Y.Z` and `latest` on release tags (`docs/features/configuration.md` → Image Tags).

Rules:
- Fix all errors before committing (warnings can be acceptable depending on the check).
- Small, atomic commits; commit after each isolated subtask, never batch unrelated changes.
- Commit messages follow Conventional Commits: `<type>(<scope>): <subject>` (`feat`, `fix`, `docs`, `refactor`, `test`, `chore`, ...), lower-case subject, no trailing period.
- **Docs ship with the code**: if behavior, features, architecture or configuration change, update `CHANGELOG.md`, `docs/features/`, `docs/architecture/`, `docs/plans/`, `AGENTS.md`, `README.md` or `openspec/changes/...` in the same commit. `tests/unit/infrastructure/test_docs_paths.py` fails when an instruction file or a feature or architecture doc names a repository path that does not exist.
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

Torznab request flow: HTTP request → use case loads plugin from registry (lazy) → plugin runs multi-stage search (search page → detail pages → links; results cached) → links validated in parallel until the requested page is full → one `.crawljob` per item → presenter renders Torznab XML. Stremio stream flow: IMDb id (a `kitsu:` id of the anime catalogs is translated first: the Anime Kitsu addon's meta, Fribb's id list as the fallback; `infrastructure/anime/`) → title(s) and the Cinemeta meta (kind, episode list; the episode reference for plugins that locate episodes) → cached or shared plugin search per title → title/episode filter, rank → each hoster's best link resolved (resolver cache) → play/proxy links.

Feature docs: `docs/features/README.md` (index). Architecture: `docs/architecture/clean-architecture.md`.

---

## 3. Clean Architecture (dependency rule)

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
- Link validation: `HEAD` with redirects first, `GET` fallback only when needed; short timeouts, semaphore-limited.
- Stremio search results are cached per title (`cache.search_ttl_seconds`, stale-while-revalidate with one refresh at a time, single-flight; `application/stremio/title_search.py`, `search_cache.py`). A request answers at its budget (`plugin_timeout_seconds` after its start) with the results known by then while the plugins run to their own timeout: a partial entry names its `missing` plugins and one completion search fills it, results arriving after an answer resolve in the background, a retry past the budget answers at once, and the response says so (`X-Cache`, `X-Search-Complete`). Resolved streams are not cached with them: each request asks `HosterResolverRegistry`, which caches outcomes (1 h for a stream, 15 min for a dead link), and a cached search answers from those and resolves the rest in the background. Those outcomes, its redirects and the open circuit breakers outlive a restart (`HosterStateStore`: one snapshot `hoster_state:v1` in the cache backend, restored before the app reports ready, lifetimes shortened by the downtime). Background searches end in `StremioStreamUseCase.aclose()` at shutdown. The plugins' long-term record (`PluginHistory`: searches, results, timeouts, the results the episode filter dropped (`dropped`), site checks and unreachable marks per plugin and UTC day, 180 days, `plugin_history:v1` in the cache backend; `GET /api/v1/stats/plugins`) is the evidence for keeping, disabling or removing a plugin whose site died: the maintainer decides from it, no threshold does.
- CrawlJobs carry the links that passed validation (with `validate_download_links` on), in deterministic order; grab-time links (`GrabResolvingPlugin`) are stored as the plugin returns them. Job ids are random UUID4s; the TTL is `cache.crawljob_ttl_seconds`.
- Config precedence (high → low): CLI args → `SCAVENGARR_*` env (a `--dotenv` file's values included; the real environment wins) → YAML → defaults. See `docs/features/configuration.md`. The startup line `config_effective` logs every setting away from its default: a secret setting needs `password`, `token`, `key` or `secret` in its name (or the `SecretStr` type) to show as `***`.
- Logging: `structlog`, structured, with context fields (`plugin`, `stage`, `duration_ms`, `results_count`; every HTTP request binds `request_id`); never log secrets.
- Metrics: the core records its steps through `TelemetryPort` (`stage()`: duration and outcome; plugin search runner, hoster resolver registry, Stremio use case, the stealth browser's page gate, HLS proxy), never plugins or resolvers; label values only from fixed sets (no titles, ids, URLs, domains). With `telemetry.tracing_endpoint` the stages are also OpenTelemetry spans (no URLs or titles in attributes). See `docs/features/observability.md`.

---

## 4. Dependencies

- Source of truth: `pyproject.toml`. For package APIs, read the installed source in `.venv` or the official docs.
- Prefer stdlib, then established libraries, then custom code. No internal mini-frameworks. New dependencies need explicit justification.
- Dependabot (`.github/dependabot.yml`) opens weekly update PRs for Poetry, GitHub Actions and the base images of `Dockerfile.prod`; they are merged after a green CI run, and a major update gets a look at the package's changelog first.

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

Performance guide: `docs/PYTHON-BEST-PRACTICES.md`.

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

- 41 plugins in `plugins/`, all inheriting from `HttpxPluginBase` (`src/scavengarr/infrastructure/plugins/httpx_base.py`) or `PlaywrightPluginBase` (`playwright_base.py`). Never duplicate base-class boilerplate. Sites on the same backend share one base in `src/scavengarr/infrastructure/plugins/` instead of copied plugin code (`DataApiPluginBase` in `data_api.py` for megakino_to and movie4k, `XenForoPluginBase` in `xenforo.py` for the XenForo forums dataload and myboerse). Sites that front one database (same ids and links in other themes) share a `mirror_group`, so a Stremio request asks only one of them, the reachable one with the best plugin score, and the next one for a query it gives nothing for (hdfilme, streamcloud, streamkiste). A plugin that declares `locates_episodes = True` (aniworld, sto for its anime results) places a Stremio request's episode on the site's own season pages through the shared index of `infrastructure/plugins/episode_index.py` (the request's `EpisodeRef`: English title, air date, absolute number) and answers nothing for the series when no row matches, never the request's numbers.
- Every plugin MUST do category filtering (label results from site data, never with the requested category; answer requests with `served_category()` / `filter_by_category()` of `infrastructure/plugins/categories.py`, `[]` without a request for categories the site lacks), pagination up to 1000 items and bounded-concurrency detail scraping, of relevant search hits only (`relevant_hits()` of `infrastructure/plugins/relevance.py`; site searches also list loose matches). Site analysis with `playwright-mcp` comes before any code; parsers are tested on the site's own pages too (`scripts/capture_pages.py`, `tests/unit/infrastructure/test_real_pages.py`). `_domains` holds genuine domains only (check the JDownloader plugin's dead and fake/scam domain notes).
- Hoster resolvers live in `src/scavengarr/infrastructure/hoster_resolvers/`: individual streaming/DDL resolvers, 10 generic DDL hosters (`generic_ddl.py`, `GenericDDLConfig`), 25 XFS hosters (`xfs.py`, `XFSConfig`). New XFS/DDL hoster = new config constant appended to `ALL_XFS_CONFIGS` / `ALL_DDL_CONFIGS` (raise the count in its test and here), no other tests or wiring. A resolver whose CDN binds the video URL to headers the player sends itself (VEEV: User-Agent, Accept-Language) implements `ClientBoundResolverPort`; `/play` then resolves it per player. A resolver without a check of its own sets `needs_playback_check = True` (SuperVideo): the registry checks its streams with `verify_streams` off too. A resolver whose CDN plays the video URL only for the address that resolved it sets `address_bound = True` (DoodStream, MixDrop, Vinovo, FSST): the registry stamps it on the stream, and Stremio sends such a file through `/proxy/<id>/file`, a byte-range pass-through, instead of `/play`.
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
- **Checks in a worktree**: a worktree has no `.venv`, so `poetry run` creates an empty one and ends with `Command not found`. Run `scripts/worktree_venv.sh` once there (links `.venv` to the main checkout's venv); then `poetry run pre-commit run --all-files` and `poetry run pytest -n auto` work as in the main checkout (pytest imports the worktree's own `src`, `pythonpath` in `pyproject.toml`). The basedpyright hook runs `scripts/basedpyright.sh`, which finds that venv by itself.
- **Line endings**: all text files are LF, enforced editor-independently by `.gitattributes` (`* text=auto eol=lf`) and the `mixed-line-ending --fix=lf` pre-commit hook; VS Code also saves new files with LF (`files.eol` in `.vscode/settings.json`). Shell scripts with CRLF fail at the shebang (exit 127): a CRLF Claude Code hook silently allows everything. The same goes for a missing executable bit (exit 126): scripts that are run directly (`docker/entrypoint.sh`, `.claude/hooks/*.sh`, `.claude/plugins/basedpyright-lsp/scripts/langserver.sh`, `.devcontainer/*.sh`) must be stored as 100755 (`git update-index --chmod=+x`; `core.fileMode=false` hides it locally), guarded by `tests/unit/infrastructure/test_repository_files.py`.
- **Editor extensions**: `customizations.vscode.extensions` in `.devcontainer/devcontainer.json` lists only extensions available on Open VSX, so VS Code and VSCodium (DevPod) install the same set; type checking uses `detachhead.basedpyright` in `standard` mode (`[tool.basedpyright]` in `pyproject.toml`, so the editor and `poetry run basedpyright` apply the same rules), not Pylance, formatting uses ruff.
- **ruff version**: `pyproject.toml` pins ruff to exactly the `rev` of `ruff-pre-commit` in `.pre-commit-config.yaml` (the edit hook uses the venv's ruff, pre-commit its own); bump both together, otherwise the two formatters can undo each other.
- **Claude Code hooks and skills**: `.claude/hooks/` (`block-dangerous.sh` PreToolUse guard; `format-and-lint.sh` runs ruff on each edited `.py` file inside the repository and reports unfixable errors back to Claude via exit 2; it leaves unused imports (F401) to pre-commit, so an import added before its first use survives; `usage-guard.sh` keeps every session out of Anthropic's rate limit: installed as a copy in `~/.claude/hooks/` and wired in `~/.claude/settings.json` as PreToolUse and UserPromptSubmit, so every session and worktree on the machine has it, it refuses tool calls from 90 % of the 5-hour window with the release time and the parking instructions (commit, one `CronCreate` wake-up a minute after the reset, end the turn), lets `CronCreate`, `SendMessage` and plain git commits through, and prints the usage on every prompt; the usage comes from the status file (`status.json` in the `claude` directory) of the private `shared-state` repo, cached 60 s; copy it again after a change) and `.claude/skills/` (`commit`, `test`, `new-plugin`, `new-resolver`; thin checklists pointing to `docs/`) and `.claude/plugins/` (the `basedpyright-lsp` marketplace, see "Code intelligence") are versioned; the rest of `.claude/` (e.g. `settings.local.json`, which wires the hooks, and `worktrees/`) stays gitignored. After changing a hook, run `bash -n` on it: a syntax error exits 2 and blocks every Bash command.
- **Production diagnostics**: `scripts/prodctl.py` (`ps`, `stats`, `logs`, `metrics`, `state`, `probe`, `digest`: one Markdown report of a window) reads production through Portainer's Docker API with `PORTAINER_URL` and `PORTAINER_API_KEY` from `.env.devcontainer` (`scripts/portainer.py`, shared with `stremio_profile.py`). Everything it prints passes `portainer.mask()` (URLs shrink to their host; no IP addresses, tokens or credentials), and a budget shared by all processes allows 100 Portainer requests per minute. Probes in `scripts/probes/` (`resources`, `tasks`, `links`, `redis`, `anime_ids`, `hls_throughput`) run inside the container and must not change state; `series_episodes`, `title_match` and `resolver_urls` (the hoster page URLs of the live resolver tests, `.cache/live/resolver-urls.json`) run in the dev container against the sites (`PYTHONPATH=src xvfb-run -a poetry run python -P scripts/probes/series_episodes.py`; `-P` because the probe directory shadows the redis package), `sto_linkout`, `vidhide_segments` and `domains` in both (`vidhide_segments` in the dev container with `--imdb` and the dev server's `PORT`); rebuilds and restarts stay with the user.
- **Code intelligence**: `.claude/plugins/` is a local plugin marketplace (`scavengarr-dev`) with `basedpyright-lsp`, which runs the venv's `basedpyright-langserver` (the main checkout's venv in a worktree): Claude gets type diagnostics after each edit of a `.py` file and the `LSP` tool (definitions, references, hover) instead of grepping for symbols. Wire it once per machine from the main checkout: `claude plugin marketplace add ./.claude/plugins` and `claude plugin install basedpyright-lsp@scavengarr-dev --scope local`; it loads in the next session. The marketplace loads in place, so edits under `.claude/plugins/` apply without a reinstall.

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
| Production diagnostics | `scripts/prodctl.py`, `scripts/probes/`, `scripts/portainer.py`, `scripts/digest.py` |
| Feature docs / architecture / plans | `docs/features/`, `docs/architecture/`, `docs/plans/` |
| Refactor history | `docs/refactor/COMPLETED/` |
| OpenSpec change specs | `openspec/changes/` |
