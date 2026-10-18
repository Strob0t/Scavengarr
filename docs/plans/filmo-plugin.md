# The filmo plugin (2026-10-11)

**Question.** The maintainer asked for a plugin for filmo.to and all its alternative domains (2026-10-11). The site analysis below was done by the advisor with playwright-mcp and httpx from the dev container, 18 min; backlog row 63 implements it.

## Domains

- The site links its official domain list: the footer's "Domains" leads to filmo.domains ("Offiziellen Filmo Domains: Alternative Adressen & DNS-Anleitung"). On 2026-10-11 the list names one address, filmo.to, and the page loads nothing after it (no list from a script).
- 30 candidates `filmo.<tld>` (cc, su, sx, lol, tv, ws, me, net, org, io, co, de, click, watch, stream, live, cam, la, vip, app, cx, ac, li, is, nu, rs, pw, ch, at, eu): none serves filmo's app. The test for the app: the build asset name of filmo.to (`/build/assets/app-<hash>.js`) and the link to the domain list. The ones that answer are other businesses or parked: filmo.me, filmo.io, filmo.cam, filmo.ch, filmo.org, filmo.at (to bitly) and filmo.eu (to sedo). filmo.live is another site ("Filmo - Instant 4K Streaming") with neither mark. The rest time out or do not resolve.
- So the plugin's domains are `["filmo.to"]`. A mirror the list names later joins them. JDownloader's sources name no filmo, so there are no dead-domain notes to check.

## The site

- **The stack.** A Laravel app (a Vite build, the `XSRF-TOKEN` and `filmo-session` cookies) behind DDoS-Guard (`server: ddos-guard`, `__ddg*` cookies). httpx with the browser User-Agent got every page with 200; no challenge was seen.
- **The language.** A client without a language choice gets the German UI (`<html lang="de">`); the browser with an English locale got the English one. Titles follow the UI (the German slug `der-astronaut-project-hail-mary` shows as "Project Hail Mary" in English). The UI languages (`/lang/<code>`) change labels, not the catalogue.
- **Movies only.** The navigation has Movies, Popular, genres, collections and letters; `/series`, `/tv`, `/serien` and `/shows` answer 404.
- **Search.** `GET /search?q=<term>`. Up to 8 cards in `section.search-top-results`, each `article.popular-spotlight-card a.popular-spotlight-card__link` with the `href` `/movies/<slug>` and the text genre + title. Broad terms add a grid of further results in `main div.container-wrapper div.row div.col-6` (42 movie links for "star", none for "Movie J" or "Movie K"). There is no pagination control. The search lists loose matches: Movie F also finds Supergirl, Redline and The Beyond.
- **Detail page** `/movies/<slug>`. The title is in `h1`, the release date in the metadata list `dl.ft-meta-definition-list dd.entry-description` (`2014-11-05`); the page names no IMDb or TMDB id. The streams sit in `div.provider-rows > div.provider-row`, one row per audio language (`span.provider-row__lang`: `Deutsch`, `English`), each with chips `[data-provider-chip]`:
  - `aria-label`: the hoster (VOE, Byse);
  - `span.provider-chip__metadata-tag`: the source and the quality (`BluRay`, `720p`);
  - `data-movie-link-id`: the link;
  - `data-p`: a Laravel-encrypted payload (base64 JSON with `iv`, `value` and `mac`, new on every render).

  The same link id stands in both rows: one file with both audio tracks.
- **The link chain** (what the page's script does on Play):
  1. `POST /n` with the JSON `{"p": <data-p>}`, in the detail page's session, with `X-CSRF-TOKEN` (the page's `meta[name=csrf-token]`), `X-XSRF-TOKEN` (the URL-decoded `XSRF-TOKEN` cookie), `X-Requested-With: XMLHttpRequest`, `Accept: application/json` and the detail page as `Referer`. The answer is `{"x": "<token>"}`; without the CSRF headers it is 419 `CSRF token mismatch.`
  2. `GET /n/<token>` without following redirects. VOE answers 302 with the hoster's embed URL in `Location` (`voe.sx/e/<id>?default_audio_language=de` for the German row, `en` for the English one). Byse answers 200 with an interstitial ("Video öffnen": "Du verlässt Filmo, um ein Video beim externen Anbieter zu öffnen.") whose body links the hoster URL (`<byse player domain>/d/<id>`).
- **Hosters and resolvers.** VOE has its resolver. Byse's player domains belong to the Filemoon resolver: its domain list (`hoster_resolvers/filemoon.py`) has `bysezejataos`, the one seen here.
- **kinoking.** kinoking labels a link "VOE (FILMO EN)" (`plugins/kinoking.py`), so it carries some of filmo's files. The two sites share no backend (kinoking serves PHP pages, `movie.php?id=`), so there is no `mirror_group`.

