## Context

`HosterResolverRegistry._try_resolver()` bounds a resolution by `http.timeout_resolve_seconds` (10 s in production) with `asyncio.timeout`. The clock is handed to the browser through the `work_clock` context variable (`infrastructure/browser/page_gate.py`), which stops it while a capture waits for a page.

A timeout counts as a breaker failure (`_record(failed=True)`). A resolver's `None` is judged `dead` and cached for 15 minutes (`_judge`). The breaker opens after 5 failures, and its cooldown doubles per failed trial up to an hour. A half-open trial runs shielded in `_guarded()`.

`StealthPool.capture_media()` holds one page of the page gate (`"capture"`) and a browser lease for the whole capture. Its steps: navigation, `solve_cloudflare()` for an interstitial, the clearance cookies kept, the player started. It closes its page in a `finally` and answers `CapturedMedia | None`, the same `None` for a challenge and for a dead player.

## Goals / Non-Goals

- **Goals:**
  - A challenge never opens a breaker or marks a live link dead.
  - A hoster that passes warm is tried again within minutes.
  - The first cold capture after a restart finishes and warms the context.
  - Numbers per hoster judge it.
- **Non-Goals:**
  - The warm state across restarts (R4) and a warm-up after a start (R5): row 91's measurement comes first.
  - Captures passing widgets (R6), plugin-side fixes (R8), curl_cffi (R9).
  - A setting for the budget or the steps.

## Decisions

- **The challenge mark.**
  - The registry sets a per-attempt mark: a small mutable object in a context variable beside `work_clock`. The browser code marks it when a page shows a challenge:
    - `is_challenge_page()` or `solve_cloudflare()` meets an interstitial or a widget;
    - `capture_media()`'s request listener sees a captcha provider's frame (Cloudflare's challenge platform, hCaptcha, reCAPTCHA);
    - the listener sees a request the resolver names for its hoster, a new `capture_media()` parameter. Filemoon names the player's `captcha` and `captcha/verify` requests.
  - Outside a resolution there is no mark, and marking is a no-op, so plugin fetches are untouched.
  - Alternatives:
    - A new return type (`CapturedMedia | Challenge | None`). Rejected: a cut capture is cancelled and returns nothing.
    - An exception. Rejected for the same reason.
  - The task the attempt runs in shares the mark object, so at the cut the registry reads what the capture marked until then.
- **The outcome.**
  - At the cut: the mark set → `challenge`; unset → `timeout`, as today.
  - A resolver's `None` with the mark set → `challenge`, not `dead`. It is not cached, and the breaker neither counts nor resets.
- **The cooldown.**
  - It lives in the registry beside the breaker: per resolver name, the step reached and the time it ends. The steps are 60, 300, 900, then 1800 s, staying at 1800. A stream ends the cooldown and resets the steps.
  - `_guarded()` checks the cooldown before `breaker.allow()`, so a skipped link never claims a half-open trial's slot.
  - A skip is logged as `hoster_resolve_challenge_cooldown` and counted as `challenge_cooldown`. It is not cached.
  - Memory only: a restart starts the browser cold, so a kept cooldown would only delay the first capture that can warm it.
  - Alternative: a breaker state. Rejected: the breaker's failure count, doubling, persistence and alert rule mean "the hoster is broken".
- **The completion.**
  - `_try_resolver()` runs the resolver's coroutine as a task and waits for it under the bound through `asyncio.shield`. On the bound:
    - With the mark set and no completion running for the hoster, the task goes to the registry's completions, one per resolver name. The attempt answers `challenge` at once.
    - Otherwise the task is cancelled, as the bound cancels it today.
  - A completion awaits the task for at most 20 s after the cut. With production's bound of 10 s that is 30 s of work, past the Pi's cold paths of 13.3 to 17.8 s.
  - With a result it runs the attempt's own steps: `_judge()`, `_cache_result()` under the attempt's cache key, `_record()`. The stream ends the cooldown and closes a half-open breaker; `unplayable` counts as today.
  - Without a stream by the deadline the task is cancelled. The capture's `finally` closes the page, and the cooldown steps up.
  - `aclose()` cancels the completions as it cancels the half-open probes.
  - Only `_attempt()` (the cached resolution) completes in the background. `resolve_for_client()` belongs to one player, is not cached, and ends as `challenge` without a completion.
- **The page and the lease.**
  - A completion keeps the one page and the lease its capture holds and takes no other.
  - The page gate's order stays: `play` first, then `plugin` and `capture` pages by their due time, then `background`.
  - A browser recycle waits for the lease, at most the 20 s.
- **The telemetry.**
  - `hoster_resolve` gets the outcomes `challenge` and `challenge_cooldown`, the latter a count without a duration, like `breaker_open`.
  - A new stage `hoster_challenge_completion` (label `resolver`) has the outcomes `stream`, `none` and `cancelled`. Its duration runs from the cut to the end, with buckets up to 20 s, in `infrastructure/telemetry/metrics.py`.
  - The labels come from fixed sets, as `observability` requires.

## Risks / Trade-offs

- **A wrong mark** (a dead hoster whose page shows a widget) costs one capture per 30 minutes instead of a breaker that opens for an hour. Small.
- **A completion holds a page up to 20 s longer.** On the Pi plugin pages and other captures may wait for a page meanwhile (`browser_page_wait` shows it). The one-per-hoster rule caps it at the number of capture hosters.
- **The cut request misses the stream.** The next request finds it in the cache, and the hoster runs warm.
- **The mark set after the cut** (the captcha's requests come late) makes the attempt `timeout`, as today. The completion is only for a challenge seen before the cut.

## Migration Plan

None. The deploy restores Filemoon's and Dropload's open breakers, and their trials end as `challenge`. With row 90 the first one finishes in the background and closes its breaker with a stream. Rollback is a revert: no snapshot key or setting changes.

## Open Questions

- Which state makes a capture warm, and whether it survives a restart: row 91's measurement, the premise of R4 and R5.
