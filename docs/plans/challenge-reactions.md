[← Back to Index](../features/README.md)

# Plan: Reactive Challenge Handling (Round 2)

**Status:** Decided 2026-10-12 07:02 UTC (the maintainer): R1 with R7, R2, and R4 with R5 become backlog rows 89 to 91; R3 is not chosen; R6, R8 and R9 wait for the numbers ([Decision and Expected Gain](#decision-and-expected-gain-2026-10-12)). R4 is built (row 91, OpenSpec `keep-capture-state`, 2026-10-12).

**Request:** the maintainer's, of 2026-10-11 17:41 UTC: after the release v0.4.0, research reactive approaches to Cloudflare challenges and captchas and document them ("reaktive lösungsansätze für cloudflare und captchas einlegen und diese dokumentieren").

**Related:**
- [captcha-solving.md](captcha-solving.md): round 1, done 2026-09-29. One detector, the chain httpx → own browser → optional sidecar, and clearance cookies that survive a restart.
- [antibot-patchright.md](antibot-patchright.md), [cloudflare-solvers.md](cloudflare-solvers.md), [browser-page-budget.md](browser-page-budget.md).
- [filmo-plugin.md](filmo-plugin.md): Filemoon on the Pi.
- [header-bound-hosters.md](header-bound-hosters.md): Dropload and Vixeo on the Pi.

Reactive means what Scavengarr does once a page shows a challenge or a captcha, not how it avoids one; round 1 and the Patchright work cover avoiding.

## What Production Shows

| Case | Cold (fresh profile) | Warm | Bound in production | Outcome |
|---|---|---|---|---|
| Filemoon's player captcha on the Pi (the player's own `captcha` and `captcha/verify`, then clicks) | 17.1 s, or no stream by 20.8 s (row 79); 17.8 s, or none by 25.0 s (row 78) | 5.8 and 6.7 s | `http.timeout_resolve_seconds` 10 s (default 15) | Every half-open trial since a restart timed out and reopened the breaker for an hour (2026-10-11 16:34:53 and 17:36:19 UTC). The breaker refused 63 links in that hour. |
| Dropload's Turnstile on the Pi | 13.3 s (the widget and two clicks) | 2.2 s | 10 s | The trials of 16:34:29 and 17:37:05 UTC timed out the same way. |
| Plugin pages behind Cloudflare on the Pi | 10 to 20 s per solve; s.to's widget 16 to 70 s | the clearance cookie, 30 min by default | the plugin timeout, 30 s | Solved, or the search times out (`challenges_unsolved`). |

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
- **Evidence:** the plugins' shielded solve; on the Pi cold 17.1 s, warm 5.8 s.
- **Pro:** the next request finds a warm hoster, and often the resolved stream itself.
- **Contra:** it holds one of the browser's pages for up to 30 s. The page gate ranks plugin and capture pages alike (by their due time, after `play`), so plugin pages and other captures may wait for a page meanwhile.
- **Cost:** medium (the stealth pool, the registry, the page budget's tests).

### R3: a capture budget of its own (gaps 1 and 3)

- **What:** a separate time bound for captures, or simply production's resolve bound raised: `http.timeout_resolve_seconds` 25 in the Pi's YAML, against the default of 15.
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

`scripts/probes/capture_timing.py --runs 2 --restore` (03614ef): two runs in one context, then that context's `storage_state(indexed_db=True)` held in memory, then the same link in a relaunched browser in a fresh context (`control`) and in a relaunched browser in a context restored from the state (`restored`). One current link per hoster, in the dev container and on the Pi (before 07:29 UTC, 61 s and 44 s per probe, production idle before and after: CPU 0.01 and 0.00 of 4 cores). The challenge column is what the run's pages requested: the player's `captcha` and `captcha/verify` (Filemoon), `turnstile` for Cloudflare's challenge host (Dropload). The time is the time to the stream.

| Hoster | Run | Dev: challenge | Dev: stream | Pi: challenge | Pi: stream |
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
- **Dropload**: the cookies `aff`, `file_id` and one with an id-like name on the hoster's host; 1 localStorage key on `cloudflare.com` (the Turnstile widget). On the Pi also a `GL_UI4` cookie on each of two ad domains.

Findings:

1. **The restored context runs warm.** It met no challenge for either hoster in either place. Dropload's restored run on the Pi took 3.3 s against 12.4 s for the control, past the 10 s resolve bound. Filemoon's skipped the captcha but still clicked once and took 8.4 s against 9.3 s: on the Pi its page load started 2.4 s into the run, and the warm run 2 also needed 5.8 s and one click. So the restore removes Filemoon's challenge, not its whole cold time.
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
- **Row 93 built (2026-10-12): the browser starts with the app.** Once the app reports ready, its lifespan runs `StealthPool.warmup()` in the background: the shared browser's launch, then the stealth context with the kept state. Dev server (`PYTHONPATH=src`, port 7981, its cache a copy of the Filemoon proof's above, `SCAVENGARR_ENVIRONMENT=prod` so that the start does not clear it), no request made:

  | Time (UTC) | Line |
  |---|---|
  | 09:20:47.557 | `app_startup_complete` |
  | 09:20:47.840 | `shared_browser_launched` |
  | 09:20:47.841 | `capture_state_restored` moflix-stream.link, n1mwq.org |
  | 09:20:47.918 | `stealth_pool_started` |

  - The launch took 0.3 s in the dev container (about 9 s on the Pi). A run with an empty cache logged the launch and `stealth_pool_started` 0.7 s after `app_startup_complete` and no restore line; a run whose port was taken shut down during the warm-up without an error.
  - Production: the first capture after the next restart, with these lines before it.

### R5: warm-up after a start (gap 5)

- **What:** one background capture per hoster whose breaker came back open, with a link from the restored resolutions. This is round 1's requirement 8, which was never built.
- **Evidence:** the breakers come back open after each restart.
- **Pro:** the first user request after a deploy finds the hoster warm.
- **Contra:** it needs a recent link and costs captures right after the start.
- **Cost:** small to medium.

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

OpenSpec `react-to-challenges`, tasks group 1. The registry gives each attempt a `ChallengeMark` (the context variable `challenge_mark` beside `work_clock`). `solve_cloudflare()` marks an interstitial, `pass_turnstile_widget()` a widget, and `capture_media()`'s request listener a captcha provider's frame (`challenge_frame()`: Cloudflare's challenge platform, hCaptcha, reCAPTCHA but not an invisible one) or a request the resolver names (`challenge_requests`; Filemoon's `captcha` and `captcha/verify`). A cut or empty resolution with a mark ends as `challenge`: not cached, not counted by the breaker, and the hoster's links wait out 1, 5, 15, then 30 minutes (ended by a stream; checked before the breaker). R7's numbers: the `hoster_resolve` outcomes `challenge` and `challenge_cooldown`, the stage's time per outcome as before, and per stream whether the capture passed a challenge (`hoster_resolve_success` → `challenge`, `None` when warm), a log field and no label, so the `stream` outcome stays one series.

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
  - Their workers do not share the Pi's VPN address, which a challenge page's clearance is bound to.
  - Their seconds of latency eat the budget.
  - Their legal footing is unclear (circumvention rules, the sites' terms).
- **A solver container for hosters.**
  - FlareSolverr (v3.5.2, 2026-09) and Byparr (v3, a patched Firefox) return the page and cookies, not the player's traffic.
  - They run a second browser on the Pi.
  - Byparr's Firefox cookies do not fit the app's Chromium.
  - They stay an option for plugin pages (round 1's sidecar).
- **A human in the loop.** Decided against on 2026-09-28, and Stremio has no way to show a captcha.

### What Others Do

- **Prowlarr** calls FlareSolverr only when it detects a challenge, then retries with the solver's User-Agent and cookies, which it caches per host.
- **JDownloader** hands a captcha to a dialog or a solver account and waits up to 300 s. Its Filemoon (Byse) plugin asks for a reCAPTCHA only when the API says `recaptcha_required`.
- **yt-dlp** solves nothing. It fails with a hint to its TLS impersonation or the browser's cookies.

## Recommended Order

1. R7 and R1 together: the outcome makes the skip short and measurable (small).
2. R2: the first cold capture warms the context. Until it is built, R3's YAML value is the stopgap, if the maintainer wants Filemoon on the Pi now.
3. R4's premise (which state warms) measured with `capture_timing`, then R4 and R5.
4. R6 and R8 when the numbers of R7 and row 70 ask for them; R9 only for a hoster that needs it.

## Decision and Expected Gain (2026-10-12)

The maintainer's decision of 07:02 UTC (AskUserQuestion in Mr. Fable's session): R1 with R7 (backlog row 89), R2 (row 90), and R4 with R5 (row 91, its premise measured first). R3 is not chosen, so production's resolve bound stays 10 s. R6, R8 and R9 wait for R7's and row 70's numbers. With the choice the maintainer asked what the options would gain. The answer, given in the session:

- **The baseline (2026-10-11).**
  - Filemoon and Dropload delivered next to nothing on the Pi. Every trial ran cold (Filemoon 17.1 and 17.8 s, Dropload 13.3 s), the 10 s bound cut it, and the breaker skipped the hoster for up to an hour.
  - One cut trial at 12:13 UTC was followed by 63 refused Filemoon links within five minutes (aniworld 36, kinoking 13, moflix 8, filmo 6).
  - Warm captures fit the bound: Filemoon 5.8 and 6.7 s, Dropload 2.2 s.
- **R1 with R7.**
  - On its own it adds no stream, since every capture still runs cold and is cut.
  - The skip shrinks from up to an hour to 1 to 30 minutes, and a challenge no longer reads as a dead hoster.
  - Its numbers per hoster (challenge, cold, warm, the time to the stream) judge R2, R4 and R5, and decide R6 and R8.
- **R2, the main gain.**
  - The first request after a restart or a recycle still misses that hoster's stream. But the capture finishes in the background, its stream lands in the resolution cache, and the hoster runs warm within the bound from then on.
  - Open: Filemoon's cold path gave a stream in 2 of 4 Pi runs (none by 20.8 and 25.0 s in the other two), so whether 30 s rescues more is unmeasured.
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
