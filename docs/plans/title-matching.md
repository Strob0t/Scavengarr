# Title matching for short and German series titles

Finding 13 of `ideas-backlog.md` (N10): in the seventh production round (`stremio-latency.md`, 2026-10-07) *Dark* S01E01 answered with no stream after the title filter dropped all 4 results (`stremio_all_filtered`), and *Haus des Geldes* S01E01 answered with no stream either. The suspicion was the queries (a one-word title, a German title of a Spanish series) or the title matcher. This file holds the evidence; the fix is assigned from it.

Measured with `scripts/probes/title_match.py`: it starts the app's composition and runs every stream plugin's search directly, once per query its language group gets (`application/stremio/queries.py`), with the request's category, season and episode, under the plugin timeout (30 s, but no search deadline) and the runner's plugin concurrency. Every raw result gets the matcher's score (`score_title_match`, configured weights) and its verdict at the threshold 0.7. Mirror groups are not collapsed; the episode filter is not applied.

## Probe run (2026-10-07, dev container, `staging` 79ed854)

The home connection without the VPN, fresh cache. Both titles come from the IMDb fallback with Wikidata (no TMDB key): *Dark* (2017), and *Haus des Geldes* (2017) with the alias *Money Heist*. Every stream plugin is German (`de`), so each request sends one query: `Dark`, `Haus des Geldes`.

### `series/tt5753856:1:1` (Dark)

| plugin | queries (raw results) | raw | kept | failed |
|---|---|---:|---:|---|
| aniworld | 'Dark' (2) | 2 | 0 | – |
| filmpalast | 'Dark' (3) | 3 | 0 | – |
| fireani | 'Dark' (1) | 1 | 0 | – |
| kinoger | 'Dark' (1) | 1 | 1 | – |
| kinoking | 'Dark' (1) | 1 | 1 | – |
| movie4k | – | 0 | 0 | 'Dark': timeout |
| sto | 'Dark' (1) | 1 | 1 | – |

No results: cineby, einschalten, haschcon, hdfilme, kinox, megakino, megakino_to, moflix, movie2k, streamcloud, streamkiste.

- kept: kinoger 'Dark' 1.00, kinoking 'Dark S01E01' 1.00, sto 'Dark - S01E01 - Geheimnisse' 1.00
- dropped: aniworld 'Dark Gathering' 0.65 and 'Bastard!! Heavy Metal, Dark Fantasy' 0.65; filmpalast 'Dark Matter S01E01' 0.65, 'The Terminal List: Dark Wolf S01E01' 0.65, 'Dark Matter - Der Zeitenläufer S01E01' 0.35; fireani 'Dark Gathering' 0.65

### `series/tt6468322:1:1` (Haus des Geldes)

| plugin | queries (raw results) | raw | kept | failed |
|---|---|---:|---:|---|
| fireani | 'Haus des Geldes' (2) | 2 | 0 | – |
| kinoger | 'Haus des Geldes' (1) | 1 | 1 | – |
| kinoking | 'Haus des Geldes' (1) | 1 | 1 | – |
| megakino | 'Haus des Geldes' (1) | 1 | 1 | – |
| sto | 'Haus des Geldes' (1) | 1 | 1 | – |

No results: aniworld, cineby, einschalten, filmpalast, haschcon, hdfilme, kinox, megakino_to, moflix, movie2k, movie4k, streamcloud, streamkiste.

- kept: kinoger 'Haus des Geldes' 1.00, kinoking 'Haus des Geldes S01E01' 1.00, megakino 'Haus des Geldes - Staffel 1' 1.00, sto 'Haus des Geldes - S01E01 - Folge 1' 1.00
- dropped: fireani 'The Demon Queen has a Death Wish' 0.43, 'Nausicaä of the Valley of the Wind' 0.27

## Reading

