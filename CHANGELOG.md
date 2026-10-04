# Changelog

All notable changes to Scavengarr are documented in this file. Format: version, date, grouped changes. Newest entries first.

---

## Unreleased (staging)

### Perf: Each Release Name Parsed Once
- On the Raspberry Pi guessit, which parses release names, took 25–33% of the Python CPU of a stream request (py-spy in production, `docs/plans/pi-performance.md`).
- The title matcher (title candidates, year), the release parser (quality, language) and the episode filter each parsed the same names, and the next request for the same title parsed them all again.
- `infrastructure/stremio/release_guess.py` parses a name once and caches the result: read-only, the last 4096 names.
- Measured on x86 with 120 results: 3.5 s → 1.0 s for the first request, 3 ms for the same titles again.

### Perf: Connections Stay Open Between Stream Requests
- The shared HTTP client used httpx's defaults: idle connections closed after 5 s, at most 20 kept. One stream request talks to 18–42 hosts, so every pause between two requests closed them all, and the next request paid the TCP and TLS handshakes again, one or two round trips through the VPN each. TLS handshakes were 21% of the Python CPU (`docs/plans/pi-performance.md`).
- Idle connections now stay open for 60 s, up to 100. A connect may take at most 5 s, so a host that does not answer no longer costs the full read timeout (`http.timeout_seconds`).
- `http.http2` (`SCAVENGARR_HTTP_HTTP2`, off by default) offers HTTP/2, so the requests to one host share a connection. It needs `httpx[http2]` (h2). It is a switch because HTTP/2 is not faster per se; production measures it both ways.

### Feat: Event-Loop Lag in the Metrics
- On the Raspberry Pi the Python process was busy for 34–82% of a stream request's wall time (`docs/plans/pi-performance.md`). CPU work on the event loop (parsing, TLS handshakes, logging) delays every callback, and with them the timeouts and deadlines.
- A timer every 0.5 s records how late it fires. `/api/v1/stats/metrics` shows the lag's p50, p99 and maximum over the last 5 min (`event_loop`), and a stall of 250 ms or more is logged as `event_loop_lag`. This is the yardstick for the performance plan.

### Chore: Profile Script for Stream Requests
- `scripts/stremio_profile.py` measures stream requests against a running container. Per request it reports:
  - wall time;
  - CPU seconds of the Python and Chromium processes;
  - httpx requests per host, counted from the JSON log.

  At the end it reports the event-loop lag.
- `--py-spy` adds a CPU profile per category, taken in the container: py-spy runs as root and the container needs `cap_add: [SYS_PTRACE]`.
- `--repeat` adds cold and warm passes.
- Access goes through the Portainer API or the docker CLI. Usage: `docs/plans/pi-performance.md`.

## v0.2.3 - 2026-10-04

Production fixes from a Raspberry Pi behind a VPN: kinoger and moflix pass their challenges once and go on over httpx, s.to's link-out gate is passed (ad layers, Turnstile and ALTCHA), and HLS-proxy streams play in Stremio Web. Each fix was verified on the production instance before the release.

### Fix: moflix Without Results Behind a Stored Clearance
- In production moflix returned 0 results on every search, without an error event. A restarted browser gets the stored clearance cookies (`ClearanceStore`), so it loaded moflix's API without a challenge and got 401 (`stealth_http_error`): the API (Laravel Sanctum) serves only requests that carry its site's Referer, and a page load sends one only in a challenge's reload. The browser fetch returned nothing, httpx never took over the session, and the host stayed in the browser memo for 30 min.
- `StealthPool.fetch_text()` asks a URL that answers the page load with an error (no challenge) once more by in-page `fetch()` from that page, the way the site's own pages call their API. Reproduced at home with a second API load in one browser context (401); with the fix httpx takes over the session and the next searches run over httpx (1.4 s, 0.3 s).

### Fix: s.to Episode Streams in Production (Ad Layers, ALTCHA Gate)
- s.to gave no episode streams in production (`sto_link_gate_unsolved`). The browser's click on the hoster link box never reached it: s.to's ad script lays layers over the page (random class names, after a delay) that take the click, and Playwright waited until its click timeout ("<div …> subtree intercepts pointer events"). At home the layer showed up only now and then, in production on every try.
- `StealthPool.click_through()` lets only its targets take pointer events (a stylesheet that stays until the page closes): the link box and the form of the Turnstile gate the click may bring up. The same layers cover that widget: with the stylesheet removed after the link-box click, production's gate failed with `turnstile_widget_unsolved` (the checkbox click: "<div class=\"edyaqwc\">… intercepts pointer events"), at home now and then. The widget's iframe sits in a closed shadow root and inherits the value of its host; checked in Chromium with such a widget under a layer. Live: Dark S01E01 through the gate in 5.8 s, the next episodes in 0.5–0.7 s.
- With that fix production passed the Turnstile widget (`turnstile_widget_passed`), but no redirect followed (`stealth_click_through_no_target`). For a VPN IP s.to's gate is the tier `turnstile_altcha`: the same form holds an ALTCHA widget whose checkbox is required, so the submit never left the page; at home the tier is `turnstile`. In our browser the widget does not verify on a click (its checkbox stays unticked). `pass_turnstile_widget()` now solves a form's ALTCHA widget before the submit, like the widget does: the page fetches the challenge from the widget's `challengeurl`, `solve_altcha()` (now also the classic format: the number whose SHA-256 of salt + number is the challenge) solves it in a thread, the payload goes into the form under the widget's name, and `form.submit()` sends it (the required checkbox would block `requestSubmit()`); a failure logs `altcha_widget_unsolved`. Verified in production with the new code: Dark S01E06 through the gate in 23.0 s (proof of work 0.3 s on the Pi), the next episodes in 6.4–6.7 s.
- Production's Turnstile widget took 16–70 s on the Pi, and the first wait for the redirect had 1.85 s left after it: a submitted gate now gets up to 10 s for its redirect even past the timeout, and s.to gives the browser 60 s for the gate (`_GATE_TIMEOUT_S`; the pass runs in the background, a Stremio request does not wait for it).

### Perf: httpx Goes On With the Browser's Session After a Challenge
- Production (Raspberry Pi 4 behind a datacenter VPN) got nothing from kinoger: its site challenges the VPN IP, and after one challenge every page of the host went through the browser for 30 min, too slow for the 10 s Stremio budget on a Pi.
- Measured from production's VPN IP: after a 3–4 s browser solve, plain httpx with the browser's cookies and User-Agent got kinoger's pages (HostAdmin WAF) and moflix's API (Cloudflare) in 0.3–0.4 s; curl_cffi (Chrome TLS fingerprint) did no better, so no new dependency.
- `BrowserFetcherPort.session(url)` returns the browser's cookies for a site and its User-Agent (`StealthPool`, `SolverFetcher`, `ChainedBrowserFetcher`). `HttpxPluginBase._fetch_text()` takes them over after a browser fetch, so later requests to the host run through httpx (`{name}_browser_session_adopted`). A challenge within 5 min of a takeover sends the host to the browser for 30 min as before (`{name}_browser_session_rejected`); an expired clearance is solved again.
- Covers every plugin that reads pages with `_fetch_text()` (kinoger, filmfans, serienfans, burningseries, kinox, sto). Docs: `docs/features/python-plugins.md`, `docs/plans/antibot-patchright.md` (Phase 4).
- moflix 1.2.0 runs on httpx instead of the browser: the homepage once for the site's session, then its JSON API with the headers its own app sends (`_fetch_text(..., headers=...)`); a challenged call goes through the browser once. Live in dev: 0.9 s per search after the first (4.3 s with the 2 s solve); before, every search ran in the browser, one at a time.
- A browser solve runs on when the Stremio deadline cuts the request that started it, so the next request gets the session. In production's logs every kinoger solve after a config reseed was thrown away at the 10 s cut (20 s per solve under load), so kinoger never recovered. A failed background solve is logged as `{name}_browser_solve_failed`.

### Fix: HLS-Proxy Streams in Stremio Web
- In Stremio Web every stream through the HLS proxy (VOE, Vidsonic, StreamUp, XFS hosters) ended in error 83, "Video is not supported", whenever the streaming server could not probe it. Stremio Web then reads the stream's content type with a `HEAD` request (stremio-video's `getContentType`), and the proxy answered `HEAD` with 405 without CORS headers.
- `HEAD` on `/api/v1/stremio/proxy/{stream_id}/{path}` now answers like `GET`; the server drops the body.
- `docs/features/stremio-addon.md`: Stremio Web plays `proxyHeaders` streams through a streaming server, so the old "Web: not supported" row is fixed. New notes on self-hosted streaming servers: they must reach Scavengarr's and their own public names (a VPN container's DNS and firewall prevent it), and tsaridas/stremio-docker's nginx answers disguised HLS segments (`…_000.css`) with 404.

## v0.2.2 - 2026-10-03

Reverse-proxy fix: `docker-compose.yml` trusts the proxy's forwarded headers from private networks, so stream proxy and Torznab links use `https://`.

### Fix: http:// Links Behind a Reverse Proxy
- Behind Caddy the production instance built its stream proxy URLs and Torznab links as `http://scavengarr.lan/…`: uvicorn honors `X-Forwarded-Proto` only from 127.0.0.1, and the proxy reaches the container from the Docker gateway. Every playlist and segment request through the HLS proxy then took a 308 redirect first, and a browser would block the links as mixed content.
- `docker-compose.yml` sets `FORWARDED_ALLOW_IPS` to the private networks. Checked: a request from 172.17.0.2 with `X-Forwarded-Proto: https` gets `https://` links, without the header `http://`.

## v0.2.1 - 2026-10-03

Fixes from the first end-to-end test against Stremio Web: Vidsonic streams through the HLS proxy, moflix without its paid player, kinoger behind its new WAF page, kinox without work for episode requests, and a play check script that follows every stream to its media bytes.

### Chore: Play Check Script for Stremio Streams
- `scripts/stremio_playcheck.py` fetches every stream of a running instance the way a player does: HLS from the master to the first segments, files from the start and once in the middle, with the stream's `proxyHeaders`. The server's playback check reads only the start of a stream; this one found the Vidsonic proxy bug, moflix's paid player and the IP-bound streams of a VPN setup. Results of the 2026-10-03 end-to-end test: `docs/plans/stremio-latency.md`.

### Perf: kinox Skips Episode Requests Without a Request
- kinox cannot answer a season or episode request (its mirror API serves a series page's default episode, and films are another category), but it searched and loaded the closest hits with their mirrors before dropping them: "Dark" S01E01 loaded "Dark Paradise", "Dark Harvest" and "Dark Hearts". In the Stremio test s.to started 2 s late on that request (plugin slots busy) and was cut. kinox now returns at once for season requests.

### Fix: kinoger Returned Nothing Behind Its New Verification Page
- Since 2026-10-03 kinoger answers browsers with a "Verification..." page (HostAdmin WAF behind Cloudflare): a proof of work in a web worker, about a second, then a redirect titled "Loading <url>" and cookies `ha-waf-ticket`/`ha-waf-hash` (30 min). The stealth browser knew only Cloudflare's challenge titles and returned the verification page as the search result, so every kinoger search found 0 hits (25 of 25 in the end-to-end test).
- Both titles count as challenge titles now (`is_challenge_page`), so the browser waits for the real page; the clearance store keeps the `ha-waf-*` cookies like `cf_clearance`. Checked live: in the stealth browser the verification page cleared by itself after about a second (twice, with fresh cookies), and searches return their hits again (Oppenheimer 3, Barbie 10, Dune 9 cards). In that last run the WAF did not challenge again, so the wait itself is covered by the unit tests.

### Fix: moflix Served Its Paid Player as a Stream
- moflix lists a "Premium (No Ads)" video next to the hoster mirrors: HLS on `moflix-stream.day` (`/movies/<release>/master.m3u8?md5=…&expires=…`). It is moflix's paid player (5 EUR a month): the master playlist answers anyone, every variant playlist answers 403 without a paid session (also with the master's token, Referer or Origin). The playback check reads only the master, so these streams were offered as "VIDHIDE · moflix" and failed in the player. v0.2.0's entry "moflix's Own HLS Streams Were Never Played" fixed their dispatch but not this.
- The plugin drops videos named "Premium". Found by the end-to-end test against Stremio: 2 of 2 checked variants answered 403; moflix's watch page shows the paywall.

### Fix: Vidsonic Streams Did Not Play Through the HLS Proxy
- Found by the end-to-end test against Stremio: Vidsonic's master playlist lists its variant from the CDN root (`/secure/98/<id>/video.m3u8`). The HLS proxy rewrote only URIs starting with the stream's CDN directory, so the player resolved that path against Scavengarr's host and got a 404; every VIDSONIC stream (filmpalast) stopped after the master playlist.
- The proxy now rewrites every URI of the stream's CDN, in URI lines and in the `URI="…"` attributes of tags (audio renditions, keys, init segments): root-relative and protocol-relative URIs and absolute CDN URLs outside the stream's directory become `<proxy>/<stream-id>//<path>`, which the proxy joins with the CDN origin. Relative URIs stay as they are; URIs of other origins stay direct (the proxy fetches from the stream's CDN only).
- Checked live: Vidsonic, StreamUp and the XFS hosters play master, variant and segments through the proxy.

## v0.2.0 - 2026-10-02

Massive expansion of the plugin ecosystem (2 → 41 plugins), Stremio addon integration, 60 hoster resolvers, plugin base class standardization, search result caching, circuit breaker, global concurrency pool, graceful shutdown, multi-language search, and growth of the test suite from 160 to 4808 tests (4767 excluding the opt-in live tests).

### Chore: One Version Source
- The version stood in eight places as `0.1.0`: `pyproject.toml`, the FastAPI app, the Stremio manifest (Stremio compares it to notice addon updates), Torznab caps, the default User-Agent in `defaults.py` and `schema.py`, and `data/config.yaml`. `infrastructure/version.py` now reads the installed package's version (`importlib.metadata`) once; the others use `APP_VERSION` / `APP_USER_AGENT`, and the shipped config leaves `http.user_agent` at its default. A release bumps `pyproject.toml` only.

### Fix: kinoger.ru Links Went to the Vidara Resolver
- The kinoger.pw support (below) claimed the second-level name `kinoger` for the `strmup` resolver, so kinoger.ru links (kinoger's VOE tab, a redirect to a VOE mirror such as `jeremyparticipantanything.com`) were sent to the Vidara API and failed instead of following the redirect. Found by the live check: 2 of 2 kinoger.ru links were dead.
- Resolvers can now claim full host names (`supported_hosts`); the registry checks them before the second-level name. `strmup` claims the host `kinoger.pw` only, `canonical_hoster()` maps claimed hosts, and the Stremio stream converter asks for the URL host before its second-level name.

### Feature: fsst Streams Play (kinoger's First Player Tab)
- fsst was a validate-only generic DDL config whose ID pattern (`/<id>`) did not match the `fsst.online/embed/<id>/` links kinoger serves, so every fsst link resolved to nothing. On kinoger's single-player pages ("Wednesday", "John Wick: Kapitel 4") fsst is the only hoster.
- `FsstResolver` (`fsst.py`) replaces the config: the embed page redirects to its Kernel Video Sharing player host (incvideo1.online), whose Playerjs setup lists every quality (`file:"[360p]<url>,[720p]<url>,[1080p]<url>"`). The best quality's `get_file` link is returned; it redirects to the MP4 on the CDN and needs no headers. A 404 page (its player plays `video_error.mp4`) means gone.
- Checked live: 8 of 8 kinoger fsst links (films and episodes) resolve in 0.3–1.1 s to a 1080p MP4 that answers range requests; a `get_file` link still played after 25 minutes. Generic DDL hosters: 10 (was 11); 60 resolvers in total.

### Feature: kinoger's Own Player Resolves (kinoger.pw)
- kinoger's "Stream HD" tab plays from `kinoger.pw/e/<id>`, which no resolver handled. The page is a white-label Vidara player: it credits "Vidara" and loads its stream with `POST /api/stream` (`{"device": "web", "filecode": id}`), like vidara.so. The `strmup` resolver's Vidara path now covers the `kinoger` domain. Checked live: 4 of 4 kinoger.pw links (films and episodes) resolve in 0.4–0.7 s, playlists and segments load; an unknown id answers 404.

### Feature: gxplayer Resolver (megakino's "Stream in HD")
- megakino links every film to VOE and to `watch.gxplayer.xyz/watch?v=<id>` ("Stream in HD"), which no resolver handled. `GxplayerResolver` (`gxplayer.py`, a port of JDownloader's `GxplayerXyz`) reads the watch page's video object (`id`, `uid`, `md5`) and returns the HLS master `/m3u8/{uid}/{md5}/master.txt?s=1&id={id}&cache=1`; no playback headers are needed. Checked live: master, 720p variant and MPEG-TS segments load without Referer.
- 60 hoster resolvers now (24 individual).

### Fix: kinoger Returned No Films and Nothing From Single-Player Pages
- kinoger's player widget lists a film's stream as episode 1-1 too, in a hidden list (`<ul id="kinog-serial" style="display: none;">`, entry "1 Часть"). Since the episode parser (2026-10-01) every film counted as a series with the label "1x1", so film requests (category 2000, Stremio movies) got no kinoger results. A hidden episode list now marks a film's player.
- A page with one player has no tabs: its player container (`<div id="container-video">`) is not inside a `<section id="contentN">`, and the parser only read sections. Such pages returned no links at all ("Wednesday", "John Wick: Kapitel 4", "El Camino"); a lone player container is now read like a tab without a label.
- Checked on 19 live pages (12 films, 7 series, 9 of them with a single player): all parse with their kind and links now. New real-page fixtures: `detail-john-wick-kapitel-4`, `detail-wednesday`.

### Chore: Dead cineby Disabled in the Shipped Config
- cineby's API host `db.videasy.net` no longer resolves (NXDOMAIN since 2026-10-01), so each search failed and the circuit breaker kept probing it. `data/config.yaml` (the Docker image's config) disables it with `plugins.overrides.cineby.enabled: false`; the plugin stays in the code, and removing the entry brings it back when the site returns. Without `data/config.yaml` it stays enabled.

### Fix: Other Series With the Same Word Were Served as Streams
- The title filter scored a result that adds words to the requested title as a perfect match (`token_set_ratio` is 100 when one token set contains the other), so "Dark" S01E01 listed "Dark Matter" (filmpalast), "Dark Gathering" and "Bastard!! Heavy Metal, Dark Fantasy" (aniworld) next to the series; with autoplay, the next episode could come from the wrong series. Such a result loses `title_extra_words_penalty` (0.35) on the subset score: it passes with a matching year (0.85), not without one (0.65) or with a wrong one (0.35). Results that only drop words ("Dune" for "Dune: Part One") and release names of the right title (guessit's clean title) score as before. Live audit with the plugins' answers for 17 titles: the old filter kept 12 results of 10 other series for "Dark" (Dark Matter, Dark Winds, His Dark Materials, ...) and 10 wrong results for 4 other titles ("To End All War: Oppenheimer & the Atomic Bomb", "Our Last Crusade or the Rise of a New World" for "The Last of Us", "One Piece Log: Fish-Man Island Saga"); the new one drops exactly those.

### Fix: German Titles Without a TMDB Key Needed the Example Config
- Without a TMDB key, the German title of a Stremio request comes from Wikidata ("Haus des Geldes" for "Money Heist"). Wikidata answers 403 to a User-Agent without contact information, and the code default `Scavengarr/0.1.0` had none (only `data/config.yaml` set the contact URL): without that file, German-titled films and series were searched under their English title only. The default is `Scavengarr/0.1.0 (+https://github.com/Strob0t/Scavengarr)` now.

### Perf: filmpalast Scrapes Real Matches Only
- filmpalast loaded the 220–320 KB detail page of every search hit of the requested kind: "Oppenheimer" also "Fireball: Visitors from Darker Worlds" and "Into the Inferno", "Dark" S01E01 also "Dark Matter S01E01" and "His Dark Materials S01E01". It goes through `relevant_hits()` now, comparing the series name without its episode tag, so an episode request scrapes the exact series alone. It was the largest stream source of the measurement (18 of 82 streams).

### Fix: movie2k Had No Series Streams
- movie2k lists a series' episodes as `<table data-episode-id="…">` (base64 of `tt3581920-s1e1-1`) whose mirrors are `<a href="#" onclick="return loadMirror('<url>')">`. The parser only took `http` hrefs (what film pages have), so every series came back without links, and series were labelled as films (it looked for `type=tv`, the URLs say `type=series`). Episode mirrors are links labelled `1x1 vidoza.net` now, narrowed to the requested episode; a page with episodes is a series.
- movie2k scraped the detail page of every search hit ("Oppenheimer" also loaded "Fireball" and "Die Schattenmacher"); it uses `relevant_hits()` now, like the other plugins. Its description was the page's inline script (the longest text block); script text is skipped.
- Found by the parser tests on real pages (`tests/fixtures/html/movie2k/`).

### Perf: One Site per Mirror Group in Stremio
- hdfilme, streamcloud and streamkiste are one database in three themes: same news ids, titles, order, texts and links (checked on 6 searches and their detail pages). A Stremio request ran all three and the stream dedup threw two thirds away. Plugins now declare a `mirror_group` (the three: `"hdfilme"`), and a Stremio request asks the first member whose circuit breaker is closed; the next takes over when it opens, and with no closed member all go to their breakers' probes (`stremio_mirrors_skipped`, `PluginCircuitBreaker.is_closed`). Torznab keeps the three indexers. A member with a broken parser answers empty and stays chosen; the real-page tests watch for that.

### Perf: Episode Requests Scrape the Exact Series Only
- A season or episode request scraped the 3 closest search hits, so "Dark" S01E01 also loaded "Dark Matter" and "Dark Winds" (series, season and episode pages plus link-outs on s.to), which the title matcher drops anyway. In the measurement s.to started 3 s late (all plugin slots busy) and was cut by the search deadline: Dark S01E01 had no stream. With a limit, `relevant_hits()` now keeps an exact hit alone; without an exact hit the 3 closest stay. kinoking filters its cards by kind before the cut, so the film "Iron Man" takes no slot of a request for the series "Iron Man - die Zukunft beginnt".

### Perf: One devideosrc Fetch per Title Instead of Three
- hdfilme, streamcloud and streamkiste are three themes of one DataLife Engine database: same news ids (Oppenheimer is 23684 on all three), same devideosrc player, same hoster links. A Stremio request ran all three, so each title's player page and `embed-links` POST went out three times at once (devideosrc answers bursts with 429, retried after 2–4 s). Concurrent `fetch_links` calls for one player now share one fetch; a caller cut by its deadline leaves it running for the others.

### Fix: Wrong Years, Genres and Descriptions on streamkiste, kinoger and megakino
- streamkiste read the year of the last related film listed under a title (Oppenheimer: 2020 instead of 2023), and kinoger's detail parser found neither year nor genres in the live theme. The title matcher scores a wrong year −0.3 and needs a year to accept a title with extra words. streamkiste keeps the first `.release` (the film's own), kinoger reads the year from the page title ("Oppenheimer (2023)") and the genres from the post's category list (`<li class="category">`), which also marks series by their "Serie" category. megakino's description was the last user comment (comments are "full-text" divs like the plot); it is the plot now. Found by the parser tests on real pages.

### Fix: kinoger Served the First Episode for Every Episode
- A kinoger series page holds every season in each player tab: the player script starts at the first episode, and the episodes are listed as `<span onclick="pw.player('<url>', this);" data-id="1-5">`. The plugin took the script's first URL, so "The Last of Us" S01E05 (and with autoplay every next episode) played S01E01. Each tab now yields one link per episode, labelled `1x5 <tab>`, and a season/episode request keeps the links of that episode (4 hosters for S01E05). Found by the parser tests on real pages; the series page also counts as a series now.

### Fix: Season Pages Served Every Episode's Links
- The episode filter narrowed a result's links by their episode labels (`1x5`, `S01E05`) only when the title had neither season nor episode. A title with a season but no episode (a season pack `Show.S01...`, and with guessit 4 also a season page "Show - Staffel 1") kept the links of every episode, so S01E02 could play episode 1. Such titles drop the result on a season mismatch and otherwise narrow their links like a show page.

### Fix: Stremio Catalog Items Opened Nothing
Found in the Stremio live test (2026-10-01).
- **Items had `tmdb:` ids**: TMDB's trending and search lists carry no IMDb ids, so every catalog item got a `tmdb:<id>` id, and Stremio opens an item only through a meta addon for its id prefix; Cinemeta knows IMDb ids only, so Stremio showed "No addons were requested for this meta!" and no stream list (series had no episode list either). Catalog items now carry IMDb ids from `/{movie|tv}/{id}/external_ids` (cached 30 days); titles without one are left out. Trending and search lists cached before the update keep `tmdb:` ids until they expire (6 h, 1 h); `tmdb:` stream ids stay accepted.
- **Empty board rows without a TMDB key**: the IMDB fallback has no trending lists, but the manifest declared both catalogs as rows, so Stremio's board showed two empty "Scavengarr Trending" rows. Without a TMDB key the catalogs are now search-only (`"isRequired": true`, named "Scavengarr Movies"/"Scavengarr Series"); the search through IMDB Suggest works as before.

### Fix: moflix's Own HLS Streams Were Never Played
- moflix hands out its own HLS playlists (`https://<name>.moflix-stream.day/movies/<release>/master.m3u8?md5=…`, 1080p, German and original audio) besides hoster embeds. The registry gave them to the resolver of their domain (`moflix-stream` → VidHide), which expects an embed page: 60 of 60 failed in the live test. Streaming playlist URLs (`.m3u8`, `.mpd`) now go straight to the content-type probe.

### Fix: English Streams Listed as German Dub
- The language parser knew English words only ("English", "Sub", "Untertitel"), so s.to's label "Englisch" fell back to the plugin's default language: English audio was listed as German Dub, ranked as German and continued by autoplay in the German binge group, and its VOE link collided with the German one in the per-hoster dedup. "Englisch(en)", "deutschen" and "Untertiteln" are recognized now ("Japanisch mit deutschen Untertiteln" → German Sub).

### Feature: Mixdrop Streams Play in Stremio
- **New `MixdropResolver`** (`hoster_resolvers/mixdrop.py`): mixdrop was a validate-only generic DDL config, so every mixdrop link (36 in the live test) came back as its embed page and was dropped. The resolver reads `MDCore.wurl` from the embed player's packed setup: the MP4 on the delivery CDN, no captcha (JDownloader's download form needs one). Domains from JD2 `MixdropCo` without its dead ones; mixdrop left the generic DDL configs (23 individual + 11 generic DDL + 25 XFS = 59 resolvers). The packed-block loop of `extract_video_url()` became the shared helper `unpacked_scripts()`.
- **The player's User-Agent for checks and the HLS proxy**: the playback check and the HLS proxy fetched with the app's own agent (`Scavengarr/0.1.0`), but Stremio plays with the browser agent of the `proxyHeaders`; mixdrop's CDN answers other agents with 403, so the check dropped streams the player could play. Both send `DEFAULT_USER_AGENT` now (a stream's own `User-Agent` header wins).

