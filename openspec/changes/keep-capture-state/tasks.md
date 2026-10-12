## 1. Tests first

- [ ] 1.1 `ClearanceStore`:
  - a capture state is kept per host;
  - only cookies of the noted hosts and their parent domains are kept, and only origins on those hosts;
  - an entry older than 24 hours is dropped;
  - a state above 512 KiB is not kept and is logged;
  - a corrupt key is dropped with a warning;
  - the creation state merges the clearance cookies and every kept host's cookies and origins, the newest winning per cookie and per origin.
- [ ] 1.2 `StealthPool`:
  - `_ensure_context()` creates the context with the store's state;
  - a failed creation with a state falls back to one without and logs it;
  - a recycle's new context gets the state too.
- [ ] 1.3 The capture:
  - with a stream it notes the page's and the frames' hosts and keeps their state in the background;
  - the resolution does not wait for it;
  - a second stream of the same host within 10 minutes keeps nothing;
  - without a stream nothing is kept;
  - the tasks end at the pool's cleanup.
- [ ] 1.4 No log line carries a cookie's value, a storage value or a URL.

## 2. Implementation

- [ ] 2.1 `clearance_store.py`: the capture states under `browser:capture_state:v1`, the creation state, the bounds; `restore()` stays for `PlaywrightPluginBase`'s contexts, the stealth pool stops calling it.
- [ ] 2.2 `stealth_pool.py`: the creation with the state in `_ensure_context()`, the hosts noted at the stream, the background keep with its 10-minute memo.

## 3. Live proof

- [ ] 3.1 Dev container: one Dropload capture through the dev server (`PYTHONPATH=src`, port 7981), then a restart, then the next Dropload capture. It must meet no Turnstile, and `capture_state_restored` must name its hosts. The same for Filemoon. The numbers go into `docs/plans/challenge-reactions.md` → R4.
- [ ] 3.2 Production after the deploy, once a capture has passed: the first Filemoon or Dropload capture after the next restart runs warm (`capture_state_restored` in the startup's log, no challenge on the capture). Recorded in `challenge-reactions.md`.

## 4. Docs

- [ ] 4.1 `docs/features/hoster-resolvers.md`: what a capture keeps and when.
- [ ] 4.2 `docs/plans/captcha-solving.md`: B3's clearance cookies now with the capture state.
- [ ] 4.3 `CHANGELOG.md` under `## Unreleased (staging)`.