**Neither the queries nor the matcher are the cause.** Both queries find the series on three to four plugins, every right hit scores 1.00, and every dropped result is another title (*Dark Gathering*, *Dark Matter*, anime that fireani's search lists for any query). The one-word title costs nothing here: the loose hits score 0.65 and 0.35, below the threshold, and the right ones are exact. The German title is what the German sites list; the Spanish original and the English alias are not needed for these plugins.

**What emptied the production answers** (production log of the round requests, `prodctl.py logs`):

- *Dark* (request at 09:04:03): the four results that reached the title filter were the loose hits of the fast plugins (aniworld, filmpalast, fireani: the drops above). The plugins with the right hit did not deliver in time: sto (27.4 s) and kinoking (29.5 s) were cancelled at the search deadline (`stremio_plugin_timeout … cut_by_deadline=True`), and kinoger read five search pages but delivered no result before the answer (no title-filter line; why is not visible at info level). This is the cut that step 21 removes: with plugins running to their own timeout, sto's and kinoking's hits reach the cache and the next request.
- *Haus des Geldes* (09:05:36): the search found 3 results and the title filter kept 1 (sto's); kinoking was again cut at the deadline. sto's result had 2 links, both on serienstream.to, and both failed in the resolver (`serienstream_invalid_url`, `hoster_resolve_failed`), so no stream was left. The answer was empty because of the link resolution, not the search. The serienstream resolver accepts only `/serie/<slug>` paths; sto resolves its link-outs (`/r?t=<token>`) to the hoster itself and keeps its own URL when that fails, so the likely cause is sto's link-out resolution failing from the Pi (the log masks the path, so this is an inference). It is systematic: 13 `serienstream_invalid_url` warnings in the three hours around the round.

**Next:** no change to the queries or the matcher. The follow-ups are the deadline cut (step 21, already on `staging`), kinoger's empty answer in production for *Dark* (a debug-level look at its detail pages from the Pi), and sto's link-outs that reach the serienstream resolver unresolved (`invalid_url`).

## Plugin years and genres (step 36, 2026-10-09)

What the eight stream plugins that stored no `year` keep since step 36, from the detail page each already fetches (no extra request): `metadata["year"]` is an int (the start year only), `metadata["genres"]` a comma-joined string as kinoger's. sto and aniworld belong to step 35.

| Plugin | Year | Genres | Source and notes |
|---|---|---|---|
| cineby | yes | yes (before) | TMDB `release_date` (film) or `first_air_date` (the series' start) |
| einschalten | yes | yes (before) | `releaseDate` of the movie entry; films only |
| haschcon | when its SEO title carries one | yes (before: the WordPress categories) | The post `date` is the upload date ("Dracula – Tot aber glücklich", 1995, posted 2026-10-03), so the year comes from `yoast_head_json.title` / `og_title` ("… (1995) – Vampir-Komödie"); without one, none |
| kinoking | yes | yes | Movie page: the schema.org block's `dateCreated`; series page: `const seriesYear` (the site's year for the series, sent in its own link requests). Genres from the keywords meta (`<genre> filme kostenlos` / `<genre> serien kostenlos`), the only place the series page names them. One fixture each: Oppenheimer 2023 Drama, The Last of Us 2023 Drama |
| kinox | yes | yes | The title's `span.Year` (read before) and the detail table's Genre row, else the DetailDat entry. Dead origin right now (`plugin-domains.md`): verified on the captured film page only; a series' Year is taken as its start year (kinox lists a series by its first year), unverified without a series fixture |
| filmpalast | films only | yes | "Veröffentlicht: 2023" in the Shortinfos entry and the "Kategorien, Genre" links. The episode page of The Last of Us S02E01 (captured 2026-10-09, now a fixture) says 2025 against the series' 2023 start, so series results store genres only |
| megakino_to, movie4k | yes (before) | yes (before) | `DataApiPluginBase` stores `year` (a string of digits from the browse entry, which the matcher accepts), `genres` and `imdb_id` already; no change. Both sites are dead right now, so no live check |

For "One Piece": kinoking's and kinox's series results now carry the year, kinoking's, kinox's and filmpalast's the genres, so the 1999 anime and the 2023 live action part by year where the page has one and by a genre such as Animation where it has not. Without a year on the page, the result stays as ambiguous as before; nothing is guessed.
