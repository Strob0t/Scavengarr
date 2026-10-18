# Plugin Domains: megakino_to and movie4k (step 29)

**Question.** Step 21's audit after the filter change (`docs/plans/series-episodes.md` → After) found megakino_to and movie4k unreachable: read timeouts on every domain, 13 and 14 of 14 searches ending in `PluginUnreachableError`. Both front one `DataApiPluginBase` backend. Before anyone hunts new domains the maintainer needs to know which it is: dead sites, a VPN exit the sites block, or a slow backend.

**Probe.** `scripts/probes/domains.py [--plugins a,b] [--get-timeout S] [--history]` checks every `_domains` entry of the named plugins (default: every stream plugin) from where it runs: DNS resolution, a TCP connect to port 443, the domain check's `HEAD https://<domain>/` (the plugin's own client and headers, its 5 s timeout), then a `GET` of the same page (15 s unless `--get-timeout`), each step with its time. A domain *answers* by the domain check's rule (`_site_answers` of `httpx_base.py`, reused, not copied): below 400, an error page, or a challenge page. With `--history` it prints the plugins' long-term record from the app on the same host (`GET /api/v1/stats/plugins`; on a build before that endpoint it reads the `plugin_history:v1` snapshot from the cache backend). Hosts only, no addresses, paths or tokens; it changes nothing.

```bash
PYTHONPATH=src poetry run python -P scripts/probes/domains.py --plugins megakino_to,movie4k --get-timeout 60   # dev container
.venv/bin/python scripts/prodctl.py probe --timeout 600 domains -- --plugins megakino_to,movie4k --get-timeout 60 --history   # the production host
```

## Reading (2026-10-07)

Dev container = a residential connection; production = the VPN exit (production, `staging` before step 21). Times are one run each.

| Plugin | Domain | Where | DNS | TCP 443 | HEAD (5 s) | GET (60 s) | Answers |
|---|---|---|---|---|---|---|---|
| megakino_to | megakino.org | dev | 2 v4 / 2 v6 | ok, 23 ms | ReadTimeout | **522** `[cloudflare]` after 19.5 s | no |
| | | production | 2 v4 / 2 v6 | ok, 37 ms | ReadTimeout | **522** `[cloudflare]` after 19.6 s | no |
| megakino_to | megakino.to | dev | 2 v4 / 2 v6 | ok, 24 ms | ReadTimeout | **522** `[cloudflare]` after 19.5 s | no |
| | | production | 2 v4 / 2 v6 | ok, 41 ms | ReadTimeout | **522** `[cloudflare]` after 19.6 s | no |
| movie4k | movie4k.sx | dev | 2 v4 / 2 v6 | ok, 22 ms | ReadTimeout | **522** `[cloudflare]` after 19.5 s | no |
| | | production | 2 v4 / 2 v6 | ok, 29 ms | ReadTimeout | **522** `[cloudflare]` after 19.4 s | no |
| movie4k | movie4k.ag | dev | 1 v4 | **TimeoutError** 5 s | ConnectTimeout | ConnectTimeout (60 s) | no |
| | | production | 1 v4 | **TimeoutError** 5 s | ConnectTimeout | ConnectTimeout (60 s) | no |
| movie4k | movie4k.stream | dev | 2 v4 / 2 v6 | ok, 26 ms | ReadTimeout | **522** `[cloudflare]` after 19.6 s | no |
| | | production | 2 v4 / 2 v6 | ok, 36 ms | ReadTimeout | **522** `[cloudflare]` after 19.7 s | no |

