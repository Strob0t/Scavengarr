# 0009: Filemoon's captcha pass is kept only while there is demand

> **Status:** target
> **As of:** 5b4303e4

## Context

Filemoon's player asks a cold browser context for a captcha before the stream and keeps the pass it gives for 30 minutes.
A capture with a valid pass does not renew it, and the first capture after the pass has gone meets the captcha again.
On the production host that capture takes longer than the resolve bound, so a Filemoon link asked for after a quiet half hour came without a stream.
Keeping the pass alive costs one background capture per pass, and keeping it without end would capture around the clock for links nobody plays.

## Decision

Once the pass has gone, one background capture (`PassKeeper`) must resolve the link of the newest cached Filemoon stream again, past the cache, and keep the new pass for the next request's capture.
The keeper must run only while there is demand: a request looked up a Filemoon link within the last 48 h (`demand_age()` of the registry, cache hits included).
The keeper's own look-ups must not count as demand (`demand=False`).
One capture per pass: a capture that brings no new pass ends the keeping until a request's capture brings one.

## Consequences

- Past 48 h without a request the keeper idles, and the next request's look-up starts it again.
- The keeper's capture has a bound of its own, longer than a request's resolve bound, and its page claim is background work, served last.
- The keeper starts only while one of the stealth browser's pages stays free beside its own, so it takes no page a request waits for.
- Without a cached Filemoon stream there is no link to capture, and the keeper waits for a request's.
- A request between the pass's end and the keeper's capture still meets the captcha.

## Status

accepted

## References

- `src/scavengarr/infrastructure/hoster_resolvers/pass_keeper.py`: the keeper and its bounds.
- `src/scavengarr/infrastructure/hoster_resolvers/registry.py`: `demand_age()`.
- [`docs/plans/challenge-reactions.md`](../../plans/challenge-reactions.md): the pass's lifetime, the keeper's design and its bound.
