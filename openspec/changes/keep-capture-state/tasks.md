## 1. Tests first

- [x] 1.1 `ClearanceStore`:
  - a capture state is kept per host;
  - only cookies of the noted hosts and their parent domains are kept, and only origins on those hosts;
  - an entry older than 24 hours is dropped;
  - a state above 512 KiB is not kept and is logged;
  - a corrupt key is dropped with a warning;
  - the creation state merges the clearance cookies and every kept host's cookies and origins, the newest winning per cookie and per origin.
- [x] 1.2 `StealthPool`:
  - `_ensure_context()` creates the context with the store's state;
  - a failed creation with a state falls back to one without and logs it;
  - a recycle's new context gets the state too.
- [x] 1.3 The capture:
  - with a stream it notes the page's and the frames' hosts and keeps their state in the background;
  - the resolution does not wait for it;
  - a second stream of the same host within 10 minutes keeps nothing;
  - without a stream nothing is kept;
  - the tasks end at the pool's cleanup.
- [x] 1.4 No log line carries a cookie's value, a storage value or a URL.

## 2. Implementation

- [x] 2.1 `clearance_store.py`: the capture states under `browser:capture_state:v1`, the creation state, the bounds; `restore()` stays for `PlaywrightPluginBase`'s contexts, the stealth pool stops calling it.
- [x] 2.2 `stealth_pool.py`: the creation with the state in `_ensure_context()`, the hosts noted at the stream, the background keep with its 10-minute memo.

## 3. Live proof

- [x] 3.1 Dev container: one Dropload capture through the dev server (`PYTHONPATH=src`, port 7981), then a restart, then the next Dropload capture. It must meet no Turnstile, and `capture_state_restored` must name its hosts. The same for Filemoon. The numbers go into `docs/plans/challenge-reactions.md` → R4.
- [x] 3.2 Production after the deploy, once a capture has passed: the first Filemoon or Dropload capture after the next restart runs warm (`capture_state_restored` in the startup's log, no challenge on the capture). Recorded in `challenge-reactions.md`. It needs group 5: without it the first capture after a restart waits for the browser's launch (backlog row 93). Proven 2026-10-12 at the restart of 17:42:29 UTC (503a0eb): all six Filemoon streams by 17:51 without a captcha, the restored state at most about 14 min old, while two cold production captures without a restore met it (`docs/plans/challenge-reactions.md` → R4).

## 4. Docs

- [x] 4.1 `docs/features/hoster-resolvers.md`: what a capture keeps and when.
- [x] 4.2 `docs/plans/captcha-solving.md`: B3's clearance cookies now with the capture state.
- [x] 4.3 `CHANGELOG.md` under `## Unreleased (staging)`.

## 5. The browser starts with the app (backlog row 93)

- [x] 5.1 Tests:
  - the lifespan starts `StealthPool.warmup()` after `app_startup_complete`, and readiness does not wait for it;
  - the warm-up creates the context once: a capture after it reuses the context, and one during it waits on the pool's lock;
  - a failed warm-up logs `browser_warmup_failed` with the error type, and the next capture launches the browser;
  - shutdown cancels a running warm-up before the pools close.
- [x] 5.2 Implementation: `StealthPool.warmup()`, and the task in `composition.py`'s lifespan.
- [x] 5.3 Live, dev server (`PYTHONPATH=src`): `shared_browser_launched` and `stealth_pool_started` come before the first request, and so does `capture_state_restored` with a kept state. Then the startup's lines go into `docs/plans/challenge-reactions.md` → R4 built.
- [x] 5.4 Docs: the shared browser's lifecycle in `docs/features/` (where the warm-up is described), `CHANGELOG.md`.

## 6. The code review of 2026-10-12 (backlog row 97)

- [x] 6.1 Finding 6, tests first: the capture reads its state from its own page before the page closes, bounded by 0.5 s and skipped close to its resolve bound; an IndexedDB database plain JSON cannot hold stays out; the store write runs in the background.
- [x] 6.2 Finding 7: the newest cookie wins at creation, whether the clearance cookies or a kept state hold it (`saved_at` on the clearance cookies).
- [x] 6.3 Finding 8 and the log lines: a refused state falls back to the cookies and drops the kept origins; `capture_state_restored` and `clearance_restored` follow the creation.
- [x] 6.3a Mr. Fable's review: a refused state of origins alone drops them too; a frame whose read fails or runs past 0.4 s costs its own host only.
- [x] 6.4 V15: the recycle launches the new browser, bounded to 30 s, and an operation waits for it with its resolve bound stopped.
- [x] 6.5 Live, dev container: `capture_timing.py --runs 2 --restore` (Filemoon and Dropload restored warm), the relaunch against staging's pool (`docs/plans/challenge-reactions.md` → R4).
- [ ] 6.6 Production after the deploy: task 3.2, and the first `shared_browser_recycled` followed by its `shared_browser_launched` before the next operation. Deployed 2026-10-13 07:10:03 UTC (4357d6a): the restore and its log line read (six hosts at 07:10:13, after the context's creation; the first Filemoon capture met Byse's captcha, the Byse hosts' age not known, `docs/plans/challenge-reactions.md` → R4); restarted 07:24:15 UTC (41d6958): Byse's state restored 12 min 48 s after its keep, and both Byse captures after it met no captcha (task 3.2 proven again for a young state); one `capture_state_save_failed` (07:26:14, in the same second as its resolution's timeout, backlog row 107); the recycle not yet.
