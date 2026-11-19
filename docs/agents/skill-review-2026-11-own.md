# Review of the project's own Claude Code skills

> **Purpose:** The state of the project's own Claude Code skills against the repository, with the evidence and the planned patches.
> **Status:** reference
> **As of:** cd4b245d

## Scope

Part A of 3: the own skills only, the four tracked ones (`commit`, `test`, `new-plugin`, `new-resolver`) and the untracked `scavengarr-workflow` in `.claude/skills/scavengarr-workflow/`.
The external skills of `docs/agents/skills.md` are the other parts.
This review changes no skill; the patches below are plans.

Each skill is checked for (a) paths, scripts, commands and names against the repository, (b) conflicts with the current conventions (dev container, agent notes, push to `staging`, rollout by the Komodo procedure `deploy-scavengarr`), (c) overlap with another skill and (d) learnings of the agent notes and of the git history since the skills' last change that never reached a skill.
Status values: `aktuell` (correct as it is), `patchen` (lines to change), `zusammenlegen` (content to merge with another skill), `fraglich` (use in doubt).
The raw command output behind every claim is in the evidence section; a label such as E-commit names its block.

## The skills

| Skill | Source (own) | Status | Evidence | Planned patch | Done (commit) |
|---|---|---|---|---|---|
| `commit` | Tracked in `.claude/skills/commit/`, added in 44d61d74, last changed in c9ae3785. | `patchen` | (a) SKILL.md:9 and :10 call `poetry run`; `command -v poetry` gives `exit=1` here, while `.devcontainer/setup.sh:100` installs poetry in the dev container; `core.hooksPath` is `.githooks` and `ls -d .githooks` gives `exit=2`. (b) grep for `rebase`, `HEAD:staging`, `git add`, `gh run`, `CI` gives `exit=1`; CLAUDE.local.md:30 to :32 asks for the rebase, the push `HEAD:staging` and `git add` of new files first, AGENTS.md:43 and :69 for the CI watch. (c) The workflow skill's gates (review-and-acceptance.md:40 to :47) repeat steps 1 to 4. (d) knowledge.md:189: pre-commit cannot fetch its hook repo (exit 3); knowledge.md:141: no hook runs on commit. E-env, E-commit. | Step 1 and 2: `poetry run <tool>` in the dev container, `.venv/bin/<tool>` where poetry is missing; a pre-commit fetch error is reported with its exit code and ruff, ruff format and basedpyright run directly. New step before 1: `git add` new files. Step 5: `git fetch origin && git rebase origin/staging`, then `git push origin HEAD:staging`. New step 6: `gh run watch <run-id> --exit-status`; a red run is fixed forward before the next change. Note that `core.hooksPath` runs no hook, so the skill is the only gate. | 9648d0c5 |
| `test` | Tracked in `.claude/skills/test/`, added in 44d61d74, last changed in c9ae3785. | `patchen` | (a) SKILL.md:7 to :9 call `poetry run pytest` (poetry: `exit=1` here); SKILL.md:11 matches `pyproject.toml:64` (`-q --tb=short` in `addopts`). (b) An extra `-q` drops the summary line: with it the run prints only the dots, without it `1 passed, 28 deselected`. (c) review-and-acceptance.md:13 of the workflow skill carries the same `-q` warning. (d) knowledge.md:189: `-o addopts=...` drops `-m "not live"`; AGENTS.md:43: a single test file runs faster without `-n`; grep for these in the skill gives `exit=1`. E-test. | The commands with the `.venv/bin/pytest` fallback. A path argument runs without `-n`. Two warnings: no extra `-q` (it hides the counts the report needs), no `-o addopts=...` for the full suite (it runs the live tests). | 9648d0c5 |
| `new-plugin` | Tracked in `.claude/skills/new-plugin/`, added in 44d61d74, last changed in 7fe3a4a8. | `patchen` | (a) `docs/how-to/new-plugin.md`, `effective_max_results` (httpx_base.py:209, playwright_base.py:140) and `_new_semaphore` (httpx_base.py:904, playwright_base.py:553) exist; the pinned `@playwright/mcp@0.0.82` (`.mcp.json:8`) has `browser_evaluate`, `browser_find` and `browser_snapshot`; SKILL.md:15 calls `poetry run` without the `PYTHONPATH=src` that `docs/how-to/dev-container.md:45` asks for in a worktree. (b) Site analysis with playwright-mcp cannot run in this sandbox (mcp.md:34: the Chromium download hosts are blocked). (c) No overlap; step 6 hands over to `commit`. (d) Eight commits changed `docs/how-to/new-plugin.md` since 7fe3a4a8, 3899c106 among them (`GrabResolvingPlugin`); the skill names none of `served_category`, `filter_by_category`, `relevant_hits`, `parse_page`, `capture_pages`, `test_real_pages`, `domain_list_url`, `mirror_group`, `GrabResolvingPlugin`, `locates_episodes` (0 hits each, AGENTS.md has each); the plugin count stands in README.md:107, :267 and AGENTS.md:158, the skill names neither file. E-plugin. | Non-negotiables after AGENTS.md:158 and :159: answer with `served_category()` / `filter_by_category()`, keep `relevant_hits()`, parse through `await parse_page()`, test on captured pages (`scripts/capture_pages.py`, `tests/unit/infrastructure/test_real_pages.py`), genuine `_domains`, `domain_list_url` and `mirror_group`, `locates_episodes`, `GrabResolvingPlugin` for grab-time links. Step 5: `PYTHONPATH=src` in a worktree and the plugin counts in README.md and AGENTS.md. Step 1: without a browser the skill stops and says so. | 38722a74 |
| `new-resolver` | Tracked in `.claude/skills/new-resolver/`, added in 44d61d74, last changed in c11a50ff. | `patchen` | (a) `ALL_XFS_CONFIGS` (xfs.py:692) and `ALL_DDL_CONFIGS` (generic_ddl.py:326) exist, their tests count 25 and 10; `build_hoster_resolvers()` (composition.py:373) puts the specific resolvers first (:382); SKILL.md:14 calls `poetry run`; `ls -d .devdata/JDownloader2` gives `exit=2` here, `.devcontainer/devcontainer.json:23` syncs it on container start only. (b) SKILL.md:11 says "raise the count in its test", AGENTS.md:160 says "raise the count in its test and here". (c) No overlap; step 5 hands over to `commit`. (d) Three commits changed `docs/how-to/new-resolver.md` since c11a50ff, 979e4853 among them (`idle_reset`); the skill names none of `ClientBoundResolverPort`, `needs_playback_check`, `address_bound`, `idle_reset`, `challenge_requests`, `autoplay_wait` (0 hits each, the how-to and AGENTS.md have each). E-resolver. | Step 1: raise the count in its test and in AGENTS.md:160; the README count (README.md:77, :108) follows the generated list. New step after 3: declare what the CDN needs (`ClientBoundResolverPort`, `needs_playback_check`, `address_bound`, `idle_reset`, `challenge_requests`), probe with `scripts/probes/header_binding.py`, leave `autoplay_wait` to the maintainer's timing. Step 2: without `.devdata/JDownloader2/` the skill says so. Step 4: the `.venv/bin/python` fallback. | 586e6b10 |
| `scavengarr-workflow` | Untracked (`.git/info/exclude:10`), a read-only mount (`test -w` gives `exit=1`), built from the maintainer's import package. | `patchen`, `zusammenlegen` (the commit gates) | (a) scripts/mutate.py:32 runs `"poetry", "run", "pytest"` and review-and-acceptance.md:14 says it needs poetry; the working copy `.devdata/mutate.py:26` runs `.venv/bin/pytest`; scripts/README.md:4: "Not run yet in this environment". (b) SKILL.md:33, :45, brief-and-roles.md:39 and review-and-acceptance.md:30 name a branch `agent/*` and a PR; CLAUDE.local.md:30 says "no branch, no PR". SKILL.md:38 and production.md:57 describe the rollout as the maintainer's click; SKILL.md names `deploy-from-git` 0 times. Stale hashes in production.md:20, :62, :86 and verification.md:12, :18, :24; the watchdog due date in production.md:87 and verification.md:26 has passed. SKILL.md:36 hands a row's review to a fresh subagent, AGENTS.md:173 allows subagents only for mechanical, fully specified tasks. (c) SKILL.md:34 and review-and-acceptance.md:42 repeat the commit skill's steps 1 and 2. (d) SKILL.md names `knowledge.md` 0 times; CLAUDE.local.md:74 says it wins over the other notes. E-workflow. | The maintainer patches the mount (no write access here): the push route `HEAD:staging` after a rebase, without a branch or a PR; the rollout by `deploy-scavengarr` and the second route by `deploy-from-git`; the hashes and due dates replaced by a pointer to `knowledge.md`; `knowledge.md` first in the reference list; `mutate.py` with the `.venv/bin/pytest` command of `.devdata/mutate.py`. The gates of review-and-acceptance.md:40 to :47 move into `commit`, and the workflow points there. The review by a subagent is named as the exception to AGENTS.md:173 in one of the two files; the maintainer decides which. E-history. | No commit: the skill is untracked, so only its live copy changed. The commit gates live in `commit`, and the workflow points there. |

