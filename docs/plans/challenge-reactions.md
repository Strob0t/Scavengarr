[← Back to Index](../features/README.md)

# Plan: Reactive Challenge Handling (Round 2)

**Status:** Decided 2026-10-12 07:02 UTC (the maintainer): R1 with R7, R2, and R4 with R5 become backlog rows 89 to 91; R3 is not chosen; R6, R8 and R9 wait for the numbers ([Decision and Expected Gain](#decision-and-expected-gain-2026-10-12)). R4 is built (row 91, OpenSpec `keep-capture-state`, 2026-10-12).

**Request:** the maintainer's, of 2026-10-11 17:41 UTC: after the release v0.4.0, research reactive approaches to Cloudflare challenges and captchas and document them ("reaktive lösungsansätze für cloudflare und captchas einlegen und diese dokumentieren").

**Related:**
- [captcha-solving.md](captcha-solving.md): round 1, done 2026-09-29. One detector, the chain httpx → own browser → optional sidecar, and clearance cookies that survive a restart.
- [antibot-patchright.md](antibot-patchright.md), [cloudflare-solvers.md](cloudflare-solvers.md), [browser-page-budget.md](browser-page-budget.md).
- [filmo-plugin.md](filmo-plugin.md): Filemoon on the production host.
- [header-bound-hosters.md](header-bound-hosters.md): Dropload and Vixeo on the production host.

Reactive means what Scavengarr does once a page shows a challenge or a captcha, not how it avoids one; round 1 and the Patchright work cover avoiding.

## What Production Shows

| Case | Cold (fresh profile) | Warm | Bound in production | Outcome |
|---|---|---|---|---|
| Filemoon's player captcha on the production host (the player's own `captcha` and `captcha/verify`, then clicks) | 17.1 s, or no stream by 20.8 s (row 79); 17.8 s, or none by 25.0 s (row 78) | 5.8 and 6.7 s | `http.timeout_resolve_seconds` 10 s (default 15) | Every half-open trial since a restart timed out and reopened the breaker for an hour (2026-10-11 16:34:53 and 17:36:19 UTC). The breaker refused 63 links in that hour. |
| Dropload's Turnstile on the production host | 13.3 s (the widget and two clicks) | 2.2 s | 10 s | The trials of 16:34:29 and 17:37:05 UTC timed out the same way. |
| Plugin pages behind Cloudflare on the production host | 10 to 20 s per solve; s.to's widget 16 to 70 s | the clearance cookie, 30 min by default | the plugin timeout, 30 s | Solved, or the search times out (`challenges_unsolved`). |

A capture is warm only after one capture has finished in the same browser context. A capture cut at the bound never finishes. So with production's 10 s every trial runs cold again, and a hoster that works stays skipped for hours.

## What Exists (Inventory of 2026-10-12)

- **Detection.**
  - One classifier covers Cloudflare pages, Turnstile, DDoS-Guard and embedded widgets (`src/scavengarr/infrastructure/captcha/detect.py`).
  - The HTTP transport neither retries a challenge nor counts it for the rate limiter.
  - In the browser, a challenge page is known by its title and the challenge frame. The checkbox is clicked after 3 s, then every 8 s, until the timeout ends with `cloudflare_unsolved` (`src/scavengarr/infrastructure/browser/turnstile.py`).
- **Plugins.**
  - A challenged page goes to the stealth browser (Patchright, headful under Xvfb). Its solve is shielded, so it finishes after the request is cut.
  - httpx then takes over the browser's cookies and User-Agent for the host, for 30 minutes (`HttpxPluginBase._fetch_via_browser()`).
  - `ClearanceStore` keeps `cf_clearance`, `__ddg*` and `ha-waf-*` for at most 24 h across restarts.
  - A Byparr sidecar is wired (`playwright.solver_url`, compose profile `solver`). It is off in production and has never been tested against a running one.
- **Hoster captures** (`StealthPool.capture_media()`).
  - The order: navigation, `solve_cloudflare()` for an interstitial, the clearance cookies kept, then the player started (3 s for autoplay, up to 3 clicks 5 s apart). The page closes in a `finally`.
  - The registry bounds the whole resolution by `http.timeout_resolve_seconds`; the clock stops while a capture waits for a page. It counts a timeout as a breaker failure.
  - The breaker opens after 5 failures, its cooldown doubles per failed trial up to an hour, and it comes back open after a restart.
- **The record.** `challenges` and `challenges_unsolved` per plugin and day (row 70). Row 88 fixed the link-out path, which had left every such challenge unsolved.

## Where Nothing Reacts

1. **A capture that meets a captcha is cut, counted and forgotten.** The resolve bound cancels it, the page closes, and the breaker counts a failure. The cold path that would have warmed the context never completes.
2. **No outcome tells a challenge from a dead hoster.** `capture_media()` answers `None` for both.
3. **A capture's own bounds exceed the resolve bound.** Navigation and the solve each get the stealth timeout, then up to 18 s of clicks follow.
4. **Captures do not pass widgets.** Dropload's Turnstile passes only through the play clicks, because `pass_turnstile_widget()` runs only in `click_through()`.
5. **The warm state is not kept.**
   - What makes Filemoon or Dropload warm is not a clearance cookie.
   - The cookies are kept before the player starts, so nothing the player sets is kept.
   - A restart or the browser's recycle (every 200 pages) loses the warm state, while the breakers come back open.
6. **Plugins.**
   - A host in the 30-minute browser memo does not count its challenge, and a failed solve gets no negative memo.
   - After `cloudflare_unsolved` no second attempt follows.
   - s.to's gate and animeloads' DDoS-Guard wait are not counted.
7. **Captures never reach the solver, and a solver could not help them.** A solver returns the page and its cookies, not the player's network traffic.

## Options

Each option names the gap it closes (the numbers of the list above).

### R1: a challenge is its own outcome (gaps 1 and 2)

- **What:** a capture that saw a challenge on its page and was cut reports `challenge`. The registry does not count it for the breaker. Instead the hoster gets a short cooldown that escalates (1, 5, 15, 30 min) and resets with a stream.
- **Evidence:** Servarr's escalating backoff; resilience4j's ignored exceptions.
- **Pro:** the hour-long skip goes, and a hoster that works warm is tried again within minutes.
- **Contra:** a hoster that never passes still costs one capture per cooldown.
- **Cost:** small (the capture's outcome, the registry, tests).

### R2: a cold capture finishes in the background (gap 1)

- **What:** once a capture sees a challenge, it runs on, shielded, up to a challenge budget of about 30 s, one per hoster at a time, while the request answers on time. Its stream goes to the resolution cache, and its pass warms the context.
- **Evidence:** the plugins' shielded solve; on the production host cold 17.1 s, warm 5.8 s.
- **Pro:** the next request finds a warm hoster, and often the resolved stream itself.
- **Contra:** it holds one of the browser's pages for up to 30 s. The page gate ranks plugin and capture pages alike (by their due time, after `play`), so plugin pages and other captures may wait for a page meanwhile.
- **Cost:** medium (the stealth pool, the registry, the page budget's tests).

#### R2 built (row 90, 2026-10-12)

OpenSpec `react-to-challenges`, tasks group 2. `_try_resolver()` runs the resolver as a task and awaits it through `asyncio.shield` under the bound. When the bound cuts an attempt whose capture showed a challenge, its outcome is cached (`resolve()`, not `resolve_for_client()`) and no completion runs for the hoster, the task goes to a background completion (`_complete`) and the attempt answers `challenge` at once; otherwise the cut cancels the task inside the bound, as before. The completion awaits the capture for at most 20 s after the cut, on the page the capture holds. A stream passes `_judge()` and is cached under the link: it ends the challenge cooldown and closes a half-open breaker. Without one (none, an error, the deadline) the challenge stands: nothing is cached or counted, and the hoster's next cooldown step starts at once (`ChallengeCooldown.unsolved()`, logged as `hoster_challenge_unsolved`). `aclose()` cancels the completions and their captures; a completion cancelled before its first step cancels its capture through a done callback (the shutdown test found that gap). The stage `hoster_challenge_completion` (`resolver`; `stream`, `none`, `cancelled`) times each completion from the cut. The stealth pool is unchanged: the capture keeps its page and lease as before, and the completion only awaits it.

Dev proof with the app's registry and resolvers (a fresh context per attempt, one current link per hoster, 2026-10-12 10:19 UTC):

| Hoster | Bound | Mark (into the capture) | Attempt | Completion | Next request |
|---|---|---|---|---|---|
| Filemoon | 4.1 s | `hoster_captcha` at 3.9 s | `challenge`, cooldown 60 s | `stream` 0.4 s after the cut (`duration_ms` 4498), cached | from the cache at once, breaker untouched |
| Dropload | 2 s | `turnstile` at 0.6 s | `challenge`, cooldown 60 s | `stream` 2.6 s after the cut (`duration_ms` 4602), cached | from the cache at once, breaker untouched |
| Filemoon | 4.1 s, deadline 0.1 s | `hoster_captcha` at 3.9 s | `challenge`, cooldown 60 s | `cancelled` at the deadline, its page closed (0 pages in use), cooldown up to 300 s | `challenge_cooldown`, 300 s left |

Limits: the dev container, one link per hoster, one run each; the short bounds stand in for production's 10 s against the production host's cold captures of 13.3 to 17.8 s. Production after the deploy is task 2.7.

**Deployed 2026-10-12 10:39:09 UTC (7c23863).** The first production completion came with the post-deploy check's movie request: hdfilme's Dropload link fell back to the browser at 10:39:43 (`dropload_captcha_browser_fallback`), was marked `turnstile` and cut at the bound at 10:39:52 (`challenge`, cooldown 60 s). The capture ran on and ended at 10:40:05 without a stream (`dropload_browser_no_stream`), 22 s after its start and 13 s after the cut, before the 20 s deadline: its own waits (the autoplay wait, then 3 clicks with 5 s each, `_start_player`) ran out. So `hoster_challenge_unsolved` with a 300 s cooldown, `hoster_challenge_completion_total{outcome="none",resolver="dropload"}` 1, and the breaker untouched; the next Dropload link (10:40:10) waited out the cooldown (`left_s=295`). One sample. In dev the same capture streamed 4.4 to 9.5 s after its start in 6 of 6 runs, so on the production host either the Turnstile pass or the player after it differs; the day's outcomes (task 2.7 with 1.10) tell which.

**Filemoon's trial, 10:58 UTC (Mr. Normal's two requests for `series/kitsu:12:1089`, checked against production's log): the first stream from a completion.** Filemoon's breaker came back open from the restart, due at about 10:57:20.

| Time (UTC) | Line |
|---|---|
| 10:58:09 | request 1: HTTP 200 in 0.1 s, `X-Cache: HIT`, 4 streams (VOE, Vidmoly), no Filemoon yet |
| 10:58:12 | aniworld's Byse link (bysezejataos.com) takes the half-open trial |
| 10:58:22 | `hoster_resolve_challenge kind=hoster_captcha cooldown_s=60`: the cut at the 10 s bound |
| 10:58:23 | `hoster_resolve_success challenge=hoster_captcha duration_ms=10789`: the completion's stream, 0.785 s after the cut, cached |
| 10:58:26 | `capture_state_saved hosts=['bysezejataos.com', 'n1mwq.org'] cookies=2 origins=2 bytes=2547` |
| 10:59:04 | request 2: HTTP 200 in 0.1 s, 9 streams, "1080p \| FILEMOON · aniworld (German Dub)" among them (`outcome=cached`) |
| 10:59:13 | two filemoon.to links of request 2's background resolution: `hoster_resolve_success challenge=None`, 9003 and 9503 ms, within the bound |
| 10:59:16 | `capture_state_saved hosts=['filemoon.to']` |

The completion's stream closed the half-open breaker: the two filemoon.to attempts ran instead of being refused (`breaker_open` stayed at 30: 28 from before the trial and 2 filemoon.to links at 10:58:12, while the trial held the half-open slot), though the breaker logs no line for closing. The warm captures took 9.0 and 9.5 s, side by side, against 5.8 and 6.7 s for single warm runs on 2026-10-11, and met no captcha. `hoster_challenge_completion_total` reads `stream` 1 for Filemoon (0.785 s) and `none` 1 for Dropload (13.2 s). Task 2.7 holds for Filemoon: its first challenged capture after the restart finished in the background, and the next ones ran warm within 10 s. For Dropload it is open. One trial; the day's numbers are task 1.10.

**Dropload's second completion, 11:16 UTC (a request none of the sessions sent: `tt26545992` S1E2, hdfilme's dropload.co link):** the browser fallback at 11:16:16; the answer went out at 11:16:25 (5 streams, the search not complete) while the capture ran on; the bound cut it at 11:16:32 (`turnstile`, cooldown 900 s, the third step: no stream has ended the series since 10:39:52). This time the completion found a stream, but its playback check failed with `ConnectError` at 11:16:44 (`hoster_resolve_check_error`), so nothing was cached or counted (`none`, 12.1 s after the cut). The capture still kept dr0pstream.com's state (11:16:47, 3 cookies), so Dropload's next capture can start warm. Dropload in production so far: 2 completions, 0 cached streams: one ran out of its own click waits, one found a stream that failed the playback check. Open: whether the `ConnectError` is the CDN refusing the production host's address or a passing failure; one sample.

### R3: a capture budget of its own (gaps 1 and 3)

- **What:** a separate time bound for captures, or simply production's resolve bound raised: `http.timeout_resolve_seconds` 25 in the production host's YAML, against the default of 15.
- **Evidence:** cold paths of 13.3 to 17.8 s.
- **Pro:** the YAML value needs no code and works at the next recreate.
- **Contra:**
  - Every resolver waits longer, and an answer that waits for its fifth stream can wait longer with it.
  - Filemoon's cold path gave nothing in some runs, even by 20.8 and 25 s.
- **Cost:** none (YAML) or small (a setting).

### R4: the warm state outlives restarts and recycles (gap 5)

- **What:** the context's storage state per host (cookies, localStorage and IndexedDB, as in Playwright's `storage_state`) goes to the cache backend next to `ClearanceStore`. It is taken after the player started.
- **Evidence:** the cold and warm readings; Playwright's storage state covers these stores, not sessionStorage.
- **Pro:** a restart no longer resets every capture hoster.
- **Contra:** which state warms Filemoon and Dropload is unmeasured. A `capture_timing` run with a restored state has to show it first. Measured in row 91, below: the restored state warms both.
- **Cost:** medium.

#### R4's premise, measured (row 91, 2026-10-12)

`scripts/probes/capture_timing.py --runs 2 --restore` (03614ef): two runs in one context, then that context's `storage_state(indexed_db=True)` held in memory, then the same link in a relaunched browser in a fresh context (`control`) and in a relaunched browser in a context restored from the state (`restored`). One current link per hoster, in the dev container and on the production host (before 07:29 UTC, 61 s and 44 s per probe, production idle before and after: CPU 0.01 and 0.00 of 4 cores). The challenge column is what the run's pages requested: the player's `captcha` and `captcha/verify` (Filemoon), `turnstile` for Cloudflare's challenge host (Dropload). The time is the time to the stream.

| Hoster | Run | Dev: challenge | Dev: stream | production: challenge | production: stream |
|---|---|---|---|---|---|
| Filemoon | 1 (cold) | captcha, captcha/verify | 5.1 s (1 click) | captcha, captcha/verify | 18.1 s (3 clicks) |
| Filemoon | 2 (warm) | none | 1.4 s | none | 5.8 s (1 click) |
| Filemoon | control | captcha, captcha/verify | 5.6 s (1 click) | captcha, captcha/verify | 9.3 s (1 click) |
| Filemoon | restored | none | 1.7 s | none | 8.4 s (1 click) |
| Dropload | 1 (cold) | turnstile | 4.9 s (1 click) | turnstile | 13.1 s (2 clicks) |
| Dropload | 2 (warm) | none | 0.6 s | none | 2.1 s |
| Dropload | control | turnstile | 9.5 s (2 clicks) | turnstile | 12.4 s (2 clicks) |
| Dropload | restored | none | 0.8 s | none | 3.3 s |

The state's make-up (names and counts only, as the probe prints them):

- **Filemoon**, alike in both: the cookies `byse_device_id` and `byse_viewer_id` on the player's host; 7 localStorage keys and 1 IndexedDB database there, 3 localStorage keys on the embedding site.
- **Dropload**: the cookies `aff`, `file_id` and one with an id-like name on the hoster's host; 1 localStorage key on `cloudflare.com` (the Turnstile widget). On the production host also a `GL_UI4` cookie on each of two ad domains.

Findings:

1. **The restored context runs warm.** It met no challenge for either hoster in either place. Dropload's restored run on the production host took 3.3 s against 12.4 s for the control, past the 10 s resolve bound. Filemoon's skipped the captcha but still clicked once and took 8.4 s against 9.3 s: on the production host its page load started 2.4 s into the run, and the warm run 2 also needed 5.8 s and one click. So the restore removes Filemoon's challenge, not its whole cold time.
2. **The hoster does not remember the address.** Each control met the challenge again, though runs 1 and 2 had passed it seconds before from the same address. The warmth is the context's state, not the address's.
3. **Not separated:** which store carries the warmth (cookies, localStorage or IndexedDB). R4 keeps the whole state per host, so this does not change the form.
4. **Limits:** one link and one run per condition and place; the state was restored seconds after it was taken. How long a restored state stays warm is unmeasured: R5 stays for a state that turns out to expire.

**Verdict:** control cold and restored warm, the first branch of row 91. R4 keeps a capture hoster's storage state in the cache backend beside `ClearanceStore` and restores it into each new context (start, recycle, relaunch). R5 is needed only where a restored state expires. R4's build: the OpenSpec change `keep-capture-state` (row 91).

#### R4 built (row 91, 2026-10-12)

OpenSpec `keep-capture-state`. After a capture found its stream, the stealth pool keeps the state of the page's host and its frames' hosts in `ClearanceStore` (`browser:capture_state:v1`: per host its cookies, those of its parent domains included, and its origins' localStorage and IndexedDB; in the background, at most once per host per 10 minutes, at most 24 hours, at most 512 KiB per host). Each new stealth context is created from the kept states and the clearance cookies, the newest winning per cookie and origin. The Playwright plugins' contexts keep `restore()` (clearance cookies only).

**Dev proof (task 3.1).** A restart as a new process: the app's `StealthPool` and `ClearanceStore` over a diskcache, one capture per process, the challenge column from `capture_timing`'s request watch. The dev server would not show it: it logs neither the Turnstile widget nor the captcha requests, and after a restart it answers the same link from its resolver cache (`hoster_state:v1`) without a capture.

| Hoster | Process | Challenge | Stream | Logs |
|---|---|---|---|---|
| Dropload | 1 (empty cache) | turnstile | 5.0 s (1 click) | `capture_state_saved` dr0pstream.com: 3 cookies, 0 origins, 586 bytes |
| Dropload | 2 (restart) | none | 1.1 s | `capture_state_restored` dr0pstream.com |
| Dropload | 3 (restart) | none | 1.1 s | `capture_state_restored` dr0pstream.com; context created in 0.0 s |
| Filemoon | 1 (empty cache) | captcha, captcha/verify | 4.9 s (1 click) | `capture_state_saved` moflix-stream.link and n1mwq.org: 2 cookies, 2 origins, 2547 bytes |
| Filemoon | 2 (restart) | – | nothing in 40.2 s | none: the resolver never reached the browser (no context, so no restore line) |
| Filemoon | 3 (restart) | none | 7.2 s (page at 5.8 s) | `capture_state_restored` moflix-stream.link, n1mwq.org |
| Filemoon | 4 (restart) | none | 2.2 s | `capture_state_restored` moflix-stream.link, n1mwq.org; context created in 0.1 s |

- Dropload's warmth lives in the hoster's own cookies: the Turnstile frame is gone when the stream comes, so its host is not noted, and the restored capture still met no widget.
- Filemoon keeps the player host's cookies and its localStorage and IndexedDB; the creation with them costs 0.1 s.
- Not explained: Filemoon's second process (nothing in 40.2 s before any browser step) and the third's late page (5.8 s); both lie before the context, in the resolver's HTTP step. One link per hoster; the state was restored minutes after it was kept.
- Production (task 3.2) waits for the deploy: the first Filemoon or Dropload capture after a restart, with `capture_state_restored` in the startup's log.
- **Deployed 2026-10-12 08:54:40 UTC (868d96c); the first capture waited for the browser.** The restart restored 0 resolutions (a quiet hour before it) and 6 open breakers. The shared browser launches at the first search's warm-up, and the first capture came before it was up:
  - 08:55:26: the post-deploy check's first request starts the search and the launch;
  - 08:55:27: Filemoon's breaker trial starts (aniworld's link from the stored search);
  - 08:55:35: `shared_browser_launched` and `stealth_pool_started`, about 9 s after the launch began;
  - 08:55:37: `hoster_resolve_timeout` at the 10 s bound, and the breaker reopens for 3600 s. Dropload's trial (08:55:39 to 08:55:52, the browser already up) and SuperVideo's failed too, both reopened for 3600 s.
  - Until about 09:55 every Filemoon and Dropload link is refused (circuit open), so no capture can keep a state in this container before then (Mr. Normal's check, 09:04).
  - So a kept state cannot make the first capture after a restart warm while that capture waits for the launch. The fix is backlog row 93 (`keep-capture-state` → "The Browser Starts With The App"): the browser and the stealth pool's context start once the app is ready. Task 3.2 follows it.
  - **The next trial, 09:57 (003f04e, rows 87 and 92; Mr. Normal's one request, `series/kitsu:12:1089`):** the browser was up (2 pages since 09:53:39), so the launch was not the cost. The trial took aniworld's Byse link at 09:57:10 and timed out at the bound at 09:57:20; the breaker reopened for 3600 s (due again at about 10:57:20). In the same request kinoger's search met a Cloudflare page at 09:57:08, solved at 09:57:25, so the capture shared the browser with that solve for its whole 10 s. No state was kept. One sample: on the production host a cold Filemoon capture does not reliably fit the bound, so R4's first kept state there comes from R2's completion (backlog row 90), and task 3.2 follows the restart after it.
- **Row 93 built (2026-10-12): the browser starts with the app.** Once the app reports ready, its lifespan runs `StealthPool.warmup()` in the background: the shared browser's launch, then the stealth context with the kept state. Dev server (`PYTHONPATH=src`, port 7981, its cache a copy of the Filemoon proof's above, `SCAVENGARR_ENVIRONMENT=prod` so that the start does not clear it), no request made:

  | Time (UTC) | Line |
  |---|---|
  | 09:20:47.557 | `app_startup_complete` |
  | 09:20:47.840 | `shared_browser_launched` |
  | 09:20:47.841 | `capture_state_restored` moflix-stream.link, n1mwq.org |
  | 09:20:47.918 | `stealth_pool_started` |

  - The launch took 0.3 s in the dev container (about 9 s on the production host). A run with an empty cache logged the launch and `stealth_pool_started` 0.7 s after `app_startup_complete` and no restore line; a run whose port was taken shut down during the warm-up without an error.
  - **Production (10d43f4, deployed 2026-10-12 10:11:59 UTC):** `app_startup_complete` 10:11:59, `shared_browser_launched` and `stealth_pool_started` 10:12:06 (7 s on the production host), the first Stremio request 10:12:25 (the post-deploy check), so no capture waited for the launch. No `capture_state_restored`: no capture has kept a state yet. The restore brought Filemoon's breaker back open (`hoster_resolve_circuit_open` from 10:12:26, due at about 10:57:20), so its next trial is the first with the browser up since the start.
  - **The first kept state across a restart (7c23863, 10:39:16 UTC):** `capture_state_restored hosts=['vixeo.io']` right after `shared_browser_launched`, before `stealth_pool_started`. It is Vidsonic's player (the Vixeo resolver); its capture streamed at 10:12:57 in 10d43f4 (`hoster_resolve_success hoster=vidsonic`, 1768 ms), most likely the save (that container's log went with it), and the start of 10:11:59 had restored none. So the save and the restore work in production. Task 3.2 still needs a Filemoon or Dropload capture that keeps a state, and the restart after it.
  - **Filemoon's state kept (10:58:26 UTC, 7c23863):** R2's completion found the stream after the cut, and the pool kept bysezejataos.com's and n1mwq.org's state (2 cookies, 2 origins, 2547 bytes; filemoon.to's at 10:59:16). Two filemoon.to captures at 10:59:13 ran warm, without a captcha, in 9.0 and 9.5 s (R2 built → Filemoon's trial). The kept state lives 24 hours, so the next restart before about 10:58 UTC on 2026-10-13 is task 3.2's proof: `capture_state_restored` with these hosts, then a Filemoon capture without `hoster_captcha` within the bound.
  - **The restart of 13:16:26 UTC (8a1f1db), task 3.2 not proven:** `shared_browser_launched` and `capture_state_restored hosts=['bysezejataos.com', 'dr0pstream.com', 'filemoon.to', 'n1mwq.org', 'vixeo.io']` at 13:16:32, `stealth_pool_started` at 13:16:34, and no `capture_state_restore_failed` since (the restore line comes before the restore, code review of 2026-10-12, row 97). The first Filemoon capture after it (an aniworld request from 13:17:47) met the captcha anyway: `hoster_resolve_challenge hoster=bysezejataos kind=hoster_captcha cooldown_s=60` at 13:18:26, then R2's completion `hoster_resolve_success challenge=hoster_captcha duration_ms=44192` at 13:18:31, and the state kept again at 13:18:40 (2 cookies, 2 origins). The captures after that ran without it: filemoon.to at 13:19:28 (`challenge=None`, 51.3 s with the waits for a page) and bysezejataos at 13:20:00 (`challenge=None`, 6.1 s). So the state kept at 10:58 and 10:59, 2 h 18 min old, no longer spared the captcha, while one 2 minutes old did. Open: how long Filemoon accepts its state (the kept cookies' lifetimes, read without their values), and so whether R4 can spare its captcha across a restart at all.
    - **What the kept state holds (row 100, `probe capture_state` at 14:00 UTC, 8a1f1db):** five hosts, kept between 13:19:33 and 13:44:31 (each capture keeps its hosts again). Filemoon's captcha pass is no cookie: it is the localStorage item `byse:captcha-clearance:embed` (208 chars) on the player's host n1mwq.org, beside the IndexedDB `byse-fingerprint` and the cookies `byse_viewer_id` and `byse_device_id` (364 d left). bysezejataos.com and filemoon.to keep only settings (language, diagnostics options), no cookie. Dropload's dr0pstream.com keeps `file_id` and `aff` (path `/e`, 9 h 38 min left) and `vcap…` (1 h 16 min left, 21 min after the keep); vixeo.io one localStorage item. The clearance cookies: `cf_clearance` of moflix-stream.xyz and kinoger.com (364 d left), kinoger's `ha-waf-hist` (6 d), its `ha-waf-ticket` and `ha-waf-hash` expired 12 min before, serienstream.to's `__ddg1_` (357 d) and `__ddg8_`, `__ddg9_`, `__ddg10_` (3 min left).
    - **What the lifetimes mean for a restart:** no kept cookie expires within Filemoon's 2 h 18 min, so the lifetime that ended the pass of 10:58 is the captcha token's: carried in the localStorage value or held by the site against it, it is not readable from names, lengths and times. A restart therefore spares Filemoon's captcha only within that unknown span after its last capture (2 min was enough on 13:18:40; 2 h 18 min was not). For Dropload, a restart more than about 1.5 h after its last capture loses the `vcap…` cookie; Cloudflare's `cf_clearance` outlives any restart as a cookie (the site's own validity is not visible here); kinoger's HAProxy ticket and DDoS-Guard's `__ddg8_`–`__ddg10_` last minutes and are renewed by use, so a restart after a pause meets their check again regardless of R4. Open: the token's span, measured as the age of the kept state at the first Filemoon capture after each restart (captcha or not).
  - **The restart of 15:11:28 UTC (1d79ec7), task 3.2 still not proven:** `capture_state_restored` with the same five hosts at 15:11:39 (`shared_browser_launched` the same second, `stealth_pool_started` at 15:11:45); n1mwq.org's state, kept at 13:44:31, was 1 h 27 min old. The first Filemoon captures came from the post-deploy check's requests and got nowhere: the production host's CPU was busy (`browser_pages_changed reason=cpu cpu_busy=0.98`), the page budget fell from 2 pages to 1, captures waited up to 28 s for a page, the resolver's 10 s bound ended five of them (`hoster_resolve_timeout` from 15:13:38 to 15:14:16, no captcha mark), and seven more were cut by their requests (outcome `cut`). The breaker opened at 15:14:16, and its trial at 15:19:32, on an idle production host, timed out at 15:19:42 without a mark as well (reopened for 120 s). The trial at 15:22:19 (the thirteenth round's Demon Slayer request, `docs/plans/stremio-latency.md`) met the captcha 9 s in (`hoster_resolve_challenge hoster=bysezejataos kind=hoster_captcha` at 15:22:28) with the state 1 h 38 min old; R2's completion found the stream at 15:22:34 (15.4 s), the state was kept again at 15:22:37, and the next capture ran warm (15:23:13, `challenge=None`, 8.5 s).
    - **Reading:** a state 1 h 38 min old no longer spared the captcha either (2 h 18 min at 13:18), while R4's restore runs (finding 1 above, `capture_timing.py --restore`) found a state restored seconds after its keep warm through the app's own `ClearanceStore` and context creation. So Filemoon's pass most likely lasts less than 1 h 38 min; that production's restore did not take is not ruled out, since its log line comes before the restore (row 97). A restart within an hour of a Filemoon capture tells the two apart (each capture keeps the state again, so the rebuild that brings row 96 can be it). And the captcha showed only about 9 s into the capture (one sample; row 101 measures it), so a capture the 10 s bound ends a little earlier is a `timeout` without a mark, which the breaker counts: both openings (15:14:16, 15:19:42) came that way, with 20 Filemoon links refused by 15:23.
    - **The restart of 17:42:29 UTC (503a0eb, row 96) tells the two apart, task 3.2 proven for a young state:** `capture_state_restored` for six hosts at 17:42:35 (n1mwq.org, bysezejataos.com and filemoon.to among them; `shared_browser_launched` the same second, `stealth_pool_started` at 17:42:39), no `capture_state_restore_failed`. The state's age: the old container's logs went with it, but the hoster state snapshot (read at 17:51:29 with a read-only script) holds a Filemoon stream that expires at 18:38:19, and every stream lives 1 h (`_CACHE_TTL_ALIVE` in `src/scavengarr/infrastructure/hoster_resolvers/registry.py`), so the old container resolved it at 17:38:19. A capture with a stream keeps its frame hosts' state at most once per 10 min, so n1mwq.org's was most likely kept between 17:28:19 and 17:38:19, at most 14 min before the restart (a failed keep would leave it older; the log lines that would show one went with the container). The first Filemoon captures came with the post-deploy check's requests: all six streams of the Filemoon resolver by 17:51 logged `challenge=None`, the first at 17:43:52 (a Byse link, 10.3 s, the check's Demon Slayer request), then 17:44:40 (two), 17:45:05, 17:50:36 and 17:50:59; no `hoster_resolve_challenge` line and no completion, which a cut capture with a mark would have started (17 resolutions at the first counter read, about 17:45:15: 4 stream, 9 cut, 2 timeout, 2 unplayable; by 17:45:43 six `hoster_resolve_timeout` lines of the Filemoon resolver, all without a mark, Ultracode's count). The control: row 101's two production runs (below), 4 to 6 min after the first streams on the same production host and address with fresh profiles and no restore, met the captcha in both cold captures. So production's restore takes, and a state at most about 16 min old spared the captcha after a restart; the restore of 15:11:39 ran the same code, so the captcha of 15:22:19 most likely came from the pass's age (1 h 38 min). Filemoon's pass lasts less than that, and a restart or a pause longer than the pass after the last Filemoon capture starts cold: the case R5 waits for (the design's rule: R5 follows when the outcomes after a deploy show a cold capture despite a kept state). Limits: one restart, one young state, its age from the snapshot's expiry rather than the store's own time; the next restart's age comes from `probe capture_state`, read right before the rebuild signal.
    - **The restart of 2026-10-13 07:10:03 UTC (4357d6a, rows 97, 98 and 102): Byse's captcha came back, and row 96's challenge path took it.** `capture_state_restored` for six hosts (bysezejataos.com, dr0pstream.com, filemoon.to, moflix-stream.link, n1mwq.org, vixeo.io; `origins=6`) at 07:10:13, logged after the context's creation (row 97), `shared_browser_launched` at 07:10:09 and `stealth_pool_started` at 07:10:13, no `capture_state_restore_failed`. The state's age per host, from the old container's keeps that `probe capture_state` showed at 07:13 and 07:20: vixeo.io 12.6 min (kept 06:57:27), dr0pstream.com 11 h 0 min (20:10:25 on 2026-10-12), moflix-stream.link 13 h 11 min (17:58:59), filemoon.to 13 h 26 min (17:44:18); the Byse hosts' keep was overwritten at 07:11:35, and the old container's logs went with it (it ran 13 h 27 min, overnight), so their age is not known. The first Filemoon resolution (an aniworld link redirected to bysezejataos at 07:11:20) met Byse's captcha and ended as `challenge` 14.5 s after its start (`hoster_resolve_challenge kind=hoster_captcha passed=False cooldown_s=60` at 07:11:34; `scavengarr_hoster_resolve_total{outcome="challenge",resolver="filemoon"}` 1), and R2's completion found the stream 1.6 s after the cut (`hoster_resolve_success challenge=hoster_captcha duration_ms=16097` at 07:11:36; completion `stream` 1) and kept the hosts' state with Byse's captcha clearance (`capture_state_saved hosts=['bysezejataos.com', 'n1mwq.org']`; n1mwq.org's localStorage holds `byse:captcha-clearance:embed` since). Filemoon's breaker came back open with the hoster state (4 breakers restored; `hoster_resolve_circuit_open hoster=filemoon` at 07:11:21, 5 `breaker_open` by 07:16). Reading: the restore carries the state over a restart; that the captcha came back says Byse's state was older than its pass (less than 1 h 38 min, R4 above), or the pass is shorter still, and which one is not known. Still open for task 6.6: the first `shared_browser_recycled` with its `shared_browser_launched`, and the day's `capture_state_save_failed` count.
    - **The restart of 07:24:15 UTC (41d6958, rows 103 and 104): task 3.2 proven again for a young state.** `capture_state_restored` for the same six hosts at 07:24:23; Byse's state (kept at 07:11:35 by the completion above) was 12 min 48 s old. Both Byse captures after it streamed without the captcha: filmo's at 07:25:24 (`challenge=None`, 9703 ms, the state saved at 07:25:23 in 274 ms) and aniworld's at 07:26:35 (9664 ms). Between them, at 07:26:14 (request 5dece365c59b), aniworld's capture of a filemoon.to link logged `capture_state_save_failed hosts=['filemoon.to'] error=TimeoutError` in the same second as its `hoster_resolve_timeout`: the save runs inside the resolution's 10 s bound, and both streams that came took 9.7 s with it (row 107 counts the day's pairs). Reading: the pass outlives 12 min 48 s and not 1 h 38 min (the restart of 15:11:28); row 106 narrows the span and keeps the pass warm.
    - **The restart of 09:01:37 UTC (4429aff, row 105): Byse's state 1 h 36 min old.** `probe capture_state` at 08:51 showed Byse's keep of 07:25:23 with `byse:captcha-clearance:embed` (208 characters); `capture_state_restored` for the same six hosts at 09:01:47 (`shared_browser_launched` at 09:01:43). The first Filemoon resolution after it (aniworld, request 74faa2c3761b) ended at 09:03:09 as `hoster_resolve_timeout` without a challenge mark, which does not tell whether the captcha came (row 101: a capture the bound ends before the mark is a `timeout`); the next Filemoon captures decide, for row 106's span. They came in the post-deploy check's burst (its One Piece, Demon Slayer and Interstellar requests through aniworld, filmo and moflix): four logged timeouts on Filemoon's two hosts from 09:03:09 to 09:03:50, none with a challenge mark (one Byse page answered the API's `embedding` 403, so it opened the watch page), and the breaker opened at 09:04:01 (60 s cooldown; the metrics count five timeouts and seven cut resolutions). No Filemoon link was asked after it by 10:03 UTC, so the breaker still shows open and the span stays unread: a check's burst of captures on the production host is no reading of the state's age.
    - **When the captcha shows in a cold capture (row 101, dev container, 15:54 to 15:56 UTC):** `capture_timing.py` prints now when each challenge was first met, in seconds into the run. Six probe processes on fresh Chromium profiles, one after another, three filemoon.to links (kinoking's) and three Byse links (bysezejataos.com, filmo's), two runs each (run 1 cold, run 2 in the same context):

      | Link | Page load (start, took) | First click | `captcha` | `captcha/verify` | Stream (CDN) | Run 2 (warm) |
      |---|---|---|---|---|---|---|
      | filemoon.to 1 | `/e/` 0.3 s, 0.6 s | 3.9 s | 4.1 s | 4.8 s | 5.2 s (owphbf24.com) | no challenge, stream 1.5 s |
      | Byse 1 | `/d/` 0.3 s, 0.3 s | 3.7 s | 3.9 s | 4.3 s | 4.7 s (r66nv9ed.com) | no challenge, 1.4 s |
      | filemoon.to 2 | `/e/` 0.3 s, 0.6 s | 4.0 s | 4.2 s | 5.6 s | 7.2 s (owphbf24.com) | no challenge, 1.7 s |
      | Byse 2 | `/d/` 0.3 s, 0.2 s | 3.6 s | 4.1 s | 4.2 s | 4.6 s (owphbf24.com) | no challenge, 1.2 s |
      | filemoon.to 3 | `/e/` 0.3 s, 0.6 s | 3.9 s | 4.2 s | 4.3 s | 4.8 s (owphbf24.com) | no challenge, 1.6 s |
      | Byse 3 | `/d/` 0.3 s, 0.2 s | 3.6 s | 3.8 s | 5.1 s | 5.5 s (r66nv9ed.com) | no challenge, 1.2 s |

      Every cold capture met the captcha (6 of 6), and none of the warm ones. The `captcha` request came 0.1 to 0.5 s after the first click on the player, which itself came 3.6 to 4.0 s in, about 3 s after the page had loaded (the app's waits before it clicks); the verify followed within 1.4 s and the stream at 4.6 to 7.2 s, well inside the 10 s bound. So in the dev container the captcha's mark comes about 4 s in and is tied to the first click, not to the page load. Limits: one network and a fast machine, six links of two hosts within two minutes; the probe's run excludes the wait for a browser page, which production's bound does not count either, but not the production host's slower page loads and clicks under load. Production's one sample (9 s in, 15:22:19 above) is consistent with a click that came late rather than a captcha that shows later: if the production host's first click lands near 9 s, a capture ended by the 10 s bound before its click carries no mark.

      **On the production host (production 503a0eb, 17:48 and 17:49 UTC, `prodctl.py probe capture_timing`, `--runs 2`, navigation bound 10 s).** Before each run the log showed no `/play`, `/proxy` or HLS line in the last 2 minutes; `stats` before the second: CPU 0.00 of 4 cores. One Byse link, then one filemoon.to link, from the dev runs' links (the browser launches, 3.9 s and 1.9 s, are outside the runs' times):

      | Link | Page load (start, took) | Clicks | `captcha` | `captcha/verify` | Stream (CDN) | Resolve |
      |---|---|---|---|---|---|---|
      | Byse, run 1 (cold) | `/d/` 1.3 s, 0.8 s | 5.2, 10.8, 15.9 s | 7.2 s | – | none | nothing after 21.1 s |
      | Byse, run 2 | `/d/` 0.6 s, 0.8 s | 4.5 s | 5.5 s | 6.8 s | 8.0 s (r66nv9ed.com) | 8.0 s |
      | filemoon.to, run 1 (cold) | `/e/` 1.3 s, 1.4 s | 5.8 s | 6.6 s | 8.0 s | 9.2 s (owphbf24.com) | 9.2 s |
      | filemoon.to, run 2 (warm) | `/e/` 0.8 s, 1.1 s | 5.1 s | – | – | 6.5 s (owphbf24.com) | 6.5 s |

      On an idle production host the first click comes at 5.2 to 5.8 s, about 1.5 s later than in the dev container. The captcha follows it 0.8 to 2.0 s later (dev: 0.1 to 0.5 s), at 5.5 to 7.2 s. The cold filemoon.to capture found its stream at 9.2 s, 0.8 s inside the 10 s bound. The cold Byse capture marked its captcha at 7.2 s, but its verify never came: it clicked twice more and found nothing by 21.1 s. In production its mark would come before the bound, so it would end as `challenge`, not as an unmarked timeout. Its second run met the captcha again, because the first had earned no pass, and passed it (stream at 8.0 s). **Does a late click explain production's 9 s?** Only in part: an idle production host marks the captcha by about 7 s, and a 9 s mark needs the click about 2 s later still. That fits a busier production host; the 15:13 captures above waited up to 28 s for a page on a busy CPU. In every run, here and in the dev container, the captcha came after the first click and never before it, so the first click is the earliest sign of Filemoon's captcha in a capture. Limits: two cold captures on an idle production host, one per host, minutes apart; the busy production host was not measured.
    - **What a kept state holds, its IndexedDB records counted (row 97, dev container, 16:24 to 16:25 UTC):** `capture_timing.py --runs 1 --restore`, one link per hoster, the state taken with `storage_state(indexed_db=True)` as the app took it then:

      | Hoster | Kept state | Run 1 | Control | Restored |
      |---|---|---|---|---|
      | Filemoon | n1mwq.org: cookies `byse_device_id` and `byse_viewer_id`, 7 localStorage keys, 1 IndexedDB database (2 records, 0 encoded); moflix-stream.link: 3 localStorage keys | captcha 4.1 s, stream 5.0 s | captcha 3.7 s, stream 9.3 s | no challenge, stream 1.6 s |
      | Dropload | dr0pstream.com: cookies `aff`, `file_id` and one with an id-like name; cloudflare.com: 1 localStorage key | turnstile 0.6 s, stream 4.5 s | turnstile 0.5 s, stream 4.5 s | no challenge, stream 0.8 s |
      | Vixeo | vixeo.io: 1 localStorage key | no challenge, stream 3.8 s | no challenge, stream 4.7 s | no challenge, stream 3.8 s |

      No kept record needed an encoding: plain JSON holds every value the three players keep, so a keep that leaves out the records plain JSON cannot hold (the File values and invalid dates of the code review's finding 8) loses nothing measured here. Limits: one link per hoster, a fresh profile each, the restore seconds after the keep.
    - **The keep read from the capture's own page (row 97, finding 6; dev container, 16:55 to 16:56 UTC):** the same links, `capture_timing.py --runs 2 --restore`, which now keeps the runs' state as the app does:

      | Hoster | Read (run 1) | State a new context starts from | Run 1 | Control | Restored |
      |---|---|---|---|---|---|
      | Filemoon | 2 hosts in 11 ms | n1mwq.org: cookies `byse_device_id` and `byse_viewer_id`, 7 localStorage keys, 1 IndexedDB database (2 records); moflix-stream.link: 3 localStorage keys | captcha 3.9 s, stream 4.5 s | captcha 3.7 s, stream 4.4 s | no challenge, stream 1.8 s |
      | Dropload | 1 host in 4 ms | dr0pstream.com: cookies `aff`, `file_id` and one with an id-like name | turnstile 0.7 s, stream 9.6 s | turnstile 0.5 s, stream 4.5 s | no challenge, stream 0.7 s |
      | Vixeo | 1 host in 4 ms | none | no challenge, stream 3.7 s | – | – |

      Filemoon's and Dropload's restored runs met no challenge, as with `storage_state()`. Vixeo kept nothing: its player writes its one localStorage key after the stream request, which the read precedes, and it meets no challenge anyway. A synthetic page in Chromium (two origins, routed, no network) shows the read and the restore of the records plain JSON cannot hold:
      - **Read:** the read took 3.9 to 5.5 ms over 5 reads, against 40.8 ms for `storage_state(indexed_db=True)`.
      - **Kept:** a database with only plain records (4 stores, 8 records, a compound key path and a unique index) was kept whole. A database with one plain record beside five others (dates, NaN, a typed array, a date key) was left out, and so was one with a File value and a unique index over invalid dates.
      - **Restore:** a new context created from the read state read every kept record back. The context from `storage_state()`'s state failed: `Error setting storage state` (finding 8, reproduced).
    - **The restore's log lines (row 97):** `capture_state_restored` now follows the creation and names how many origins the context took, so after the next restart it means that the context took the state (the restart of 15:11:28 above could not tell). A refused state falls back to the cookies and drops the kept origins (`capture_state_origins_dropped`).
    - **A frame's failed read (row 97, Mr. Fable's review; dev container, 17:46 to 17:47 UTC):** a synthetic page in real Chromium (the top page and two frames on three hosts, routed, no network), the read as the capture runs it, against c16c84c's one-gather read:

      | Ad frame | c16c84c | Now |
      |---|---|---|
      | answers | 3 hosts kept, 4 ms | 3 hosts kept, 5 to 6 ms |
      | detaches during the read | whole read failed (`Error`), 2 ms | 2 hosts kept, the ad's missed, 4 ms |
      | its main thread blocked for 5 s | whole read failed (`TimeoutError`), 503 ms | nothing kept, every host missed (`TimeoutError`), 403 to 404 ms |

      The blocked frame shares its renderer with the top page here (`--renderer-process-limit=2` in the app), so the top page's read waited too. Limits: one run per case, a synthetic page.
    - **The recycle's relaunch (row 97, V15; dev container, 17:23 to 17:24 UTC):** the shared browser's restart after 200 pages now launches the new browser, bounded to 30 s, and an operation waits for it with its resolve bound stopped. Before, the next operation launched it inside its own bound: 6 to 11 s on the production host at the app's starts, against the 10 s bound. Real Chromium under Xvfb, the restart after 1 page, 5 rounds per row, the next operation under a 10 s work clock:

      | Pool | Next operation | To its page (median) | Bound spent (median) |
      |---|---|---|---|
      | staging | during the restart | 0.73 s | 0.73 s |
      | staging | after the restart | 0.40 s | 0.40 s |
      | row 97 | during the restart | 0.73 s | 0.07 s |
      | row 97 | after the restart | 0.07 s | 0.07 s |

      The 0.33 s between the staging rows' 0.40 s and row 97's 0.07 s is the dev container's launch; on the production host the launch is the 6 to 11 s above. Limits: no network, an empty profile, the dev container's launch time. A crash's relaunch still falls to the next operation.
    - **The read after the stream's hand-over (row 107; dev container, 2026-10-13 09:34:20 to 09:36:55 UTC):** the stream goes out first, and the state's read, the page's close and the store's write follow in a task of their own, the page counted at the gate and holding the browser's lease until it closes. `capture_timing.py --restore --runs 1` (run 1, then a control and a context restored from the kept state, each in a relaunched browser), the probe of this change over staging's code (9466e3f, whose browser and resolver code a1a3f34 has unchanged) and over this change in turn, one link per capture resolver (the links of row 103, collected 2026-10-12 18:42):

      | Hoster | Run 1's stream (staging, this change) | Read, staging | Read, this change | Restored (staging, this change) |
      |---|---|---|---|---|
      | Filemoon (Byse) | 5.0 s, 4.9 s | 9 ms, begun 15 ms before the stream went out | 9 ms, begun 0 ms after it | 2.0 s, 1.8 s |
      | Filemoon (filemoon.to) | 5.5 s, 5.4 s | 11 ms, 17 ms before | 13 ms, 0 ms after | 2.2 s, 3.3 s |
      | Dropload | 4.6 s, 4.8 s | 5 ms, 9 ms before | 6 ms, 0 ms after | 0.7 s, 0.7 s |
      | SuperVideo | 0.8 s, 1.1 s | 47 ms, 51 ms before | 8 ms, 0 ms after | 0.6 s, 0.7 s |
      | Vixeo | 1.3 s, 1.3 s | 5 ms, 8 ms before | 5 ms, 0 ms after | none: the store held no state after either |
      | DoodStream | none: the page did not load (17.6 and 17.8 s, failed) | – | – | – |

      Every restored run streamed without a click or a challenge. Reading: the read now begins after the hand-over, so a stream found near the bound goes out without it, and the state is still kept in a form the next context takes back. In the dev container the read is 5 to 47 ms, so the streams here come no sooner; on the production host one read took 274 ms and another ran into its 0.5 s bound. Limits: one call per hoster and code, one link each, the dev container's CPU; DoodStream's capture was not reached. The production proof is the count of `capture_state_save_failed` with a `hoster_resolve_timeout` of the same request within a second, before and after the rebuild.