## The plugin (row 63)

- `plugins/filmo.py` on `HttpxPluginBase`, `provides = "stream"`, movies only, the domains above, languages German and English. It sends `Accept-Language: de-DE,de;q=0.9`, so a changed site default does not flip the UI.
- The search page sets the session cookies; the detail pages and the link calls of the search share them.
- Results: one per movie, carrying the hoster URLs, never `/n/<token>` (the token is bound to the session). The links are labelled by their row's language (`Deutsch` German, `English` English) and their quality tags.
- Per chip two requests (`POST /n`, `GET /n/<token>`), under the base's bounded concurrency. A 419 refetches the detail page once; after that the chip is skipped with a warning.
- TV requests answer `[]` (`served_category()` and `filter_by_category()`); the search cards pass `relevant_hits()`.

## Open

- A runtime read of the official domain list would add a mirror without a code change. No plugin reads a site's domain list today, so this is an idea for the maintainer, not part of the row.
- DDoS-Guard can start a challenge under load. The bases' challenge detection counts it (row 50's `ChallengeFlag`); the row adds no browser fallback.

## After

Row 63's live proof, 2026-10-11 (Mr. Normal, `handoff-63`):

- The live smoke test (`tests/live/test_plugin_smoke.py`, `filmo` with "Movie F") passed against filmo.to.
- The plugin's search for Movie F (category 2000) from the dev container: 1 result in 0.7 s (search page, detail page, 4 chips with 2 requests each), year 2014, three links: VOE `voe.sx` German 720p, Byse `bysezejataos.com` German 720p (the same file in both rows), VOE `voe.sx` English 720p. No `/n/` URL in the results.
- `movie/<Movie F>` on the dev server (`PYTHONPATH=src`, port 7981, cold cache): 200 in 2.9 s with 5 streams, two of them filmo's (`VOE · filmo`, German Dub and English Dub, 720p). Byse's link failed in the Filemoon resolver at once, no timeout: `filemoon_unavailable`, the hoster answers `embedding from this domain is not allowed for this video`. The video refuses embedding from the resolver's domain; not fixed in this row (the Filemoon resolver's owner decides; production's breaker for it opened on 2026-10-11 after a 52.6 s timeout, a different failure).

Row 65's live proof, 2026-10-11 (Ultracode, `handoff-65`), on the Byse link of [Byse (Row 65)](#byse-row-65):

- The watch page `/d/<id>` lays out the player frame at x 252, y 269, 766x430 in the stealth browser's 1280x720 viewport, below the page's header, and it is the page's only frame (`div.video-page__player-frame`). `capture_media()`'s click on the page centre (640, 360) lands in that frame above its play button: no stream after 18.7 s. A click on the frame's centre (635, 484) starts `access/challenge` to `embed/playback`, and the `master.m3u8` request follows about 1.8 s after the click. The page-level request events cover the frame's requests. So the resolver names the frame (`.video-page__player iframe`), and `capture_media(url, player=...)` clicks its centre.
- From the dev container (`PYTHONPATH=src`, `xvfb-run -a`): the watch page's capture took 4.5 s (the embed page: no stream after 18.3 s). Through the registry the link resolved in 5.6 s (`filemoon_embedding_blocked`, `hoster_resolve_success`) and the stream played with the frame host's Referer: master 200, a 1280x720 variant of 1005 segments, the first segment MPEG-TS (206).
- Production, read: the container restarted, so the Filemoon lines between the deploy and the restart are gone with the old container. After the restart there was one `filemoon_unavailable` with the `embedding` answer, from kinoking, so the restriction is not filmo's alone. One kinoking Filemoon capture timed out and re-opened the Filemoon breaker for 3600 s, which then skipped 63 Filemoon links (aniworld 36, kinoking 13, moflix 8, filmo 6). Whether the watch page's capture stays within the resolve time on the production host shows after the next deploy.

## Byse (Row 65)

Mr. Fable's probe of 2026-10-11, 4 min, on one fresh Byse link from the chain (Movie F, the German row; host `bysezejataos.com`, the link in its `/d/` form). The Byse API, by request headers:

| Request | No headers | filmo's Referer | filmo's Origin and Referer | The hoster's `/d/` Referer |
|---|---|---|---|---|
| `/api/videos/<id>/embed/details` | 403 `embedding from this domain is not allowed` | 403 | 200 | 403 |
| `/api/videos/<id>/details` | 200 | 200 | 200 | 200 |
| `/api/videos/<id>` | 200 | 200 | 200 | 200 |

The video's owner allows its player on authorised domains only, filmo.to among them. In Patchright (headful under Xvfb):

