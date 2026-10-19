## 1. Row 89: the challenge outcome and its numbers (R1, R7)

- [x] 1.1 Tests: a cut capture that showed a challenge ends as `challenge`; the breaker's failure count is unchanged; the link is not cached as dead.
- [x] 1.2 Tests: a resolver's `None` with the mark set ends as `challenge`, not `dead`.
- [x] 1.3 Tests: the cooldown's steps (60, 300, 900, 1800 s, staying at 1800); a stream ends it and resets the steps; a skip is logged, counted and not cached.
- [x] 1.4 Tests: the cooldown is checked before `breaker.allow()`, so a half-open trial's slot stays free; a half-open trial that ends as `challenge` frees its slot.
- [x] 1.5 Tests: a cut capture without a challenge counts as `timeout`, as before.
- [x] 1.6 Tests: the browser marks on an interstitial, a Turnstile widget, a captcha provider's frame and a request the resolver names (Filemoon's `captcha` and `captcha/verify`); a plain page marks nothing; outside a resolution marking is a no-op.
- [x] 1.7 Implementation: the mark beside `work_clock`, the marks in `capture_media()` and `turnstile.py`, Filemoon's named requests, the registry's outcome and cooldown, and the outcomes `challenge` and `challenge_cooldown`.
- [x] 1.8 Live proof (dev container): a capture cut at a short resolve bound after a challenge ends as `challenge`, and the breaker is unchanged.
- [x] 1.9 Docs:
  - `docs/current/hoster-resolvers.md`: the outcome and the cooldown;
  - `docs/current/observability.md`: the outcomes;
  - `CHANGELOG.md`.
- [ ] 1.10 Production after the deploy: Filemoon's and Dropload's outcomes over a day, recorded in `docs/plans/challenge-reactions.md` against its baseline.

## 2. Row 90: the background completion (R2)

- [x] 2.1 Tests (a fake capture): the stream found after the cut is judged and cached under the link; the next resolve answers it from the cache; the cooldown ends; a half-open breaker closes.
- [x] 2.2 Tests: one completion per hoster (a second cut capture is cancelled); the deadline 20 s after the cut cancels it, its page closes and the cooldown steps up; `aclose()` cancels it.
- [x] 2.3 Tests: `resolve_for_client()` ends as `challenge` without a completion; the page gate's tests stay green; a completion holds only its capture's page.
- [x] 2.4 Implementation: the attempt as a task under the bound, the completions, and the stage `hoster_challenge_completion` in `infrastructure/telemetry/metrics.py`.
- [x] 2.5 Live proof (dev container): a capture cut at a short resolve bound finishes in the background, and the next request answers from the cache.
- [x] 2.6 Docs:
  - `docs/current/hoster-resolvers.md`: the completion;
  - `docs/current/observability.md`: the stage;
  - `CHANGELOG.md`.
- [ ] 2.7 Production after the deploy: the first challenged Filemoon and Dropload captures after the restart finish in the background, and the next captures run warm within 10 s. Recorded in `docs/plans/challenge-reactions.md`.

## 3. Row 96: the challenge findings of the code review of 2026-10-12

- [x] 3.1 Tests: a dead link behind a passed challenge is cached as dead and the hoster's next link resolves; a cut past its challenge stays `challenge` without a cooldown; a completion past its challenge caches a dead link without a step up, and its deadline steps nothing up; the browser passes the mark on a cleared interstitial, a widget's token and an offline notice (finding 2).
- [x] 3.2 Tests: DoodStream's capture declares its passive widget and its frames mark nothing; an interstitial still marks; reCAPTCHA's `bframe` marks nothing (finding 3).
- [x] 3.3 Implementation: `ChallengeMark.open`, `mark_resolution_passed()`, the passes in `turnstile.py` and `capture_media()`, `passive_widget` through `capture_stream()`, the registry's rules, and the capture timing probe's `passive_widget`.
- [x] 3.4 Docs: `docs/current/hoster-resolvers.md`, `docs/current/observability.md`, `CHANGELOG.md`.
- [x] 3.5 Tests and implementation: a play-time refresh passes the challenge cooldown, an open breaker still refuses it (finding 5); docs in `docs/current/hoster-resolvers.md` and `CHANGELOG.md`.
- [x] 3.6 Tests and implementation: a request for a link whose completion runs joins it and answers its stream, no second capture; the join waits at most the resolve bound; a joined refresh never answers the refused stream; the outcome `joined` (finding 9). Docs in `docs/current/hoster-resolvers.md`, `docs/current/observability.md` and `CHANGELOG.md`.
- [x] 3.7 Tests and implementation: the completions leave one of the page gate's pages free, and a single page hands nothing over; the composition hands the registry the gate (finding 12). Docs in `docs/current/hoster-resolvers.md` and `CHANGELOG.md`.
- [x] 3.8 Tests and implementation: a request cut in the bound's loop iteration cancels the capture and counts as cut (finding 15); a completion's playback check error is logged, caches and steps up nothing (V29). Docs in `docs/current/hoster-resolvers.md` and `CHANGELOG.md`.

