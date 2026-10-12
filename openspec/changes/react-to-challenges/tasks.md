## 1. Row 89: the challenge outcome and its numbers (R1, R7)

- [ ] 1.1 Tests: a cut capture that showed a challenge ends as `challenge`; the breaker's failure count is unchanged; the link is not cached as dead.
- [ ] 1.2 Tests: a resolver's `None` with the mark set ends as `challenge`, not `dead`.
- [ ] 1.3 Tests: the cooldown's steps (60, 300, 900, 1800 s, staying at 1800); a stream ends it and resets the steps; a skip is logged, counted and not cached.
- [ ] 1.4 Tests: the cooldown is checked before `breaker.allow()`, so a half-open trial's slot stays free; a half-open trial that ends as `challenge` frees its slot.
- [ ] 1.5 Tests: a cut capture without a challenge counts as `timeout`, as before.
- [ ] 1.6 Tests: the browser marks on an interstitial, a Turnstile widget, a captcha provider's frame and a request the resolver names (Filemoon's `captcha` and `captcha/verify`); a plain page marks nothing; outside a resolution marking is a no-op.
- [ ] 1.7 Implementation: the mark beside `work_clock`, the marks in `capture_media()` and `turnstile.py`, Filemoon's named requests, the registry's outcome and cooldown, and the outcomes `challenge` and `challenge_cooldown`.
- [ ] 1.8 Live proof (dev container): a capture cut at a short resolve bound after a challenge ends as `challenge`, and the breaker is unchanged.
- [ ] 1.9 Docs:
  - `docs/features/hoster-resolvers.md`: the outcome and the cooldown;
  - `docs/features/observability.md`: the outcomes;
  - `CHANGELOG.md`.
- [ ] 1.10 Production after the deploy: Filemoon's and Dropload's outcomes over a day, recorded in `docs/plans/challenge-reactions.md` against its baseline.

## 2. Row 90: the background completion (R2)

- [ ] 2.1 Tests (a fake capture): the stream found after the cut is judged and cached under the link; the next resolve answers it from the cache; the cooldown ends; a half-open breaker closes.
- [ ] 2.2 Tests: one completion per hoster (a second cut capture is cancelled); the deadline 20 s after the cut cancels it, its page closes and the cooldown steps up; `aclose()` cancels it.
- [ ] 2.3 Tests: `resolve_for_client()` ends as `challenge` without a completion; the page gate's tests stay green; a completion holds only its capture's page.
- [ ] 2.4 Implementation: the attempt as a task under the bound, the completions, and the stage `hoster_challenge_completion` in `infrastructure/telemetry/metrics.py`.
- [ ] 2.5 Live proof (dev container): a capture cut at a short resolve bound finishes in the background, and the next request answers from the cache.
- [ ] 2.6 Docs:
  - `docs/features/hoster-resolvers.md`: the completion;
  - `docs/features/observability.md`: the stage;
  - `CHANGELOG.md`.
- [ ] 2.7 Production after the deploy: the first challenged Filemoon and Dropload captures after the restart finish in the background, and the next captures run warm within 10 s. Recorded in `docs/plans/challenge-reactions.md`.
