# 0015: Docs and commit texts tell nothing about what the maintainer watches

> **Purpose:** Records why docs and commit texts tell nothing about what the maintainer watches.
> **Status:** target
> **As of:** ee46af83

## Context

The repository is public, and its plans recorded production observations: the requests behind them, their times and the network around the production host.
Such records tell what, when and where the maintainer watches, and a public repository's history keeps them.
Durations, counts and build hashes carry the same evidence without telling any of it.

## Decision

Docs, OpenSpec texts and commit texts must carry none of these:

- titles or IMDb or Kitsu ids of the maintainer's requests, and request ids;
- clock times or dates of playbacks and observations;
- local host names, private network addresses, or the names of the internet provider, of another network service or of a device, the production machine's model and its short form included;
- conclusions about the household, the network or viewing habits.

A placeholder such as "Series A" stands in for a title, and the production machine is "the production host" in every text.
Durations, counts and build hashes are allowed, and test data and fixtures keep their titles.

## Consequences

- `tests/unit/infrastructure/test_docs_privacy.py` fails on what a pattern can find: clock times with seconds or in UTC, request ids, local host names, private network numbers, autonomous system numbers, named time zones and the production machine's device name, and the last three in the code too.
- What no pattern finds, a title, a date or a habit, stays the writer's and the reviewer's duty.
- A production reading in a doc names a placeholder, durations and counts instead of a title and a time.

## Status

accepted

## References

- `AGENTS.md` §1: the rule for every change.
- [`docs/README.md`](../../README.md): the documentation rules.
- `tests/unit/infrastructure/test_docs_privacy.py`: the guard.
- [`docs/agents/orchestration.md`](../../agents/orchestration.md): the rule in the Working rules.
