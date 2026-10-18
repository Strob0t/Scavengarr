## Why

On the production host a hoster capture that meets a challenge outlasts production's resolve bound of 10 s (`docs/plans/challenge-reactions.md` → What Production Shows):
- Filemoon's player captcha takes 17.1 to 17.8 s cold, or gives no stream.
- Dropload's Turnstile takes 13.3 s.
- Warm, they take 5.8 to 6.7 s and 2.2 s.

The registry cuts the cold capture and counts the cut as a breaker failure. The breaker then skips the hoster for up to an hour. One cut trial on 2026-10-11 was followed by 63 refused Filemoon links within five minutes. The cold path that would warm the browser context never completes, so every trial after a restart is cold again. A capture that returns no stream within the bound is cached as a dead link for 15 minutes, although the link is alive.

The maintainer's decision: R1 with R7 (backlog row 89) and R2 (row 90). R4 with R5 (row 91) follow in a change of their own, once row 91's measurement shows which state warms a capture.

## What Changes

- **A challenge is its own outcome (R1).**
  - A resolution whose capture showed a challenge ends as `challenge` when the bound cuts it or it ends without a stream. The challenge is Cloudflare's interstitial or Turnstile widget, a captcha provider's frame, or a request the resolver names as its hoster's captcha.
  - The breaker neither counts nor resets on it, and the link is not cached as dead.
  - The hoster gets a challenge cooldown instead: 1, 5, 15, then 30 minutes for consecutive challenges, ended by a stream.
- **The numbers (R7).**
  - The `hoster_resolve` stage gets the outcomes `challenge` and `challenge_cooldown`.
  - A new stage `hoster_challenge_completion` records each background completion.
- **A challenged capture finishes in the background (R2).**
  - When the bound cuts a capture that showed a challenge, the capture runs on, shielded, for up to 20 s after the cut (30 s of work with production's bound of 10 s). At most one runs per hoster.
  - The request answers `challenge` at once.
  - A stream it finds is judged and cached under its link like any resolution's. It ends the cooldown and closes a half-open breaker.

## Impact

- **Affected specs:**
  - new `hoster-challenge-reactions`;
  - `observability` (Hoster Resolution Metrics).
- **Affected code:**
  - `src/scavengarr/infrastructure/hoster_resolvers/registry.py` (the outcome, the cooldown, the completions);
  - `src/scavengarr/infrastructure/browser/page_gate.py` (the per-attempt flag beside `work_clock`);
  - `src/scavengarr/infrastructure/browser/stealth_pool.py` and `turnstile.py` (the marks);
  - `src/scavengarr/infrastructure/hoster_resolvers/_browser.py` and `filemoon.py` (the hoster's captcha requests);
  - `src/scavengarr/infrastructure/telemetry/metrics.py` (the new stage);
  - their tests.
- **Affected docs:** `docs/features/hoster-resolvers.md`, `docs/current/observability.md`, `docs/plans/challenge-reactions.md` (the numbers after each deploy), `CHANGELOG.md`.
- **Unchanged:**
  - No setting: the 20 s after the cut and the cooldown steps are constants.
  - The breaker's persistence (`hoster-resolution-persistence`) and the snapshot's keys stay as they are. The cooldown lives in memory only, since a restart starts the browser cold anyway.
  - The restored open breakers of Filemoon and Dropload run their half-open trials as before, and those trials now end as `challenge`, not as failures.
- **Known limit:** a challenge that no capture passes (a real image captcha, an address the hoster refuses) still costs one capture per cooldown, at most two an hour after the steps.
