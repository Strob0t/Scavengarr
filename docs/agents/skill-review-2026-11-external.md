# Review of the external Claude Code skills

> **Purpose:** The state of the external Claude Code skills against their upstream sources and the repository, with the evidence and the planned patches.
> **Status:** reference
> **As of:** 6d1483de

## Scope

Part B of 3: the external skills of `.devcontainer/setup.sh` (`SKILL_SOURCES`, `CAVEMAN_SKILLS`, `RETIRED_SKILLS`) and graphify; the own skills are in [skill-review-2026-11-own.md](skill-review-2026-11-own.md).
This review changes no skill, no `setup.sh` and no `docs/agents/skills.md`; the patches below are plans.

Each skill is checked for (a) the installed copy against the upstream `HEAD`, (b) its use line in `docs/agents/skills.md` against the skill's own text and the repository, (c) overlap with another skill, an own skill or a built-in Claude Code command and (d) the `RETIRED_SKILLS` entry against the table "Removed".
Status values: `aktuell` (correct as it is), `patchen` (refresh or lines to change), `zusammenlegen` (keep one of two), `fraglich` (use in doubt).
"Installed" is the `SKILL.md` blob in `/opt/coding-skills/.claude/skills/`, "upstream" the blob of the same path at the default branch's `HEAD`; "1 change behind" means the installed blob is the parent's blob of the last commit that touched the file.
The raw command output behind every claim is in the evidence section; a label such as E-upstream names its block.

## The skills