## Overlap

- Commit gates: `commit` steps 1 to 4, the workflow's SKILL.md:34 and review-and-acceptance.md:40 to :47 say the same; the workflow adds `git add` first, the import order and the privacy test.
  One home: `commit`.
- Test output: `test` SKILL.md:11 and the workflow's review-and-acceptance.md:13 both describe `addopts`; only the workflow warns about the extra `-q`.
  One home: `test`.
- `new-plugin` and `new-resolver` overlap with their how-tos by design: `docs/how-to/dev-container.md:70` calls the own skills "thin checklists that point to `docs/`".
  Their non-negotiables lag behind AGENTS.md:158 to :160, which carry the same rules in short form.

## Learnings that never reached a skill

- No commit changed `.claude/skills/` in the 252 non-merge commits since c11a50ff (E-env).
- In the 80 non-merge commits since 2026-10-21, six touch the files behind the skills: three dependency bumps, 55c568e8 (the `CLAUDE.md` symlink), 979e4853 and 3899c106; no subject names a skill (E-history).
- The pruning rule of the agent notes leaves the own skills out (`.devdata/agent-notes/skills-usage.md:16`), so none of them is `fraglich` for removal (E-history).
- The sandbox has no poetry, but `.venv` has every tool (E-env); the tracked skills know only `poetry run`.
- pre-commit cannot fetch its hook repo through the proxy (exit 3), and `core.hooksPath` points to a missing folder, so no hook runs on commit (E-commit).
- `-o addopts=...` drops `-m "not live"`, and an extra `-q` hides the summary (E-test).
- The plugin and resolver how-tos gained the rules of 3899c106 and 979e4853 after the skills' last change (E-plugin, E-resolver).
- Outside the skills: `.devdata/agent-notes/knowledge.md:134` still reports that AGENTS.md denies a `CLAUDE.md`, while AGENTS.md:26 names the symlink (E-notes).
- The own skills are not recorded in `docs/agents/skills.md` (grep for the five names in backticks gives `exit=1`), although AGENTS.md:186 records every skill there; `docs/how-to/dev-container.md:70` names the four tracked ones (E-notes).
- This document has no line in the index `docs/README.md` yet, which line 50 there asks for (E-notes): the task allowed one new file only.