### Perf: Stremio Answers Do Not Wait for Slow Hosters
- **`stremio.resolve_grace_seconds`** (new, default 4.0): once the first stream is resolved, unfinished resolutions get at most this long, instead of the rest of `stream_deadline_seconds`. 4 s keeps the DoodStream mirrors, which land 3.2–4.0 s after the first stream (3 s cut them in a test run), and cuts Dropload's captcha (7 s). Browser-resolved hosters (DoodStream mirrors such as playmogo, Dropload's captcha, Byse/Filemoon) take 3–7 s; measured 2026-10-01, Interstellar had 5 streams after 4.9 s but was answered after 10.8 s, Attack on Titan had 8 after 8.0 s and was answered after 15.0 s. `stremio_resolve_complete` logs `grace_hit`. 0 restores the old behavior.

### Perf: Stremio Saves Only the Stream Links It Serves
- The stream use case saved a `CachedStreamLink` for every ranked stream, although only the HLS proxy and `/play/` look links up: "Dune" (73 ranked streams) spent 6 s saving links and was answered after 21.5 s. Links are now saved only for the streams served through those endpoints (`build_cache_link()`); direct video URLs need none.

### Perf: Plugins Scrape Only Real Matches
Site searches also list loose matches ("Batman" finds "Justice League", "Oppenheimer" found "Fireball" and "Norman" on the DataLife Engine sites), and plugins loaded every hit's detail pages: kinoking needed 34–45 s per search (a cold movie page takes up to 15 s there), streamcloud and streamkiste 8–12 s, so in Stremio they were always cut at the search deadline. The new helper `relevant_hits()` (`infrastructure/plugins/relevance.py`) keeps the hits whose title contains every query word (case, accents, punctuation folded), else the site's first 3 (titles in another language); sto, aniworld, kinoking, kinox, hdfilme, kinoger, megakino, streamcloud and streamkiste scrape only those, closest titles first (fewest extra words), and for a season or episode request at most 3 (`SINGLE_TITLE_HITS`): "Dark" also matches "Dark Matter", "Dark Winds", …, whose pages and link-outs overran the search budget, so "Dark S01E01" got no stream at all. Torznab searches no longer return the loose matches either (Prowlarr and the Arr apps rejected them by title anyway).
- **Circuit breaker per plugin and category**: kinoking's movie pages take 12–17 s to answer (its series pages 1 s), so every Stremio movie request waited the whole 10 s search budget for a plugin that never delivered, while its series results reset the breaker. The Stremio plugin search now keys the breaker by plugin and category (`kinoking:2000`), so a site too slow for one content type is skipped for that type only (`stremio_plugin_circuit_open`, now logged at info). Answers without results no longer reset the breaker: kinoking answers searches without hits at once, which closed it again between its movie timeouts (measured 2026-10-01: it never opened).
- **aniworld scraped FAQ pages**: its ajax search also lists support FAQ pages and single episodes ("One Piece": 3 series, 11 episodes, 50 FAQ pages), and the plugin fetched a detail page and a made-up episode page for each, about 128 requests per search on the sister site of s.to, which gates link-outs after bursts. Only series pages are scraped now. A search without hits (empty answer) is no longer logged as `aniworld_invalid_json`.
- **cine had no links and serves downloads only**: the links API answers `{"status": false}` without the language the site's own page sends (`lang=1` German, `lang=2` English), so every title came back without links (`cine_no_links`). Links are fetched per language the search entry lists and tagged with it; only entries matching the query are scraped ("Oppenheimer" also listed "Toy Story 4"). Every `/out/` link opens a reCAPTCHA gateway, so no resolver plays them: `provides = "download"` keeps cine out of Stremio.
- **animeloads serves downloads only** (`provides = "download"`): its Stremio results were one series preview embed per series, no episode stream, and no resolver plays the site's embeds, so every Stremio request spent a browser and 11–15 s on it for nothing.

