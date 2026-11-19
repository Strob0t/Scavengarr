# Usage of the Claude Code skills

> **Purpose:** The usage numbers of the Claude Code skills from the session transcripts against the old usage record, and the order they set for the planned patches.
> **Status:** reference
> **As of:** 8229ad17

## Scope

Part C of 3: the usage numbers; the own skills are in [skill-review-2026-11-own.md](skill-review-2026-11-own.md), the external skills in [skill-review-2026-11-external.md](skill-review-2026-11-external.md).
This review changes no skill, no `setup.sh` and no `docs/agents/skills.md`.
The raw command output behind every claim is in the evidence section; a label such as E-count names its block.

## Source of the record

The record is `.devdata/agent-notes/skills-usage.md` (untracked).

- Which log: no log. The record is a hand-kept list, copied on 2026-10-25 from the file memory-archive/skills-usage-list.md of the import package (last modified 2026-10-25). Its entries come from the coders' push reports and from the orchestrator session's own note (E-source).
- Which period: the pruning window 2026-10-19 to 2026-11-02; entries exist only up to 2026-10-25 (E-source).
- Which command: none. The record names no query and no script.
- The original file of the package is gone: `ls` and `find` give no copy (E-source). The record itself is therefore nicht nachprüfbar: `ls: cannot access '/workspace/memory-archive': No such file or directory`.

The recount uses a different source: the Claude Code transcripts of this dev container, every `*.jsonl` under `~/.claude/projects` (986 files, 21 of them subagent transcripts, first entry 2026-10-23, last 2026-11-11), counted by `.devdata/review-usage/usage.py` (E-count).
A use is a `Skill` tool call (`input.skill`) in a timed transcript entry; the 12 copies of such calls in untimed request entries are dropped (48 blocks, 36 unique ids, 36 counted).
Reads of a `SKILL.md` through the `Read` tool are listed apart: a read is not a call, and review work reads the files too.
"Scavengarr" is the project directory of this repository (55 sessions, 25 of them in the window); "other" are the 26 other project directories of the container (910 sessions), which use the same install path.

## The counts

"Count in skills-usage.md" is the record's number for 2026-10-19 to 2026-10-25.
"Recomputed count" is the number of calls in the window 2026-10-19 to 2026-11-02 (transcripts from 2026-10-23), then in brackets all calls up to 2026-11-11 and the share in Scavengarr.
"Delta" is the recomputed window count minus the record's count.

| Skill | Count in skills-usage.md | Recomputed count | Delta | Note | Done (commit) |
|---|---|---|---|---|---|
| `test-driven-development` | 0 | 1 (all 21, Scavengarr 0) | +1 | 21 sessions, 2026-10-26 to 2026-11-09, all in other projects. Part B: `aktuell`. | Nothing to do. |
| `scavengarr-workflow` | not counted (own skill) | 7 (all 8, Scavengarr 8) | n/a | 8 sessions, 2026-10-26 to 2026-11-07. Besides, 8 `SKILL.md` reads, 2 of them in other projects. Part A: `patchen`, `zusammenlegen`. | No commit: untracked, live copy patched (part A). |
| `code-review` (retired) | 0 | 2 (all 2, Scavengarr 0) | +2 | Both calls in one session of another project on 2026-11-02, after decision O4 (2026-10-19) retired it. Whether the retired copy or Claude Code's built-in answered is not in the count (unverified). Part B: `patchen`. | Not done here: the image build outside this repository installs it (part B). |
| `ponytail` | 0 | 1 (all 2, Scavengarr 0) | +1 | 2 sessions, 2026-10-26 and 2026-11-09, other projects. Part B: `aktuell`. | Nothing to do. |
| `writing-plans` | 0 | 1 (all 1, Scavengarr 0) | +1 | One call on 2026-10-26 in another project; 1 `SKILL.md` read in Scavengarr on 2026-11-10. Part B: `aktuell`. | Nothing to do. |
| `writing-for-agents` | 1 | 0 (all 0) | -1 | The record's use on 2026-10-25 (orchestrator session of the import package) is in no transcript of this container. Cause unverified: the session ran elsewhere, or it did not use the `Skill` tool. | Nothing to do. |
| `caveman` | 0 | 0 (all 0) | 0 | 1 `SKILL.md` read in another project on 2026-11-01, no call. | c0048d39 (`CAVEMAN_SKILLS=(caveman)`). |
| `commit`, `test`, `new-plugin`, `new-resolver` | not counted (own skills) | 0 (all 0) | n/a | 5 `SKILL.md` reads each, all in Scavengarr on 2026-11-10 and 2026-11-11; reads, not calls. Part A: `patchen`. | 9648d0c5 (`commit`, `test`), 38722a74 (`new-plugin`), 586e6b10 (`new-resolver`). |
| `api-and-interface-design`, `brainstorming`, `ci-cd-and-automation`, `code-simplification`, `codebase-design`, `diagram-design`, `dispatching-parallel-agents`, `documentation-and-adrs`, `domain-modeling`, `executing-plans`, `graphify`, `grill-me`, `handoff`, `humanizer`, `impeccable`, `incremental-implementation`, `observability-and-instrumentation`, `performance-optimization`, `ponytail-audit`, `ponytail-debt`, `ponytail-review`, `recall`, `receiving-code-review`, `reflect`, `requesting-code-review`, `research`, `rubric`, `security-audit`, `spec-driven-development`, `subagent-driven-development`, `systematic-debugging`, `verification-before-completion` | 0 | 0 (all 0) | 0 | 32 of the 38 external skills: no call and no `SKILL.md` read in 986 transcripts. | Per skill in the part B table: 7a10696f (use lines), 9b674bb1 (scope lines), 4b489405 (`humanizer` pin). |
| `claude-api`, `dataviz` | not in the record | 0 (all 1 each, Scavengarr 0) | n/a | Not in `docs/agents/skills.md` and not in the install path; one call each in another project (2026-11-05, 2026-11-07). | Nothing to do. |
| `delegate-claude`, `delegate-workbench`, `orchestrate` | not in the record | 0 (all 0) | n/a | Not in the install path; one `SKILL.md` read each in another project. | Nothing to do. |