## Limits

- The tracked skill files are not writable from this sandbox either (`test -w .claude/skills/commit/SKILL.md` gives `exit=1`): every patch needs a session with write access to `.claude/skills/`.
- No production, Komodo or container access: the rollout lines are checked against the agent notes only, not against Komodo.
- No browser in this sandbox: the playwright-mcp tool names are checked in the pinned package's source, not in a running server.

## Evidence

Each block is the output of `bash .devdata/review-tmp/evidence.sh <section>` from the repository root, unchanged: `$ <command>`, its output, `exit=<code>`.
`$W` is `.claude/skills/scavengarr-workflow`.

### E-env

```text
$ command -v poetry
exit=1
$ ls .venv/bin/pytest .venv/bin/ruff .venv/bin/basedpyright .venv/bin/pre-commit
.venv/bin/basedpyright
.venv/bin/pre-commit
.venv/bin/pytest
.venv/bin/ruff
exit=0
$ grep -n 'poetry' .devcontainer/setup.sh
100:if ! command -v poetry >/dev/null 2>&1; then
101:  pipx install poetry
134:poetry config virtualenvs.in-project true
138:  poetry install
exit=0
$ git config --get core.hooksPath
.githooks
exit=0
$ ls -d .githooks
ls: cannot access '.githooks': No such file or directory
exit=2
$ git ls-files .claude/skills
.claude/skills/commit/SKILL.md
.claude/skills/new-plugin/SKILL.md
.claude/skills/new-resolver/SKILL.md
.claude/skills/test/SKILL.md
exit=0
$ grep -n 'scavengarr-workflow' .git/info/exclude
10:/.claude/skills/scavengarr-workflow/
exit=0
$ test -w .claude/skills/commit/SKILL.md
exit=1
$ test -w $W/SKILL.md
exit=1
$ for s in commit test new-plugin new-resolver; do printf "%s added=%s last=%s\n" $s $(git log --diff-filter=A --format=%h -- .claude/skills/$s/SKILL.md | tail -1) $(git log -1 --format=%h -- .claude/skills/$s); done
commit added=44d61d74 last=c9ae3785
test added=44d61d74 last=c9ae3785
new-plugin added=44d61d74 last=7fe3a4a8
new-resolver added=44d61d74 last=c11a50ff
exit=0
$ git rev-parse --short=12 HEAD
cd4b245d82e5
exit=0
$ git log --no-merges --format=%h c11a50ff..HEAD | wc -l
252
exit=0
$ git log --no-merges --format=%h c11a50ff..HEAD -- .claude/skills | wc -l
0
exit=0
```

