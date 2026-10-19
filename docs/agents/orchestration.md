# Orchestration

> **Status:** reference
> **As of:** 22fb988b

The maintainer runs three Claude Code sessions on this repository: an advisor that plans, dispatches and reviews (Mr. Fable) and two coders (Ultracode and Mr. Normal).
This document holds the rules they work by.
The work queue is [`docs/backlog.md`](../backlog.md); the rows done before the queue was cut are in [`docs/archive/backlog-2026-10.md`](../archive/backlog-2026-10.md).

## Roles

- **Mr. Fable**, the advisor: reads production, plans rows, writes task specifications and OpenSpec proposals, dispatches, reviews every push, accepts rows in the backlog, gives the rebuild signal, runs the post-deploy check and collects the decisions that are the maintainer's.
- **Ultracode**: new capabilities, cross-layer work (use cases, ports, DI wiring), the stream and proxy path, resolvers and plugins with site analysis, anything with an OpenSpec change, defects without a known cause.
- **Mr. Normal**: fully specified single-area work: scripts and probes, docs, configuration files, tests from a precise spec, mechanical refactors, isolated fixes with a known cause.
- **The maintainer** decides outward-facing and irreversible changes: deploys and rebuilds of production, releases and merges into `main`, the image's visibility, the git history and production's Compose.

## Working rules

- **Three sessions, one `staging`.**
  Before touching `application/use_cases/stremio_*.py`, `application/stremio/*`, `infrastructure/hoster_resolvers/*` or `plugins/*`, check that no other worktree has uncommitted changes there: `git worktree list`, then `git -C <worktree> status --short`.
  `git fetch` and rebase before every push; CHANGELOG conflicts keep both sides.
- **The task list shows the current row.**
  The maintainer sees each coder's task list in the Claude Code CLI, so it shows the row in work: one task per step of the row (tests, code, live proof, docs, push), `in_progress` while it runs, `completed` when done.
  A row's leftover tasks are completed or deleted when it is accepted, and an idle coder's list is empty.
  A plan from plan mode does not replace it.
- **Release only in a quiet moment:** every worktree clean and pushed, CI green; otherwise in-flight CHANGELOG lines land under the wrong version.
- **Privacy in docs and commit texts.**
  Nothing that tells what, when or where the maintainer watches: no titles or IMDb/Kitsu ids of the maintainer's requests (a placeholder such as "Series A" instead), no request ids, no clock times or dates of playbacks and observations (durations, counts and build hashes are fine), no LAN hostnames (`*.example.lan` in examples), no ISP, VPN or device names, no household references.
  Test data and fixtures keep their titles.
  `tests/unit/infrastructure/test_docs_privacy.py` guards the docs; [decision 0015](../target/decisions/0015-privacy-rule.md) holds the rule.
- **Each row:** TDD, pre-commit and the full suite (AGENTS.md §1 and §6), docs in the same commit (CHANGELOG and the document the row names), one commit per row or per isolated subtask.
- **`README.md` stays current.**
  The README is the project's front page, and the maintainer reads it as the state of the project: a row that changes a feature, a setting, the setup, the deployment, a count the README names (plugins, resolvers, tests) or the document layout updates the README in the same push, and the review of every row checks it.
  `tests/unit/infrastructure/test_readme.py` fails when a document under `docs/current/`, `docs/how-to/` or `docs/reference/` is missing from the README's documentation table or a count is stale.
- **OpenSpec changes** (`openspec/changes/<id>/`) follow Stage 2 of `openspec/AGENTS.md`: read proposal, design and tasks, implement in order, tick the tasks, archive after the release that ships them.

## Rows and worktrees

- One row per session at a time, in its own worktree `handoff-<row>` branched from `origin/staging` (EnterWorktree bases on `origin/main`, so `git fetch origin && git checkout -B handoff-<row> origin/staging` first), removed after the merge.
- A row's files are the ones its backlog row and task specification name; the two active rows never share a code file.
  A row that needs another file asks the advisor first (SendMessage to Mr. Fable).
  Shared docs: `CHANGELOG.md` (one entry per row under the unreleased heading), `AGENTS.md` (only the sentence the row names), `docs/backlog.md` (only the row's own line); rebase before every push, keep both sides on a conflict.
- Dev servers: Ultracode on port 7979, Mr. Normal on 7981.
- After each push: `gh run watch <id> --exit-status`, a red run is fixed before anything else, then a report to Mr. Fable (hashes, CI run id, files, open points).
  Never `main`, never a force-push, never a merge.
- Mr. Fable reviews every push against the code it describes, appends the acceptance to the row's line, and gives the next row.

## Production

- Production is read-only for every session: `scripts/prodctl.py` (`ps`, `stats`, `logs`, `metrics`, `state`, `probe`, `digest`, `check`) within its request budget; rebuilds, restarts and the Compose stay with the maintainer ([`docs/how-to/operations.md`](../how-to/operations.md)).
- A deploy: Mr. Fable reads the numbers the open rows ask for before it and gives the rebuild signal with the `staging` commit; the maintainer rebuilds from `staging`; pushes wait until Mr. Fable's "rebuild done" after the post-deploy check (`prodctl.py check`, repeated when the container was replaced again).
- Searches and plays against production for a proof wait for Mr. Fable's window, so one session's requests do not disturb another session's reading.
- A proof that needs a playback is the maintainer's: they play the title in their own Stremio client, and the sessions read logs and metrics; the post-deploy check's standing requests are the one exception, run by Mr. Fable.
- Every larger row carries a live proof: the resolver live suite, a site probe from the dev container, or a production reading after the deploy; its numbers go into the row's line and the plan it belongs to.

## Standing decisions

Decisions with an architectural rule live in [`docs/target/decisions/`](../target/decisions/); these process decisions of the maintainer are in force:

- Releases and merges into `main` only on the maintainer's explicit request (AGENTS.md §1); the production host builds the image from `staging` itself.
- megakino_to, movie4k, fireani, kinox and boerse stay enabled, and the upns and rpmplay resolvers stay, whatever production shows: a plugin reports unreachable and waits for its site's return ([decision 0008](../target/decisions/0008-dead-site-plugins-stay-enabled.md)).
- No Prometheus alert rules: the production stack scrapes and shows, and the plugins' long-term record is the evidence the maintainer decides a plugin's lifecycle from.
- The git history stays as it is; private traces are kept out of every new commit instead.
- The published image `ghcr.io/strob0t/scavengarr` is public.
- Only the 5-hour usage window is managed: the usage-guard hook parks a session at 90 % with one wake-up after the reset.
- Repository docs are English; the maintainer is answered in the language of the request.

## References

- [`AGENTS.md`](../../AGENTS.md) §1 (workflow), §8 (subagents), §9 (dev container)
- [`docs/backlog.md`](../backlog.md) and [`docs/archive/backlog-2026-10.md`](../archive/backlog-2026-10.md)
- [`docs/how-to/operations.md`](../how-to/operations.md) and [`docs/how-to/deploy.md`](../how-to/deploy.md)
