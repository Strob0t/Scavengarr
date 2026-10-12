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
  - `docs/features/hoster-resolvers.md`: the outcome and the cooldown;
  - `docs/features/observability.md`: the outcomes;
  - `CHANGELOG.md`.
- [ ] 1.10 Production after the deploy: Filemoon's and Dropload's outcomes over a day, recorded in `docs/plans/challenge-reactions.md` against its baseline.

## 2. Row 90: the background completion (R2)

- [x] 2.1 Tests (a fake capture): the stream found after the cut is judged and cached under the link; the next resolve answers it from the cache; the cooldown ends; a half-open breaker closes.
- [x] 2.2 Tests: one completion per hoster (a second cut capture is cancelled); the deadline 20 s after the cut cancels it, its page closes and the cooldown steps up; `aclose()` cancels it.
- [x] 2.3 Tests: `resolve_for_client()` ends as `challenge` without a completion; the page gate's tests stay green; a completion holds only its capture's page.
- [x] 2.4 Implementation: the attempt as a task under the bound, the completions, and the stage `hoster_challenge_completion` in `infrastructure/telemetry/metrics.py`.
- [x] 2.5 Live proof (dev container): a capture cut at a short resolve bound finishes in the background, and the next request answers from the cache.
- [x] 2.6 Docs:
  - `docs/features/hoster-resolvers.md`: the completion;
  - `docs/features/observability.md`: the stage;
  - `CHANGELOG.md`.
- [ ] 2.7 Production after the deploy: the first challenged Filemoon and Dropload captures after the restart finish in the background, and the next captures run warm within 10 s. Recorded in `docs/plans/challenge-reactions.md`.

## 3. Row 96: the challenge findings of the code review of 2026-10-12

- [x] 3.1 Tests: a dead link behind a passed challenge is cached as dead and the hoster's next link resolves; a cut past its challenge stays `challenge` without a cooldown; a completion past its challenge caches a dead link without a step up, and its deadline steps nothing up; the browser passes the mark on a cleared interstitial, a widget's token and an offline notice (finding 2).
- [x] 3.2 Tests: DoodStream's capture declares its passive widget and its frames mark nothing; an interstitial still marks; reCAPTCHA's `bframe` marks nothing (finding 3).
- [x] 3.3 Implementation: `ChallengeMark.open`, `mark_resolution_passed()`, the passes in `turnstile.py` and `capture_media()`, `passive_widget` through `capture_stream()`, the registry's rules, and the capture timing probe's `passive_widget`.
- [x] 3.4 Docs: `docs/features/hoster-resolvers.md`, `docs/features/observability.md`, `CHANGELOG.md`.
- [x] 3.5 Tests and implementation: a play-time refresh passes the challenge cooldown, an open breaker still refuses it (finding 5); docs in `docs/features/hoster-resolvers.md` and `CHANGELOG.md`.
