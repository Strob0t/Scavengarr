# Claude Code skills

> **Purpose:** The Claude Code skills of the dev container and their sources.
> **Status:** reference
> **As of:** b33f1b84

The skills installed for Claude Code in the dev container, grouped by their source
repository. `.devcontainer/setup.sh` installs them on every attach with the pinned
`skills` CLI (`SKILL_SOURCES`), one call per source:

```bash
npx -y skills@1.7.2 add obra/superpowers -g -y -a claude-code -s brainstorming
```

graphify is a Python package with an installer of its own (`pipx install graphifyy`,
then `graphify install --platform claude`, which writes the skill to
`~/.claude/skills/graphify/` and a three-line user-level `~/.claude/CLAUDE.md` that
points `/graphify` at it; the `graphify-mcp` server it ships is not wired). It stays
user-wide: its project option (`graphify install --project`) writes a `CLAUDE.md` into
the repository root, where `AGENTS.md` is the one instruction file, and a PreToolUse
hook into the project's Claude Code settings. Every attach writes the user-level `CLAUDE.md` again.
The caveman skills come from `CAVEMAN_SKILLS` in the same script.

Every installed skill's description costs context in every session: add a skill
deliberately and record it here. A skill nobody invoked for two weeks is removed: the
advisor (Mr. Fable) keeps the list of the skills the sessions invoke, the coders name
theirs in every push report, and a removed skill moves from `SKILL_SOURCES` to
`RETIRED_SKILLS` in `.devcontainer/setup.sh`, which removes the installed copy on the
next attach, and to the table [Removed](#removed).

## Working and communicating

| Skill | Source | Use |
|---|---|---|
| `caveman` | JuliusBrussee/caveman | terse answer style in the chat (not for docs, code or commits) |
| `ponytail` | DietrichGebert/ponytail | the smallest complete change, no over-engineering (every coding task) |
| `handoff` | mattpocock/skills | the hand-over document before a session change |
| `recall` | pwguler/skills | reconstruct the state from earlier sessions |
| `reflect` | pwguler/skills | record the lessons at a session's end |
| `humanizer` | blader/humanizer | smooth prose that reads as machine-written |
| `writing-for-agents` | mattpocock/skills | changes to `AGENTS.md`, skills and briefs (required) |

## Planning and deciding

| Skill | Source | Use |
|---|---|---|
| `brainstorming` | obra/superpowers | before new functionality; produces questions with at least three options (required) |
| `grill-me` | mattpocock/skills | sharpen a plan or decision with the user |
| `writing-plans` | obra/superpowers | a plan in `docs/plans/` before a change across several files (required) |
| `spec-driven-development` | addyosmani/agent-skills | a target document where no specification exists |
| `domain-modeling` | mattpocock/skills | glossary and data model |
| `documentation-and-adrs` | addyosmani/agent-skills | a new ADR |
| `research` | mattpocock/skills | research against primary sources |
| `api-and-interface-design` | addyosmani/agent-skills | contracts, ports and module boundaries |
| `codebase-design` | mattpocock/skills | the cut of packages and modules |

## Building

| Skill | Source | Use |
|---|---|---|
| `test-driven-development` | obra/superpowers | every piece of business logic, every bug fix (required) |
| `executing-plans` | obra/superpowers | work through a plan in the session itself |
| `subagent-driven-development` | obra/superpowers | a plan with independent tasks for subagents |
| `dispatching-parallel-agents` | obra/superpowers | two independent tasks at the same time |
| `incremental-implementation` | addyosmani/agent-skills | small, verifiable steps |
| `code-simplification` | addyosmani/agent-skills | refactoring without a change of behaviour |
| `impeccable` | pbakaus/impeccable | interfaces, design system, accessibility |
| `observability-and-instrumentation` | addyosmani/agent-skills | logs, metrics, traces |
| `performance-optimization` | addyosmani/agent-skills | queries, projections, load assumptions |
| `ci-cd-and-automation` | addyosmani/agent-skills | GitHub Actions, pre-commit |
| `diagram-design` | cathrynlavery/diagram-design | diagrams beyond Mermaid |
| `graphify` | Graphify-Labs/graphify (own installer, `graphify install`) | structure and dependency questions about the code base |

## Checking

| Skill | Source | Use |
|---|---|---|
| `systematic-debugging` | obra/superpowers | before every fix of an error or a red test (required) |
| `verification-before-completion` | obra/superpowers | run and show the checks before any "done" (required) |
| `ponytail-review` | DietrichGebert/ponytail | review of a diff or a subagent's result (required) |
| `ponytail-audit`, `ponytail-debt` | DietrichGebert/ponytail | repository audit, the list of deliberate shortcuts |
| `rubric` | pwguler/skills | defect review against criteria fixed in advance |
| `requesting-code-review`, `receiving-code-review` | obra/superpowers | request a review; check feedback technically instead of applying it blindly |
| `security-audit` | cloudflare/security-audit-skill | security review of code and services |

## Removed

| Skill | Source | Why |
|---|---|---|
| `code-review` | mattpocock/skills | Claude Code's built-in `/code-review` covers it (ledger O4) |