### R5: warm-up after a start (gap 5)

- **What:** one background capture per hoster whose breaker came back open, with a link from the restored resolutions. This is round 1's requirement 8, which was never built.
- **Evidence:** the breakers come back open after each restart.
- **Pro:** the first user request after a deploy finds the hoster warm.
- **Contra:** it needs a recent link and costs captures right after the start.
- **Cost:** small to medium.

#### R5 for Filemoon: the captcha pass kept (row 106)

R4's restore took, but a kept Byse state warmed a capture only while its captcha pass lived. Row 106 measured the pass's span, then built the smallest keep that follows from it.

- **The pass (dev container, `capture_timing`-style runs of one Byse link in one browser, a cold capture and three warm ones after it):** the pass is the localStorage item `byse:captcha-clearance:embed` on the player host n1mwq.org, a JSON object with a 170-character token and `expiresAt` in epoch milliseconds, 1785 s after the captcha. A capture with the pass does not renew it:

  | Run | Began | Challenge | Stream | The item after it |
  |---|---|---|---|---|
  | 1 (cold) | 0 | captcha 4.2 s, verify 4.2 s | 4.6 s | new, 1785 s left |
  | 2 | 20 min later | none | 3.2 s | unchanged, 586 s left |
  | 3 | 29 min later | none | 1.9 s | unchanged, 47 s left |
  | 4 | 31 min later | captcha 4.2 s, verify 5.7 s | 6.1 s | new, 1785 s left |

  So the pass lives a fixed 30 min, and a capture just before its end keeps nothing alive; the first capture after it pays the captcha. Limits: one link, one browser, one run per offset.
