[← Back to Index](../features/README.md)

# Plan: Reactive Challenge Handling (Round 2)

**Status:** Proposed 2026-10-12 (Mr. Fable); nothing is built yet, and the maintainer decides per item.

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
- **Contra:** it holds one of the browser's pages for up to 30 s (plugin pages keep their priority).
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
- **Contra:** which state warms Filemoon and Dropload is unmeasured. A `capture_timing` run with a restored state has to show it first.
- **Cost:** medium.

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
