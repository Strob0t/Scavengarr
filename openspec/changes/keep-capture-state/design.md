## Context

`StealthPool._ensure_context()` creates one context per browser lifetime with `new_context(service_workers="block")`, routes heavy resources and adds the stored clearance cookies (`ClearanceStore.restore()`, `add_cookies`).

`capture_media()` keeps the clearance cookies after `solve_cloudflare()` (`_remember`), before the player starts, so nothing the player sets is kept. Its page closes in a `finally`.

Playwright (Patchright) sets cookies at any time, but localStorage and IndexedDB only through `new_context(storage_state=...)`. `context.storage_state(indexed_db=True)` serializes the whole context, every origin, and has no filter. Row 91's probe restored such a state into a fresh context in a relaunched browser, and the capture ran warm.

## Goals / Non-Goals

- **Goals:**
  - A capture hoster that passed once stays warm across restarts, recycles and relaunches.
  - No resolution waits long for the state (row 97: a read of milliseconds, bounded).
  - Only the capture's own hosts are kept.
- **Non-Goals:**
  - R5's warm-up captures.
  - Separating which store carries the warmth (cookies, localStorage, IndexedDB); the form keeps all three.
  - Plugin pages' state beyond the clearance cookies.
  - A setting.

## Decisions

- **One store.**
  - `ClearanceStore` keeps the capture states beside the clearance cookies. They are two cache keys of one class, since both answer "what lets a new context skip a challenge".
  - The capture states live under `browser:capture_state:v1`: one JSON object, host → `{saved_at, cookies, origins}`.
  - The key's TTL is 24 hours from the newest entry's `saved_at`, and entries older than 24 hours are dropped at load.
- **Restore at creation.**
  - `_ensure_context()` asks the store for one storage state: the unexpired clearance cookies, plus every kept host's unexpired cookies and origins. Cookies are de-duplicated by name, domain and path; origins by origin. The newest wins: a stored clearance cookie carries the time it was saved (`saved_at`, left out of what a context gets), weighed against the kept state's `saved_at`, so neither store beats the other by its kind (code review 2026-10-12, finding 7).
  - It creates the context with it.
  - When the creation with a state fails, the context is created without one, logged as `capture_state_restore_failed`.
  - The stealth pool no longer calls `restore()` (cookies after creation), since its cookies travel in the creation state. `restore()` stays for the Playwright plugins' own contexts (`PlaywrightPluginBase._configure_context()`), which get the clearance cookies as today and no capture state.
- **The hosts.** When the capture has its stream, it notes the page's host and its frames' hosts, before the page closes:
  - a cookie is kept when its domain, without the leading dot, is one of these hosts or a parent domain of one;
  - an origin is kept when its host is one of them.
  - Ad domains that set cookies without a frame on the page stay out (the Pi's `GL_UI4`).
- **Read from the capture's own page (row 97, code review 2026-10-12, finding 6).**
  - Once `capture_media()` has its stream, before its page closes, it reads the state of the hosts not kept within the last 10 minutes (in memory): the context's cookies, and in each frame on one of them that origin's localStorage and IndexedDB (`collect_capture_state()`, one evaluate per origin, in Playwright's `StorageScript` format). A store write in the background follows; the tasks end at the pool's cleanup.
  - The read runs inside the capture's page slot and lease, so the browser's recycle cannot close it, and it visits no other origin. It took 4 to 11 ms per capture in the dev container.
  - It is bounded by 0.5 s and skipped when the capture's resolve bound (`work_clock`) has less time left, so it never costs a stream. A completion past its bound reads: the bound no longer ends it.
  - An IndexedDB database is read whole and left out when plain JSON cannot hold one of its records (a date, a file, a typed array). A new context cannot always take such a record back (finding 8), and a page whose database is missing creates it anew, whereas one with records gone may not.
  - Alternatives:
    - `storage_state(indexed_db=True)` in the background, as built first: the driver's hidden page walked every origin the context had committed, outside the page gate and without a lease. It raced the recycle (12 of 12 runs failed in the review), swallowed popups (0 of 15 delivered), and cost about 20 ms per origin, growing with the context's age.
    - A background task that takes the page over: the page gate's slot and the browser lease are context managers whose stages attach tracing context to the task that entered them. A second slot in the background would wait for a free page while the capture held its own.
    - The resolution therefore waits for the read: milliseconds, once per host per 10 minutes.
- **Bounds.**
  - A host's filtered state above 512 KiB is not kept (`capture_state_too_large`, with the host and the size).
  - A corrupt key is dropped with a warning (`capture_state_corrupt`).
  - Logs name hosts and counts (cookies, origins, bytes), never names of values or values.
- **The warm-up at the start.**
  - The lifespan starts one background task after `app_startup_complete`: `StealthPool.warmup()`, which runs `_ensure_context()` (the shared browser's launch through its pool, then the context with the kept state).
  - It is the same path the first capture takes, done early. The plugin search runner's per-search warm-up stays: after a crash or a recycle it relaunches the browser.
  - A failure is logged (`browser_warmup_failed`, the error type) and changes nothing else. The task is cancelled at shutdown before the pools close.
- **R5's warm-up capture stays out.** With a kept state, a restart's first capture runs warm while the state holds. If R7's outcomes after a deploy show a cold capture despite a kept state, R5 follows: a warm-up capture with a recent link.

## Risks / Trade-offs

- **A stale state** (the hoster rotates what it trusts) costs a challenge, as a fresh context does, and the next pass replaces it.
- **The read's cost** is one cookie query and one evaluate per kept origin, on the resolution path: 4 to 11 ms in the dev container, at most 0.5 s, none near the resolve bound.
- **What a player writes after its stream request** is not kept: Vixeo's one localStorage key, on a hoster that meets no challenge. Filemoon's captcha pass is written at the verify, before the stream.
- **The cache holds session-like cookies and storage of hoster pages**, as it holds clearance cookies today. The cache backend is the app's own (diskcache or Redis), and nothing is logged.
- **A hoster whose warmth lives in sessionStorage**, which `storage_state` does not cover, stays cold. Row 91 found none.

## Migration Plan

None. The new key starts empty, and the clearance cookies keep their key. Rollback is a revert; the new key ages out within 24 hours.

## Open Questions

- How long a kept state stays warm: R7's numbers after the deploys, or a pause test of `capture_timing --restore` on the maintainer's request.