- **Production (54.2 h of logs on e749a0c, up to the app's loss of its network):** every Byse resolution whose pass was 30 min old or more, or that had none, came without a stream within the 10 s bound: 12 timeouts without a challenge mark and 5 challenges, the passes 84 min to about 23 h old. The new passes came only from background completions past the bound (R2), whose streams came 17.4, 18.1, 21.5 and 31.5 s after the resolution's start, and the one warm Byse stream came 0.6 min after such a completion. Limits: one build and window; the log lines name a link's hoster by its second-level name, which moflix's Byse links share with its VidHide links (`moflix-stream`), so the count may hold some of VidHide's.
- **A gap in R4's keep (fixed with row 106):** the stealth pool kept a host's state at most once per 10 min, so a captcha met within 10 min of the last keep left the old pass in the store, and a restart restored a pass that had gone. A capture whose page met a challenge on its way to the stream now keeps its state at once (`_keep_state(fresh=True)` in `infrastructure/browser/stealth_pool.py`); one without a challenge keeps the interval.
- **The keeper (`PassKeeper`, `infrastructure/hoster_resolvers/pass_keeper.py`):** it reads the pass's expiry from the kept capture state (`pass_expiry()` in `hoster_resolvers/filemoon.py`, over `ClearanceStore.creation_state()`), and 30 s after it resolves the link of the newest cached Filemoon stream again (`HosterResolverRegistry.last_stream()`, then `resolve(link, refresh=True, bound=30)`: past the cache and the challenge cooldown, within a bound of its own; an open breaker still refuses). That capture meets the captcha, and its new pass is kept for the next request. Its page claim is `background` (served last), and it starts only while one of the stealth browser's pages stays free beside its own (at the start's limit of 2 pages: none in use). One capture per pass: one without a new pass ends the keeping until a request's capture brings one, and without a cached Filemoon stream nothing is captured (`no_link`). A capture the bound cuts after the captcha runs on as R2's completion, which keeps the pass when it finds the stream. Its stream is cached anew, so the keeping goes on, one capture per 30 min (48 a day at most), while there is demand: a request looked up a Filemoon link within the last 48 h (Mr. Fable's bound; `_DEMAND_S`). The registry records each resolver's last lookup that a request made (`demand_age()`: `resolve()` with its cache hits and `cached()`, so a stream the keeper's capture cached counts when a request takes it; the keeper's own lookups pass `demand=False`), and the record outlives a restart in the hoster state snapshot. Past 48 h the keeper idles (`idle`), and the next request's lookup starts it again. Production's quiet stretches lasted up to about 23 h, and the cost after the last request is at most 96 captures. Stage `hoster_pass_keep` ([Observability](../features/observability.md#metrics)).
- **The keeper's bound (dev proofs, the app's lifespan with the 10 s bound and one Byse link):** in two runs the keeper fired on time (30 s past the expiry, the claim `background`, past the cache), but production's 10 s bound cut its capture without a challenge mark: no completion, no new pass, and the request after it came without a stream too. Timelines of cold resolutions with a 60 s bound show why: the httpx requests take 0.25 s, the page load took 8.6 s in one run and 0.2 s in the other, the player's first click follows 3 s later (`_MEDIA_AUTOPLAY_WAIT_S`), the captcha request 0.2 s after the click and the stream 0.9 and 2.4 s after the captcha. A page load of more than about 6 s puts the click past the 10 s, and nothing marks the capture. So the keeper's capture has a bound of its own, 30 s (`_BOUND_S`, also its claim's due): what a request's challenged capture gets in production in all, the bound and R2's 20 s completion. Requests keep the 10 s, and a keeper timeout counts toward the breaker. Production's 12 unmarked timeouts of 17 fit the same cut before the captcha; their cause there (page load or the requests before it) is not measured. With the bound, a third run: the keeper fired 30 s past the expiry, its capture met the captcha (page load 0.2 s, the click 3 s later, the captcha request 0.2 s after the click, the stream request 1.65 s after the captcha) and found the stream in 5.7 s, and a new pass came (1766 s left 19 s after the captcha's verify). That capture would have fit the 10 s as well; the bound covers the slow page loads of the runs before. The request 10 s after it met slow responses (5.1 s for the two httpx requests, the page not loaded within the bound) and shows nothing about the pass. A run in one lifespan shows what a request after a capture with the captcha meets: two lookups past the cache streamed without a challenge in 1.6 and 6.3 s, the player starting without a click (cold, it waits for the click and then asks for the captcha). Limits: one run with the bound, one with the warm lookups, one link.
- **Limits:** the keeper follows the latest pass of the kept player hosts (one in every capture measured); a capture still pays the captcha between the expiry and the keeper's capture, about the margin and the capture's time; the free page is checked when the capture starts, not held. The production proof: the first Filemoon request after a quiet stretch streams without the captcha, after the next deploy.