| Skill | Source (extern, pin/ref) | Status | Evidence | Planned patch | Done (commit) |
|---|---|---|---|---|---|
| `brainstorming` | obra/superpowers, no ref pin; `main` 22f0b463e7a5 (tag v7.0.2) | `patchen` | E-upstream: installed 7f5eef02f894, upstream 9eb0edc7fedf, +60 -38. E-net: 1 change behind (v7.0.2). E-use: SKILL.md:225 writes the design to `docs/superpowers/specs/`, :226 lets the user's preference override it. E-detail: SKILL.md:28 asks for "the approaches", no count; skills.md:47 says "at least three options". | Refresh to v7.0.2 after its release notes. skills.md:47: the design goes to `docs/plans/` or `openspec/changes/` (AGENTS.md:58), and the three options are the owner's rule, not the skill's. | 7a10696f (use line). Refresh: v7.0.2 read, no break; `setup.sh` takes `HEAD`, no commit needed. |
| `writing-plans`, `test-driven-development`, `dispatching-parallel-agents`, `systematic-debugging`, `verification-before-completion`, `requesting-code-review`, `receiving-code-review` | obra/superpowers, as above | `aktuell` | E-upstream: `same` for all seven. E-use: writing-plans SKILL.md:16 defaults to `docs/superpowers/plans/`, :17 lets a preference override it; skills.md:49 and AGENTS.md:58 set `docs/plans/`. | None. | Nothing to do. |
| `executing-plans` | obra/superpowers, as above | `patchen` | E-upstream: ac77129d7c69 against ea23196d7450, +6 -4. E-net: 1 change behind (v7.0.2). | Refresh. | No commit: `setup.sh` takes `HEAD`. |
| `subagent-driven-development` | obra/superpowers, as above | `patchen` | E-upstream: ea170ed101d6 against 45195787cb3f, +7 -4. E-net: 1 change behind (v7.0.2). E-use: SKILL.md:8 sends "a fresh implementer subagent per task"; E-overlap: AGENTS.md:173 allows subagents "Only for mechanical, fully specified tasks". | Refresh. skills.md:63 adds the limit of AGENTS.md section 8. | 7a10696f (limit). Refresh: no commit, `setup.sh` takes `HEAD`. |
| `handoff`, `grill-me` | mattpocock/skills, no ref pin; `main` 4ae2317ddb22 (tag v1.3.2) | `patchen` | E-upstream: `same` for both. E-use: both carry `disable-model-invocation: true`, so only a user's `/handoff` or `/grill-me` runs them; skills.md:37 and :48 do not say so. | skills.md marks both "user-invoked only". | 7a10696f |
| `recall`, `reflect` | pwguler/skills, no ref pin; `main` 0c8a2eb35acb (tag v1.38.0) | `patchen` | E-upstream: `same` for both. E-use: both carry `disable-model-invocation: true`; skills.md:38 and :39 describe them as session steps without that limit. | skills.md marks both "user-invoked only"; a headless task cannot run them. | 7a10696f |
| `rubric` | pwguler/skills, as above | `aktuell` | E-upstream: `same`. E-use: the description (defect review with independent read-only reviewers) matches skills.md:82. | None. | Nothing to do. |
| `writing-for-agents` | mattpocock/skills, as above | `aktuell` | E-upstream: `same`. E-use: "Use when creating or editing skills, or modifying AGENTS.md" matches skills.md:41. | None. | Nothing to do. |
| `domain-modeling` | mattpocock/skills, as above | `patchen` | E-upstream: `same`. E-use: the description names "a GLOSSARY.md"; E-repo: the glossary is `docs/reference/glossary.md`. | skills.md:51 names `docs/reference/glossary.md` as the glossary. | 7a10696f |
| `research` | mattpocock/skills, as above | `patchen` | E-upstream: fecee97e9457 against ffc9eb89849c, +1 -1. E-net: 1 change behind (7739eaed7f8f). E-extra: upstream line 6 adds "Start it with a fresh context window." | Refresh. | No commit: `setup.sh` takes `HEAD`. |
| `api-and-interface-design`, `codebase-design` | addyosmani/agent-skills (`main` 301b02b12bff, tag 0.6.15) and mattpocock/skills | `zusammenlegen` | E-use: api "Use when designing APIs, module boundaries, or any public interface", codebase-design "design or improve a module's interface"; skills.md:54 "contracts, ports and module boundaries", :55 "the cut of packages and modules". E-upstream: api 3e2e37f8f4fb against ce58d72ed0ee, +3 -1, 1 change behind; codebase-design `same`. | The maintainer keeps one skill for module interfaces and removes the other through `RETIRED_SKILLS`; until then refresh api-and-interface-design. | 9b674bb1: both kept with scope lines ([Phase 2 decisions](#phase-2-decisions)). |
| `spec-driven-development` | addyosmani/agent-skills, as above | `fraglich` | E-upstream: f54426bec00b against 3fcc536f14f2, +1 -1, 1 change behind. E-overlap: AGENTS.md:6 and :11 send proposals and specs to `openspec/AGENTS.md`, `openspec/changes/` holds 6 entries; E-use: SKILL.md:103 writes to `docs/`; brainstorming also writes a design. | The maintainer decides between OpenSpec and this skill for specs; skills.md:50 names the one in force. | 9b674bb1: kept with a scope line, OpenSpec for spec changes. |
| `documentation-and-adrs` | addyosmani/agent-skills, as above | `patchen` | E-upstream: ecc28cef82b6 against eff8fddc25d9, +3 -2, 1 change behind. E-use: SKILL.md:40 `docs/adr/`, :48 `docs/decisions/`; E-repo: AGENTS.md:55 `docs/target/decisions/`. | Refresh. skills.md:52 names `docs/target/decisions/`. | 7a10696f (use line). Refresh: no commit, `setup.sh` takes `HEAD`. |
| `incremental-implementation`, `observability-and-instrumentation` | addyosmani/agent-skills, as above | `aktuell` | E-upstream: `same` for both; E-use: the descriptions match skills.md:65 and :68. | None. | Nothing to do. |
| `code-simplification` | addyosmani/agent-skills, as above | `fraglich` | E-upstream: 239b28484895 against c4f794aa3fc6, +1 -1, 1 change behind. The skill list of this session (no command output) carries Claude Code's built-in `simplify` ("Review the changed code for reuse, simplification ... then apply the fixes"), the reason skills.md:90 gives for `code-review`. | The maintainer decides a retirement like `code-review`'s; else refresh. | 9b674bb1: kept with a scope line next to the built-in `/simplify`. |
| `performance-optimization` | addyosmani/agent-skills, as above | `patchen` | E-upstream: ff57eab055e7 against 296cf3f79eaa, +2 -2, 1 change behind. skills.md:69 says "queries, projections, load assumptions"; E-extra: `projection` is in 0 files of `src/`, SKILL.md:16 starts from Core Web Vitals. | Refresh. skills.md:69: latency of the I/O paths, measured as `docs/how-to/performance-measurement.md` says. | 7a10696f (use line). Refresh: no commit, `setup.sh` takes `HEAD`. |
| `ci-cd-and-automation` | addyosmani/agent-skills, as above | `patchen` | E-upstream: 118456fcb102 against 531bb8f08595, +7 -4. E-net: 3 changes behind (from d3ffa3cabd0d). The use line skills.md:70 matches the description. | Refresh. | No commit: `setup.sh` takes `HEAD`. |
| `ponytail`, `ponytail-review`, `ponytail-audit`, `ponytail-debt` | DietrichGebert/ponytail, no ref pin; `main` 96608e078b2d (tag v5.3.3) | `aktuell` | E-upstream: `same` for all four. Overlap of `ponytail-review` with other review skills: see [Overlap](#overlap). | None. | Nothing to do. |
| `humanizer` | blader/humanizer, no ref pin; `main` b48c832f0f80 (tag v4.0.0) | `patchen` | E-upstream: 19fc634208e8 against 175bf05f45ae, +151 -50. E-net: older than the last 3 changes. E-detail: installed `version: "3.1.0"`; E-extra: upstream `version: "4.0.0"`. E-upstream: 16 commits since 2026-11-01. | Read the 4.0.0 changes, then refresh; an unpinned source takes a major release unread. | 4b489405: no bump, pinned to v3.1.0 after the 4.0.0 notes. |
| `impeccable` | pbakaus/impeccable, no ref pin; `main` c1e0e5492fbb (tag skill-v4.8.2) | `fraglich` | E-repo: installed `version: 4.7.1`, upstream `version: 4.8.2`; E-upstream: 213 commits since 2026-11-01. E-repo: the only tracked HTML, CSS and JS files are 6 test fixtures; the project has no interface of its own. | The maintainer decides a retirement; else refresh. | 9b674bb1: kept with a scope line. Refresh: 4.8.2 read, no break; `setup.sh` takes `HEAD`. |
| `diagram-design` | cathrynlavery/diagram-design, no ref pin, no tag; `main` f2496acbde9f | `fraglich` | E-upstream: e6162989c378 against 22544df735bf, +11 -11; E-net: older than the last 3 changes; 34 commits since 2026-11-01. E-extra: 0 tracked SVG files; E-overlap: 14 files of `docs/` use Mermaid; skills.md:71 "diagrams beyond Mermaid". | The maintainer decides a retirement; else refresh. | 9b674bb1: kept with a scope line. Refresh: no commit, `setup.sh` takes `HEAD`. |
| `security-audit` | cloudflare/security-audit-skill, no ref pin, no tag; `main` c1c8a8c14710 | `aktuell` | E-upstream: `same`; last upstream commit 2026-09-14. | None. | Nothing to do. |
| `caveman` (and `CAVEMAN_SKILLS`) | JuliusBrussee/caveman, no ref pin; `main` 3dc497a7f858 (tag v4.1.0) | `patchen` | E-upstream: `caveman` `same`; `cavecrew`, `caveman-explore`, `caveman-review`, `caveman-compress`, `caveman-help`, `caveman-stats` are `not-installed`. E-sources: setup.sh:51 lists all seven, skills.md names the six 0 times (`exit=1`). | Recommended: `CAVEMAN_SKILLS=(caveman)`, the set skills.md records. Alternative: skills.md records the six. | c0048d39 (recommended route). |
| `graphify` | Graphify-Labs/graphify (own installer); default branch `v8` 35d1d2038b9e, tag v0.9.106 | `patchen` | E-detail: `graphify 0.9.92` installed; E-upstream: newest tag v0.9.106, 293 commits since 2026-11-01. skills.md:72 matches the description (E-use). | Upgrade the package (`pipx upgrade graphifyy`), then `graphify install --platform claude` again. | Not done: an operator step in the dev container, outside `SKILL_SOURCES`; no `pipx` in this sandbox. |
| `code-review` (retired) | mattpocock/skills | `patchen` | E-sources: setup.sh:79 `RETIRED_SKILLS=(code-review)` and skills.md:90 agree, but `ls -d $S/code-review` gives `exit=0`, and the skill list of this session (no command output) still offers it. setup.sh:82 tests `$HOME/.claude/skills/$skill`; E-repo: that folder holds only `synced`, the installed set is the mount `/opt/coding-skills/.claude/skills`. | The build of the mount applies `RETIRED_SKILLS` too, or setup.sh tests the folder the skills are installed in. | Not done here: `setup.sh` does not build `$S`; the workbench image's skill lock outside this repository installs it, a follow-up for its owner. |

## Overlap

- Reviews: `ponytail-review`, `rubric`, `requesting-code-review`, the built-in `/code-review` and `/security-review`, and the retired but installed `code-review` all review a diff. skills.md:80 makes `ponytail-review` the required one; the others need a line on when they run instead.
- Specs and plans: `brainstorming` (design to `docs/superpowers/specs/`), `spec-driven-development` (`docs/`), `writing-plans` (`docs/plans/` by override) and OpenSpec (`openspec/changes/`, AGENTS.md:6). One home per artifact: plans in `docs/plans/`, spec changes in `openspec/changes/` (AGENTS.md:58).
- Module interfaces: `api-and-interface-design` and `codebase-design` (row above).
- Own skills: `ci-cd-and-automation` and `verification-before-completion` touch the gates of the own `commit` skill (part A); `caveman-commit` stays out because of it (E-overlap: setup.sh:49).

## Pins and install path

- `SKILL_SOURCES` pins no ref: every attach installs the upstream `HEAD` of each source (setup.sh:74). Only the CLI is pinned: `skills@1.7.2` (setup.sh:50), the newest is 1.8.1 (E-net).
- The installed copies date from 2026-11-01 (E-sources); 13 of the 35 skill files of `SKILL_SOURCES` differ from upstream, 9 of them by 1 change, `ci-cd-and-automation` by 3 and three by more than 3 (E-upstream, E-net).
- No source is archived; all 11 sources answered (E-upstream).
- The skills of this environment are not in `$HOME/.claude/skills`, where `setup.sh` installs and retires them, but in a mount (E-repo); `setup.sh` does not build this environment's set.

## Phase 2 decisions

The owner handed the cases `zusammenlegen` and `fraglich` to phase 2. The owner decision of 2026-11-09 allows two results: keep, or keep with a scope line in `docs/agents/skills.md` (use for X, not for Y). No skill is removed and no external skill is forked.
`$S` is `/opt/coding-skills/.claude/skills`.

| Case | Decision | Evidence (command: output) |
|---|---|---|
| `api-and-interface-design` vs `codebase-design` | Keep both, with scope lines. `codebase-design` is the primary skill for module interfaces (ports, seams, the cut of modules). `api-and-interface-design` covers the HTTP contracts of `src/scavengarr/interfaces/api/`. | `grep -h -m1 -E '^description:' $S/api-and-interface-design/SKILL.md $S/codebase-design/SKILL.md`: "Use when designing APIs, module boundaries, or any public interface. Use when creating REST or GraphQL endpoints, ..." and "Shared vocabulary for designing deep modules. Use when the user wants to design or improve a module's interface, ... decide where a seam goes, ...". `ls src/scavengarr/interfaces/api`: `download`, `middleware.py`, `stats`, `stremio`, `torznab`. |
| `spec-driven-development` vs OpenSpec | Keep, with a scope line. The repository uses OpenSpec, so a spec change is an OpenSpec proposal in `openspec/changes/`. The skill only settles unclear requirements where `openspec/specs/` has no spec yet. | `ls openspec/changes openspec/specs`: 6 entries in `changes/` (`add-container-image`, `add-stall-watchdog`, `archive`, `keep-capture-state`, `react-to-challenges`, `resume-idle-reset-files`) and 10 specs (`configuration` to `stremio-stream-quality`). |
| `code-simplification` vs the built-in `simplify` | Keep, with a scope line. The built-in `/simplify` exists and reviews the changed code of a diff. The skill is for a planned refactoring of existing code. | `grep -a -o -m1 'Review the changed code for reuse, simplification, efficiency' /usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe`: the string, `exit=0`. |
| `impeccable` | Keep, with a scope line. Use it only for a user interface of the project (there is none now), not for the site pages in `tests/fixtures/`. | `git ls-files '*.html' '*.css' '*.js' '*.ts' '*.tsx' '*.vue'`: 6 files, all in `tests/fixtures/domain_lists/`. |
| `diagram-design` | Keep, with a scope line. Use it only for a diagram that Mermaid cannot draw. | `printf 'svg=%s mermaid_docs=%s\n' "$(git ls-files '*.svg' \| wc -l)" "$(grep -rl -i mermaid docs \| wc -l)"`: `svg=0 mermaid_docs=15` (phase 1 counted 14). |

The release notes read in phase 2, before a refresh or a bump:

- superpowers v7.0.2: brainstorming gives its options with a recommendation and asks fewer questions, and the subagent-driven-development helpers take `-C`. Nothing breaks the use here, so the unpinned source stays as it is.
- humanizer 4.0.0: "A rewrite may now contain a detail that was not in your text. If you rely on the old rule, stay on v3.1.0." The docs rest on evidence, so `SKILL_SOURCES` pins `blader/humanizer@v3.1.0`.
- impeccable 4.8.2: the changes are on the side of its CLI (outage messages, `OPENAI_BASE_URL`, `impeccable agents`). The skill keeps its use. 4.9.0 makes the CLI upload data when signed in. The CLI is not installed here.
- skills CLI 1.8.1: list options such as `-s` still take several values in `add` and `remove`, and the Node engine stays `>=22.20.0`. `owner/repo@v1.2.3` checks out a tag (1.8.0). A live run into a temporary config directory added 3 skills and removed 1, with `exit=0` each. npx also gives `exit=0` when a copy fails, so the `|| echo WARN` lines of `setup.sh` do not catch such a failure.

A refresh needs no change of `setup.sh`: every attach installs the upstream `HEAD` of each unpinned source. The copies in `$S` come from an image built outside this repository, which pins its own commits (superpowers at v7.0.1). That image also still installs the retired `code-review`. Both are follow-ups for the owner of that build.

## Limits

- Only `SKILL.md` is compared; the other files of a skill folder (references, scripts) are not.
- "Aktuell" holds at the queries of this review; with no ref pin, the next attach installs whatever `HEAD` is then.
- The usage record ends on 2026-10-25 (E-usage), so no status rests on invocation counts; the two-week rule of skills.md:25 needs a new record.
- The first two GraphQL queries, which asked for whole file trees, answered `gh: HTTP 502`; the smaller path queries in E-upstream and E-net answered.
- No clone of an upstream repository; the release notes of v7.0.2, humanizer 4.0.0 and impeccable 4.8.2 are not read.

## Evidence

Each block is the output of `bash .devdata/review-ext/evidence.sh <section>` from the repository root, unchanged except for the trailing blanks that `cut -c` leaves (stripped for the whitespace hook): `$ <command>`, its output, `exit=<code>`.
`$S` is `/opt/coding-skills/.claude/skills`, `$D` is `.devdata/review-ext`; the `.graphql`, `.jq` and `.py` helpers and the fetched `.json` files lie there.

### E-sources

```text
$ grep -n 'SKILLS_CLI=\|CAVEMAN_SKILLS=\|RETIRED_SKILLS=\|HOME/.claude/skills' .devcontainer/setup.sh
50:SKILLS_CLI="skills@1.7.2"
51:CAVEMAN_SKILLS=(caveman cavecrew caveman-explore caveman-review caveman-compress caveman-help caveman-stats)
79:RETIRED_SKILLS=(code-review)
82:  [ -d "$HOME/.claude/skills/$skill" ] && installed_retired+=("$skill")
exit=0
$ ls $S | wc -l
38
exit=0
$ ls -d $S/code-review $S/graphify
/opt/coding-skills/.claude/skills/code-review
/opt/coding-skills/.claude/skills/graphify
exit=0
$ grep -m2 -E '^(name|description):' $S/code-review/SKILL.md | cut -c1-150
name: code-review
description: "Review the changes since a fixed point (commit, branch, tag, or merge-base) along two axes: Standards (does the code follow this repo's
exit=0
$ grep -n -F "\`code-review\`" docs/agents/skills.md
90:| `code-review` | mattpocock/skills | Claude Code's built-in `/code-review` covers it (ledger O4) |
exit=0
$ grep -c -E 'cavecrew|caveman-(explore|review|compress|help|stats)' docs/agents/skills.md
0
exit=1
$ date -u -r $S/brainstorming/SKILL.md +%F
2026-11-01
exit=0
$ comm -3 <(sed '/^## Removed/q' docs/agents/skills.md | grep -oE '^\| `[a-z-]+`(, `[a-z-]+`)?' | grep -oE '[a-z-]+' | sort -u) <(ls $S | sort)
	code-review
exit=0
$ grep -n -i -m3 'version\|pip\|graphifyy' $S/graphify/SKILL.md | cut -c1-120
13:/graphify                                             # full pipeline on current directory (HTML viz; add --obsidian
14:/graphify <path>                                      # full pipeline on specific path
15:/graphify https://github.com/<owner>/<repo>           # clone repo then run full pipeline on it
exit=0
```

### E-upstream

```text
$ jq -r -f $D/upstream.jq $D/upstream.json
## obra/superpowers archived=false branch=main head=22f0b463e7a5 date=2026-11-03 commits_since_2026-11-01=1 newest_tag=v7.0.2
## mattpocock/skills archived=false branch=main head=4ae2317ddb22 date=2026-10-31 commits_since_2026-11-01=0 newest_tag=v1.3.2
## pwguler/skills archived=false branch=main head=0c8a2eb35acb date=2026-10-16 commits_since_2026-11-01=0 newest_tag=v1.38.0
## DietrichGebert/ponytail archived=false branch=main head=96608e078b2d date=2026-10-29 commits_since_2026-11-01=0 newest_tag=v5.3.3
## blader/humanizer archived=false branch=main head=b48c832f0f80 date=2026-11-10 commits_since_2026-11-01=16 newest_tag=v4.0.0
## addyosmani/agent-skills archived=false branch=main head=301b02b12bff date=2026-11-01 commits_since_2026-11-01=2 newest_tag=0.6.15
## pbakaus/impeccable archived=false branch=main head=c1e0e5492fbb date=2026-11-11 commits_since_2026-11-01=213 newest_tag=skill-v4.8.2
## cathrynlavery/diagram-design archived=false branch=main head=f2496acbde9f date=2026-11-09 commits_since_2026-11-01=34 newest_tag=none
## cloudflare/security-audit-skill archived=false branch=main head=c1c8a8c14710 date=2026-09-14 commits_since_2026-11-01=0 newest_tag=none
## JuliusBrussee/caveman archived=false branch=main head=3dc497a7f858 date=2026-11-09 commits_since_2026-11-01=117 newest_tag=v4.1.0
## Graphify-Labs/graphify archived=false branch=v8 head=35d1d2038b9e date=2026-11-10 commits_since_2026-11-01=293 newest_tag=v0.9.106
exit=0
$ python3 -I $D/compare.py compare .devcontainer/setup.sh $D/blobs.json $S
obra/superpowers brainstorming: installed=7f5eef02f894 upstream=9eb0edc7fedf (skills/brainstorming/SKILL.md) differs
obra/superpowers writing-plans: installed=4cf0275d88d6 upstream=4cf0275d88d6 (skills/writing-plans/SKILL.md) same
obra/superpowers test-driven-development: installed=46838cc9e893 upstream=46838cc9e893 (skills/test-driven-development/SKILL.md) same
obra/superpowers executing-plans: installed=ac77129d7c69 upstream=ea23196d7450 (skills/executing-plans/SKILL.md) differs
obra/superpowers subagent-driven-development: installed=ea170ed101d6 upstream=45195787cb3f (skills/subagent-driven-development/SKILL.md) differs
obra/superpowers dispatching-parallel-agents: installed=3fa091b348bb upstream=3fa091b348bb (skills/dispatching-parallel-agents/SKILL.md) same
obra/superpowers systematic-debugging: installed=62fc752aaef5 upstream=62fc752aaef5 (skills/systematic-debugging/SKILL.md) same
obra/superpowers verification-before-completion: installed=7d45333cc4a4 upstream=7d45333cc4a4 (skills/verification-before-completion/SKILL.md) same
obra/superpowers requesting-code-review: installed=68d338a58ecd upstream=68d338a58ecd (skills/requesting-code-review/SKILL.md) same
obra/superpowers receiving-code-review: installed=950da7b74bf6 upstream=950da7b74bf6 (skills/receiving-code-review/SKILL.md) same
mattpocock/skills handoff: installed=a224edc643a2 upstream=a224edc643a2 (skills/productivity/handoff/SKILL.md) same
mattpocock/skills writing-for-agents: installed=a37608daf6e8 upstream=a37608daf6e8 (skills/productivity/writing-for-agents/SKILL.md) same
mattpocock/skills grill-me: installed=3947ff9c4ad9 upstream=3947ff9c4ad9 (skills/productivity/grill-me/SKILL.md) same
mattpocock/skills domain-modeling: installed=a4438c6879c5 upstream=a4438c6879c5 (skills/engineering/domain-modeling/SKILL.md) same
mattpocock/skills research: installed=fecee97e9457 upstream=ffc9eb89849c (skills/engineering/research/SKILL.md) differs
mattpocock/skills codebase-design: installed=3f63c8146dd2 upstream=3f63c8146dd2 (skills/engineering/codebase-design/SKILL.md) same
pwguler/skills recall: installed=7e671842cf94 upstream=7e671842cf94 (skills/recall/SKILL.md) same
pwguler/skills reflect: installed=cafe94ff8b7a upstream=cafe94ff8b7a (skills/reflect/SKILL.md) same
pwguler/skills rubric: installed=ef4ecfabf6cf upstream=ef4ecfabf6cf (skills/rubric/SKILL.md) same
DietrichGebert/ponytail ponytail: installed=d64edd32265b upstream=d64edd32265b (skills/ponytail/SKILL.md) same
DietrichGebert/ponytail ponytail-review: installed=a50e3ac6ded6 upstream=a50e3ac6ded6 (skills/ponytail-review/SKILL.md) same
DietrichGebert/ponytail ponytail-audit: installed=ff9a95480700 upstream=ff9a95480700 (skills/ponytail-audit/SKILL.md) same
DietrichGebert/ponytail ponytail-debt: installed=3124f520a65d upstream=3124f520a65d (skills/ponytail-debt/SKILL.md) same
blader/humanizer humanizer: installed=19fc634208e8 upstream=175bf05f45ae ((root)humanizer/SKILL.md) differs
addyosmani/agent-skills spec-driven-development: installed=f54426bec00b upstream=3fcc536f14f2 (skills/spec-driven-development/SKILL.md) differs
addyosmani/agent-skills documentation-and-adrs: installed=ecc28cef82b6 upstream=eff8fddc25d9 (skills/documentation-and-adrs/SKILL.md) differs
addyosmani/agent-skills api-and-interface-design: installed=3e2e37f8f4fb upstream=ce58d72ed0ee (skills/api-and-interface-design/SKILL.md) differs
addyosmani/agent-skills incremental-implementation: installed=9c4f49bc841a upstream=9c4f49bc841a (skills/incremental-implementation/SKILL.md) same
addyosmani/agent-skills code-simplification: installed=239b28484895 upstream=c4f794aa3fc6 (skills/code-simplification/SKILL.md) differs
addyosmani/agent-skills observability-and-instrumentation: installed=84d9cb373467 upstream=84d9cb373467 (skills/observability-and-instrumentation/SKILL.md) same
addyosmani/agent-skills performance-optimization: installed=ff57eab055e7 upstream=296cf3f79eaa (skills/performance-optimization/SKILL.md) differs
addyosmani/agent-skills ci-cd-and-automation: installed=118456fcb102 upstream=531bb8f08595 (skills/ci-cd-and-automation/SKILL.md) differs
pbakaus/impeccable impeccable: installed=e5b0ecd98c59 upstream=d84acd41a9c1 (.claude/skills/impeccable/SKILL.md) differs
cathrynlavery/diagram-design diagram-design: installed=e6162989c378 upstream=22544df735bf (skills/diagram-design/SKILL.md) differs
cloudflare/security-audit-skill security-audit: installed=92178dad304d upstream=92178dad304d (skills/security-audit/SKILL.md) same
JuliusBrussee/caveman caveman: installed=9a94044529e5 upstream=9a94044529e5 (skills/caveman/SKILL.md) same
JuliusBrussee/caveman cavecrew: installed=not-installed upstream=1cf8b46a6bd9 (skills/cavecrew/SKILL.md) differs
JuliusBrussee/caveman caveman-explore: installed=not-installed upstream=5bc2aa79833d (skills/caveman-explore/SKILL.md) differs
JuliusBrussee/caveman caveman-review: installed=not-installed upstream=ed2bf49948c1 (skills/caveman-review/SKILL.md) differs
JuliusBrussee/caveman caveman-compress: installed=not-installed upstream=8afb16585dfd (skills/caveman-compress/SKILL.md) differs
JuliusBrussee/caveman caveman-help: installed=not-installed upstream=7805b3748cb9 (skills/caveman-help/SKILL.md) differs
JuliusBrussee/caveman caveman-stats: installed=not-installed upstream=681136b629ca (skills/caveman-stats/SKILL.md) differs
exit=0
$ python3 -I $D/fetch.py diff $D/texts.json $S $D/upstream
## obra/superpowers skills/brainstorming/SKILL.md upstream=9eb0edc7fedf
  22f0b463e7a5 2026-11-03 Release v7.0.2: brainstorming tuning, harness bootstrap fixes, SDD he…
  2ce8aa15d05b 2026-10-21 Release v7.0.1: brainstorming retune, skills that better survive comp…
  bb92a7774141 2026-10-10 Release v7.0.0: rebuilt brainstorming, Antigravity Marketplace manife…
  installed_lines=294 upstream_lines=316 +60 -38
## obra/superpowers skills/executing-plans/SKILL.md upstream=ea23196d7450
  22f0b463e7a5 2026-11-03 Release v7.0.2: brainstorming tuning, harness bootstrap fixes, SDD he…
  2ce8aa15d05b 2026-10-21 Release v7.0.1: brainstorming retune, skills that better survive comp…
  5bf4e7801107 2026-09-19 Release v6.4.1: diagnosing-superpowers, Native plan execution, OpenCo…
  installed_lines=321 upstream_lines=323 +6 -4
## obra/superpowers skills/subagent-driven-development/SKILL.md upstream=45195787cb3f
  22f0b463e7a5 2026-11-03 Release v7.0.2: brainstorming tuning, harness bootstrap fixes, SDD he…
  2ce8aa15d05b 2026-10-21 Release v7.0.1: brainstorming retune, skills that better survive comp…
  5bf4e7801107 2026-09-19 Release v6.4.1: diagnosing-superpowers, Native plan execution, OpenCo…
  installed_lines=336 upstream_lines=339 +7 -4
## mattpocock/skills skills/engineering/research/SKILL.md upstream=ffc9eb89849c
  7739eaed7f8f 2026-10-29 Start subagents with a fresh context window, not a fork (#1341)
  321658273cb1 2026-08-19 Remove all em-dashes from the repo
  0d74d01cbc64 2026-07-01 feat: add the research skill
  installed_lines=12 upstream_lines=12 +1 -1
## blader/humanizer SKILL.md upstream=175bf05f45ae
  8b067c507fd1 2026-11-10 Release 4.0.0
  9b036932684b 2026-11-10 Make the After examples read better
  9e561abdec8e 2026-11-10 Allow added specifics and rework the After examples
  installed_lines=397 upstream_lines=498 +151 -50
## addyosmani/agent-skills skills/spec-driven-development/SKILL.md upstream=3fcc536f14f2
  000499946182 2026-10-15 fix(skills): pass --fix through npm run to the linter
  7ade9d30f21a 2026-10-02 fix(spec): stop the turn after writing the spec
  bc97fd46fdb2 2026-09-12 Merge #554: clarify external artifact ownership
  installed_lines=259 upstream_lines=259 +1 -1
## addyosmani/agent-skills skills/documentation-and-adrs/SKILL.md upstream=eff8fddc25d9
  8e6726dc1dea 2026-10-19 fix(documentation-and-adrs): make the GOOD comment describe the fixed…
  30f886278158 2026-10-08 docs: keep prose review tool-neutral
  fe9f1f26e0fe 2026-10-07 docs: add source-preserving prose review to documentation skill
  installed_lines=297 upstream_lines=298 +3 -2
## addyosmani/agent-skills skills/api-and-interface-design/SKILL.md upstream=ce58d72ed0ee
  63128d0d4515 2026-10-19 fix(api-and-interface-design): return the skill's error shape from th…
  5f7865623e92 2026-10-02 fix(skills): throw statement
  ce89b03cbd76 2026-08-13 docs(api): add idempotency-key implementation guidance
  installed_lines=367 upstream_lines=369 +3 -1
## addyosmani/agent-skills skills/code-simplification/SKILL.md upstream=c4f794aa3fc6
  f1f6fbf39810 2026-10-16 fix(code-simplification): say the skill applies when the code is past…
  fea75b16472b 2026-03-31 Align skill descriptions with Anthropic best practices
  702a894889a9 2026-03-31 Soften prescriptive language for Claude 4.6 compatibility
  installed_lines=331 upstream_lines=331 +1 -1
## addyosmani/agent-skills skills/performance-optimization/SKILL.md upstream=296cf3f79eaa
  ee3e8504246b 2026-10-23 fix(performance): invoke Lighthouse CI, not the unscoped lhci package
  35c3acb42ed1 2026-10-11 fix(docs): label JavaScript blocks as JavaScript; Codex manifest says…
  b67408fa3a94 2026-10-03 fix(performance-optimization): restore index qualifiers, ANALYZE case…
  installed_lines=267 upstream_lines=267 +2 -2
## addyosmani/agent-skills skills/ci-cd-and-automation/SKILL.md upstream=531bb8f08595
  a4a21f6390a8 2026-10-29 Merge #711: say setup-node caches the package-manager cache
  076030da7604 2026-10-29 Merge #691: pass the rollback version through env, not straight into run
  d3ffa3cabd0d 2026-10-19 fix(ci-cd-and-automation): setup-node caches the npm cache, not node_…
  installed_lines=390 upstream_lines=393 +7 -4
## pbakaus/impeccable .claude/skills/impeccable/SKILL.md upstream=d84acd41a9c1
  b90d56fd244b 2026-11-09 Release skill 4.8.2
  655be24ce38d 2026-11-07 Release 4.8.1
  8c7b98c0ae13 2026-11-05 Sync generated provider output
  installed_lines=88 upstream_lines=88 +4 -4
## cathrynlavery/diagram-design skills/diagram-design/SKILL.md upstream=22544df735bf
  6869b1e323e4 2026-11-09 feat(import): redraw PlantUML sequence and class diagrams (#358)
  eb4605347044 2026-11-09 feat(skill): ship the geometry verifier inside the skill package (#513)
  1241c49c8b39 2026-11-09 feat(dependency): add arc diagram variant for skip-span imports (#436)
  installed_lines=394 upstream_lines=394 +11 -11
exit=0
```

### E-net

```text
$ npm view skills dist-tags.latest --cache $D/npm-cache
1.8.1
npm notice
npm notice New major version of npm available! 10.9.9 -> 12.2.2
npm notice Changelog: https://github.com/npm/cli/releases/tag/v12.2.2
npm notice To update run: npm install -g npm@12.2.2
npm notice
exit=0
$ gh api graphql -f query="$(python3 -I $D/fetch.py pinquery $D/texts.json)" > $D/pins.json
exit=0
$ python3 -I $D/fetch.py pin $D/texts.json $D/pins.json $S
obra/superpowers brainstorming: installed=7f5eef02f894 matches parent of 22f0b463e7a5 (2026-11-03), 1 change(s) behind
obra/superpowers executing-plans: installed=ac77129d7c69 matches parent of 22f0b463e7a5 (2026-11-03), 1 change(s) behind
obra/superpowers subagent-driven-development: installed=ea170ed101d6 matches parent of 22f0b463e7a5 (2026-11-03), 1 change(s) behind
mattpocock/skills research: installed=fecee97e9457 matches parent of 7739eaed7f8f (2026-10-29), 1 change(s) behind
blader/humanizer humanizer: installed=19fc634208e8 matches older than the last 3 changes
addyosmani/agent-skills spec-driven-development: installed=f54426bec00b matches parent of 000499946182 (2026-10-15), 1 change(s) behind
addyosmani/agent-skills documentation-and-adrs: installed=ecc28cef82b6 matches parent of 8e6726dc1dea (2026-10-19), 1 change(s) behind
addyosmani/agent-skills api-and-interface-design: installed=3e2e37f8f4fb matches parent of 63128d0d4515 (2026-10-19), 1 change(s) behind
addyosmani/agent-skills code-simplification: installed=239b28484895 matches parent of f1f6fbf39810 (2026-10-16), 1 change(s) behind
addyosmani/agent-skills performance-optimization: installed=ff57eab055e7 matches parent of ee3e8504246b (2026-10-23), 1 change(s) behind
addyosmani/agent-skills ci-cd-and-automation: installed=118456fcb102 matches parent of d3ffa3cabd0d (2026-10-19), 3 change(s) behind
pbakaus/impeccable impeccable: installed=e5b0ecd98c59 matches older than the last 3 changes
cathrynlavery/diagram-design diagram-design: installed=e6162989c378 matches older than the last 3 changes
exit=0
```

### E-use

```text
$ for d in $S/*/; do printf "%s: %s\n" "$(basename "$d")" "$(grep -m1 "^description:" "$d/SKILL.md" | cut -c14-150)"; done
api-and-interface-design: Guides stable API and interface design. Use when designing APIs, module boundaries, or any public interface. Use when creating REST or Gr
brainstorming: "You MUST use this before any creative work - creating features, building components, adding functionality, modifying behavior, or planni
caveman: >
ci-cd-and-automation: Automates CI/CD pipeline setup. Use when setting up or modifying build and deployment pipelines. Use when you need to automate quality ga
code-review: "Review the changes since a fixed point (commit, branch, tag, or merge-base) along two axes: Standards (does the code follow this repo's
code-simplification: Simplifies code for clarity. Use when refactoring code for clarity without changing behavior. Use when code works but is harder to read,
codebase-design: Shared vocabulary for designing deep modules. Use when the user wants to design or improve a module's interface, find deepening opportuni
diagram-design: Create branded architecture, architecture delta, IT current-state, flowchart, sequence, state machine, ER/data model, timeline, swimlane,
dispatching-parallel-agents: Use when facing 2+ independent tasks that can be worked on without shared state or sequential dependencies
documentation-and-adrs: Records decisions and documentation. Use when you need to document an architecture decision (ADR) or the reasoning behind a design choice
domain-modeling: Build and sharpen a project's domain model. Use when discussing codebase terminology, writing or editing a GLOSSARY.md, or recording or e
executing-plans: Use when executing an implementation plan in the current session as the implementer yourself — your human partner chose inline executio
graphify: "Use for any question about a codebase, its architecture, file relationships, or project content — especially when graphify-out/ exists
grill-me: A relentless interview to sharpen a plan or design.
handoff: Compact the current conversation into a handoff document for another agent to pick up.
humanizer: |
impeccable: "Use when the user wants to design, redesign, shape, critique, audit, polish, simplify, harden, animate, or otherwise improve a frontend
incremental-implementation: Delivers changes incrementally in thin, verifiable slices. Use when implementing any feature or change that touches more than one file, o
observability-and-instrumentation: Instruments code so production behavior is visible and diagnosable. Use when adding logging, metrics, tracing, or alerting. Use when ship
performance-optimization: Optimizes application performance across frontend, backend, queries, and databases. Use when performance requirements exist, when you sus
ponytail-audit: >
ponytail-debt: >
ponytail-review: >
ponytail: >
recall: Rebuild where recent work stands, from past sessions of the agent, the live repository, and the history of the project. Give back a short
receiving-code-review: Use when receiving code review feedback, before implementing suggestions, especially if feedback seems unclear or technically questionabl
reflect: Find the lessons in the current session of the coding agent. Put each lesson where the next agent meets it, in a check, a skill, or a pro
requesting-code-review: Use when completing tasks, implementing major features, or before merging to verify work meets requirements
research: Investigate a question against high-trust primary sources and capture the findings as a Markdown file in the repo. Use when the user want
rubric: Find defects in a change with independent read-only reviewers. Judge quality that no command can settle, such as "this API design is good
security-audit: Security guidance and vulnerability review for codebases, APIs, services, CLI tools, libraries, and daemons. Use for security questions,
spec-driven-development: Creates specs before coding. Use when starting a new project, feature, or significant change and no specification exists yet. Use when dr
subagent-driven-development: Use when executing implementation plans with independent tasks in the current session
systematic-debugging: Use when encountering any bug, test failure, or unexpected behavior, before proposing fixes
test-driven-development: Use when implementing any feature or bugfix, before writing implementation code
verification-before-completion: Use when about to claim work is complete, fixed, or passing, before committing or creating PRs - requires running verification commands a
writing-for-agents: Writing documents for agents. Use when creating or editing skills, or modifying AGENTS.md or CLAUDE.md.
writing-plans: Use when you have a spec or requirements for a multi-step task, before touching code
exit=0
$ grep -l -E '^disable-model-invocation: true' $S/*/SKILL.md | cut -d/ -f6
grill-me
handoff
recall
reflect
exit=0
$ grep -o -n -E '(docs|specs?|plans?|adr)/[A-Za-z_./<>-]*' $S/brainstorming/SKILL.md $S/writing-plans/SKILL.md $S/documentation-and-adrs/SKILL.md $S/spec-driven-development/SKILL.md | sort -t: -k1,1 -k2,2n -u | cut -d/ -f6-
brainstorming/SKILL.md:225:docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md
documentation-and-adrs/SKILL.md:40:docs/adr/
documentation-and-adrs/SKILL.md:48:docs/decisions/
spec-driven-development/SKILL.md:103:docs/
writing-plans/SKILL.md:16:docs/superpowers/plans/YYYY-MM-DD-<feature-name>.md
writing-plans/SKILL.md:67:spec/design
writing-plans/SKILL.md:189:docs/superpowers/plans/<filename>.md
writing-plans/SKILL.md:198:docs/superpowers/plans/<filename>.md
exit=0
$ grep -n -i -m3 'subagent\|fresh' $S/subagent-driven-development/SKILL.md $S/requesting-code-review/SKILL.md | cut -d/ -f6- | cut -c1-140
subagent-driven-development/SKILL.md:2:name: subagent-driven-development
subagent-driven-development/SKILL.md:6:# Subagent-Driven Development
subagent-driven-development/SKILL.md:8:Execute plan by dispatching a fresh implementer subagent per task, a task review (spec compliance + c
requesting-code-review/SKILL.md:8:Dispatch a code reviewer subagent to catch issues before they cascade. The reviewer gets precisely crafted
requesting-code-review/SKILL.md:15:- After each task in subagent-driven development
requesting-code-review/SKILL.md:20:- When stuck (fresh perspective)
exit=0
```

### E-overlap

```text
$ grep -n -i -o 'openspec/[a-z/]*' AGENTS.md
6:openspec/AGENTS
11:openspec/AGENTS
58:openspec/changes/
208:openspec/changes/
exit=0
$ ls openspec/changes | wc -l
6
exit=0
$ grep -no 'Only for mechanical, fully specified tasks' AGENTS.md
173:Only for mechanical, fully specified tasks
exit=0
$ grep -no 'caveman-commit, which collides with the project .commit. skill' .devcontainer/setup.sh
49:caveman-commit, which collides with the project `commit` skill
exit=0
$ ls .claude/skills/scavengarr-workflow/references
brief-and-roles.md
lessons.md
production.md
review-and-acceptance.md
verification.md
exit=0
$ git ls-files '*.html' '*.css' '*.js' '*.ts' '*.tsx' '*.vue' | wc -l
6
exit=0
$ grep -rl -i 'mermaid' docs | wc -l
14
exit=0
```

### E-repo

```text
$ grep -n '^| `' docs/agents/skills.md | cut -c1-100
35:| `caveman` | JuliusBrussee/caveman | terse answer style in the chat (not for docs, code or commi
36:| `ponytail` | DietrichGebert/ponytail | the smallest complete change, no over-engineering (every
37:| `handoff` | mattpocock/skills | the hand-over document before a session change |
38:| `recall` | pwguler/skills | reconstruct the state from earlier sessions |
39:| `reflect` | pwguler/skills | record the lessons at a session's end |
40:| `humanizer` | blader/humanizer | smooth prose that reads as machine-written |
41:| `writing-for-agents` | mattpocock/skills | changes to `AGENTS.md`, skills and briefs (required)
47:| `brainstorming` | obra/superpowers | before new functionality; produces questions with at least
48:| `grill-me` | mattpocock/skills | sharpen a plan or decision with the user |
49:| `writing-plans` | obra/superpowers | a plan in `docs/plans/` before a change across several fil
50:| `spec-driven-development` | addyosmani/agent-skills | a target document where no specification
51:| `domain-modeling` | mattpocock/skills | glossary and data model |
52:| `documentation-and-adrs` | addyosmani/agent-skills | a new ADR |
53:| `research` | mattpocock/skills | research against primary sources |
54:| `api-and-interface-design` | addyosmani/agent-skills | contracts, ports and module boundaries |
55:| `codebase-design` | mattpocock/skills | the cut of packages and modules |
61:| `test-driven-development` | obra/superpowers | every piece of business logic, every bug fix (re
62:| `executing-plans` | obra/superpowers | work through a plan in the session itself |
63:| `subagent-driven-development` | obra/superpowers | a plan with independent tasks for subagents
64:| `dispatching-parallel-agents` | obra/superpowers | two independent tasks at the same time |
65:| `incremental-implementation` | addyosmani/agent-skills | small, verifiable steps |
66:| `code-simplification` | addyosmani/agent-skills | refactoring without a change of behaviour |
67:| `impeccable` | pbakaus/impeccable | interfaces, design system, accessibility |
68:| `observability-and-instrumentation` | addyosmani/agent-skills | logs, metrics, traces |
69:| `performance-optimization` | addyosmani/agent-skills | queries, projections, load assumptions |
70:| `ci-cd-and-automation` | addyosmani/agent-skills | GitHub Actions, pre-commit |
71:| `diagram-design` | cathrynlavery/diagram-design | diagrams beyond Mermaid |
72:| `graphify` | Graphify-Labs/graphify (own installer, `graphify install`) | structure and depende
78:| `systematic-debugging` | obra/superpowers | before every fix of an error or a red test (require
79:| `verification-before-completion` | obra/superpowers | run and show the checks before any "done"
80:| `ponytail-review` | DietrichGebert/ponytail | review of a diff or a subagent's result (required
81:| `ponytail-audit`, `ponytail-debt` | DietrichGebert/ponytail | repository audit, the list of del
82:| `rubric` | pwguler/skills | defect review against criteria fixed in advance |
83:| `requesting-code-review`, `receiving-code-review` | obra/superpowers | request a review; check
84:| `security-audit` | cloudflare/security-audit-skill | security review of code and services |
90:| `code-review` | mattpocock/skills | Claude Code's built-in `/code-review` covers it (ledger O4)
exit=0
$ grep -nF 'add "${parts[0]}"' .devcontainer/setup.sh
74:  npx -y "$SKILLS_CLI" add "${parts[0]}" -g -y -a claude-code -s "${parts[@]:1}" </dev/null \
exit=0
$ ls -A /opt/coding-skills /opt/coding-skills/.claude
/opt/coding-skills:
.claude

/opt/coding-skills/.claude:
skills
exit=0
$ ls -A $HOME/.claude/skills
synced
exit=0
$ grep -n -m1 '^version' $S/impeccable/SKILL.md $D/upstream/impeccable.md
/opt/coding-skills/.claude/skills/impeccable/SKILL.md:4:version: 4.7.1
.devdata/review-ext/upstream/impeccable.md:4:version: 4.8.2
exit=0
$ command -v graphify
/usr/local/bin/graphify
exit=0
$ ls docs/reference/glossary.md
docs/reference/glossary.md
exit=0
$ grep -no 'docs/target/decisions/' AGENTS.md
55:docs/target/decisions/
94:docs/target/decisions/
207:docs/target/decisions/
exit=0
$ grep -no 'its plan in .docs/plans/.' AGENTS.md
58:its plan in `docs/plans/`
exit=0
$ git ls-files '*.html' '*.css' '*.js' '*.ts' '*.tsx' '*.vue'
tests/fixtures/domain_lists/aniworld_bundle.js
tests/fixtures/domain_lists/aniworld_index.html
tests/fixtures/domain_lists/filmo_bundle.js
tests/fixtures/domain_lists/filmo_index.html
tests/fixtures/domain_lists/serien_bundle.js
tests/fixtures/domain_lists/serien_index.html
exit=0
```

### E-detail

```text
$ grep -n -i -m2 'approaches\|options' $S/brainstorming/SKILL.md | cut -c1-140
28:1. Lay out the approaches in a message of their own, and have your
29:   human partner pick one (see Lay out the approaches).
exit=0
$ grep -n -i -m2 'override\|preference' $S/writing-plans/SKILL.md $S/brainstorming/SKILL.md | cut -d/ -f6- | cut -c1-140
writing-plans/SKILL.md:17:- (User preferences for plan location override this default)
brainstorming/SKILL.md:191:how big the work is in plain words. They can override it.
brainstorming/SKILL.md:226:Otherwise, ask. Your human partner's preferences override both.
exit=0
$ grep -n -m2 'GLOSSARY\|CONTEXT' $S/domain-modeling/SKILL.md | cut -c1-120
3:description: Build and sharpen a project's domain model. Use when discussing codebase terminology, writing or editing
8:Actively build and sharpen the project's domain model as you design. This is the *active* discipline: challenging term
exit=0
$ grep -n -i -m2 'mermaid' $S/diagram-design/SKILL.md | cut -c1-140
3:description: Create branded architecture, architecture delta, IT current-state, flowchart, sequence, state machine, ER/data model, timelin
163:| Reproducing Mermaid's renderer layout | Imports automatic spacing and routing instead of making an editorial layout |
exit=0
$ grep -n -m1 'version' $S/humanizer/SKILL.md $D/upstream/humanizer.md | cut -d/ -f6-
humanizer/SKILL.md:10:  version: "3.1.0"

exit=0
$ graphify --version
graphify 0.9.92
exit=0
$ grep -n -i -m2 'fork\|subagent' $S/research/SKILL.md $D/upstream/research.md | cut -d/ -f6- | cut -c1-140
exit=0
```

### E-extra

```text
$ diff $S/research/SKILL.md $D/upstream/research.md | cut -c1-160
6c6
< Spin up a **background agent** to do the research, so you keep working while it reads.
---
> Spin up a **background agent** to do the research, so you keep working while it reads. Start it with a fresh context window.
exit=0
$ grep -n -m1 'version' $D/upstream/humanizer.md
10:  version: "4.0.0"
exit=0
$ grep -rli 'projection' src | wc -l
0
exit=0
$ git ls-files '*.svg' | wc -l
0
exit=0
$ grep -n -i -m2 'lighthouse\|core web vitals' $S/performance-optimization/SKILL.md | cut -c1-120
3:description: Optimizes application performance across frontend, backend, queries, and databases. Use when performance
16:- Core Web Vitals scores are below thresholds
exit=0
```

### E-usage

```text
$ grep -no 'Nothing after 2026-10-25 is recorded here' .devdata/agent-notes/skills-usage.md
26:Nothing after 2026-10-25 is recorded here
exit=0
$ grep -no 'A skill nobody invoked for two weeks is removed' docs/agents/skills.md
25:A skill nobody invoked for two weeks is removed
exit=0
```
