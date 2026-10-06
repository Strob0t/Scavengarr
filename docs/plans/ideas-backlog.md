[← Back to Index](../features/README.md)

# Ideas Backlog (Advisor Review)

**Status:** Proposal (2026-10-06). Decisions go into the table at the end; items that are built move to their own plan or feature doc.
**Source:** A review of the repository state and of production on 2026-10-06 (`scripts/prodctl.py`: `state`, `metrics` and 7 h of logs of the Raspberry Pi behind the VPN, 93 stream requests since the container start 6 h earlier), read against the open items in [next-steps.md](next-steps.md) §6, [optimization-options.md](optimization-options.md) (19 options) and [code-review-fixes.md](code-review-fixes.md). Only findings and ideas that are **not** in those documents are listed in full; the rest is referenced.

## Snapshot (2026-10-06)

| Area | State |
|---|---|
| Delivery | `staging` is 134 commits ahead of `main` (v0.2.3, 2026-10-04); the CHANGELOG's "Unreleased" section has 264 lines; CI green. Production builds from `staging` (the Pi's own Dockerfile, `ADD <git>#ref`); `scavengarr_build_info` reports `version="0.2.3"` and no commit, so the deployed commit is not visible. |
| Production, stream requests (6 h) | 93 requests: 27 first answers (search) in 13.4 s on average, 49 from the search cache in 0.07 s, 9 joined a running search (9.9 s), 8 stale (1.6 s); 4.1 streams per answer; every title had a stream (`stremio_request_total{outcome="streams"}` only). Playback ran: 281 segments and 58 variant playlists through the HLS proxy. 111 master playlists were refused to ffmpeg (`hls_proxy_converter_refused`), 96 of them within one minute for 48 stream ids, two each: a play check, not playback. |
| Production, plugins | 13 plugins search. kinoger: 15 of 27 searches failed, 19.6 s on average (the stealth browser's contention, open in [round5-measures.md](round5-measures.md)). kinoking 11.3 s (6 failures), s.to 4.9 s, moflix 3.9 s, hdfilme 3.6 s (58 results, the mirror group's pick), filmpalast 2.0 s, movie2k 1.7 s, megakino 1.7 s, einschalten 1.6 s (94 results, the most), fireani 1.2 s, aniworld 0.9 s. kinox: 35 searches, 0 results (27× `kinox_no_hoster_links`, the verification wall), 3.6 s each. haschcon: 1 result in 35. megakino_to and movie4k: 30× `unreachable` each, so the health check keeps them out of the searches. |
| Production, hosters | Resolutions: VOE 93 (1.2 s on average), `direct` 81 (0.3 s, **all 81 dead**), DoodStream 55, StreamUp 27, VidHide 24 (2.8 s), Filemoon 24 (5.1 s), mixdrop 22, VEEV 21, FireStream 17, Vinovo 16, Dropload 16 (7.6 s), Vidmoly 15, SuperVideo 12, Vidsonic 11, FSST 11, Vixeo 10 (8.5 s). Breakers open: Dropload (70 links skipped), Filemoon (55), SuperVideo (33), Vixeo. |
| Production, logs | 10,157 lines in 7 h: 4,860 (48 %) from the `httpx` logger (`HTTP Request: GET https://… "HTTP/1.1 200 OK"`, full URL with CDN tokens), 1,158 `http_request` access lines (query values masked), 287 warnings, 1 error. |
| Code | Largest modules: `application/use_cases/stremio_stream.py` 928 lines (most fixes since 2026-10-03 landed there), `infrastructure/config/schema.py` 816, `plugins/animeloads.py` 777, `interfaces/composition.py` 747, `hoster_resolvers/xfs.py` 734, `plugins/sto.py` 726, the Stremio router 695. Real-page fixtures cover 10 plugins; of the 13 in the Stremio fan-out, einschalten, fireani, haschcon, kinox and moflix have none. |
| Process | OpenSpec is in use again (`add-observability`, 18 of 18 tasks, 2026-10-05); `add-config-system` (75 of 87) and `add-plugin-loader` (67 of 186) are untouched since January and not archived. |

## New findings

Each with its evidence; the ideas below refer to them.