### R6: captures pass widgets (gap 4)

- **What:** `pass_turnstile_widget()` before the play clicks when the page shows a widget.
- **Evidence:** Dropload's cold path of 13.3 s.
- **Pro:** fewer clicks on Turnstile pages.
- **Contra:** so far only Dropload benefits.
- **Cost:** small.

### R7: telemetry (gap 2)

- **What:** the capture's outcome per hoster (`challenge`, `cold`, `warm`, in fixed label sets) and its solve time.
- **Evidence:** none exists today, only log lines.
- **Pro:** R1 to R5 can be judged by numbers.
- **Contra:** none.
- **Cost:** small.

#### R1 and R7 built (row 89, 2026-10-12)

OpenSpec `react-to-challenges`, tasks group 1. The registry gives each attempt a `ChallengeMark` (the context variable `challenge_mark` beside `work_clock`). `solve_cloudflare()` marks an interstitial, `pass_turnstile_widget()` a widget, and `capture_media()`'s request listener a captcha provider's frame (`challenge_frame()`: Cloudflare's challenge platform, hCaptcha, reCAPTCHA but not an invisible one) or a request the resolver names (`challenge_requests`; Filemoon's `captcha` and `captcha/verify`). A cut or empty resolution with a mark ends as `challenge`: not cached, not counted by the breaker, and the hoster's links wait out 1, 5, 15, then 30 minutes (ended by a stream; checked before the breaker). R7's numbers: the `hoster_resolve` outcomes `challenge` and `challenge_cooldown`, the stage's time per outcome as before, and per stream whether the capture passed a challenge (`hoster_resolve_success` → `challenge`, `None` when warm, with the attempt's wall time `duration_ms`), a log field and no label, so the `stream` outcome stays one series.

