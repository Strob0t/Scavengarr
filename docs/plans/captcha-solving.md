[← Back to Index](../features/README.md)

# Plan: Captcha and Challenge Solving

**Status:** Done (2026-09-29): steps 1–7 implemented; step 8 (recognizer container) deferred until a target needs it. Open limits: animeloads captcha quota per IP (KNOWN_ISSUES), Byparr adapter not verified against a running sidecar.
**Priority:** High (captchas block links in nox, animeloads, vinovo, devideosrc, doodstream; Cloudflare costs minutes per uncached search)
**Related:** `docs/plans/antibot-patchright.md` (Patchright, headful Turnstile click, browser fallback), `docs/features/crawljob-system.md` → Grab-Time Resolution, `src/scavengarr/infrastructure/{browser,captcha}/`

## Scope

In scope: Cloudflare page challenges, embedded Turnstile widgets, reCAPTCHA/hCaptcha, site-specific captchas (animeloads, nox), proof-of-work captchas (ALTCHA and similar), DDoS-Guard.

Non-goals: paid solver services, a human in the loop, filecrypt/container captchas (JDownloader solves those), training own models.

## Requirements

Must:
1. **Detect** one shared classification per response/page: `cloudflare_page`, `turnstile`, `recaptcha`, `hcaptcha`, `ddos_guard` (incl. the manual `?check=1` page), `altcha`. Every hit is logged (plugin/hoster, kind, outcome, `duration_ms`) and reaches scoring (`captcha_detected`, `error_kind="captcha"`).
2. **One chain**, plugins and resolvers never pick a strategy themselves: httpx → own browser (Patchright headful under Xvfb) → optional Byparr/FlareSolverr sidecar (Cloudflare pages only) → optional recognizer container (widget/image captchas only).
3. **Best effort by default**: click the checkbox / let invisible variants pass; on an image or audio task without an enabled recognizer, give up cleanly (warning, partial result, scoring entry, no crash, no loop).
4. **Recognizer container, opt-in**: only active when `recognizer_url` is set. API `POST /v1/recognize {kind: audio|image_text|odd_one_out, data_b64 | images_b64}` → `{text | index, confidence}`. Stateless, no browser of its own: our browser drives the captcha, the container only transcribes/recognizes. Unreachable or slow → best effort + warning. Defaults: faster-whisper (audio), ddddocr (text); no vision LLM.
5. **Sidecar, opt-in**: Byparr (FlareSolverr-compatible API, `solver_url`) as fallback when the own browser fails or no display is available.
6. **Proof-of-work captchas** (ALTCHA, Anubis, mCaptcha) are solved in-process with `hashlib`, one fresh challenge per submit.
7. **Keep clearances**: clearance cookies (`cf_clearance`, DDoS-Guard `__ddg*`) per domain in `CachePort` with their own expiry, restored into new browser contexts (revised 2026-09-29, see Decisions).
8. **Warmup**: at startup solve the hosts the profile holds a clearance for; afterwards refresh only on demand (no periodic background load). The first search after a long idle may be slow.
9. **Limits**: Xvfb stays required (headless = degraded mode with warning); at most 2 browser pages, one Chromium, 1–4 GB RAM budget; fixed time budget per solve.
10. **Config/security**: every solver can be disabled via config and `SCAVENGARR_*`; tokens and cookies are never logged.
11. **Tests**: deterministic unit tests with fixture HTML per captcha kind and per chain branch; live smoke per target; opt-in solvers skipped when not configured.

Should:
12. Re-enable targets once a solver passes: vinovo, doodstream, devideosrc (Turnstile token), animeloads (odd-one-out). wolfstream is anti-bot JS, not a captcha → separate.
13. Metrics per target and kind: solve rate, p50/p95 duration, give-up rate.
14. Reuse `cf_clearance` in curl_cffi (same UA/IP, matching impersonate profile). New dependency → separate justification.

## Decisions (user, 2026-09-28)

| Topic | Decision |
|---|---|
| Strategies | own browser + Byparr sidecar; no paid solvers, no human in the loop |
| Image/audio captchas | best effort by default, recognizer container as opt-in |
| Latency | warmup at startup + on demand (A3) |
| Deployment | Xvfb/headful stays required |
| Clearance storage | persistent browser profile (B2) |
| Custom captchas | JDownloader sources → spike → descope if not text/audio (C1→C2→C3) |
| Recognizer API | own mini API (D1) |
| nox links | resolved at grab time, not search time |

Revised 2026-09-29 (after the spikes):

| Topic | Decision |
|---|---|
| Clearance storage | clearance cookies per domain in `CachePort` (B3) instead of a persistent profile (B2): a persistent profile has exactly one browser context, which would end per-plugin and per-request isolation and allow only one process per profile, for a gain limited to restarts within the cookie lifetime (~30 min for `cf_clearance`, ~20 min for DDoS-Guard) |
| animeloads | one search result per release; grab resolves all episodes of that release |
| Click'n'Load decryption | `cryptography` as a direct dependency |
| Recognizer container | deferred: no current target needs it (animeloads is solved by pixel comparison, vinovo/DoodStream need no captcha, no reCAPTCHA/hCaptcha targets) |

## Research summary (2026-09-28)

