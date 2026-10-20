# The dev container

> **Purpose:** Setting up and working in the dev container: git access, the tools it installs, checks in a worktree, the editor, the Claude Code hooks and the production diagnostics.
> **Status:** how-to
> **As of:** b102c25c

## Goal

A dev container in which `git push`, `gh`, the checks of every commit, the browser tests and the production diagnostics work, in the main checkout and in every worktree.
The rules these steps serve are in [`AGENTS.md`](../../AGENTS.md) §9.

## Prerequisites

- VS Code or VSCodium (DevPod) with the dev container support.
- `.env.devcontainer` (not versioned): on the first start `initializeCommand` copies `.env.devcontainer.example` to it if it is missing; fill it in, then rebuild.
  It holds `GH_TOKEN`, the git identity `GIT_AUTHOR_NAME`/`GIT_AUTHOR_EMAIL`, `PORTAINER_URL` and `PORTAINER_API_KEY` for the production diagnostics and `USAGE_STATUS_REPO` for the usage guard.
  A change to it needs a container rebuild or restart.

## Steps

### Git access

`git push` and `gh` use `GH_TOKEN` from `.env.devcontainer`, and `.devcontainer/setup.sh` runs `gh auth setup-git` on every attach.

### What the setup script installs

- Node 22 comes from the devcontainer `node` feature (nvm, `/usr/local/share/nvm/current/bin`), not apt: Debian's Node 18 breaks `npx skills`.
- `setup.sh` installs npm globals without `sudo` (openspec, `@caveman-ai/cli`), a fixed subset of the caveman skills (`CAVEMAN_SKILLS`) and the skill set of [`docs/agents/skills.md`](../agents/skills.md) (`SKILL_SOURCES`, one `skills add` per source repository, plus graphify through its own installer) on every attach.
  Every installed skill's description costs context in every session, so a skill is added deliberately and recorded in that table.
- There is no Docker in the container: `playwright-mcp` runs over stdio from `.mcp.json` (`npx @playwright/mcp@<pinned>`), and `PLAYWRIGHT_MCP_VERSION` in `setup.sh` stays in sync with `.mcp.json`.
- `setup.sh` installs the matching Chromium (`playwright install --with-deps chromium`), the app's Patchright Chromium with its system libraries (`python -m patchright install --with-deps chromium`, independent of the optional MCP step) and `xvfb` with `xauth`.
  Headful browser tests run as `xvfb-run -a <cmd>`.

### A dev server for the LAN

Port 7979 is published on all host interfaces (`runArgs`: `-p 0.0.0.0:7979:7979`; `forwardPorts` would only tunnel it to the editor's machine).
LAN clients such as Stremio reach a server started in the container with `--host 0.0.0.0 --port 7979` at `http://<workstation>:7979`, and so does everyone else on the LAN.

### Checks in a worktree

A worktree has no `.venv`, so `poetry run` creates an empty one and ends with `Command not found`.
Run `scripts/worktree_venv.sh` once there: it links `.venv` to the main checkout's venv.
Then `poetry run pre-commit run --all-files` and `poetry run pytest -n auto` work as in the main checkout, and pytest imports the worktree's own `src` (`pythonpath` in `pyproject.toml`).
The ruff and basedpyright hooks run `scripts/venv_tool.sh`, which finds that venv by itself.
Everything else that imports the package runs with `PYTHONPATH=src` in a worktree: the dev server `poetry run start`, `scripts/generate_config_doc.py`, `scripts/generate_plugin_list.py` and the probes.
The venv's editable install points at the main checkout's `src`, so without it a worktree's server or generator runs the main checkout's code, and a live proof proves nothing.

### Line endings and executable bits

All text files are LF, enforced editor-independently by `.gitattributes` (`* text=auto eol=lf`) and the `mixed-line-ending --fix=lf` pre-commit hook; VS Code also saves new files with LF (`files.eol` in `.vscode/settings.json`).
Scripts that are run directly (`docker/entrypoint.sh`, `.claude/hooks/*.sh`, `.claude/plugins/basedpyright-lsp/scripts/langserver.sh`, `.devcontainer/*.sh`) are stored as 100755 with `git update-index --chmod=+x`, since `core.fileMode=false` hides the bit locally.
`tests/unit/infrastructure/test_repository_files.py` guards both.

### The editor and the formatter

`customizations.vscode.extensions` in `.devcontainer/devcontainer.json` lists only extensions available on Open VSX, so VS Code and VSCodium (DevPod) install the same set.
Type checking uses `detachhead.basedpyright` in `standard` mode, not Pylance: `[tool.basedpyright]` in `pyproject.toml` gives the editor and `poetry run basedpyright` the same rules.
Formatting uses ruff, and the edit hook and pre-commit run the same one, the venv's: pre-commit's ruff hooks are local hooks through `scripts/venv_tool.sh`, so `pyproject.toml` holds the only pin, and Dependabot's Poetry group updates it.

### Claude Code hooks and skills

`.claude/hooks/`, `.claude/skills/` and `.claude/plugins/` are versioned; the rest of `.claude/` (`settings.local.json`, which wires the hooks, and `worktrees/`) stays gitignored.

- `block-dangerous.sh` is the PreToolUse guard.
- `format-and-lint.sh` runs ruff on each edited `.py` file inside the repository and reports unfixable errors back to Claude via exit 2; it leaves unused imports (F401) to pre-commit, so an import added before its first use survives.
- `usage-guard.sh` keeps every session out of Anthropic's rate limit.
  It is installed as a copy in `~/.claude/hooks/` and wired in `~/.claude/settings.json` as PreToolUse and UserPromptSubmit, so every session and worktree on the machine has it; copy it again after a change.
  From 90 % of the 5-hour window it refuses tool calls with the release time and the parking instructions (commit, one `CronCreate` wake-up a minute after the reset, end the turn), and it lets `CronCreate`, `SendMessage` and plain git commits through.
  It prints the usage on every prompt, read from the status file (`status.json` in the `claude` directory) of the private status repo that `USAGE_STATUS_REPO` names, cached 60 s; `USAGE_GUARD_TZ` adds a local time zone to the release time.
- The skills in `.claude/skills/` (`commit`, `test`, `new-plugin`, `new-resolver`) are thin checklists that point to `docs/`.

### Code intelligence

`.claude/plugins/` is a local plugin marketplace (`scavengarr-dev`) with `basedpyright-lsp`, which runs the venv's `basedpyright-langserver` (the main checkout's venv in a worktree).
Claude gets type diagnostics after each edit of a `.py` file and the `LSP` tool (definitions, references, hover) instead of grepping for symbols.
Wire it once per machine from the main checkout with `claude plugin marketplace add ./.claude/plugins` and `claude plugin install basedpyright-lsp@scavengarr-dev --scope local`; it loads in the next session.
The marketplace loads in place, so edits under `.claude/plugins/` apply without a reinstall.