Dev proof with the app's registry and resolvers (a fresh context per attempt, one current link per hoster, 2026-10-12 09:29 to 09:34 UTC):

| Hoster | Bound | Mark (into the capture) | Outcome | Breaker | Next link |
|---|---|---|---|---|---|
| Filemoon | 6 s (6 runs) and 25 s (1 run) | `hoster_captcha` at 3.6 to 4.5 s (the first request; 6 of the 7 runs printed it) | `stream` at 4.3 to 5.4 s | untouched | - |
| Filemoon | 4.1 s | `hoster_captcha` at 3.7 s | `challenge`, cooldown 60 s | untouched | `challenge_cooldown`, 60 s left |
| Filemoon | 4.2 s | none before the cut | `timeout` | opened | `breaker_open` |
| Dropload | 25 s, 6 runs | `turnstile` at 0.5 to 0.6 s | `stream` at 4.4 to 9.5 s | untouched | - |
| Dropload | 2 s | `turnstile` at 0.6 s | `challenge`, cooldown 60 s | untouched | `challenge_cooldown`, 60 s left |
| Filemoon | 0.3 s | none (no page yet) | `timeout` | opened | - |

One more Filemoon run with a 20 s bound met no captcha and found no stream in 18.7 s: `dead`, cached for 15 minutes, as before (no mark, nothing for R1 to tell). The 4.2 s run is the design's known limit: a captcha requested after the cut leaves the attempt a `timeout`. The 0.3 s control is production's case after a restart (08:55, 2026-10-12): a trial cut while the browser launches has shown nothing and stays a `timeout`. Limits: the dev container, one link per hoster. Production's outcomes over a day after the deploy are task 1.10.

