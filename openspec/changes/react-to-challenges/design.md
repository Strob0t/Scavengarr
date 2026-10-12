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
- **The pass (row 96, code review 2026-10-12, finding 2).**
  - The mark keeps the first challenge's kind and whether one still stands (`open`). The browser passes it when its page got past the challenge: `solve_cloudflare()` after a shown interstitial cleared, `pass_turnstile_widget()` after the widget's token came and the form went out, and the capture when the page shows the hoster's offline notice. A later challenge marks it again.
  - A resolver's `None` after a pass is judged as without a challenge (`dead`, cached): the page gave its verdict on the link. Before, a dead link behind a cleared interstitial ended as `challenge`, and its cooldown skipped the hoster's live next link.
  - A cut after any challenge stays `challenge` (the cold path cost the bound) and runs on, but starts a cooldown only while the challenge stands. A completion past its challenge that finds no stream caches the link as dead; an error or the deadline past it gives no verdict and steps nothing up.
  - Residuals: a completion that gets past a challenge which stood at the cut and finds the link dead leaves the cut's cooldown step to run out (a pass ends no cooldown, a stream does). The hoster's own captcha (Filemoon's `captcha`, `captcha/verify`) and Dropload's gate frame give no pass signal before the stream, so a dead link behind them stays `challenge`.
  - Alternative: pass on any later page without a challenge. Rejected: a gate page that moved on says nothing about the file.
- **The passive widget (row 96, finding 3).**
  - Provider frames mark by default. A resolver whose player loads a widget that does not gate the stream passes `passive_widget=True` to `capture_stream()`; only DoodStream does (dev, 2026-10-12: its invisible Turnstile's token came 1 s after the stream).
  - reCAPTCHA counts by its checkbox frame (`anchor`): its puzzle frame (`bframe`) loads hidden with every widget, the invisible one too.
  - Alternative: provider frames mark only on resolvers that declare a gate. Rejected: a new gate on an undeclared hoster would go unseen, and a wrong mark costs less (see Risks).
- **The cooldown.**
  - It lives in the registry beside the breaker: per resolver name, the step reached and the time it ends. The steps are 60, 300, 900, then 1800 s, staying at 1800. A stream ends the cooldown and resets the steps.
  - `_guarded()` checks the cooldown before `breaker.allow()`, so a skipped link never claims a half-open trial's slot.
  - A skip is logged as `hoster_resolve_challenge_cooldown` and counted as `challenge_cooldown`. It is not cached.
  - A play-time refresh (`resolve(refresh=True)`, the CDN refused a stored stream) passes the cooldown; only an open breaker refuses it (row 96, finding 5). The cooldown spares captures for links nobody plays yet, and a refused refresh failed the playback at once.
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
  - A request for a link whose completion runs joins it (row 96, finding 9): it waits, at most the resolve bound, and answers the stream the completion returns, not the cache, which may still hold the stream a refresh's CDN refused. A second capture of the link would end without the first's challenge and cache the link as dead over the completion's stream. The completions are kept by resolver name (the one-per-hoster rule, `aclose()`) and by the link's cache key (the join).
  - Only `_attempt()` (the cached resolution) completes in the background. `resolve_for_client()` belongs to one player, is not cached, and ends as `challenge` without a completion.
- **The page and the lease.**
  - A completion keeps the one page and the lease its capture holds and takes no other.
  - The completions leave one of the page gate's pages free: at most `limit − 1` run at a time, and on a single page none (row 96, finding 12; the registry gets the gate). Two completions held both of the start's 2 pages, and a /play resolution waited for one until it ended `PageBusy`; the page budget adds pages only when the Pi has room.
  - The page gate's order stays: `play` first, then `plugin` and `capture` pages by their due time, then `background`.
  - A browser recycle waits for the lease, at most the 20 s.
- **The telemetry.**
  - `hoster_resolve` gets the outcomes `challenge` and `challenge_cooldown`, the latter a count without a duration, like `breaker_open`.
  - A new stage `hoster_challenge_completion` (label `resolver`) has the outcomes `stream`, `none` and `cancelled`. Its duration runs from the cut to the end, with buckets up to 20 s, in `infrastructure/telemetry/metrics.py`.
  - The labels come from fixed sets, as `observability` requires.

## Risks / Trade-offs

- **A wrong mark** (a dead hoster whose page shows a widget) costs one capture per 30 minutes instead of a breaker that opens for an hour. Small.
- **A completion holds a page up to 20 s longer.** On the Pi plugin pages and other captures may wait for a page meanwhile (`browser_page_wait` shows it). The one-per-hoster rule and the free page (row 96) cap it.
- **The cut request misses the stream.** The next request finds it in the cache, and the hoster runs warm.
- **The mark set after the cut** (the captcha's requests come late) makes the attempt `timeout`, as today. The completion is only for a challenge seen before the cut.

## Migration Plan

None. The deploy restores Filemoon's and Dropload's open breakers, and their trials end as `challenge`. With row 90 the first one finishes in the background and closes its breaker with a stream. Rollback is a revert: no snapshot key or setting changes.

## Open Questions

- Which state makes a capture warm, and whether it survives a restart: row 91's measurement, the premise of R4 and R5.
