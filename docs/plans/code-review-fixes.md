[← Back to Index](../features/README.md)

# Plan: Fixes from the Full Code Review of `staging`

**Status:** In progress (started 2026-09-29)
**Priority:** High (security and "does not start" findings in wave 1)
**Source:** Full read-only review of the current `staging` code by six reviewers (domain/application/interfaces, hoster resolvers, infrastructure core, plugin base + plugins a–k, plugins l–z, deployment/tooling), 79 findings. The most severe ones were re-checked against the code before this plan.

Every fix is test-first (failing test → fix → green), one commit per fix or per tightly related group, `pre-commit` + full `pytest` before each commit, docs in the same commit.

## Wave 1 — Security and "does not start"

| # | Finding | Fix | Test |
|---|---|---|---|
| 1.1 | **SSRF / open proxy**: `/api/v1/stremio/proxy/{id}/{path}` joins the client path with `urljoin`; an absolute or `//host` path replaces the CDN host (`hls_proxy.build_cdn_url`). | `build_cdn_url` rejects results whose scheme/host differ from the CDN base (`ValueError`); the router answers 400. | Unit: absolute, protocol-relative and relative paths; router 400. |
| 1.2 | **Connection leak**: `stream_hls_segment` raises on 4xx/5xx without closing the streamed response; ~100 CDN errors exhaust the shared pool. | Close the response before re-raising. | Unit: 403 segment → response closed. |
| 1.3 | `docker/entrypoint.sh` is mode 100644 in git → container exits 126. | Commit the executable bit; `chmod +x` in `Dockerfile.prod` as well. | `git ls-files -s` mode check test. |
| 1.4 | Xvfb lock survives `docker compose restart` → no display, all browser plugins fail. | Remove `/tmp/.X99-lock` and the socket before starting Xvfb. | `bash -n`; reviewed. |
| 1.5 | `.claude/hooks/*.sh`, `.devcontainer/*.sh` are mode 100644 → in a fresh clone the guard hook exits 126 and allows everything. | Commit the executable bits. | Same mode check test. |
| 1.6 | `data/config.yaml` sets `cache.dir: /data/cache` → a local `poetry run start --config data/config.yaml` (as documented) fails with PermissionError. | Relative default (`./.cache/scavengarr`); Docker keeps its env override. | Config test: YAML cache dir is relative. |
| 1.7 | Dev container fails on a fresh clone: `--env-file .env.devcontainer` and `cp .continue/config.yaml` need gitignored files. | Create an empty env file in `initializeCommand` if missing; guard the `cp`. | Reviewed. |
| 1.8 | Root `Dockerfile` has an invalid `CMD` and is picked by a plain `docker build .`; no `.dockerignore` (the legacy builder uploads `.venv`, `.devdata`, `.env.devcontainer` with `GH_TOKEN`); no `init: true` (zombie Chromium processes). | Delete the unused root `Dockerfile` (dev container builds `.devcontainer/Dockerfile`); add `.dockerignore`; `init: true` in compose. | Reviewed; YAML parse. |
| 1.9 | ruff 0.15 in pre-commit vs 0.14 in the dev dependencies (edit hook) → formatters can fight. | Align the dev dependency with pre-commit. | `pre-commit` run. |

## Wave 2 — Wrong or missing results

