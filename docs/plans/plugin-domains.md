# Plugin Domains: megakino_to and movie4k (step 29)

**Question.** Step 21's audit after the filter change (`docs/plans/series-episodes.md` → After) found megakino_to and movie4k unreachable: read timeouts on every domain, 13 and 14 of 14 searches ending in `PluginUnreachableError`. Both front one `DataApiPluginBase` backend. Before anyone hunts new domains the maintainer needs to know which it is: dead sites, a VPN exit the sites block, or a slow backend.

**Probe.** `scripts/probes/domains.py [--plugins a,b] [--get-timeout S] [--history]` checks every `_domains` entry of the named plugins (default: every stream plugin) from where it runs: DNS resolution, a TCP connect to port 443, the domain check's `HEAD https://<domain>/` (the plugin's own client and headers, its 5 s timeout), then a `GET` of the same page (15 s unless `--get-timeout`), each step with its time. A domain *answers* by the domain check's rule (`_site_answers` of `httpx_base.py`, reused, not copied): below 400, an error page, or a challenge page. With `--history` it prints the plugins' long-term record from the app on the same host (`GET /api/v1/stats/plugins`; on a build before that endpoint it reads the `plugin_history:v1` snapshot from the cache backend). Hosts only, no addresses, paths or tokens; it changes nothing.

```bash
PYTHONPATH=src poetry run python -P scripts/probes/domains.py --plugins megakino_to,movie4k --get-timeout 60   # dev container
.venv/bin/python scripts/prodctl.py probe --timeout 600 domains -- --plugins megakino_to,movie4k --get-timeout 60 --history   # the Pi
```

## Reading (2026-10-07)

Dev container = the home connection; Pi = the VPN exit (production, `staging` before step 21). Times are one run each.

| Plugin | Domain | Where | DNS | TCP 443 | HEAD (5 s) | GET (60 s) | Answers |
|---|---|---|---|---|---|---|---|
| megakino_to | megakino.org | dev | 2 v4 / 2 v6 | ok, 23 ms | ReadTimeout | **522** `[cloudflare]` after 19.5 s | no |
| | | Pi | 2 v4 / 2 v6 | ok, 37 ms | ReadTimeout | **522** `[cloudflare]` after 19.6 s | no |
| megakino_to | megakino.to | dev | 2 v4 / 2 v6 | ok, 24 ms | ReadTimeout | **522** `[cloudflare]` after 19.5 s | no |
| | | Pi | 2 v4 / 2 v6 | ok, 41 ms | ReadTimeout | **522** `[cloudflare]` after 19.6 s | no |
| movie4k | movie4k.sx | dev | 2 v4 / 2 v6 | ok, 22 ms | ReadTimeout | **522** `[cloudflare]` after 19.5 s | no |
| | | Pi | 2 v4 / 2 v6 | ok, 29 ms | ReadTimeout | **522** `[cloudflare]` after 19.4 s | no |
| movie4k | movie4k.ag | dev | 1 v4 | **TimeoutError** 5 s | ConnectTimeout | ConnectTimeout (60 s) | no |
| | | Pi | 1 v4 | **TimeoutError** 5 s | ConnectTimeout | ConnectTimeout (60 s) | no |
| movie4k | movie4k.stream | dev | 2 v4 / 2 v6 | ok, 26 ms | ReadTimeout | **522** `[cloudflare]` after 19.6 s | no |
| | | Pi | 2 v4 / 2 v6 | ok, 36 ms | ReadTimeout | **522** `[cloudflare]` after 19.7 s | no |

With the 15 s default both `HEAD` and `GET` end in `ReadTimeout` on every domain (the audit's picture); the 60 s run shows what Cloudflare answers after about 19.5 s: `522 Connection timed out`, the edge's own error for an origin it cannot reach. The `answers` column stays "no" on the Pi too: the production build predates `_site_answers`, so the probe falls back to the plain rule there (`? (no rule)` for the 522 rows in the raw output, `no` for the connect failures), and 522 is above 400 and no challenge under either rule.

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

**Probe.** `scripts/probes/domains.py --plugins fireani,kinox,kinoking --get-timeout 30` in the dev container (the home connection) and on the Pi (`prodctl.py probe --timeout 600 domains -- … --history`, the VPN exit), both within one sitting on 2026-10-09; kinoking and fireani repeated twice more on each side. The `HEAD` column is the domain check itself (5 s). The record is the app's own (`GET /api/v1/stats/plugins`, step 16 is deployed).

| Plugin | Domain | Where | DNS | TCP 443 | HEAD (5 s) | GET (30 s) | Answers |
|---|---|---|---|---|---|---|---|
| fireani | fireani.me | dev | 1 v4 | **TimeoutError** 5 s | ConnectTimeout | ConnectTimeout | no |
| | | Pi | 1 v4 | **TimeoutError** 5 s | ConnectTimeout | ConnectTimeout | no |
| kinox | www22.kinox.to, ww22.kinox.to, www22.kinos.to, ww22.kinos.to, www22.kinoz.to, ww22.kinoz.to, www20.kinox.to, www15.kinox.to, www.kinox.to (all 9) | dev | 2 v4 / 2 v6 | ok, 25–52 ms | ReadTimeout | **522** `[cloudflare]` after 19.6–19.9 s | no |
| | | Pi | 2 v4 / 2 v6 | ok, 33–57 ms | ReadTimeout | **522** `[cloudflare]` after 19.6–20.0 s | no |
| kinoking | kinoking.cc | dev (3 runs) | 2 v4 / 2 v6 | ok, 25–36 ms | 200 in 0.34, 0.35, 1.2 s | 200 in 0.2–0.7 s | yes |
| | | Pi (3 runs) | 2 v4 / 2 v6 | ok, 30–34 ms | **ReadTimeout (5.1 s), 200 in 5.08 s, ReadTimeout (5.2 s)** | 200 in 4.1, 4.7, 8.5 s | yes |

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
- **kinoking: alive, slow from the VPN exit.** From home the domain check answers in 0.3 to 1.2 s; from the Pi the same `HEAD` takes 5.1 to 5.2 s and fails the 5 s check in two of three runs, the `GET` 4 to 8.5 s. The 22 % unreachable marks are those timeouts, spread evenly over three days, and the plugin returns results when it gets through (2 on 10-08, 2 on 10-09). Not a block (every answer is a 200), not a challenge: Cloudflare's path from the Pi's exit to kinoking's origin is slow, right at the check's timeout.

Nothing in `_domains` or the plugins was changed. The decision is the maintainer's: fireani and kinox are candidates for disabling until the record shows a return (kinox's wall was already a reason); kinoking is a question of the domain check's 5 s timeout from the VPN exit, not of the site.