### E-commit

```text
$ grep -no 'poetry run [a-z-]*' .claude/skills/commit/SKILL.md
9:poetry run pre-commit
10:poetry run pytest
exit=0
$ grep -no 'git push origin [A-Za-z:]*' .claude/skills/commit/SKILL.md AGENTS.md CLAUDE.local.md
.claude/skills/commit/SKILL.md:13:git push origin staging
AGENTS.md:62:git push origin staging
AGENTS.md:77:git push origin vX
AGENTS.md:78:git push origin staging
CLAUDE.local.md:30:git push origin HEAD:staging
exit=0
$ grep -n 'rebase\|HEAD:staging\|git add\|gh run\|CI' .claude/skills/commit/SKILL.md
exit=1
$ grep -no 'a red run after a push is fixed before the next change' AGENTS.md
43:a red run after a push is fixed before the next change
exit=0
$ grep -no 'gh run watch <run-id> --exit-status' AGENTS.md
69:gh run watch <run-id> --exit-status
exit=0
$ grep -no 'new files first (pre-commit skips untracked files)' CLAUDE.local.md
32:new files first (pre-commit skips untracked files)
exit=0
$ grep -no 'pre-commit cannot fetch its GitHub hook repo through the proxy (exit 3)' .devdata/agent-notes/knowledge.md
189:pre-commit cannot fetch its GitHub hook repo through the proxy (exit 3)
exit=0
$ grep -no 'no pre-commit hook runs on commit' .devdata/agent-notes/knowledge.md
141:no pre-commit hook runs on commit
exit=0
$ grep -n 'Gates for your own commits' $W/references/review-and-acceptance.md
40:## Gates for your own commits
exit=0
$ sed -n '42,47p' $W/references/review-and-acceptance.md | cut -c1-72 | sed 's/ *$//'
- Before every commit: pre-commit and the full suite; fix every error.
- New files: `git add` them before pre-commit, which skips untracked fil
- Imports: ruff's autofix deletes an import that is not used yet: write
- AGENTS.md changes: run the full suite (`test_repository_files.py` caps
- Privacy: `test_docs_privacy.py` fails on clock times, request ids, LAN
- Commit messages: Conventional Commits, lower-case subject, no trailing
exit=0
```

