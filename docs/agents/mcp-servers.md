# MCP servers

> **Status:** reference
> **As of:** d17c66f7

The MCP servers Claude Code starts for this repository, from `.mcp.json` (versioned);
`.devcontainer/setup.sh` installs what they need on every attach. Servers configured per
user (`~/.claude.json`, such as Context7 or caveman) are not listed here.

| Server | Gives | Started as (stdio) | Installed by `setup.sh` |
|---|---|---|---|
| `playwright-mcp` | A browser for the site analysis that comes before plugin work: snapshots, clicks, network requests; headless, isolated | `npx -y @playwright/mcp@0.0.82 --browser chromium --headless --isolated --no-sandbox` | The matching Chromium (`PLAYWRIGHT_MCP_VERSION`, kept in sync with `.mcp.json`) |
| `serena` | Code intelligence from a language server: symbol search and overviews, references, pattern search, symbol-level edits, project memories | `serena start-mcp-server --context claude-code --project-from-cwd --open-web-dashboard False`, `SERENA_USAGE_REPORTING=false` | `uv` through pipx, `serena-agent==2.0.0` through `uv tool install -p 3.13` (`SERENA_VERSION`), basedpyright's server through `serena download-ls-dependencies` |

## Serena

[oraios/serena](https://github.com/oraios/serena), version 2.0.0 (`uv tool upgrade serena-agent`
after bumping `SERENA_VERSION` in `setup.sh` and the version here; `serena --version`).

- **Interface**: Serena 2's REPL. Two tools: `initial_instructions` (the manual and the
  session id; call it first) and `serena_repl`, which runs Python against `s.lsp` (symbols,
  references: `find_symbol`, `get_symbols_overview`, `find_referencing_symbols`), `s.fs`
  (files, `search_for_pattern`), `s.edit` (`replace_symbol_body`, `insert_after_symbol`,
  `replace_content`), `s.mem` (memories) and `s.cfg`. The last expression of the code is
  the answer; `s.info("lsp")` documents a facade. Line numbers are 0-based. The
  `claude-code` context leaves file reads, directory listings and shell commands to Claude
  Code's own tools; the LSP tool of the `basedpyright-lsp` plugin (AGENTS.md § 9) stays the
  quickest way to a definition or hover.
- **Project**: `--project-from-cwd` takes the nearest ancestor with `.serena/project.yml`
  or `.git`, so the main checkout and every worktree are projects of their own.
  `.serena/project.yml` is versioned: the project name, `python_basedpyright` as the
  language server (it reads `[tool.basedpyright]` of `pyproject.toml`), `no-onboarding`
  (AGENTS.md is the instruction file). `.gitignore` keeps the rest of `.serena/` out of the
  repository: the language server's cache, memories, and `project.local.yml` for local
  overrides.
- **Dashboard**: `http://127.0.0.1:24282/dashboard/` (the next free port when several
  sessions run), bound to the loopback address; no browser opens on start.
- **Data**: nothing leaves the machine. Serena's anonymous usage report is off
  (`SERENA_USAGE_REPORTING=false` in `.mcp.json`); the language server is fetched once
  into `~/.serena/language_servers/`.
- **Check**: `/mcp` in Claude Code lists the server as connected; `serena project
  health-check .` in the checkout exercises the language server. A session that starts
  before the server is ready (the first start after an attach fetches nothing, but a cold
  language server takes a few seconds) reconnects through `/mcp`; `MCP_TIMEOUT=60000` in
  the shell raises Claude Code's start-up bound if it ever trips.
- **Fallback**: when Claude Code cannot find `serena` on its `PATH` (`~/.local/bin`, exported
  by `setup.sh` through `~/.bashrc`), put the executable's full path into `.mcp.json`
  locally; do not commit it.
