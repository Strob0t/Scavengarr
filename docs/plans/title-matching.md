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

## Probe run (2026-10-09, series identity: before `01492ad`, after `11d1b46`)

Task 7.2 of the series-identity change: the title probe over the series set above plus the three One Piece ids, run from the dev container (home connection, fresh cache) twice within three minutes, on `staging` before the matcher's identity rules (`01492ad`: Cinemeta meta, no year gate, no IMDb or category rule) and after them (`11d1b46`). The reference line names what the matcher knew after: the year, the IMDb id and the kind (`animation` from the Cinemeta genres). Each table counts a plugin's raw results and the kept ones in both runs (a failure in brackets); the lines below list every result of the after run with its score and the rule that dropped it, and the results only the before run saw. kinoking's search timed out in one run per id (its pages take 12 to 17 s); megakino_to and movie4k had no reachable domain in either run. Every drop was checked against the plugin's stored metadata (category, year, genres, IMDb id; a dump script that calls the plugins' `search()` directly, not kept).

### `series/tt5753856:1:1` (Dark)

Reference after: Dark (2017; tt5753856; not animation).

| plugin | before: raw / kept | after: raw / kept |
|---|---|---|
| aniworld | 2 / 0 | 2 / 0 |
| filmpalast | 3 / 0 | 3 / 0 |
| kinoger | 1 / 1 | 1 / 1 |
| kinoking | 1 / 1 | 0 / 0 (Dark: timeout) |
| megakino_to | 0 / 0 (Dark: PluginUnreachableError) | 0 / 0 (Dark: PluginUnreachableError) |
| movie4k | 0 / 0 (Dark: PluginUnreachableError) | 0 / 0 (Dark: PluginUnreachableError) |
| sto | 1 / 1 | 1 / 1 |

No results in either run: cineby, einschalten, fireani, haschcon, hdfilme, kinox, megakino, moflix, movie2k, streamcloud, streamkiste.

- after, aniworld: 'Dark Gathering' 0.00 dropped by category
- after, aniworld: 'Bastard!! Heavy Metal, Dark Fantasy' 0.00 dropped by category
- after, filmpalast: 'Dark Matter S01E01' [Dark.Matter.S01E01.Episode.Eins.GERMAN.AAC.1080p.BluRay.x265-w00t] 0.65 dropped by score
- after, filmpalast: 'The Terminal List: Dark Wolf S01E01' [The.Terminal.List.Dark.Wolf.S01E01.GERMAN.DL.1080p.WEB.h264-SAUERKRAUT] 0.65 dropped by score
- after, filmpalast: 'Dark Matter - Der Zeitenläufer S01E01' [Dark.Matter.2024.S01E01.German.DL.Atmos.1080p.ATVP.WEB.H265-ZeroTwo] 0.00 dropped by year
- after, kinoger: 'Dark' 1.20 kept
- after, sto: 'Dark - S01E01 - Geheimnisse' 1.00 kept
- before only, kinoking: 'Dark S01E01' 1.00 kept

### `series/tt6468322:1:1` (Haus des Geldes)

Reference after: Haus des Geldes (2017; alt: Money Heist; tt6468322; not animation) / Money Heist (2017; tt6468322; not animation).

| plugin | before: raw / kept | after: raw / kept |
|---|---|---|
| kinoger | 1 / 1 | 1 / 1 |
| kinoking | 1 / 1 | 1 / 1 |
| megakino | 1 / 1 | 1 / 1 |
| megakino_to | 0 / 0 (Haus des Geldes: PluginUnreachableError) | 0 / 0 (Haus des Geldes: PluginUnreachableError) |
| movie4k | 0 / 0 (Haus des Geldes: PluginUnreachableError) | 0 / 0 (Haus des Geldes: PluginUnreachableError) |
| sto | 1 / 1 | 1 / 1 |

No results in either run: aniworld, cineby, einschalten, filmpalast, fireani, haschcon, hdfilme, kinox, moflix, movie2k, streamcloud, streamkiste.

- after, kinoger: 'Haus des Geldes' 1.20 kept
- after, kinoking: 'Haus des Geldes S01E01' 1.20 kept
- after, megakino: 'Haus des Geldes - Staffel 1' 1.00 kept
- after, sto: 'Haus des Geldes - S01E01 - Folge 1' 1.00 kept

### `series/tt0388629:1:1` (One Piece, the 1999 anime)

Reference after: One Piece (1999; tt0388629; animation).

| plugin | before: raw / kept | after: raw / kept |
|---|---|---|
| aniworld | 1 / 1 | 2 / 1 |
| filmpalast | 1 / 1 | 1 / 0 |
| kinoger | 1 / 1 | 1 / 0 |
| kinoking | 0 / 0 (One Piece: timeout) | 2 / 0 |
| megakino_to | 0 / 0 (One Piece: PluginUnreachableError) | 0 / 0 (One Piece: PluginUnreachableError) |
| movie2k | 1 / 1 | 1 / 0 |
| movie4k | 0 / 0 (One Piece: PluginUnreachableError) | 0 / 0 (One Piece: PluginUnreachableError) |
| sto | 1 / 1 | 2 / 1 |
| streamcloud | 1 / 1 | 1 / 0 |
| streamkiste | 1 / 1 | 1 / 0 |

No results in either run: cineby, einschalten, fireani, haschcon, hdfilme, kinox, megakino, moflix.

- after, aniworld: 'One Piece' 1.00 kept
- after, aniworld: 'One Piece (2025)' 0.00 dropped by year
- after, filmpalast: 'One Piece S01E01' [One.Piece.2023.S01E01.GERMAN.DL.720p.WEB.h264-SAUERKRAUT] 0.00 dropped by category
- after, kinoger: 'One Piece' 0.00 dropped by category
- after, kinoking: 'One Piece S01E01' 0.00 dropped by category
- after, kinoking: 'ONE PIECE S01E01' 0.00 dropped by category
- after, movie2k: 'ONE PIECE' 0.00 dropped by year
- after, sto: 'One Piece - S01E01 - Hier kommt Ruffy, der künftige König der Piraten!' 1.00 kept
- after, sto: 'One Piece (2023) - S01E01 - Das Abenteuer beginnt' 0.00 dropped by category
- after, streamcloud: 'ONE PIECE' 0.00 dropped by imdb
- after, streamkiste: 'ONE PIECE' 0.00 dropped by imdb

### `series/tt11737520:1:1` (One Piece, the 2023 live action)

Reference after: One Piece (2023; tt11737520; not animation).

| plugin | before: raw / kept | after: raw / kept |
|---|---|---|
| aniworld | 1 / 1 | 2 / 0 |
| filmpalast | 1 / 1 | 1 / 1 |
| kinoger | 1 / 1 | 1 / 1 |
| kinoking | 0 / 0 (One Piece: timeout) | 2 / 1 |
| megakino_to | 0 / 0 (One Piece: PluginUnreachableError) | 0 / 0 (One Piece: PluginUnreachableError) |
| movie2k | 1 / 1 | 1 / 1 |
| movie4k | 0 / 0 (One Piece: PluginUnreachableError) | 0 / 0 (One Piece: PluginUnreachableError) |
| sto | 1 / 1 | 2 / 1 |
| streamcloud | 1 / 1 | 1 / 1 |
| streamkiste | 1 / 1 | 1 / 1 |

No results in either run: cineby, einschalten, fireani, haschcon, hdfilme, kinox, megakino, moflix.

- after, aniworld: 'One Piece' 0.00 dropped by category
- after, aniworld: 'One Piece (2025)' 0.00 dropped by category
- after, filmpalast: 'One Piece S01E01' [One.Piece.2023.S01E01.GERMAN.DL.720p.WEB.h264-SAUERKRAUT] 1.20 kept
- after, kinoger: 'One Piece' 1.20 kept
- after, kinoking: 'ONE PIECE S01E01' 1.20 kept
- after, kinoking: 'One Piece S01E01' 0.00 dropped by year
- after, movie2k: 'ONE PIECE' 1.20 kept
- after, sto: 'One Piece (2023) - S01E01 - Das Abenteuer beginnt' 1.20 kept
- after, sto: 'One Piece - S01E01 - Hier kommt Ruffy, der künftige König der Piraten!' 0.00 dropped by category
- after, streamcloud: 'ONE PIECE' 1.20 kept
- after, streamkiste: 'ONE PIECE' 1.20 kept

### `series/tt30476502:1:1` (The One Piece, the 2027 remake)

Reference after: The One Piece (2027; tt30476502; animation).

| plugin | before: raw / kept | after: raw / kept |
|---|---|---|
| megakino_to | 0 / 0 (The One Piece: PluginUnreachableError) | 0 / 0 (The One Piece: PluginUnreachableError) |
| movie4k | 0 / 0 (The One Piece: PluginUnreachableError) | 0 / 0 (The One Piece: PluginUnreachableError) |

No results in either run: aniworld, cineby, einschalten, filmpalast, fireani, haschcon, hdfilme, kinoger, kinoking, kinox, megakino, moflix, movie2k, sto, streamcloud, streamkiste.



### Reading (7.2)

**The two acceptance lines hold.** The 1999 request keeps no 2023 result: filmpalast's `One.Piece.2023` release (5000 with genres), kinoger's page (year "2023", genres "Action, Abenteuer, Fantasy, Serie") and kinoking's "ONE PIECE S01E01" go by category, movie2k's "ONE PIECE" (year "2023", no genres) by year, streamcloud's and streamkiste's pages (`imdb_id` tt11737520) by the id, sto's "One Piece (2023)" entry by category (3.6 scrapes it next to the anime now). The 2023 request keeps no 5070 result: aniworld's two entries and sto's anime page (genre "Anime") go by category. Before, the 1999 request kept all seven (filmpalast's 2023 release at 0.70, the rest at 1.00) and the 2023 request kept aniworld's and sto's anime pages.