### E-test

```text
$ grep -no 'poetry run pytest' .claude/skills/test/SKILL.md
7:poetry run pytest
8:poetry run pytest
9:poetry run pytest
exit=0
$ grep -n '^addopts' pyproject.toml
64:addopts = "-q --tb=short --ignore=tests/benchmark -m \"not live\" --maxprocesses=8 --maxschedchunk=8"
exit=0
$ grep -n 'addopts' .claude/skills/test/SKILL.md
11:Output is already compact (`-q --tb=short` via `addopts`); don't add `-v`. Live tests are excluded unless `-m live` is passed.
exit=0
$ .venv/bin/pytest -q tests/unit/infrastructure/test_repository_files.py -k agents_md
.                                                                        [100%]
exit=0
$ .venv/bin/pytest tests/unit/infrastructure/test_repository_files.py -k agents_md
.                                                                        [100%]
1 passed, 28 deselected in 0.09s
exit=0
$ grep -no 'an extra .-q. hides the summary line' $W/references/review-and-acceptance.md
13:an extra `-q` hides the summary line
exit=0
$ grep -no 'never override addopts for the full suite' .devdata/agent-notes/knowledge.md
189:never override addopts for the full suite
exit=0
$ grep -no 'a single test file runs faster without .-n.' AGENTS.md
43:a single test file runs faster without `-n`
exit=0
$ grep -n 'override\|single test file\|hides' .claude/skills/test/SKILL.md
exit=1
```

### E-plugin

```text
$ grep -no 'poetry run python scripts/[a-z_]*[.]py' .claude/skills/new-plugin/SKILL.md
15:poetry run python scripts/generate_plugin_list.py
exit=0
$ grep -c 'PYTHONPATH' .claude/skills/new-plugin/SKILL.md
0
exit=1
$ grep -no 'runs with .PYTHONPATH=src. in a worktree' docs/how-to/dev-container.md
45:runs with `PYTHONPATH=src` in a worktree
exit=0
$ for t in served_category filter_by_category relevant_hits parse_page capture_pages test_real_pages domain_list_url mirror_group GrabResolvingPlugin locates_episodes; do printf "%s skill=%s how-to=%s AGENTS=%s\n" $t $(grep -c $t .claude/skills/new-plugin/SKILL.md) $(grep -c $t docs/how-to/new-plugin.md) $(grep -c $t AGENTS.md); done
served_category skill=0 how-to=1 AGENTS=1
filter_by_category skill=0 how-to=1 AGENTS=1
relevant_hits skill=0 how-to=1 AGENTS=1
parse_page skill=0 how-to=3 AGENTS=1
capture_pages skill=0 how-to=2 AGENTS=1
test_real_pages skill=0 how-to=1 AGENTS=1
domain_list_url skill=0 how-to=1 AGENTS=1
mirror_group skill=0 how-to=1 AGENTS=1
GrabResolvingPlugin skill=0 how-to=1 AGENTS=1
locates_episodes skill=0 how-to=0 AGENTS=1
exit=0
$ git log --format=%h 7fe3a4a8..HEAD -- docs/how-to/new-plugin.md
3899c106
04af3bc1
997f82bc
3dcf43e5
2e3f28f8
68e3dd12
fe54f8f6
0d6532ec
exit=0
$ grep -n 'README\|CHANGELOG\|AGENTS' .claude/skills/new-plugin/SKILL.md
exit=1
$ grep -no '43 plugins' README.md AGENTS.md
README.md:107:43 plugins
README.md:267:43 plugins
AGENTS.md:158:43 plugins
exit=0
$ grep -n 'def effective_max_results\|def _new_semaphore' src/scavengarr/infrastructure/plugins/httpx_base.py src/scavengarr/infrastructure/plugins/playwright_base.py
src/scavengarr/infrastructure/plugins/httpx_base.py:209:    def effective_max_results(self) -> int:
src/scavengarr/infrastructure/plugins/httpx_base.py:904:    def _new_semaphore(self) -> asyncio.Semaphore:
src/scavengarr/infrastructure/plugins/playwright_base.py:140:    def effective_max_results(self) -> int:
src/scavengarr/infrastructure/plugins/playwright_base.py:553:    def _new_semaphore(self) -> asyncio.Semaphore:
exit=0
$ grep -no 'The Chromium download hosts are blocked' .devdata/agent-notes/mcp.md
34:The Chromium download hosts are blocked
exit=0
$ ls .devdata/review-tmp/pwcore
package
playwright-core-1.64.0-alpha-1789764292000.tgz
exit=0
$ for t in browser_evaluate browser_find browser_snapshot; do printf "%s files=%s\n" $t $(grep -rlo $t .devdata/review-tmp/pwcore/package | wc -l); done
browser_evaluate files=1
browser_find files=1
browser_snapshot files=1
exit=0
```