- **Cloudflare**: Patchright recommends `launch_persistent_context(..., channel="chrome", headless=False, no_viewport=True)` and no custom UA. In a 31-target benchmark `channel="chrome"` mattered more than the leak patches. CDP clicks on Turnstile were detectable via `screenX/screenY` (Chrome fix 2025); OS-level clicks (xdotool under Xvfb) are the fallback. `cf_clearance` lasts 30 min by default (15–45 min per zone), bound to visitor/device (in practice IP + UA + TLS).
- **Sidecars**: Byparr v3 (Firefox-based, FlareSolverr API, no Xvfb) is maintained; FlareSolverr's own README says its captcha solvers do not work.
- **Embedded Turnstile**: tokens live 300 s and are single-use → generate just-in-time in the own browser, no pool.
- **reCAPTCHA v2 audio**: local ASR (faster-whisper base int8, ~150 MB) is feasible; the real limit is Google's per-IP "automated queries" block. v3 score depends on IP and profile history.
- **hCaptcha**: hcaptcha-challenger now needs the Gemini API; local vision models cost 4–8 GB RAM with no proven success rate → do not invest until a target needs it.
- **OCR**: ddddocr (MIT, ONNX, CPU) is the best default for text captchas.
- **Legal (not legal advice)**: BGH I ZR 224/12 allows screen scraping in principle; circumventing a technical protection measure can be unfair competition. The operator carries the risk.
- **anime-loads.org**: custom "odd one out" image captcha (`POST /files/captcha` → images → pick the differing image → verify), solved by pixel comparison in open-source tools (DanielC000/aniloads); verification works only in page context. DDoS-Guard in front; a rejected client gets a manual captcha on `?check=1`.
- **nox.to**: ALTCHA v2 (PBKDF2/SHA-256, cost 5000, prefix `00`), no JDownloader plugin exists. One pass token unlocks all links of a release; the 5 s countdown is client-side only; replayed payloads are rejected; anonymous users have hourly/weekly limits.

Sources: see the research report in the 2026-09-28 session (Patchright README, Cloudflare docs on Challenge Passage/Turnstile validation, altcha.org docs, GitHub repos of Byparr, FlareSolverr, hcaptcha-challenger, ddddocr, aniloads).

## animeloads spike (2026-09-29)

Live, Patchright headful under Xvfb:

- DDoS-Guard JS challenge clears in ~6 s; the media page lists releases as `#downloads ul.nav-pills li a[href="#download_<n>"]` ("Release 2: 1080p").
- Links per release and episode: `POST /ajax/captcha` with `enc=base64(["media",<slug>,"downloads",<release>,<episode index | "cnl">])` and `response=nocaptcha` → `{"code":"error","message":"noadblock"}`, i.e. captcha required.
- Captcha: `POST /files/captcha {cID:0, rT:1}` → 5 image hashes (48×48 PNG, `GET /files/captcha?cid=0&hash=<h>`); the odd one out has ~4× the pixel difference of the others (7448 vs ~1888). Comparing the images on a canvas inside the page solves it without Python image libraries; `{cID:0, pC:<hash>, rT:2}` → `"1"`; then `POST /ajax/captcha {enc, response:"captcha", captcha-idhf:0, captcha-hf:<hash>}` → `{"code":"success", "content":{…{"hoster":"rapidgator","cnl":{"jk","crypted"}}}}`. Solved on the first try in 1.5 s.
- The requests must run in the page's main world with the site's jQuery (`page.evaluate(..., isolated_context=False)`; Patchright isolates `evaluate` by default).
- Links come as Click'n'Load: AES-128-CBC, key = IV = `unhexlify(jk)` (some clients swap hex chars 15/16), zero padding → `https://rapidgator.net/file/…/onepunchman.1080p.e01.rar.html`.
- Error messages: `slowdown` (rate limit), `wrong_captcha`, `cnl_login`.

Follow-up findings: the whole-release request (`"cnl"`) needs a login (`cnl_login`); anonymously every episode needs its own captcha. Rapid captcha bursts get `wrong_captcha` or rejected answers, so grabs pace ~6 s per episode.

## Embedded Turnstile (2026-09-29)

None of the three targets needs a token pipeline:

- **vinovo**: Turnstile only guards the official download button (`/api/file/urldown`); the player path (`/api/file/url/{id}`) needs no captcha → dedicated `VinovoResolver` over httpx.
- **DoodStream**: the invisible Turnstile passes by itself in the headful stealth browser; the stream was missed only because its CDN URL has no file extension → `capture_media()` also takes video-element requests.
- **devideosrc download embed**: one Turnstile click in the headful browser (6 s) reveals base64 hoster links; not integrated because hdfilme and streamcloud already get the same hosters captcha-free from the devideosrc player API.

A generic "solve the widget, read `cf-turnstile-response`" helper stays unbuilt until a target needs the raw token.

## Steps

| # | Step | Status |
|---|---|---|
| 1 | ALTCHA solver, `GrabResolvingPlugin` + `CrawlJobResolveUseCase`, nox links on grab | done |
| 2 | nox: log the gateway's block reason, live smoke for grab resolution | done |
| 3 | Shared captcha detector (`infrastructure/captcha/detect.py`), logging + scoring | done |
| 4 | animeloads: odd-one-out captcha in the browser, links on grab | done (anonymous: releases ≤ 13 episodes; login path untested live) |
| 5 | Keep clearance cookies across restarts (B3) | done (live: 4.9 s with challenge → 0.9 s after restart) |
| 6 | Embedded Turnstile tokens (vinovo, devideosrc, doodstream) | done: none needs a token flow (see below) |
| 7 | Byparr sidecar as second `BrowserFetcherPort` | done (not verified live: no Docker in the dev container) |
| 8 | Recognizer container (opt-in) | deferred (no target needs it) |

Each step: TDD, own commits, docs in the same commit, results recorded here.