| # | Finding | Fix |
|---|---|---|
| 2.1 | **aniworld** builds episode URLs without `/stream/` → every series/anime episode request finds nothing (0 results in all Stremio measurements). | Use the detail URL as base. Also: season without episode picks the first episode of *that* season (aniworld, fireani). |
| 2.2 | **kinox** ignores season/episode (mirror request has none) → wrong episode's streams. | Series entries are skipped for episode requests unless the requested episode can be addressed; see implementation note in the commit. |
| 2.3 | **megakino** requests page 0 and 1 (same page in DLE) → duplicates; episode filter ignores the season and falls back to all links. | Pages from 1; season-aware episode filter, no fallback to all episodes. |
| 2.4 | Resolvers treat `"error"` anywhere in the final URL as an error redirect (7 copies) → "The.Terror.S01E01" is dead. | One shared helper checking path segments only. |
| 2.5 | DDL file-ID regexes (nitroflare, uploaded, 1fichier) anchored with `$` → links with file names/extra params rejected. | Allow a trailing path/params. |
| 2.6 | alphaddl/1fichier offline markers `"404"` / `"not found"` match live pages. | Specific markers only. |
| 2.7 | `/download/{job}` returns 500 for titles with characters outside Latin-1 (header encoding). | ASCII header values, RFC 6266 `filename*`. |
| 2.8 | `.crawljob` values are written without stripping CR/LF → scraped descriptions can inject JDownloader keys. | Collapse line breaks in every value. |
| 2.9 | Episode filter drops multi-episode/multi-season releases (guessit lists). | Membership test. |
| 2.10 | `StreamSorter.sort` drops `RankedStream.title`. | `dataclasses.replace`. |
| 2.11 | XFS `/dl` form POST and embed URL use the original host, not the host that served the page → POST redirected to GET, extraction fails. | Use `resp.url`. |
| 2.12 | `unpack_p_a_c_k` returns base-62 packed code unchanged. | Base-62 decoding. |
| 2.13 | **moflix** returns its own title page when a title has no videos; ignores season/episode. | No result without videos; filter videos by season/episode. |
| 2.14 | **serienfans** episode results titled `"{title} - E{n}"` → Sonarr cannot parse. | `S{ss}E{ee}` titles. |
| 2.15 | **movie4k** (copy of megakino_to) has no season/episode filter, keeps deleted streams, crashes on list-shaped tmdb data. | Share megakino_to's stream collection and metadata helpers. |
| 2.16 | **sto** scrapes only the first season without a season; series and hoster redirects are processed sequentially. | All seasons (bounded by the result limit); bounded parallelism. |
| 2.17 | **cine**: `resp.json()` outside try + gather without `return_exceptions` → one bad answer aborts the search. | Base helpers, `return_exceptions`. |
| 2.18 | **dataload** never re-logs in after the session/CSRF token expires. | Detect login/CSRF failure, log in again. |
| 2.19 | Queries not URL-encoded (animeloads, cineby, ddlvalley, filmpalast_to, scnsrc). | `quote_plus` / `params`. |
| 2.20 | **filmpalast_to** reads only the first search page. | Follow next-page links. |
| 2.21 | **gofile** guest token never invalidated on 401/403. | Clear and retry once. |
| 2.22 | **burningseries** declares streams but only returns bs.to page URLs (no resolver) → up to 50 useless detail fetches per Stremio request. | Decided in implementation after reading the site code path; documented in the commit. |

Not changed (measured or not verifiable): `check_playable`'s 6 s timeout vs vinovo (measured 2026-09-29: vinovo 4/4 playable with the check on); myboerse next-page detection (reviewer guess from XenForo conventions, site not checked).

## Wave 3 — Robustness and operations

| # | Finding | Fix |
|---|---|---|
| 3.1 | Standard-library exception logs are lost: `QueueHandler.prepare` deep-copies records with tracebacks. | Render `exc_info` to text, shallow copy. |
| 3.2 | Plugin registry re-executes all plugin files on every `list_names()` (healthz!) on the event loop; one broken plugin file makes `get_by_provides()` raise for every Stremio request. | Cache peeked names; skip and log failing plugins. |
| 3.3 | Concurrency pool fair share not enforced (slot reserved only after acquiring). | Reserve inside the condition, release on cancel. |
| 3.4 | Secrets in logs: TMDB `api_key` in retry/error URLs; full query strings (Torznab `apikey`) in request logs. | Redact sensitive query parameters in logged URLs. |
| 3.5 | Resolvers hard-code 15 s per request and `resolve()` has no overall bound (`/play` can hang > 50 s); `http.timeout_resolve_seconds` is ignored there. | Bound `resolver.resolve()` by the configured resolve timeout; timeouts are not cached as dead. |
| 3.6 | ALTCHA solving has no bound on site-supplied `cost`/`keyPrefix` → threads pinned for hours. | Cap parameters and wall-clock time. |
| 3.7 | CrawlJob save errors swallowed (`gather(return_exceptions=True)` ignored) → grab 404; one failed stream-link save discards the whole Stremio answer. | Log and drop only the affected items/streams. |
| 3.8 | CrawlJob TTL hard-coded to 1 h (AGENTS.md: configurable). | `crawljob_ttl_seconds` config. |
| 3.9 | API rate limit (120 rpm/IP) also counts HLS segment requests → playback can stall. | Exempt `/stremio/proxy/` and health endpoints. |
| 3.10 | Validator caches grow forever; GET fallback downloads full bodies. | Prune expired entries; streamed GET without reading the body. |
| 3.11 | HLS manifest cache grows forever. | Prune on insert, size cap. |
| 3.12 | Circuit breaker half-open lets every call through. | One probe in flight per plugin. |
| 3.13 | Score index TTL not refreshed on updates; read-modify-write race. | Always re-save under a lock. |
| 3.14 | Redis backend ignores `cache.max_concurrent`. | Pass it through. |
| 3.15 | `_resolve_own_links` / `_resolve_result_links` gather unbounded. | Plugin semaphore. |
| 3.16 | kinox mirror requests bypass the shared semaphore. | One semaphore. |
| 3.17 | Playwright driver leaked when an owned browser disconnects. | Stop the old driver. |
| 3.18 | moflix overrides the Cloudflare wait with a sleep loop and `except: pass`, bypassing the Turnstile solver. | Use the base implementation plus a condition wait. |
| 3.19 | Scraped URLs are fetched with redirects to private/loopback addresses allowed (blind SSRF into the LAN). | Reject private/loopback/link-local targets for resolver and validator requests. |