## 4. Row 121: a Filemoon capture without a kept pass is marked from its start (R5, decision D7)

- [x] 4.1 Tests: a kept pass with time left leaves the capture unmarked; none, an expired one or one with less than 30 s left marks it `hoster_captcha` before the capture; a gone video (the details API's 404) and an offline page mark nothing; without the kept state the pages alone mark.
- [x] 4.2 Tests (the registry with a short bound): a cut capture without a kept pass ends as `challenge` with no breaker failure and a cooldown, and its completion caches the stream; with a kept pass the cut stays a `timeout`; a page past its captcha keeps its verdict (`dead`).
- [x] 4.3 Implementation: `FilemoonResolver(clearance_store=)` reads `ClearanceStore.creation_state()` and `pass_expiry()` before the capture and marks the challenge (`mark_resolution_challenge()`); `build_hoster_resolvers(clearance_store=)` wires it.
- [x] 4.4 Docs: `docs/current/hoster-resolvers.md`, `docs/current/browser-and-challenges.md`, `docs/current/known-issues.md`, `CHANGELOG.md`, this change's spec.
- [x] 4.5 Production after the deploy: a Filemoon stream request without a kept pass ends as `challenge`, its completion caches the stream, and the pass keeper's events follow about 30 min after the captcha; recorded in backlog row 121. Two readings on two builds:
  - 6f9e42f5, one cold capture: marked from its start, it ended as `challenge` after 17.3 s (cooldown 60 s) with no breaker failure, and its completion found no stream: the capture used up its clicks 34 s after its start.
  - 2ca40548, two cold captures and the keeper: both ended as `challenge`, their streams came 43.7 s (through the completion) and 34.0 s after the start, and the second kept its pass (`capture_state_saved` in 385 ms; the first one's read ran into its 0.5 s bound). A later Filemoon link resolved warm in 8.2 s. The keeper fired 30 s past the pass's expiry, and its capture met the captcha and used up its clicks after 29 s (`hoster_pass_keep stream=False`). No Filemoon timeout and no breaker opening.
  - The keeper's capture without a pass is group 5.

## 5. Row 141: a capture that met the hoster's captcha waits for it

- [x] 5.1 Measurement:
  - the dev container, two cold captures: one click each, the verify 0.3 and 0.5 s after the captcha, the stream 0.4 and 2.2 s after the verify;
  - the dev container with Chromium's CPU throttled sixfold: the verify 0.5 to 9.9 s after the captcha in 11 cold captures;
  - the production host, from the probe runs on record: the stream 8.1 s after the verify, after the third click.
  - `capture_timing --diagnose` names each request's start and the time its answer took.
- [x] 5.2 Tests:
  - a captcha that runs gets its stream past the clicks, with one click;
  - each captcha request moves the end;
  - a captcha gone quiet ends the capture after its wait, its remaining clicks following;
  - a provider's widget keeps the clicks' cadence;
  - the state read's bounds (2 s, a frame's 1.5 s) and the keeper's bound (45 s).
- [x] 5.3 Implementation:
  - `_CAPTCHA_WAIT_S` and `_CaptchaClock` in `capture_media()`, through `_capture_listener()` and `_start_player()`;
  - `_KEEP_BOUND_S` 2 s and `_FRAME_READ_S` 1.5 s;
  - the pass keeper's `_BOUND_S` 45 s.
- [x] 5.4 Live proof (the dev container, CPU throttled sixfold, one Byse link): 8 of 8 cold captures streamed after one click, their verify 0.5 to 9.7 s after the captcha and their stream 0.7 to 1.1 s after the verify; the restored ones streamed without a click in 2.8 to 3.8 s.
- [x] 5.5 Docs: `docs/current/browser-and-challenges.md`, `docs/current/hoster-resolvers.md`, `docs/plans/challenge-reactions.md`, `docs/how-to/operations.md`, `CHANGELOG.md`, this change's spec.
- [ ] 5.6 Production after the deploy: the pass keeper's next cycles keep their pass (`hoster_pass_keep stream=True`), read from production's log after the maintainer's use.