The package in `.devdata/review-tmp/pwcore` is the `playwright-core` version that the pinned `@playwright/mcp@0.0.82` depends on:

```text
$ grep -no '@playwright/mcp@[0-9.]*' .mcp.json
8:@playwright/mcp@0.0.82
exit=0
$ npm view @playwright/mcp@0.0.82 dependencies --cache .devdata/review-tmp/npm-cache
{
  playwright: '1.64.0-alpha-1789764292000',
  'playwright-core': '1.64.0-alpha-1789764292000'
}
exit=0
```

### E-resolver

```text
$ grep -no 'poetry run python scripts/[a-z_]*[.]py' .claude/skills/new-resolver/SKILL.md
14:poetry run python scripts/generate_resolver_list.py
exit=0
$ ls -d .devdata/JDownloader2
ls: cannot access '.devdata/JDownloader2': No such file or directory
exit=2
$ grep -n 'postStartCommand' .devcontainer/devcontainer.json
23:  "postStartCommand": "bash .devcontainer/sync-jdownloader.sh || true",
exit=0
$ grep -no 'raise the count in its test[a-z ]*' .claude/skills/new-resolver/SKILL.md AGENTS.md
.claude/skills/new-resolver/SKILL.md:11:raise the count in its test
AGENTS.md:160:raise the count in its test and here
exit=0
$ grep -n '^ALL_XFS_CONFIGS\|^ALL_DDL_CONFIGS' src/scavengarr/infrastructure/hoster_resolvers/xfs.py src/scavengarr/infrastructure/hoster_resolvers/generic_ddl.py
src/scavengarr/infrastructure/hoster_resolvers/xfs.py:692:ALL_XFS_CONFIGS: tuple[XFSConfig, ...] = (
src/scavengarr/infrastructure/hoster_resolvers/generic_ddl.py:326:ALL_DDL_CONFIGS: tuple[GenericDDLConfig, ...] = (
exit=0
$ grep -n 'len(ALL_XFS_CONFIGS) == [0-9]\|len(ALL_DDL_CONFIGS) == [0-9]' tests/unit/infrastructure/test_xfs_resolver.py tests/unit/infrastructure/test_generic_ddl_resolver.py
tests/unit/infrastructure/test_xfs_resolver.py:159:        assert len(ALL_XFS_CONFIGS) == 25
tests/unit/infrastructure/test_generic_ddl_resolver.py:74:        assert len(ALL_DDL_CONFIGS) == 10
exit=0
$ grep -no '[0-9]* XFS hosters\|[0-9]* generic DDL hosters' AGENTS.md docs/reference/resolvers.md
AGENTS.md:160:10 generic DDL hosters
AGENTS.md:160:25 XFS hosters
docs/reference/resolvers.md:12:10 generic DDL hosters
docs/reference/resolvers.md:12:25 XFS hosters
docs/reference/resolvers.md:64: XFS hosters
exit=0
$ grep -no '[0-9]* hoster resolvers' README.md
77:63 hoster resolvers
108:63 hoster resolvers
400: hoster resolvers
418: hoster resolvers
exit=0
$ grep -n 'def build_hoster_resolvers\|before the generic' src/scavengarr/interfaces/composition.py
373:def build_hoster_resolvers(
382:    specific resolvers come before the generic XFS and DDL ones.
exit=0
$ grep -n 'class ClientBoundResolverPort' src/scavengarr/domain/ports/hoster_resolver.py
33:class ClientBoundResolverPort(HosterResolverPort, Protocol):
exit=0
$ for t in ClientBoundResolverPort needs_playback_check address_bound idle_reset challenge_requests autoplay_wait; do printf "%s skill=%s how-to=%s AGENTS=%s\n" $t $(grep -c $t .claude/skills/new-resolver/SKILL.md) $(grep -c $t docs/how-to/new-resolver.md) $(grep -c $t AGENTS.md); done
ClientBoundResolverPort skill=0 how-to=2 AGENTS=1
needs_playback_check skill=0 how-to=1 AGENTS=1
address_bound skill=0 how-to=2 AGENTS=1
idle_reset skill=0 how-to=1 AGENTS=1
challenge_requests skill=0 how-to=1 AGENTS=1
autoplay_wait skill=0 how-to=1 AGENTS=1
exit=0
$ git log --format=%h c11a50ff..HEAD -- docs/how-to/new-resolver.md
979e4853
8627d9be
2e3f28f8
exit=0
```

