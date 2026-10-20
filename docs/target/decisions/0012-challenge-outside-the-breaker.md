# 0012: A challenge is an outcome of its own, outside the circuit breaker

> **Purpose:** Records why a challenge is an outcome of its own, outside the circuit breaker.
> **Status:** target
> **As of:** 01a39f90

## Context

A capture that met a captcha or a Cloudflare challenge was cut by the resolve bound, its page closed, and the circuit breaker counted a failure.
Nothing told a challenge from a dead hoster, so a hoster that works once its browser context is warm was skipped for up to an hour.
The cut also threw away the cold capture that would have warmed the context.
The options weighed were a challenge outcome with an escalating cooldown, a background completion of the cut capture, and a longer resolve bound for captures.
The maintainer chose the outcome with its cooldown and the background completion; the longer bound was not chosen.

## Decision

A resolution whose pages showed a challenge and found no stream in time must end as `challenge`, and the circuit breaker must not count it.
The hoster's links must wait out a challenge cooldown instead: 1, 5, 15, then 30 minutes, ended by the hoster's next stream.
A capture the bound cut after its challenge must run on in the background, for up to 20 s and one per hoster, and cache the stream it finds.
A completion that finds no stream takes the cooldown's next step at once.
A page that got past its challenge gives its verdict: no stream there is a dead link.

## Consequences

- The resolve bound stays as it is: the background completion, not a longer bound, gives a cold capture its time.
- The cooldown lives in memory only: a restart starts the browser cold, and a kept cooldown would only delay the capture that warms it.
- A completion holds its browser page past the cut, so plugin pages and other captures may wait for a page meanwhile.
- A hoster that never passes its challenge still costs one capture per cooldown step.
- A capture resolver whose hoster runs a captcha of its own names its requests (`challenge_requests` of `capture_stream()`), so the capture can mark the challenge.
- A player whose captcha widget does not gate the stream declares it (`passive_widget=True`), so the widget alone makes no challenge.

## Alternatives

- A longer resolve bound for every resolver: every resolver would wait longer, and the cold path found nothing in some runs even with twice the time.
- A new return type or an exception for a challenged capture: a cut capture is cancelled and returns nothing.
- The cooldown as a breaker state: the breaker's failure count, doubling, persistence and alert mean "the hoster is broken".
- A cooldown that outlives a restart: the browser starts cold, so a kept cooldown would only delay the first capture that can warm it.
- Paid solvers and a human in the loop: the maintainer decided against both, and Stremio has no way to show a captcha.
- A solver container for the hosters: it returns the page and its cookies, not the player's traffic.

## Status

accepted

## References

- `src/scavengarr/infrastructure/hoster_resolvers/challenge_cooldown.py`: the cooldown's steps.
- `src/scavengarr/infrastructure/hoster_resolvers/registry.py`: the `challenge` outcome and the background completion.
- [`openspec/changes/react-to-challenges/design.md`](../../../openspec/changes/react-to-challenges/design.md): the design.
- [`docs/plans/challenge-reactions.md`](../../plans/challenge-reactions.md): the gaps, the options and the maintainer's choice.
