# Claude Code skills

> **Purpose:** The Claude Code skills of the dev container and their sources.
> **Status:** reference
> **As of:** b33f1b84

The skills installed for Claude Code in the dev container, grouped by their source
repository. `.devcontainer/setup.sh` installs them on every attach with the pinned
`skills` CLI (`SKILL_SOURCES`), one call per source:

```bash
npx -y skills@1.8.1 add obra/superpowers -g -y -a claude-code -s brainstorming
```

graphify is a Python package with an installer of its own (`pipx install graphifyy`,
then `graphify install --platform claude`, which writes the skill to
`~/.claude/skills/graphify/` and a three-line user-level `~/.claude/CLAUDE.md` that
points `/graphify` at it; the `graphify-mcp` server it ships is not wired). It stays
user-wide: its project option (`graphify install --project`) writes a `CLAUDE.md` into
the repository root, where `AGENTS.md` is the one instruction file, and a PreToolUse
hook into the project's Claude Code settings. Every attach writes the user-level `CLAUDE.md` again.
The caveman skill comes from `CAVEMAN_SKILLS` in the same script, which lists `caveman` only.

Every installed skill's description costs context in every session: add a skill
deliberately and record it here. Usage sets the priority of the review work, not a
removal: the advisor (Mr. Fable) keeps the list of the skills the sessions invoke, the
coders name theirs in every push report, and the skills used most are reviewed first.
No skill is removed for low use. `RETIRED_SKILLS` in `.devcontainer/setup.sh` removes
the installed copy of each skill it lists on the next attach; the table
[Removed](#removed) names them.

## Working and communicating

| Skill | Source | Use |
|---|---|---|
| `caveman` | JuliusBrussee/caveman | terse answer style in the chat (not for docs, code or commits) |
| `ponytail` | DietrichGebert/ponytail | the smallest complete change, no over-engineering (every coding task) |
| `handoff` | mattpocock/skills | the hand-over document before a session change; user-invoked only (`disable-model-invocation`) |
| `recall` | pwguler/skills | reconstruct the state from earlier sessions; user-invoked only (`disable-model-invocation`), so a headless task cannot run it |
| `reflect` | pwguler/skills | record the lessons at a session's end; user-invoked only (`disable-model-invocation`), so a headless task cannot run it |
| `humanizer` | blader/humanizer (pinned `v3.1.0`) | smooth prose that reads as machine-written; 4.0.0 may add a detail the text did not have, which docs built on evidence cannot take |
| `writing-for-agents` | mattpocock/skills | changes to `AGENTS.md`, skills and briefs (required) |

## Planning and deciding

| Skill | Source | Use |
|---|---|---|
| `brainstorming` | obra/superpowers | before new functionality (required); the design goes to `docs/plans/` or, for a spec change, `openspec/changes/`, not to the skill's `docs/superpowers/specs/`; at least three options per decision is the owner's rule, the skill sets no count |
| `grill-me` | mattpocock/skills | sharpen a plan or decision with the user; user-invoked only (`disable-model-invocation`) |
| `writing-plans` | obra/superpowers | a plan in `docs/plans/` before a change across several files (required) |
| `spec-driven-development` | addyosmani/agent-skills | settle unclear requirements where `openspec/specs/` has no spec yet; the spec change itself is an OpenSpec proposal in `openspec/changes/` (`openspec/AGENTS.md`), not the skill's `docs/` file |
| `domain-modeling` | mattpocock/skills | glossary and data model; the glossary is `docs/reference/glossary.md`, not a `GLOSSARY.md` |
| `documentation-and-adrs` | addyosmani/agent-skills | a new ADR in `docs/target/decisions/`, not the skill's `docs/adr/` or `docs/decisions/` |
| `research` | mattpocock/skills | research against primary sources |
| `api-and-interface-design` | addyosmani/agent-skills | the HTTP contracts of `src/scavengarr/interfaces/api/` (Torznab, Stremio, stats, download); not for module interfaces, which `codebase-design` covers |
| `codebase-design` | mattpocock/skills | the primary skill for module interfaces: ports, seams and the cut of packages and modules |

## Building

| Skill | Source | Use |
|---|---|---|
| `test-driven-development` | obra/superpowers | every piece of business logic, every bug fix (required) |
| `executing-plans` | obra/superpowers | work through a plan in the session itself |
| `subagent-driven-development` | obra/superpowers | a plan with independent tasks for subagents, within `AGENTS.md` section 8: only mechanical, fully specified tasks |
| `dispatching-parallel-agents` | obra/superpowers | two independent tasks at the same time |
| `incremental-implementation` | addyosmani/agent-skills | small, verifiable steps |
| `code-simplification` | addyosmani/agent-skills | a planned refactoring of existing code without a change of behaviour; the changed code of a diff goes to Claude Code's built-in `/simplify` |
| `impeccable` | pbakaus/impeccable | a user interface of the project, should one come (there is none); not for the site pages in `tests/fixtures/` |
| `observability-and-instrumentation` | addyosmani/agent-skills | logs, metrics, traces |
| `performance-optimization` | addyosmani/agent-skills | the latency of the I/O paths (searches, link validation, resolution), measured as `docs/how-to/performance-measurement.md` says; not the skill's Core Web Vitals |
| `ci-cd-and-automation` | addyosmani/agent-skills | GitHub Actions, pre-commit |
| `diagram-design` | cathrynlavery/diagram-design | only a diagram that Mermaid cannot draw; the docs draw theirs in Mermaid |
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