**Deployed 2026-10-12 10:11:59 UTC (10d43f4, with `duration_ms` from c161bfb).** The first production challenge came 94 s after the start: hdfilme's Dropload link fell back to the browser at 10:12:57 (`dropload_captcha_browser_fallback`), met a Turnstile and ended at 10:13:33 as `challenge` (`kind=turnstile`, cooldown 60 s). No `circuit_breaker_*` line for Dropload followed, and its next link (10:13:34) was skipped by the cooldown (`left_s=59`); before row 89 the same attempt was a `timeout`, which the breaker counts (Dropload's trial at 08:55:52 reopened it for 3600 s). The 36 s from the fallback to the outcome are wall time; the resolve bound counts work time only (the page gate's wait stops `work_clock`). SuperVideo's restored trial failed without a mark and reopened for 3600 s (10:12:58), as before. The same two minutes' 25 streams took 0.9 to 3.7 s (`duration_ms`; VOE, Vidmoly, Playmogo, FSST and others). Reading the lines for task 1.10: an attempt's lines (`hoster_resolve_challenge`, `hoster_resolve_success`, `hoster_resolve_timeout`) name the link's host (`dr0pstream`), the guard's lines (`hoster_resolve_challenge_cooldown`, `hoster_resolve_circuit_open`) the resolver (`dropload`).

**Row 96 (code review 2026-10-12, findings 2 and 3; tasks group 3).** The mark also says whether its challenge still stands (`ChallengeMark.open`): the browser passes it when the page got past (`mark_resolution_passed()`: an interstitial that cleared, a widget's token, the hoster's offline notice). A resolution without a stream after a pass is `dead` and cached, since the page gave its verdict (a dead Dropload link behind a cleared interstitial had ended as `challenge`, and its cooldown skipped the hoster's live next link); a cut after a pass stays `challenge` and runs on but starts no cooldown (`hoster_resolve_challenge passed=True cooldown_s=0`). Provider frames still mark by default; DoodStream declares its invisible Turnstile passive (`passive_widget=True`: its token came 1 s after the stream, dev 2026-10-12), and reCAPTCHA counts by its checkbox frame only (its `bframe` loads hidden with every widget). Filemoon's own captcha and Dropload's gate frame give no pass signal before the stream, so a dead link behind them stays `challenge`.

**Row 96's baseline (Mr. Fable, before its deploy).** Production's `scavengarr_hoster_resolve_total` per resolver in 1d79ec7, from its start at 15:11:28 to 15:38 UTC (27 minutes: the post-deploy check, the thirteenth round and row 94's pause check among its requests; the counters go with the container):

| Resolver | `stream` | `cached` | `dead` | `challenge` | `cut` | `timeout` | `breaker_open` | `busy` | `check_error` | `unplayable` |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| direct | 0 | 25 | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| doodstream | 6 | 39 | 8 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| dropload | 0 | 0 | 0 | 1 | 0 | 0 | 1 | 0 | 6 | 0 |
| filemoon | 4 | 15 | 0 | 1 | 7 | 6 | 20 | 1 | 0 | 2 |
| firestream | 1 | 4 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| fsst | 1 | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| gxplayer | 1 | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| mixdrop | 0 | 5 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| rpmplay | 0 | 5 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| strmup | 1 | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| supervideo | 0 | 0 | 0 | 0 | 0 | 0 | 12 | 0 | 0 | 0 |
| upns | 0 | 5 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| vidhide | 1 | 4 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| vidmoly | 6 | 33 | 6 | 0 | 3 | 0 | 0 | 0 | 0 | 0 |
| vidsonic | 1 | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| vinovo | 1 | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| voe | 17 | 48 | 2 | 0 | 5 | 0 | 0 | 0 | 0 | 0 |

Challenge completions (`scavengarr_hoster_challenge_completion_total`): dropload `none` 1, filemoon `stream` 1. Filemoon's `challenge` and completion are the trial of 15:22:19 (R4, the restart of 15:11:28), its `timeout`s the cold captures before it; Dropload's `check_error`s are playback checks of its CDN. Row 96's round compares the same outcomes, with `joined`, `challenge_cooldown` and the completions, per hoster and per hour of uptime.

**Row 96's round (Ultracode, after its deploy).** The same counters in 503a0eb, from its start at 17:42:29 to 18:09:30 UTC (27 minutes, as long as the baseline; the post-deploy check's requests and the round's plays, four titles from 17:55 to 17:59, among them):

| Resolver | `stream` | `cached` | `dead` | `challenge` | `cut` | `timeout` | `breaker_open` | `busy` | `check_error` | `unplayable` |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| direct | 0 | 25 | 14 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| doodstream | 9 | 25 | 4 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| dropload | 0 | 0 | 0 | 0 | 0 | 1 | 7 | 0 | 2 | 0 |
| filemoon | 7 | 12 | 0 | 0 | 9 | 10 | 8 | 0 | 0 | 2 |
| firestream | 2 | 4 | 0 | 0 | 3 | 1 | 0 | 0 | 0 | 0 |
| fsst | 3 | 4 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| gxplayer | 1 | 4 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 1 |
| mixdrop | 2 | 5 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| rpmplay | 0 | 4 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| strmup | 3 | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| supervideo | 0 | 0 | 0 | 0 | 0 | 0 | 9 | 0 | 0 | 0 |
| upns | 0 | 4 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| vidhide | 2 | 3 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| vidmoly | 6 | 18 | 3 | 0 | 3 | 0 | 0 | 0 | 0 | 0 |
| vidsonic | 3 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| vinovo | 1 | 4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| voe | 25 | 25 | 6 | 0 | 8 | 0 | 0 | 0 | 0 | 0 |

No challenge completion, no `joined` or `challenge_cooldown`, and no `hoster_resolve_challenge` line in the 27 minutes; the two Turnstile passes (`turnstile_widget_passed`) were the sto plugin's pages, not a hoster's. Two breakers opened: Filemoon's at 17:45:43 for 60 s, Dropload's at 17:46:05 for 3600 s. Against the baseline:

- **Nothing moved from `timeout` to `challenge`: Filemoon showed the app no captcha.** The state restored at the start spared it (task 3.2 above, a young state). Its 7 streams came with `challenge=None`, in 5.9, 8.0, 8.2, 8.7, 10.3 and 10.3 s, and one in 33.5 s (`duration_ms`; at least 23.5 s of it waited for a page, which stops the bound). Its 10 timeouts (9 on the filemoon and bysezejataos hosts, 1 on a moflix-stream link) ended at the bound without a mark: six from 17:44:01 to 17:45:43 among the post-deploy check's requests, four during the plays (17:57:12 to 17:58:46). On the idle production host a warm filemoon.to capture streamed at 6.5 s (the production host runs of 17:48 and 17:49 above); under the window's load most streams came near the bound, and the timeouts are captures that found none in their 10 s of work. Whether one of them would have met a captcha after its cut, the counters cannot tell; none of the 7 streams met one. Row 96's split between a passed and a standing challenge had nothing to judge in production yet.
- **`cut` against the completions.** Filemoon 9 cuts (baseline 7), all of them before the plays, and 28 for all resolvers (baseline 18); no completion (baseline 2). A `cut` is a resolution whose request ended first (its answer went out); the registry cancels it, challenge or not. A completion follows only the bound's cut after a challenge (`_hand_over`), and there was none to hand over.
- **`breaker_open`.** Filemoon 8 (baseline 20). Its opening followed five failures in a row after its stream at 17:45:05 (an `unplayable` at 17:45:12, four `timeout`s); three links were skipped while it was open, and five at 17:50:28 while its half-open trial ran, whose stream at 17:50:36 closed it. The plays' four timeouts stayed below the threshold of five, and the stream at 17:58:56 reset the count. Dropload's 7 follow its opening: its three browser fallbacks (hdfilme's links) found two streams that failed the CDN check (`ConnectTimeout`, the two `check_error`s), and the third ended at the bound without a mark. SuperVideo's 9 skip the breaker it was restored with; no trial ran in the window.

The plays (One Piece S22E04, Interstellar, Demon Slayer S02E01, Breaking Bad S01E01; 26 of 27 streams played) show nothing new for this table: their one failure, VidHide's CDN cutting an HLS segment mid-body, is row 102.

**Row 96's first challenge in production (2026-10-13, 4357d6a, Mr. Fable).** After the restart of 07:10:03 UTC the restored state did not spare Byse's captcha (R4 above), and the first Filemoon resolution met it: it ended as `challenge` (`hoster_resolve_challenge kind=hoster_captcha passed=False cooldown_s=60`, 07:11:34) instead of a breaker failure, its hoster got the first cooldown (1 minute), and the completion found the stream 1.6 s after the cut (`scavengarr_hoster_challenge_completion_total{outcome="stream",resolver="filemoon"}` 1). One case.

### R8: plugin-side fixes (gap 6)

- **What:**
  - the memo path counts its challenge;
  - a failed solve gets a negative memo;
  - one retry with backoff follows `cloudflare_unsolved`;
  - s.to's gate and animeloads' wait are counted.
- **Evidence:** item e of `cloudflare-solvers.md`; row 70's reading on 2026-10-18.
- **Pro:** the record shows what the plugins meet, with fewer pointless browser fetches.
- **Contra:** none.
- **Cost:** small each.

### R9: curl_cffi for HTTP steps (no listed gap)

- **What:** HTTP steps reuse the browser's clearance through curl_cffi's TLS imitation (round 1's requirement 14).
- **Evidence:** `cf_clearance` is bound to the IP, the User-Agent and probably the TLS fingerprint.
- **Pro:** it frees the browser for some hosters' API steps.
- **Contra:** a new dependency, and the binding differs per hoster and is unpublished.
- **Cost:** medium, and only once a hoster needs it.

### Not Recommended

- **Paid solver APIs** (2Captcha, CapSolver, Anti-Captcha; about 1.2 to 2 $ per 1000 solves).
  - The maintainer decided against them on 2026-09-28.
  - Their workers do not share the production host's VPN address, which a challenge page's clearance is bound to.
  - Their seconds of latency eat the budget.
  - Their legal footing is unclear (circumvention rules, the sites' terms).
- **A solver container for hosters.**
  - FlareSolverr (v3.5.2, 2026-09) and Byparr (v3, a patched Firefox) return the page and cookies, not the player's traffic.
  - They run a second browser on the production host.
  - Byparr's Firefox cookies do not fit the app's Chromium.
  - They stay an option for plugin pages (round 1's sidecar).
- **A human in the loop.** Decided against on 2026-09-28, and Stremio has no way to show a captcha.

### What Others Do

- **Prowlarr** calls FlareSolverr only when it detects a challenge, then retries with the solver's User-Agent and cookies, which it caches per host.
- **JDownloader** hands a captcha to a dialog or a solver account and waits up to 300 s. Its Filemoon (Byse) plugin asks for a reCAPTCHA only when the API says `recaptcha_required`.
- **yt-dlp** solves nothing. It fails with a hint to its TLS impersonation or the browser's cookies.

## Recommended Order

1. R7 and R1 together: the outcome makes the skip short and measurable (small).
2. R2: the first cold capture warms the context. Until it is built, R3's YAML value is the stopgap, if the maintainer wants Filemoon on the production host now.
3. R4's premise (which state warms) measured with `capture_timing`, then R4 and R5.
4. R6 and R8 when the numbers of R7 and row 70 ask for them; R9 only for a hoster that needs it.

## Decision and Expected Gain (2026-10-12)

The maintainer's decision of 07:02 UTC (AskUserQuestion in Mr. Fable's session): R1 with R7 (backlog row 89), R2 (row 90), and R4 with R5 (row 91, its premise measured first). R3 is not chosen, so production's resolve bound stays 10 s. R6, R8 and R9 wait for R7's and row 70's numbers. With the choice the maintainer asked what the options would gain. The answer, given in the session:

- **The baseline (2026-10-11).**
  - Filemoon and Dropload delivered next to nothing on the production host. Every trial ran cold (Filemoon 17.1 and 17.8 s, Dropload 13.3 s), the 10 s bound cut it, and the breaker skipped the hoster for up to an hour.
  - One cut trial at 12:13 UTC was followed by 63 refused Filemoon links within five minutes (aniworld 36, kinoking 13, moflix 8, filmo 6).
  - Warm captures fit the bound: Filemoon 5.8 and 6.7 s, Dropload 2.2 s.
- **R1 with R7.**
  - On its own it adds no stream, since every capture still runs cold and is cut.
  - The skip shrinks from up to an hour to 1 to 30 minutes, and a challenge no longer reads as a dead hoster.
  - Its numbers per hoster (challenge, cold, warm, the time to the stream) judge R2, R4 and R5, and decide R6 and R8.
- **R2, the main gain.**
  - The first request after a restart or a recycle still misses that hoster's stream. But the capture finishes in the background, its stream lands in the resolution cache, and the hoster runs warm within the bound from then on.
  - Open: Filemoon's cold path gave a stream in 2 of 4 production runs (none by 20.8 and 25.0 s in the other two), so whether 30 s rescues more is unmeasured.
  - Also unmeasured: how long a context stays warm (R7 shows it).
- **R4 with R5, a smaller gain on top of R2.**
  - The first request after a deploy, a restart or a recycle (every 200 pages) finds the hoster warm.
  - Unproven until row 91's measurement: if the warm state lives only in the browser's memory, R4 drops out and R5 remains.
- **Out of reach.**
  - A real image captcha.
  - A hoster that refuses the VPN address.
  - Filemoon's `embedding from this domain is not allowed` answer, another failure.
  - The share of all streams: no count of all links exists, and R7's numbers give one.
- **Costs.**
  - A cold capture holds its browser page up to 20 s past the cut, while plugin pages and other captures may wait for one (`browser_page_wait` shows it).
  - A request that waits for Filemoon's stream waits for its warm capture, about 6 s, within the bound.

After each of the three deploys, compare the following against the baseline:
- Filemoon's and Dropload's `hoster_resolve` outcomes per day (stream, challenge, timeout);
- the cooldown's skips against the breaker's refusals;
- the warm captures' times.

## Sources

Research of 2026-10-11 and 2026-10-12. The claims about third-party projects come from these pages.

- **Solvers:**
  - FlareSolverr's [releases](https://github.com/FlareSolverr/FlareSolverr/releases) and [README](https://github.com/FlareSolverr/FlareSolverr);
  - Byparr's [releases](https://github.com/ThePhaseless/Byparr/releases);
  - Prowlarr's [FlareSolverr proxy](https://github.com/Prowlarr/Prowlarr/blob/develop/src/NzbDrone.Core/IndexerProxies/FlareSolverr/FlareSolverr.cs).
- **Cloudflare:**
  - [clearance](https://developers.cloudflare.com/cloudflare-challenges/concepts/clearance/);
  - [challenge passage](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/challenge-passage/) (30 min by default);
  - [Turnstile's server-side validation](https://developers.cloudflare.com/turnstile/get-started/server-side-validation/) (a token is valid for 300 s and only once).
- **Browser state:** [Playwright's storage state](https://playwright.dev/python/docs/auth).
- **Backoff and ignored failures:**
  - Servarr's [EscalationBackOff](https://github.com/Sonarr/Sonarr/blob/develop/src/NzbDrone.Core/ThingiProvider/Status/EscalationBackOff.cs);
  - [resilience4j's circuit breaker](https://resilience4j.readme.io/docs/circuitbreaker).
- **TLS imitation:** [curl_cffi](https://github.com/lexiforest/curl_cffi).
- **Paid solvers:** [CapSolver's prices](https://docs.capsolver.com/en/pricing/); the court's view of captchas as an access control, [Ticketmaster v. Prestige](https://www.courtlistener.com/opinion/7329861/ticketmaster-llc-v-prestige-entmt-inc/).
