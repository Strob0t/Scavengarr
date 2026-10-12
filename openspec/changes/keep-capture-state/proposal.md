## Why

A hoster capture runs warm only after one capture has passed the hoster's challenge in the same browser context. The context lives in memory, so a restart, the shared browser's recycle (every 200 pages) or a relaunch makes every capture hoster cold again. On the Pi the cold paths outlast the 10 s resolve bound (`docs/plans/challenge-reactions.md` → What Production Shows).

Row 91 measured the premise on 2026-10-12, in the dev container and on the Pi (`challenge-reactions.md` → R4's premise, measured):
- A fresh context in a relaunched browser met the challenge again seconds after the same address had passed it, so the hoster does not remember the address.
- A fresh context restored from the earlier context's `storage_state(indexed_db=True)` met none.
- On the Pi Dropload took 3.3 s restored against 12.4 s cold. Filemoon took 8.4 s against 9.3 s, its captcha skipped.

`ClearanceStore` already keeps the clearance cookies (`cf_clearance`, `__ddg*`, `ha-waf-*`) across restarts. What makes Filemoon and Dropload warm is not among them:
- Filemoon: the player host's cookies, its localStorage and IndexedDB.
- Dropload: the hoster's cookies and the Turnstile frame's localStorage.

The maintainer's decision of 2026-10-12 07:02 UTC: R4 with R5 (backlog row 91). R5 is needed only where a kept state turns out to expire.

## What Changes

- **Kept after a capture with a stream.**
  - The storage state of the capture page's host and its frames' hosts (cookies, localStorage, IndexedDB) goes to the cache backend, next to the clearance cookies in the same store.
  - It is taken in the background after the capture returned, at most once per host per 10 minutes, and kept for at most 24 hours.
- **Restored into every new context.** Each new browser context (the start, a recycle, a relaunch) is created with the kept states and the clearance cookies (`new_context(storage_state=...)`), since localStorage and IndexedDB can be set only when a context is created.
- **Bounded and quiet.**
  - A host's state above 512 KiB is not kept.
  - A corrupt entry is dropped.
  - A failure to keep or restore costs a challenge, never a capture.
  - No value is logged.
- **R5 is not built.** It waits for R7's numbers (backlog row 89) to show a cold capture after a restart despite a kept state.

## Impact

- **Affected specs:** new `browser-capture-state`.
- **Affected code:**
  - `src/scavengarr/infrastructure/browser/clearance_store.py` (the capture states beside the clearance cookies; one `storage_state()` for the stealth pool's new context; `restore()` stays for the Playwright plugins' own contexts);
  - `src/scavengarr/infrastructure/browser/stealth_pool.py` (`_ensure_context()` and the capture's stream path);
  - their tests.
- **Affected docs:** `docs/features/hoster-resolvers.md` (captures), `docs/plans/captcha-solving.md` (B3's clearance cookies now with the capture state), `docs/plans/challenge-reactions.md` (R4 built), `CHANGELOG.md`.
- **Unchanged:**
  - No setting: the 10 minutes, the 24 hours and the 512 KiB are constants.
  - The clearance cookies' key `browser:clearance_cookies` and its rules stay.
  - The capture states get a key of their own, `browser:capture_state:v1`.
- **Order:** before row 89, whose marks also touch `stealth_pool.py`. In production the first state is kept once a capture passes. With the 10 s bound that is a fast cold pass (Filemoon's 9.3 s), or row 90's background completion.
