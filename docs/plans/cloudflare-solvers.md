# Cloudflare-Solver: FlareSolverr, trawl und Scavengarrs eigener Stack

Analyse vom 2026-10-09 (FlareSolverr `master` vom 2026-09-27, v3.5.2; trawl `dev`, v1.8.0 vom 2026-10-08; Scavengarr `staging` d040933). Quellcode beider Repos als Tarball gelesen, Metadaten über die GitHub-API. Pfade ohne Präfix bei Scavengarr liegen unter `src/scavengarr/`.

## Zusammenfassung

FlareSolverr (Python, MIT) startet pro Request einen Chrome über Selenium mit einem eingefrorenen undetected-chromedriver, erkennt Cloudflares Challenge-Seite an Titel und Selektoren, trifft die Turnstile-Checkbox mit TAB und SPACE und gibt HTML, Cookies (`cf_clearance`) und User-Agent zurück; es ist gepflegt, aber mit 54 offenen Issues, ohne Captcha-Lösung und mit Status immer 200. trawl (TypeScript auf Bun, AGPL-3.0, drei Monate alt, ein Hauptautor) ist ein FlareSolverr-kompatibler Ersatz auf Camoufox-Firefox mit vierstufiger Eskalation (HTTP-Fetch, gecachte Session, frischer Solve, Residential-Proxy), Redis-Clearance-Cache, Turnstile-Klick über das Shadow-DOM, MITM-Proxy und nativ gebauten arm64-Images. Scavengarr hat beides bereits in eigener Form: Patchright-Chromium headful unter Xvfb mit Turnstile-Klick, einen Clearance-Store über Neustarts, die Übernahme der Browser-Session in httpx und einen verdrahteten, aber nie gegen einen laufenden Sidecar getesteten `/v1`-Solver-Fallback (Profil `solver` mit Byparr in `docker-compose.yml`). Empfehlung: kein neuer Pflichtdienst, sondern der vorhandene `SolverFetcher` optional gegen trawl (oder FlareSolverr) als Sidecar für die Fälle, in denen der eigene Browser auf dem Pi scheitert, und davor die fünf Lücken im eigenen Stack schließen (503-Challenge-Retries, `_safe_fetch` ohne Fallback, Bot-User-Agent in Domaincheck, Link-Validierung und Health-Prober, keine Challenge-Zählung in der Plugin-Historie, kein negativer Memo nach einem gescheiterten Solve). Offizielle APIs ersetzen den Umweg nur dort, wo das Repo sie schon nutzt (moflix, filmfans, serienfans, einschalten, nox, cine, DataApi); RSS-Feeds der Foren liefern keine Suche; `robots.txt` liest niemand, und ein moderates Rate-Limit gibt es bereits (AIMD 5 rps pro Domain, Retry-Backoff, Circuit Breaker).

## Teil 1: FlareSolverr

Repo: https://github.com/FlareSolverr/FlareSolverr. Pfade relativ zum Repo.

### Architektur und Weg eines Requests

