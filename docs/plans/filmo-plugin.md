# The filmo plugin (2026-10-11)

**Question.** The maintainer asked for a plugin for filmo.to and all its alternative domains (2026-10-11). The site analysis below was done by the advisor with playwright-mcp and httpx from the dev container, 11:02 to 11:20 UTC; backlog row 63 implements it.

## Domains

- The site links its official domain list: the footer's "Domains" leads to filmo.domains ("Offiziellen Filmo Domains: Alternative Adressen & DNS-Anleitung"). On 2026-10-11 the list names one address, filmo.to, and the page loads nothing after it (no list from a script).
- 30 candidates `filmo.<tld>` (cc, su, sx, lol, tv, ws, me, net, org, io, co, de, click, watch, stream, live, cam, la, vip, app, cx, ac, li, is, nu, rs, pw, ch, at, eu): none serves filmo's app. The test for the app: the build asset name of filmo.to (`/build/assets/app-<hash>.js`) and the link to the domain list. The ones that answer are other businesses or parked: filmo.me, filmo.io, filmo.cam, filmo.ch, filmo.org, filmo.at (to bitly) and filmo.eu (to sedo). filmo.live is another site ("Filmo - Instant 4K Streaming") with neither mark. The rest time out or do not resolve.
- So the plugin's domains are `["filmo.to"]`. A mirror the list names later joins them. JDownloader's sources name no filmo, so there are no dead-domain notes to check.

## The site

- **The stack.** A Laravel app (a Vite build, the `XSRF-TOKEN` and `filmo-session` cookies) behind DDoS-Guard (`server: ddos-guard`, `__ddg*` cookies). httpx with the browser User-Agent got every page with 200; no challenge was seen.
- **The language.** A client without a language choice gets the German UI (`<html lang="de">`); the browser with an English locale got the English one. Titles follow the UI (the German slug `der-astronaut-project-hail-mary` shows as "Project Hail Mary" in English). The UI languages (`/lang/<code>`) change labels, not the catalogue.
- **Movies only.** The navigation has Movies, Popular, genres, collections and letters; `/series`, `/tv`, `/serien` and `/shows` answer 404.
- **Search.** `GET /search?q=<term>`. Up to 8 cards in `section.search-top-results`, each `article.popular-spotlight-card a.popular-spotlight-card__link` with the `href` `/movies/<slug>` and the text genre + title. Broad terms add a grid of further results in `main div.container-wrapper div.row div.col-6` (42 movie links for "star", none for "Matrix" or "Inception"). There is no pagination control. The search lists loose matches: Interstellar also finds Supergirl, Redline and The Beyond.
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

- The live smoke test (`tests/live/test_plugin_smoke.py`, `filmo` with "Interstellar") passed against filmo.to.
- The plugin's search for Interstellar (category 2000) from the dev container: 1 result in 0.7 s (search page, detail page, 4 chips with 2 requests each), year 2014, three links: VOE `voe.sx` German 720p, Byse `bysezejataos.com` German 720p (the same file in both rows), VOE `voe.sx` English 720p. No `/n/` URL in the results.
- `movie/tt0816692` on the dev server (`PYTHONPATH=src`, port 7981, cold cache): 200 in 2.9 s with 5 streams, two of them filmo's (`VOE · filmo`, German Dub and English Dub, 720p). Byse's link failed in the Filemoon resolver at once, no timeout: `filemoon_unavailable`, the hoster answers `embedding from this domain is not allowed for this video`. The video refuses embedding from the resolver's domain; not fixed in this row (the Filemoon resolver's owner decides; production's breaker for it opened on 2026-10-11 after a 52.6 s timeout, a different failure).