- `/e/<id>` shows "Embedding blocked on this site", also with the API requests routed with filmo's Origin.
- `/d/<id>`, the hoster's own watch page, embeds the player frame of a second host. That frame runs `access/challenge`, `access/attest`, `embed/captcha`, `embed/captcha/verify`, `embed/playback` and `embed/timeslider`, then fetches `master.m3u8`, a variant and `.ts` segments from a CDN with the frame host's Referer, without any filmo header.

The Filemoon resolver turns every link into `/e/` and asks `embed/details` first, so it gives up on such a video (`filemoon_unavailable`) before its browser starts. Row 65 captures the `/d/` page in that case. JDownloader's plugin (`BysejikuarCom`) downloads through `/api/videos/<id>/downloads` (a countdown and reCAPTCHA) and has no stream path.

## Filemoon in production (Rows 65 and 78)

`scripts/probes/capture_timing.py` on the production host: the Filemoon resolver's capture in a Chromium of the probe's own beside the app's, production's navigation bound (10 s), two runs per link and three clicks per run at most. Two links of one player backend: a Byse link in its `/d/` form and the live resolver test's Filemoon link (`/e/`). Both pages frame the player of a second host, whose access flow is `access/challenge`, `access/attest`, `embed/captcha` and `embed/captcha/verify` (the first time in a browser profile), `embed/playback`, then jwplayer and its HLS provider, then `master.m3u8` from the CDN.

### First run (2026-10-11, 3 min): no stream

| Link | Run | Page start, load | Clicks at | Stream | Resolve |
|---|---|---|---|---|---|
| Byse `/d/` | 1 | 1.1, 0.8 s | 4.9, 10.7, 15.9 s | none | 21.2 s |
| Byse `/d/` | 2 | 0.6, 0.7 s | 4.5, 9.8, 15.1 s | none | 20.4 s |
| `/e/` | 1 | 1.1, 0.8 s | 5.1, 10.3, 15.4 s | none | 20.6 s |
| `/e/` | 2 | 0.8, 0.6 s | 4.6, 9.8, 15.0 s | none | 20.2 s |

The dev container captured both links in every run, in 1.3 to 6.1 s. Two causes were open: the VPN's egress or the arm64 browser build.

### Diagnosis (5 min, `--diagnose`): the ad tag

The same links and bounds, no stream again in 4 of 4 runs (resolve 19.9 to 25.2 s, three clicks each). Both machines run Chromium 153, build 8010 (the Chrome for Testing build 153.0.8010.12 that Patchright pins for x86_64 and arm64).

- On the production host the page and the player frame load their scripts and settings (every request 200, the player's `embed/settings` included). Then the frame loads an ad tag, `llvpn.com /tag.min.js`, and its follow-ups on two more ad domains. No request of the access flow follows, no jwplayer and no CDN request. No request failed, so the capture's own route cut nothing (it aborts media only).
- After the clicks neither the page nor the player frame has a video element. MediaSource answers yes for H.264 (`avc1.42E01E`) and AAC (`mp4a.40.2`), and canPlayType answers "maybe" for HLS, as in the dev container.
- In the dev container the network's resolver answers the three ad domains with `0.0.0.0`. The tag fails with `net::ERR_CONNECTION_REFUSED` in all four runs, and the access flow and the stream follow.

The browser build is not the cause: the build is the same, its codec answers are the same, and the player never gets as far as media. The difference is the ad tag, which the production host's VPN path serves and the dev network refuses. The limits at this point: 4 against 4 runs, so a correlation; the probe records outcomes, not bodies; popups were not counted yet.

### The decisive test (Row 78, 3 min)

`--block llvpn.com` aborts that one domain in the probe's browser, the dev container's state for it. One call without the block followed on the same minute's network.

| Call | Run | Page start, load | Clicks at | Stream | Resolve | Access flow | Popups |
|---|---|---|---|---|---|---|---|
| Byse `/d/`, blocked | 1 | 1.7, 2.9 s | 7.9, 13.7, 19.4 s | none | 25.0 s | challenge to playback with the captcha, jwplayer and the HLS provider; no playlist | 0 |
| Byse `/d/`, blocked | 2 | 0.8, 0.8 s | 4.6 s | 6.8 s | 6.8 s | challenge to playback, jwplayer, playlist | 0 |
| `/e/`, blocked | 1 | 1.1, 0.7 s | 5.0, 10.0, 15.1 s | 17.8 s | 17.8 s | challenge to playback with the captcha, jwplayer, playlist | 0 |
| `/e/`, blocked | 2 | 0.7, 0.8 s | 4.6 s | 6.9 s | 6.9 s | challenge to playback, jwplayer, playlist | 0 |
| `/e/`, open | 1 | 1.1, 0.9 s | 5.4, 10.7, 15.8 s | none | 21.0 s | none (the ad tag and its follow-ups) | 3 |
| `/e/`, open | 2 | 0.9, 0.7 s | 4.6, 9.8, 15.0 s | none | 20.2 s | none (the ad tag and its follow-ups) | 3 |

The ad tag takes the clicks. Without the block every click opens a popup (three clicks, three popups, in both runs), so the player never gets a click and never starts its access flow. With the tag blocked no popup opens, the player runs its access flow in 4 of 4 runs and gives a stream in 3. The VPN matters only as the path that serves the ad tag.

Limits:

- 6 runs on the production host within three minutes, two links, one player backend.
- The first run in a fresh profile takes the captcha path. One such run captured at 17.8 s; the other loaded the player but asked for no playlist by 25.0 s. The second runs skip the captcha (6.8 and 6.9 s), inside production's resolve bound of 10 s; the first runs are not.
- The app's stealth context outlives a probe call, so production should mostly meet the second case. That is not measured.
- `llvpn.com` is the ad network's domain today; it can change.

### The fix (Row 79)

Mr. Fable's design: a capture that opts in aborts, as the client's block, every script request whose site is neither the page's nor one of its frames'. Only the Filemoon resolver opts in, for its `/e/` pages and Byse's `/d/` watch pages; the other captures are measured on the production host first. Not a domain list (the ad network can switch domains) and not a DNS blocklist on the VPN (the maintainer's configuration, and such lists also block hoster domains). One host always passes (row 80): `challenges.cloudflare.com`, from which a page loads its Turnstile widget, so a Filemoon page that shows the widget can still pass it. The exact host, not the site `cloudflare.com`: `cdnjs.cloudflare.com` serves any library and stays foreign. Cloudflare's interstitial needs no exception, since it loads its scripts from the page's own host. No Filemoon page has shown the widget yet, so the unit tests carry the rule.