- HTTP-Server Bottle 0.13.4 (`JSONErrorBottle` in `src/flaresolverr.py`: `index()` GET `/`, `health()` GET `/health`, `controller_v1()` POST `/v1`), serviert über waitress 3.0.2 (`WaitressServerPoll`, Z. 149-153), weil Bottles Default keine parallelen Requests kann. Bottle-Plugins in `src/bottle_plugins/`: Zugriffslog, Fehler zu `{"error": ...}` mit HTTP 500, Prometheus-Zähler je Domain.
- Service-Schicht `src/flaresolverr_service.py`: `controller_v1_endpoint` → `_controller_v1_handler` (Dispatch nach `cmd`) → `_cmd_request_get` / `_cmd_request_post` / `_cmd_sessions_*` → `_resolve_challenge` (Browser aus der Session oder neu, `func_timeout(maxTimeout)`) → `_evil_logic` (Erkennung, Warteschleife, Ergebnis) mit `click_verify`, `_resolve_turnstile_captcha`, `_get_turnstile_token`, `_post_request`.
- Browser-Erzeugung `src/utils.py:get_webdriver(proxy)`: `uc.Chrome(...)` aus der mitgelieferten Kopie `src/undetected_chromedriver/` (3.5.5, Fork von ultrafunkamsterdam) über Selenium 4.47.0; `start_xvfb_display()` per xvfbwrapper; `create_proxy_extension` für Proxy-Auth.
- Session-Store `src/sessions.py:SessionsStorage`: In-Memory-Dict `session_id → Session(driver, created_at)`, kein Lock (Issue #1752: Browser-Leak bei parallelem `sessions.create`), kein Reaper.
- Weg eines `POST /v1` mit `cmd: request.get`: `controller_v1()` liest das JSON und setzt den ENV-Proxy ein (Z. 52-68) → `controller_v1_endpoint` loggt den ganzen Body auf INFO (Z. 99, Issue #1753: Proxy-Passwort im Log) → `_controller_v1_handler` (`maxTimeout` Default 60000 ms) → `_cmd_request_get` → `_resolve_challenge`: Browser holen, `_evil_logic` in einem Thread unter `func_timeout`; Timeout wird zu "Error solving the challenge. Timeout after N seconds." → `_evil_logic`: optional Medien per CDP blocken, `driver.get(url)`, Access-Denied-Prüfung, Challenge-Erkennung, Warteschleife, Ergebnis → Antwort als `__dict__`; Browser ohne Session `quit()` im `finally`.

### Ansatz gegen die Challenge

- Chrome/Chromium über Selenium plus gepatchtem chromedriver: `Patcher.patch_exe` (`src/undetected_chromedriver/patcher.py` Z. 416-444) ersetzt den `cdc_`-Block, das klassische Selenium-Merkmal. Seit v3.0.0 (2023-01) statt Firefox/Puppeteer/Node.
- Headless-Default `HEADLESS=true`: auf Linux Xvfb plus `--headless=new` plus ein CDP-Skript, das `navigator.webdriver` auf `false` setzt (`__init__.py` Z. 399-410, 496-520). Der User-Agent wird einmal aus dem Browser gelesen, "Headless" entfernt und bei jedem Start per `--user-agent=` gesetzt (`src/utils.py` Z. 164-165, 329-331). Flags: `--no-sandbox`, `--window-size=1920,1080`, `--disable-dev-shm-usage`, `--no-zygote`, `--disable-gpu-sandbox` nur auf ARM, `--accept-lang=$LANG`, Proxy als `--proxy-server` oder MV3-Extension bei Auth.
- Erkennung in `_evil_logic` (Z. 399-428, Listen Z. 25-54): Access denied über `ACCESS_DENIED_TITLES` ("Access denied", "Attention Required! | Cloudflare") und `ACCESS_DENIED_SELECTORS`; Challenge über `CHALLENGE_TITLES` ("Just a moment...", "DDoS-Guard") und `CHALLENGE_SELECTORS` (`#cf-challenge-running`, `.ray_id`, `#cf-please-wait`, `#challenge-spinner`, `#turnstile-wrapper`, `.lds-ring`, `td.info #js_info`, `div.vc div.text-box h2`); `TURNSTILE_SELECTORS` = `input[name='cf-turnstile-response']`. HTTP-Status wird nicht geprüft (Selenium liefert ihn nicht).
- Warten (Z. 430-467): pro Versuch `BROWSER_WAIT_TIMEOUT` (1 s) mit `WebDriverWait.until_not(title_is)` und `until_not(presence_of_element_located)`; bei Timeout `click_verify` (ActionChains: `num_tabs` mal TAB, dann SPACE, zusätzlich Klick auf den Button "Verify you are human" per XPath) und erneut. Die Schleife ist unbegrenzt, allein `maxTimeout` beendet sie. Standalone-Turnstile seit v3.5.0 über `tabs_till_verify` (`_get_turnstile_token`: Endlosschleife, bis sich der hidden input ändert).

### Rückgabe und Sessions

- `solution`: `url`, `status` immer 200, `headers` immer `{}` (beide "todo: fix"), `response` = `page_source` (entfällt bei `returnOnlyCookies`), `cookies` im Selenium-Format mit `cf_clearance`, `userAgent`, optional `screenshot` und `turnstile_token`. Hülle: `status` ok/error, `message` ("Challenge solved!", "Challenge not detected!", "Error: ..."), Zeitstempel, `version`. README Z. 210: das Clearance-Cookie gilt nur zusammen mit dem FlareSolverr-User-Agent (und derselben IP).
- Sessions: `sessions.create` hält einen Browser offen (ID übergeben oder `uuid1`), `request.get` mit `session` und `session_ttl_minutes` erneuert per destroy plus create; `proxy` nur bei der Erstellung; kein Limit, kein Reaper, Sessions leben bis `destroy` oder Prozessende. `maxTimeout` deckt Navigation, Warteschleife und Turnstile ab, nicht den Browserstart.

### Grenzen und Wartungsstand

- Captchas: README Z. 337-345 "At this time none of the captcha solvers work"; im Code gibt es weder `CAPTCHA_SOLVER` noch `/captcha` (nur in `docker-compose.yml` und `flaresolverr.service`); hCaptcha nur als Beispielseite. Interaktives Turnstile nur über den Tastatur-Trick ohne Erfolgsprüfung. Access denied / IP-Ban → Fehler mit HTTP 500; ungelöste Challenge = Timeout (es gibt keine Meldung "Challenge not solved"). Keine echten Statuscodes und Header, POST nur urlencoded, keine Authentifizierung, Request-Body im Log.
- Wartung (GitHub-API 2026-10-09): 15 820 Stars, Releases v3.5.2 (2026-09-12), v3.5.0 (2026-05-26), v3.4.6 (2025-11-29); letzter Commit 2026-09-27 (SECURITY.md); 24 Commits seit 2026-01-01; Maintainer ngosang (241) und ilike2burnthing (119), danach Einzelbeiträge; 54 offene Issues, 44 offene PRs; die neuesten Meldungen (Juli bis September 2026: #1791, #1750 bis #1754, #1743, #1719) sind unbeantwortet. Der undetected-chromedriver ist als Kopie eingefroren und lokal angepasst.

### Ressourcenbedarf

- Keine Zahlen in README oder Code; README Z. 23-24: "Web browsers consume a lot of memory ... With each request a new browser is launched." Ohne Session ein Chrome-Start mit temporärem Profil pro Request, beim Prozessstart ein Test-Browser; die Histogram-Buckets 0/10/25/50 s (`src/metrics.py`) zeigen die erwartete Latenz. Keine Browser-Semaphore, nur die waitress-Threads.
- ARM: Images für linux/386, amd64, arm/v7, arm64/v8 (`.github/workflows/release-docker.yml`), Basis `python:3.11-slim-bookworm` mit Chromium aus Debian-apt (nicht gepinnt), `--disable-gpu-sandbox` auf ARM, `dumb-init`, Ports 8191 und 8192 (Prometheus). ENVs: `LOG_LEVEL`, `LOG_HTML`, `PROXY_URL`/`_USERNAME`/`_PASSWORD`, `LANG`, `HEADLESS`, `DISABLE_MEDIA`, `BROWSER_WAIT_TIMEOUT`, `PORT`, `HOST`, `PROMETHEUS_ENABLED`/`_PORT`. `TZ`, `TEST_URL` und `CAPTCHA_SOLVER` stehen im README, nicht im Code.
- Lizenz MIT (`LICENSE`); Python ≥ 3.9 laut Check, faktisch ≥ 3.10 (`str | None` in `src/dtos.py`); Abhängigkeiten bottle, waitress, selenium, func-timeout, prometheus-client, requests, xvfbwrapper.

## Teil 1: trawl

Repo: https://github.com/germondai/trawl (Doku https://docs.trawl.germondai.com). Pfade relativ zum Repo. Kein Fork; der Code nennt Byparr als Vorbild (`packages/tiers/src/utils/challengeWait.ts` Z. 1).

### Architektur und Weg eines Requests

- TypeScript auf Bun 1.4.2 mit Elysia; Monorepo `apps/api` (Server), `apps/web`, `apps/docs`, `packages/browser` (Pool, Fingerprints, Session-Cache), `packages/tiers` (Orchestrator, Tiers, Erkennung, Solver), `packages/types`. `apps/api/src/index.ts` → `createApiApp()` (`app.ts`), Port 8191; Routen `/` (FlareSolverr-artige Statusmeldung), `/health`, `/stats`, `/dashboard`, `POST /v1`, `POST /scrape`, `/sessions`, `GET /proxy-ca.crt`, optional `/mcp` (Tools scrape, read, extract, screenshot, inspect); MITM-Proxy auf 8192 (`proxy/server.ts`, eigene CA, Tier 0 Forwarding, Challenge-Cache 5 min pro Domain).
- `/v1` (`apps/api/src/routes/v1.ts`, `adapters/flaresolverr.ts`): JSON wie FlareSolverr v2 mit `request.get`, `request.post`, `sessions.create/list/destroy`; `maxTimeout` Default 60000; Antwort `{status, message, startTimestamp, endTimestamp, version:"2.0.0", solution:{url, status, headers:{}, response, cookies, userAgent}}`; Fehler `status:"error"`, HTTP 400 bei Validierung, 429 bei erschöpftem Pool, 503 während der Initialisierung.
- Orchestrator `packages/tiers/src/orchestrator.ts`: `scrape()` → `RequestBudget(maxTimeout)` → `scrapeWithinBudget()`: Tier 1 `tiers/1.ts` Buns `fetch()` mit Firefox-Headern (`success`, `needs-js`, `blocked`, `error`) → `BrowserPool.acquire` (`packages/browser/src/pool.ts`, bevorzugt einen Browser mit `lastDomain === domain`) → Tier 2 `tiers/2.ts` Replay der gecachten Session im geteilten Kontext → Tier 3 `tiers/browser.ts` frischer Kontext, `routeChallengeWait`, `solvePageCaptchas`, `persistentBrowserWall`, Erfolg speichert die Session → Tier 4 dasselbe mit Residential-Proxy (`RESIDENTIAL_PROXY_URL` oder `proxy` pro Request), sonst Fehler.

### Ansatz gegen die Challenge

- Camoufox, ein auf C++/Juggler-Ebene gepatchter Firefox 152.0.4-beta.30, über `camoufox-js` 0.12.1 und `playwright-core` 1.62.1 (`packages/browser/package.json`); kein Chromium, kein Selenium. Headless per Default (`pool.ts` Z. 276, "virtual" = Xvfb nur für den optionalen DataDome-Headful-Pool). Launch mit `os` pro Fingerprint, randomisiertem Screen, `geoip`, `humanize`, `block_webrtc`, uBlock Origin; einziges eigenes Init-Script patcht `attachShadow` (geschlossene Shadow Roots als `shadowRootUnl`). Standard-Image nur Linux-Fingerprints (`CAMOUFOX_KEEP_SPOOFED_OS_FONTS=0`).
- Erkennung `packages/tiers/src/utils/detect.ts:isCloudflarePage` (Z. 42-80): Header `cf-mitigated: challenge`, Titel-Regex "just a moment|please wait|checking|attention required", Texte, DOM-IDs `challenge-running`, `turnstile-wrapper`, `challenge-form`, Marker `_cf_chl_opt`, Blockseite `cf-error-details`, schlanker Stub mit `__CF$cv$params`; `isBlocked` wertet 403 und 429; `detectChallengeType` kennt zudem imperva, akamai, ddos-guard, aws-waf, datadome, hcaptcha, recaptcha, altcha u. a.
- Warten `utils/challengeWait.ts:waitForChallengeResolution`: Poll alle 300 ms über Titel, DOM, URL und Challenge-Frames; zwei inaktive Samples gelten als gelöst; `cf_clearance` für den Ziel-Host beobachtet, nach 5 s ohne Redirect eigene Navigation; `/cdn-cgi/error/`, 1020, 1015 → "ip-blocked". `attemptTurnstileClick` alle 3 s in vier Stufen: Checkbox im Shadow-DOM, Frame-Selektoren, Koordinaten-Klick auf das iframe, Tab und Space. Eingebettete Widgets über `solvers/turnstile.ts:solveTurnstile` (25 s). Timeouts: Budget 60 s, `goto` 30 s, Browser-Acquire 15 s → 429, Launch 90 s.
- Nach dem Warten prüft `persistentBrowserWall` das Dokument; Ergebnisse "cloudflare-persistent", "cloudflare-challenge-timeout", "datacenter-ip-blocked" eskalieren nur noch zu Tier 4.

### Rückgabe und Sessions

- `ScrapeResult` (`packages/types/src/index.ts`): `url`, `html`, `cookies[]` (alle Cookies des Kontexts, `cf_clearance` eingeschlossen), `userAgent` aus `navigator.userAgent`, `statusCode`, `tier`, `sessionCached`, `timings`, `body` als Rohbytes, `responseHeaders`, optional screenshot, consoleLogs, networkLogs, redirectChain. In `/v1` landen url, status, response, cookies, userAgent; `headers` bleibt leer.
- Clearance-Cache `session:{hostname}` mit `{cookies, userAgent, savedAt}` in Redis (`packages/browser/src/session.ts`, `REDIS_SESSION_TTL_SECONDS` 3600) oder prozesslokaler LRU (1000 Einträge); gespeichert nach Tier 3 und 4 ohne expliziten Proxy; Tier 2 replayt im geteilten Pool-Kontext mit dem UA nur als Header. Benannte Browser-Sessions (`browserSessions.ts`): eigener persistenter Kontext pro ID, Kapazität 4, Idle-TTL 3600 s, Proxy fix, nur im Prozess.

### Grenzen und Wartungsstand

- Steht der Cloudflare-Wall oder antwortet 1020/1015, hilft nur Tier 4 mit Residential-Proxy; ohne `RESIDENTIAL_PROXY_URL` endet der Request mit Fehler. hCaptcha-Bildraster, DataDome-Slider, Imperva-Captcha, DuckDuckGo- und AWS-WAF-Captcha ungelöst; reCAPTCHA v2 über Audio und Speech-to-Text; bezahlter Fallback `CAPTCHA_SOLVER=2captcha` "unverified" (CHANGELOG 1.8.0). Tier 1 sendet Firefox-Header mit Buns TLS-Signatur (Issue #213); Issue #212: Tier 1 fordert seit 1.8.0 zstd an und reicht es im MITM-Proxy roh an .NET-Clients wie Jackett und Prowlarr weiter. `/v1`: `headers` leer, importierte Cookies erzwingen einen frischen Kontext.
- Wartung: Repo seit 2026-06-26, v1.8.0 vom 2026-10-08, 25 Releases, 582 Commits, 962 Stars, 17 Contributors, aber 526 von 582 Commits von germondai (Bus-Faktor 1), 2 offene Issues, 0 offene PRs, tägliche Nightlies.

### Ressourcenbedarf

| Posten | Wert | Quelle |
|---|---|---|
| RAM je Camoufox-Instanz | 350 bis 500 MB unter Last | `apps/docs/architecture/browser-pool.md` Z. 98 |
| Pool 1 / 3 / 5 | ~400 MB / ~1,2 GB / ~2 GB; Host 1 / 2 / 3 GB empfohlen | `browser-pool.md` Z. 100-104 |
| Compose | `shm_size: 1gb` Pflicht, prod `mem_limit: 3g` | `docker-compose.yml`, `docker-compose.prod.yml` |
| Erststart | 15 bis 30 s, Healthcheck `start_period` 90 s | README, `apps/docs/getting-started/quick-start.md` Z. 38 |
| CPU | keine Zahlen; `:latest` braucht AVX2 auf x86, `:baseline` nicht | README "Docker images" |
| ARM | arm64 nativ gebaut (`publish.yml` auf ubuntu-24.04-arm), Camoufox `lin.arm64` mit SHA256-Pin, Fingerprint "Linux armv8" | `apps/api/Dockerfile`, `packages/browser/src/fingerprint.ts` |

Lizenz AGPL-3.0 (`LICENSE`). Als unveränderter, separat betriebener Dienst über HTTP berührt das Scavengarrs Code nicht; Änderungen am trawl-Code, die als Netzwerkdienst laufen, lösen die Quellpflicht für trawl aus.

## Teil 2: Vergleich

| | FlareSolverr 3.5.2 | trawl 1.8.0 |
|---|---|---|
| Ansatz | Chrome/Chromium über Selenium mit gepatchtem chromedriver, Xvfb plus `--headless=new`, ein Browser pro Request (oder Session), Turnstile per TAB+SPACE | Camoufox-Firefox (C++-Patches) über playwright-core, Browser-Pool, vier Tiers (fetch, gecachte Session, frischer Solve, Residential-Proxy), Turnstile-Klick über Shadow-DOM, Frame, Koordinaten, Tastatur |
| Sprache, Laufzeit | Python 3.11, Bottle/waitress | TypeScript, Bun 1.4.2, Elysia |
| API | `POST /v1` (request.get/post, sessions.*), `/health`, Prometheus auf 8192 | `POST /v1` FlareSolverr-kompatibel, natives `POST /scrape` (Tier, Timings, Status, Header, Bytes), `/sessions`, MCP, MITM-Proxy 8192, Dashboard |
| Rückgabe | HTML, Cookies, UA; Status immer 200, Header leer | HTML, Cookies, UA, in `/scrape` echter Status und Header; in `/v1` Header leer |
| Session-Wiederverwendung | benannte Browser-Sessions im Speicher, kein Reaper, TTL pro Request | Clearance-Cache pro Host in Redis oder LRU (TTL 1 h), benannte Browser-Sessions (4, Idle 1 h) |
| Performance | Browserstart pro Request (Sekunden), Buckets 0/10/25/50 s, keine Parallelitätsgrenze | Tier 1 ohne Browser, Tier 2 aus dem Cache; Pool begrenzt, 429 bei Erschöpfung; Erststart 15-30 s |
| Zuverlässigkeit | Warteschleife nur durch `maxTimeout` begrenzt, kein Captcha-Solver, WAF-Block = Fehler; Issues zu Gray Screen und Page Crash | Persistenter Wall nur mit Residential-Proxy, Tier-1-TLS-Signatur ist Buns, zstd-Problem mit .NET-Clients (offen) |
| Wartung | seit 2020, 15,8k Stars, zwei Maintainer, 54 offene Issues, neue Meldungen unbeantwortet, letzter Commit 2026-09-27 | seit 2026-06, 962 Stars, 25 Releases in drei Monaten, ein Hauptautor, 2 offene Issues |
| Ressourcen, ARM | keine Zahlen; Images 386/amd64/arm/v7/arm64 | 350-500 MB je Browser, `shm_size 1g`; arm64 nativ |
| Lizenz | MIT | AGPL-3.0 |

## Teil 3: Nutzen für Scavengarr

### Wo HTTP-Requests Cloudflare treffen können

Scavengarr hat einen eigenen Stack; die Stellen und ihr heutiger Schutz:

- **httpx-Plugins** (`infrastructure/plugins/httpx_base.py`): gemeinsamer Client mit SSRF-Guard, `RetryTransport` und `DomainRateLimiter` (`interfaces/composition.py:287-338`). Nur `_fetch_text()` erkennt Challenges (ab Status 400, `infrastructure/captcha/detect.py:24-66`: 403/503 plus "Just a moment", "challenge-platform", "cf-error-details", "Attention Required", "cf-turnstile") und fällt auf den Browser zurück (`httpx_base.py:418-435`); ein Host mit Challenge geht 30 min direkt in den Browser (`:47,96-99`); nach dem Lösen übernimmt httpx Cookies und UA des Browsers (`:479-509,590-604`), eine erneute Challenge binnen 5 min gilt als abgelehnte Session (`:50,459-466`). `_safe_fetch()` hat keine Erkennung und keinen Fallback (`:318-370`); jjs liegt laut Docstring hinter Cloudflare und nutzt nur `_safe_fetch` (`plugins/jjs.py:4,262,304`). Der Domaincheck `HEAD /` läuft mit dem Bot-User-Agent `Scavengarr/<version>` (`:231`, `infrastructure/version.py:26`).
- **Playwright-Plugins** (`infrastructure/plugins/playwright_base.py`): Patchright statt Playwright, ein geteiltes Chromium (`infrastructure/browser/shared_browser.py:66-98`), headful unter Xvfb im Prod-Image (`Dockerfile.prod:67,109`, `docker/entrypoint.sh:22-34`); `_wait_for_cloudflare` → `solve_cloudflare` mit 30 s (`:104,343-352`, `infrastructure/browser/turnstile.py:26-74`: Titel "just a moment", "attention required", "nur einen moment"; Checkbox im `challenges.cloudflare.com`-Frame nach 3 s, dann alle 8 s); Clearance-Cookies werden im `ClearanceStore` gespeichert und wiederhergestellt (`infrastructure/browser/clearance_store.py:27-60,103-124`, bis 24 h, im CachePort, überlebt Neustarts).
- **Stealth-Pool** (`infrastructure/browser/stealth_pool.py`): `fetch_text`, `capture_media`, `resolve_redirect`, `click_through` (s.to-Gate mit Turnstile und ALTCHA, `:516-588`), Seiten über `PageGate`/`PageBudget` (`page_gate.py:1-40`).
- **Hoster-Resolver** (`infrastructure/hoster_resolvers/`): den Stealth-Pool bekommen nur SuperVideo, DoodStream, Filemoon, Vixeo und die XFS-Resolver (`composition.py:569-617`); kein Resolver nutzt den Solver; die User-Agents sind uneinheitlich (Chrome 131 in firestream, playmate, mixdrop, veev, vinovo; abgeschnittener UA ohne Chrome-Token in filemoon, xfs, doodstream; Bot-UA in generic_ddl, rapidgator, ddownload, gofile, mediafire, streamtape).
- **Link-Validierung** (`infrastructure/validation/http_link_validator.py:159-215`, nur Torznab): HEAD/GET ohne eigene Header, keine Erkennung; ein Cloudflare-403 macht den Link 15 min ungültig.
- **HLS-Proxy** (`infrastructure/stremio/hls_proxy.py:161-167`): Chrome-UA plus gespeicherte Stream-Header; keine Challenge-Behandlung, CDN-Fehler werden 502.
- **Metadaten-Clients** auf dem gemeinsamen Client mit Bot-UA, ohne Fallback: Anime-Kitsu-Addon (hinter Cloudflare, nimmt den Scavengarr-UA an, `infrastructure/anime/kitsu_addon.py:7-9,26`), Fribb-Liste, Cinemeta (`infrastructure/stremio/cinemeta.py`), IMDb Suggest und Wikidata (`infrastructure/tmdb/imdb_fallback.py`), TMDB.
- **Sites mit Beleg**: Turnstile vor der Suche bei ddlspot, ddlvalley, scnsrc, mygully (Playwright); boerse JS-Challenge mit totem Origin; filmfans und serienfans `/external`-Links hinter Cloudflare; kinoger Cloudflare plus HostAdmin-WAF, in Produktion jede Seite durch den Browser und 13 von 13 Suchen im Timeout (`CHANGELOG.md:1709`); moflix-API-Challenge für VPN-IPs; byte; sto Turnstile/ALTCHA-Gate vor den Link-outs mit Quote; devideosrc-Player (hdfilme, streamcloud, streamkiste) hinter dem Cloudflare-Cache; kinox, megakino_to und movie4k Cloudflare 522 (tote Origins); animeloads und cine DDoS-Guard. Health-Probes zählten 91 Challenges in 48 h, alle kinoger (`docs/plans/plugin-domains.md:95-97`).
- **Vorhandener Solver**: Profil `solver` in `docker-compose.yml:80-84` mit `ghcr.io/thephaseless/byparr:latest`, aktiviert über `SCAVENGARR_PLAYWRIGHT_SOLVER_URL` (`:44-45`); `SolverFetcher` (`infrastructure/browser/solver_fetcher.py:49-80`) sendet `POST {solver_url}/v1` mit `cmd: request.get`, `url`, `maxTimeout`, nimmt `solution.response`, `cookies`, `userAgent`, merkt Cookies und UA pro Host im Speicher (`:82-100`); `ChainedBrowserFetcher` fragt erst den eigenen Stealth-Pool, dann den Solver (`composition.py:245-270`), nur für httpx-Plugins (`:555-564`); `click_through` wird vom Solver nicht unterstützt. Status laut Doku: "not verified against a running sidecar" (`docs/plans/antibot-patchright.md:5,194-200`, `docs/plans/captcha-solving.md:5`); ein Sidecar ändert die Reputation der Exit-Adresse nicht und kostet dieselbe Chromium-CPU (`docs/plans/optimization-options.md:59,86`); `cf_clearance` ist an IP und UA gebunden (`antibot-patchright.md:226-229`).

### Empfehlung

1. **Kein neuer Pflichtdienst, keine Bibliothek.** Was FlareSolverr und trawl tun, tut der eigene Stack schon im Prozess: Patchright-Chromium mit Turnstile-Klick, Clearance-Store über Neustarts, Session-Übernahme in httpx, Stealth-Pool für Resolver und das s.to-Gate. Eine eingebettete Bibliothek brächte einen zweiten Browser in denselben Prozess; die Playwright-Plugins und die Resolver könnten keinen Sidecar nutzen, weil sie die Seite selbst brauchen (Klicks, Capture).
2. **Der Sidecar bleibt der optionale Fallback, der er schon ist, mit trawl als bestem Kandidaten.** Die `/v1`-Schnittstelle ist bei beiden gleich, der `SolverFetcher` braucht keinen neuen Code für den Wechsel. trawl passt besser als FlareSolverr: eine zweite Engine (Firefox statt Chromium) hilft genau dann, wenn Cloudflare Chromium-Automatisierung markiert; der Clearance-Cache liegt in Redis, das Scavengarr schon hat; arm64 wird nativ gebaut; Tier 1 und 2 antworten ohne Browser, wenn eine Session besteht. Gegen trawl sprechen AGPL (für den unveränderten Betrieb ohne Folge), ein Hauptautor und 400 MB plus `shm 1g` auf dem Pi. FlareSolverr ist die konservative Wahl (MIT, älter, breiter eingesetzt), startet aber pro Request einen Chrome und liefert weder Status noch Header. Byparr, der heutige Profil-Dienst, ist das Vorbild von trawl und wurde hier nie gegen einen laufenden Sidecar getestet.
3. **Zuerst die Lücken im eigenen Stack schließen; sie kosten keinen Dienst:** (a) `RetryTransport` wiederholt eine 503-Challenge dreimal und halbiert dabei die Domain-Rate (`infrastructure/common/retry_transport.py:86-91`): eine Antwort mit `cf-mitigated: challenge` oder einer Challenge-Seite nicht wiederholen; (b) `_safe_fetch()` die Erkennung und den Browser-Memo von `_fetch_text()` geben (`httpx_base.py:318-370`), womit jjs und die `_safe_fetch`-Plugins den Fallback bekommen; (c) Domaincheck, Health-Prober (`infrastructure/scoring/health_prober.py:21-22`) und Link-Validierung mit dem Plugin-UA statt des Bot-UA senden und die Resolver-UAs auf `DEFAULT_USER_AGENT` vereinheitlichen (`infrastructure/plugins/constants.py:13-17`); (d) Challenges und Solver-Ausgänge in `PluginHistory` zählen (`domain/ports/plugin_history.py:15-17`) und im Digest zeigen, damit die Entscheidung über einen Sidecar auf Zahlen steht statt auf 91 von Hand gezählten Logzeilen; (e) der `SolverFetcher` speichert seine Sessions im `ClearanceStore` statt im Speicher und bekommt einen negativen Memo nach "blocked", damit ein gescheiterter Solve den Sidecar nicht alle 30 s neu beschäftigt. Der Widerspruch zwischen Scoring (Challenge = Health 0, `infrastructure/scoring/ewma.py:87-91`) und Health-Monitor (Challenge = Site lebt, `infrastructure/plugins/health_monitor.py:42-49`) gehört dazu.
4. **Offizielle APIs und RSS.** Wo eine Site eine API hat, nutzt das Repo sie bereits: DataApi `/data/browse` und `/data/watch` (megakino_to, movie4k), moflix `/api/v1/search`, filmfans und serienfans `/api/v2/search`, serienjunkies `/api/media/<id>/releases`, einschalten `/api/search`, nox `/api/search` mit ALTCHA, cine `/request/*`, fireani-RPC, aniworld `/ajax/search`, WordPress-REST bei haschcon und hdworld, devideosrc `/api/embed-links`. Die vBulletin- und XenForo-Foren (boerse, mygully, myboerse, dataload) bieten RSS für neue Threads (`external.php?type=RSS2`, `index.rss`), aber keine Suche, und bei boerse ist der Origin tot; ein Feed ersetzt die Suchanfrage nicht. Die Cloudflare-geschützten Streaming-Sites haben keine offizielle API.

### Integration konkret

Dateien, die sich ändern (bei der Entscheidung für den Sidecar):

- Compose des Pi (die Datei enthält Secrets und steht nicht hier) und `docker-compose.yml` im Repo: der Dienst neben Scavengarr, Scavengarr bekommt `SCAVENGARR_PLAYWRIGHT_SOLVER_URL`.
- `infrastructure/browser/solver_fetcher.py`: Sessions über den `ClearanceStore`, `session` pro Host, echter Status von trawl, negativer Memo, Ziel-Host im Rate-Limiter, eine Telemetrie-Stage `solver` mit den Ausgängen `solved`, `blocked`, `busy`, `unreachable`.
- `infrastructure/common/retry_transport.py`, `infrastructure/plugins/httpx_base.py` (`_safe_fetch`, Domaincheck-Header), `infrastructure/scoring/health_prober.py`, `domain/ports/plugin_history.py` mit `infrastructure/plugins/plugin_history.py` und `scripts/digest.py`: die Lücken (a) bis (e).
- `docs/features/configuration.md` (Solver-Abschnitt), `docs/plans/antibot-patchright.md` (Phase 3 verifiziert), `CHANGELOG.md`.

Compose-Skizze (Variablennamen aus trawls `.env.example` und `docker-compose.yml`):

```yaml
  trawl:
    image: ghcr.io/germondai/trawl:latest   # arm64 nativ; :baseline für CPUs ohne AVX2 oder ältere Kernel
    shm_size: 1gb
    mem_limit: 1g
    environment:
      BROWSER_POOL_SIZE: "1"
      REDIS_URL: redis://redis:6379         # Clearance-Cache überlebt Neustarts; weglassen = LRU im Speicher
      REDIS_SESSION_TTL_SECONDS: "3600"
      LOG_LEVEL: info
    profiles: [solver]
  scavengarr:
    environment:
      SCAVENGARR_PLAYWRIGHT_SOLVER_URL: http://trawl:8191
      SCAVENGARR_PLAYWRIGHT_BROWSER_FALLBACK: "true"   # eigener Browser zuerst; "false" nur für einen Vergleichslauf
```

Aufruf, Session-Wiederverwendung, Fehlerbehandlung und Fallback als Skizze für `SolverFetcher` (die vorhandene Kette bleibt: eigener Stealth-Pool, dann Solver, dann leer):

```python
async def fetch_text(self, url: str, *, budget_ms: int) -> SolverPage | None:
    host = urlsplit(url).hostname or ""
    if self._blocked_until.get(host, 0.0) > time.monotonic():   # negativer Memo nach "blocked"
        return None
    async with self._slots:                                     # Semaphore 1 bis 2: der Sidecar hat einen Browser
        await self._limiter.acquire(host)                       # das Rate-Limit des Ziel-Hosts, nicht des Solvers
        body = {"cmd": "request.get", "url": url, "maxTimeout": budget_ms,
                "session": host, "session_ttl_minutes": 60}     # Browser-Session pro Host, bei beiden Diensten
        try:
            resp = await self._client.post(f"{self._url}/v1", json=body, timeout=budget_ms / 1000 + 10)
        except httpx.HTTPError as exc:
            log.warning("solver_unreachable", host=host, error=type(exc).__name__)
            return None                                         # das Plugin meldet leer, die Historie zählt es
    if resp.status_code == 429:                                 # trawl: Pool erschöpft; kein Retry im Request
        log.info("solver_busy", host=host)
        return None
    data = resp.json()
    if data.get("status") != "ok":
        reason = str(data.get("message", ""))[:80]
        if any(w in reason for w in ("blocked", "persistent", "banned")):
            self._blocked_until[host] = time.monotonic() + 1800  # 30 min wie der Browser-Memo
        log.warning("solver_failed", host=host, reason=reason)
        return None
    sol = data["solution"]
    if int(sol.get("status", 200)) >= 400:                      # trawl liefert echte Status, FlareSolverr immer 200
        return None
    await self._clearance.save(host, sol.get("cookies", []), sol.get("userAgent"))   # ClearanceStore statt dict
    return SolverPage(sol["response"], sol.get("cookies", []), sol.get("userAgent"))
```

`HttpxPluginBase._adopt_browser_session` übernimmt danach wie heute Cookies und UA in den Cookie-Jar des gemeinsamen Clients (`httpx_base.py:498-509`); die Clearance gilt nur mit dem UA des Sidecars und von derselben Adresse, also muss der Sidecar denselben Egress haben wie Scavengarr.

Rahmen: ein moderates Rate-Limit existiert (AIMD 5 rps pro Domain mit Burst 10, Halbierung bei 429/503, `infrastructure/common/rate_limiter.py:1-25,88-93`; Retry-Backoff 1/2/4 s mit `Retry-After`; Circuit Breaker 5 Fehler, 60 s bis 1 h; Plugin-Semaphoren 5), nur `record_timeout` ruft niemand auf (`rate_limiter.py:98-105`). `robots.txt` wird nirgends gelesen; die geschützten Sites verbieten automatisierten Zugriff in der Regel vollständig, ein Honorieren hieße, diese Plugins abzuschalten, was die Entscheidung des Betreibers ist. Die Nutzungsbedingungen der Hoster und Sites untersagen automatisierte Abrufe meist ebenso; der Betrieb bleibt auf Inhalte beschränkt, die der Betreiber abrufen darf.

### Trade-offs

- **Latenz:** der eigene Browser braucht auf dem Pi 16 bis 70 s für Turnstile (`docs/plans/stremio-latency.md:138`); ein Sidecar kommt nach dem eigenen Versuch, also obendrauf, es sei denn `browser_fallback=false`. trawl antwortet mit gecachter Session ohne Browser (Tier 1 und 2), FlareSolverr startet pro Request einen Chrome. Das Stremio-Budget pro Request bleibt die Grenze; ein Solve, der es überschreitet, nützt erst der nächsten Anfrage.
- **Ressourcen:** trawl 350 bis 500 MB je Browser plus `shm 1g`, FlareSolverr ein Chrome pro Request; beide verbrennen dieselbe CPU des Pi wie Patchright (`optimization-options.md:59,86`). Mit einem Pool von 1 kostet trawl etwa so viel wie ein zweites Scavengarr-Chromium.
- **Stabilität bei Cloudflare-Änderungen:** alle drei Stacks brechen mit derselben Erkennungsänderung; FlareSolverr reagiert langsam (zwei Maintainer, neue Issues unbeantwortet), trawl schnell, aber mit einem Autor; der eigene Stack hängt an Patchright. Eine zweite Engine (Firefox) ist die einzige echte Diversifikation.
- **Adresse und Reputation:** keiner der Dienste ändert, dass die Exit-Adresse (VPN) die Challenges auslöst; `cf_clearance` ist an Adresse und UA gebunden, der Sidecar muss über denselben Egress gehen. Ein Residential-Proxy (trawls Tier 4) wäre ein kostenpflichtiger Weg, der hier nicht empfohlen wird.
- **Sicherheit:** beide Dienste ohne Authentifizierung, nur im Compose-Netz erreichbar machen; FlareSolverr loggt Request-Bodies.

## Quellen

- FlareSolverr: `src/flaresolverr.py`, `src/flaresolverr_service.py`, `src/sessions.py`, `src/dtos.py`, `src/utils.py`, `src/metrics.py`, `src/undetected_chromedriver/{__init__,patcher}.py`, `Dockerfile`, `docker-compose.yml`, `requirements.txt`, `README.md`, `CHANGELOG.md`, `LICENSE`, `.github/workflows/release-docker.yml`; Issues #128, #166, #737, #1599, #1719, #1743, #1750 bis #1754, #1791; https://github.com/FlareSolverr/FlareSolverr
- trawl: `README.md`, `CHANGELOG.md`, `LICENSE`, `.env.example`, `docker-compose.yml`, `apps/api/Dockerfile`, `.github/workflows/publish*.yml`, `apps/api/src/{index,app,deps}.ts`, `apps/api/src/routes/{v1,scrape,sessions,mcp}.ts`, `apps/api/src/adapters/flaresolverr.ts`, `apps/api/src/proxy/server.ts`, `packages/browser/src/{pool,fingerprint,session,memory-session}.ts`, `packages/tiers/src/orchestrator.ts`, `packages/tiers/src/tiers/{1,2,3,4,browser}.ts`, `packages/tiers/src/utils/{detect,challengeWait,challengeRouter,browserWall}.ts`, `packages/tiers/src/solvers/{turnstile,hcaptcha,recaptcha,index}.ts`, `packages/types/src/index.ts`, `apps/docs/architecture/{tiered-execution,browser-pool}.md`, `apps/docs/deployment/{docker-compose,standalone,troubleshooting}.md`; Issues #212, #213; https://github.com/germondai/trawl
- Scavengarr: die im Text genannten Pfade unter `src/scavengarr/`, `plugins/`, `docker-compose.yml`, `Dockerfile.prod`, `docker/entrypoint.sh`, `docs/plans/antibot-patchright.md`, `docs/plans/captcha-solving.md`, `docs/plans/optimization-options.md`, `docs/plans/plugin-domains.md`, `docs/plans/stremio-latency.md`, `docs/features/configuration.md`, `docs/features/python-plugins.md`, `docs/features/hoster-resolvers.md`, `CHANGELOG.md` (KNOWN_ISSUES)