1. **Third-party request logs dominate the log.** `configure_logging` sets every stdlib logger to the app's level (`infrastructure/logging/setup.py`, the loop over `loggerDict`), so httpx's INFO line per request is written too: 48 % of production's log lines, each with the full URL, including CDN tokens that the app's own access log masks since 577e7cd. On the Pi the log lives on the SD card.
2. **The stream's own quality and size are read and thrown away.** `check_playable` (`hoster_resolvers/_verify.py`) fetches the first KiB of every resolved stream with a `Range` header: for HLS that is the master playlist with its `#EXT-X-STREAM-INF … RESOLUTION=1920x1080` lines, for a file the `Content-Range` header carries the total size. Neither is kept: no resolver sets a `StreamQuality` other than `UNKNOWN` (none of the 33 resolver modules), and `stream_builder` takes the quality from the plugin result alone (release name, site badge, link label). Streaming sites without release names therefore show no quality line in Stremio and rank as unknown.
3. **Hosters without a resolver are a blind spot.** A link whose hoster has no resolver is probed with `HEAD` (`HosterResolverRegistry._probe`, telemetry label `direct`); in 6 h all 81 probes were dead (plus 54 cached dead answers), i.e. embed pages. The hoster's name is logged at DEBUG only (`hoster_probe_*`), so neither the logs nor the metrics say which hosters the plugins deliver that nobody resolves, or which plugin emits them.
4. **No build identity in production.** The info metric and `/health` carry the package version only; production runs `staging` (which commit is unknown without a shell). Every production measurement since 2026-10-03 had to infer the commit from behaviour.
5. **kinox costs without yield.** 35 searches, 0 results, 3.6 s and 6–14 requests with 503 retries each, every link-out behind the site's verification wall (KNOWN_ISSUES). The health check does not catch it (the site answers), the breaker does not either (an empty answer neither counts nor resets; option 2 in optimization-options.md).
6. **The deployment pipeline is hand-made.** The Pi builds the image from GitHub with its own Dockerfile (not in the repository; a copy in `.cache/live/pi/Dockerfile`), ships the plugins inside the image while `docker-compose.yml` mounts them, seeds `config.yaml` once (the February config ran for days, see [stremio-latency.md](stremio-latency.md)), and watchtower cannot update a locally built image. Each production test round began with "please rebuild".

## Ideas

