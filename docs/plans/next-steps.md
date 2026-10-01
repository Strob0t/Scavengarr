[← Back to Index](../features/README.md)

# Plan: Project Status and Next Steps

**Status:** In progress. Proposed 2026-09-30; the CI part of item 2 is done (2026-09-30). Items 1 and 6 wait for the maintainer; items 2–5 can start without further input.
**Priority:** High for 1–3 (the work since February does not reach the Stremio setup, nothing guards `staging`, a quarter of the tested plugins return nothing), medium for 4–5.
**Source:** Assessment after the [code review fixes](code-review-fixes.md) on 2026-09-29: repository and GitHub state, dependency check, the full live smoke run (`pytest -m live`), a coverage run and a probe of the production instance.

## Summary

The code is in good shape; delivery and operation are not. The tests are extensive and green, the architecture is clean, the docs match the code, and the live Stremio end-to-end test plays streams. But nothing has been released since February, production is down, no CI runs the tests, and plugin health is only checked by hand.

| # | Item | Who | Size |
|---|---|---|---|
| 1 | Production up again, release v0.2.0 | maintainer (host access, merge approval) | S |
| 2 | CI, monitoring, scheduled live run | CI: agent; monitoring and schedule: maintainer's host | S |
| 3 | Plugin round for Stremio | agent | M |
| 4 | Type checking in the gate | agent | M (unknown until counted) |
| 5 | DLE base, real-page fixtures, dependency updates | agent | M each |
| 6 | Decisions | maintainer | — |

## Snapshot (2026-09-29)