In the dev container both link kinds capture with it (Byse 4.5 and 1.6 s, `/e/` 5.3 and 1.5 s), and the tag reads `blocked` from the capture's own route. The production host's proof follows the deploy: production's Filemoon captures and the breaker over the following hours. The breaker is restored open; its first half-open trial runs in a fresh profile (the captcha path: 17.8 s, or no playlist by 25.0 s), past the 10 s resolve budget, so it may reopen for an hour before a warm trial (6.8 and 6.9 s in row 78).

The production host's proof (2026-10-11, 2 min, production at 3a257fe): `capture_timing --diagnose` without `--block`, so only the app's rule aborts a request; two calls (Byse's `/d/`, then a regular `/e/` link) of two runs each in the probe's own browser, while the app's log shows no request or capture over 3 min 38 s.

| Link | Run | Clicks at | Stream | Popups |
|---|---|---|---|---|
| Byse `/d/` | 1 | 5.4, 10.6, 15.9 s | 17.1 s | 0 |
| Byse `/d/` | 2 | 4.4 s | 5.8 s | 0 |
| `/e/` | 1 | 5.5, 10.6, 15.7 s | none by 20.8 s | 0 |
| `/e/` | 2 | 4.7 s | 6.7 s | 0 |

- In every run `llvpn.com` `/tag.min.js` reads `blocked`, the abort of the app's route, and no other script is aborted. The player's Chromecast sender (`gstatic.com`) is still pending when a capture ends, as in row 78's runs.
- Three of four runs give a stream, as with row 78's probe block (three of four), against none of two with three popups per run without either.
- Each call's first run takes the captcha path (`captcha`, `captcha/verify`) and three clicks: 17.1 s, or no stream by 20.8 s although every access request answered 200 (the player frame had no video element after the clicks). The second run skips the captcha and needs one click (5.8 and 6.7 s), inside the 10 s resolve budget.

So the rule does on the production host what the probe's block did. Production's breaker stays open until its own half-open trial; whether a trial lands on the captcha path is what the next captures show.

### Production's trials (2026-10-11, read, production at 3a257fe)

Every half-open trial since the restart timed out at the 10 s resolve bound and reopened its breaker for an hour: Filemoon's 1 h 01 min apart (Byse links from aniworld), Dropload's 1 h 02 min apart (after its captcha fallback; the clock stops while a capture waits for a page). The trials came from the post-deploy check's background resolutions; until then the open breaker refuses Filemoon's links (63 refusals since the restart). The app keeps one browser context for its lifetime, so a capture after a finished one runs warm (5.8 and 6.7 s in the production host's proof), but a capture cut at 10 s never finishes the cold path (17.1 s, or no stream by 20.8 s) that would warm it: with production's bound every trial lands on the cold path. Row 80 does not change it, since no Filemoon page has shown Cloudflare's widget (the captcha is the player's own). The question of the budget went to the maintainer.