### Fix: s.to Delivered No Playable Episode
Found in the Stremio live test (2026-10-01): every s.to stream was dropped, so German series had one stream or none.
- **The search found no series**: the site's search cards now nest one `/serie/` anchor in another with the title after the inner one, so the parser read no card and took the episode hits below them instead (series whose episode titles contain the term): "Dark" became "It's Always Sunny in Philadelphia" (episode "A Dark Day for Baseball"), and titles without such an episode hit found nothing. The parser now reads the cards' `h6.show-title`, skips the episode hits and keeps each series once (the page renders its results twice); fixtures use the live markup.
- **The official provider counted as hoster**: episode pages also link the streaming service that owns a series ("Anbieter", `data-provider-name="Provider"`, no embed; the link-out answered 410 for Breaking Bad). The plugin skips it now instead of returning an unplayable stream and spending a link-out on it.
- **Dead domain first**: `s.to` no longer resolves (NXDOMAIN; JDownloader's SerienStreamTo lists it as dead) but was the first of `_domains`; the plugin now uses `serienstream.to` and `186.2.175.5`.
- **Link-outs stayed unresolved**: s.to answers `/r?t=<token>` with a redirect only when it is opened from its episode page (or with that page's session); without `Referer` it returns a page that works only inside its player iframe, and the plugin fell back to the unresolvable link-out. The plugin now sends the episode page as `Referer` (`HttpxPluginBase._resolve_redirect(..., referer=...)`).
- **Link-outs behind Turnstile**: after bursts of link-outs from one IP the site answers them for every new session with a Turnstile widget in its player iframe, for minutes. When no link-out of an episode resolves, the plugin now lets the browser pass the gate once (`BrowserFetcherPort.click_through`: open the episode page, click a hoster, tick the widget if needed, submit; verified live: about 6 s with the widget, 1 s without) and continues in httpx with the browser's session cookies (`HttpxPluginBase._use_browser_session`), which the site trusts afterwards. The pass runs as its own task, so a search cut by the Stremio deadline does not cancel it and the next request profits; a failed pass is not retried for 5 minutes. `SolverFetcher` cannot click and returns `None`.
- **Unrelated series were scraped**: the site's search also lists other series ("Breaking Bad" finds 18, among them "Better Call Saul"), and the plugin fetched each one's detail page, episode page and link-outs: 7 s and about 40 link-outs per request, a burst after which the site gates link-outs behind Turnstile for a while. Only series whose title contains every query word are scraped now (else the site's top 3, for titles in another language), each once (the result page links a series twice).

### Feature: Stremio Autoplay of the Next Episode, Readable Stream List
Found in a live test with Stremio Web (2026-10-01).
- **Autoplay never picked a Scavengarr stream**: Stremio's binge watching plays the next episode's first stream whose `behaviorHints.bingeGroup` equals the current one, and Scavengarr set none. Every stream now carries `bingeGroup: scavengarr|<language>` (a German dub continues in German, at the best quality and hoster the next episode has) and, when the site has one, the release name as `filename` for subtitle addons.
- **Stream list**: `name` was the whole title plus quality ("Oppenheimer (2023) HD 1080P", three lines in Stremio's narrow name column) and the description one long line that Stremio cut off before hoster and size. `name` is now `Scavengarr` plus the quality (`1080p`, `720p`, `4K`, …), the description one short line each for the site's own title (release name if any, which also shows a wrong match), `language · size` and `HOSTER · plugin`.
- **One stream per hoster and language** (was: per hoster): a German-sub VOE stream was dropped whenever a German-dub VOE stream resolved, so anime fans had no sub option. Series titles that already contain the episode (`… s01e01`) no longer get it twice.
- **Hoster names**: plugins label hosters inconsistently (`VOE`, `voe.sx`, `unknown`), so two VOE streams were returned as different hosters, the hoster bonus missed, and the list showed "UNKNOWN" or "VOE.SX". Hoster names are now resolver names (`HosterResolverRegistry.canonical_hoster`): a label naming a known hoster wins, then a known URL domain (mirror domains such as `d0000d.com` become `doodstream`); otherwise domain labels are reduced to their second-level part and placeholders fall back to the URL.

### Fix: ddlvalley and scnsrc Were Registered as German
- Both English sites set `default_language = "en"`, a class attribute that shadowed the base's `default_language` property while the registry reads `languages` (left at the base default `["de"]`); 15 more plugins repeated `default_language = "de"` to no effect. The two sites declare `languages = ["en"]` now (`docs/plugins.md` regenerated) and the dead overrides are gone; `default_language` stays the property returning `languages[0]`.

### Fix: Found by the Type Checker
- **Torznab reachability probe**: when a site answers `HEAD` with 405/501, the probe falls back to a ranged `GET`, but passed `timeout=` to `AsyncClient.send()`, which has no such parameter: every fallback raised `TypeError` (the test mocked `send`). The timeout now goes on the request.
- **Plugin `timeout` override on Playwright plugins**: `plugins.overrides.<name>.timeout` set an attribute Playwright plugins never read; it is reported as unsupported (`plugin_timeout_override_unsupported`) instead of silently ignored. `max_concurrent`/`max_results` apply to both bases as before.

### Chore: Type Checking in the Gate
- `[tool.basedpyright]` (`typeCheckingMode = "standard"`, `src` and `plugins`) replaces the editor-only setting in `.vscode/settings.json`, and `basedpyright` is a dev dependency, so `poetry run basedpyright` checks with the editor's rules (first step of next-steps item 4).
- The 120 findings are fixed (`src/` and `plugins/` have 0 errors in standard mode) and basedpyright runs as a local pre-commit hook with the project venv, so CI checks types too. Among the fixes: concurrency ports return async context managers, plugin request kwargs are typed for httpx, the shared browser pool and the cookie hand-over of the login plugins (`PlaywrightPluginBase._cookie_params`) are typed, and the dead YAML-plugin branch of the Stremio plugin search is gone.

### Fix: streamcloud's Fallback Domain Was a Gambling Site
- `streamcloud.my`, streamcloud's fallback domain, now serves an Indonesian online-slot site: had `streamcloud.download` failed, the plugin would have scraped it. The fallback is `streamcloud.plus` (it, `.press`, `.uno` and `.forum` redirect to the primary, so a new primary domain is followed). Found by probing every plugin domain while comparing the CUII block list (`docs/plans/cuii-coverage.md`); no other plugin domain is parked or hijacked.

### Chore: Stremio Measurement Harness in the Repository
- `scripts/stremio_measure.py` requests the 17 titles of the latency runs like Stremio does and prints latency, streams and sources per title plus a summary (`docs/plans/stremio-latency.md`). It lived outside the repository; a test pins its title set, whose id for *Der Schuh des Manitu* was wrong for 8 runs.

### Test: Parsers Run on Real Pages
- `tests/unit/infrastructure/test_real_pages.py` runs the parsers of the five DataLife Engine sites (hdfilme, kinoger, megakino, streamcloud, streamkiste) on 13 search and detail pages captured from a live run (`tests/fixtures/html/<plugin>/*.html.gz`, 230 KB, the per-visitor `dle_login_hash` scrubbed), with values read off the pages. The hand-written fixtures of the plugin tests only show a parser what it expects; the real pages found the kinoger episode bug and the wrong years, genres and descriptions fixed above (next-steps item 5).
- `scripts/capture_pages.py <plugin> "<query>"` records every page a live search fetches (Cloudflare pages through the stealth browser) and `--fixture <page> <name>` stores one as a fixture, for the other plugins and for recapturing when a site changes its theme. It scrubs per-visitor values: DLE's `dle_login_hash`, the Laravel `csrf-token` meta and s.to's encrypted `/r?t=` redirect tokens.
- The top stream sources of the measurement are covered too: filmpalast, movie2k, aniworld, s.to and kinoking (search, film, series and episode pages; 21 more pages). movie2k's and filmpalast's pages showed the fixes above.

### Chore: Dependency Updates
- fastapi 0.128 → 0.142, uvicorn 0.40 → 0.54 and respx 0.22 → 0.23 (constraints in `pyproject.toml`), plus the in-range updates of the lock file, among them starlette 0.46 → 1.7, pydantic 2.11 → 2.13, rebulk 3.2 → 6.0 (guessit's parser), pytest 9.1, pytest-asyncio 1.4 and pre-commit 4.6. The two movieblog URL tests ran the coroutine with `asyncio.get_event_loop()`, which pytest-asyncio 1.4 no longer provides outside a test loop; they are async tests now. Starlette's test client warns that it will move off httpx (`StarletteDeprecationWarning`), nothing to change yet.
- pydantic-settings 2.10 → 2.15 and python-dotenv 1.1 → 1.2: both were pinned to their first minor release (`>=2.10.1,<2.11.0`, `>=1.1.1,<1.2.0`) since the config loader was added, without a recorded reason; they use caret constraints like the other dependencies now.
- structlog 25 → 26: the suite passes and both log formats (JSON lines, console) render as before, exceptions included.
- cryptography 46 → 50 (AES decryption of Click'n'Load link containers in `infrastructure/plugins/clicknload.py`): the suite passes.
- redis 7 → 8 (optional cache backend): redis-py 8 speaks RESP3 by default (Redis 6 or newer; the compose profile runs `redis:7-alpine`) and sets 5 s socket timeouts. Its typed async client returns `bytes | str` for `GET`: a text reply counts as a cache miss now (`redis_get_not_bytes`) instead of failing in `pickle.loads`, and the health-check PING is awaited directly. The adapter had no tests; it has unit tests with a mocked client now (no Redis server in the dev container, so no live check).
- ruff 0.15 → 0.16 with the `ruff-pre-commit` rev: no new lint findings. ruff 0.16 also formats the Python blocks of Markdown files (the pre-commit hook passes `.md` files now); `[tool.ruff.format] exclude = ["*.md"]` keeps the hand-aligned, partly pseudo-code doc snippets as written.
- guessit 3.8 → 4.4: on 249 titles and release names from a live run of the stream plugins, 7 parse differently, all for the better: a German "Staffel 1" or "1 Staffel" is read as season 1, and "The Last of Us" keeps its last word (3.8 read "Us" as a language). Title-match decisions on these results are identical; the season pages that now carry a season narrow their links by episode (see "Season Pages Served Every Episode's Links").

### Chore: `xvfb-run` Works in the Dev Container
- `.devcontainer/setup.sh` installs `xauth` with `xvfb`: `--no-install-recommends` left it out, and `xvfb-run -a <cmd>` (headful browser tests, AGENTS.md §9) failed with "xauth command not found". The step also runs when Xvfb is present but xauth is missing.

### Chore: CI on GitHub Actions
- `.github/workflows/ci.yml` runs `pre-commit run --all-files` and the offline suite (`pytest`, live tests excluded) on every push to `staging`, on pull requests to `staging` and `main`, and on demand: Python 3.12, Poetry, cached virtualenv and pre-commit environments, read-only token. Checked in a clean clone without browsers, credentials or dev container environment: install 25 s, pre-commit 40 s, 4547 tests in 62 s; no offline test needs a real browser.
- Live smoke tests stay out of CI: GitHub's datacenter IPs get harder Cloudflare challenges than the home network the addon runs in.

### Chore: Dev Container Port Reachable from the LAN
- `.devcontainer/devcontainer.json` publishes port 7979 on all host interfaces (`runArgs`: `-p 0.0.0.0:7979:7979`) instead of `forwardPorts`, which only tunnels the port to the editor's machine. Stremio and other LAN clients can now reach a server started in the container with `--host 0.0.0.0 --port 7979`, for live tests against the local media stack.

### Chore: Dev Container Extensions Work in VSCodium
- `.devcontainer/devcontainer.json` drops `ms-python.pytest` (not on Open VSX; test discovery is part of `ms-python.python`) and `ms-python.black-formatter` (the project formats with ruff) and adds `detachhead.basedpyright`, the Open VSX replacement for Pylance, which VSCodium/Code - OSS may not use.
- `.vscode/settings.json` sets `basedpyright.analysis.typeCheckingMode` to `standard` (Pylance-like); basedpyright's own default `recommended` flags the whole code base.

### Fix: Code Review of `staging` (plan: `docs/plans/code-review-fixes.md`)
- **Security — HLS proxy was an open proxy (SSRF)**: `/api/v1/stremio/proxy/{id}/{path}` joined the client path with `urljoin`, so `…/proxy/<id>/http://192.168.x.x/…` fetched any internal address and returned the body. Paths that leave the stream's CDN (other scheme or host) now get `400`.
- **HLS segment errors leaked connections**: a 4xx/5xx segment response was raised without closing the streamed response; about 100 CDN errors (expired segment tokens) exhausted the shared HTTP pool and blocked every plugin and resolver. The response is closed before the error propagates.
- **Docker container did not start from a fresh clone**: `docker/entrypoint.sh` was stored in git as 100644 (the local `core.fileMode=false` hid it), so the container exited 126 in a restart loop. The executable bit is committed and `Dockerfile.prod` sets it too. Same for `.claude/hooks/*.sh` (a non-executable guard hook exits 126 and lets every command through) and `.devcontainer/*.sh`; `tests/unit/infrastructure/test_repository_files.py` guards the modes.
- **Browser dead after `docker compose restart`**: the Xvfb lock of the killed display survived in the container's `/tmp`, the new Xvfb refused to start, and every browser plugin failed. The entrypoint removes the stale lock and waits for the display socket (reproduced with a stale lock: old entrypoint no Xvfb, new one running).
- **Local start with the shipped config failed**: `data/config.yaml` set `cache.dir: /data/cache`, which is not writable outside Docker (`poetry run start --config data/config.yaml` from the README aborted with PermissionError). Now `./.cache/scavengarr`; the image keeps `SCAVENGARR_CACHE_DIR=/app/cache`.
- **Dev container on a fresh clone**: `--env-file .env.devcontainer` and the `.continue` copy needed gitignored files. `initializeCommand` now creates `.env.devcontainer` from the new `.env.devcontainer.example`, the copy is skipped when the file is missing.
- **Docker build hygiene**: the unused root `Dockerfile` (invalid `CMD`, picked by a plain `docker build .`) is removed; `.dockerignore` keeps `.venv`, `.devdata`, `.env*` (with `GH_TOKEN`), tests and docs out of the build context; compose runs the service with `init: true` so orphaned Chromium processes are reaped.
- **ruff pinned to the pre-commit version** (0.15.0) so the edit hook and pre-commit format identically.
- **aniworld found no episodes**: episode URLs were built as `/anime/<slug>/staffel-N/episode-M` without the `/stream/` segment (404 live, the correct URL answers 200 with hosters), so every series/anime episode request returned nothing — 0 aniworld results in all Stremio measurements. A season without episode now starts at that season's first episode (aniworld and fireani; before, season 1 was used).
- **kinox returned the wrong episode**: its mirror API only serves a series page's default episode, but the result was presented for any requested season/episode. Series are now skipped for season/episode requests (movies unaffected), and the mirror AJAX calls of all entries share one concurrency limit (before: a new limit per entry, ~5 × N parallel requests).
- **megakino**: pagination started at `search_start=0`, which DLE serves as page 1 again, so the first 20 hits were scraped and returned twice. Season/episode requests skip pages of other seasons ("X - Staffel N") before fetching them, and a page without the requested episode no longer falls back to all of its episodes.
- **Resolvers marked live files dead**: 7 resolvers treated `"error"` anywhere in the final URL as an error redirect, so "The.Terror.S01E01.mkv" or `serienstream.to/…/the-terror` were dead (and cached as dead for 15 min). One shared `is_error_redirect()` now checks path segments and query keys only. nitroflare, uploaded, 1fichier and mixdrop links with a file name or extra parameters after the ID (`/view/<id>/Movie.mkv`, `?<id>&af=…`) were rejected as invalid URLs; alphaddl's `"404"` and 1fichier's `"not found"` offline markers matched live pages.
- **Grab failed with 500 for titles outside Latin-1** (en dash, Polish/Czech letters, CJK): `/download/{job}` put the raw title into HTTP headers. `filename` is now ASCII with the real name in `filename*=UTF-8''…`, `X-CrawlJob-Package` is percent-encoded.
- **`.crawljob` injection**: `packageName`, `filename`, `comment` (scraped descriptions), `downloadFolder` and `downloadPassword` were written without removing line breaks, so a description containing `\ndownloadFolder=/x` added a JDownloader key. Line breaks are collapsed to spaces.
- **Stremio episode filter dropped multi-episode releases**: guessit returns lists for `S01E01-E03` / `S01-S03`, and `[1, 2, 3] != 2` removed a release that contains the requested episode. Membership is checked now. `StreamSorter.sort()` also dropped each stream's `title` (the stream-name fallback) because it rebuilt the objects field by field.
- **XFS form POST to the wrong host**: when a rotating mirror redirected the embed page, the `/dl` form was posted to the original host (redirected, re-sent as GET without the form) and extraction failed; the CDN `Referer` also named the wrong page. Both now use the host/page that actually served the form and player.
- **Packed player JS in base 62 was not unpacked**: the unpacker decoded words with `int(word, base)`, which fails above base 36, so the packer's default "Normal" (base-62) encoding came back unchanged and XFS/Filemoon extraction found nothing. Words are decoded with the packer's own digit set now.
- **moflix returned its own title page as the link** when a title had no videos (or the detail API failed), and returned every episode's videos for an episode request. Titles without videos give no result now, and season/episode requests keep only videos with that `season_num`/`episode_num`.
- **serienfans episode results were titled "Show - E5"**, which Sonarr cannot parse, so every per-episode result was rejected. Titles are `Show S02E05 - Episode title` now.
- **movie4k was a stale copy of megakino_to** (same "/data" API): no season/episode filter (a series request returned every episode's streams), deleted streams were returned, a list-shaped `tmdb.movie` raised `AttributeError` and silently dropped the title, and Torznab quality subcategories were sent as genre filters (2040 Movies/HD searched only comedies). Both plugins now share `DataApiPluginBase` (`infrastructure/plugins/data_api.py`) and only set their name and domains.
- **sto (s.to)**: a full-series search (no season) only returned season 1; it now covers every season up to the result limit. The series of a search are processed in parallel (bounded) instead of one after another, and the hoster redirects of an episode resolve together.
- **cine**: one non-JSON answer (e.g. a DDoS-Guard page with status 200) raised in `resp.json()` and aborted the whole search. The three API calls now share `_post_api` (base `_safe_fetch` + `_safe_parse_json`), a failing title is skipped and logged, and titles without hoster links give no result instead of cine.to's own page as the "link".
- **dataload stopped working until restart once its session expired**: the login state was never reset. A search answered as for a guest (`data-logged-in="false"`) or rejected (expired CSRF token) now logs in again and retries once, and the login check only accepts an `xf_user` cookie of data-load.me (the shared client can hold other XenForo forums' cookies).
- **Search terms with `&`, `+` or `#` were cut off or split** in animeloads, cineby, ddlvalley and scnsrc: the query went into the URL unencoded ("Fast & Furious" searched for "Fast "). It is now `quote_plus`-encoded. filmpalast puts the term into a path segment: it is `quote`d there, and `/`, `?` and `#` become spaces (the site answers an encoded `/` with 404 and finds "Wer ist Hanna?" only without the `?`).
- **filmpalast returned at most 32 results**: only the first search page was read. The plugin now follows `/search/title/<term>/<n>` while a page links "vorwärts", up to `_max_results` (32 pages).
- **GoFile kept a token the API had dropped**: every GoFile link counted as dead until the 25-minute cache expired. A 401 `error-wrongToken` now renews the guest token once, and concurrent resolves share one guest account (GoFile throttles account creation with 429). Known issue found on the way: GoFile currently refuses guest lookups altogether (401 `error-notPremium`, its website adds a token from an obfuscated script); that is logged as `gofile_guest_access_refused` and not retried.
- **burningseries scraped scam clones and could not deliver streams**: bs.to is gone, so the plugin fell back to `burning-series.io`/`.net`, clones that swap the player for their own redirect (JDownloader lists `.io` as scam). It now uses the genuine domains (`burningseries.ac`, `bs.cine.to`, `burningseries.sx`). The hoster links sit behind reCAPTCHA v2, so the plugin now declares `provides = "download"` (no longer queried for Stremio, where its pages never resolved); its results are pages for JDownloader's BsTo crawler. Episode searches link the episode page from the German season page as `Title S02E02` (the old `/serie/<slug>/2/2` URL does not exist; episodes without hosters give no result), season searches the German season page as `Title S02`.
- **aniworld fell back to a scam copy**: `aniworld.info` imitates the site with ad pages (JDownloader, 2026-09-09) and was the plugin's second domain. The plugin now uses `aniworld.to` only. A check of all plugin domains against JDownloader's fake/scam notes found no other hit; the plugin guide and AGENTS.md now require genuine domains.
- **Exception logs of the standard library were lost**: the logging queue handler deep-copied every record, which fails on tracebacks (and on log fields that cannot be copied), so e.g. uvicorn's "Exception in ASGI application" only produced "--- Logging error ---". Records are now handed over as a shallow copy, and stdlib tracebacks are rendered to text (`exception` field; the JSON format used to show a repr of the `exc_info` tuple).
- **Plugin registry re-imported plugin files on every name lookup, and one broken file broke Stremio**: `list_names()` executed all plugin modules again on every call, on the event loop (`/healthz` calls it on every probe), and a plugin file that failed to import made `get_by_provides()` raise on every Stremio request. The registry now imports each file once on first use and caches instances and metadata; failing files are logged and skipped, duplicate names logged (`plugin_name_duplicate`).
- **Concurrency pool fair share was not enforced under load**: a slot counted against the request only after the global semaphore was acquired, so all tasks of one request queued behind a full pool passed the fair-share check and later took more than their share. The share is now reserved before waiting (and given back when a waiter is cancelled).
- **Secrets in logs**: the TMDB `api_key` appeared in retry logs and in rendered httpx errors ("… for url '…?api_key=…'"), the Torznab `apikey` in every request log (`query`), a Redis password in the Redis URL. A structlog processor now masks secret parameters and URL passwords in every log field, tracebacks included, for structlog and stdlib records alike.
- **Hoster resolution had no overall time bound, and timeouts marked links dead**: a resolver's requests each had their own timeout (mostly 15 s), so one resolution (e.g. a `/play` click) could hang for a minute; `http.timeout_resolve_seconds` only applied to the registry's own requests. `resolver.resolve()` now gets that time in total. A timeout or network error is no longer cached as a dead link for 15 minutes.
- **ALTCHA solving had no bound on site-supplied parameters**: a hostile or broken challenge (huge `cost`, long or non-hex `keyPrefix`, huge `keyLength`) could pin a worker thread for hours (`asyncio.to_thread` cannot be cancelled). `cost`, `keyLength` and `keyPrefix` are now validated, and the solver gives up after 20 s.
- **Failed cache writes were swallowed or broke the whole answer**: a Torznab item whose CrawlJob could not be saved was still returned (the grab then answered 404); now it is dropped and logged (`crawljob_save_failed`). In Stremio one failed stream-link save failed the whole request; now only streams that need the link (HLS proxy, `/play/`) are dropped (`stremio_stream_link_save_failed`).
- **CrawlJob lifetime was hard-coded to 1 hour** (AGENTS.md promised a configurable TTL): new key `cache.crawljob_ttl_seconds` (default 3600, > 0) sets both the cache entry and the job's `expires_at`. `CrawlJobFactory(default_ttl_hours=…)` became `CrawlJobFactory(ttl_seconds=…)`.
- **API rate limit could stall playback**: the 120 requests/min per IP also counted HLS proxy requests (a player loads dozens of segments at start, then one every few seconds) and health probes. Both are exempt now. The limiter also forgets clients idle for a minute (it only evicted empty entries, which a client that never came back does not leave).
- **Link validation downloaded whole files and kept every result forever**: the GET fallback read the full response body (for a direct download link the file itself); it is now streamed and only the status is read. Expired validation results and unreachable-host marks are pruned every 1,000 validations.
- **HLS manifest cache grew forever**: expired manifests were only removed when the same URL came again, and rotating manifest URLs (tokens in the query) never do. The cache now holds at most 512 manifests (expired ones are dropped first, then the oldest).
- **Circuit breaker let every request through while half-open**: after the cooldown all concurrent Stremio requests hit the (probably still dead) plugin and paid its full timeout, not just one probe. Now a single probe is in flight; the others skip the plugin until it reports, and a probe that never reports is replaced after the cooldown.
- **Plugin scores could vanish**: the score index was only written when a new (plugin, category, bucket) appeared, so it expired 30 days later while the snapshots were still updated, and concurrent updates could drop each other's index entries. The index is now rewritten with every update, under a lock.
- **Redis ignored `cache.max_concurrent`**: the factory hard-coded 50 parallel operations for Redis. The configured value now applies to both backends; with the default (10) Redis gets fewer parallel operations than before, so set `cache.max_concurrent: 50` to keep the old Redis behaviour.
- **Own-host link resolution was unbounded**: `_resolve_result_links()` (filmfans, serienfans) and `_resolve_own_links()` (kinox, byte) followed every `/external/…` redirect at once, up to hundreds of requests to the plugin's own, usually Cloudflare-protected host. At most `_max_concurrent` run at once now.
- **Playwright driver leaked after a browser crash**: when a plugin's own (standalone) Chromium disconnected, the plugin launched a new one but left the old Playwright driver process running. It is stopped now; the shared pool's driver stays with the pool.
- **moflix bypassed the Turnstile solver**: its `_wait_for_cloudflare()` override polled for the XSRF-TOKEN cookie in a sleep loop that swallowed every error and never called the base solver (Turnstile click, clearance memo). It now solves the challenge through the base class and then waits for the cookie with a condition (`moflix_xsrf_cookie_timeout` when it does not appear); live smoke test passes.
- **Scraped URLs could reach the local network (blind SSRF)**: link validation, hoster resolution, playback checks and redirects followed whatever a scraped page pointed to, including `192.168.x.x`, `localhost`, `169.254.169.254` or Scavengarr itself. The shared HTTP client now refuses every request and redirect hop to a non-public address (`private_address_refused`); hostnames are checked after a DNS lookup, so a blocked domain that resolves to the router is refused too. The solver sidecar (`playwright.solver_url`) stays reachable.
- **`moflix-stream` claimed by two resolvers**: VidGuard and Vidhide both listed it and registration order decided. VidGuard (offline per JDownloader, including the former moflix-stream.day) no longer claims it; moflix-stream.click is Vidhide. The registry now keeps the first resolver for a domain claimed twice and logs `hoster_domain_conflict`.
- **Removed the unused stream-time liveness probe**: `probe.py` (`probe_urls_stealth()`) only ran without a hoster resolver, and the composition always wires one; resolution is the liveness check. Removed with it: `StealthPool.probe_url()`, the `probe_fn` parameter of `StremioStreamUseCase`, the always-empty `probe` section of `/api/v1/stats`, and the config keys `stremio.probe_at_stream_time`, `probe_timeout_seconds`, `probe_stealth_enabled`, `probe_stealth_concurrency` (old configs still load, the keys are ignored). `probe_concurrency`, `max_probe_count` and `probe_stealth_timeout_seconds` stay (resolution and `StealthPool`).
- **ddlspot/ddlvalley lost every result on one slow page, ddlvalley paged without limit**: a navigation timeout on search page N threw away pages 1..N-1; now the pages already collected are kept (`ddlspot_search_page_failed`, `ddlvalley_search_page_failed`). ddlvalley also stops at `_MAX_PAGES`. burningseries no longer crashes on a production-year text without a year.
- **Plugins bypassed the base fetch helpers** (per-plugin timeout and User-Agent on the shared client, Cloudflare browser fallback, uniform error logging): page requests now go through `_fetch_text()` / `_safe_fetch()` in burningseries, kinox (search, detail and mirror AJAX), sto (search, detail and episode pages; the `/r?t=` link-outs resolve through `_resolve_redirect()`), and megakino, streamcloud, streamkiste, hdfilme and movie2k (search, browse and detail pages; the DLE search POSTs go through `_safe_fetch()`, and megakino's `yg` token only counts once its request succeeded, so a failed one is retried).
- **myboerse never found anything, and neither XenForo forum filtered by category** (checked live): myboerse's search POST lacked the session's CSRF token (`_xfToken`), which the forum answers with HTTP 400, and it only knew `page-N` pagination (search pages use `?page=N`). On both forums `c[nodes][]` was ignored because XenForo applies it only with `search_type=post`, so a movie search also returned tutorials, music and audiobooks. The two plugins were copies; they now share `XenForoPluginBase` (`infrastructure/plugins/xenforo.py`: login with an own-host session cookie, CSRF token, one re-login, `search_type=post`, pagination, thread links) and only set their domains and forum map. The forum maps are rebuilt from the live forum index: software was labelled 5020 (TV/Foreign) and is now PC (4000/4030/4040/4060/4070), consoles 1000 (dataload filed them as PC games), mobile games 4060/4070 (myboerse filed them as consoles), PC games 4050, foreign-language films 2010, lossless audio 3040, music videos and concerts 3020, sport 5060, myboerse's XXX section 6000; missing subforums are added (dataload's foreign films, myboerse's foreign comics, English audiobooks, Cydia apps). A category without a forum returns `[]` without a request, a child category the forum does not tell apart uses its parent's forums, and results of unknown forums are labelled 8000 instead of a forum-name guess. myboerse no longer returns its `/xtra/` link (an affiliate redirect to a fixed Rapidgator file) as a download.
- **Animated films and documentaries were missing from movie searches** (megakino, streamcloud, streamkiste, kinoger, movie2k, hdfilme): films with the genre Animation/Anime were labelled 5070 (TV/Anime), hdfilme's documentaries 5080 (TV/Documentary) and its horror and animation films 2040 (Movies/HD), so a movie request (Stremio always sends 2000) dropped them. Films are now 2000 whatever the genre; series 5000, anime and animation series 5070. The six plugins carried copies of the same category code, which let Audio/PC/Books/Other requests through unfiltered and ignored 2070/2080/5090; they now use the shared `categories.py` (`served_category()`, `filter_by_category()`, `stream_category()`): a category the site does not serve returns `[]` before any request, a child it does not tell apart (2040) is answered with its parent's results. `_category_matches()` of both plugin bases is `categories.category_matches()`.
- **filmpalast and kinoking labelled results with the requested category**: a series request returned filmpalast's films labelled 5000; kinoking answered Audio/PC requests with films and Books/Other with series. Results now carry the site's label (filmpalast lists series per episode, `... S03E08` is 5000, everything else 2000; kinoking: the card type), other categories return `[]` without a request, and filmpalast filters by category and season/episode on the search titles before it loads detail pages.
- **nima4k labelled results with the requested category and never filtered**: a movie request returned series and concerts labelled 2000, and without a category every result was 2000 (the site's listings carry genre pills, not the section names the plugin looked for; documentaries were mapped to 5070, TV/Anime). Results are now labelled from the release (checked live): season packs (`Show.S03...`, "Staffel") 5000, animation series 5070, concerts 3020, sport 5060, everything else 2000 (documentary films included), filtered by the requested category; a category without a section returns `[]` without a request.
- **scnlog labelled results with the requested category and missed German releases**: every result carried the request (or 2000), and a movie request only searched `movies/`, while scnlog files German and other non-English films and episodes under `foreign/`. Results are now labelled by their section (checked live: the release URL starts with it): movies 2000, tv-shows 5000, foreign episodes 5020 and films 2010, games 4050, apps 4000, pda 4040, music 3000, ebooks 7000, xxx 6000. A request searches every section holding its category in parallel (films: `movies/` and `foreign/`) and keeps only matching rows before loading detail pages; categories without a section (e.g. consoles) return `[]` without a request. The unused `categories` attribute is gone.
- **scnsrc labelled results with the requested category, and applications as TV/Foreign**: a request's category replaced the post's own (a movie request returned games labelled 2000); applications were 5020, console games PC games (4000), concerts and music videos plain audio, unknown categories films. Posts keep their site category: PC games 4050, consoles 1010-1180 (NDS, PSP, Wii, Xbox 360, PS3, Wii U, PS4), applications 4000/4030 (macOS)/4060 (iPhone), concerts and music videos 3020, unknown 8000. A request searches the sections holding its category (PC: applications and games), keeps only matching posts before their pages are loaded, stops after 100 result pages (it paged until an empty page), and categories without a section return `[]` without a request.
- **warezomen labelled every row with the requested category (or 2000)**: the results table's Type column was never read. Rows are now labelled by it (checked live: Movie 2000, TV 5000, Game 4050, Software 4000, Music 3000, Other 8000), a request keeps only its rows (the page cap counts those), and categories the site has no type for return `[]` without a request.
- **crawli found nothing, and never more than films**: the site now sends its pages base64-encoded for a script to write, so the parser saw no results (0 for every query, checked live); it also only accepted film rows (`a.sres3`), so TV, music, apps and games searches always came back empty, and every result carried the requested category (apps were 5020). The page is decoded, rows of every section are parsed and labelled by their section class (sres1 apps 4000, sres2 music 3000, sres3 film 2000, sres5 games 4050, sres7 serie 5000, unsorted 8000; episode titles are TV even where filed as films). A PC request searches apps and games, a TV request every section (`/serie/` only lists series pages, the episodes are filed as films), results are filtered by the requested category, and categories without a section return `[]` without a request.
- **mygully and boerse labelled every thread 2000**: series, music, books and games alike, and XXX/Other requests searched the Video forum. Threads are now labelled by the forum searched (Video 2000, series by their title 5000, Audio 3000, Text/Dokumente 7000, Games 4050), filtered by the requested category, and categories without a forum return `[]` before the login; console requests too, since the Games forums hold PC and console games alike. Not checked live (both need credentials). `categories.is_series_title()` (scene `S01E02`/`S03` tokens, "Staffel") replaces the series regexes of nima4k, crawli and scnlog; scnlog now also labels foreign season packs 5020.
- **ddlvalley labelled every post 2000, ddlspot dropped games from PC requests**: ddlvalley now labels posts by the WordPress category searched (games 4050, apps 4000 instead of 5020 TV/Foreign; site-wide posts by their title, series 5000, otherwise 8000) and searches apps and games for a PC request. ddlspot labels games 4050 (were 4000 like software, and a PC request kept software only), unknown types 8000 (were films), filters rows by the requested category while paging (child and unmapped categories used to return every row). Categories without a section return `[]` before the browser starts. Not checked live (Cloudflare Turnstile).
- **byte labelled episodes, software, lossless music and more as films**: its category map knew a third of the site's categories and fell back to 2000, audiobooks were 7020 (EBook), documentaries 5000, consoles PC games, programs 5020. The map now covers the site's menu (checked live: films 2000, English films 2010, series/season packs/episodes 5000, documentaries 5080, PC games 4050, consoles 1000/1030/1180, programs 4000/4030/4070, music 3000, lossless 3040, concerts 3020, audiobooks 3030, books 7000, XXX 6000, unknown 8000). A request sends the site category only where one group holds the whole family (films, TV, books, XXX), otherwise it searches every group; rows are filtered by the requested category before their pages load, and categories the site lacks return `[]` without a request.
- **hdsource, jjs, movieblog and serienjunkies answered categories they do not have**: their filters only knew movie and TV requests, so Audio/PC/Books/Other requests got every film and series, and the TV-only serienjunkies answered movie requests with series. They now return `[]` without a request for such categories, filter with `categories.category_matches()` (a season request means series), and hdsource labels games 4050 (was 4000). jjs and movieblog carried the same copied `_filter_by_category`; it is gone, as is the unused `categories` attribute of serienjunkies.
- **sto labelled series with the requested TV category and with quality codes**: a 5070 request labelled every series anime, and genres were mapped to quality subcategories (drama 5030 TV/SD, horror 5040 TV/HD, fantasy 5050). Series are now labelled from their genres alone (anime/animation 5070, documentaries 5080, otherwise 5000) and filtered by the requested category (an anime request no longer returns every series); non-TV requests return `[]` before any request.
- **Removed `is_tv_category()` / `is_movie_category()`** from `infrastructure/plugins/constants.py`: the plugins answer category requests with `categories.py` now and nothing used them any more.
- **megakino_to and movie4k answered an anime request with every series**: `DataApiPluginBase` filtered by the API's film/series type only. Results are now filtered by the served category (`categories.py`), so TV/Anime and TV/Documentary requests keep their series.

### Docs: README Overhaul, CONTRIBUTING, Docker Compose, Generated Plugin List
- **README rewritten for self-hosters** (Stremio first, Arr indexer second): header with badges, "How it works" Mermaid diagram, features grouped by area, Docker Compose quick start, step-by-step Stremio/Prowlarr setup, table of the most important settings, FAQ/troubleshooting, disclaimer, acknowledgements, and an open "How Scavengarr is built" section (largely AI-written, under strict review, TDD, Clean Architecture, quality gates). It names no sites.
- **`CONTRIBUTING.md`**: development setup, tests, code style, project structure, tech stack, plugin and resolver guides, branch/commit rules, AI-assisted contributions (moved out of the README).
- **`docker-compose.yml`**: builds `Dockerfile.prod` locally, mounts `data/` and `plugins/`, keeps the cache in a named volume (the image runs as a non-root user), `shm_size: 1gb` for Chromium; optional profiles `solver` (Byparr) and `redis`.
- **`docs/plugins.md`** is generated from the plugin metadata by `scripts/generate_plugin_list.py` (website, mirrors, content, engine, languages, disclaimer on top); `tests/unit/infrastructure/test_plugin_list_doc.py` fails when it is outdated. Regenerate after adding or changing a plugin.
- `pyproject.toml` description updated.

### Feature: Stremio Answers Within a Deadline, Only Playable Streams
Measured with a Stremio-style harness (18 titles, every returned stream played): answers took 17–38 s (median 19.8 s) and 24 % of the returned streams did not play. Plan and results: `docs/plans/stremio-latency.md`.
- **`plugin_timeout_seconds` is a search budget from the request start** (new default 10 s, was 30 s per plugin). Plugins queue for concurrency slots (two query variants × all plugins), and each plugin's timeout used to start only when it got a slot, so the search alone took up to 35 s. Plugins still running at the deadline are cut, queued ones skipped. A timeout counts for the circuit breaker when the plugin had at least half the budget.
- **New `stremio.stream_deadline_seconds`** (default 15 s): overall budget per stream request. Hoster resolution stops there (at least 2 s after the search) and returns what is resolved; unfinished resolutions are cancelled. `stremio_deadline_ms` stays unused.
- **Per-hoster resolution in rank order**: hosters resolve in parallel, the streams of one hoster one after another until one works, so the best *working* stream per hoster is returned; before, only the best-ranked link per hoster was resolved and the hoster disappeared when that link was dead. Resolving all candidates at once is not an option: the burst of connections to dozens of CDNs made the home router block the machine like a port scan.
- **Playback check (`stremio.verify_streams`, default on)**: `HosterResolverRegistry` fetches the first bytes of every resolved URL with its playback headers (`check_playable`); error status, HTML or an HLS answer without `#EXTM3U` drop the stream, and the result is cached like a failed resolution. Catches e.g. mixdrop/supervideo pages returned instead of video, CDN 404/502 and expired TLS certificates.
- **Circuit breaker cooldown doubles** with every failed half-open trial (60 s → … → 1 h), reset on success.
- **`data/config.yaml`**: `plugin_timeout_seconds: 10`, `stream_deadline_seconds: 15`, `verify_streams: true`. Measured cold (18 titles): answers within 15.1 s (was up to 38.5 s, median 19.8 s), 96 % of the returned streams play (was 76 %), 17/18 titles with a playable stream as before; 12 s / 17 s gave the same streams 2 s later. Behind AIOStreams set the addon timeout to 17000 ms (`docs/features/stremio-addon.md`).

### Fix: A Network Blip No Longer Hides Hosters for 15 Minutes
- **Link validation backs off on unreachable hosts** instead of skipping them for a flat 15 minutes: the first connection failure skips a host (and the failed URL) for 60 s, every further one doubles that up to 15 min, and the first answer from the host resets it. Measured 2026-09-29: a 40 s outage of the local network marked voe.sx, vinovo, kinoger, fsst and the moflix hosts unreachable, and every Stremio request of the next 15 minutes came back without them (anime and series with zero streams).

### Chore: AGENTS.md Is the Single Agent Instruction File
- **`CLAUDE.md` merged into `AGENTS.md` and removed**: one instruction file for every coding agent (AGENTS.md convention). The OpenSpec-managed block stays at the top of `AGENTS.md` (`openspec update` rewrites it); the former `CLAUDE.md` content follows unchanged apart from its title and self-references. Claude Code (v2.1.277+) reads `AGENTS.md` automatically when no `CLAUDE.md` exists; `.claude/` (hooks, skills, settings) is unaffected. References in the commit skill, `openspec/project.md` and the integration test plan now point to `AGENTS.md`; historical CHANGELOG and plan entries keep the old name.

### Chore: Editor and Tool Output Hygiene
- `.vscode/settings.json` sets `"files.eol": "\n"`: VS Code saves new files with LF, matching `.gitattributes` and the `mixed-line-ending` hook.
- `.gitignore` ignores `.playwright-mcp/` (snapshots, console logs and screenshots written by the playwright-mcp browser tool).

### Fix: No Site Pages or Login Pages as Download Links
- **kinox**: the mirror AJAX (`/aGET/Mirror/...`) now answers JSON with the iframe HTML escaped inside (`"Stream": "<iframe src=\"\/redirect\/<hash>\"...>"`); the plugin found no iframe and fell back to its own detail page as the "download" link, which link validation accepted (200) for Torznab. It now reads the JSON, makes relative iframe URLs absolute, resolves kinox's own `/redirect/<hash>` links to the hoster (`_resolve_own_links`) and drops results without a hoster link. Live the redirects currently loop on a JavaScript "Verifizierung" page, even in a real browser with the site's own mirror click, so kinox returns no results until the site fixes them (KNOWN_ISSUES).
- **filmpalast** skips hoster account pages listed as streams (`https://vixeo.io/login` for "Vixeo HD").
- Kept on purpose: movie4k's `cuty.io` / `clk.asia` shortener links and `streampalace.org` pages. JDownloader decrypts both shorteners (`CutyIo`, `ClicksflyCom`), so they are useful in Torznab crawljobs; the Stremio path already drops streams no resolver can play.

### Feature: VOE Mirror Domains, Vixeo Resolver, Thumbnail-Safe Capture
- **VOE mirror domains**: VOE rotates its domains; the resolver now exposes 134 of them (`hoster_resolvers/_voe_domains.py`, from JD2 `VoeSxCrawler` plus `goofy-banana` and `jeremyparticipantanything` seen on plugin sites). Live: a goofy-banana link resolves in 0.7 s; before, no resolver was found. JD2 also lists three domains that Scavengarr maps to Vidhide; they stay there so each domain has one owner.
- **Vixeo resolver** (`vixeo.io`, Vidsonic's new player): the page builds the signed HLS URL in JavaScript, so the stream comes from the browser capture. Live: 4.3 s, playable without headers; dead IDs fail in 0.2 s.
- **Capture ignores seek-preview playlists**: `capture_media` took vixeo's `thumbnails.m3u8` (a playlist of JPEGs) for the stream. URLs containing `thumbnail`, `sprite` or `preview` no longer count.
- Checked and dropped from the uncovered list: `streamdav.com`, `upload.do` and `voe-stream.space` are parked domains (all redirect to `router.parklogic.com`), `odysseusa.cc` answers 404 for its video.

### Feature: FireStream and Playmate Resolvers
- Two hosters from the plugin link scan had no resolver. Both are ports of JDownloader plugins and need no browser:
  - **FireStream** (`firestream.to` → `firestream.site`): the embed page carries a token that `/api/videos/<id>/resolve` trades for a signed HLS URL, on the host that served the page only. Live: 1.0–1.2 s (the browser capture took 14.7 s).
  - **Playmate** (`playmate.to`): `/api/video-meta` checks the file, `/api/s` returns the HLS master; the API rejects non-browser user agents (403). Live: 0.5–0.6 s, dead file recognised in 0.2 s.
- Not implemented, documented instead (see KNOWN_ISSUES): gxplayer bans this network (redirect loop to `banned.php`), embedrise serves only a placeholder `video.mp4` (404) for every video, frdl.to (DDL) sits behind the same ad-tracker JavaScript redirect as vidmoly.

### Fix: Hoster Mirrors (dr0pstream, streamhls, Byse Domains) and Captcha-Gated Players
- **Mirror domains** from the plugin link scan: `dr0pstream.com` → dropload (JD2 `DroploadIo`; 13 of ~190 scanned links), `streamhls.to` → savefiles (same player, image proxy `img.savefiles.com`), and 14 Filemoon/Byse domains (`bysezejataos`, `byse`, `filemooon`, …; JD2 `FilemoonSxCrawler`) now reach their resolver. Filemoon exposes `supported_domains` for the first time.
- **Captcha-gated XFS players**: a page without a video URL but with a Turnstile/reCAPTCHA widget in front of the player (dr0pstream's play button) goes to the stealth browser, whose click passes the widget. Live: dr0pstream → dropcdn HLS master in 0.7–5 s. The dropcdn servers were unreachable from the test network, so playback itself is unverified.
- **Filemoon skips hopeless browser runs**: before starting the browser the resolver asks the Byse details API; a gone video (404) or an embed restricted to other domains (403 `embedding … not allowed`) fails in 0.2 s instead of 18 s of clicking. A Cloudflare 403 is not taken as a verdict.

### Fix: Vidara Streams via the JSON API
- vidara.so and its mirror vidaraa.cc serve the StreamUp backend through a JSON API (`POST /api/stream`, JD2 `VidaraTo`), not the page/AJAX flow the Strmup resolver used, so every vidara link failed (`strmup_no_hls_url`) and vidaraa was not dispatched at all. Vidara hosts now use the API; file IDs may carry an `/e/` prefix and be 12+ characters. Live: both links → HLS master in 0.3 s.

### Test: Live Stremio End-to-End Use Case
- `tests/live/test_stremio_e2e_live.py` (opt-in, `-m live`) starts the real app in-process (composition lifespan, `httpx.ASGITransport`), asks for the streams of Oppenheimer and Breaking Bad S01E01 and plays them like Stremio: `proxyHeaders`, HLS proxy or direct CDN, master → media playlist → first segment. Passes when one stream delivers media. Links are scraped fresh on each run. Live run after the resolver fixes: film 6 of 7 streams play (supervideo's CDN is the exception), episode 2 of 2; both tests pass in about 2 minutes.

### Fix: Veev Streams Failed to Play in Stremio
- veevcdn binds the stream token to the User-Agent that resolved it. The resolver used the app client's UA (`Scavengarr/0.1.0`), Stremio played with the browser UA from `proxyHeaders`, and every veev stream answered 403 (found by the end-to-end Stremio run: resolved, listed, unplayable). The resolver now resolves with the browser UA and returns it in `ResolvedStream.headers`, which `build_behavior_hints` passes on (live: 206 with the returned headers).

### Fix: GoodStream Links on goodstream.one
- GoodStream moved from `.uno` to `.one` and links now look like `/video/embed/4Ky4/680x420` (short IDs); the resolver rejected them as `invalid_url`. The config accepts `/video/embed/<id>` next to the 12-character XFS IDs and knows the new dead-file page ("No such file").

### Fix: Cloudflare-Protected Hoster Players via Browser Capture
- **XFS video hosters, DoodStream and SuperVideo fall back to the stealth browser** when the embed page answers with a Cloudflare challenge: savefiles, bigwarp, streamwish mirrors and every dood mirror (all redirect to the challenged `playmogo.com`) returned `None` before. The shared helper `hoster_resolvers/_browser.py` (`capture_stream`) turns `StealthPool.capture_media` into a `ResolvedStream`; `create_all_xfs_resolvers()` and `DoodStreamResolver` take an optional `stealth_pool`, wired in `composition.py`. SuperVideo's own Playwright HTML fetch is replaced by the same capture (live: `supervideo.cc` behind Cloudflare → HLS master in 1.2 s).
- **Dead files end the capture early**: `capture_media` checks the page title and visible text for dead-file notices after the Cloudflare step and after every play click. Live: a dead dood link fails in 0.7 s, a dead savefiles link (the notice appears only after the click) in 8.5 s instead of 18 s.
- **XFS**: offline-marker and error-redirect checks shared by the video and DDL paths (`_is_dead`).
- Known issue: SuperVideo's CDN redirects non-browser clients to ad trackers (see `docs/features/hoster-resolvers.md`).

### Fix: Filemoon Streams via Browser Capture
- **Filemoon resolved nothing**: the Byse player added a proof-of-work captcha ("click play to verify you're a human") between attestation and playback, and the attestation now rejects the invented viewer/device IDs (HTTP 400). The PoW is a custom memory-hard hash (~65k attempts at difficulty 16), far too slow in Python and one more thing that changes with every Byse update.
- **`StealthPool.capture_media(url, timeout=...)`**: opens a player page in the stealth browser (Cloudflare solved if present), waits for autoplay, clicks the play button up to 3 times (ad popups closed) and returns the first stream request (`.m3u8`/`.mpd`/`.mp4`/`master.txt`) with its Referer. The page loads with CSS (only media downloads are cut) so the click hits the button.
- **Filemoon** parses legacy pages (packed JS, direct HLS) as before and captures the stream of Byse player pages or blocked pages from the browser. Live: `filemoon.to` → HLS master playlist in 2–5 s, playlist fetchable with the returned Referer. The Byse API code (ECDSA attestation, AES-GCM decryption) is gone, and with it the `cryptography` dependency.

### Fix: Hoster Mirror Domains Reach Their Resolver
- **Mirror dispatch**: DoodStream, VidGuard, Strmup, DDownload and Serienstream kept their mirror lists private (`_DOMAINS` checked inside `resolve()`), so the registry never routed `d0000d.com`, `dood.to`, `myvidplay.com`, `vgembed.com`, `vidara.so`, … to them; without a plugin hint such links fell through to content-type probing. They now expose `supported_domains`. Streamtape gets a mirror list (JD2 `StreamtapeCom` + mirrors seen on plugin sites: `streamta.pe`, `strtape`, `shavetape`, `tapeblocker`, `streamtapeadblockuser`, `gettapeads`, …), DoodStream adds `playmogo`/`pooop` (every dood mirror currently redirects to `playmogo.com`).
- **Whitespace in links**: the registry strips the URL before dispatch; a scraped streamtape link ending in `\n` raised inside the resolver.

### Captcha: animeloads Releases with Real Download Links
- **One Torznab result per release** (was one per series with the media page as "download"): for the first 10 series, the media page's release tabs are read (group, resolution → `1080p`/`720p`/…, notes, audio/subtitle languages, package size, archive password), e.g. `One Punch Man (2015) [12/12] [720p WebRip] [Japanese | Sub: German] [Riitz-chan]`. Stremio (`isolated_search`) keeps series results with their preview stream.
- **Links on grab** (`GrabResolvingPlugin`): the site's "odd one out" image captcha (5 × 48 px images) is solved inside the page by comparing pixels on a canvas (no image library); verification and link requests use the site's jQuery in the page's main world. Links arrive as Click'n'Load packages, decrypted by the new `infrastructure/plugins/clicknload.py` (`cryptography` becomes a direct dependency). Anonymous users need one captcha per episode, so only releases up to 13 episodes are resolved; with `SCAVENGARR_ANIMELOADS_USERNAME`/`_PASSWORD` the whole release comes with one captcha (login path covered by unit tests only, no test account). Rejected answers (the site answers the same for a wrong pick and a rate limit) are retried up to 5 times with a pause.
- **Archive passwords**: `SearchResult.metadata["archive_password"]` goes into the crawljob's `extractPasswords` (`CrawlJobFactory`).
- Live: search "One Punch Man" → 30 release results in 8 s; grab of a 12-episode release → 12 rapidgator links in ~70 s. Grabs take about 6 s per episode, which can come close to an Arr app's download timeout.

### Anti-Bot: Challenge Clearances Survive Restarts
- **`ClearanceStore`** (`infrastructure/browser/clearance_store.py`): after a solved Cloudflare challenge, the clearance cookies (`cf_clearance`, DDoS-Guard `__ddg*`) are stored per name/domain in the cache (`CachePort`: diskcache or Redis) until they expire, and restored into every new browser context (stealth context and all Playwright plugin contexts, isolated ones included). Other cookies (sessions, logins) are not stored; cookie values are never logged.
- Live (kinoger): first process solves the Turnstile in 4.9 s; a second process restores the cookie and loads the page without a challenge in 0.9 s. Useful for restarts within the cookie lifetime (~30 min for `cf_clearance`).
- Chosen over a persistent Chromium profile, which has a single browser context (no per-plugin/per-request isolation) and allows one process per profile (`docs/plans/captcha-solving.md`, Decisions).
- `PlaywrightPluginBase._remember_clearance(page)` for plugins whose gate is not Cloudflare (DDoS-Guard).

### Resolver: vinovo Works Again, DoodStream Plays
- **New dedicated `VinovoResolver`** (`hoster_resolvers/vinovo.py`, port of the JDownloader `VinovoTo` stream path): Turnstile only guards vinovo's official download button; the player path needs no captcha — page token from `<meta name="token">` and CDN base from `data-base` on `/e/{id}`, `POST /api/file/url/{id}` → stream token → `{data-base}/stream/{token}`. The token is bound to the resolving User-Agent (returned in the playback headers, as for veev). Live: vinovo links from movie2k resolve and play (206 `video/mp4`); dead files ("Video not found") return `None`. The CDN can take ~30 s to the first byte. vinovo left the XFS configs: 22 individual + 12 generic DDL + 25 XFS = 59 resolvers.
- **DoodStream streams are captured again**: in the stealth browser the player passes its invisible Turnstile, but its CDN URL (`…cloudatacdn.com/…~id?token=…`) has no file extension, so `StealthPool.capture_media()` missed it. Requests of the page's video element (resource type `media`) now count as the stream too. Live: `doodstream.com/d/…` → playable MP4 (206) with `Referer: https://playmogo.com/`.

### Anti-Bot: Optional Byparr/FlareSolverr Sidecar
- **New config `playwright.solver_url`** (env `SCAVENGARR_PLAYWRIGHT_SOLVER_URL`, unset by default): base URL of a Byparr or FlareSolverr sidecar. `SolverFetcher` (`infrastructure/browser/solver_fetcher.py`) implements `BrowserFetcherPort` over the FlareSolverr v1 API (`request.get`; JSON bodies rendered in a bare `<pre>` are unwrapped; `resolve_redirect` returns the solver's final URL when it left the host).
- **Order**: own browser first, solver when it fails (`ChainedBrowserFetcher`); with `browser_fallback: false` the solver is used alone. Not verified against a running Byparr (no Docker in the dev container); covered by respx tests of the documented API.

### Captcha: Shared Challenge Detector
- **`detect_challenge(status, html, headers)`** (`infrastructure/captcha/detect.py`) classifies a response as `cloudflare_page`, `ddos_guard`, `turnstile`, `hcaptcha`, `recaptcha` or `altcha` (page blocks before embedded widgets). `is_cloudflare_challenge()` is now a view on it (unchanged semantics).
- **Health prober** counts DDoS-Guard blocks (body markers or `server: ddos-guard` on 403/503) as captcha like Cloudflare, so DDoS-Guard-protected plugins no longer score as healthy; a captcha widget on a working homepage does not count. Hits log `health_probe_challenge` with the kind.
- **`HttpxPluginBase._fetch_text()`** logs the challenge kind on errors and browser fallbacks.

### Captcha: nox Download Links via ALTCHA, Resolved at Grab Time
- **ALTCHA solver** (`infrastructure/captcha/altcha.py`): solves ALTCHA v2 proof-of-work challenges (PBKDF2/SHA-256/384/512) in-process, no browser and no external service. ALTCHA is a cost-based captcha, not a human test; nox's challenge (cost 5000, prefix `00`) takes well under a second (run via `asyncio.to_thread`).
- **Grab-time link resolution**: new optional plugin capability `GrabResolvingPlugin.resolve_download(url)`. Jobs of such plugins carry `CrawlJob.resolve_plugin`; `GET /api/v1/download/{job_id}` resolves the links through `CrawlJobResolveUseCase` when the job is grabbed, stores the resolved job (repeated grabs need no second captcha) and answers `502` if nothing could be resolved. Links behind a captcha or a download quota are no longer resolved for every search result.
- **nox serves real hoster links** (live: release page → `https://filer.net/folder/…` in 0.8 s): the crawljob used to contain only the release page, which JDownloader cannot handle. `resolve_download()` looks up the release's online links, solves one ALTCHA challenge for a pass token that unlocks all of them (`/go/{downloadToken}/url?cp=…`) and skips offline links before the captcha. Every unlocked link counts against nox's hourly/weekly limit for anonymous users, hence grab time instead of search time.
- **Refusals are logged**: a refused gateway link logs `nox_unlock_refused` with status, `message` and `reason` (`hourly_limit`, `weekly_limit`, `captcha_required`, `expired`), so a grab that ends in `502` shows why. Live smoke: `tests/live/test_grab_resolve_live.py` (one grab per run to spare the quota).

### Fix: Devcontainer Chromium System Libraries
- `setup.sh` installs the app's Patchright Chromium with `--with-deps`: the system libraries (`libatk` etc.) came only from the optional playwright-mcp step, and when that failed no browser could start, headless or headful.

### Chore: Leaner Claude Code Context
- **Slash commands → skills**: `.claude/commands/` replaced by `.claude/skills/` (`commit`, `test`, `new-plugin`, `new-resolver`). Skills load only their description up front and can be picked by the model itself. `new-plugin` no longer describes the removed YAML/Scrapy plugins; `new-resolver` is new and checks XFS/generic-DDL reuse and JDownloader sources first.
- **`format-and-lint.sh` reports what ruff cannot fix**: remaining lint/syntax errors go back to Claude (exit 2) instead of being swallowed until pre-commit. Uses the project `.venv` ruff directly (falls back to the main checkout's venv inside git worktrees, skips when none), no ANSI colors.
- **Compact pytest output**: `addopts` gains `-q --tb=short`; under Claude Code (`CLAUDECODE=1`) `tests/conftest.py` also disables colors, because the agent shell's `FORCE_COLOR` wrapped every progress dot in ANSI escapes.
- **Fewer caveman skills**: `setup.sh` installs only `caveman`, `cavecrew`, `caveman-explore`, `caveman-review`, `caveman-compress`, `caveman-help`, `caveman-stats` instead of all 14 (Cloud-only skills and the `/commit`-colliding `caveman-commit` dropped).

### Fix: Bugs Found by the Docs Audit
- **Torznab: unknown plugin returns 404 everywhere**: `/torznab/{plugin}/health` and the `extended=1` test probe caught only `TorznabPluginNotFound`, but the registry raises `PluginNotFoundError`, so unknown plugins produced 500 (dev) / 200 (prod). Both exceptions are now mapped to 404. E2E tests now mock the real registry exception (the old mocks hid the bug).
- **Torznab: non-numeric `cat` returns 400**: `cat=abc` raised an unhandled `ValueError` (500). It now raises `TorznabBadRequest` → empty feed with HTTP 400.
- **Torznab caps: `limits default` is 100**: caps advertised `default="50"` while search defaults to `limit=100`; `TorznabCaps.limits_default` now matches.
- **Config: rate-limit/retry env vars work**: `SCAVENGARR_RATE_LIMIT_ADAPTIVE`, `_MIN_RPS`, `_MAX_RPS` and `SCAVENGARR_HTTP_RETRY_MAX_ATTEMPTS`, `_BACKOFF_BASE`, `_MAX_BACKOFF` were read by `EnvOverrides` but dropped by the loader (missing from its flat-key map); they now apply.
- **Config: cache backend via env**: new `SCAVENGARR_CACHE_BACKEND`, `SCAVENGARR_CACHE_REDIS_URL` and `SCAVENGARR_CACHE_MAX_CONCURRENT`. The unprefixed `CACHE_*` variables suggested by a comment were never read (the section is validated from the merged dict); the comment now says so.
- **CLI: `HOST`/`PORT` from `--dotenv` apply**: the CLI read them before `load_config()` loaded the `.env` file; they are now resolved afterwards (`--host`/`--port` still win). First CLI unit tests (`tests/unit/interfaces/test_cli.py`).
- **Config: one set of defaults**: Pydantic field defaults in `schema.py` disagreed with `defaults.py` (`scoring.enabled`, `stremio.max_concurrent_plugins`, `http.user_agent`, `cache.dir`). The schema now mirrors the effective values (no runtime change); a test keeps them in sync.
- **Scoring: alias domains count as supported**: `MiniSearchProber` classified links only against resolver names, so alias domains (`filelions` → Vidhide, `luluvdo` → Lulustream) lowered the supported-hoster ratio. New `HosterResolverRegistry.supported_domains` (names + aliases) is wired in instead. The docs' claim that background probes skip `provides="both"` plugins was wrong (`get_by_provides("stream")` includes them) and is removed.
- **Removed unused dependencies**: `lxml` and `beautifulsoup4` (plus transitive `soupsieve`) were declared but imported nowhere; plugins parse HTML with stdlib `html.parser`. Stale `load_all()` mention removed from the `PluginRegistry` docstring.
- **Stremio `/health` lists supported hosters**: the endpoint called a non-existent `list_hosters()` and swallowed the error, so `supported_hosters` was always empty. It now reads `HosterResolverRegistry.supported_hosters`; the E2E test mock is spec'd against the real class.

### Chore: LF Line Endings and Versioned Claude Code Hooks
- **All text files normalized to LF**: `.gitattributes` widened from `*.md` to `* text=auto eol=lf` and the index renormalized (227 files, mostly `.py`; EOL-only, `git diff --ignore-cr-at-eol` is empty). New `mixed-line-ending --fix=lf` pre-commit hook catches CRLF before commit.
- **Claude Code hooks fixed and versioned**: `.claude/hooks/` and `.claude/commands/` are no longer gitignored. Both hooks had CRLF shebangs, failed with exit 127 and therefore never ran (Claude Code treats non-2 exits as non-blocking). `block-dangerous.sh` rewritten: one `jq` call, no `grep` subprocesses, per-command matching (no false hits across `&&`/`;`/`|`), and new blocks for `git push -f`/`+refspec`, pushes to `…:main`, commits/pushes while `main` is checked out, `rm -fr`/`--recursive` on `/`, `~`, `$HOME`, and `git clean -f`; `--force-with-lease` is allowed.

### Fix: Playwright Live Smoke Tests Never Ran
- **`tests/live/conftest.py` Chromium check is async**: it used the sync API inside the running event loop, raised every time and made all Playwright smoke tests skip with "Chromium not installed". With the fix, 2 of 9 Playwright plugins pass live (animeloads, moflix); ddlspot, ddlvalley, scnsrc (Cloudflare Turnstile) and streamworld fail; boerse, byte hit a network error; myboerse, mygully need credentials. Findings in `docs/plans/plugin-repair.md`.

### Anti-Bot: Patchright instead of playwright + playwright-stealth
- **Browser driver swapped to Patchright** (`patchright` ^1.63, Chromium 153; drop-in Playwright fork): removes the `Runtime.enable`/`Console.enable` and automation-flag leaks that Cloudflare detects. `playwright` and `playwright-stealth` are no longer dependencies; all imports use `patchright.async_api`.
- **No forced User-Agent in browser contexts**: contexts keep Patchright's real UA (a fixed Chrome 131 UA disagreed with the Chromium client hints). New `_browser_user_agent` (default `None`) and `_context_options()`, also used by the boerse/mygully login contexts; `_user_agent` stays for httpx side requests.
- **`PlaywrightPluginBase._stealth` renamed to `_block_resources`**: it now only controls aborting image/font/CSS requests; singleton and per-request contexts share `_configure_context()`. `StealthPool` no longer applies playwright-stealth.

### Fix: Torznab Link Validation Flooded the Network
- **Only the requested page is validated.** A Torznab search validated every result (1000 results ≈ 3000 hoster links) and built a CrawlJob for each before slicing to the requested page (Prowlarr: `limit=100`). The search cache now holds the unvalidated plugin results; each request validates them in order, `limit` at a time, until `offset + limit` valid results exist, and builds CrawlJobs only for that page. Live ("Iron Man"): scnlog 299 instead of 2999 link checks, streamcloud 32 s instead of 257 s.
- **Unreachable hosts are skipped.** `HttpLinkValidator` remembers a host for 15 minutes after a connection-level failure (`ConnectError`/`ConnectTimeout`) and sends no GET retry; at most 4 validations per host run at once. Dead hosters (uploaded.net, ul.to, go4up, uptobox, ...) used to get one connection attempt per link. These bursts made the home router treat the machine as a port scanner and block it ("No route to host"), after which every other request failed as well; the live run now shows 0 blocked connection checks (was 24 of ~85).

### Fix: hdfilme Links from the devideosrc Player
- Movies and series now take their hoster links from the embedded devideosrc.co player via the shared `devideosrc` helper (meinecloud and the `su-spoiler` episode lists are gone from the site); season/episode filtering via `devideosrc.filter_episodes()`. Live: category browsing returns results again (36 movies, 34 series per 40 items; S01E01 filter works). Keyword search still returns nothing until the site fixes its search (PHP fatal error).

### Removed: streamworld Plugin
- The site is gone: streamworld.ws serves an empty web-server page and streamworld.co became a legal watchlist/subscription app without hoster links. Plugin, unit tests and live smoke entry removed; 41 plugins (34 httpx + 7 Playwright).

### Test: Live Smoke Tests Use the Browser Fallback
- `tests/live/conftest.py` wires the Cloudflare browser fallback (`StealthPool` via `HttpxPluginBase.set_browser_fetcher`) like `composition.py`, per test and lazily (Chromium only starts on the first browser fetch). Before, filmfans, kinoger and serienfans failed the live smoke with 0 results although they work in the app. filmfans and serienfans still hit the 60 s smoke budget (the sites rate-limit, a full run takes ~140 s) and show up as skipped.

### Fix: boerse (and mygully session hand-over)
- **Back to results** (live: 74 relevant results for "Iron Man", ~200 s; was a "network error"): the login ran in its own context, but its session cookies (and Cloudflare clearance) only reached a context through `_prepare_context()`, which `isolated_search()` calls *before* `search()` logs in, and a plain `search()` never. The search page therefore met a fresh challenge without session ("no searchform"). `search()` now hands the session to the context it actually uses right after `_ensure_session()`; mygully uses the same pattern and gets the same fix.
- Search results are the `a#thread_title_NNN` anchors only; the sidebar's "latest threads" links used to leak in (games like "Caves of Qud" for "Iron Man"). The next page is the `rel="next"` link (`›`); `»` is vBulletin's *last* page and used to skip straight to it.

### Fix: dataload
- **Back to results** (live: 107 for "Iron Man" in ~34 s; was 0): the search POST lacked XenForo's CSRF field, which the forum now enforces (HTTP 400). The session token is read from the post-login page (`<html data-csrf="...">`) and sent as `_xfToken`. Search result pages paginate with `?page=N`, which the parser missed (it only knew `page-N`); the next page now comes from XenForo's `pageNav-jump--next` link, so pagination works again (was 1 page).

### Fix: kinoking
- **Back to results** (live: 73 for "Iron Man" in ~9 s, Breaking Bad S01 episodes; was 0): the site was redesigned. Search cards are `div.fav-data-source` with `data-id`/`data-type`/`data-title`, paginated with `&page=N` (50 per page, up to 20 pages). Movie pages embed `const SERVERS = [...]` (named servers with hoster mirrors): aggregator players (meinecloud, vidsync, cinesrc), non-German servers (`(EN)`, `(FR)`) and the title-matching `group_kk_*` servers (they mix up sequels) are skipped, and each server contributes at most 3 mirrors. Series pages embed `const allEpisodesData = [...]` with each episode's own `video_links`, so a series costs one request; results are per episode (`<Series> S01E02`), first season unless a season is requested. Genres are no longer on the pages, so the genre→category map is gone (movies 2000, series 5000, caller's category wins). Per-request timeout raised to 30 s: a cold movie page collects its "(LIVE)" servers first.

### Fix: streamkiste
- **Back to results** (live: 52 for "Iron Man" in ~18 s, 24 for "Breaking Bad"; was 0): links come from the embedded devideosrc.co player via the shared `devideosrc` helper, like streamcloud (MeineCloud is gone). The site embeds the *series* player for movies too; `devideosrc.fetch_links()` now falls back to the movie player when the series page carries no token and reports which kind answered (`PlayerLinks.kind`), so movies stay movies. New `devideosrc.filter_episodes()` (moved from streamcloud) gives streamkiste season/episode filtering. The detail parser reads metadata only; the dead meinecloud/onclick helpers are gone. `_DOMAINS` now starts with `streamkiste.bid` (`.taxi` redirects there).

### Fix: streamcloud
- **Back to results** (live: 52 for "Iron Man" in ~16 s, 24 for "Breaking Bad"; was 0): hoster links moved from meinecloud.click and the season tabs to an embedded **devideosrc.co** player (`/movie/<imdb>`, `/serial/<imdb>`). New shared helper `scavengarr.infrastructure.plugins.devideosrc`: detects the player on a detail page, reads the signed token from the player page and loads the hoster embeds from `POST /api/embed-links` (no captcha; only the separate download embed is Turnstile-gated). Series links are labelled `<season>x<episode> <hoster>`, so the existing season/episode filter keeps working. The detail parser now reads metadata only (values in `<div>` or `<span>`); dead meinecloud/tab parsing and `_domain_from_url` are gone. `_DOMAINS` is now `streamcloud.download` (`.plus` → `.uno` → `.download`).
- devideosrc's player pages are always loaded past Cloudflare's cache (cached copies carry expired tokens and even cached 429 answers); a 429 there is retried with a fresh URL.

### Fix: RetryTransport Retried Cached 429s
- A 429/503 served from Cloudflare's cache (`cf-cache-status: HIT/STALE/UPDATING`) is now returned at once. Before, the transport retried it with backoff although a cached answer never changes, and each retry halved the domain's adaptive rate (a streamcloud search slowed from ~16 s to ~140 s).

### Fix: nox
- **Back to results** (live: 31 for "Iron Man" in ~1.5 s; browse: 73 recent releases; was 0): the `/api/frontend/...` API is gone. Search now pages through `GET /api/search?q=&page=` (media entries, 20 per page, up to 10 pages) and lists each entry's releases from `GET /api/media/{slug}` (bounded concurrency; releases without online links are skipped). Browse (empty query) uses `GET /api/releases/recent/{days}`. Release links point to the new release page `/media/{slug}?release={id}` (the old `/release/{slug}` is 404); posters come from `/api/image/w342/…`. Type `episode` is now `series`; documentaries (`doku`) map to 5080. Category filtering uses the shared `_category_matches()`.

### Fix: fireani
- **Back to results** (live: 4 for "Naruto", 32 over 2 pages for "one", ~2–4 s; was 0): the REST API (`/api/anime/...`) is gone. Search is now server-rendered: `/search?q=&page=` is parsed from the page's `__NUXT_DATA__` payload (small devalue resolver in the plugin; 30 per page, paginated up to ~1000 items). Seasons and links come from the site's Connect RPC endpoints (`POST /api.v1.anime.AnimeService/GetAnime` and `/GetEpisode`, JSON). Fields are camelCase now (`animeSeasons`, `animeEpisodeLinks`, `voteAvg`). The site's player sits behind a Turnstile check, but only in the browser: the RPC answers without a token.

### Fix: hdfilme Domain (Site Still Broken Upstream)
- `_DOMAINS` now `hdfilme.cafe`: hdfilme.legal → .press → .party → .bid all redirect there. The plugin still returns 0 results because the site's own search is broken (every `?do=search` request, from httpx and from a real browser, answers with a PHP fatal error in `engine/mods/sfilter/filter.php`; DLE quick search is disabled). Film links also moved from meinecloud.click to `devideosrc.co/embed/download/<imdb>`, behind a Turnstile gate. Listed under KNOWN_ISSUES until the site is fixed.

### Fix: byte
- **Back to results, now an httpx plugin** (live: 266 results for "Iron Man" in ~30 s, was 0 or a timeout): byte.to serves search, detail pages and link widgets as plain HTML, so the plugin moved from `PlaywrightPluginBase` to `HttpxPluginBase` (Cloudflare challenges, should they return, go through the shared browser fallback). Links now come from the per-hoster widgets (`/widgets/button.php?…`, fetched directly instead of rendering iframes); the hoster is read from `<img title="rapidgator.net">`, links flagged offline (`red-dot`) are skipped, and byte's own `go.php?hash=` redirector is resolved to its target (e.g. filecrypt). Size and category are parsed from the new `<B>Größe:</B> 7,14 GB` cells (label and value share a cell). Plugin count: 34 httpx + 8 Playwright. byte.to slows its responses down after a burst of ~2000 requests (one full 1000-item search), so back-to-back full searches can take several minutes.

### Fix: scnlog
- **Back to results** (live: 992 of the 1000-item cap for "Iron Man", was 0): the site moved to a new layout. Search results are `li.row` › `div.title` › `a`, the next page is `a.next`; detail pages have the title in `h1.single-title` and plain links (no `external` class, URL as link text) inside `div.download`, so the hoster name comes from the link's domain.

### Resolver: veev.to Works Again
- **New dedicated `VeevResolver`** (`hoster_resolvers/veev.py`, port of JDownloader `VeevTo`): the player API path needs no captcha — LZW-decode the `window._vvto` token, call `/dl?op=player_api&cmd=gi`, decode `file.dv[0].s` into a direct MP4 URL. Live: 2 of 4 veev links from movie2k/moflix/megakino resolve to playable MP4 (206 `video/mp4`); the other two are offline and correctly return `None`. veev left the XFS configs (`needs_captcha`): 18 individual + 12 generic DDL + 26 XFS = 56 resolvers.
- **vinovo and wolfstream stay disabled** after a headful retest: vinovo needs an embedded Turnstile token posted to its API, wolfstream redirects to an ad domain via anti-bot JS.

### Fix: serienfans
- **Back to results** (live: 35 releases for "Breaking Bad", was 0): search API, series pages, season API and index pages go through `_fetch_text()` (Cloudflare → browser fallback); `/external/2/<hash>` links of the returned results are resolved to their filecrypt containers. Same 429 rate limit as filmfans (~2.5 min uncached).

### Fix: kinoger
- **Back to results** (live: 4 streams for "Iron Man" in 7 s, was 0): search and detail pages go through `_fetch_text()` (Cloudflare → browser fallback). Stream links point to external hosters and need no resolution. In the Stremio path (`plugin_timeout_seconds` 15 s in `data/config.yaml`) the very first search after start can time out while the challenge is solved; later searches reuse the clearance cookie of the stealth context.

### Fix: filmfans
- **Back to results** (live: 13 releases of "Iron Man" 2008, was 0): search API, movie pages and the release API go through `_fetch_text()` (Cloudflare → browser fallback); `/external/<hash>` download links are resolved to their filecrypt containers, dead ones dropped. The site rate-limits bursts (429): an uncached search takes ~2–2.5 min, which can exceed Prowlarr's request timeout (results are cached 15 min).

### Anti-Bot: Link-Out Resolution, Rate-Limit Retry, Host Memo
- **`BrowserFetcherPort.resolve_redirect()`** / `StealthPool.resolve_redirect()`: first off-site URL of a redirect chain, via `request` events (routes only see the first URL of a chain). `HttpxPluginBase._resolve_redirect()` tries httpx first (`Location` header), `_resolve_result_links()` applies it to final results: `/external/<hash>` links behind Cloudflare would otherwise fail link validation and downloaders.
- **Rate limits**: `StealthPool.fetch_text()`/`resolve_redirect()` retry 429/502/503/504 after 2 s, 5 s, 10 s (shared `_navigate()`).
- **Cloudflare host memo**: after a challenge, a host skips plain httpx for 30 min (`HttpxPluginBase._cf_blocked_until`).
- `HttpxPluginBase._parse_json_text()` for JSON bodies from `_fetch_text()`.

### Anti-Bot: Browser Fallback for httpx Plugins
- **New port `BrowserFetcherPort`** (`domain/ports/browser_fetcher.py`): `fetch_text(url, *, timeout) -> str | None`.
- **`StealthPool.fetch_text()`** implements it: navigate, solve the challenge, return the DOM for HTML or re-fetch non-HTML (JSON) in-page for the raw body; bounded by `fetch_concurrency` (`min(stremio.max_concurrent_playwright, 2)`). Live: filmfans page 22 s (challenge) then its JSON API 2 s, kinoger search 13 s, serienfans detail 5 s.
- **`HttpxPluginBase._fetch_text(url, params=...)`**: plain GET; on a Cloudflare challenge the injected fetcher loads the URL instead. `HttpxPluginBase.set_browser_fetcher()` is wired in composition.
- **New config `playwright.browser_fallback`** (default `true`, env `SCAVENGARR_PLAYWRIGHT_BROWSER_FALLBACK`).
- **`read_when_settled()`**: page reads that race a post-challenge reload ("Execution context was destroyed") are retried after `domcontentloaded`; used by `fetch_text()` and `_fetch_page_html()`.

### Test: Live Smoke Caps Results at 50
- `tests/live/conftest.py` sets `search_max_results` to 50 (autouse fixture, same mechanism as the Stremio path): smoke tests check that a plugin works, not that it scrapes 1000 items within 90 s. Playwright smoke now: animeloads, ddlspot, ddlvalley, moflix, scnsrc pass; byte (timeout), boerse (network error), streamworld (0 results) remain for `docs/plans/plugin-repair.md`.

### Fix: scnsrc
- **Back to results** (live: 37 of 42 posts for "Iron Man", was 0): search pages now list only title and category, so release name and links are loaded from each post page (new `_PostPageParser`, covers the TV `tvshow_info` layout and the film/P2P info table; only Torrent/Usenet/NZB anchors inside `storycontent`). Titles are the scene release names. Post pages are rate-limited (nginx 503): concurrency 2 with retry backoff; search pages skip `networkidle`.

### Browser: Post-Challenge Settle and Shared Retry
- **`solve_cloudflare()` waits for the post-challenge redirect** (drops `__cf_chl_tk`) and `domcontentloaded` before returning; reading the page right after the title changed failed with "page is navigating".
- **`PlaywrightPluginBase._fetch_page_html()`** gains `wait_for_idle` and `retry_backoff_s`: transient failures (429/502/503/504, no response) are retried after each backoff, other errors (e.g. 404) fail at once. ddlspot and ddlvalley use it instead of their own page handling.

### Fix: ddlvalley
- **Back to results** (live: 250 hits for "Iron Man", was 0; 58 before the rate-limit fix): Turnstile solved by the new solver. Post pages are rate-limited by nginx (503 "Service Temporarily Unavailable" for ~75% of posts at 5 parallel fetches), so detail concurrency is 2 and 503s are retried after 2 s and 4 s. Search pages skip the `networkidle` wait (server-rendered WordPress): 27 search pages in 13 s instead of ~2 min.

### Fix: ddlspot
- **Back to results** (live: 30 hits for "Iron Man" in 8 s): Turnstile solved by the new solver; detail pages now load in the cleared browser context (plain HTTP gets an empty 200 body, which silently yielded 0 links); domain `www.ddlspot.com` (bare domain redirects); full titles from the link's `title` attribute instead of the truncated, space-less link text.

### Anti-Bot: Turnstile Solver
- **New `infrastructure/browser/turnstile.py`**: `solve_cloudflare(page, timeout_ms=...)` returns at once on a normal page, gives a challenge ~3 s to auto-clear, then clicks the Turnstile checkbox inside the `challenges.cloudflare.com` iframe (re-click every 8 s). Used by `PlaywrightPluginBase._wait_for_cloudflare()` (timeout default 15 s → 30 s) and `StealthPool.wait_for_cloudflare()`. Live: ddlspot and scnsrc challenges solved with one click in ~4 s (headful).
- **Challenge responses no longer abort navigation**: Cloudflare answers with 403/503, and `_navigate_and_wait()`, `_fetch_page_html()` and `_verify_domain()` gave up on any status `>= 400` before the challenge could be solved. New `_passes_cloudflare()` only fails on error statuses that are not a challenge page.

### Anti-Bot: One Chromium Process
- **Browser code moved to `infrastructure/browser/`**: `SharedBrowserPool` (from `plugins/`), `StealthPool` and `cloudflare.py` (from `hoster_resolvers/`).
- **`StealthPool` runs on the shared Chromium** (`browser_pool=` instead of its own launch): one browser process instead of two, saving roughly 420–700 MiB PSS when both were in use. It recreates its context after a browser relaunch; composition creates the shared pool first and cleans up the stealth context before the browser.

### Anti-Bot: Headful Browser by Default (Xvfb)
- **`playwright.headless` now defaults to `false`**: headful Chromium when a display exists, because interactive Cloudflare Turnstile rejects every headless browser. New `infrastructure/browser/display.py` (`resolve_headless()`): without `DISPLAY` all three launch sites (shared pool, stealth pool, standalone plugin) fall back to headless and log `browser_headful_no_display` once.
- **Docker**: runtime image installs `xvfb`; new `docker/entrypoint.sh` starts Xvfb on `:99` and `exec`s the CLI (SIGTERM still reaches the app). `SCAVENGARR_PLAYWRIGHT_HEADLESS=false`.
- **RAM** (PSS, measured): Chromium idle 230 → 420 MiB, 3 pages 451 → 697 MiB, Xvfb ~70 MiB.

### Chore: Patchright Chromium Install
- `Dockerfile.prod` installs the browser with `python -m patchright install chromium` (stage renamed `browsers`; cache path `~/.cache/ms-playwright` unchanged). `.devcontainer/setup.sh` installs the Patchright Chromium for the venv on every attach.

### Chore: Xvfb in the Dev Container
- **`.devcontainer/setup.sh` installs `xvfb`** (only if `Xvfb` is missing): interactive Cloudflare Turnstile rejects every headless browser, while headful Patchright under `xvfb-run` clears filmfans, kinoger, serienfans, ddlspot, ddlvalley and scnsrc (anti-bot Phase 0, `docs/plans/antibot-patchright.md`).

### Docs: Audit and Style Unification
- **Every doc checked against the code**: all feature, architecture, plan, refactor and OpenSpec documents plus README were audited claim by claim and corrected. Among the fixes:
  - health endpoints are `/api/v1/healthz` and `/api/v1/readyz`, including the Docker healthcheck examples
  - `CACHE_*` env vars are ignored, so Redis is configured via YAML
  - CrawlJob TTL is fixed at 1 h, and CrawlJobs are stored as JSON, not pickle
  - the plugin concurrency default is 5, not 3
  - all plugins are imported at startup, not lazily
  - `HttpxSearchEngine` only validates links
  - removed non-existent APIs: `LinkValidatorPort`, `PluginValidationError`, `load_all()`, the stage engine and result dedup
  - effective config defaults are documented
  - README facts corrected: license GPL-3.0, Python 3.12–3.13, clone URL, no `--factory` flag
- **Current behaviour documented**: known bugs are documented as they behave today, with `Known issue` notes, and config keys nothing reads are marked "currently unused". Code fixes follow separately.
- **Consolidation**: `plugin-system.md` covers discovery, registry, overrides and dispatch, and `python-plugins.md` is the plugin author guide. `docs/architecture/codeplan.md` shrank from 1641 to 270 lines (module map, design invariants, dependency chains). Plans and OpenSpec changes carry accurate, dated status lines and ticked checklists.
- **Unified style**: every doc has the same header (back link, H1, one-line summary), `---` between sections, unwrapped paragraphs, ` — ` dashes and a `Source Code References` table.
- **Config comments**: `data/config.yaml` comments now state the real precedence (`.env` acts as env vars) and that `cache.max_concurrent`/`cache.redis_url` are YAML-only. The remaining German comments in `pyproject.toml`, `.pre-commit-config.yaml` and `Dockerfile` are translated.
- **Markdown syntax cleanup**: every code fence now declares a language (`text`, `python`, `http`, `bash`, ...), `***` separators replaced by `---`, non-breaking hyphens in `docs/PYTHON-BEST-PRACTICES.md` replaced so its table-of-contents anchors resolve.
- **Root `AGENTS.md` reduced** to the `openspec update`-managed block (restored missing `<!-- OPENSPEC:START -->` marker) plus a pointer to `CLAUDE.md`. The removed agent guide was outdated (Docker commands, `commitlint`/`mypy` pre-commit hooks and a `redact_config_for_logging()` helper that do not exist). The Conventional Commits rule moved into `CLAUDE.md`.

### Docs: Slim CLAUDE.md
- **`CLAUDE.md` cut from 42 KB to 9 KB**: it is loaded into every AI session, so it now keeps only the rules that apply to every change (workflow, dependency rule, Python rules, mock patterns, dev container pitfalls) and links to `docs/` for details. The hand-maintained test file tree was dropped (derivable from `tests/`), the unconfigured docs-mcp-server section removed.
- **Markdown line endings unified**: all `.md` files converted to LF; `.gitattributes` enforces `eol=lf` for `*.md`.
- **Guides moved to feature docs**: "Adding a New Plugin" (site analysis, mandatory search standards) now lives in `docs/features/python-plugins.md`, "Adding a New Resolver" (non-XFS workflow, test checklist, JDownloader sources) in `docs/features/hoster-resolvers.md`.

### Refactor: StremioStreamUseCase Split
- **Episode filter moved to infrastructure**: `filter_by_episode()` (guessit-based) now lives in `infrastructure/stremio/episode_filter.py` and is injected into `StremioStreamUseCase` as `episode_filter_fn`. The application layer no longer imports guessit directly.
- **Stream building extracted**: stream formatting, hoster dedup, direct-video detection, behaviorHints, cache links and proxy URL building moved to `application/stremio/stream_builder.py` (pure functions, tests in `test_stremio_stream_builder.py`).
- **Query building extracted**: search query normalisation, base-title fallback queries and multi-language reference/query construction moved to `application/stremio/queries.py` (tests in `test_stremio_queries.py`).
- **`PluginSearchRunner` extracted**: plugin fan-out (query fallback dedup, budget slots, per-plugin timeout, circuit breaker, metrics, episode filter, validation, browser warmup) moved to `application/stremio/plugin_search.py`. `StremioStreamUseCase` builds it from its existing dependencies, so the constructor API is unchanged. Benchmarks exercise the runner directly. Removed the unused `max_concurrent_*` attributes. `stremio_stream.py` shrank from 1374 to 670 lines. 14 new runner tests.

### Live Tests Opt-In
- **`poetry run pytest` excludes live tests by default** (`addopts = "--ignore=tests/benchmark -m \"not live\""`). Live smoke tests hit real websites, so broken external sites made the mandatory pre-commit test run permanently red. Run them explicitly with `poetry run pytest -m live`.

### Dev Container: Node 22 + Caveman
- **Node 22 via devcontainer feature**: `ghcr.io/devcontainers/features/node:1` (version 22) replaces the apt `nodejs` fallback in `setup.sh` (Debian bookworm ships Node 18, too old for the `skills` CLI). `setup.sh` now fails early if Node lacks `node:util.styleText` (< 20.12) and installs npm globals without `sudo` (nvm prefix).
- **Caveman auto-install**: `setup.sh` installs the `@caveman-ai/cli` CLI and the `JuliusBrussee/caveman` Claude Code skills (`npx skills add ... -g -a claude-code`) on every attach, so a rebuilt container needs no manual setup. Failures only warn.

### Dev Container: No Docker, playwright-mcp via stdio
- **Docker removed from the devcontainer**: the docker-in-docker feature never worked on nftables-only host kernels ("can't initialize iptables table `nat'"), and the `docker-compose.yml` MCP stack (docs-mcp-server, llm-context, jaeger, nginx proxy) was unused by Scavengarr. Removed the dind feature, the compose file, the Docker steps in `setup.sh` and the MCP `forwardPorts` (only 7979 remains).
- **playwright-mcp over stdio**: `.mcp.json` is now committed and starts `@playwright/mcp@0.0.82` via `npx` (headless Chromium, isolated profile). `setup.sh` installs the matching Chromium with system dependencies. No container, port or daemon needed.
- **docs-mcp-server** is no longer configured.

### Dev Container Git Access
- **Push without DevPod tunnel**: the devcontainer installs `gh` (feature `github-cli`) and `setup.sh` runs `gh auth setup-git` when `GH_TOKEN` is set in `.env.devcontainer`, so `git push`/`gh pr` work even when the DevPod credential tunnel (`localhost:12049`) is down. `setup.sh` also sets `user.name`/`user.email` from `GIT_AUTHOR_NAME`/`GIT_AUTHOR_EMAIL`.

### JDownloader Reference Sync
- **Auto-updating JDownloader sources**: `.devcontainer/sync-jdownloader.sh` keeps `.devdata/JDownloader2/plugins/` and `controlling/` as SVN working copies of `svn://svn.jdownloader.org/jdownloader/trunk/src/jd/` (replaces the former manual copies). Runs on every devcontainer start via `postStartCommand`, never blocks startup on network errors, and writes changed files + commit messages of the last update to `.devdata/JDownloader2/CHANGES.md`.

### Documentation Drift Fixes
- **CLI docs corrected**: the CLI uses stdlib `argparse` (entry point `poetry run start` → `scavengarr.interfaces.cli:start`), not Typer. Fixed in `CLAUDE.md`, `AGENTS.md`, `README.md`, `docs/features/`.
- **Test inventory**: `CLAUDE.md` test tree now lists all 21 previously missing test files (incl. new `unit/interfaces/`); test counts updated across docs (4137 total = 3905 unit + 169 E2E + 25 integration + 38 live).

### animeloads Plugin Tests
- **56 unit tests** for the `animeloads` Playwright plugin (`tests/unit/infrastructure/test_animeloads_plugin.py`): category mapping/filtering, `SearchResult` building (embed vs. media page links, metadata, truncation), search flow incl. season/episode restriction, pagination (page URLs, empty page stop, `_MAX_PAGES`, `max_results`), DDoS-Guard wait handling, and page cleanup on errors. `animeloads` was the only plugin without unit tests.

### Dependency Cleanup
- **Fix: uninstallable lockfile**: `poetry.lock` pinned `lancedb 0.5.7` (pulled in via `crewai-tools`), which no longer exists on PyPI — `poetry install` failed on every fresh environment. Removed the unused dev dependencies `crewai`, `crewai-tools[mcp]`, `openinference-instrumentation-crewai` and `mcp` (not imported anywhere) and re-locked; ~100 transitive packages dropped.

### Concurrency Benchmark Suite & Auto-Tune Fix
- **Benchmark suite** (`tests/benchmark/`): synthetic E2E benchmarks for ConcurrencyPool slot tuning, probe/validation semaphore sweeps, and formula-vs-empirical comparison. Runs manually via `poetry run pytest tests/benchmark/ -s -v` (excluded from the default `pytest` run via `addopts = "--ignore=tests/benchmark"`).
- **Fix: probe/validation hard-caps**: `probe_concurrency` capped at 100 (was unbounded — 128 on 32 cores), `validation_max_concurrent` capped at 120 (was 160). Caps derived from benchmark diminishing-returns analysis (<5% throughput gain beyond threshold).
- **New test**: `test_extreme_host_probe_validation_capped` verifies caps on 32-core hosts.

### Code Review: Tests, Performance & Quality
- **HLS proxy E2E tests** (11 tests): full request-response cycle for `GET /proxy/{stream_id}/{path}` — manifest rewriting, segment streaming, error codes (400/404/502/503), query string fallback, CORS headers, header forwarding.
- **`_build_stream_from_resolved` unit tests** (8 tests): proxy URL construction for HLS with/without headers, direct MP4, echo-URL skip, query string preservation, custom manifest filenames.
- **`_resolve_query_string` unit tests** (5 tests): request query priority, video URL fallback, empty handling.
- **guessit offloaded to thread pool**: `_filter_by_episode()` and `convert_search_results()` now run in `run_in_executor()` to avoid blocking the async event loop during CPU-bound release name parsing.

### HLS Proxy Query String Fix & Resolver HEAD Verification
- **Fix: HLS proxy preserves CDN auth tokens**: proxy URL now extracts the actual manifest filename and query string from the video URL instead of hardcoding `master.m3u8`. CDN auth tokens (e.g. `?t=abc123&expires=...`) are forwarded correctly, fixing 403 errors on all HLS streams routed through the proxy.
- **Fix: proxy endpoint query string fallback**: when Stremio strips query params from the proxy URL, the endpoint falls back to the original video URL's query string to recover CDN auth tokens.
- **HEAD verification for VOE, Streamtape, SuperVideo resolvers**: extracted video URLs are now HEAD-checked before returning. Unreachable CDN URLs (403, timeout) return `None` instead of reaching Stremio as "video is not supported".
- **6 new tests**: HEAD verification failure tests for VOE (2), Streamtape (2), SuperVideo (2).

### HLS Proxy Endpoint & XFS Video Verification
- **HLS proxy endpoint** (`GET /api/v1/stremio/proxy/{stream_id}/{path:path}`): proxies HLS manifest and segment requests with correct CDN headers (Referer etc.) for hosters like Dropload whose CDNs require headers on all sub-requests, not just the master manifest. Rewrites absolute CDN URLs in variant playlists so the HLS player routes all fetches through the proxy.
- **XFS video URL verification**: after extracting a video URL from an XFS embed page, the resolver now performs a HEAD check against the CDN to verify the URL is actually reachable. Filters out IP-locked CDN tokens (e.g. LULUVID/LULUVDOO) that always return 403 because the token was bound to Cloudflare's edge IP.
- **Extended CachedStreamLink entity**: new `video_url`, `video_headers`, and `is_hls` fields support HLS proxy routing. Backward-compatible deserialization for existing cache entries.
- **New module** `infrastructure/stremio/hls_proxy.py`: manifest rewriting (`rewrite_manifest`), CDN base extraction (`cdn_base_from_url`), CDN fetch helper (`fetch_hls_resource`), URL builder (`build_cdn_url`).
- **34 new tests**: HLS proxy helpers (18), XFS video verification (5), stream link cache HLS fields (3), E2E streamable assertion updated for proxy URLs.

### Container-Aware Resource Detection & Adaptive Auto-Tuning
- **cgroup-aware resource detector** (`infrastructure/resource_detector.py`): reads actual CPU/memory limits from Linux cgroups (v2 → v1 → OS fallback) instead of host values. Detection order mirrors JVM `UseContainerSupport` and Go 1.25. On a 16-core/64GB host with `--cpus=2 --memory=2g`, the system now correctly sees 2 CPUs and 2GB RAM
- **Extended auto-tuning** (`_auto_tune()` in composition root): when `stremio.auto_tune_all=true` (default), ALL concurrency parameters are scaled proportionally to detected resources:
  - `max_concurrent_plugins`: `min(cpu*3, mem_gb*2, 30)` — was only CPU-based
  - `max_concurrent_playwright`: `min(cpu, mem_gb/0.15, 10)` — RAM-limited (~150MB per BrowserContext)
  - `probe_concurrency`: `cpu * 4` — lightweight HEAD requests scale linearly
  - `validation_max_concurrent`: `cpu * 5` — same as probes
- **Adaptive AIMD rate limiting** (`TokenBucket` in `rate_limiter.py`): per-domain request rates now adjust automatically based on target-server feedback using TCP-style AIMD (Additive Increase / Multiplicative Decrease):
  - Success → rate increases by 10% (capped at `rate_limit_max_rps`, default 50)
  - 429/503 → rate halved immediately (floored at `rate_limit_min_rps`, default 0.5)
  - Timeout → rate reduced by 25%
  - Each domain has its own independent adaptive rate — throttling on site A doesn't affect site B
- **RetryTransport feedback integration**: after every HTTP response, the transport now calls `record_success()` or `record_throttle()` on the rate limiter, enabling real-time rate adaptation
- **New config fields**: `stremio.auto_tune_all` (bool, default true), `http.rate_limit_adaptive` (bool, default true), `http.rate_limit_min_rps` (float, default 0.5), `http.rate_limit_max_rps` (float, default 50.0)
- **48 new tests**: resource detector (cgroup v2/v1/fallback, fractional CPUs, unlimited, frozen dataclass), adaptive rate limiter (AIMD, min/max bounds, domain independence, compounding), auto-tune (5 container scenarios from 1CPU/512MB to 16CPU/64GB), retry transport feedback

### Performance Tuning
- **Aggressive timeout cuts**: HTTP scraping 30→15s, hoster resolution 15→10s, Playwright page load 30→20s, plugin timeout 30→15s, probe timeout 10→5s, stealth probe 15→10s, link validation 5→3s
- **Higher concurrency**: `max_concurrent_plugins` 5→15 with auto-tune cap raised 10→20, `max_concurrent_playwright` added at 7, `validation_max_concurrent` 20→30, `probe_concurrency` 10→20, `max_probe_count` 50→80, plugin default `_max_concurrent` 3→5
- **Configurable resolve semaphore**: `_resolve_top_streams()` now uses `probe_concurrency` from config (was hardcoded at 10)
- **Faster retries**: `retry_max_attempts` 3→2, `retry_backoff_base` 1.0→0.5s, `retry_max_backoff` 30→10s
- **Higher throughput**: `rate_limit_rps` 5→10 per domain, `max_results_per_plugin` 100→50 (less work per plugin, faster turnaround)
- **Longer cache**: `search_ttl_seconds` 900→1800 (30min cache for repeated searches)
- **`resolve_target_count` convention**: 0 = disabled (resolve all streams), replaces magic number

### Playwright Stealth Default
- **Stealth mode now on by default**: `PlaywrightPluginBase._stealth` changed from `False` to `True` — all 9 Playwright plugins (boerse, mygully, moflix, streamworld, animeloads, byte, scnsrc, ddlvalley, ddlspot) now use stealth evasions automatically
- **SuperVideoResolver uses shared StealthPool**: replaced dedicated Playwright browser lifecycle with injected `StealthPool` — eliminates a redundant Chromium process and reuses the stealth-enabled browser pool. Graceful degradation to httpx-only when no pool is available
- **StealthPool `wait_for_cloudflare()` public**: renamed from `_wait_for_cloudflare()` to allow external callers (SuperVideoResolver) to use the Cloudflare wait logic
- **StealthPool always created**: composition root now creates `StealthPool` unconditionally (was gated by `probe_stealth_enabled`), since SuperVideoResolver needs it regardless of probe configuration

### Plugin Fixes (megakino, boerse)
- **megakino**: Fix broken plugin (0 results in live tests). Three issues resolved:
  1. Domain redirect: `megakino.me` now redirects to `megakino1.biz` — updated `_DOMAINS` list with 4 mirrors (`megakino1.biz`, `megakino1.ws`, `megakino1.net`, `megakino.me`)
  2. yg_token JS challenge: detail page GET requests require a `yg_token` cookie — added `_ensure_token()` that fetches `/index.php?yg=token` (204 response sets cookie) before scraping detail pages
  3. iframe extraction: site changed from `<a href="/dl/...">` to `<iframe data-src="https://voe.sx/e/...">` for hoster links — updated `_DetailPageParser` to extract iframe `data-src`/`src` attributes
- **boerse**: Add missing `boerse.tw` mirror domain to `_DOMAINS` and `_INTERNAL_HOSTS` (6 mirrors total: am/tw/sx/im/ai/kz)

### Stremio Streamable Link E2E Tests
- **Fix existing E2E tests**: `test_stremio_endpoint.py` and `test_stremio_series_e2e.py` updated to pass `pool=ConcurrencyPool()` and mock `get_languages`/`get_mode` on the plugin registry — required after the global concurrency pool became mandatory
- **New `test_stremio_streamable_e2e.py`**: 31 comprehensive E2E tests verifying the full Stremio stream pipeline produces genuinely streamable links (direct `.mp4`/`.m3u8` video URLs with `behaviorHints`, or `/play/` proxy URLs). Covers movie resolution (MP4, HLS, mixed, echo filtering, dedup, behaviorHints), series resolution (episode filtering, multi-plugin, high episode numbers), circuit breaker integration, concurrency pool integration, edge cases (all plugins error, all resolvers fail/echo, empty results), and full pipeline roundtrips
- **Test suite total**: 3900 unit + E2E tests (158 E2E)

### Global Concurrency Pool & Playwright Request Isolation
- **ConcurrencyPool**: new infrastructure component (`infrastructure/concurrency.py`) provides a global concurrency budget with separate httpx and Playwright slot pools. Fair-share algorithm dynamically divides slots across active requests: `fair_share = max(1, total_slots // active_requests)`. When a request exits, remaining requests automatically get more slots
- **Per-request BrowserContext isolation**: Playwright plugins now create a fresh `BrowserContext` per request via `isolated_search()`, preventing state corruption when concurrent requests hit the same singleton plugin. Uses `ContextVar[BrowserContext]` to pass the per-request context transparently through `_ensure_context()`
- **`isolated_search()` method**: added to both `PlaywrightPluginBase` and `HttpxPluginBase`. Playwright plugins create an isolated BrowserContext; httpx plugins pass through to `search()` unchanged
- **`_serialize_search` mode**: Playwright plugins that rely on persistent page state (streamworld, moflix) set `_serialize_search = True` to serialize searches via `asyncio.Lock` instead of creating per-request contexts
- **Cookie-based session transfer**: authenticated Playwright plugins (boerse, mygully) now login in a temporary BrowserContext, export cookies, and inject them into per-request contexts via `_prepare_context()` override — enables concurrent searches without session conflicts
- **Domain ports**: `ConcurrencyPoolPort` and `ConcurrencyBudgetPort` protocols in `domain/ports/concurrency.py` keep the use case decoupled from the concrete infrastructure
- **Composition root wiring**: `ConcurrencyPool` is created with `httpx_slots=max_concurrent_plugins` and `pw_slots=max_concurrent_playwright`, then injected into `StremioStreamUseCase`
- **Unified code path**: pool is now required (not optional) — eliminated the dual code path that maintained a local semaphore fallback. `_dispatch_search()` static method cleanly routes to `isolated_search()` or `search()` based on plugin capability

### Resilience & Observability Improvements
- **Circuit breaker for plugins**: new `PluginCircuitBreaker` (infrastructure/circuit_breaker.py) tracks per-plugin failure counts. After 5 consecutive failures (configurable), the breaker opens and skips the plugin for 60s (configurable cooldown). Half-open state allows a single probe request. Integrated into `StremioStreamUseCase` — failures, timeouts, and successes are all recorded. `snapshot()` exposes per-plugin state for diagnostics
- **Graceful shutdown**: new `GracefulShutdown` (infrastructure/graceful_shutdown.py) tracks in-flight HTTP requests via `request_started()` / `request_finished()`. On shutdown, `wait_for_drain(timeout=10.0)` blocks until all in-flight requests complete or the timeout elapses. Integrated into the HTTP middleware and composition root lifespan
- **Health & readiness endpoints**: `/api/v1/healthz` (liveness) and `/api/v1/readyz` (readiness) — readiness returns 503 until startup is complete and during shutdown drain
- **`/api/v1/stats/metrics` endpoint**: exposes runtime metrics as JSON — plugin search stats (count, success rate, avg duration), probe stats, circuit breaker state per plugin, concurrency pool utilisation (slots total/available/active), and shutdown status
- **Playwright browser relaunch retry**: `_launch_standalone(*, retries=1)` retries browser launch once after a 1s delay on failure, with proper cleanup of partial Playwright state between attempts
- **`isolated_search()` context leak fix**: moved `_prepare_context()` and stealth setup inside the `try` block so that the `BrowserContext` is always closed even if preparation fails. Uses `Token[BrowserContext | None] | None` pattern for safe ContextVar reset

### Cineby Plugin Timeout Fix
- **Increase cineby concurrency**: override `_max_concurrent` from 3 → 8 (lightweight JSON API at db.videasy.net handles higher concurrency)
- **Cap detail fetches**: add `_MAX_DETAIL_FETCH = 25` — only the first 25 search results get detail-fetched (IMDB ID, runtime); remaining results are built from search data only. Prevents timeout on broad queries like "Avengers" (100+ results × 3 concurrency = ~15s detail phase → now 25 results × 8 concurrency = ~1.5s)

### Parallel Language Group Search
- **Parallelize `_search_lang_groups()`**: language groups (e.g. German plugins + English plugins) now search concurrently via `asyncio.gather()` instead of sequentially. Saves ~2-5s on multi-language requests where the slower group no longer blocks the faster one

### Shared Playwright Browser Pool & Pre-Warming
- **SharedBrowserPool**: new infrastructure component (`shared_browser.py`) manages a single Chromium process shared by all 9 Playwright plugins. Each plugin gets its own `BrowserContext` for isolation while sharing the underlying browser — eliminates per-plugin ~1-2s browser startup overhead
- **Composition-time pool injection**: plugins receive the shared pool reference at startup via `set_shared_pool()` instead of per-request task injection — eliminates race conditions on plugin state and simplifies the search orchestration
- **Browser pre-warming**: when a Stremio search request arrives and Playwright plugins are present, the browser warmup fires as a background task (named `browser-warmup` with exception callback) immediately while httpx plugins begin searching
- **Parallel Playwright plugins**: Playwright plugins now run concurrently on the shared browser (bounded by `max_concurrent_playwright`, default 5) instead of sequentially via `Semaphore(1)`. PW semaphore is dynamically sized to `min(pw_plugin_count, max_concurrent_playwright)` per request
- **Add `max_concurrent_playwright` config** (default 5): upper bound for parallel Playwright plugin searches; actual concurrency is dynamically capped at the number of PW plugins in the request
- **Ownership-aware cleanup**: `PlaywrightPluginBase.cleanup()` only closes the browser/Playwright when the plugin owns it (standalone mode). When using a shared pool, only the context and page are closed
- **Disconnection recovery**: `_ensure_browser()` checks `browser.is_connected()` and relaunches transparently if the browser has crashed or disconnected. Context and page state are reset on disconnect
- **Resilient cleanup**: `SharedBrowserPool.cleanup()` logs warnings instead of silently swallowing exceptions during browser close / Playwright stop
- **`get_mode()` on PluginRegistryPort**: use case checks plugin mode without loading the full plugin object — avoids double plugin fetch in search orchestration

### Stremio Stream Resolution Performance
- **Skip probe when resolve is active**: probe phase (`probe_at_stream_time`) is now skipped when a resolve callback is configured, since resolution implicitly checks liveness — saves one entire I/O phase (~5-10s)
- **Early-stop resolve**: `_resolve_top_streams()` now uses `asyncio.wait(FIRST_COMPLETED)` and stops once `resolve_target_count` (default 15) genuine video URLs have been extracted, cancelling remaining tasks. Avoids waiting for slow hosters when enough playable streams are ready
- **Add `resolve_target_count` config** (default 15): target number of successfully resolved video streams before early-stop
- **Shared semaphore across query variants**: `_search_with_fallback()` now shares a single semaphore across all query variants (e.g. "Dune: Part Two" + "Dune") instead of creating independent semaphores per variant — prevents connection overload
- **Increase `max_concurrent_plugins` default** from 5 → 10: allows more httpx plugins to search in parallel

### Multi-Language Search & unidecode Migration
- **unidecode for universal transliteration**: replace manual 4-character German umlaut table (`_UMLAUT_TABLE`) and 15-character transliteration table (`_TRANSLITERATION`) with `unidecode` library — supports 130+ Unicode scripts for title matching and search query generation
- **Multi-language plugin support**: plugins now declare `languages: list[str]` (default `["de"]`) instead of `default_language: str`. Backward-compatible property `default_language` returns `languages[0]`
- **Per-language TMDB title resolution**: `get_title_and_year()` and `find_by_imdb_id()` accept a `language` parameter; cache keys include language to avoid cross-language collisions
- **Wikidata title lookup generalized**: IMDB fallback client `_fetch_wikidata_title()` supports any language (was German-only)
- **Multi-language search dispatch**: Stremio use case groups plugins by language, fetches TMDB titles for each unique language in parallel, and searches each group with language-specific queries. A plugin with `languages=["de", "en"]` gets searched with both German and English title queries. Combined `TitleMatchInfo` reference merges all language titles for the title matcher
- **Registry `get_languages()`**: `PluginRegistryPort` exposes `get_languages(name)` to retrieve a plugin's language list

### E2E-Discovered Fixes (Stream Resolution)
- **Domain alias dispatch**: `HosterResolverRegistry` now maps all `supported_domains` from XFS/DDL resolvers, not just the resolver name — fixes dispatch for vidhide family domains (filelions, streamhide, louishide, etc.)
- **Drop unresolvable proxy streams**: when a resolve function is configured but returns `None`, the stream is excluded from Stremio responses instead of creating a `/play/` proxy URL that always 502s
- **Subtitle fallback search queries**: titles with colons (e.g. "Dune: Part One") now generate a second search query using just the base title ("Dune"), because German streaming sites often omit subtitles — improves Dune from 2 to 3 streams, Starship Troopers from 5 to 6
- **Punctuation-safe title matching**: `_normalize()` now strips punctuation (colons, hyphens, etc.) before token comparison, so "dune:" and "dune" are correctly recognized as matching tokens
- **rapidfuzz title matching**: replace `difflib.SequenceMatcher` + custom `_token_similarity()` with `rapidfuzz.fuzz.token_sort_ratio` / `token_set_ratio` for faster, more robust fuzzy matching (C++ backend) — also enhance sequel detection to penalise ANY sequel number mismatch (bidirectional: "Iron Man 2" ref vs "Iron Man 3" result, or "Iron Man 2" ref vs "Iron Man" result)
- **Add goodstream XFS config**: new video hoster resolver for goodstream.one/goodstream.uno (XFS-based, confirmed via JDownloader plugin)
- **Add 6 vidhide domain aliases**: streamhide, louishide, streamvid, availedsmallest, tummulerviolableness, tubelessceliolymph (all parklogic.com anti-adblock protected vidhide family)
- Fix XFS two-step form hosters: detect `<form id="F1" action="/dl">` splash pages and POST to `/dl` with `op=embed&file_code={id}&auto=1` to obtain the actual player page (affects bigwarp, savefiles, streamruby, and other form-based XFS hosters)
- Mark wolfstream as `needs_captcha=True` — embed pages return obfuscated JS redirect (anti-bot), not extractable with httpx
- Fix vidking resolver regex: accept `/embed/tv/{tmdb_id}/{season}/{episode}` paths for series content (was only matching `/embed/movie/`)

### XFS Video Hoster Extraction
- Upgrade XFS resolver from validate-only to full video URL extraction for 18 video hosters
- Extract playable HLS/MP4 URLs from embed pages via JWPlayer config, Dean Edwards packed JS, and Streamwish `hls2` patterns
- Shared video extraction module (`_video_extract.py`) reused by both XFS and Filemoon resolvers
- Add `is_video_hoster` / `needs_captcha` flags to `XFSConfig` for per-hoster behavior
- Video hosters: fetch `/e/{file_id}` embed page → extract video URL → return `ResolvedStream` with Referer header
- DDL hosters (katfile, hexupload, clicknupload, filestore, uptobox, hotlink): keep validate-only behavior
- Captcha-required hosters (veev, vinovo): return None immediately (Cloudflare Turnstile required)
- Add `extra_domains` field to `XFSConfig` for JDownloader-sourced domain aliases (vidhide has 19 aliases)
- Supported video hosters: streamwish, vidmoly, vidoza, vidhide, lulustream, upstream, wolfstream, vidnest, mp4upload, uqload, vidshar, vidroba, vidspeed, bigwarp, dropload, savefiles, funxd, streamruby

### Stremio Playback Fixes
- Filter non-video URLs from Stremio responses: `_is_direct_video_url()` detects embed pages vs actual video URLs (.mp4, .m3u8, HLS patterns)
- Skip unplayable streams at search time: resolvers that only validate availability (XFS, DDL) but cannot extract video URLs are excluded from Stremio results instead of producing guaranteed-502 proxy URLs
- Guard in `/play/` endpoint rejects resolved URLs that are just the embed page echoed back (returns 502 instead of redirecting to HTML)
- Fix VOE resolver: follow JS redirects from voe.sx → rotating domains (e.g. lauradaydo.com), fetch token array from external loader.js script

### Production Bug Fixes (Stream Resolution)
- Fix `http-equiv=` garbage treated as URL in megakino_to/movie4k `_collect_streams()` — reject non-HTTP stream values
- Add belt-and-suspenders URL scheme validation in `HttpLinkValidator.validate_batch()`
- Fix veev resolver regex: accept 12+ char alphanumeric IDs (was exactly 12, veev.to now uses 43-char IDs)
- Fix vidking resolver regex: accept `/embed/movie/{id}` paths used by cineby/videasy plugins
- Re-add goodstream XFS config (previously removed in error — confirmed XFS-based via JDownloader GoodstreamUno.java plugin)

### Torznab Pagination
- Wire `TorznabQuery.offset`/`limit` fields through router → use case for server-side result pagination
- Add `offset` and `limit` query parameters to the Torznab search endpoint (defaults: 0 / 100)
- Prowlarr can now page through cached result sets via standard Torznab pagination

### Cloudflare Detection in Health Probes
- `HealthProber` now detects Cloudflare challenges during HEAD/GET probes:
  - HEAD path: `cf-ray` header + 403/503 status code heuristic
  - GET fallback: body-based marker detection via `is_cloudflare_challenge()`
- `ProbeResult.captcha_detected` is now set by the health prober (was always `False`)
- `compute_health_observation()` returns 0.0 for captcha-blocked probes — CF-blocked plugins rank lower

### Dead Code Cleanup
- Remove empty `infrastructure/scraping/` package (stale from Scrapy removal)
- Remove dead `TorznabAction` type alias (defined but never used)
- Remove dead `CacheBackend` re-export from `infrastructure/cache/__init__.py`
- Remove dead `AgeBucket` re-export from `domain/entities/__init__.py`
- Fix duplicate `AgeBucket` in `query_pool.py` — import from domain instead of redefining
- Remove 3 dead config field assignments in `StremioStreamUseCase` (`stremio_deadline_ms`, `max_items_total`, `max_items_per_plugin`) — stored but never read
- Delete dead `/indexers` data file (obsolete Scrapy reference)
- Delete empty `.env.example`
- Clean orphaned `__pycache__` directories
- Remove dead `PluginRegistry.load_all()` method (never called)
- Remove dead `router` re-export from `interfaces/api/__init__.py`
- Remove dead `PluginStats.last_search_ns` field (written but never read)
- Remove dead `StageResult` dataclass from `domain/plugins/base.py`
- Remove dead `PluginValidationError` from `domain/plugins/exceptions.py`
- Remove dead `LinkValidatorPort` protocol (entire file deleted)
- Remove dead `validation_schema.py` module (entire file deleted)
- Remove dead `test_auth_env_resolution.py` test file (entire file deleted)
- Remove dead `probe_urls()` function from `infrastructure/hoster_resolvers/probe.py`
- Remove dead `_DOMAINS` set and `_is_streamtape_domain()` from `streamtape.py`
- Remove dead `get_german_title()` from `TmdbClientPort`, `HttpxTmdbClient`, and `ImdbFallbackClient`

### HTTP Rate Limiting & 429 Retry (Defense in Depth)
- Add `RetryTransport` — custom httpx transport wrapping all outgoing HTTP requests
  - Proactive: per-domain token-bucket rate limiting via existing `DomainRateLimiter` (5 RPS default)
  - Reactive: automatic retry on 429/503 with exponential backoff + jitter + `Retry-After` header support
  - Configurable: `retry_max_attempts` (default 3), `retry_backoff_base` (1s), `retry_max_backoff` (30s)
- Wire `DomainRateLimiter` (previously dead code) into shared `httpx.AsyncClient` via transport layer
- Remove manual 429 retry from `SuperVideoResolver` (now handled transparently by transport)
- Add 3 config fields: `http.retry_max_attempts`, `http.retry_backoff_base`, `http.retry_max_backoff`

### Plugin Scoring & Probing
Background plugin scoring system that measures plugin health and search quality via EWMA-based probes, then selects only the top-N plugins per Stremio request.

- Add domain entities: `ProbeResult`, `EwmaState`, `PluginScoreSnapshot` with age buckets
- Add `PluginScoreStorePort` protocol and `CachePluginScoreStore` persistence (JSON via CachePort)
- Add pure EWMA scoring functions: `alpha_from_halflife`, `ewma_update`, `compute_confidence`, `compute_health_observation`, `compute_search_observation`, `compute_final_score`
- Add `HealthProber` (HEAD with 405/501 GET fallback, Cloudflare detection) and `MiniSearchProber` (limited search + hoster HEAD checks)
- Enhance `MiniSearchProber` to filter HEAD-checks by supported hosters (from `HosterResolverRegistry`)
- Add `supported_ratio` (5th component, weight 0.25) to `compute_search_observation()` — scores now reflect whether a plugin's result links point to hosters with registered resolvers
- Add `hoster_supported` / `hoster_total` fields to `ProbeResult`
- Add `QueryPoolBuilder` with dynamic TMDB-based query generation (trending + discover endpoints, weekly rotation, German locale, bundled fallback lists)
- Add `ScoringScheduler` background task (health probes daily, search probes 2x/week per plugin/category/bucket)
- Add `ScoringConfig` and extend `StremioConfig` with scoring budget parameters
- Add per-plugin YAML overrides (`PluginOverride` model: timeout, max_concurrent, max_results, enabled)
- Fix YAML config loading for `stremio` and `scoring` sections (pre-existing gap in `_SECTION_KEYS`)
- Wire scoring components in composition root with clean cancellation on shutdown
- Add scored plugin selection in `StremioStreamUseCase` with cold-start fallback and exploration slot
- Add `GET /api/v1/stats/plugin-scores` debug endpoint with query filters
- Add `PluginRegistry.remove()` method for disabling plugins via config overrides

### Hoster Resolver Expansion
- Add 6 new XFS hoster configs: Mp4Upload, Uqload, Vidshar, Vidroba, Hotlink, Vidspeed (27 XFS hosters total)
- Add new SendVid streaming resolver (two-stage: API status check + page video extraction)
- Add new Mediafire DDL resolver (public file info API, offline detection via error 110 + delete_date)
- Add new GoFile DDL resolver (ephemeral guest token with 25-min cache, content availability API)
- Add 6 new XFS hoster configs: StreamRuby, Veev, Lulustream, Upstream, Wolfstream, Vidnest
- Add 20 new StreamWish domain aliases from JDownloader (obeywish, awish, embedwish, etc.)
- Add 5 new Streamtape domain aliases (scloud, strtapeadblock, tapeblocker, etc.)
- Add new StreamUp (strmup) standalone HLS resolver with page + AJAX fallback extraction
- Add `vidara` domain alias to StreamUp resolver (Vidara = StreamUp infrastructure)
- Add new Vidsonic standalone HLS resolver with hex-obfuscated URL decoding
- Wire StrmupResolver and VidsonicResolver in composition root

### Stremio Stream Deduplication
- Add per-hoster deduplication: only the best-ranked stream per hoster is returned
- Prevents duplicate links from the same hoster (e.g., 5 VOE links → 1 best VOE link)
- Applied after sorting, before probing/caching — keeps highest-ranked link per hoster

### Architecture Fixes (Clean Architecture Compliance)
- Remove all infrastructure imports from `StremioStreamUseCase` (application layer)
  - Define `_StremioConfig`, `_StreamSorter`, `_MetricsRecorder` protocols locally
  - Inject `sorter`, `convert_fn`, `filter_fn`, `user_agent`, `max_results_var` via constructor
  - Composition root (interfaces layer) now owns all infrastructure wiring
- Add `@runtime_checkable` to all 9 domain Protocol ports (was only on 2 of 9)
- Make `CrawlJob` entity immutable (`frozen=True`) — built once by factory, never mutated
- Remove explicit Protocol inheritance from `CacheCrawlJobRepository` (duck-typing consistency)

### Stremio Playback: behaviorHints.proxyHeaders
Pre-resolve hoster embed URLs at `/stream` time and emit `behaviorHints.proxyHeaders` so Stremio's local streaming server sends the correct `Referer` and `User-Agent` headers to hoster CDNs. This eliminates buffering caused by 403 rejections on missing headers.

- Add `behavior_hints` field to `StremioStream` domain entity
- Add `_build_behavior_hints()` and `_resolve_top_streams()` to `StremioStreamUseCase`
- Add `ResolveCallback` type and wire `HosterResolverRegistry.resolve` via composition root
- Add `Referer` header to all streaming resolver returns (VOE, Filemoon, SuperVideo, Streamtape)
- Emit `behaviorHints.notWebReady` + `proxyHeaders.request` in Stremio stream JSON
- Fallback to `/play/` proxy redirect for streams that fail pre-resolution

### Performance (Audit)
- Parallelize `_select_plugins()` score fetching with `asyncio.gather()` (was sequential await loop)
- Parallelize `_run_search_cycle()` probes: collect all due probes first, then run concurrently with semaphore
- Offload CPU-bound `filter_by_title_match()` (guessit + SequenceMatcher) to thread pool via `run_in_executor()`
- Add periodic eviction of expired entries in `HosterResolverRegistry` caches (prevents unbounded memory growth)

### Code Quality (Audit)
- Consolidate 12 identical DDL hoster resolvers into parameterised `GenericDDLConfig` + `GenericDDLResolver` (alfafile, alphaddl, fastpic, filecrypt, filefactory, fsst, go4up, mixdrop, nitroflare, 1fichier, turbobit, uploaded)
- Add shared `extract_domain()` utility for URL domain extraction, replacing 15 inline duplicates across resolver modules
- Add `exc_info=True` to 4 `except Exception` handlers in business logic (stremio_stream.py, composition.py)
- Remove dead code across domain, application, infrastructure, and plugin layers
- Consolidate duplicate constants and unused imports

### YAML Plugin Infrastructure Removal (Refactor)
Migrated 3 remaining YAML plugins (warezomen, filmpalast, scnlog) to Python httpx plugins. Removed entire YAML plugin infrastructure: ScrapyAdapter, YAML schema models, YAML loader, YAML discovery, and all associated tests (~3,500 lines deleted). Renamed `HttpxScrapySearchEngine` to `HttpxSearchEngine`. Removed the `scrapy` dependency from `pyproject.toml`.

- Add warezomen Python httpx plugin replacing YAML (`fd4bc98`)
- Add filmpalast Python httpx plugin replacing YAML (`3ae7921`)
- Add scnlog Python httpx plugin replacing YAML (`0fa4f36`)
- Remove YAML plugin infrastructure and scrapy dependency (`42fced9`)
- Rename HttpxScrapySearchEngine to HttpxSearchEngine (`12aa0a5`)

### Plugin Standardization (Refactor)
All 29 Python plugins migrated to shared base classes (`HttpxPluginBase` / `PlaywrightPluginBase`), eliminating 50–100 lines of duplicated boilerplate per plugin (client setup, domain fallback, cleanup, semaphore, user-agent).

- Add `HttpxPluginBase` shared base class for httpx plugins (`16b084b`)
- Add `PlaywrightPluginBase` shared base class for Playwright plugins (`16b084b`)
- Add shared plugin constants and CSS-selector HTML helpers (`b12c5a7`)
- Migrate 5 API-only plugins (einschalten, fireani, haschcon, megakino_to, movie4k) to HttpxPluginBase (`407fef9`)
- Migrate aniworld, dataload, nima4k plugins to HttpxPluginBase (`21030d3`, `bded483`, `e4f7f13`)
- Migrate all remaining 21 plugins to shared base classes (`10d23db`)
- Add missing `season`/`episode` params to 10 plugin `search()` signatures (`deef995`)
- Reorganize configurable settings (`_DOMAINS`, `_MAX_PAGES`, etc.) to top of all 28 plugins with section headers (`a79fb8e`)
- Replace hardcoded year boundary with dynamic `datetime.now().year + 1` in cine plugin (`b3e40e3`)

### New Plugins (40 Python plugins added)
Expanded from 2 plugins (filmpalast YAML + boerse Python) to 42 total plugins (33 httpx + 9 Playwright), covering German streaming, DDL, and anime sites.

**Httpx plugins (33):**
- aniworld.to — anime streaming with domain fallback (`3321775`)
- burningseries (bs.to) — series streaming (`b1e46ff`)
- cine.to — movie streaming via JSON API (`3153df0`)
- dataload (data-load.me) — DDL forum with vBulletin auth (`94004e6`)
- einschalten.in — streaming via JSON API (`a729041`)
- filmfans.org — movie DDL with release parsing (`7924969` → `7cd46ed`)
- fireani.me — anime via JSON API (`160171f`)
- haschcon.com — streaming (`0d65a50`)
- hdfilme.legal — streaming with MeineCloud link extraction (`fdaf283`)
- kinoger.com — streaming with domain fallback (`1c03b95`)
- kinoking.cc — streaming with movie/series detection (`067a634`)
- kinox.to — streaming with 9 mirror domains and AJAX embed extraction (`d645ccf`, `20e40e9`)
- megakino.me — streaming (`ff68aeb`)
- megakino_to (megakino.org) — streaming via JSON API (`df2cf77`)
- movie2k.cx — streaming with 2-stage HTML scraping
- serienfans.org — TV series DDL with JSON search API and season/episode support
- movie4k.sx — streaming via JSON API with cross-language title matching (`52f07dd`, `dfc58db`)
- myboerse.bz — DDL forum with multi-domain fallback (`27b42b4`, `d80c69a`)
- nima4k.org — DDL with category browsing (`d001135`)
- nox.to — DDL archive with JSON API, movies + TV episodes
- sto (s.to/SerienStream) — TV-only streaming (`7924969`, `2a73f16`)
- streamcloud.plus — streaming with domain fallback (`10f3808`)
- streamkiste.taxi — streaming with 5 mirror domains (`ff8c662`, `bea8be1`)
- cineby.gd — streaming via JSON API
- crawli.net — single-stage download search engine
- hd-source.to — DDL with multi-page scraping
- hd-world.cc — DDL archive via WordPress REST API, movies + TV series
- jjs (jjs.page) — DDL with multi-stage scraping
- movieblog.to — DDL blog (WordPress)
- serienjunkies.org — DDL with captcha-protected links
- filmpalast.to — movie/TV streaming (migrated from YAML)
- scnlog.me — scene log with pagination (migrated from YAML)
- warezomen.com — DDL site (migrated from YAML)

**Playwright plugins (9):**
- animeloads (anime-loads.org) — anime with DDoS-Guard bypass (`75176af`, `08cced5`)
- boerse.sx — DDL forum with Cloudflare + vBulletin auth (rewritten, see v0.1.0)
- byte.to — DDL with Cloudflare bypass and iframe link extraction (`2cdab77`)
- ddlspot.com — DDL with pagination up to 1000 results (`fca8947`, `21a0657`)
- ddlvalley.me — DDL WordPress with pagination (`0fedecf`, `3d80cec`)
- moflix (moflix-stream.xyz) — streaming via internal API with Cloudflare bypass (rewritten from httpx, `eaa0002`)
- mygully.com — DDL forum with Cloudflare + vBulletin auth
- scnsrc.me (SceneSource) — scene releases with multi-domain fallback (`cb34282`, `2d930bb`)
- streamworld.ws — streaming (rewritten from httpx to Playwright, `de29957`)

**YAML plugins (removed):**
- filmpalast.to, scnlog.me, warezomen.com — all migrated to Python httpx plugins (see YAML Plugin Infrastructure Removal)

### Stremio Addon
Full Stremio addon integration with manifest, catalog search, and stream resolution. Allows using Scavengarr as a Stremio source for all indexed plugins.

- Add Stremio domain entities, TMDB port, and StremioConfig (`c055303`)
- Add TMDB httpx client with caching and German locale (`c7950ef`)
- Add release name parser with guessit integration (`89b8ca9`)
- Add stream converter for SearchResult → RankedStream (`a4b2e0c`)
- Add configurable stream sorter for Stremio addon (`015dde6`)
- Add StremioCatalogUseCase for TMDB trending and search (`8d7dfbc`)
- Add StremioStreamUseCase for IMDb-to-streams resolution (`526e0c5`)
- Add Stremio router with manifest, catalog, and stream endpoints (`0d5854e`, `fed81df`)
- Add title-match scoring module for Stremio stream filtering (`6a06df9`, `b9454cf`)
- Add `get_title_and_year()` to TMDB client and IMDB fallback (`55a7bf7`, `2af65b1`)
- Add IMDB fallback title resolver for Stremio without API key (`23fe5c4`)
- Add Wikidata German title lookup for IMDB fallback client (`eb8094a`)
- Robust title matching via guessit + multi-candidate scoring (`e0b6e76`)
- Thread `plugin_default_language` through stream converter (`8bf0911`)
- Add `default_language` attribute to all plugins (`c53e04c`)
- Add per-plugin timeout to prevent slow plugins blocking response (`c03a28b`)

### Hoster Resolver System
56 hoster resolvers across three categories: 17 individual resolvers (streaming + DDL), 12 generic DDL resolvers (parameterised `GenericDDLConfig`), and 27 XFS-consolidated resolvers (generic `XFSResolver` with parameterised `XFSConfig`). All resolver tests use respx (httpx-native HTTP mocking).

**Core infrastructure:**
- Add ResolvedStream entity and HosterResolverPort protocol (`f6a3676`)
- Add HosterResolverRegistry with content-type probing fallback (`8a7642b`)
- Add hoster hint fallback for rotating redirect domains (`cfe3314`)
- URL domain priority + redirect following in hoster registry (`b083c0b`)
- Add `cleanup()` to HosterResolverRegistry (`c148640`)
- Integrate hoster resolvers into `/play/` endpoint (`2b1f82c`)
- Cache stream links and generate proxy play URLs (`686b4bf`, `f61e30a`)
- Add `/stremio/play/{stream_id}` endpoint with 302 redirect (`08be69c`)
- Add stream preflight probe to filter dead hoster links at `/stream` time
- Add hybrid Playwright Stealth probe for Cloudflare bypass

**Streaming resolvers (10):**
- Add VOE hoster resolver with multi-method extraction (`242ce2d`)
- Add Streamtape hoster resolver with token extraction (`b163637`)
- Add SuperVideo hoster resolver with XFS video extraction (`d980ebe`)
- Add DoodStream hoster resolver with pass_md5 extraction (`5ba3a58`)
- Add Filemoon hoster resolver with packed JS unpacker (`e9353f3`)
- Add Filemoon Byse SPA API extraction and challenge/attest/decrypt flow (`ad62013`, `8592356`)
- Add packed JS decoder for SuperVideo video URL extraction (`e7baaa6`)
- Add Playwright fallback to SuperVideo for Cloudflare bypass (`7ce90dd`, `4438322`)
- Add 429 rate-limit retry with back-off to SuperVideo resolver (`67babee`)
- Add Mixdrop hoster resolver with token extraction (multi-domain)
- Add VidGuard hoster resolver with multi-domain embed resolution
- Add Vidking hoster resolver with embed page validation
- Add Stmix hoster resolver with embed page validation
- Add SerienStream hoster resolver (s.to / serien.sx domain matching)

**DDL resolvers (15):**
- Add filer.net DDL hoster resolver via public status API
- Add Katfile DDL hoster resolver (XFS offline marker detection)
- Add Rapidgator DDL hoster resolver (website scraping validation)
- Add DDownload DDL hoster resolver (ddownload.com / ddl.to, XFS page check)
- Add Alfafile DDL hoster resolver (page scraping)
- Add AlphaDDL hoster resolver (page scraping)
- Add Fastpic image host resolver (fastpic.org / fastpic.ru)
- Add Filecrypt container resolver (container validation)
- Add FileFactory DDL hoster resolver (page scraping)
- Add FSST hoster resolver (page scraping)
- Add Go4up mirror link resolver (mirror link validation)
- Add Nitroflare DDL hoster resolver (page scraping)
- Add 1fichier DDL hoster resolver (multi-domain page scraping)
- Add Turbobit DDL hoster resolver (multi-domain page scraping)
- Add Uploaded DDL hoster resolver (uploaded.net / ul.to)

**XFS consolidation (27 hosters):**
- Add generic `XFSResolver` with `XFSConfig` dataclass consolidating 27 XFS hosters into one module (`xfs.py`)
- Original 15: Katfile, Hexupload, Clicknupload, Filestore, Uptobox, Funxd, Bigwarp, Dropload, Goodstream, Savefiles, Streamwish (9 domains), Vidmoly, Vidoza, Vinovo, Vidhide (6 domains)
- Added 12 more: Mp4Upload, Uqload, Vidshar, Vidroba, Hotlink, Vidspeed, StreamRuby, Veev, Lulustream, Upstream, Wolfstream, Vidnest
- Parameterised tests auto-generated from all 27 configs
- Delete 15 individual resolver files + 15 individual test files (~4,200 lines removed)

**Test improvements:**
- Migrate all 17 non-XFS resolver test files from AsyncMock to respx (httpx-native HTTP mocking)
- Add live contract test skeleton for resolver smoke tests (`tests/live/test_resolver_live.py`)

### Plugin Improvements
Various fixes and enhancements to individual plugins.

- Rewrite kinoger search parser for redesigned site template (`3cf475c`)
- Rewrite streamworld plugin from httpx to Playwright mode (`de29957`)
- Rewrite moflix plugin from httpx to Playwright mode (`eaa0002`)
- Fix streamkiste parser to handle `<span class="movie-title">` tags (`bea8be1`)
- Fix sto plugin to reject non-TV categories (TV-only site) (`2a73f16`)
- Fix filmpalast.to plugin selectors and change provides to stream (`dfc48a3`)
- Fix animeloads DDoS-Guard detection excludes h1 selector (`08cced5`)
- Optimize sto plugin to fetch only requested episode instead of full season (`bb48c58`)
- Add season/episode filtering to mixed plugins (`9d433f5`, `d109844`, `ea8385f`, `ec643ae`)
- Add `provides` attribute to plugin system (`e38a07b`)
- Add domain fallback to aniworld plugin (`f72fc2d`)
- Add pagination to ddlspot, ddlvalley, scnlog, warezomen, boerse (`21a0657`, `3d80cec`, `a30164b`, `3cfa20f`)
- Add Torznab category filtering for YAML plugins (`5dc3018`)
- Add kinox AJAX embed URL extraction for hoster resolution (`20e40e9`)

### API & Router Improvements
- Centralize `/api/v1/` prefix for all endpoints (`d25ee5c`)
- Rename `main.py` → `app.py`, `cli.py` → `__main__.py` (`b25bf5c`)
- Delegate router to use cases, remove inline business logic (`7c36166`)
- Wire Stremio use cases into AppState and composition (`d3f076d`)

### Search Result Caching
Cache layer for repeated search queries with configurable TTL and cache-hit indicators.

- Add `_search_cache_key()` with SHA-256 hashing of plugin + query + category
- Add cache read/write to `TorznabSearchUseCase` with graceful error handling
- Add `search_ttl_seconds` config (default 900s / 15 minutes, 0 = disabled)
- Add `X-Cache: HIT/MISS` response header to Torznab search responses

### Plugin Fixes (website changes)
Five plugins updated to match changed website structures.

- Fix filmfans release loading: extract `initMovie()` hash and fetch releases via `/api/v1/{hash}` JSON endpoint
- Fix kinoger search parser: update selectors for redesigned DLE template (`shortstory` → detail link extraction)
- Fix megakino_to: add GET fallback for domain verification (HEAD returns 405)
- Fix movie4k: add GET fallback for domain verification (HEAD returns 405)
- Fix streamkiste: rewrite detail parser to extract streams from meinecloud.click external script

### Test Suite Growth (160 → 3963 tests)
Test suite expanded from 160 to 3963 tests (3742 unit + 158 E2E + 25 integration + 38 live) with comprehensive coverage across all layers.

- Add unit tests for all 42 plugin test files
- Add unit tests for all 56 hoster resolvers (17 individual + 12 generic DDL + 27 XFS consolidated)
- Add unit tests for HttpxPluginBase and PlaywrightPluginBase
- Add unit tests for Stremio components (stream converter, stream sorter, TMDB client, title matcher, IMDB fallback)
- Add unit tests for release name parser, plugin registry, HTML selectors
- Add unit tests for stream link cache and hoster registry
- Add unit tests for circuit breaker, concurrency pool, graceful shutdown, metrics endpoint
- Add unit tests for EWMA scoring, plugin score cache, query pool, health prober, search prober, scoring scheduler
- Add 158 E2E tests (46 Torznab endpoint + 112 Stremio endpoint including 31 streamable link tests)
- Add 25 integration tests (config loading, crawljob lifecycle, link validation)
- Add 38 live smoke tests (plugin smoke tests + resolver contract tests)
- Migrate all resolver tests from AsyncMock to respx (httpx-native HTTP mocking)
- Add parameterised XFS resolver tests auto-generated from 27 configs

### Documentation
- Add plugin search standards (categories + pagination up to 1000) (`c1fa2c4`)
- Update agent policy — only for simple mechanical tasks (`e01246c`)
- Add team agents rules to CLAUDE.md (`2a0eddc`)
- Restructure documentation following MasterSelects pattern (`dea6fad`)

---

## v0.1.0 - 2026-02-09 (Initial Release)

First release of Scavengarr as a self-hosted Torznab/Newznab indexer. Includes the core scraping pipeline, plugin system (YAML + Python), Torznab API, CrawlJob packaging, link validation, and a comprehensive unit test suite.

### Boerse Plugin Rewrite
Complete rewrite of the boerse.sx plugin to handle the real site structure, including Cloudflare JS challenge bypass via Playwright and vBulletin form-based authentication.

- Rewrite boerse.py plugin with Playwright for Cloudflare JS challenge bypass (`502c2b7`)
- Rewrite login, search, and link extraction to match real vBulletin site structure (`7e41ad3`)
- Resolve nested `<div>` parsing bug in post content and use full `#searchform` (`9bfecf6`)
- Filter download links to known container hosts only (keeplinks.org, filecrypt.cc, etc.), deduplicate thread URLs by thread ID (`533f6aa`)
- Read boerse credentials lazily in `_ensure_session()` to avoid startup failures when env vars are not yet set (`e090033`)

### Mirror URL Fallback
Automatic domain failover for plugins with multiple mirror URLs. When the primary domain is unreachable, the system probes mirrors and falls back transparently.

- Add `mirror_urls` field to YAML plugin schema for declaring alternative domains (`1591072`)
- Add mirror domain fallback to ScrapyAdapter: probe mirrors on connection failure (`9543e2c`)
- Probe mirror URLs in health endpoint when primary domain is unreachable (`b1b8901`)
- Merge `mirror_urls` into `base_url` as a single-or-list field for simpler plugin config (`4eab6b5`)

### Multi-Link CrawlJob Packaging
CrawlJob system extended to bundle multiple validated download links from different hosters into a single `.crawljob` file, with automatic promotion of alternatives when primary links are dead.

- Multi-link CrawlJob packaging: bundle all valid hoster URLs into a single `.crawljob` artifact (`35326b7`)
- Promote alternative download links when primary link fails HEAD/GET validation (`078fcae`)

### Python Plugin System
New imperative plugin type for sites that require complex logic beyond what YAML selectors can express (authentication, JavaScript interaction, custom parsing).

- Add boerse.sx Python plugin with domain fallback across 5 mirrors and anonymizer link handling (`bf0a9d3`)
- Add Python plugin dispatch to TorznabSearchUseCase: detect `.py` plugins and call their `search()` method (`98e6081`)
- Add env var support to AuthConfig for YAML plugin credentials: `$ENV{VAR_NAME}` syntax (`5b53f00`)
- Align `PluginRegistryPort.get()` return type with concrete registry implementation (`a73c6b9`)

### Link Validation
HTTP-based link validation with parallel execution, HEAD-first strategy, and GET fallback for hosters that block HEAD requests.

- Add GET fallback to HttpLinkValidator for hosters that return 403/405 on HEAD requests (`e69cd54`)
- Add `validate_results()` method to SearchEnginePort protocol for post-search filtering (`d7d1dab`)

### Test Suite
Comprehensive unit test suite covering all three architecture layers with proper mock patterns (sync MagicMock for PluginRegistryPort, AsyncMock for async ports).

- Add comprehensive unit test suite: 160+ tests across domain, application, and infrastructure (`e0674c5`)
  - Domain: CrawlJob entity, TorznabQuery/Item/Caps, SearchResult, plugin schema validation
  - Application: CrawlJobFactory, Torznab caps/indexers/search use cases
  - Infrastructure: parsers, converters, extractors, presenter, link validator, search engine, cache
- Apply ruff format to test files for consistent style (`2040852`)

### Clean Architecture Refactor
Three-phase migration from flat codebase to Clean Architecture with Domain, Application, Infrastructure, and Interfaces layers. See `docs/refactor/COMPLETED/clean-architecture-migration.md` for full details.

**Phase 1: Domain layer cleanup**
- Remove Pydantic from Domain layer, convert all entities to `@dataclass` (`7726ba8`)

**Phase 2: Entity consolidation**
- Consolidate SearchResult definition into single canonical location (`b7bc0be`)

**Phase 3: Adapter reorganization**
- Reorganize all adapters into `infrastructure/` namespace by concern (`d97d7a3`)

**Follow-up commits:**
- Move presenter to infrastructure layer (`8729319`)
- Rename `httpx_scrapy_engine` to `search_engine` for clarity (`56b48df`)
- Rename cache factory for naming consistency (`d32066c`)
- Use shared size parser across layers, eliminating duplication (`de788dc`)
- Consolidate duplicate int parsing into `infrastructure/common/` utils (`7610b9b`)
- Add common utils structure: parsers, converters, extractors (`b0f4cca`)
- Move composition root from application to interfaces layer (correct placement) (`a9eab40`)
- Remove redundant `discover()` calls from use cases and router (`028c932`)
- Parallelize multi-stage scraping with `asyncio.gather` for non-blocking I/O (`f419b5e`)
- Prevent duplicate search results from multi-stage scraping via dedup logic (`6b7fd8d`)

### Code Quality
Codebase-wide standardization of typing patterns, docstring conventions, and language consistency.

- Standardize typing to modern Python 3.10+ syntax (`T | None`, `list[T]`, `dict[K, V]`) and replace ABC with Protocol across all ports (`84995a1`)
- Standardize docstrings: remove redundant comments, ensure consistent English documentation (`2c6278a`)
- Translate all remaining German comments and docstrings to English for international consistency (`04f4b81`, `bcfe059`, `b18711e`, `4e440d7`)
- Apply pre-commit auto-fixes: trailing whitespace, end-of-file, import sorting (`dccc4ad`, `0e6c937`)

### Documentation
Project documentation covering architecture, coding standards, plugin system, and test suite organization.

- Add comprehensive project documentation covering all architecture layers (`d1d4a56`)
- Add typing standards and test suite information to CLAUDE.md (`9c7106a`)
- Document all infrastructure components and their responsibilities in CLAUDE.md (`9abf1c3`)

### Core Infrastructure (Initial)
Foundation of the project: FastAPI server, Scrapy scraping engine, plugin loader, configuration system, and CrawlJob generation.

- Initial content commit: FastAPI/Uvicorn server, Scrapy-based scraping, Playwright integration, structlog logging, diskcache backend (`7fd6747`)
- Add YAML configuration system with pydantic-settings and plugin loader with filesystem discovery (`3889847`)
- Add CrawlJob system for `.crawljob` file generation and assorted bug fixes (`df3e952`)
- Refactoring: improve module structure, separate concerns, clean up imports (`ed53426`)

---

## KNOWN_ISSUES

Current known issues:

- **s.to link-out quota for VPN IPs** (2026-10-04): for a VPN IP s.to's gate is the tier `turnstile_altcha`, and one pass (about 20 s in the browser on a Raspberry Pi 4) unlocks 3 link-outs. Stremio episode requests get s.to for about three requests per pass; a Torznab search resolves the link-outs of every matching episode (about 950 requests and 83 s for "Dark"), and most results keep the s.to link-out.
- **Playmate in tsaridas/stremio-docker's web player** (2026-10-04): the image's nginx answers Playmate's disguised HLS segments (`…_000.css`, `…_001.js`) as web player files, with 404, so Playmate streams fail there (error 81).

- **animeloads captcha quota** (2026-09-29): anime-loads rate-limits captchas per IP (after ~30 captchas within ~40 min every answer was rejected for at least 10 min). An anonymous grab uses one captcha per episode, so only a few grabs per hour succeed; the rest end in HTTP 502. A login (`SCAVENGARR_ANIMELOADS_USERNAME`/`_PASSWORD`) needs one captcha per release.
- **kinox hoster links unreachable** (2026-09-29): every mirror's `/redirect/<hash>` opens a "Verifizierung" page that asks for an image captcha ("Captcha eingeben", 2026-10-01), also in a real browser; kinox returns no results until the site drops it.
- **Hosters without a resolver** (2026-10-02): rubyvidhub.com (kinoger's "go" tab) answered Cloudflare 522 (origin down, streamruby.com too) on every check; embedrise.com plays only a placeholder `video.mp4` that answers 404; frdl.to (freedl.ink, DDL) uses the same ad-tracker redirect as vidmoly. JD2 has plugins for the last two (`EmbedriseCom`, `FreedlInk`) should they become reachable.
- **GoFile links do not resolve** (2026-09-29): GoFile refuses guest lookups (401 `error-notPremium`; its website adds an `X-Website-Token` from an obfuscated script).
- **Vidmoly unreachable behind ad blockers** (2026-09-28): the embed page's script redirect sends non-browser clients (and the stealth browser, when the network blocks ad domains, e.g. Pi-hole) to an ad click tracker; no stream is reachable then.
- **SuperVideo streams need a browser-like player** (2026-09-28): the CDN (`*.serversicuro.cc`) answers non-browser clients with a JavaScript redirect and then ad-tracker redirects; resolution works, playback in players without JavaScript does not.
- **IP-bound streams** (2026-10-03): DoodStream and Vinovo bind a stream URL to the IP that resolved it (`200 error_wrong_ip`, 403 from another IP). They play only when the player's streaming server reaches the internet through Scavengarr's IP, e.g. both behind the same VPN; streams through the HLS proxy are not affected.
- **Dead sites** (2026-10-01): megakino.to and movie4k.sx answer Cloudflare 522 (origin unreachable), so megakino_to and movie4k return nothing and the circuit breaker keeps them out of most requests; cineby's API host no longer resolves, and the shipped config disables it.
- **Cloudflare-protected sites need a headful browser**: ddlspot, ddlvalley, scnsrc, filmfans, kinoger and serienfans only pass the interactive Turnstile with Patchright headful (Xvfb, `playwright.headless: false`) and `playwright.browser_fallback: true`. filmfans and serienfans rate-limit bursts (429): an uncached search takes ~2–2.5 min and can exceed Prowlarr's request timeout. See `docs/plans/antibot-patchright.md`.