### E-workflow

```text
$ grep -no 'agent/[<a-z>*-]*' $W/SKILL.md $W/references/*.md
.claude/skills/scavengarr-workflow/SKILL.md:33:agent/<topic>
.claude/skills/scavengarr-workflow/SKILL.md:45:agent/*
.claude/skills/scavengarr-workflow/references/brief-and-roles.md:39:agent/<n>-<topic>
.claude/skills/scavengarr-workflow/references/review-and-acceptance.md:30:agent/*
exit=0
$ grep -no 'no branch, no PR' CLAUDE.local.md
30:no branch, no PR
exit=0
$ grep -no 'clicks\? Run [a-z]* [a-z]* [A-Za-z]*' $W/SKILL.md $W/references/production.md
.claude/skills/scavengarr-workflow/SKILL.md:38:click Run on the Komodo
.claude/skills/scavengarr-workflow/references/production.md:57:clicks Run in the Komodo
exit=0
$ grep -c 'deploy-from-git' $W/SKILL.md $W/references/production.md
.claude/skills/scavengarr-workflow/SKILL.md:0
.claude/skills/scavengarr-workflow/references/production.md:1
exit=0
$ grep -no '6978502[0-9a-f]*\|d26ee0e\|[Aa]round 2026-11-04' $W/SKILL.md $W/references/*.md
.claude/skills/scavengarr-workflow/references/production.md:20:6978502687e0
.claude/skills/scavengarr-workflow/references/production.md:62:d26ee0e
.claude/skills/scavengarr-workflow/references/production.md:62:6978502
.claude/skills/scavengarr-workflow/references/production.md:86:6978502
.claude/skills/scavengarr-workflow/references/production.md:87:around 2026-11-04
.claude/skills/scavengarr-workflow/references/verification.md:12:6978502
.claude/skills/scavengarr-workflow/references/verification.md:18:6978502
.claude/skills/scavengarr-workflow/references/verification.md:18:6978502687e0
.claude/skills/scavengarr-workflow/references/verification.md:18:d26ee0e
.claude/skills/scavengarr-workflow/references/verification.md:24:6978502
.claude/skills/scavengarr-workflow/references/verification.md:26:Around 2026-11-04
exit=0
$ grep -no '"poetry", "run", "pytest"' $W/scripts/mutate.py
32:"poetry", "run", "pytest"
exit=0
$ grep -no 'needs .poetry. on PATH' $W/references/review-and-acceptance.md
14:needs `poetry` on PATH
exit=0
$ grep -no '".venv/bin/pytest"' .devdata/mutate.py
26:".venv/bin/pytest"
exit=0
$ grep -c 'knowledge.md' $W/SKILL.md
0
exit=1
$ grep -no 'newest and wins over the others' CLAUDE.local.md
74:newest and wins over the others
exit=0
$ grep -no 'Not run yet in this environment' $W/scripts/README.md
4:Not run yet in this environment
exit=0
$ grep -no 'Before e[a-z]* commit: pre-commit and the full suite' $W/SKILL.md $W/references/review-and-acceptance.md
.claude/skills/scavengarr-workflow/SKILL.md:34:Before each commit: pre-commit and the full suite
.claude/skills/scavengarr-workflow/references/review-and-acceptance.md:42:Before every commit: pre-commit and the full suite
exit=0
```

