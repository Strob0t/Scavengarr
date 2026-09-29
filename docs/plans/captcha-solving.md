[← Back to Index](../features/README.md)

# Plan: Captcha and Challenge Solving

**Status:** In progress (started 2026-09-28). Step 1 (ALTCHA + grab-time resolution for nox) done.
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
7. **Keep clearances**: Patchright persistent profile (`user_data_dir` in the data directory, not world-readable), dropped and recreated when corrupt. One browser context for all pages; one process per profile directory.
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

## Steps

| # | Step | Status |
|---|---|---|
| 1 | ALTCHA solver, `GrabResolvingPlugin` + `CrawlJobResolveUseCase`, nox links on grab | done |
| 2 | nox: log the gateway's block reason, live smoke for grab resolution | open |
| 3 | Shared captcha detector (`infrastructure/captcha/detect.py`), logging + scoring | open |
| 4 | animeloads: odd-one-out captcha in the browser, links on grab | open |
| 5 | Cloudflare: persistent profile, `channel="chrome"` check, warmup at startup | open |
| 6 | Embedded Turnstile tokens (vinovo, devideosrc, doodstream) | open |
| 7 | Byparr sidecar as second `BrowserFetcherPort` | open |
| 8 | Recognizer container (opt-in) | open |

Each step: TDD, own commits, docs in the same commit, results recorded here.