Effort: XS under an hour, S a few hours, M a day or two, L more. Recommendation: **Do** (clear value, low risk), **Measure/Probe** (value depends on a number nobody has), **Ask** (the maintainer's call), **Later**, **No**.

### Quick wins from production

| # | Idea | Value | Effort | Risk | Rec. |
|---|---|---|---|---|---|
| I1 | Third-party loggers (`httpx`, `httpcore`) at WARNING unless the app's log level is DEBUG (finding 1) | Half the log volume; no CDN tokens in the log | XS | None: DEBUG still shows them | Do |
| I2 | Quality and size from the stream itself (finding 2): `check_playable` parses the highest `RESOLUTION` of the sniffed playlist head (read up to 4 KiB for it) and the `Content-Range` total of a file into the `ResolvedStream`; the builder and sorter take them when the site gave none | Quality line and quality ranking for every HLS stream (most streaming sites); file sizes in the description | S–M | A master playlist longer than the sniffed head, a variant-only playlist (no `RESOLUTION`): stays unknown as today | Do |
| I3 | Unknown-hoster inventory (finding 3): an INFO event `hoster_without_resolver` with hoster name and plugin, and a per-hoster counter in `/api/v1/stats/metrics` (JSON, not a Prometheus label: the set is open) | Shows which resolver to build next and which plugin emits dead link types | XS–S | None | Do |
| I4 | Build identity (finding 4): `commit` and `built` in `scavengarr_build_info`, `/health` and the startup log, from a build argument (`SCAVENGARR_COMMIT`; `Dockerfile.prod` and the Pi's Dockerfile set it) | Ends guessing which commit production runs | XS | None | Do |
| I5 | kinox (finding 5): (a) `plugins.overrides.kinox.enabled: false` in production (config), (b) the plugin ends the search as failed when the first link-out shows the verification wall, so the breaker opens and its half-open probes notice the site's return, (c) yield-based demotion: a plugin with 0 results in N consecutive searches while others deliver runs in the background only (keeps the completeness decided on 2026-10-01) | 3.6 s and 6–14 requests per request today (the first answer is held by the soft deadline anyway, so the gain is load, not latency) | (a) config, (b) S, (c) M | (b) is kinox-specific; (c) changes the fan-out for every plugin | (b) Do, (c) Later |

### Delivery and operations

| # | Idea | Value | Effort | Risk | Rec. |
|---|---|---|---|---|---|
| I6 | Release v0.3.0 now (134 commits, six end-to-end rounds, production runs this code) | `main` and the CHANGELOG show what runs; the version in the metrics means something again | S | None | Ask (merge needs the maintainer) |
| I7 | A published multi-arch image (finding 6): CI builds `linux/amd64` and `linux/arm64` to GHCR on `main` (tags) and `staging` (`:staging`); `Dockerfile.prod` bundles the plugins; the Pi's compose takes `image:` and watchtower updates it at 04:00 as it does the other containers | No source builds on the Pi; every deploy reproducible and visible (I4); the Pi's Dockerfile retires | M | arm64 builds need the `ubuntu-24.04-arm` runners (free for public repositories) or QEMU; the image is as public as the repository (next-steps §6) | Do, after I6 |
| I8 | Alert rules as code (`docker/prometheus-alerts.yml` beside the dashboard): a plugin with 0 results for 24 h while others deliver, first-answer p50 above 20 s for an hour, a hoster breaker open for 6 h, event-loop lag p99 above 100 ms, target down | Finds the next kinox, kinoger or dead site without a session; the February config would have alerted on the first day | S | Needs the Pi's Prometheus scrape job (open since 2026-10-05, maintainer) | Do |
| I9 | Config drift guard: one `config_effective` startup event with the non-default values (timings, concurrency, disabled plugins, no secrets) and a warning for keys the schema does not know | Finding 6's config incident becomes a log line; `prodctl logs --grep config_effective` answers "what does production run with?" | S | None | Do |
| I10 | Persist hoster resolutions and breakers (option 8 extended): `HosterResolverRegistry._result_cache` and the breakers live in memory and die with the process, while the link cache and the search cache already use the cache backend (Redis in production); after a deploy every link resolves again (13 s of Chromium per request with new links, fifth round) | Warm start after deploys and restarts, which I7 makes more frequent | S–M | A stale open breaker after a deploy that fixed the hoster: keep the half-open probes | Do, with I7 |

### Product (Stremio)

| # | Idea | Value | Effort | Risk | Rec. |
|---|---|---|---|---|---|
| I11 | Anime ids: the manifest accepts `tt` and `tmdb:` only; Stremio's anime catalogs (Anime Kitsu, MyAnimeList) open titles as `kitsu:<id>`/`mal:<id>` with absolute episode numbers, so a title opened there never reaches Scavengarr although four anime plugins exist. Mapping through Kitsu's `/anime/<id>/mappings` or the `anime-lists` JSON (kitsu, mal, imdb, tmdb, tvdb; cached), absolute to season/episode through Cinemeta's `videos` of the mapped IMDb id | Anime from the user's focus list reachable from the catalogs people use for it | M–L | Season mapping is the hard part (long-runners, split cours); only worth it if anime is browsed through those catalogs | Ask |
| I12 | Per-install configuration (`/{config}/manifest.json` and a `/configure` page): dub only or dub and sub, excluded hosters or plugins, maximum streams, quality floor | Different devices or people, different lists; the convention of other addons | M | One more surface; the config travels in the URL | Later (one user, one config today) |
| I13 | A "search still running" placeholder stream when the first answer was cut at the soft deadline | Tells the viewer that reopening brings more | S | A non-playable entry in the list; Stremio cannot refresh; with `Cache-Control` (option 7) the reopened list may come from the client's cache | No |

### Code health

| # | Idea | Value | Effort | Risk | Rec. |
|---|---|---|---|---|---|
| I14 | Split `stremio_stream.py` (928 lines) along its three jobs: the search run (deadline, early answer, joins), the resolution (grace, target, background), and the answer (assembly, caching); `application/stremio/` already holds the helpers | The adaptive browser budget and option 13/14 land in a module one can hold in one head; fewer regressions of the kind the review fixed | M | Pure refactor behind 4,500 tests | Do, before the adaptive budget |
| I15 | Real-page fixtures for the Stremio plugins without them: einschalten (the top yielder), fireani, haschcon, kinox, moflix (JSON API) | Site changes show up in the suite, not in production | S each | None | Do |
| I16 | OpenSpec hygiene: archive `add-config-system` and `add-plugin-loader`; keep OpenSpec (it was used for the observability change), revising next-steps §6's "drop it" | Two stale changes gone from every `openspec list` | XS | None | Do |

### Already catalogued elsewhere

Open and still worth their place, not repeated here: optimization-options.md options 3 (predicted-late plugins), 5 (half-open probes to their end), 6 (re-resolvable binge/resume links), 7 (`Cache-Control`), 9 (shorter grace, measure), 13/14 (resolve during the search, lazy links); code-review-fixes.md's open observations (Torznab `cat=` with several ids, DDL hosters in Stremio, console categories, GoFile guest access); the s.to Torznab link-out flood; the adaptive browser page budget for kinoger; next-steps.md §6 (public repository, forum credentials, `~/.claude` volume); the history rewrite of the two network addresses. The rest of the 2026-10-06 code review (GuardedNetworkBackend, `/health` metrics, `Tracing.close`, search cache key, request id in worker threads, HEAD on segments, warezomen, SuperVideo, mirror failover) landed on `staging` the same day (6adbf9f…33a4066).

## Decisions

| Date | Decision |
|---|---|
| 2026-10-06 | Proposed; nothing decided yet. |