Result: in this repository's sessions no external skill was called through the `Skill` tool, in the window and up to 2026-11-11; the only called skill here is the own `scavengarr-workflow`.
That agrees with the record ("Coder reports, skills invoked through the Skill tool: none"), except for its one `writing-for-agents` entry.
The other 28 calls come from other projects of the same container: 26 of installed external skills (`test-driven-development` 21, `code-review` 2, `ponytail` 2, `writing-plans` 1) and 2 of skills outside the install path (`claude-api`, `dataviz`).

## Use for priority

Usage only sets the order of the phase 2 patches.
It is not a delete criterion: a count of 0 removes no skill and makes no skill `fraglich`; the `fraglich` status of part B rests on its own evidence.
Ties keep the order of part A, then part B.

1. `scavengarr-workflow`: 8 calls, the only skill called in this repository (part A `patchen`, `zusammenlegen`).
2. `code-review` (retired): 2 calls after its retirement (part B `patchen`).
3. `commit`, `test`, `new-plugin`, `new-resolver`: 0 calls (part A `patchen`).
4. The external skills with status `patchen` or `zusammenlegen` in part B, 0 calls each, in the order of part B's table.

`test-driven-development` (21 calls), `ponytail` (2) and `writing-plans` (1) are called but `aktuell` in part B: they need no patch now; their use puts them first when a refresh makes them `patchen`.

## Limits

- The transcripts start on 2026-10-23: 2026-10-19 to 2026-10-22 of the window have no source (E-count). Whether older transcripts were removed is unverified.
- Only this container's transcripts are counted; sessions on other machines are not in them.
- Only `Skill` tool calls count. A skill followed from a `Read` or from memory is not a call; a user's slash command was searched for as a `<command-name>` entry and found 0 times (E-count), so the user-invoked skills (`handoff`, `grill-me`) have no visible use, and whether the transcripts record such commands in this form is unverified.
- The own skills have no record count: the record leaves them out as no candidates.
- The two-week rule that `docs/agents/skills.md` had at line 25 (E-context) removed a skill nobody invoked; this review did not apply it. The owner decided the conflict on 2026-11-09: usage sets the order of the review work, no skill is removed for low use, and `docs/agents/skills.md` says so now.

## Evidence

The block is the output of `bash .devdata/review-usage/evidence.sh` from the repository root, unchanged: `$ <command>`, its output, `exit=<code>`.
The helper `.devdata/review-usage/usage.py` lies next to it (untracked); it prints days only and labels the project directories `scavengarr` or `other`.