| Area | State |
|---|---|
| Code | `src/` ~21k lines, 41 plugins ~18k lines, tests ~61k lines; largest modules ~700–800 lines (megakino, animeloads, sto, config schema, Stremio stream use case) |
| Tests | 4547 tests pass (from 3611 unit, 172 E2E and 37 integration test functions), 94 % line coverage of `src/` and `plugins/`; pre-commit green |
| Live | Stremio end-to-end passes (Oppenheimer and Breaking Bad S01E01 each play a stream); 20 of 35 plugin smoke tests return results ([details](#live-smoke-run-2026-09-29)) |
| Release | `main` is at `a08ed04` "test claude" (2026-02-09), `staging` is 536 commits ahead; version `0.1.0`; ~860 CHANGELOG lines under "Unreleased", v0.1.0 still dated `2025-XX-XX` |
| Production | `https://scavengarr.lan` answers 502 on every path (Caddy up, backend down; also earlier that day) |
| Automation | No CI (no `.github/workflows/`; added 2026-09-30, see item 2); pre-commit runs ruff and hygiene hooks only; basedpyright runs in the editor only (`.vscode/settings.json`, `standard`) |
| Dependencies | 17 top-level packages outdated; major steps: guessit 3.8 → 4.4, redis 7 → 8, cryptography 46 → 50; also fastapi 0.128 → 0.142, uvicorn 0.40 → 0.54 |
| Process | OpenSpec unused since January: `add-config-system` (75 of 87 tasks) and `add-plugin-loader` (67 of 186) never archived, no `openspec/specs/`; plans live in `docs/plans/` |
| Repository | public, 0 stars, 0 forks; `docs/plugins.md` names every site |

### Live smoke run (2026-09-29)

From the dev container, `poetry run pytest -m live`: 24 passed, 10 failed, 7 skipped in 13 min.

| Result | Plugins | Cause |
|---|---|---|
| Results | animeloads, aniworld, burningseries, byte, dataload, einschalten, filmpalast, fireani, haschcon, hdworld, megakino, moflix, movie2k, nima4k, nox, scnlog, sto, streamcloud, streamkiste, warezomen | — |
| Broken | cine | the search finds 38 titles, the link lookup returns nothing for any of them (`cine_no_links`); same on a rerun |
| Broken upstream | kinox, hdfilme | KNOWN_ISSUES: redirect loop on a "Verifizierung" page; the site's own search fails with a PHP error |
| Cloudflare Turnstile | kinoger, serienfans, ddlspot, ddlvalley fail; scnsrc, filmfans skip (network error) | KNOWN_ISSUES: they pass only with a headful browser; the run was headless |
| Unreachable from this network | megakino_to, movie4k | TCP timeout on IPv4 while other Cloudflare sites answer; the failure comes before any parsing, so it is not the category change of `2495d3a` |
| Unreliable | kinoking | search timeout, network error on the rerun; a direct request got an answer in 0.4 s |
| Not checked | myboerse, mygully (no credentials), boerse (network error) | — |
| No live test | cineby, crawli, hdsource, jjs, movieblog, serienjunkies | also: both XFS tests in `test_resolver_live.py` run nothing (empty parameter set) |

## 1. Bring `staging` to production (maintainer)

- **Start production again** with the current code: `docker compose up -d --build` in the checkout on the host (192.168.88.2). Check that `/api/v1/healthz` answers 200 and that a Stremio stream request returns streams.
- **Release v0.2.0** (merge only on explicit request, AGENTS.md §1): version `0.1.0` → `0.2.0` (minor: new features since February), "Unreleased (staging)" becomes "v0.2.0 - <date>", the `2025-XX-XX` placeholder of v0.1.0 gets filled in, PR `staging` → `main`, merge, sync back. The public `main` then no longer shows the February state.

## 2. Safety net

- **CI** (agent) — **Done 2026-09-30:** `.github/workflows/ci.yml` runs `poetry install --with dev`, `pre-commit run --all-files` and `pytest` without live tests on every push to `staging`, on pull requests to `staging` (where CONTRIBUTING.md sends contributors) and `main`, and on demand. Free for public repositories. No non-live test needs a real browser: in a clean clone with an empty `PLAYWRIGHT_BROWSERS_PATH`, a fresh `HOME` and no `SCAVENGARR_*` variables, install took 25 s, pre-commit 40 s and the 4547 tests 62 s, all green.
- **Monitoring** (maintainer's host): the app already has `/api/v1/healthz`, `/api/v1/readyz`, `/api/v1/torznab/{plugin}/health` and `/api/v1/stats/plugin-scores`. A monitor on the host (for example Uptime Kuma) would have reported the 502 right away.
- **Scheduled live run** (maintainer's host): `pytest -m live` once a week from the home network (about 13 min), with the result as a notification. Not on GitHub-hosted runners: they use datacenter IPs, which Cloudflare-protected sites challenge harder, so the results would not show what the home network sees. No self-hosted runner either, since pull requests from forks of a public repository could run code on it.

## 3. Plugin round for Stremio

In order of value for the Stremio use case (German films and series, new releases, anime; answer budget in [stremio-latency.md](stremio-latency.md)). Each fix follows [plugin-repair.md](plugin-repair.md): site analysis with `playwright-mcp` first, then test-first.

1. **cine**: find out why the link lookup is empty for every title (site change or a new request format). **2026-10-01:** the links API now needs the language (`lang=1` German, `2` English); fixed. Every `/out/` link opens a reCAPTCHA gateway, so cine serves downloads only.
2. **Turnstile group** (kinoger, serienfans, ddlspot, ddlvalley, scnsrc, filmfans): run their smoke tests headful (`xvfb-run -a`, `playwright.headless: false`, `playwright.browser_fallback: true`) and fix what still fails.
3. **megakino_to, movie4k**: check from another network whether the sites are down, blocked here or moved (new `_domains`). **2026-10-01:** down, not blocked: a real browser gets Cloudflare 522 (origin unreachable) on megakino.to and movie4k.sx; the circuit breaker keeps them out of most requests.
4. **kinoking**: find out whether the site is slow or the plugin timeout too short. **2026-10-01:** the site: movie pages take 12–17 s to the first byte (series pages 1 s), so its movies never fit the 10 s search budget; the circuit breaker now tracks plugins per category, which keeps kinoking's movies out of movie requests while its series stay.
5. **Test gaps**: smoke entries for cineby, crawli, hdsource, jjs, movieblog and serienjunkies; give the XFS live tests real URLs or remove them.
6. **With credentials** (`SCAVENGARR_<PLUGIN>_USERNAME` / `_PASSWORD`): myboerse, mygully and boerse, including the XenForo thread links that guests do not see.

The plugins are independent, so the round can run in parallel, one agent per plugin.

## 4. Type checking in the gate

AGENTS.md requires fully typed code, but only the editor checks it.

1. Move the basedpyright settings from `.vscode/settings.json` to `[tool.basedpyright]` in `pyproject.toml`, so the editor and the command line use the same rules.
2. Count the findings in `standard` mode; fix `src/` first, then `plugins/`.
3. Add basedpyright to pre-commit and CI once it is clean.

**Done 2026-10-01:** basedpyright runs in pre-commit (and so in CI); `src/` and `plugins/` have 0 errors. Step 1: `basedpyright` dev dependency, `[tool.basedpyright]` with `typeCheckingMode = "standard"` for `src` and `plugins`). First count: 120 errors, 57 in `src/` (infrastructure 37, application 13, interfaces 7) and 63 in `plugins/`; most are `reportArgumentType` (44), `reportAssignmentType` (18) and `reportIncompatibleMethodOverride` (17). Four of them were bugs: the Torznab probe's GET fallback raised `TypeError`, a Playwright plugin's timeout override had no effect, ddlvalley and scnsrc were registered as German, and a failed standalone Playwright start referenced an unbound variable.

## 5. Structure and maintenance

- **DataLife Engine base**: hdfilme, kinoger, megakino, streamcloud and streamkiste all use the DLE search (`do=search`, `subaction=search`). One `DlePluginBase` in `infrastructure/plugins/`, like `DataApiPluginBase` and `XenForoPluginBase`, so a fix lands once.
- **Parser tests on real pages**: the code review found crawli (base64-encoded pages), myboerse (missing CSRF token) and the XenForo node filter broken live while every unit test passed, because the fixtures reflect what the parser expects. Store the pages of a live run as fixtures and test the parsers against them (see the open fixture items in [integration-tests.md](integration-tests.md)).
- **Dependencies**: patch and minor updates in one batch; guessit 4 on its own (release-name parsing feeds titles, seasons and categories), redis 8 and cryptography 50 on their own. Each with the full suite and the live Stremio end-to-end test. **2026-10-01:** the batch is in (fastapi 0.142, uvicorn 0.54, respx 0.23, starlette 1.7, rebulk 6, pytest-asyncio 1.4; suite and live Stremio probe green). Since then structlog 26, cryptography 50, redis 8, ruff 0.16, pydantic-settings 2.15 and python-dotenv 1.2. Open: guessit 4.
- Open items that stay in their plans: [search-caching.md](search-caching.md) (invalidation endpoint, metrics, TTL test), [integration-tests.md](integration-tests.md) (pipeline, CrawlJob HTTP and TTL tests).

## 6. Decisions for the maintainer

| Question | Recommendation |
|---|---|
| Keep the repository public? | Private, if it is for personal use: nobody follows it (0 stars, 0 forks), and a takedown notice against the sites named in `docs/plugins.md` would hit the whole repository. |
| OpenSpec | Archive both changes and drop the OpenSpec block from AGENTS.md: `docs/plans/` is the process in use, and the block costs context in every agent session. |
| Torznab `cat=` with several ids | Pass all ids to the plugins (a port change); today `cat=5070,5000` becomes a strict 5070 request ([open observations](code-review-fixes.md#open-observations)). |
| DDL hosters in Stremio | Decide whether validate-only DDL hosters may appear as Stremio streams; today the playback check drops them all. |
| Forum credentials | Provide them as `SCAVENGARR_*` variables if the forum plugins should be checked live. |
| Persist `~/.claude` in the dev container | Yes: a named volume on `/home/vscode/.claude` keeps Claude Code's login, sessions and memory across rebuilds (today they live in the container filesystem and are lost). The Dockerfile must create the directory owned by `vscode`, otherwise Docker creates the mount point as root. |

## Next session: live tests with stremio.lan

Goal: run the Stremio use case against the maintainer's Stremio Web instance (`https://stremio.lan`) with a Scavengarr started in the dev container.

- The dev container publishes port 7979 on all host interfaces (AGENTS.md §9); start the server with `--host 0.0.0.0 --port 7979`. The manifest is at `/api/v1/stremio/manifest.json`.
- `https://stremio.lan` is an HTTPS page, so the browser blocks plain-HTTP addon and stream URLs (mixed content). The instance needs an HTTPS route, for example a Caddy site on 192.168.88.2 that proxies to `<workstation>:7979`.
- Stream and proxy URLs are built from `request.base_url`. uvicorn trusts `X-Forwarded-*` headers only from `127.0.0.1` by default, so behind Caddy on another host start the server with `FORWARDED_ALLOW_IPS=<caddy-ip>`; otherwise the URLs come out as `http://`.
- Measure against the budget in [stremio-latency.md](stremio-latency.md): time until the streams show and the share of streams that play, for German films, series, new releases and anime.
- Claude Code's memory is not part of the repository: after the rebuild restore it from the gitignored copy with `mkdir -p ~/.claude/projects/-workspaces-scavengarr/memory && cp .claude/memory-backup/* ~/.claude/projects/-workspaces-scavengarr/memory/`.