### E-history

```text
$ git log --since=2026-10-21 --no-merges --format=%h cd4b245d | wc -l
80
exit=0
$ git log --since=2026-10-21 --no-merges --format="%h %s" cd4b245d -- .claude/skills docs/how-to/new-plugin.md docs/how-to/new-resolver.md docs/how-to/dev-container.md AGENTS.md .pre-commit-config.yaml pyproject.toml | cut -c1-90
a5993378 chore(deps): Bump the minor-and-patch group with 5 updates
55c568e8 docs(agents): claude.md is a symlink to agents.md
bf0c2022 chore(deps): Bump the minor-and-patch group with 6 updates
979e4853 feat(resolvers): send veev through the resuming file proxy
16939806 chore(deps): Bump the minor-and-patch group with 5 updates
3899c106 feat(plugins): serienfans and filmfans resolve their link-outs at the grab (row 1
exit=0
$ git log --since=2026-10-21 --no-merges --format=%h -i --grep='skill' cd4b245d | wc -l
0
exit=0
$ grep -no 'are the repository.s own and are not candidates' .devdata/agent-notes/skills-usage.md
16:are the repository's own and are not candidates
exit=0
$ grep -n 'fresh subagent' $W/SKILL.md | cut -c1-90
36:   The builder never reviews its own row: a fresh subagent gets the diff and the proced
exit=0
$ grep -no 'Only for mechanical, fully specified tasks' AGENTS.md
173:Only for mechanical, fully specified tasks
exit=0
```

### E-notes

```text
$ grep -no 'still says "there is no .CLAUDE.md."' .devdata/agent-notes/knowledge.md
134:still says "there is no `CLAUDE.md`"
exit=0
$ grep -no '.CLAUDE.md. is a symlink' AGENTS.md
26:`CLAUDE.md` is a symlink
exit=0
$ grep -n 'Every new or moved document adds its own line here' docs/README.md
50:Every new or moved document adds its own line here, and every document follows the rules at the end of this file.
exit=0
$ grep -n -F -e '`commit`' -e '`test`' -e '`new-plugin`' -e '`new-resolver`' -e '`scavengarr-workflow`' docs/agents/skills.md
exit=1
$ grep -no 'recorded in .docs/agents/skills.md.' AGENTS.md
186:recorded in `docs/agents/skills.md`
exit=0
$ grep -no 'thin checklists that point to .docs/.' docs/how-to/dev-container.md
70:thin checklists that point to `docs/`
exit=0
```