```text
### E-source
$ grep -n -o -e Copied\ on\ 2026-10-25 -e memory-archive/skills-usage-list.md -e last\ modified\ 2026-10-25 -e The\ window\ runs\ 2026-10-19\ to\ 2026-11-02 -e the\ coders\ named\ the\ skills\ they\ invoked\ in\ their\ push\ reports -e None\ recorded\ for\ the\ orchestrator\ session\ through\ the\ Skill\ tool -e are\ the\ repository.s\ own\ and\ are\ not\ candidates -e \`writing-for-agents\`:\ 2026-10-25\,\ orchestrator\ session -e Coder\ reports\,\ skills\ invoked\ through\ the\ Skill\ tool:\ none -e Nothing\ after\ 2026-10-25\ is\ recorded\ here .devdata/agent-notes/skills-usage.md
3:Copied on 2026-10-25
3:memory-archive/skills-usage-list.md
4:last modified 2026-10-25
10:The window runs 2026-10-19 to 2026-11-02
11:the coders named the skills they invoked in their push reports
15:None recorded for the orchestrator session through the Skill tool
16:are the repository's own and are not candidates
17:`writing-for-agents`: 2026-10-25, orchestrator session
18:Coder reports, skills invoked through the Skill tool: none
26:Nothing after 2026-10-25 is recorded here
exit=0
$ ls -d /workspace/memory-archive /workspace/Scavengarr/memory-archive
ls: cannot access '/workspace/memory-archive': No such file or directory
ls: cannot access '/workspace/Scavengarr/memory-archive': No such file or directory
exit=2
$ find /workspace /home/coder -name skills-usage-list.md
exit=0
### E-count
$ python3 -I .devdata/review-usage/usage.py
files=986 subagent_files=21 first_day=2026-10-23 last_day=2026-11-11 other_projects=26
sessions project=other total=910 window=477
sessions project=scavengarr total=55 window=25
skill_tool_use_blocks=48 unique_ids=36 counted_timed=36
user_slash_commands=0 {}
skill calls total window scavengarr other sessions first_day last_day
test-driven-development 21 1 0 21 21 2026-10-26 2026-11-09
scavengarr-workflow 8 7 8 0 8 2026-10-26 2026-11-07
code-review 2 2 0 2 1 2026-11-02 2026-11-02
ponytail 2 1 0 2 2 2026-10-26 2026-11-09
claude-api 1 0 0 1 1 2026-11-05 2026-11-05
dataviz 1 0 0 1 1 2026-11-07 2026-11-07
writing-plans 1 1 0 1 1 2026-10-26 2026-10-26
SKILL.md reads skill project day reads
caveman other 2026-11-01 1
commit scavengarr 2026-11-10 4
commit scavengarr 2026-11-11 1
delegate-claude other 2026-10-26 1
delegate-workbench other 2026-11-01 1
new-plugin scavengarr 2026-11-10 4
new-plugin scavengarr 2026-11-11 1
new-resolver scavengarr 2026-11-10 4
new-resolver scavengarr 2026-11-11 1
orchestrate other 2026-11-01 1
scavengarr-workflow other 2026-10-25 1
scavengarr-workflow other 2026-11-09 1
scavengarr-workflow scavengarr 2026-10-25 1
scavengarr-workflow scavengarr 2026-10-28 1
scavengarr-workflow scavengarr 2026-11-06 1
scavengarr-workflow scavengarr 2026-11-10 3
test scavengarr 2026-11-10 4
test scavengarr 2026-11-11 1
writing-plans scavengarr 2026-11-10 1
exit=0
### E-context
$ bash -c ls\ /opt/coding-skills/.claude/skills\ \|\ wc\ -l
38
exit=0
$ git log -3 --format=%h\ %ad\ %s --date=short
8229ad17 2026-11-11 docs(agents): review the external claude code skills
6d1483de 2026-11-11 docs(agents): add the history evidence to the own skill review
d5bdae73 2026-11-11 docs(agents): review the project's own claude code skills
exit=0
$ grep -n A\ skill\ nobody\ invoked\ for\ two\ weeks\ is\ removed docs/agents/skills.md
25:deliberately and record it here. A skill nobody invoked for two weeks is removed: the
exit=0
```