**Dark and Haus des Geldes are unchanged**, kinoger's and kinoking's pages now score 1.20 (the year bonus for 2017), filmpalast's "Dark Matter - Der Zeitenläufer" (2024) goes by year instead of 0.35. Production's empty answer for Dark after the deploy of `11d1b46` (19:06 UTC) is reach, not the gate: sto refuses the Pi and kinoger timed out there, so only the loose hits reached the filter (the same picture as the 2026-10-07 round above). The One Piece (2027) has no results on any plugin in either run.

**One wrong drop: kinoking's anime page.** For the 1999 request kinoking's "One Piece S01E01" went by category (for the 2023 request the same result goes by year, so its stored year is the anime's). kinoking labels every series 5000 and, since step 36, stores the page's genres from its keywords meta; the category rule of `11d1b46` read "5000 with genres" as "a site that lists genres and did not call the title anime", which was written when kinoking carried no genres. That the result is the anime and not the 2027 remake: kinoking's cards carry TMDB ids and mirror TMDB's names, which tell the three apart by case alone ("One Piece" the anime, "ONE PIECE" the live action, "THE ONE PIECE" the remake), and the result's year lies outside the live action's tolerance. That its genres name "Animation": kinoking's genres are TMDB's German genre names (Dark: "Krimi, Drama, Sci-Fi &amp; Fantasy"), and TMDB's for the anime are Action & Adventure, Animation, Komödie; a direct read of the page was not possible after the probe (the plugin's search timed out three times from 19:16 UTC on, and a plain fetch got the challenge page twice and then a 524). Fix (this commit): the result's kind comes from its label and genres, symmetric. 5070 means animation; 5000 with genres means what the genres say, with the genre words the plugins label 5070 by (`names_anime` of `infrastructure/plugins/categories.py`, shared with `stream_category`: "anime", "animation"); no genres means unknown. A known kind that differs from the reference's kind drops the result with the reason `category`; an unknown one leaves it to the year and text rules. kinoking's anime page is then kept for the 1999 request (1.20 with the year bonus) and dropped by category instead of year for the 2023 one; every other verdict above stays.

**No year exclusion.** No plugin in the set stores a latest-season year: kinoking's `seriesYear` is 2017 for Dark (int), kinoger's "2017" (string), and the years of the One Piece results are the live action's 2023 where the page is the live action.

**Open:** kinoking's genres keep the page's HTML entities ("Sci-Fi &amp; Fantasy" for Dark), which the matcher does not mind but a reader of the metadata will (step 36's file).
