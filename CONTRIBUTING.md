# Contributing to Scavengarr

Thanks for helping! New plugins and hoster resolvers are the most valuable contributions; fixes for plugins broken by site changes come right after. This guide covers setup, tests, conventions and the step-by-step guides. The complete rule set, which also applies to AI coding assistants, is in [AGENTS.md](AGENTS.md).

## Table of Contents

- [Development setup](#development-setup)
- [Running tests](#running-tests)
- [Code quality](#code-quality)
- [Project structure](#project-structure)
- [Tech stack](#tech-stack)
- [Adding a plugin](#adding-a-plugin)
- [Adding a hoster resolver](#adding-a-hoster-resolver)
- [Branches, commits and pull requests](#branches-commits-and-pull-requests)
- [Documentation](#documentation)
- [AI-assisted contributions](#ai-assisted-contributions)

---

## Development setup

Requirements: Python 3.12 or 3.13, [Poetry](https://python-poetry.org/), Git.

```bash
git clone https://github.com/Strob0t/Scavengarr.git
cd Scavengarr
git checkout staging
poetry install --with dev
poetry run pre-commit install
poetry run python -m patchright install chromium
```

Run the server locally:

```bash
poetry run start --host 0.0.0.0 --port 7979 --config data/config.yaml
# headful browser without a desktop:
xvfb-run -a poetry run start --host 0.0.0.0 --port 7979 --config data/config.yaml
```

The repository also contains a dev container (`.devcontainer/`) with Python, Node 22, Chromium and Xvfb preinstalled. On its first start it creates `.env.devcontainer` from `.env.devcontainer.example`; add your `GH_TOKEN` and git identity there and rebuild. Port 7979 is published on all interfaces of the host, so a server started in the container is reachable from the LAN (for example from Stremio). Details: [AGENTS.md → Dev container](AGENTS.md#9-dev-container).

---

## Running tests

```bash
poetry run pytest                               # unit, integration and E2E tests (offline)
poetry run pytest -m live                       # live smoke tests against the real sites (opt-in)
poetry run pytest tests/benchmark/ -s -v        # concurrency benchmarks
```

The offline suite has about 4,500 tests and must pass before every commit. CI (`.github/workflows/ci.yml`) runs pre-commit and the offline suite on every push to `staging` and on every pull request. Live tests hit real websites; a failure there means a site or hoster changed, not that your change is wrong. They do not run in CI: GitHub's datacenter IPs get harder Cloudflare challenges than a home network.

Scavengarr is developed **test-first**: write a failing test, make it pass with the smallest change, refactor, commit. Test layout and mock conventions:

| Kind | Location |
|---|---|
| Unit tests | `tests/unit/{domain,application,infrastructure,interfaces}/` |
| Plugin tests | `tests/unit/infrastructure/test_<name>_plugin.py` |
| Resolver tests | `tests/unit/infrastructure/test_<name>_resolver.py` (HTTP mocked with `respx`) |
| Integration / E2E | `tests/integration/`, `tests/e2e/` (deterministic, no external sites) |
| Live smoke tests | `tests/live/` (`-m live`) |

- `PluginRegistryPort` is synchronous → `MagicMock`; `SearchEnginePort`, `CrawlJobRepository`, `CachePort`, `PluginScoreStorePort` are async → `AsyncMock`.
- Hoster resolver tests use `respx`, not `AsyncMock`.

---

## Code quality

```bash
poetry run ruff check .          # lint
poetry run ruff format .         # format
poetry run basedpyright          # type check (standard mode)
poetry run pre-commit run --all-files   # all of the above
```

The most important Python rules (full list: [AGENTS.md → Python rules](AGENTS.md#5-python-rules-must-read)):

- `from __future__ import annotations` in every file; modern typing only (`T | None`, `list[T]`); fully typed signatures.
- Ports are `Protocol`s, entities and value objects are `@dataclass` (frozen where immutable).
- Never block the event loop: parallel I/O with bounded concurrency, CPU-heavy parsing in an executor.
- Never swallow exceptions; log with `structlog` and structured fields, never log secrets.
- Prefer the standard library and existing dependencies; new dependencies need a justification.

---

## Project structure

```text
src/scavengarr/
  domain/           # entities, value objects, ports (Protocols) — no I/O, no frameworks
  application/      # use cases, Stremio services, policies (limits, timeouts)
  infrastructure/   # adapters: plugins base classes, hoster resolvers, link validation,
                    # cache, Torznab presenter, config, logging, scoring
  interfaces/       # FastAPI routers, CLI, composition root (dependency wiring)
plugins/            # one Python file per site (41 plugins)
scripts/            # maintenance scripts (e.g. plugin list generator)
tests/              # unit, integration, E2E, live tests and benchmarks
docs/               # feature docs, architecture, plans, generated plugin list
data/config.yaml    # default configuration (mounted by Docker Compose)
```

Scavengarr follows Clean Architecture: outer layers depend on inner ones only, and the domain never imports FastAPI, httpx or diskcache. Read [docs/architecture/clean-architecture.md](docs/architecture/clean-architecture.md) before larger changes.

---

## Tech stack

| Component | Library |
|---|---|
| HTTP framework | FastAPI + Uvicorn |
| Static scraping | httpx |
| HTML parsing | stdlib `html.parser` |
| Browser scraping | Patchright (Playwright fork, Chromium) |
| Title matching | RapidFuzz |
| Release parsing | guessit |
| Configuration | pydantic-settings, PyYAML, python-dotenv |
| Caching | diskcache (SQLite) / Redis |
| Logging | structlog |
| CLI | argparse (stdlib) |

---

## Adding a plugin

A plugin is one Python file in `plugins/` that inherits from `HttpxPluginBase` (static pages, APIs) or `PlaywrightPluginBase` (sites that need a browser). The base classes handle HTTP clients, mirrors, rate limiting and browser lifecycle; never duplicate that in a plugin.

Every plugin must:

- filter by category (movies / TV) and paginate up to 1000 results,
- scrape detail pages with bounded concurrency,
- declare `name`, `provides` (`stream`, `download` or `both`), `_domains` (primary domain first, then mirrors) and, if not German, `languages`.

Analyse the site in a real browser before writing code. The full guide is [docs/features/python-plugins.md → Adding a New Plugin](docs/features/python-plugins.md).

After adding, removing or changing a plugin's metadata, regenerate the plugin list (a test fails otherwise):

```bash
poetry run python scripts/generate_plugin_list.py
```

## Adding a hoster resolver

Resolvers live in `src/scavengarr/infrastructure/hoster_resolvers/`. Hosters built on XFileSharing or a generic direct-download pattern only need a new config constant (`XFSConfig` in `xfs.py`, `GenericDDLConfig` in `generic_ddl.py`) — no new tests or wiring. Everything else gets its own resolver with `respx`-based tests. JDownloader's open-source hoster plugins are a good reference for how a hoster works. Guide: [docs/features/hoster-resolvers.md → Adding a New Resolver](docs/features/hoster-resolvers.md).

---

## Branches, commits and pull requests

- Work on `staging` (or a branch from it) and open pull requests against **`staging`**. `main` only receives release merges from `staging`.
- Keep commits small and atomic: one isolated change per commit.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/): `<type>(<scope>): <subject>`, lower-case subject, no trailing period — e.g. `fix(fireani): read episode links from the new api`.
- Before every commit: `poetry run pre-commit run --all-files` and `poetry run pytest` must pass.

## Documentation

Docs ship with the code: when behaviour, features, configuration or architecture change, update the matching files in the same commit — `CHANGELOG.md`, `docs/features/`, `docs/architecture/`, `README.md`, `AGENTS.md`. Larger changes start with a short plan in `docs/plans/` (problem, design, affected files, tests).

## AI-assisted contributions

Much of Scavengarr was written with AI coding assistants under human review, and assisted contributions are welcome under the same bar as any other: you are responsible for every line you submit, it must follow the rules above, and it must come with tests. Point your assistant at [AGENTS.md](AGENTS.md); it is written for that purpose.
