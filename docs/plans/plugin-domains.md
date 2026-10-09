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
