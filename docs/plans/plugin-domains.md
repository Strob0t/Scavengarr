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