With the 15 s default both `HEAD` and `GET` end in `ReadTimeout` on every domain (the audit's picture); the 60 s run shows what Cloudflare answers after about 19.5 s: `522 Connection timed out`, the edge's own error for an origin it cannot reach. The `answers` column stays "no" on the production host too: the production build predates `_site_answers`, so the probe falls back to the plain rule there (`? (no rule)` for the 522 rows in the raw output, `no` for the connect failures), and 522 is above 400 and no challenge under either rule.

**History record.** Production answers `GET /api/v1/stats/plugins` with 404 and holds no `plugin_history:v1` snapshot: the build predates step 16's `PluginHistory`. The record of the day comes from the container's log instead (`prodctl.py logs --since 72h`, the container was up 4 h): at 07:11 UTC `megakino_to_no_domain_reachable` and `movie4k_no_domain_reachable`, two `megakino_to_timeout`, one `movie4k_timeout` (a search that had started before the domain check failed), then `plugin_unreachable` for both at 07:12 and `stremio_plugins_unreachable` on the following requests: the health monitor keeps them out of the searches. No result from either plugin in the window.

## Verdict

- **megakino_to: dead origin behind Cloudflare, not a blocked exit.** Both domains resolve and accept TCP from home and from the VPN exit alike, and Cloudflare's edge answers both connections identically with 522 after its own ~19.5 s origin timeout: the site's server behind the edge does not answer anyone. A slow backend would show a late answer or a 524, a block of the VPN exit a difference between the two connections, and there is none.
- **movie4k: dead, same backend, same picture.** movie4k.sx and movie4k.stream answer 522 from the Cloudflare edge from both connections; movie4k.ag resolves to one address that accepts no connection at all (TCP timeout from both). Same origin as megakino_to (one `DataApiPluginBase` backend), same failure.

The decision stays with the maintainer: new domains by site analysis (Ultracode) if the sites moved, keep them until the record shows a return, or disable the two plugins. Nothing in `_domains` was changed. What this probe cannot tell: whether the operators moved to domains not in `_domains`; a web search for the sites' current names is the next step if the plugins are to be kept.

## Where the sites went (2026-10-07, research only)

The question of the verdict: did the two sites move to domains outside `_domains`? Sources in the order of the assignment; one `GET` per candidate from the dev container (status, final host, `<title>`, the theme, and `GET /data/browse/?lang=2&keyword=…` as the discriminator: the `DataApiPluginBase` backend answers it with JSON, a DLE theme with its HTML index or 404). Hosts only.

**JDownloader.** `.devdata/JDownloader2/` names neither site (no `megakino` or `movie4k` in any plugin source or note), so there is no domain list and no dead-domain note to cross-check against.

**The old domains.** All five still point at Cloudflare (four `104.21.x.x` addresses each; `movie4k.ag` one address outside Cloudflare that accepts no connection), `www.` variants the same. The 522 pages are Cloudflare's own error page, with no `Location`, no operator text, no link: no redirect trail. The Internet Archive and archive.ph cannot be fetched from here, so the sites' last pages (movie4k.sx once listed its alternates under `/domains`) are not readable.

**Web search.** For megakino the search finds only the DLE family that the other plugin, `megakino`, already serves (megakino22.com with its mirrors, megakino.me, megakino.foo), the CUII-blocked lineage megakino.co → .fi → .how, and clone warnings against each other; tarnkappe.info (Dezember 2025) lists megakino.org as a separate site on "a typical pirate CMS" with a German/English switch, which is the `lang=2` API. For movie4k: scanners saw movie4k.sx answer 200 in June and July 2026 with a certificate expiring 2026-09-26, tarnkappe.info calls it a clone of the sold original, and movie4k.sx's own title named movie4k.ag, movie4k.at and movie4k.ac as its alternates. Queries for the sites' current domain lists or channels the search tool declined (piracy mirrors); no Telegram or X channel was found through the queries it answered, and none is attempted elsewhere.

| Candidate | GET | Title / theme | `/data/browse/` | Reading |
|---|---|---|---|---|
| megakino22.com (also megakino.ms, megakino2.org, megakino3.com → it) | 200 | no title, no DLE markers on the landing | 200 `text/html`, not JSON | the `megakino` plugin's site (DLE behind a token gate), not the API backend |
| megakino.me | 200 | "MEGAKino – Schaue Kinofilme…", DLE | 200 `text/html`, not JSON | DLE clone, not the backend |
| 9megakino.com, 7megakino.lol → 9megakino.lol | 200 | "MEGAKino \| Filme und Serien…", DLE | 404 | DLE clone, not the backend |
| megakino.foo | 200 | "Megakino ▷ Stream HD Filme…", DLE | 404 | DLE clone, not the backend |
| megakino.com.de | 200 → www | "Der Unoffizielle Streaming-Hub", Apache | 404 | an SEO page about the brand, not a site |
| megakino.how, megakino.cx | ConnectTimeout | – | – | dead (the CUII lineage) |
| movie4k.com.de | 521 | Cloudflare "web server is down" | 521 | dead clone |
| movie4k.at, www.movie4k.at | ConnectError | – | – | dead (the site's own alternate) |
| movie4k.ac | ConnectTimeout | – | – | dead (the site's own alternate) |
| movie4k.ag | ConnectTimeout | – | – | dead (in `_domains`) |

**Per site.**

- **megakino_to (megakino.org, megakino.to): no trace.** No candidate answers the `/data` API; every live "megakino" host is a DLE theme, and the DLE family is the `megakino` plugin's site already. megakino.to still drew traffic in November 2025 (Semrush), so the site died between then and step 21's audit; where its operators went, if anywhere, nothing found names.
- **movie4k (movie4k.sx, .ag, .stream): no trace.** The site was up through July 2026 and named its own alternates; those are dead too, and the one movie4k clone found (movie4k.com.de) is down. Nothing names a successor.

**For the decision.** Nothing found points at a successor domain for either site, both were alive this summer, and a site analysis (Ultracode) has no domain to analyse: the choice is between keeping the two plugins switched on while `PluginHistory` records their unreachable marks (they cost one domain check per search until the health monitor parks them) and disabling them now; the record after the next deploy shows a return if there is one.

## fireani, kinox, kinoking (2026-10-09)

**Question.** Production's 48 h digest since the 10-07 deploy shows fireani unreachable in 220 of 283 domain checks (78 %), kinox in 104 of 186 (56 %, never a result) and kinoking in 28 of 128 (22 %, 8 timeouts, its breaker opened once), beside 91 `health_probe_challenge` records. Which class is each: dead, blocked from the VPN exit only, a challenge wall the health prober counts but the plugin cannot pass, a slow backend, or alive?

**Probe.** `scripts/probes/domains.py --plugins fireani,kinox,kinoking --get-timeout 30` in the dev container (a residential connection) and on the production host (`prodctl.py probe --timeout 600 domains -- … --history`, the VPN exit), both within one sitting on 2026-10-09; kinoking and fireani repeated twice more on each side. The `HEAD` column is the domain check itself (5 s). The record is the app's own (`GET /api/v1/stats/plugins`, step 16 is deployed).

| Plugin | Domain | Where | DNS | TCP 443 | HEAD (5 s) | GET (30 s) | Answers |
|---|---|---|---|---|---|---|---|
| fireani | fireani.me | dev | 1 v4 | **TimeoutError** 5 s | ConnectTimeout | ConnectTimeout | no |
| | | production | 1 v4 | **TimeoutError** 5 s | ConnectTimeout | ConnectTimeout | no |
| kinox | www22.kinox.to, ww22.kinox.to, www22.kinos.to, ww22.kinos.to, www22.kinoz.to, ww22.kinoz.to, www20.kinox.to, www15.kinox.to, www.kinox.to (all 9) | dev | 2 v4 / 2 v6 | ok, 25–52 ms | ReadTimeout | **522** `[cloudflare]` after 19.6–19.9 s | no |
| | | production | 2 v4 / 2 v6 | ok, 33–57 ms | ReadTimeout | **522** `[cloudflare]` after 19.6–20.0 s | no |
| kinoking | kinoking.cc | dev (3 runs) | 2 v4 / 2 v6 | ok, 25–36 ms | 200 in 0.34, 0.35, 1.2 s | 200 in 0.2–0.7 s | yes |
| | | production (3 runs) | 2 v4 / 2 v6 | ok, 30–34 ms | **ReadTimeout (5.1 s), 200 in 5.08 s, ReadTimeout (5.2 s)** | 200 in 4.1, 4.7, 8.5 s | yes |

**The record** (per UTC day: checks, unreachable, searches, results):

| Plugin | 10-07 | 10-08 | 10-09 (to ~17:30) |
|---|---|---|---|
| fireani | 25 checks, 1 unreachable, 11 searches, 2 results | 72 checks, 33 unreachable, 12 searches, 3 results | 189 checks, **189 unreachable**, no search |
| kinox | 24 checks, 0 unreachable, 15 searches, 0 results | 45 checks, 0 unreachable, 12 searches, 0 results | 120 checks, **107 unreachable**, no search |
| kinoking | 43 checks, 19 unreachable, 8 searches, 7 timeouts | 52 checks, 7 unreachable, 9 searches, 1 timeout, 2 results | 33 checks, 2 unreachable, 4 searches, 2 results |

(An unreachable plugin is rechecked every 5 min instead of every interval, hence the 189 and 120 checks of 10-09.)

**The log** (`prodctl logs --since 48h`, UTC):

- The 91 `health_probe_challenge` records are **all kinoger.com** (`cloudflare_page`), 3 to 4 per hour through the whole window (13 on 10-07, 46 on 10-08, 32 on 10-09): the health prober's cycle meeting kinoger's known Cloudflare page. None belongs to fireani, kinox or kinoking.
- The plugins' own domain checks log no `<name>_domain_answers` or `<name>_no_domain_reachable` in the window (the health monitor marks instead); the marks: **fireani** `plugin_reachable` 10-08 13 h, `plugin_unreachable` 10-08 13 h and 21 h, then no return. **kinox** `plugin_unreachable` 10-09 07 h; before that `kinox_search` and `kinox_detail` 11 each, `kinox_no_hoster_links` 5 and two `kinox_http_error status=503` from www22.kinox.to at 10-08 21:07: the site answered its pages but no mirror gave a hoster link, the verification wall of KNOWN_ISSUES. **kinoking** alternates `plugin_unreachable` / `plugin_reachable` every 30 to 60 min on all three days (10-07 18–21 h, 10-08 11 h, 20–21 h, 10-09 10 h, 14 h); the breaker `kinoking:2000` opened once, 10-08 09:44. No cluster, no block window: the kinoking marks are continuous.

**Verdicts.**

- **fireani: dead host, not a blocked exit.** fireani.me resolves to one address that accepts no TCP connection from home or from the VPN exit (5 s timeout, three runs each side). Alive until 10-08 (results on 10-07 and 10-08, the last `plugin_reachable` 10-08 13 h); every one of the 189 checks of 10-09 failed. Whether the host is down or drops everyone at its firewall, the probe cannot tell; it is not the VPN exit.
- **kinox: dead origin behind Cloudflare since 10-09; the verification wall is not the whole story.** Until 10-08 the nine domains answered and the plugin found pages whose mirrors led to the wall (no hoster link, the KNOWN_ISSUES picture, plus two 503s on the evening of 10-08). Since 10-09 all nine domains answer Cloudflare's 522 from home and from the VPN exit alike, after the edge's ~19.7 s origin timeout: the origin is gone, the wall behind it unreachable. The 56 % is the wall days (0 unreachable) averaged with 10-09 (107 of 120).
- **kinoking: alive, slow from the VPN exit.** From home the domain check answers in 0.3 to 1.2 s; from the production host the same `HEAD` takes 5.1 to 5.2 s and fails the 5 s check in two of three runs, the `GET` 4 to 8.5 s. The 22 % unreachable marks are those timeouts, spread evenly over three days, and the plugin returns results when it gets through (2 on 10-08, 2 on 10-09). Not a block (every answer is a 200), not a challenge: Cloudflare's path from the production host's exit to kinoking's origin is slow, right at the check's timeout.

Nothing in `_domains` or the plugins was changed. The decision is the maintainer's: fireani and kinox are candidates for disabling until the record shows a return (kinox's wall was already a reason); kinoking is a question of the domain check's 5 s timeout from the VPN exit, not of the site.

## Live suite (2026-10-09)

`PYTHONPATH=src xvfb-run -a poetry run pytest -m live -q -p no:cacheprovider -rA --tb=line` from the dev container (a residential connection, not the production host's VPN exit), 18:57 to 19:08 UTC, 10.5 min: **30 passed, 7 failed, 4 skipped** of 41. The failures are site failures, not test failures: every failed plugin failed before its parser ran (no domain answered, or the search request timed out), except boerse, which passed Cloudflare and then found no login form. The report decides the follow-up steps; nothing was changed.

**Plugins** (`tests/live/test_plugin_smoke.py`: one search per plugin, passes with at least one result that has a title and an http link; skipped on network errors and missing credentials):

| Plugin | Base | Result | Reason |
|---|---|---|---|
| aniworld | httpx | passed | |
| burningseries | httpx | passed | |
| byte | httpx | passed | |
| cine | httpx | passed | |
| dataload | httpx | passed | |
| einschalten | httpx | passed | |
| filmfans | httpx | passed | |
| filmpalast | httpx | passed | |
| fireani | httpx | **failed** | unreachable: the search request to fireani.me timed out (`fireani_timeout context=search`), 0 results; the dead host of the section above |
| haschcon | httpx | passed | |
| hdfilme | httpx | passed | |
| hdworld | httpx | passed | |
| kinoger | httpx | passed | |
| kinoking | httpx | **failed** | unreachable: the search request to kinoking.cc timed out after 30 s (`kinoking_timeout context=search`), 0 results; the domain check answered, so this is the slow site of the section above, now slow from home too |
| kinox | httpx | **failed** | unreachable: `no domain reachable`, all 9 domains failed the check (Cloudflare 522 in the section above) |
| megakino | httpx | passed | |
| megakino_to | httpx | **failed** | unreachable: `no domain reachable` (megakino.org, megakino.to) |
| moflix | httpx | passed | |
| movie2k | httpx | passed | |
| movie4k | httpx | **failed** | unreachable: `no domain reachable` (movie4k.sx, movie4k.ag, movie4k.stream) |
| myboerse | httpx | skipped | no credentials in the dev container |
| nima4k | httpx | passed | |
| nox | httpx | passed | |
| scnlog | httpx | passed | |
| serienfans | httpx | passed | |
| sto | httpx | passed | |
| streamcloud | httpx | passed | |
| streamkiste | httpx | passed | |
| warezomen | httpx | passed | |
| animeloads | Playwright | **failed** | unreachable in this test: the domain check of anime-loads.org failed at 19:03, six minutes after the grab test below found the same domain and 7 results on it; a flaky check, not a dead site |
| boerse | Playwright | **failed** | parser miss at the login: Cloudflare solved on boerse.am and boerse.tw (`cloudflare_solved clicked=True`), then `no login form` on boerse.am, .tw, .sx, .im and .ai; boerse.kz timed out after 30 s (`RuntimeError: All boerse domains failed during login`). The login page or its form changed; the credentials were never sent |
| ddlspot | Playwright | passed | |
| ddlvalley | Playwright | passed | |
| mygully | Playwright | skipped | no credentials in the dev container |
| scnsrc | Playwright | passed | |

**Resolvers and grabs:**

| Test | Result | Reason |
|---|---|---|
| `test_resolver_live.py` XFS live URLs | skipped | `_LIVE_URLS` is empty: no resolver has a live case, so the suite says nothing about the 25 XFS and 10 DDL hosters |
| `test_resolver_live.py` XFS dead URLs | skipped | `_DEAD_URLS` is empty, same |
| `test_grab_resolve_live.py` nox, Iron Man | passed | |
| `test_grab_resolve_live.py` animeloads, One Punch Man | passed | 7 results, 30 releases; one `animeloads_captcha_rejected` on the way, the grab still returned hoster links |

**Stremio end to end** (`test_stremio_e2e_live.py`: the app with all plugins, one stream request, at least one stream must play):

| Title | Result | Streams | Resolved |
|---|---|---|---|
| Oppenheimer (2023) | passed | 5 returned of 13 links (8 unresolved at the budget, 5 plugins missing: kinoger, kinoking, kinox, megakino_to, movie4k) | voe (filmpalast, HLS), mixdrop (hdfilme), vinovo (movie2k), gxplayer (megakino, HLS), veev (moflix). Not resolved: supervideo (hdfilme) failed its check with `PrivateAddressError`; moflix's upns and rpmplay have no resolver (`hoster_without_resolver`); two dood links were swapped for their dr0pstream alternative |
| Breaking Bad S01E01 | passed | 4 returned of 6 links (2 unresolved, 3 plugins missing) | kinoger's own player (HLS) and fsst (kinoger), voe (sto, after the Turnstile gate `sto_link_gate_passed`), voe (filmpalast). Not resolved: dr0pstream (hdfilme) `ConnectError` at the check |

**What this adds to the record.** fireani, kinox and kinoking fail from home the way the section above saw them from the production host (kinoking got worse: the search itself times out now). megakino_to and movie4k are new on the unreachable list: both share `DataApiPluginBase`, and none of their five domains answered the check. boerse is the one plugin whose failure is in our code's reach: the login form is not where `boerse.py` looks for it after the Cloudflare page. animeloads needs a second look at its domain check only. The resolver suite has no cases, so the hoster picture comes from the end-to-end runs alone: 9 resolutions over 2 titles, supervideo's `PrivateAddressError` and dr0pstream's `ConnectError` are the two resolver-side failures, and moflix's upns and rpmplay are hosters without a resolver.

## Live suite (2026-10-12)

Staging 229d833 (rows 87, 89, 90, 91, 92 and 93 of the day: the hoster registry's challenge outcomes and background completion, the stealth pool's kept capture state and its warm-up, the episode placement). First `scripts/probes/resolver_urls.py` (12:39 to 12:47 UTC: 19 titles, 261 results, 737 claimed links, 20 resolvers; its failed searches: cineby, megakino_to and movie4k `PluginUnreachableError` 22 each, kinoking timeout 9), then `PYTHONPATH=src xvfb-run -a poetry run pytest -m live -q -p no:cacheprovider -rA --tb=line` from the dev container (a residential connection), 12:47 to 12:56 UTC, 8.9 min: **65 passed, 12 failed, 3 skipped** of 80. The 2026-10-09 run had 41 tests because the resolver cases had no links then; the resolvers' baseline is row 47's run (2026-10-10, section "upns and rpmplay" below: 35 of 40). **No failure is a code regression:** the three plugins fail at their sites, and the nine resolver failures are dead files, CDNs that refuse or time out from this network, and two known cases. None of the failing resolvers' modules, `_verify.py` or the registry's playback check changed since row 47 (`git log` since 2026-10-10 12:00 names only `registry.py`, whose row 89 and 90 diff touches no check or verify line). The resolver suite raises the app's log to errors, so the resolver reasons come from a rerun of the nine live links through the app's registry with the log captured (after the run, before 13:00 UTC; event names and fixed fields only).

**Plugins** (`tests/live/test_plugin_smoke.py`):

| Plugin | Base | 2026-10-09 | 2026-10-12 | Reason (2026-10-12) |
|---|---|---|---|---|
| aniworld | httpx | passed | passed | |
| burningseries | httpx | passed | passed | |
| byte | httpx | passed | skipped | site: "Network error reaching byte"; `domains.py --plugins byte` after the run: HEAD `ReadTimeout` after 5.1 s, GET 200 through Cloudflare after 5.1 s, a slow site |
| cine | httpx | passed | passed | |
| dataload | httpx | passed | passed | |
| einschalten | httpx | passed | passed | |
| filmfans | httpx | passed | passed | |
| filmo | httpx | – | passed | new in the suite since 2026-10-09 |
| filmpalast | httpx | passed | passed | |
| fireani | httpx | **failed** | passed | the search answered this time |
| haschcon | httpx | passed | passed | |
| hdfilme | httpx | passed | passed | |
| hdworld | httpx | passed | passed | |
| kinoger | httpx | passed | passed | |
| kinoking | httpx | **failed** | passed | the search answered this time (the probe before the run still saw 9 timeouts) |
| kinox | httpx | **failed** | **failed** | site: `Plugin 'kinox' returned 0 results`; domain, search and detail page answer (`kinox_domain_found`, `kinox_detail` 7), every mirror ends at kinox's captcha redirect (over the whole run, the end-to-end titles included: `kinox_no_redirect` 6, `kinox_no_hoster_links` 7, `kinox_http_error` 10): the finding of row 73 below, no longer "no domain reachable" |
| megakino | httpx | passed | passed | |
| megakino_to | httpx | **failed** | **failed** | site: `PluginUnreachableError: megakino_to: no domain reachable` |
| moflix | httpx | passed | passed | |
| movie2k | httpx | passed | passed | |
| movie4k | httpx | **failed** | **failed** | site: `PluginUnreachableError: movie4k: no domain reachable` (`movie4k_domain_check_failed` for movie4k.ag and movie4k.stream) |
| myboerse | httpx | skipped | skipped | no credentials in the dev container |
| nima4k | httpx | passed | passed | |
| nox | httpx | passed | passed | |
| scnlog | httpx | passed | passed | |
| serienfans | httpx | passed | passed | |
| sto | httpx | passed | passed | |
| streamcloud | httpx | passed | passed | |
| streamkiste | httpx | passed | passed | |
| warezomen | httpx | passed | passed | |
| animeloads | Playwright | **failed** | passed | the domain check answered this time |
| boerse | Playwright | **failed** | passed | the forum answered again: step 38 below found its origin down behind Cloudflare (522), not a markup change |
| ddlspot | Playwright | passed | passed | |
| ddlvalley | Playwright | passed | passed | |
| mygully | Playwright | skipped | skipped | no credentials in the dev container |
| scnsrc | Playwright | passed | passed | |

**Resolvers** (`tests/live/test_resolver_live.py`, one link per resolver from the probe above; "before" is row 47's run of 2026-10-10):

| Resolver | Links found | Live before | Live | Dead before | Dead | Reason (2026-10-12) |
|---|---|---|---|---|---|---|
| doodstream | 122 | passed | passed | passed | passed | |
| dropload | 83 | **failed** | **failed** | passed | passed | network, as before: the captcha capture finds the stream (`dropload_captcha_browser_fallback`, `dropload_browser_stream`, `capture_state_saved`), then its CDN check fails with `hoster_resolve_check_error error=ConnectTimeout` (step 40: `ConnectError`) |
| filemoon | 34 | passed | passed | passed | passed | |
| firestream | 16 | passed | **failed** | passed | passed | site: `firestream_resolved`, then `playback_check_failed reason='status 502'` and `hoster_resolve_unplayable`: the CDN answers 502 for this file |
| fsst | 19 | passed | passed | passed | passed | |
| gxplayer | 8 | passed | passed | passed | passed | |
| mixdrop | 68 | passed | passed | passed | passed | |
| playmate | 6 | passed | **failed** | passed | passed | dead link: `playmate_resolved`, then `playback_check_failed reason='status 404'`, `hoster_resolve_unplayable`; step 42's removed-file case (the stream API hands out a 404 placeholder) |
| rpmplay | 14 | **failed** | **failed** | passed | passed | dead link, as before: `rpmplay_file_offline` |
| streamtape | 1 | passed | passed | passed | passed | |
| strmup | 31 | passed | passed | passed | passed | |
| supervideo | 63 | **failed** | **failed** | passed | passed | site, as before: Cloudflare 403 (`supervideo_cloudflare_detected status=403`), the browser captures a stream (`supervideo_browser_stream`), its CDN answers HTML to the check (`playback_check_failed reason='html'`) |
| upns | 14 | **failed** | **failed** | passed | passed | dead link, as before: `upns_file_offline` |
| veev | 27 | passed | **failed** | passed | passed | network: `veev_resolved`, then the stream check times out (`hoster_resolve_check_error error=ConnectTimeout`), twice in two reruns (6.7 s each) |
| vidhide | 22 | passed | **failed** | passed | passed | network: the link is moflix's vidhide mirror (`moflix-stream`, an XFS config); its video check against the CDN `acek-cdn` ends in an HTTP error after 8.8 s, the verify timeout of 8 s (`vidhide_video_verify_error cdn=acek-cdn`) |
| vidoza | 2 | **failed** | **failed** | passed | passed | network, as before: `private_address_refused` (the dev container's resolver, the cineby section below) |
| vidsonic | 9 | passed | passed | passed | passed | |
| vinovo | 14 | passed | passed | passed | passed | |
| vixeo | 5 | passed | passed | passed | passed | |
| voe | 179 | passed | passed | passed | passed | |

**Grabs** (`tests/live/test_grab_resolve_live.py`):

| Test | 2026-10-09 | 2026-10-12 |
|---|---|---|
| nox, Iron Man | passed | passed |
| animeloads, One Punch Man | passed | passed |

**Stremio end to end** (`tests/live/test_stremio_e2e_live.py`):

| Title | 2026-10-09 | 2026-10-12 | Streams (2026-10-12) | Resolved (2026-10-12) |
|---|---|---|---|---|
| Oppenheimer (2023) | passed, 5 of 13 links | passed | 5 returned of 18 links (7 attempted, 13 unresolved at the budget; 5 plugins missing: cineby, kinoger, kinoking, megakino_to, movie4k) | voe (filmpalast, HLS), mixdrop (its mxdrop domain, hdfilme), vinovo (movie2k), gxplayer (megakino, HLS), voe (filmo, HLS). Not resolved: upns and rpmplay (moflix), `hoster_resolve_failed`, the offline files of row 47 |
| Breaking Bad S01E01 | passed, 4 of 6 links | passed | 2 returned of 5 links (3 attempted; 3 plugins missing: cineby, megakino_to, movie4k) | voe (filmpalast, HLS), voe (sto, HLS). Not resolved: dr0pstream (hdfilme) `hoster_resolve_check_error error=ConnectError`, as on 2026-10-09 |

**Reading.** Against 2026-10-09 the plugins improved: fireani, kinoking, animeloads and boerse pass, filmo is new and passes, byte skipped on a slow answer. The three failures are dead-site plugins failing at their sites (megakino_to and movie4k: no domain; kinox: the captcha redirect of row 73). Against row 47 the resolvers lost four live cases (firestream, playmate, veev, vidhide) and all twenty dead cases pass. Each of the four ends after the resolver found its stream, at the playback or video check: a 502 and a 404 from the CDN, and two network timeouts from this network. One link per resolver, one network: a second link of each, or the same links from the production host, would tell a dead file from a CDN that refuses the dev container; that is not tested here. The two end-to-end titles pass with fewer streams than on 2026-10-09 (Breaking Bad 2 instead of 4: of its 5 links 3 were attempted and 2 resolved; on 2026-10-09 kinoger's own player and fsst resolved, this time kinoger's one result brought no stream, and the log does not say why its links were not attempted), and no resolution in either met a challenge (`challenge=None` on all seven).

**The four lost cases on further links (13:12 to 13:33 UTC, staging 8a1f1db).** `resolver_urls.py --avoid <earlier output>` takes each resolver's next link (the option is new for this). The run's own links file lived in its worktree's untracked `.cache/` and was gone, so link 1 is the probe's first link again (13:12 to 13:18 UTC); it ends with the same events as the run above, most likely the same link. Link 2 avoids link 1 (13:18 to 13:24), link 3 avoids both (13:26 to 13:32). The live cases ran on links 1 and 2 (`pytest -m live tests/live/test_resolver_live.py -k ...`, the links file swapped and restored); the events come from the registry rerun with the log captured, as above, and production's figures since its 10:39 restart from Mr. Fable:

| Resolver | Link 1 | Link 2 | Link 3 | Production since 10:39 | Verdict |
|---|---|---|---|---|---|
| firestream | filmpalast's; `firestream_resolved`, then `playback_check_failed reason='status 502'` | moflix's, another domain; `status 502` again | filmpalast's; `firestream_resolved`, then `hoster_resolve_check_error error=ReadTimeout` (7.7 s) | 5 streams and 1 dead of 6 | the resolver works; its CDN answers 502 or times out to the dev container on every link, so the live case cannot pass from here |
| playmate | moflix's; `status 404` | filmpalast's; **passed**, `hoster_resolve_success is_hls=True duration_ms=2982` | – | 3 of 3 | link 1 is a removed file (step 42's 404 placeholder); the resolver works |
| veev | moflix's; `veev_resolved`, then `hoster_resolve_check_error error=ConnectTimeout` (6.7 s) | kinoking's; `veev_offline` | kinoking's; `veev_offline` | no resolution | not settled: the resolver extracts the video URL and tells an offline file, but no stream of the three reached its CDN from here |
| vidhide (moflix's mirror `moflix-stream`) | `vidhide_video_verify_error cdn=acek-cdn` (8.9 s, the 8 s verify timeout) | `vidhide_file_offline` | `vidhide_video_head_failed status=502 cdn=dramiyos-cdn` | no resolution | not settled: the XFS config extracts the video URL and tells an offline file, while two CDNs fail the video check from here (an HTTP error, a 502) |

No resolver is broken on every link in its own code: each one reached its video URL or named an offline file, and every failure after that lies at a CDN reached from the dev container. Three of them (firestream, veev, vidhide) still pass no live case from here; firestream's production figures place the fault at the network, veev's and vidhide's need a resolution where their CDN answers (production, or a later run).

## Live suite (2026-10-12, evening)

Staging 1d79ec7 (row 95; production runs it since 15:11:28 UTC). The same steps as the afternoon run above: `scripts/probes/resolver_urls.py` (15:12:53 to 15:19:56 UTC: 19 titles, 258 results, 733 claimed links, 20 resolvers; its failed searches: cineby, megakino_to and movie4k `PluginUnreachableError` 22 each, kinoking timeout 10), then `PYTHONPATH=src xvfb-run -a poetry run pytest -m live -q -p no:cacheprovider -rA --tb=line` from the dev container, 15:20:21 to 15:29:25 UTC, 9.1 min: **64 passed, 12 failed, 4 skipped** of 80 (afternoon: 65, 12, 3). **No regression:** the twelve failures are the afternoon's twelve, with the same reasons. The one change is kinoking, passed then and skipped now on a slow search. Between the two runs only row 95 changed code (`git log 229d833..1d79ec7` on `plugins/`, the plugin bases and the resolvers: aniworld's and sto's episode placement, one line each); no failing plugin's or resolver's code changed. The resolver reasons again come from a rerun of the nine live links through the app's registry with the log captured, after the run (event names and fixed fields only).

**Plugins** (`tests/live/test_plugin_smoke.py`; rows that passed in both runs are left out: aniworld, burningseries, cine, dataload, einschalten, filmfans, filmo, filmpalast, fireani, haschcon, hdfilme, hdworld, kinoger, megakino, moflix, movie2k, nima4k, nox, scnlog, serienfans, sto, streamcloud, streamkiste, warezomen, animeloads, boerse, ddlspot, ddlvalley, scnsrc):

| Plugin | Base | Afternoon | Evening | Reason (evening) |
|---|---|---|---|---|
| byte | httpx | skipped | skipped | site: "Network error reaching byte" again; `domains.py --plugins kinoking,byte` after the run: HEAD and GET 200 through Cloudflare in 111 and 70 ms, a slow answer during the run only |
| kinoking | httpx | passed | skipped | site: "Network error reaching kinoking"; the link probe before the run saw 10 search timeouts (afternoon: 9, and the test passed), in the end-to-end run it answered Breaking Bad and missed Oppenheimer's budget; after the run HEAD 200 in 339 ms, GET 200: the site's search is slow at times, no code changed |
| kinox | httpx | **failed** | **failed** | site, as before: `Plugin 'kinox' returned 0 results` (row 73's captcha redirect) |
| megakino_to | httpx | **failed** | **failed** | site, as before: no domain reachable |
| movie4k | httpx | **failed** | **failed** | site, as before: no domain reachable |
| myboerse | httpx | skipped | skipped | no credentials in the dev container |
| mygully | Playwright | skipped | skipped | no credentials in the dev container |

**Resolvers** (`tests/live/test_resolver_live.py`, one link per resolver from each run's probe; every dead case passed in both runs; resolvers that passed their live case in both runs are left out: doodstream, filemoon, fsst, gxplayer, mixdrop, streamtape, strmup, vidsonic, vinovo, vixeo, voe):

| Resolver | Links afternoon | Links evening | Live afternoon | Live evening | Reason (evening) |
|---|---|---|---|---|---|
| dropload | 83 | 83 | **failed** | **failed** | network, as before: the captcha capture finds the stream (`dropload_browser_stream cdn=dropcdn`), then `hoster_resolve_check_error error=ConnectTimeout` |
| firestream | 16 | 16 | **failed** | **failed** | as before: `firestream_resolved`, then `playback_check_failed reason='status 502'` (its CDN refuses the dev container on every link, the lost-cases table above) |
| playmate | 6 | 6 | **failed** | **failed** | dead link, as before: the probe's first link is again moflix's removed file (`status 404`); link 2 passed in the afternoon |
| rpmplay | 14 | 14 | **failed** | **failed** | dead link, as before: `rpmplay_file_offline` |
| supervideo | 63 | 63 | **failed** | **failed** | as before: Cloudflare 403, the browser captures a stream, its CDN answers HTML (`playback_check_failed reason='html'`) |
| upns | 14 | 14 | **failed** | **failed** | dead link, as before: `upns_file_offline` |
| veev | 27 | 27 | **failed** | **failed** | network, as before: `veev_resolved`, then `hoster_resolve_check_error error=ConnectTimeout` (6.7 s) |
| vidhide | 22 | 22 | **failed** | **failed** | network: moflix's mirror again, now `vidhide_video_head_failed status=502 cdn=acek-cdn` (afternoon: a verify error at the 8 s timeout on the same CDN) |
| vidoza | 2 | 2 | **failed** | **failed** | network, as before: `private_address_refused` |

The link counts match the afternoon's for every resolver except voe (175, afternoon 179).

**Grabs** (`tests/live/test_grab_resolve_live.py`): nox (Iron Man) and animeloads (One Punch Man) passed in both runs.

**Stremio end to end** (`tests/live/test_stremio_e2e_live.py`):

| Title | Afternoon | Evening | Streams (evening) | Played (evening) |
|---|---|---|---|---|
| Oppenheimer (2023) | passed, 5 of 18 links | passed | 5 returned of 14 links (5 attempted and resolved, 9 unresolved at the budget; 6 plugins missing: cineby, kinoger, kinoking, kinox, megakino_to, movie4k): voe (filmpalast, HLS), mixdrop (its mxdrop domain, hdfilme), vinovo (movie2k), gxplayer (megakino, HLS), voe (filmo, HLS), the afternoon's five | stream 1: **VOE from filmpalast** (HLS) |
| Breaking Bad S01E01 | passed, 2 of 5 links | passed | 2 returned of 5 links (3 attempted; 3 plugins missing: cineby, megakino_to, movie4k): voe (filmpalast, HLS), voe (sto, HLS); dr0pstream (hdfilme) `hoster_resolve_check_error error=ConnectTimeout` (afternoon: `ConnectError`) | stream 1: **VOE from filmpalast** (HLS) |

The test returns at the first stream that plays and logs none, so the played stream comes from a rerun of its path right after the run (the same app setup and the test's own `_play()`): both titles listed the same streams in the same order, and stream 1 played at once. No resolution met a challenge (`challenge=None` on all seven).

**Reading.** Against the afternoon nothing regressed: the same three dead-site plugins and the same nine resolver failures with the same reasons, all at sites, dead files or CDNs reached from the dev container. kinoking's skip is its slow search, which the probe already showed in both runs. Both end-to-end titles play VOE from filmpalast first.

## boerse (2026-10-09, step 38)

**Question.** The live suite above called boerse a parser miss: Cloudflare solved, then `no login form` on five domains. Step 38 asked for the login page the plugin sees after the solve, compared with the parser's selectors.

**Capture.** Through the plugin's own browser and context (`_ensure_browser()`, `_context_options()`, `_wait_for_cloudflare()`, then `page.content()`; `scripts/capture_pages.py` records only httpx fetches), home page and `/login.php` per domain, from the dev container, 19:30 to 19:45 UTC; and `scripts/probes/domains.py --plugins boerse --get-timeout 30` (run with `python -P`, the probe directory shadows the redis package otherwise):

| Domain | DNS | TCP 443 | HEAD (5 s) | GET (30 s) | After the Cloudflare solve (browser) |
|---|---|---|---|---|---|
| boerse.am | 2 v4 / 2 v6 | ok | 403 `[cloudflare]` | 403 challenge page | challenge solved, then **`boerse.am \| 522: Connection timed out`**, home and `/login.php` |
| boerse.tw | 2 v4 / 2 v6 | ok | 403 `[cloudflare]` | 403 challenge page | same: solved, then **522** |
| boerse.sx | 2 v4 / 2 v6 | ok | ReadTimeout 5.1 s | **522** after 19.7 s | **522** at once, no challenge |
| boerse.im | 2 v4 / 2 v6 | ok | ReadTimeout 5.1 s | **522** after 19.7 s | **522** at once |
| boerse.ai | 2 v4 | ok | 200 | 200 | redirect to a registrar's **for-sale page** for the domain (title "Trustpilot", 260 KiB, no form) |
| boerse.kz | 1 v4 | **TimeoutError** 5 s | ConnectTimeout | ConnectTimeout | `Page.goto` timeout after 30 s |

**Verdict: the forum's origin is down behind Cloudflare, the markup did not change.** All four domains that reach Cloudflare get the edge's own 522 page (`cf-error-details`, 6.9 KiB), which has no form, so the parser's `form[action*="login"]` was right to find nothing; the fifth domain is parked; the sixth accepts no connection. A web search found no announcement of a new domain: the forum's own announcement thread lists exactly these six (boerse.sh and boerse.bz retired), and third-party monitors show the site down intermittently through 2026. The login form cannot be captured until the origin answers again.

**Done in the plugin** (`fix(plugins): boerse names the dead origin`): the login reads the page after the Cloudflare wait and treats Cloudflare's error page as no answer (`boerse_origin_error status=522` instead of `no login form`); no answering domain raises `PluginUnreachableError` like every other Playwright plugin, so the Stremio search marks boerse unreachable and the plugins' record counts it under `unreachable`, the evidence for the keep-or-disable decision; a real page without a form keeps the `All boerse domains failed during login` error. boerse.ai left `_domains` (parked). The 522 page is a fixture (`tests/fixtures/html/boerse/home-522.html.gz`, Ray ID and visitor IP scrubbed by `scripts/capture_pages.py`). The live boerse test still fails while the origin is down (`PluginUnreachableError` is not a skip in `tests/live/test_plugin_smoke.py`, as for animeloads); the row above should read *unreachable*, not *parser miss*.

**Open.** When the origin returns, capture the login page the same way (home page after the solve: the vBulletin form with `vb_login_username`, `vb_login_password`, `vb_login_md5password`, `securitytoken`, `do=login`, `s`, `cookieuser`) and check the parser's selectors against it; nothing says they changed.

## Live resolver cases (2026-10-09, step 40)

**Setup.** `scripts/probes/resolver_urls.py` (dev container, `python -P`, 19:52 to 19:58 UTC) searched the 19 titles of `docs/plans/round-titles.txt` through the stream plugins the way `title_match.py` does and mapped every result link to its resolver with the registry's `canonical_hoster`: 227 results, 562 claimed links, **18 resolvers** with a link, written to the untracked `.cache/live/resolver-urls.json`. Searches that failed: megakino_to and movie4k unreachable (22 each, the dead domains of the live suite), kinoking 12 and kinox 10 timeouts. Links no resolver claims (second-level domain, count): aniworld 92 (the plugin's own redirect links, resolved through its site, not a hoster), upns 15 and rpmplay 15 (moflix's hosters without a resolver), gupload 13, kinoger 7, odysseusa 7, vids 4, flyfile 2, anonstream 1, youtube 1, hxfile 1, flyf 1. No DDL resolver got a link: the round titles are stream titles and the download plugins are not searched (as in `title_match.py`), so the 10 DDL hosters and most XFS configs stay untested by this file; vidhide and vidoza are the two XFS configs with a link.

**Run.** `xvfb-run -a poetry run pytest -m live tests/live/test_resolver_live.py` from the dev container, four runs between 19:59 and 20:10 UTC, the last one 48 s: **31 passed, 5 failed** of 36 (18 live, 18 dead). The dead case is the live URL with the id of its last path segment altered (last four characters reversed; zeros for an all-digit id; a trailing slash or extension kept), and it held for every resolver once the rule handled fsst's shape (an id of digits only: the reversed id was another existing video; a trailing slash: the first rule left the id untouched).

| Resolver | Links found | Live | Dead | Reason |
|---|---|---|---|---|
| doodstream | 95 | passed | passed | |
| dropload | 82 | **failed** | passed | resolves through the browser fallback (captcha), then the playback check of its CDN fails with `ConnectError` from the dev container (the live suite's Breaking Bad run saw the same); not a resolver bug |
| filemoon | 12 | passed | passed | |
| firestream | 16 | passed | passed | |
| fsst | 19 | passed | passed | dead case needed the all-digit rule |
| gxplayer | 8 | passed | passed | |
| mixdrop | 68 | passed | passed | |
| playmate | 6 | **failed** | passed | resolves, then the playback check answers 404: the HLS URL the resolver builds is refused by the CDN (`hoster_resolve_unplayable`) |
| streamtape | 1 | **failed** | passed | resolves, then the playback check gets HTML instead of media (`hoster_resolve_unplayable`): the video URL is a page, the resolver's extraction is stale |
| strmup | 33 | passed | passed | |
| supervideo | 56 | **failed** | passed | Cloudflare 403, the browser fallback captures a stream, its CDN answers HTML to the playback check (`hoster_resolve_unplayable`) |
| veev | 22 | passed | passed | |
| vidhide | 21 | passed | passed | XFS config |
| vidoza | 2 | **failed** | passed | `private_address_refused`: the host resolves to a private address from the dev container's resolver, so the SSRF guard refuses the request; both verdicts say nothing about the resolver (the dead case passes for the same reason) |
| vidsonic | 8 | passed | passed | |
| vinovo | 14 | passed | passed | |
| vixeo | 5 | intermittent | passed | passed in two of four runs; the other two: the stealth capture got a 404 from the player |
| voe | 94 | passed | passed | |

**Reading.** 12 of 18 resolvers resolve a link the plugins found today and report an altered id dead. The five failures are two kinds: a stream that resolves but whose CDN fails the registry's playback check (dropload: connection refused from here; playmate: 404; streamtape and supervideo: HTML), and vidoza's host refused by the private-address guard. The playback check is the server's own verdict (`verify_streams`), so these resolvers would hand Stremio no stream from this network either; whether the CDNs refuse the dev container's address or every address is the next question, and the production host's view (`prodctl.py logs`, `hoster_resolve_unplayable` per hoster) answers it without a probe. A resolver with no link in the file (the DDL hosters, 23 of the 25 XFS configs, kinoger's own player, upns and rpmplay without a resolver) is not tested, and a hoster whose resolver finds nothing live is recorded here, not removed. The file is per machine and day: a new probe run before a live run keeps the cases current, and the test module says what it found in its ids only (resolver names; no URL reaches the output, the app's log is raised to errors for the module).

## streamtape and playmate (2026-10-09, step 42)

Step 40's live run left two resolvers failing the registry's playback check. Both hoster pages were captured with the app's own HTTP client (`build_http_client`), scrubbed, and compared with the resolvers' extraction; the sweep behind the second table took every playmate and streamtape link the round titles yield (the probe keeps the first per resolver).

| Resolver | Page | Extraction | Outcome |
|---|---|---|---|
| streamtape | The embed page carries the `get_video` parameters in three hidden elements (`ideoolink`, `botlink`, `robotlink`). Their HTML texts hold two decoy tokens; the script assigns the real value to each element as string pieces joined with `substring` chains, and the player reads `botlink`. Tokens are 12 characters, mixed case. | Stale. The resolver took the first HTML literal (a decoy) and "corrected" its token with an uppercase-and-digit pattern, which kept a two-character prefix of the real token. `get_video` answered a 200 with `{"status":500,"msg":"Sorry, error on our side!"}`, which the resolver's HEAD check accepted and the playback check reported as HTML. | Fixed (195e539): `video_params()` evaluates the script's assignment to the player's element; fixture `tests/fixtures/html/streamtape/embed-decoys.html.gz` (synthetic tokens keep the decoys distinct). Live and dead case pass. |
| playmate | The probe's link (an `/embed/` form) is a removed file: the embed page says "This video is no longer available", the meta API still answers `success: true` with a title, and the stream API hands out a placeholder `sx`: a one-segment `.mp4` path on playmate.to that answers 404. A live file's `sx` is a three-segment `.txt` master playlist on a CDN host, as the resolver expects. | Current. The extraction matches JD2's and the live files. | No change: the registry's playback check marks the removed file unplayable (dead for 15 min). 5 of the 6 playmate links of the round titles play; the live suite's case is one of them now (`.cache/live/resolver-urls.json`). |

**Reading.** Neither CDN refuses this network. Streamtape's page changed under the resolver (the decoys and the token alphabet); a removed playmate file is a file the hoster's own API does not admit, a single sample, and the playback check already catches it, so the resolver stays as it is.

## One browser User-Agent (2026-10-10, step 44)

The domain check, the health prober and the link validator sent the shared client's bot User-Agent; filemoon, the XFS hosters and doodstream a truncated browser UA without the Chrome token; generic DDL, rapidgator, ddownload, gofile, mediafire and streamtape none of their own. All send `DEFAULT_USER_AGENT` now. Proof: the live resolver cases of step 40 (`.cache/live/resolver-urls.json`, 18 resolvers, a live and a dead case each) on the dev container, once before and once after the change, minutes apart.

| Run | Passed | Failed live cases |
|---|---|---|
| before | 31 of 36 | doodstream, dropload, supervideo, vidoza, voe |
| after | 32 of 36 | doodstream, dropload, supervideo, vidoza |

**Reading.** No resolver that passed before fails after, so every resolver keeps the browser UA. Of the changed resolvers the cases cover filemoon, streamtape and the XFS hoster vidhide (pass before and after), doodstream and the XFS hosters dropload and vidoza (fail before and after: the UA was not what blocked them; doodstream passed in step 40's run, dropload and vidoza failed there too). The DDL hosters (generic DDL, rapidgator, ddownload, gofile, mediafire) and the other 22 XFS hosters have no live case, because the round titles yield no link of theirs; their change is the header alone. voe passes after but was not changed: its failure before was the site's variance. Every dead case holds in both runs.

## HTML answers fail the CDN verification (2026-10-10, row 46)

`verify_video_url()` (the XFS video path, VOE, Streamtape) counted any 200/206 as reachable, so a dead file that a CDN serves as an error page with 200 passed to the playback check, or with `stremio.verify_streams` off to Stremio. It rejects `text/html` now and asks a CDN that does not allow HEAD (405) for the first bytes, which must not be HTML either (5661f9e; `docs/features/hoster-resolvers.md` → CDN verification). Proof: the live resolver cases (`.cache/live/resolver-urls.json`, 18 resolvers, a live and a dead case each) on the dev container, once before and once after the change, minutes apart.

| Run | Passed | Failed live cases |
|---|---|---|
| before | 33 of 36 | dropload, supervideo, vidoza |
| after | 33 of 36 | dropload, supervideo, vidoza |

**Reading.** No case changes: no resolver that passed before fails after, and every dead case holds in both runs; the three failures are the known ones (dropload's CDN refuses the connection from here, vidoza's host resolves to a private address here, supervideo below). The cases cover the changed paths through vidhide (XFS), voe and streamtape, which pass before and after: their CDNs answer the HEAD with a media type. The HTML rule itself rests on the unit tests (a CDN answering 200 `text/html`; HEAD 405, then a Range GET with an HTML body): no live link produced such an answer in these runs.

**supervideo** does not flip, because the resolver never used the HEAD verification. Its page answers 403 (Cloudflare), the browser fallback captures the stream, and the registry's playback check (`needs_playback_check`) judges it: in this run the CDN (serversicuro) answered the check's Range GET with 429 (`playback_check_failed` with `reason="status 429"`, after four suite runs from this address within an hour), in step 40's run with an HTML page; the dead case is found offline by the browser (`stealth_capture_offline`). So supervideo's live case was never a false alive in the suite. Whether production handed out a supervideo stream that then failed in the player (the maintainer's second report) is a question for production's logs after the next deploy (`prodctl.py logs --grep supervideo`; production runs the playback check, `config_effective` names no `verify_streams` override, and the container's log, which starts at the rebuild of 10:27 UTC, held no supervideo resolution by 10:50 UTC).
## upns and rpmplay (2026-10-10, row 47)

**Question.** moflix's `moflix.upns.xyz` and `moflix.rpmplay.xyz` links were the hosters without a resolver in every round since step 40 (15 links each over the 19 round titles). What does the player do, and do its files play?

**Setup.** Site analysis in the dev container, 09:30 to 11:20 UTC: the player's script (`assets/index-*.js`, an obfuscated vidstack build) read for its API paths and strings; the player opened headless (playwright-mcp) and headful (Patchright under `xvfb-run`) on fresh ids of both hosters with every `/api/` and media request logged (paths, parameter names, statuses and sizes only); the API called with httpx for the 28 links the moflix plugin returned for the 19 titles (14 per hoster over 8 movies; the 11 series titles gave moflix none on these hosters), plain and as the embedding site (`Referer` and `Origin` moflix, `r=` its URL); moflix's own video entries for Oppenheimer and Dune: Part Two read through the plugin's API (`/api/v1/titles/<id>?load=videos`) for their upload dates; the decryption confirmed in three public implementations found by the key (CloudStream `extractors/VidStack.kt`, Cyberdrop-DL `crawlers/vidstack.py`, Stream4me `servers/rpmshare.py`).

**The player.** One family on both hosts (`playerId` `cmt` on upns, `gwm` on rpmplay); the video id is the URL fragment. Headless it stops after `GET /api/v1/info?id=` with the page text "Opss! Headless Browser is not allowed"; headful it calls `GET /api/v1/video?id=<id>&w=1280&h=720&r=<document.referrer>` on the first click, with no header beyond a browser's. Both answers are hex ciphertext, AES-128-CBC with the fixed key and IV from the script (the public implementations name a second IV for other installations). `info` decrypts to the player's configuration (ads, poster, feature flags), identical for every id including an invented one; `video` is the stream call, its JSON's `source` the HLS master (the script's strings name `/gg/master.`, `/tt/master.`, `cfStream`, `p2pStream` and a `capacityToken`). The script's other paths need a token or carry nothing (`/api/v1/player?t=`: "Token is invalid"; `/download?id=`: 403 "Sorry this video is unavailable"; `/folder?id=`: `[]`).

**The files.** Every one of the 28 links answers 404 `{"message": "Video not found or deleted"}`: from httpx, from the headful player itself (the page then shows the same words), with and without moflix as the embedding site. moflix's entries date the uploads from 2023-08 (Oppenheimer's) to 2026-05-15 (Dune: Part Two's "Mirror 5" on upns and "Mirror 7" on rpmplay), so the newest are gone too, while moflix's other mirrors resolve (the live suite's veev, strmup and vidhide cases are moflix links). A burst of calls (4 in parallel) got 429 `{"message": "Rate limit exceeded"}` from about the 15th answer on; a serial pass 5 s apart, a minute later, got 404 for each. Whether the host blocks this network under the same words cannot be ruled out without a live file on the player family: the one public example id (a 2025 script, p2pplay) answers the same 404.

**The resolver.** `UpnsResolver` (`hoster_resolvers/upns.py`; one module, one instance per name, so the two mirrors stay two hosters in Stremio; the row said two modules) does what the player does: the `video` call with the player host as `Referer`, decryption with both IVs (a wrong IV garbles the first block only, so the text must open the JSON object), `source` as the HLS master with `Origin` and `Referer` of the player host after `verify_video_url()`; 404 is offline, 429 not resolved (the registry caches both as dead for 15 min). No browser capture: the player refuses headless browsers, and the API needs no token. In a round the links turn from `hoster_without_resolver` into `offline`, one API call per link per 15 min.
**Live suite.** `tests/live/test_resolver_live.py` after `scripts/probes/resolver_urls.py --plugins moflix` added the two cases (the registry claims the links now: 15 upns and 15 rpmplay links of the round titles, the first of each taken, Der Schuh des Manitu's), against the row 46 run of the morning as the baseline:

| Run | Resolvers | Passed | Failed live cases |
|---|---|---|---|
| Row 46 (baseline) | 18 | 33 of 36 | dropload, supervideo, vidoza |
| Row 47 | 20 | 35 of 40 | dropload, supervideo, vidoza, upns, rpmplay |

The two new live cases fail the way the analysis predicts (`upns_file_offline`, `rpmplay_file_offline`: the files are deleted, the resolver answers `None`), their dead cases pass (an altered fragment id is 404 too), and no other case changed. The row's live proof, both cases passing, needs a moflix upload on these hosters that still exists: the next probe run before a live run takes a newer link by itself once moflix adds one.
## cineby (2026-10-10, row 48)

**Question.** cineby has had 0 results in every audit since 2026-10-07, and in the dev container its search host `db.videasy.net` resolves to a private address that the request guard refuses (`private_address_refused`, 14 of 14 in the audit of 2026-10-10). Does the host resolve publicly, does the search answer, and what is the site's current API host?

**Setup.** Dev container, 11:00 to 11:45 UTC: the name through the system resolver and through two public DNS-over-HTTPS resolvers (Cloudflare, Google); the plugin's eight `_domains` through `scripts/probes/domains.py --plugins cineby`; the answering domains opened with curl and in a headful Patchright browser with every request host logged (`r48_site.py`: hosts and path shapes only); the series probe `scripts/probes/series_episodes.py --plugins cineby` before and after the change (14 requests: the round's 12 series plus Severance S01E05 and S02E05). Production: see the paragraph at the end.

**The search host.** `db.videasy.net` does not exist: both public resolvers answer NXDOMAIN under a live `videasy.net` zone (Cloudflare name servers). The private address of the audit is the dev container's own making: its resolver carries the LAN's search domain, a name that fails gets that suffix appended, and the home router answers every name under its own domain with its LAN address. The request guard then refuses it, which is right: a private address is never fetched. The guard stays as it is.

**The site.** `cineby.gd` and `cineby.app` answer 301 to `https://www.cineby.at/`, the site's current domain, and that domain's delegation is lame on 2026-10-10: its name servers refuse queries (Google: "Name servers refused query (lame delegation?)", Cloudflare: "No Reachable Authority at delegation cineby.at"), so a browser's load of the page times out and the site's current API host cannot be read from its own requests. The six other entries of the plugin's list are not the site: `cineby.xyz` is a parked advertising page (brand logos from a "searchpotato" bucket), `cineby.bond` and `cineby.watch` are landing pages that link to further cineby domains (`.site`, `.digital`, `.sbs`, `.media`), `cineby.site` answers a Cloudflare challenge, `cineby.today` and `cineby.digital` do not connect. The old `_verify_domain()` would have taken `cineby.xyz` (200) for the working site; cineby's `search()` never called it, which is why the plugin never reported unreachable.

| Domain | DNS | HEAD / GET from the dev container | What it is |
|---|---|---|---|
| cineby.gd | yes | 301 → www.cineby.at | redirect to the site |
| cineby.app | yes | 301 → www.cineby.at | redirect to the site |
| cineby.at | lame delegation (SERVFAIL) | timeout | the site |
| cineby.xyz | yes | 200 | parked advertising page |
| cineby.bond | yes | 200 | landing page linking other cineby domains |
| cineby.watch | yes | 200 | landing page |
| cineby.site | yes | 200 / 403 challenge | unknown behind the challenge |
| cineby.today | yes | connect timeout | dead |
| cineby.digital | yes | connect error | dead |

**The change.** The API host is the plugin's one dependency, so it is the plugin's domain now (`_domains = ["db.videasy.net"]`, `base_url` the API base): `search()` calls `_verify_domain()` first, which sends `HEAD` to the API host once per process, takes an answer of any status for reachable and a transport failure (the name does not resolve, no connection, a non-public address, a timeout) for `PluginUnreachableError` (`cineby_api_unreachable`), checked again by the next search; the health monitor probes `base_url`, so its verdict and the plugin's agree. The result links name the site's current domain (`_SITE_URL`, `https://www.cineby.at`); the parked and dead mirrors left the list. The plugin's host was not updated: without the site there is no current API host to read, and the next look is the page's own requests once `cineby.at` resolves again (the search box or `/search` of the real site, the `/3/search/multi` call's host).

**Series probe, before and after.** Before: `| cineby | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |`, 14 requests at 0 s each, every one `private_address_refused` and `cineby_fetch_error`, counted as searches without results. After: `| cineby | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | PluginUnreachableError 14 |`, 14 `cineby_api_unreachable` lines with `error=PrivateAddressError`, no fetch. In a round the plugin is marked unreachable (`stremio_plugin_unreachable`, the record's `unreachable` count) and skipped until the health monitor finds the API host answering.
**Production (2026-10-10, 12:00 to 12:30 UTC, read-only through `scripts/prodctl.py`).** The plugin is disabled by config there: the 24 h log holds three cineby lines, all from the start at 10:27:28 UTC (`config_effective`, `plugin_loaded`, `plugin_disabled_by_config plugin=cineby`), no search, and the plugin record has no cineby entry (`probe domains --plugins cineby --history`: "cineby: no record"). The audits' zeros were the dev container's, never production's. The production container's resolver does not know the API host either: `db.videasy.net`, `www.cineby.at` and `cineby.at` fail with "No address associated with hostname" (a DNS-only probe file run by path, one `getaddrinfo` per name, outcomes without addresses), `videasy.net` and `cineby.gd` resolve to public addresses. That resolver has no search domain, so there is no private address and no guard refusal: after the deploy `_verify_domain()` meets a connect error instead and reports unreachable the same way. The deployed code's domains probe (still the eight former site domains) sees them as the dev container does, none of them the site: `cineby.gd` and `cineby.app` resolve and accept the connection but the `HEAD` and `GET` end in `ConnectError`, `cineby.xyz`, `cineby.bond` and `cineby.watch` answer 200 behind Cloudflare, `cineby.site` 200 and then 403 (the challenge), `cineby.today` fails at DNS, `cineby.digital` at the connection. The change alters production only once the maintainer enables the plugin there: then it reports unreachable instead of answering nothing.

## Official domain lists (2026-10-11, row 69)

**Question.** Which of the 42 plugins' sites publish an official list of their addresses (N24, the maintainer's decision of 2026-10-11: a site that publishes one is read and managed by its plugin)?

**Survey.** Dev container only, nothing in production. Each plugin's first answering `_domains` entry was fetched with httpx (browser User-Agent), and its anchors were searched for a list link (`domain`, `adresse`, `mirror`, `alternativ`, a `<name>.domains` host). The 16 sites that answered 403 or a challenge page were read again in Patchright under xvfb, after the challenge cleared. Only landing pages were read; a list linked from a deeper page would be missed.

| Plugin | Read | List |
|---|---|---|
| aniworld | httpx | `https://aniworld.domains/`: a Vite single-page app; its bundle names `[{name, url, description}]` entries: aniworld.to, aniworld.cc and a proxy IP literal |
| filmo | httpx | `https://filmo.domains/`: a Vite single-page app; its bundle's `alternativeUrls: [{label, href}]` names filmo.to |
| sto | httpx | `https://serien.domains/`: a Vite single-page app like aniworld's; it names serienstream.to, serienstream.cx and a proxy IP literal |
| burningseries | httpx | none readable: the page text names `burningseries.domains`, which refused the connection |
| hdfilme | httpx | none: an on-site page `hdfilme-alternative.html` names the current address, but it is on the site itself, and hdfilme.cafe already redirects to hdfilme.ceo |
| kinox | httpx | none: an on-site page `Alternative.html` ("Backup Domains") names the mirrors that `_domains` already holds (kinox.to, kinos.to, kinoz.to) |
| hdsource | httpx | none: a blog post about the site's domain block, no list |
| cine, crawli, ddlspot, einschalten, filmpalast, fireani, haschcon, hdworld, kinoking, megakino, movie2k, nima4k, serienjunkies, streamcloud, streamkiste, warezomen | httpx | none |
| animeloads, byte, dataload, ddlvalley, jjs, moflix, movieblog, myboerse, nox, scnlog, scnsrc | Patchright | none ("Mirror" and "Alternative" hits are release names and categories) |
| boerse, filmfans, kinoger, mygully, serienfans | Patchright, challenge not cleared | unknown |
| cineby, megakino_to, movie4k | no answer | unknown: megakino_to and movie4k are unreachable (step 29); cineby's API host resolves to a private address here (row 48) |

**Mechanism.** `HttpxPluginBase` reads a site's list through `infrastructure/plugins/domain_list.py`. A plugin names `domain_list_url` and `domain_marker`, a CSS selector for an element that only the site's own landing page shows (here, its link to the list).
- When no `_domains` entry answers (not even with an error page or a challenge), the domain check reads the list through the plugin's client, so the shared client's `PrivateAddressGuard` refuses LAN targets.
- The list is read from its page and the scripts the page loads from the list's own host. A host counts when it appears as a `url:`/`href:` value or in an `<a href>`. IP literals are dropped, and at most 20 hosts are kept.
- The hosts are cached as `<plugin>:domain_list:v1` for 24 hours. A list that fails to load or names no host is cached as `[]` for one hour, with one `plugin_domain_list_failed` warning. Without an injected cache the list is skipped.
- Each listed host beyond `_domains` is fetched. The first one that answers below 400 and shows the marker is pinned like a `_domains` entry and logged as `plugin_domain_list_host` (plugin, host). The digest names it under Plugins, so the maintainer decides whether it goes into `_domains`.
- The Playwright base is unchanged: no Playwright site publishes a list.

**Live proof** (13:56 UTC, dev container, `PYTHONPATH=src`, the guarded client as the app builds it). Each list was read, each listed host fetched and checked for the marker, and then the domain check ran with `_domains` replaced by a dead `<plugin>-dead.invalid`.

| Plugin | Listed | Verdicts | Check with dead `_domains` |
|---|---|---|---|
| filmo | filmo.to | filmo.to 200, marker | pinned filmo.to |
| aniworld | aniworld.to, aniworld.cc | both 200, marker | pinned aniworld.to |
| sto | serienstream.to, serienstream.cx | both 200, marker | pinned serienstream.to |

aniworld.cc and serienstream.cx serve their sites and were not in `_domains`. The maintainer said yes (row 74), and both now follow the first domain: with it dead (a `.invalid` host), the domain check pinned aniworld.cc and serienstream.cx (dev container, 2026-10-11). The marker appears on every captured page of the three sites except filmo's redirect interstitial (`test_the_marker_is_on_the_sites_own_pages`).

## kinox keeps no hoster link (2026-10-11, row 73)

**Question.** Production's digest of 14:01 UTC showed kinox with 52 searches on 2026-10-11, 0 results, and 29 checks, none of them unreachable. At 13:14 UTC (Interstellar) it logged `kinox_domain_found`, then `kinox_detail ... hosters=2`, then `kinox_no_hoster_links`. Is the mirror call failing, or `_resolve_own_links()`?

**Finding** (dev container, `PYTHONPATH=src`, Interstellar, 14:05 to 14:14 UTC):
- The domain check, search, detail page and mirror call all work. `www22.kinox.to` answers; the search finds `/Stream/Interstellar.html`; the detail page lists Vinovo and Dood.to.
- Each `/aGET/Mirror/...` answers 200 with JSON (`HosterHome`, `HosterName`, `Replacement`, `Stream`). Its `Stream` holds an iframe on kinox's own `/redirect/<32-character hash>?t=<time>`, which `_fetch_mirror_url()` reads correctly.
- The redirect never leaves the site. It answers 200 without a `Location`: without a `Referer`, a 1.2 KB "Please wait, checking your request..." page; opened from the movie page, a 31 to 40 KB page with a POST form. That page asks for a human captcha: "Captcha eingeben", "Schritt 1 von 3: Bitte klicke auf einen Hund" (three steps of image clicks, the prompt changing per load), with hidden honeypot fields (email, name, website and others).
- So `_resolve_redirect()` finds no off-site target, `_resolve_own_links()` drops every link, and the plugin logs `kinox_no_hoster_links`.
- Site analysis in a real browser (playwright-mcp) confirmed it: a click on the Vinovo hoster loads the player iframe on `/redirect/<hash>`, and it shows "Schritt 1 von 3: Bitte klicke auf ein Dreieck und einen Pfeil, um fortzufahren".

**Decision.** No change to `plugins/kinox.py`. The site gives no hoster link without a human captcha, which is the row's stop rule. The CHANGELOG has listed it under KNOWN_ISSUES since 2026-10-01 (the same captcha). kinox stays enabled (the maintainer's rule), and its record keeps saying so: searches with 0 results, the site reachable.

**Live proof.** The plugin smoke test (`poetry run pytest -m live -k kinox tests/live`) fails with "returned 0 results", as expected while the captcha stands. No pages were captured as fixtures, because there is no parser change to test.