Serena (`.mcp.json`; oraios/serena 2.0.0, installed by `setup.sh` through `uv tool install`) adds the language server's symbol search, references and symbol-level edits through one `serena_repl` tool (`s.lsp`, `s.fs`, `s.edit`, `s.mem`; `initial_instructions` first).
`.serena/project.yml` is versioned (basedpyright as its language server, no onboarding), and the rest of `.serena/` is per machine.
Its dashboard listens on 127.0.0.1 only and opens no browser, and its usage reporting is off.
The servers and their flags: [`docs/agents/mcp-servers.md`](../agents/mcp-servers.md).

### Production diagnostics

`scripts/prodctl.py` (`ps`, `stats`, `logs`, `metrics`, `state`, `probe`, `digest`, `check`) reads production through Portainer's Docker API with `PORTAINER_URL` and `PORTAINER_API_KEY` from `.env.devcontainer` (`scripts/portainer.py`).
Everything it prints passes `portainer.mask()`: URLs shrink to their host, and no IP addresses, tokens or credentials show.
A budget shared by all processes allows 100 Portainer requests per minute.
Probes in `scripts/probes/` must not change state; which run in the container, which in the dev container (with `PYTHONPATH=src`) and the post-deploy check: [Operations](operations.md).
Rebuilds and restarts stay with the user.

## Verify

- `gh auth status` reports the token's account, and `git push` needs no password.
- `poetry run pre-commit run --all-files` and `poetry run pytest -n auto` pass in the main checkout and, after `scripts/worktree_venv.sh`, in a worktree.
- `xvfb-run -a true` exits 0.
- `bash -n` passes on every hook in `.claude/hooks/`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Command not found: pytest` in the main checkout | The `.venv` shebangs broke after a workspace path change: `poetry env remove --all && poetry install --with dev`. |
| `Command not found` from `poetry run` in a worktree | The worktree has no venv yet: run `scripts/worktree_venv.sh` once. |
| A worktree's dev server or generator shows the main checkout's code | It ran without `PYTHONPATH=src`. |
| `xvfb-run` fails with "xauth command not found" | `xauth` is missing: rerun `.devcontainer/setup.sh`. |
| A shell script exits 127 at its shebang | It has CRLF line endings, and a CRLF Claude Code hook silently allows everything: convert it to LF. |
| A script exits 126 | Its executable bit is missing: `git update-index --chmod=+x <path>`. |
| Every Bash command is blocked | A hook has a syntax error and exits 2: run `bash -n` on it after every change. |