## Wave 4 — Plugin categories and remaining low findings

| # | Finding | Fix |
|---|---|---|
| 4.1 | Category handling wrong or missing (mandatory per AGENTS.md): nima4k (no filter, requested category forced onto results, anime→documentaries), movie4k (quality subcategories mapped to genres), mygully (always 2000), myboerse (unmapped subcategories search everything; 5020→software), scnlog/scnsrc (unmapped subcategories unfiltered, results labelled with the request), warezomen (none), boerse, ddlvalley, crawli, byte, dataload, filmpalast_to, ddlspot (games dropped), hdsource, jjs, kinoking, serienjunkies, DLE plugins (unknown categories return everything). | Map by parent category, label results from site data, filter with `_category_matches`, return `[]` for categories a site does not serve. |
| 4.2 | Animated/anime *films* labelled 5070 (TV) and dropped from movie searches (kinoger, megakino, movie2k, streamcloud, streamkiste); hdfilme maps documentaries to 5080 and horror/animation to 2040. | Genre overrides only for series; films stay in 2000. |
| 4.3 | Copy-pasted DLE logic (megakino, streamcloud, streamkiste, movie2k) has diverged. | Shared helper module. |
| 4.4 | Direct `client.get/post` bypassing base helpers (timeouts, UA, Cloudflare fallback) in burningseries, cine, hdfilme, kinox, dataload, megakino, movie2k, sto, myboerse. | Route through `_safe_fetch` / `_fetch_text`. |
| 4.5 | ddlvalley ignores `_MAX_PAGES`; ddlspot/ddlvalley lose collected rows on a page timeout; burningseries crashes on odd year text. | Enforce limits, catch per page, guard the regex. |
| 4.6 | `moflix-stream` claimed by Vidguard and Vidhide (winner by registration order). | Remove it from Vidguard (JDownloader lists VidguardTo as offline); registry warns on duplicate domains. |
| 4.7 | `probe.py` unreachable in production (resolver always configured), marker list drifted. | Remove the dead path. |

### 4.4 follow-up: the XenForo forums (dataload, myboerse)

Checked live on 2026-09-29 (guest search with the page's CSRF token, `/login/` page, forum index):

- **myboerse search always failed**: its POST lacked the session's `_xfToken`, which myboerse answers with HTTP 400 ("Sicherheitsfehler aufgetreten"). dataload had that fix; myboerse, a copy of the same code, had none of the dataload fixes (CSRF token, re-login after session expiry, `xf_user` cookie of the own host, `?page=N` pagination via `pageNav-jump--next`).
- **Category filtering was a no-op on both**: XenForo applies `c[nodes][]` only to post searches (`search_type=post`). Without it a movie search also returned tutorials, music and audiobooks.
- **Wrong or missing nodes**: software labelled 5020 (TV/Foreign), dataload's console forums under PC games, myboerse's Android/iOS games under consoles; subforums missing from the maps (dataload's foreign-film subforums 162–167; myboerse's foreign comics, English audiobooks, Cydia apps, XXX section). Unmapped forums were labelled by a forum-name guess (XXX films as 2000).
- **myboerse `/xtra/` links are an affiliate placeholder**: `/xtra/?x=<anything>` redirects to the same Rapidgator file `www.MyBoerse.bz_Premium_Download.txt`, and the plugin returned it as a download link.

Design: `XenForoPluginBase` (`infrastructure/plugins/xenforo.py`, subclass of `HttpxPluginBase`, same pattern as `DataApiPluginBase`) holds login (CSRF token, own-host session cookie), search (`search_type=post`, `_xfToken`, one re-login), pagination, thread scraping and the parsers. The plugins set `name`, `_domains` and `_node_categories` (forum node → Torznab category for every download forum of the live forum index). A category request searches the nodes whose category it covers (parent → children); a child category the forum does not tell apart (2040 where all films are 2000) uses its parent's nodes; a category without a section returns `[]` without a request. Results are labelled by their forum node (unknown node: 8000). Requests use `_safe_fetch()` without the browser fallback, because the session lives in the HTTP client's cookie jar. Only link-container hosts count as download links.

Tests: `tests/unit/infrastructure/test_xenforo_base.py` (parsers, node selection, login, search flow) against a minimal subclass; the plugin tests keep attributes, domains and the node maps.

## Documentation

`CHANGELOG.md`, the affected `docs/features/*.md`, `docs/architecture/*` where behaviour changes, `AGENTS.md` if rules change; results and deviations recorded here.
