# Series episodes per plugin

What the Stremio episode filter (`infrastructure/stremio/episode_filter.py`) does with each plugin's series results, measured before and after its change in step 21 (OpenSpec `continue-cut-searches`, decisions 6 and 7). Acceptance: no leaks after the change, and no plugin keeps fewer results of the right episode than before.

Measured with `scripts/probes/series_episodes.py`: it starts the app's composition (plugins, browser fallback, title lookup) and runs every stream plugin's search directly, with the queries, category, season and episode of a Stremio request, under the plugin timeout (30 s) and the plugin concurrency of the runner; mirror groups are not collapsed. Requests: the series of `.cache/live/round-titles.txt` (Series D S01E01 and S01E02, Series C, Series F S04E01, Series E, Series G, Anime A, Anime G, Anime H, Anime I) plus Series H S01E05 and S02E05, 12 requests.

Columns: title-matching results (the title filter's survivors); their numbering from the plugin's `metadata` ints, else the release name (guessit): season and episode, episode only, season only, none; the filter's outcome: kept unchanged, narrowed (decided by the links' episode labels; a result whose labelled links all match counts here too), dropped; right: kept results whose title or a link label names the requested episode; leaks: kept results whose title or a link label (the filter's `1x5`/`S01E05` patterns, plus `Episode 5`, `Folge 5`, `E05`) names another episode; failed: searches that timed out or raised.

## Before (2026-10-07, dev container, `staging` 79a73c7)

A residential connection without the VPN, fresh cache. Wall time per request 15–31 s.

| plugin | results | season_episode | episode | season | none | kept | narrowed | dropped | right | leaks | failed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| aniworld | 4 | 0 | 0 | 0 | 4 | 4 | 0 | 0 | 0 | 0 | 0 |
| cineby | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| einschalten | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| filmpalast | 7 | 7 | 0 | 0 | 0 | 7 | 0 | 0 | 7 | 0 | 0 |
| fireani | 4 | 0 | 0 | 0 | 4 | 4 | 0 | 0 | 0 | 0 | 0 |
| haschcon | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| hdfilme | 7 | 0 | 0 | 0 | 7 | 0 | 7 | 0 | 7 | 0 | 0 |
| kinoger | 10 | 0 | 0 | 2 | 8 | 10 | 0 | 0 | 0 | 0 | 0 |
| kinoking | 10 | 10 | 0 | 0 | 0 | 10 | 0 | 0 | 10 | 0 | timeout 3 |
| kinox | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| megakino | 7 | 0 | 0 | 4 | 3 | 0 | 4 | 3 | 4 | 0 | 0 |
| megakino_to | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| moflix | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| movie2k | 2 | 0 | 0 | 0 | 2 | 0 | 2 | 0 | 2 | 0 | 0 |
| movie4k | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | timeout 1 |
| sto | 11 | 11 | 0 | 0 | 0 | 11 | 0 | 0 | 11 | 0 | 0 |
| streamcloud | 7 | 0 | 0 | 0 | 7 | 0 | 7 | 0 | 7 | 0 | 0 |
| streamkiste | 7 | 0 | 0 | 0 | 7 | 0 | 7 | 0 | 7 | 0 | 0 |
| **total** | 76 | 28 | 0 | 6 | 42 | 46 | 27 | 3 | 55 | 0 | 4 |

The table lists every stream plugin; the rows of zeros found no title-matching series result for these requests (cineby, einschalten, haschcon, kinox, megakino_to, moflix, movie4k).

- **Leaks: none.** Every kept result either names the requested episode or names none at all.
- **Series results without episode numbers:** 42 of 76 results name no episode in the title. 26 of them carry episode labels on their links and are decided by them (below). 16 carry no episode information at all and are kept unchanged, and so are kinoger's 2 season packs ("Series D - Staffel 01-05"): aniworld 4, fireani 4, kinoger 10, all with empty link labels. aniworld and fireani open the requested episode's page themselves (aniworld `/staffel-<season>/episode-<episode>`, fireani's `GetEpisode` with season and episode), so their hits are the right episode, but nothing in the result says so; the stricter filter of decision 6 would drop all of them unless the plugins set `metadata["season"]` and `metadata["episode"]` (ints). kinoger gives one unlabelled link per title: the series' episode list (`1x1`-labelled links) was not found on those pages, so whether the link is the requested episode is open.
- **sto** stores its season in `metadata` as a string (`"season": str(season)`); the filter reads ints, so the release name (`S01E01`) decides there. Harmless today; with decision 6 the metadata should carry ints.
- **Decided by labels** (30: 27 narrowed, 3 dropped): hdfilme 7, streamcloud 7, streamkiste 7, movie2k 2, megakino 7 (4 season pages, 3 without a season in the title). The 3 drops are megakino pages of another season (Anime A "2 Staffel", Series F "5 Stafffel" for S04, Series H S02E05 on a season 1 page): correct.
- **Release names with the episode** (28): filmpalast 7, kinoking 10, sto 11, all kept and right.
- **Failed:** kinoking 3 timeouts, movie4k 1.

## Before, after step 22 (2026-10-07, dev container, `staging` 8f62d6f)

The same 12 requests with the old filter, after the parser follow-ups of step 22: aniworld and fireani name the episode they fetched in `metadata`, sto's season and episode are ints, kinoger reads its series' episodes from the player script's season arrays and labels each link. The "After" table of step 21's filter change (task 7.4) compares against this one.

| plugin | results | season_episode | episode | season | none | kept | narrowed | dropped | right | leaks | failed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| aniworld | 4 | 4 | 0 | 0 | 0 | 4 | 0 | 0 | 4 | 0 | 0 |
| cineby | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| einschalten | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| filmpalast | 7 | 7 | 0 | 0 | 0 | 7 | 0 | 0 | 7 | 0 | 0 |
| fireani | 4 | 4 | 0 | 0 | 0 | 4 | 0 | 0 | 4 | 0 | 0 |
| haschcon | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| hdfilme | 7 | 0 | 0 | 0 | 7 | 0 | 7 | 0 | 7 | 0 | 0 |
| kinoger | 10 | 0 | 0 | 2 | 8 | 0 | 10 | 0 | 10 | 0 | 0 |
| kinoking | 13 | 13 | 0 | 0 | 0 | 13 | 0 | 0 | 13 | 0 | 0 |
| kinox | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| megakino | 7 | 0 | 0 | 4 | 3 | 0 | 4 | 3 | 4 | 0 | 0 |
| megakino_to | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| moflix | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| movie2k | 2 | 0 | 0 | 0 | 2 | 0 | 2 | 0 | 2 | 0 | 0 |
| movie4k | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | timeout 1 |
| sto | 11 | 11 | 0 | 0 | 0 | 11 | 0 | 0 | 11 | 0 | 0 |
| streamcloud | 7 | 0 | 0 | 0 | 7 | 0 | 7 | 0 | 7 | 0 | 0 |
| streamkiste | 7 | 0 | 0 | 0 | 7 | 0 | 7 | 0 | 7 | 0 | 0 |
| **total** | 79 | 39 | 0 | 6 | 34 | 39 | 37 | 3 | 76 | 0 | 1 |

- **Right episode:** 76 of 79 results (before: 55 of 76). **Leaks:** none. **Dropped:** 3, the same megakino pages of another season.
- **aniworld and fireani** (4 each): numbered from their metadata now, all right.
- **kinoger** (10): all narrowed by their `<season>x<episode>` labels to the requested episode. Before, every series page gave the script's first URL, S01E01, without a label (Series F S04E01 got S01E01 from all four player tabs).
- **Without episode information:** none left; the 34 results without an episode in the title are all decided by their link labels.
- **Failed:** movie4k 1 timeout; kinoking had none this time (13 results instead of 10).

## After (2026-10-07, dev container, `worktree-handoff-21` at the filter change and the domain-check fix, on `staging` as 24c8c84 and de6032c)

The same 12 requests with the filter of step 21's group 7 (24c8c84: the numbers in a result's `metadata` first, a result without episode information is not kept, an episode without a season is season 1) and the domain-check fix (de6032c), run before ed41ee0 (sto) joined `staging`. A plugin none of whose domains answers does not search now (`PluginUnreachableError`, counted under "failed"). The first run after the filter change, before de6032c, found kinoger unreachable in all 14 searches; it is the reason for that fix (below).

| plugin | results | season_episode | episode | season | none | kept | narrowed | dropped | right | leaks | failed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| aniworld | 4 | 4 | 0 | 0 | 0 | 4 | 0 | 0 | 4 | 0 | 0 |
| cineby | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| einschalten | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| filmpalast | 6 | 6 | 0 | 0 | 0 | 6 | 0 | 0 | 6 | 0 | 0 |
| fireani | 4 | 4 | 0 | 0 | 0 | 4 | 0 | 0 | 4 | 0 | 0 |
| haschcon | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| hdfilme | 7 | 0 | 0 | 0 | 7 | 0 | 7 | 0 | 7 | 0 | 0 |
| kinoger | 9 | 0 | 0 | 2 | 7 | 0 | 9 | 0 | 9 | 0 | 0 |
| kinoking | 10 | 10 | 0 | 0 | 0 | 10 | 0 | 0 | 10 | 0 | timeout 3 |
| kinox | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| megakino | 6 | 0 | 0 | 3 | 3 | 0 | 3 | 3 | 3 | 0 | timeout 1 |
| megakino_to | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | PluginUnreachableError 13, timeout 1 |
| moflix | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| movie2k | 2 | 0 | 0 | 0 | 2 | 0 | 2 | 0 | 2 | 0 | 0 |
| movie4k | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | PluginUnreachableError 14 |
| sto | 11 | 11 | 0 | 0 | 0 | 11 | 0 | 0 | 11 | 0 | 0 |
| streamcloud | 7 | 0 | 0 | 0 | 7 | 0 | 7 | 0 | 7 | 0 | 0 |
| streamkiste | 7 | 0 | 0 | 0 | 7 | 0 | 7 | 0 | 7 | 0 | 0 |
| **total** | 73 | 35 | 0 | 5 | 33 | 35 | 35 | 3 | 70 | 0 | 32 |

- **Right episode:** 70 of 73 results (before: 76 of 79); every result the filter let through is the right episode. **Leaks:** none. **Dropped:** 3, the same megakino pages of another season (the parser follow-up below).
- **Fewer results than before, none of them lost to the filter:** kinoking 10 (3 searches timed out), megakino 6 with 3 right (1 timeout: the "Series E - Staffel 1" page), filmpalast 6 (its search gave no "Series G" hit this time), kinoger 9 (no "Series C" hit); no record of these results exists, so they never reached the filter. A rerun of the four plugins ten minutes later (`--plugins filmpalast,kinoger,kinoking,megakino`) gave the counts of the table before: filmpalast 7, kinoger 10, kinoking 13, megakino 7 (4 right, 3 dropped), no failures: 34 of 37 right. No plugin keeps fewer right episodes than before.
- **kinoger** (10 in the rerun): all narrowed by their labels, as before. The first run after the filter change found kinoger unreachable in all 14 searches: kinoger.com and kinoger.to answer HEAD and GET with 403 and `cf-mitigated: challenge`. Until step 21's group 6 the domain check fell back to the primary domain and the search went on through the browser, which solves the challenge; group 6 raised instead. de6032c counts an answer below 500 or a challenge page as reachable, the health monitor's rule, and kinoger searches again.
- **Unreachable sites:** megakino_to and movie4k answer neither HEAD nor GET on any domain (read timeouts). Their searches end in `PluginUnreachableError` (13 and 14 of 14; one megakino_to domain check timed out as a whole) instead of an empty result or a timeout, and the health monitor keeps them out of the next searches until a recheck finds the site.
- **Without episode information:** none; the 33 results without an episode in the title are decided by their link labels.
- **Failed:** kinoking 3 timeouts, megakino 1, megakino_to 13 unreachable and 1 timeout, movie4k 14 unreachable; in the rerun of the four plugins none.

### Parser follow-ups

- **megakino** (dropped 3, all correct): "Series F - 5 Stafffel" for S04E01 and "Anime A - 2 Staffel" for S01E01 are pages of another season, "Series H" for S02E05 carries only the label `1x5 Voe`. The labels show a parser limit: `_add_episode` labels each episode from the page's `<select id="ep<n>">` as `1x<n>`, the season fixed at 1, so a season page of season 2 or later carries season-1 labels (`1x1 Voe` on both pages above). Asked for those pages' own season (`series/<Series F>:5:1`, `series/<Anime A>:2:1`, `--plugins megakino`), both pages came back and both were dropped by their labels: 0 of 2 right, a loss, not a leak. Follow-up (backlog row 28, step 22's area, `plugins/megakino.py`): label with the page's season from its title ("N Staffel", "Staffel N"; 1 without one) and the filter narrows them like hdfilme's.

**After step 28** (2026-10-07, dev container, `handoff-28` on `staging` 2ba88c5): megakino labels a page's episodes with the season its title names (`2x1 Voe` on "Anime A - 2 Staffel", `5x1 Voe` on "Series F - 5 Stafffel"; 1 without one) and states season and episode in the metadata of an episode request. `series/<Series F>:5:1` and `series/<Anime A>:2:1` with `--plugins megakino`: 2 results, both season_episode from the metadata, both kept and right (before: 0 of 2). Asked for another season (`series/<Series F>:4:1`, `series/<Anime A>:1:1`), megakino gives no result: `_other_season` now also reads the number before the word ("5 Stafffel"), so the search skips those pages before their detail fetch instead of the filter dropping them.

## s.to season rows (task 6.1 of step 35, 2026-10-09)

Captured from the dev container (s.to refuses the production host) with `scripts/capture_pages.py sto "Anime A" --season 2 --episode 1` and the same for *Series G*: `serienstream.to` answered every page (search, series page, season page, episode page) with 200 and the real markup (`<title>Anime A Staffel 2 | SerienStream (S.to)</title>`), no challenge page. The two season pages are fixtures: `tests/fixtures/html/sto/season-one-piece-2.html.gz` and `season-the-last-of-us-2.html.gz`.

**Rows.** An episode is `tr.episode-row` (the header row is `tr.text-uppercase` and has no number); `th.episode-number-cell` holds the season-relative number, `td.episode-title-cell` holds `strong.episode-title-ger` (the German title) and `span.episode-title-eng` (the English title). There is no `td.seasonEpisodeTitle` on s.to; that is aniworld's markup, so the empty answer for that selector on 2026-10-09 was the markup, not a challenge. Anime A, season 2, episode 1 (whitespace collapsed):

```html
<tr class="episode-row " onclick="window.location='/serie/anime-a/staffel-2/episode-1'">
  <th scope="row" class="text-center fw-semibold episode-number-cell">1</th>
  <td class="fw-medium episode-title-cell">
    <strong class="d-block episode-title-ger" title="<episode title>"><episode title></strong>
    <span class="text-white-50 episode-title-eng" title="Episode 062"> Episode 062 </span>
  </td>
  <td class="episode-watch-cell"> ... <img src="/storage/providers/voe.svg" alt="VOE" ...> ... </td>
  <td class="text-end episode-language-cell"> ... svg-flag-german ... svg-flag-english-german ... </td>
</tr>
```

**Answer for 6.2.** Yes on both counts. For an anime the English span carries the absolute number the way aniworld does (`Episode 062`, without aniworld's brackets): the 16 rows of Anime A season 2 run `Episode 062` to `Episode 077`, and the series page lists 24 `staffel-N` links. For a series the span carries the English title: *Series G* S02E02 has `Durch das Tal` in the `strong` and `Through the Valley` in the span. So `sto.py` can share aniworld's index and locate code with its own selectors (`span.episode-title-eng` for the number or English title, `strong.episode-title-ger` for the German one). Today's `_SeriesDetailParser` reads the `strong` only and leaves `en_title` empty.

**Applied (6.2).** `sto.py` declares `locates_episodes` and locates its anime results (5070) on the shared module with the selectors above (`sto:episodes:v1:<slug>`, 7 days). An anime row has no English title, so the number must match exactly (rule 1's number-only case); a series (5000, 5080) keeps the request's numbers, since IMDb's seasons are the site's for them. The two captured season pages are the fixtures of `test_real_pages.py`.

## After, series identity (2026-10-09, dev container, `worktree-handoff-35` after 026fca2 with the `isolated_search` fix)

Task 7.1 of step 35. `xvfb-run -a poetry run python -P scripts/probes/series_episodes.py --plugins aniworld,sto series/<Anime A>:1:1 series/<Anime A>:5:2 series/<Anime A>:22:4 series/<Anime H>:2:1 series/<Anime H>:4:1 series/kitsu:<Anime A>:1089` (Anime A and Anime H; the `kitsu:` id is translated first, as the use case does). The probe builds each request's episode reference from the Cinemeta meta and hands it to the two plugins, which declare `locates_episodes`; `located` counts the results whose metadata names the site's page.

The first run failed every search with `TypeError 8` per plugin: `HttpxPluginBase.isolated_search`, the search runner's entry, did not take the `episode_ref` keyword the runner passes (the Playwright base did), so no httpx plugin had ever received a reference outside the unit tests, which call `search()` directly. Fixed in this step with tests on the base and through both plugins' `isolated_search`.

| plugin | results | season_episode | episode | season | none | kept | narrowed | dropped | right | leaks | located | failed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| aniworld | 6 | 6 | 0 | 0 | 0 | 6 | 0 | 0 | 6 | 0 | 6 | 0 |
| sto | 5 | 5 | 0 | 0 | 0 | 5 | 0 | 0 | 5 | 0 | 5 | 0 |
| **total** | 11 | 11 | 0 | 0 | 0 | 11 | 0 | 0 | 11 | 0 | 11 | 0 |

References:

- series/<Anime A>:1:1: S1E1 absolute 1 title "<episode title>" aired 1999-10-20
- series/<Anime A>:5:2: S5E2 absolute 62 title '<episode title>? <episode title>!' aired 2001-03-21
- series/<Anime A>:22:4: S22E4 absolute 1088 title "<episode title>" aired 2024-01-07
- series/<Anime H>:2:1: S2E1 absolute 27 title 'Flame Hashira Kyojuro Rengoku' aired 2021-10-10
- series/<Anime H>:4:1: S4E1 absolute 45 title "Someone's Dream" aired 2023-04-09
- series/kitsu:<Anime A>:1089: S22E4 absolute 1089 title "<episode title>" aired 2024-01-07

Placements:

- aniworld, series/<Anime A>:1:1: 'Anime A' -> staffel-1/episode-1 by number
- sto, series/<Anime A>:1:1: 'Anime A - S01E01 - <episode title>' -> staffel-1/episode-1 by number
- aniworld, series/<Anime A>:5:2: 'Anime A' -> staffel-2/episode-1 by number
- sto, series/<Anime A>:5:2: 'Anime A - S05E02 - <episode title>' -> staffel-2/episode-1 by number
- aniworld, series/<Anime A>:22:4: 'Anime A' -> staffel-22/episode-1 by number
- sto, series/<Anime A>:22:4: 'Anime A - S22E04 - <episode title>' -> staffel-21/episode-197 by number
- sto, series/<Anime H>:2:1: 'Anime H - S02E01 - Flammensäule Rengoku Kyojurou' -> staffel-2/episode-1 by title
- aniworld, series/<Anime H>:2:1: 'Anime H' -> staffel-2/episode-1 by title
- aniworld, series/<Anime H>:4:1: 'Anime H' -> staffel-3/episode-1 by title
- aniworld, series/kitsu:<Anime A>:1089: 'Anime A' -> staffel-22/episode-1 by number
- sto, series/kitsu:<Anime A>:1089: 'Anime A - S22E04 - <episode title>' -> staffel-22/episode-1 by number

**Reading.** 11 of the 12 answers (6 requests, 2 plugins) are located, and every located result is kept and right by the episode filter; before step 35 the plugins built the page from the request's numbers in the sites' own Staffel numbering (Anime A S5E2: the site's staffel-5, episode 2). Three cases carry the findings:

- **Anime A S22E4 on s.to is one episode off.** Cinemeta's regular list puts the entry at position 1088; the sites number the episode 1089 (Kitsu agrees: `kitsu:<Anime A>:1089` carries the same title and air date). aniworld's rows show the English title, so rule 1 picks the right row among the five neighbours (staffel-22/episode-1); s.to's anime rows show `Episode 1088` in place of the English title, so the number alone decides and the row is "<episode title>" (staffel-21/episode-197, the episode before). The Kitsu number places it right on both sites. Where IMDb's list lacks an episode the sites count, every later IMDb request is off by that gap on s.to; a request from the anime catalogs is not.
- **Anime H S4E1 on s.to finds no row.** The site's "Anime H" has 63 rows in five Staffeln. Its staffel-3 (the Swordsmith Village arc) shows the German titles of that arc ("Jemandes Traum" is "Someone's Dream") but the English titles of the Entertainment District arc ("Sound Hashira Tengen Uzui", "Infiltrating the Entertainment District"...), a mistake in the site's data; the rows carry no numbers. The reference's English title matches no row, so sto answers nothing for the series, as the spec wants (never the request's numbers: staffel-4/episode-1 would have been the Hashira Training arc). aniworld's staffel-3 names the episode and is placed by the title.
- **Anime H S2E1** is placed by the title on both sites (Cinemeta's S2E1 is the Mugen Train arc's first episode, the sites' staffel-2/episode-1 too), and the `kitsu:` path answers the Anime A episode on both sites in 7 s with the translation.

**Open.** The reference carries Cinemeta's English title only. Both sites' rows carry the German title, which is right where s.to's English column is wrong, and s.to's anime rows carry no English title at all; a German title in the reference (TMDB names episodes in German) would let the title rules place both s.to cases above. Not in this change; for the maintainer to decide.

## After, row 30 (2026-10-10, dev container, `worktree-handoff-30` at 0421fa9 on `staging` 513f747)

The same 12 requests after row 30 of the ideas backlog: the filter reads the link labels of a result whose title or metadata names the episode too (its links of other episodes go; a result whose labelled links all name other episodes is dropped) and reads episode words without a season (`Folge 5`, `Episode 5`, `Ep. 5`, `E05`) besides `1x5` and `S01E05`. `PYTHONPATH=src xvfb-run -a poetry run python -P scripts/probes/series_episodes.py --json <file>`; the probe's own label pattern reads the episode words independently of the filter, as an oracle for its regressions. Since step 35 the probe hands each request's episode reference to the plugins (`located`).

| plugin | results | season_episode | episode | season | none | kept | narrowed | dropped | right | leaks | located | failed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| aniworld | 4 | 4 | 0 | 0 | 0 | 4 | 0 | 0 | 4 | 0 | 4 | 0 |
| cineby | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| einschalten | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| filmpalast | 6 | 6 | 0 | 0 | 0 | 6 | 0 | 0 | 6 | 0 | 0 | 0 |
| fireani | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| haschcon | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| hdfilme | 6 | 0 | 0 | 0 | 6 | 0 | 6 | 0 | 6 | 0 | 0 | 0 |
| kinoger | 7 | 0 | 0 | 0 | 7 | 0 | 7 | 0 | 7 | 0 | 0 | 0 |
| kinoking | 7 | 7 | 0 | 0 | 0 | 7 | 0 | 0 | 7 | 0 | 0 | timeout 5 |
| kinox | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| megakino | 5 | 5 | 0 | 0 | 0 | 0 | 4 | 1 | 4 | 0 | 0 | 0 |
| megakino_to | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | PluginUnreachableError 14 |
| moflix | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| movie2k | 1 | 0 | 0 | 0 | 1 | 0 | 1 | 0 | 1 | 0 | 0 | 0 |
| movie4k | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | PluginUnreachableError 14 |
| sto | 10 | 10 | 0 | 0 | 0 | 10 | 0 | 0 | 10 | 0 | 2 | 0 |
| streamcloud | 6 | 0 | 0 | 0 | 6 | 0 | 6 | 0 | 6 | 0 | 0 | 0 |
| streamkiste | 6 | 0 | 0 | 0 | 6 | 0 | 6 | 0 | 6 | 0 | 0 | 0 |
| **total** | 58 | 32 | 0 | 0 | 26 | 27 | 30 | 1 | 57 | 0 | 6 | 33 |

- **Right episode:** 57 of 58 results (2026-10-07: 70 of 73); every result the filter let through is the right episode. **Leaks:** none, by the oracle. **Dropped:** 1, megakino's "Series H" page for S02E05: its metadata names season 1 (the title names none, step 28's rule) and its only label is `1x5 Voe`, the page the 2026-10-07 audit dropped too.
- **The narrowing of a title-named episode** touched 4 megakino results, the only ones whose metadata names the episode and whose links carry labels (hdfilme, kinoger, movie2k, streamcloud and streamkiste name no episode and are decided by their labels as before; aniworld, filmpalast, kinoking and sto carry no labels): "Series E - Staffel 1", "Series G - Staffel 1", "Series G - 1 Staffel" and "Series H" for S01E05, each with one link labelled with the requested episode, so no link was removed and nothing was dropped by the new rule (before the change the same results passed as kept, with the same link). The leak class of the step 21 review (a page titled after the episode but linking the season's) did not occur in these 12 requests; its proof is `TestTitleNamedEpisodes` in `tests/unit/infrastructure/test_episode_filter.py`.
- **Episode words:** no site labelled a link with `Folge`, `Episode` or `E<n>` in this run (the labels are `1x5 dropload`, `1x1 Voe`, `1x1 Stream HD`, `1x1 vinovo.to` or empty), so the word pattern is proven by `TestEpisodeWords` only.
- **Fewer results than on 2026-10-07** (58 against 73), none lost to the filter: fireani 0 (its search timed out in all 14 searches, `fireani_timeout`; before 4), kinoking 7 (5 timeouts; before 10 with 3), hdfilme, streamcloud and streamkiste 6 each (before 7), kinoger 7 (before 9), megakino 5 (before 6), movie2k 1 (before 2), sto 10 (before 11). The probe records only results that reached the filter; a site search that gave no hit or timed out leaves none. Per request 2 to 8 results: Series C 2 (kinoger, sto), Anime I 2 (aniworld, kinoking), Anime A, Anime H and Series E 3 each, both Series H requests 8.
- **Unreachable sites:** megakino_to and movie4k in all 14 searches (`PluginUnreachableError`), as before. cineby's search API host resolves to a private address in the dev container, which the request guard refuses (`private_address_refused`, all 14 searches); whether production, which resolves through the VPN, sees the same was not measured.
- **Located:** aniworld 4 of 4 (Anime A S1E1 by number; Anime G, Anime H and Anime I S1E1 by title), sto 2 (Anime A and Anime H S1E1); sto's other 8 results are its non-anime series.

## Row 71: does a German episode title add streams? (2026-10-11, dev container, `handoff-71` on `staging` ad74cd3)

N21's question, measured before it is built. `PYTHONPATH=src xvfb-run -a poetry run python -P scripts/probes/series_episodes.py --plugins sto,aniworld <ids>`, read-only against the sites, 1 min. The ten requests are step 35's six plus Anime G (`<Anime G>`) and Anime I (`<Anime I>`), each with S01E01 and the first episode of the last season Cinemeta lists (AoT season 4 of 4, Anime I season 2 of 2). With both sites among the plugins, the probe now compares the links of the kept results that show the episode: aniworld's `/redirect/<n>` links are followed as the registry follows them at play time (`_follow_redirects()`, three at a time), each link becomes its hoster and file id, and only counts per hoster are printed.

| request | sto | aniworld | hoster | both | sto only | aniworld only |
|---|---|---|---|---:|---:|---:|
| Anime A 1:1 | staffel-1/episode-1 by number | staffel-1/episode-1 by number | byse, filemoon, playmogo, vidmoly, voe | 0 | voe 2 | 1, 2, 3, 3, 3 |
| Anime A 5:2 | staffel-2/episode-1 by number | staffel-2/episode-1 by number | byse, filemoon, playmogo, vidmoly, voe | 0 | voe 2 | 1, 2, 3, 3, 3 |
| Anime A 22:4 | staffel-21/episode-197 by number (one off) | staffel-22/episode-1 by number | byse, filemoon, playmogo, vidmoly, voe | 0 | voe 2 | 1, 2, 3, 3, 3 |
| Anime H 2:1 | staffel-2/episode-1 by title | staffel-2/episode-1 by title | byse, filemoon, playmogo, vidmoly, voe | 0 | voe 1 | 1, 2, 3, 3, 3 |
| Anime H 4:1 | nothing | staffel-3/episode-1 by title | byse, filemoon, playmogo, vidmoly, voe | 0 | 0 | 1, 2, 3, 3, 3 |
| `kitsu:<Anime A>:1089` | staffel-22/episode-1 by number | staffel-22/episode-1 by number | byse, filemoon, playmogo, vidmoly, voe | 0 | voe 2 | 1, 2, 3, 3, 3 |
| Anime G 1:1 | nothing | staffel-1/episode-1 by title | filemoon, playmogo, vidmoly, voe | 0 | 0 | 3, 3, 3, 3 |
| Anime G 4:1 | staffel-4/episode-1 by title | staffel-4/episode-1 by title | filemoon, playmogo, vidmoly, voe | 0 | voe 2 | 3, 3, 3, 3 |
| Anime I 1:1 | nothing | staffel-1/episode-1 by title | byse, filemoon, playmogo, vidmoly, voe | 0 | 0 | 1, 2, 3, 3, 3 |
| Anime I 2:1 | nothing | staffel-2/episode-1 by title | byse, playmogo, vidmoly, voe | 0 | 0 | 3, 3, 3, 3 |

(byse is `bysezejataos.com`, filemoon `filemoon.to`, playmogo `playmogo.com`, vidmoly `vidmoly.biz`, voe `voe.sx`; the "aniworld only" counts follow the hoster column's order. No link stayed unresolved.)

- **Placement:** aniworld placed all 10 episodes, 4 by number (Anime A, the kitsu id) and 6 by title. sto placed 6: 4 by number and 2 by title. One of its number placements is one off: for Anime A 22:4 it places staffel-21/episode-197, while aniworld places staffel-22/episode-1, step 35's known case. sto answered nothing for 4 requests (Anime H 4:1, Anime G 1:1, Anime I 1:1 and 2:1), and aniworld placed all four by title.
- **Overlap:** sto links VOE only, 1 or 2 files per episode. aniworld links 4 or 5 hosters with 3 files each on most, VOE among them. The two sites share no file: the comparison was checked in-process for Anime A 1:1. All 14 links have the same form (`/e/<id>` or `/<name>-<id>.html`, 12-character ids), and the 14 ids are distinct. sto's VOE files are therefore other uploads of the episode, not missed matches.
- **Verdict:** aniworld places every episode sto cannot place, and where both place one, sto adds no hoster, only 1 or 2 further VOE files next to aniworld's three. Under the decision rule N21 is shelved: a German episode title would let sto place the four episodes it misses, but aniworld already serves them with more hosters.
- **Limits:** ten requests across four anime, one run, the sites' state. Only sto and aniworld were asked. The overlap compares file ids, so a re-upload of the same video under a new id counts as a different file. The one-off number placement (Anime A 22:4) is outside the question, and a German title would fix it only with N21's rule change: `locate()` takes the row with the number when that row shows no English title (s.to's anime rows) and compares English titles only, while N21 compares the German column among the neighbours too (step 35's Open). The stream builder keeps the best-ranked stream per hoster and language (`deduplicate_by_hoster()`), so sto's VOE of the episode before can take aniworld's VOE slot; that is a decision of its own for the maintainer (Mr. Fable's review, 2026-10-11). Whether sto places non-anime series that aniworld lacks was not asked: N21 is about anime episode titles.

## Row 72: how far an IMDb request numbers a long-running anime (2026-10-11, dev container, `handoff-72` on `staging` 6cebe24)

The maintainer's question of 2026-10-11, measured before a fix. `PYTHONPATH=src poetry run python -P scripts/probes/anime_offsets.py`, read-only against Cinemeta and the Anime Kitsu addon. The eleven series are long-runners whose Kitsu entry spans the series, so the Kitsu number counts as the sites do; each id was checked against the name the addon's meta gives. For each Kitsu episode the addon maps to an IMDb season and episode (`imdbSeason`/`imdbEpisode`), the probe asks the app's episode reference (`TitleResolver.episode_ref()`) for the absolute number an IMDb request for that episode gets from the mapped id's Cinemeta list, and subtracts the Kitsu number.

| Series | Kitsu | Episodes | Mapped | Equal | +1 | −1 | Off more | Not in Cinemeta | Season 0 | Unmapped | Not in Kitsu | First off |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Anime A | 12 | 1410 | 1409 | 589 | 0 | 590 | 0 | 230 | 0 | 1 | 0 | 591 (S16E12, −1) |
| Anime F | 210 | 1585 | 1585 | 1217 | 0 | 0 | 0 | 368 | 0 | 0 | 0 | – |
| Anime C: Sequel | 1555 | 500 | 500 | 500 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – |
| Anime C | 11 | 220 | 220 | 220 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – |
| ANIME E | 244 | 366 | 366 | 366 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – |
| Anime C: Spin-off | 13051 | 293 | 293 | 293 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – |
| Anime J | 13209 | 170 | 170 | 170 | 0 | 0 | 0 | 0 | 0 | 0 | 13 | – |
| Anime P | 4676 | 175 | 175 | 175 | 0 | 0 | 0 | 0 | 0 | 0 | 153 | – |
| Anime T (2011) | 6448 | 148 | 148 | 148 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – |
| Anime M: Sequel | 10879 | 131 | 131 | 131 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – |
| Anime D | 442 | 224 | 224 | 49 | 95 | 0 | 80 | 0 | 0 | 0 | 2 | 50 (S2E1, +1) |

(Not in Cinemeta: mapped episodes Cinemeta does not list. Season 0: mapped to a special, which has no absolute number. Not in Kitsu: regular Cinemeta entries that no Kitsu episode maps to. The causes below were read from the same metas.)

- **Anime A:** the addon maps every Kitsu episode except 590 ("History's Strongest Collaboration vs. Glutton of the Sea", 2013-04-07, the crossover special), which IMDb lists in no season. From Kitsu 591 (S16E12) on, Cinemeta's position is one below the Kitsu number: 590 episodes, up to Kitsu 1180 (S23E25, the newest episode Cinemeta lists). Row 71's S22E04 (1088 for 1089) is one of them. The 230 episodes not in Cinemeta (Kitsu 1181 to 1410, S23E26 to S23E255) are mostly the addon's entries ahead of the broadcast; it dates the first and the last 2026-09-27.
- **Anime F:** Kitsu 1 to 1217 (S1E1 to S34E31) are equal. The 368 episodes not in Cinemeta (Kitsu 1218 to 1585, S34E32 to S34E399) are entries ahead of the broadcast; the addon dates the first and the last 2026-10-10.
- **Anime D:** Cinemeta lists S2E0 and S4E0 among the regular seasons. No Kitsu episode maps to them, but the position counts them: Kitsu 50 to 144 (S2E1 to S3E47) are one high, Kitsu 145 to 224 (S4E1 to S5E40) two high.
- **Anime J and Anime P:** Cinemeta lists later seasons that Kitsu keeps as entries of their own (Anime J's season 2, 13 entries; Anime P's seasons 5 to 8, 153 entries, the series of 2014 and 2018). They follow the mapped episodes and shift no number.
- **The other six** number every mapped episode as Kitsu does.
- **What it sizes:**
  - sto today: its anime rows show no English title, so `locate()` takes the row with the reference's number (`episode_index.py`, rule 1). Among these eleven series that is wrong for Anime A from S16E12 on (590 of the 1179 mapped episodes Cinemeta lists) and for Anime D from S2E1 on (175 of 224), and right for the other nine. aniworld's rows show the English title, and the title picks among the rows within 5 of the number (`_NUMBER_SLACK`), so an offset of 1 or 2 leaves the right row among them.
  - The Kitsu number fix (the addon's mapping read backwards: the Kitsu episode of the request's IMDb episode) covers every mapped episode, so all 765 episodes that are off here (590 + 95 + 80). It cannot help the regular Cinemeta entries no Kitsu episode of the series maps to (Anime D' S2E0 and S4E0, the later seasons of Anime J and Anime P); those keep the position.
  - A narrower candidate the table shows: a position that skips episode 0 entries would give Anime D' 175 episodes their Kitsu number and change nothing else here (checked on the same metas). Anime A's −1 needs Kitsu's number: IMDb lacks the episode, so no count over Cinemeta's list can find it.
- **Limits:** eleven series, one run, the catalogs' state. The addon's mapping counts as the truth; that the sites number as Kitsu does was checked for Anime A only (row 71: sto and aniworld show Kitsu's 1089 for S22E04). The probe compares numbers, not placements: no site was asked, and whether a plugin serves the wrong episode also depends on its rows' titles. Most anime have a Kitsu entry per season, whose number counts that season alone; they are out of scope here (a `kitsu:` request of such an entry keeps the position when its number lies more than 5 from it, `_KITSU_SLACK`).

## Row 77: the position skips episode 0 entries of regular seasons (2026-10-11, dev container, `handoff-77` on `staging` 8ad03c8)

`TitleResolver.episode_ref()` counted the position among all entries of season 1 and up. Cinemeta lists some episode 0 entries in regular seasons (Anime D' S2E0 "Opening" and S4E0, the Pyramid of Light film), so every later episode got a number one or two above Kitsu's and the sites'. The position now counts the entries with season 1 and up and episode 1 and up. An episode 0 entry of a regular season has no position, as a special has none: its reference keeps the title and air date, and the absolute number only when a `kitsu:` request names it. `_KITSU_SLACK` measures against the new position. The search cache's key carries the number, so each affected episode misses the cache once after the deploy.

Row 72's probe again (`PYTHONPATH=src poetry run python -P scripts/probes/anime_offsets.py`, 2026-10-11 ~): Anime D' row is now 224 equal, 0 off (was 49 equal, 95 one high, 80 two high), and the other ten series give the same counts as in row 72's table.

Which anime carry such entries: of the 898 IMDb ids behind the Anime Kitsu addon's popular, top-rated and airing catalogs (1000 entries each), the Cinemeta metas of three list an episode 0 in a regular season. Before and after, the probe's comparison per Kitsu entry with HEAD's and the new position:

| Series | Kitsu | Episode 0 entries | Equal before | Equal after | Site's number of one affected episode |
|---|---|---|---:|---:|---|
| Anime D (<Anime D>) | 442 | S2E0, S4E0 | 49 of 224 (S2E1: 51, S4E1: 147) | 224 of 224 (S2E1: 50, S4E1: 145) | aniworld: S2E1 shows 50, S4E1 shows 145 (seasons 1..5 numbered 1–49, 50–97, 98–144, 145–184, 185–224); sto: rows without numbers, placed by title |
| Anime R (<Anime R>) | 359 | S1E0 | 0 of 112 (all one high; S1E1: 2) | 112 of 112 (S1E1: 1) | aniworld: S1E1 shows 1 (seasons numbered 1–50, 51–112); sto: no such series |
| Anime S Chronicle (<Anime S>) | 154 | S1E0 | 0 of 26 (all one high; S1E1: 2) | 26 of 26 (S1E1: 1) | aniworld: rows without numbers, placed by title; sto: no such series |

Anime S (`kitsu:<Anime S>`, three OVAs) maps to Cinemeta's season 3 of the same IMDb id: its own Kitsu entry counts from 1 (+53 before, +52 after), the case of row 72's Anime J and Anime P entries, out of scope here.

`series_episodes` (`--plugins sto,aniworld`) for <Anime D>:

- S2E1, reference 50: aniworld places staffel-2/episode-1 by number (its row shows 50, the English title "The Mystery Duelist Part 1"); sto places staffel-2/episode-1 by title (its rows show no number). Both right.
- S4E1, reference 145: sto places staffel-4/episode-1 by title. aniworld answers nothing. Its row 145 is the right one, but its English title is the dub's ("Pulse of a New Darkness" against Cinemeta's "A <episode title> (<episode title>)", 0.53 below the 0.6 a neighbour needs), and `locate()` trusts an exact number only for a row without a title. With the old 147 it found nothing either, so the change costs nothing here. The miss is `locate()`'s, not the position's: a follow-up candidate.

Limits: one run, the catalogs' state at the time; the scan covers the addon's three catalogs and no full list (a series outside them with such entries is not counted). The sites' count was read on aniworld only; sto lists Anime D without numbers and the other two not at all.

## Row 76: an IMDb request carries the Kitsu number (2026-10-11, dev container, `handoff-76` on `staging` ac3b279)

`AnimeIdResolverPort.kitsu_episode()` gives an IMDb series request's episode its number in the one Kitsu entry that counts the series from its start, and the episode reference takes it on a `kitsu:` request's terms (within 5 of the position, else the position). The candidates are the id list's series entries without a TheTVDB season for the IMDb id (`AnimeIdLists.whole_series()`); the addon's record of each (a missing one fetched once per title, the fetch shared by concurrent requests) counts when it names the IMDb id and places its episode 1 at S1E1, and exactly one of the counting records' episodes must sit at the request's season and episode. In the list of 2026-10-11 (7956 entries with both ids), 376 IMDb ids have such entries, and one has more than one: Anime P (`<Anime P>`: 4676, 8203, 13658).

Row 72's probe through the app's look-up (`anime_offsets.py` now asks `kitsu_episode()` with the id list loaded first and counts the episodes it numbered; `--position` gives the numbering without it), 16 min:

| Series | Kitsu | Numbered | Equal, position | Equal, with the number |
|---|---|---:|---:|---:|
| Anime A | 12 | 1179 | 589 (590 one low) | 1179 |
| Anime F | 210 | 1217 | 1217 | 1217 |
| Anime C: Sequel | 1555 | 500 | 500 | 500 |
| Anime C | 11 | 220 | 220 | 220 |
| ANIME E | 244 | 366 | 366 | 366 |
| Anime C: Spin-off | 13051 | 0 | 293 | 293 |
| Anime J | 13209 | 0 | 170 | 170 |
| Anime P | 4676 | 175 | 175 | 175 |
| Anime T (2011) | 6448 | 148 | 148 | 148 |
| Anime M: Sequel | 10879 | 131 | 131 | 131 |
| Anime D | 442 | 0 | 224 | 224 |

(Numbered: the mapped episodes Cinemeta lists whose IMDb request got a Kitsu number. The episodes Cinemeta does not list stay out of the comparison, as in row 72: Anime A's 230, Anime F's 368. The other columns (episodes, mapped, not in Cinemeta, season 0, unmapped, not in Kitsu) match row 72's table.)

- **Anime C: Spin-off** (`<Anime C: Spin-off>`) and **Anime J** (`<Anime J>`): the list gives their entries TheTVDB season 1, so they are no candidates; their position equals Kitsu's number already.
- **Anime D:** the list names `<Anime D>` for `kitsu:<Anime D>`, the addon `<Anime D>`, which Cinemeta uses: out of the look-up's reach. Its position is right since row 77.
- **Anime P:** `--kitsu 4676,8203,13658` numbers 4676's 175 episodes and none of the later series' episodes (8203: 102 episodes, 0 numbered, +175 from S5E1 on; 13658: 51, 0 numbered, +277 from S8E1 on, as with the position alone): their episode 1 is no S1E1, so their own count from 1 is never taken.

`series_episodes` (`--plugins sto,aniworld`) for Anime A, before (the probe at ac3b279) and after:

| Request | Reference | sto before | sto after | aniworld before and after |
|---|---|---|---|---|
| S22E04 | 1088 → 1089 | staffel-21/episode-197, "<episode title>": the episode before | staffel-22/episode-1, "<episode title>" (Cinemeta: "<episode title>") | staffel-22/episode-1 |
| S16E12 | 590 → 591 | nothing | staffel-15/episode-11, "<episode title>" (Cinemeta: "<episode title>") | staffel-15/episode-11 |

The metadata phase (`stremio_phase_seconds{phase="metadata"}`, read from `/metrics` around each request): the dev server with a fresh diskcache, Anime A S22E04, S22E05 and S22E06 ten seconds apart, before at ac3b279.

| Request | Before | After |
|---|---:|---|
| S22E04, nothing cached | 0.681 s | 0.696 s: the list not in memory yet, so no number; it loaded in the background |
| S22E05, the titles cached | 0.003 s | 0.319 s: the addon's Anime A record fetched; 1090 |
| S22E06, everything cached | 0.004 s | 0.005 s; 1091 |

Limits: one run each, the catalogs' and the sites' state of the day; sto's placement was read for two Anime A episodes. The first requests after a start go without the number until the list is in memory (S22E04 above). Before Mr. Fable's review, a request that came while another one fetched a record it needed went without the number too: the Anime P run, its three series compared at once, numbered none of 4676's episodes. Concurrent requests now share the one fetch, and the same run numbers all 175. Production follows the deploy.

## Row 82: how far aniworld's exact-number row can be trusted (N26) (2026-10-11, dev container, `handoff-82` on `staging` 8a756c4)

The maintainer's decision of 2026-10-11: N26 is measured first. `PYTHONPATH=src poetry run python -P scripts/probes/anime_offsets.py --aniworld --slug <Anime L>=anime-l --slug <Anime M: Sequel>=anime-m-sequel`, read-only against Cinemeta, the Anime Kitsu addon, Fribb's list and aniworld. Per series the probe takes the series aniworld's search gives for Cinemeta's name (the plugin's closest relevant hit) and reads its season pages once, through the plugin's fetcher, into the index `episode_index()` builds. It builds every regular Cinemeta episode's reference as the app does for an IMDb request (`TitleResolver.episode_ref()` with row 76's Kitsu number where the look-up gives one) and places it with `locate()`. The outcomes: at the row with the reference's number (exact), at a row near it whose title wins (neighbour, with the offset), by the title alone (rules 2 and 3), nowhere although the number's row has an English title (N26), or nowhere.

| Series | aniworld | Rows (numbered) | Episodes (Kitsu number) | Exact | Neighbour (offsets) | Title | N26 (number's row best) | Nothing | Title-confirmed | At another row (ties) |
|---|---|---:|---:|---:|---|---:|---:|---:|---:|---:|
| Anime D (`<Anime D>`) | anime-d | 236 (224) | 224 (0) | 40 | 33 (−5: 3, −4: 4, −3: 3, −2: 7, −1: 8, +1: 5, +3: 1, +5: 2) | 4 | 147 (18) | 0 | 77 | 37 (0) |
| Anime K (`<Anime K>`) | anime-k | 1219 (0) | 1375 (276) | 0 | 0 | 1211 | 0 | 164 | 0 | 0 |
| Anime L (`<Anime L>`) | anime-l ¹ | 104 (0) | 253 (0) | 0 | 0 | 107 | 0 | 146 | 0 | 0 |
| Anime M (`<Anime M>`) | anime-m | 291 (291) | 291 (0) | 291 | 0 | 0 | 0 | 0 | 291 | 0 |
| Anime A (`<Anime A>`) | anime-a | 1180 (1177) | 1179 (1179) | 1125 | 7 (−5: 1, −4: 1, −2: 1, −1: 3, +4: 1) | 3 | 44 (34) | 0 | 1127 | 7 (0) |
| Anime F (`<Anime F>`) | anime-f | 1254 (1254) | 1217 (1217) | 1002 | 129 (−5: 5, −4: 2, −3: 2, −2: 10, −1: 92, ±0: 2, +1: 5, +2: 4, +3: 1, +4: 4, +5: 2) | 2 | 83 (29) | 1 | 1133 | 131 (92) |
| Anime C: Sequel (`<Anime C: Sequel>`) | anime-c-sequel | 500 (500) | 500 (500) | 488 | 4 (−3: 1, −2: 1, −1: 1, +1: 1) | 0 | 8 (4) | 0 | 492 | 4 (3) |
| Anime C (`<Anime C>`) | anime-c | 220 (220) | 220 (220) | 198 | 2 (−5: 1, −2: 1) | 0 | 20 (13) | 0 | 200 | 2 (0) |
| Anime E (`<Anime E>`) | anime-e | 414 (363) | 366 (366) | 360 | 1 (+1: 1) | 2 | 2 (2) | 1 | 361 | 1 (0) |
| Anime C: Spin-off (`<Anime C: Spin-off>`) | anime-c-spin-off | 293 (292) | 293 (0) | 289 | 1 (−2: 1) | 2 | 1 (0) | 0 | 291 | 2 (0) |
| Anime J (`<Anime J>`) | anime-j | 172 (168) | 183 (0) | 161 | 4 (−1: 4) | 12 | 2 (1) | 4 | 166 | 5 (4) |
| Anime P (`<Anime P>`) | anime-p | 353 (328) | 328 (175) | 326 | 2 (−5: 1, −3: 1) | 0 | 0 | 0 | 328 | 2 (2) |
| Anime T (`<Anime T>`) | anime-t | 148 (148) | 148 (148) | 148 | 0 | 0 | 0 | 0 | 148 | 0 |
| Anime M: Sequel (`<Anime M: Sequel>`) | anime-m-sequel ¹ | 131 (131) | 131 (131) | 131 | 0 | 0 | 0 | 0 | 131 | 0 |
| All | | | 6708 | 4559 | 183 | 1343 | 307 (101) | 316 | 4745 | 191 (101) |

¹ Cinemeta's name finds another series: for "Anime L" the closest hit is "Anime L 2020" (67 rows), for "Anime M: Sequel" the one-row "Anime M: Sequel: Beerus" (aniworld writes "Dragonball Super"). The table has the pages named with `--slug`. "Anime L: Digital Monsters" holds Adventure and 02 (104 rows); Cinemeta's list goes on with the later series. aniworld lists all fourteen series.

Title-confirmed: the placements a title confirmed (rule 1 with a title, rules 2 and 3) where the site has a row with the reference's number. At another row: those whose row is not the number's, 191 of 4745 (4.0 %); ties: the number's row matches as well as the row taken.

**N26's titles.** N26 calls aniworld's English title the dub's; it is the other way round. aniworld's Anime D titles are mostly the translated Japanese ones ("Pulse of a New Darkness", #145), Cinemeta's are the 4Kids dub's ("A <episode title> (<episode title>)"); Wikipedia's list of the episodes gives both per number. aniworld's German titles translate the dub's, part numbers included ("<episode title>" for "Yugi vs. Pegasus: Match of the Millennium, Part 4"). In the long-runners both sides translate the same Japanese title in other words ("Magellan's Scheme! The Jailbreak is Obstructed" against "Magellan's Tricky Move! A Foiled Escape Plan!", 0.52). Anime M has the dub's titles on both sides: all 291 confirmed.

**The check by eye.**

- N26's cases: the probe's five per series (30, fewer where a series has fewer) and 39 more, 69 in all (Anime D 16, Anime A 10, Anime F 10, Anime C 20, Anime C: Sequel 8, Anime E 2, Anime J 2, Anime C: Spin-off 1). The number's row was the episode in all 69. Sources: Wikipedia's lists for Anime D (12 cases) and Case Closed (5: the Funimation title against the translated Japanese one), aniworld's German titles for 4 more of Anime D, the rest by reading the two titles side by side. Anime C: Sequel's #53 by its neighbours: aniworld's English title is "Title", and rows 51 to 55 match Cinemeta's 51 to 55.
- The placements at another row, the premise of the 4.0 %: all 90 that are no tie and 25 of the 101 ties. The number's row was the episode in 114 of the 115. The row `locate()` took was right once: Anime F S31E10 "The Black Organization's Scheme (Hunt)" (#1077). aniworld numbers it 1078: it counts "Police Academy Arc Wild Police Story CASE. Rei Furuya" as 1077 and lacks "(Landing)". So the 4.0 % is no share of wrong numbers: where a title could tell, it showed the number's row wrong in 1 of 4745 placements.
- Why the titles mislead: `token_set_ratio` gives 1.00 when one title's words are a subset of the other's, so a part's number does not count ("Something to Fill the Hole" against "Hole", "Metropolitan Police Detective Love Story 2 (2)" against its "(1)"), and `_best` keeps the first of equal scores, the earlier row. Anime F's 92 ties are all a later part or chapter of a case placed on an earlier one (86 one row back, 5 two, 1 three). The other 90 take a row whose title shares words, at 0.60 to 1.00 (1.00 for a subset again: Anime C: Spin-off's "Family Day" on "A Day in the Life of the Uzumaki Family"): "Clash In The Coliseum (Part 2 of 6)" to "(Part 6 of 6)" all land on #129 "Coliseum in the Sky - Yugi vs Kaiba". Rule 3 put "The Final Face Off (Part 1 of 5)" to "(Part 4 of 5)" (#138 to #141) on the series' last four episodes, "The Final Duel: Part 1" to "Part 4" (#221 to #224).

**The maintainer's options for N26:**

| Option | The change in `locate()` | Placed, nothing now | Other placements | Wrong in the check |
|---|---|---:|---|---|
| The candidate | Rule 1: when no row near the number reaches 0.6, the number's row counts, before rules 2 and 3 | 307: Anime D 147, Anime F 83, Anime A 44, Anime C 20, Anime C: Sequel 8, Anime E 2, Anime J 2, Anime C: Spin-off 1 | 8 title placements far from the number move to its row (Anime D 4, Anime F 2, Anime C: Spin-off 1, Anime J 1), all 8 wrong now | 0 of 69; the titles showed the number wrong in 1 of 4745 |
| A narrower one | The same, only when the number's row is also the best title match near the number | 101: Anime A 34, Anime F 29, Anime D 18, Anime C 13, Anime C: Sequel 4, Anime E 2, Anime J 1 | 2 of the 8 | 0 of 69: the check found no wrong row for it to keep out; 206 episodes stay without a row |
| Nothing | — | 0 | — | The 307 episodes stay without an aniworld result |

**Beyond N26** (the same function, a decision of its own): of the 191 placements at another row, the check covered 115, and 114 of them give the wrong episode today; the 76 ties not checked follow the same pattern (a later part on an earlier one).

- Ties to the number's row (a neighbour wins only with a better title): the 101 ties move to the number's row, right in all 25 checked.
- The number first (the number's row whenever the site shows the reference's number; the title only without one): the 191 move to the number's row (right in 114 of the 115 checked; Anime F's #1077 wrong), and N26's 307 are placed.

Limits: one run, the catalogs' and aniworld's state of 2026-10-11 (the runs 4 min apart gave the same counts for the same pages), the dev container, nothing in production. The probe searches with Cinemeta's name; the app searches with TMDB's titles, not measured here (¹). Anime K and Anime L show no absolute numbers, so rule 1 never applies and N26 does not arise; their title placements were not checked (Anime L places 107 episodes on 104 rows, so at least three rows take two episodes). The 4554 placements a title confirmed at the number's row were not checked by eye: title and number agree there. Of the ties, 25 of 101 were checked. Anime D, Anime C: Spin-off and Anime J have no Kitsu number (row 76): their references use the position. aniworld's English titles of Anime F keep " [JP" of "[JP Episode N]" (the index cuts "Episode N]" only, so "jp" is a word of every Conan title), and Anime C: Sequel's #53 is "Title".

### Row 87: the number first, before and after (2026-10-12, dev container, `handoff-87` on `staging` e149f55)

The maintainer chose the number first: `locate()` takes the row that shows the reference's absolute number, whatever its title, and the title rules decide only without that row or without a number. The command above, read-only against the same sites: before on `staging`'s `locate()`, after with the number first. The before run gave row 82's table to the count; per series, before → after:

| Series | Exact | Neighbour | Title | N26 | Nothing | At another row |
|---|---:|---:|---:|---:|---:|---:|
| Anime D | 40 → 224 | 33 → 0 | 4 → 0 | 147 → 0 | 0 | 37 → 0 |
| Anime K | 0 | 0 | 1211 | 0 | 164 | 0 |
| Anime L | 0 | 0 | 107 | 0 | 146 | 0 |
| Anime M | 291 | 0 | 0 | 0 | 0 | 0 |
| Anime A | 1125 → 1176 | 7 → 0 | 3 | 44 → 0 | 0 | 7 → 0 |
| Anime F | 1002 → 1216 | 129 → 0 | 2 → 0 | 83 → 0 | 1 | 131 → 0 |
| Anime C: Sequel | 488 → 500 | 4 → 0 | 0 | 8 → 0 | 0 | 4 → 0 |
| Anime C | 198 → 220 | 2 → 0 | 0 | 20 → 0 | 0 | 2 → 0 |
| Anime E | 360 → 363 | 1 → 0 | 2 | 2 → 0 | 1 | 1 → 0 |
| Anime C: Spin-off | 289 → 292 | 1 → 0 | 2 → 1 | 1 → 0 | 0 | 2 → 0 |
| Anime J | 161 → 168 | 4 → 0 | 12 → 11 | 2 → 0 | 4 | 5 → 0 |
| Anime P | 326 → 328 | 2 → 0 | 0 | 0 | 0 | 2 → 0 |
| Anime T | 148 | 0 | 0 | 0 | 0 | 0 |
| Anime M: Sequel | 131 | 0 | 0 | 0 | 0 | 0 |
| **All** (6708 episodes) | 4559 → 5057 | 183 → 0 | 1343 → 1335 | 307 → 0 | 316 | 191 → 0 |

- **What changed.** An offline diff of the same rows against a copy of `staging`'s `locate()`: 498 placements change, exactly N26's 307 and the 191 at another row. Of the 191, 181 were neighbours, 8 far title placements (Anime D 4, Anime F 2, Anime C: Spin-off 1, Anime J 1) and 2 a later part of a split double-length Anime F episode that keeps one number (#219 and #345, now on the first part). No placement without the number's row changed. The check by eye above covers 69 of the 307 and 115 of the 191: the number's row is the episode in all of them but Anime F #1077, which now takes aniworld's #1077, the Rei Furuya episode: the change's known limit, a site numbering shifted against Kitsu's.
- **sto** (`series_episodes.py --plugins sto`, two requests per series: the regular episode a third in and the last one, 28 requests, 11 min apart): 19 requests have results. 17 are the same, Anime D S2E26 keeps its page with the rule now named `fuzzy_title`, and Anime C: Sequel S8E16 gains a second result, from the series Anime C (sto's #167). The same requests with `--plugins aniworld,sto` (before, staging's code imported from the main checkout, and after): aniworld places three episodes it gave nothing for (Anime D S2E26, Anime C S5E41, Anime J S1E62) and gives Anime C's #167 for Anime C: Sequel S8E16 too.
- **The parent series' episodes** (a finding beyond `locate()`): TMDB's German title "Anime C: Sequel" adds the base query "Anime C" (`build_search_queries()`), aniworld's and sto's searches then locate the reference on the series Anime C too, and the title matcher keeps "Anime C" for "Anime C: Sequel" (a result that only drops words keeps 1.00). On the indices, Anime C: Sequel's 500 references land on Anime C's: aniworld 42 before (by title) and 235 after; sto 201 before (its anime rows show no English title, so the number placed them already) and 210 after (9 of its Anime C rows carry text in the English column, "Episode 166 +"). Live, Anime C: Sequel S1E6, S2E9, S3E1 and S9E10 (1 min apart): sto gives an Anime C result for all four before and after, aniworld for two before and all four after. Every "Parent: Subtitle" title whose parent is another numbered series on these sites is exposed; the cause lies in the base query's hits and the matcher (KNOWN_ISSUES).

Limits: one run each, the sites' state of 2026-10-12, nothing in production. The sto comparison covers two requests per series, not the long-runners' full lists. The parent-series counts come from the indices for Anime C: Sequel on Anime C only, the live check from four requests; other parent and sub-series pairs were not measured.

### Row 92: the parent series yields to the sub-series, before and after (2026-10-12, dev container, `handoff-87` on `staging` a038575)

The fix of the finding above (Mr. Fable's decision (B)): a series result is partial when none of its titles holds a request title whole and one of them is a strict word subset of one (`is_partial_title`, a year is no word); a plugin's partial matches count only without its other match, and on a partial series aniworld and sto place the episode by its title alone. The same probes, read-only against the same sites: before with row 87 alone, after with row 92 on top (5 min).

- **Anime C: Sequel** S1E6, S2E9, S3E1 and S9E10 (`series_episodes.py --plugins aniworld,sto`): before, both plugins answered Anime C's episode with the number beside Anime C: Sequel's for all four (aniworld 8 results, sto 8); after, Anime C: Sequel's alone (aniworld 4, sto 4), each at its own number.
- **Anime E: Sequel** S1E1, S1E13, S2E1 and S3E5 (absolute 1, 13, 14 and 31; both sites keep it as Anime E's season 17): before, sto placed all four on season 17 by title and aniworld all four on Anime E's own episodes by number (S1E1, S1E13, S1E14, S2E11); after, both place all four on season 17 by title (staffel-17/episode-1, 13, 14, 31), which `staging`'s code (before row 87) did as well. No wrong placement.
- **Row 82's fourteen series** (`anime_offsets.py --aniworld`, now with the request's titles from the title client): none is partial, and the probe's table equals row 87's after-table cell for cell, its share line too (5052 title-confirmed placements with an exact-number row, none at another row).
- **sto** (`series_episodes.py --plugins sto`, row 87's 28 requests): 19 requests have results, 18 as in row 87's after-run; Anime C: Sequel S8E16 loses Anime C's #167. With `--plugins aniworld,sto` 19 of 21 are the same: Anime C: Sequel S8E16 loses Anime C's results of both plugins, and Anime C: Spin-off S1E293 shows sto's page once, as before row 87 (row 87's after-run listed it a second time with one link).

Limits: one run each, the sites' state of 2026-10-12, nothing in production. The partial rule was measured on Anime C: Sequel and the Anime E: Sequel only, the fourteen series of row 82 for no change; other parent and sub-series pairs were not run.

### Row 95: a position the anime ids do not vouch for, before and after (2026-10-12, dev container, `handoff-95` on `staging` 3e4d74f)

The code review of 2026-10-12 (finding 1): without the Kitsu number (right after a start, the hour after a failed list download, or an episode whose number does not come out) the reference carried the Cinemeta position, and row 87's `locate()` took the row that shows it, whatever its title; on Anime A from #590 on that row is the episode before. Mr. Fable's decision: the reference names its number's source (`EpisodeRef.source`). Kitsu's number and the `position` of a series no Kitsu entry counts whole keep row 87's rule; any other position is `unchecked` and takes a titled row only through the neighbour and title rules, as main did, and a number-only row as before; the number's row wins a title tie. The app's start loads the id list. Read-only against the same sites, 6 min; before on `staging`'s code (from a worktree of its own), after on `handoff-95`.

- **The review's case** (`anime_offsets.py --aniworld --imdb <Anime A> --start`, and an offline diff of the same references through main's, staging's and row 95's `locate()`): Anime A's 1179 regular episodes as right after a start, without the Kitsu number, judged by the Kitsu number the app finds once the list is in. 590 positions are off (#590 on). Of the 311 from position 590 to 900, the review's set, staging places 0 at the Kitsu number's row, main 298 and row 95 298 (the review counted 297 on its run). Over all 1179: staging 591, main 1119, row 95 1119. Row 95 takes main's row for each of the 1179 (only the labels differ: `neighbour` where main said `number`, `fuzzy_title` where it said `title`); 45 find no row on both, Anime A's N26 cases.
- **Row 82's probe with the Kitsu numbers** (`anime_offsets.py --aniworld`, the list in memory): the table equals staging's cell for cell on all 14 series (staging's code, row 95), the share line too (4921 title-confirmed placements with an exact-number row, none at another row); N26 is 0 everywhere. Anime D (224 exact), Anime C: Spin-off (292) and Anime J (168) have no whole-series Kitsu entry: their positions are trusted (0 unchecked) and placed as before. Against row 87's after-table, Anime L (1 by title, 252 nothing; then 107 and 146) and Anime M: Sequel (131 nothing; then 131 exact) differ because aniworld's search now answers "Anime L 2020" and "Anime M: Sequel: Beerus" first, for staging's code too.
- **Case 3's cost** (a series with a whole-series Kitsu entry whose episode got no number): Anime K has 1099 unchecked references (its aniworld rows show no numbers, so the titles placed them before as well: unchanged), Anime P 153 (175 got a Kitsu number). Without the tie rule, 2 of Anime P's 153 went to a neighbour whose English title is a word subset of the reference's and so matches as fully: #249 "Tartaros Chapter - Celestial Spirit King vs. Underworld King" to #246 "Tartaros Chapter - Underworld King", and #273 "Treasure" to #268 "Treasure Hunt". With the number's row first, both stay at their own row, and Anime P is 328 exact as before.
- **Right after a start** (`--start`: every position unchecked, as until the list is in memory), each series judged by the Kitsu number, row 95 against the same references as trusted positions (staging): Anime A 1119 vs 591, Anime F 1096 vs 1216, Anime C: Sequel 491 vs 500, Anime C 198 vs 220, Anime E 360 vs 363, Anime P 175 vs 175, Anime T 148 vs 148; 3587 vs 3213 of 4212 judged (Anime K's 276 and Anime M: Sequel's 131 find no numbered row either way). Anime D falls back to main's 40 exact, 33 neighbours, 4 by title and 147 nothing for that window. The window is the list's load, which the start now begins (`load_in_background()`): in the dev container 0.43 s from the network into an empty diskcache (longest event-loop stall 55 ms), and 0.01 s from the cache backend in a new instance, as after a restart (the list is kept 7 days).

Limits: one run each, the sites' state of 2026-10-12, nothing in production. A placement counts as right at the row that shows the Kitsu number: aniworld counts Anime A as Kitsu does, but not every series (Anime F #1077, row 87). The load times are the dev container's network and disk, not the production host's.

### Row 98: partial titles and the title sets, before and after (2026-10-12, dev container, `handoff-98` on `staging` 3b9ca72)

The code review of 2026-10-12, findings 4, 10 and 11: a mirror standby's results count for the member it stood in for (`standby_for`, `asked_plugin()`); an article and "and"/"und" are no title words (`is_partial_title`); every title info carries the request's series titles in all the languages the stream plugins search in (`TitleMatchInfo.series_titles`), which the matcher's partial flag and `EpisodeRef.titles` share, and the matcher normalises with the episode index's `normalize_title`. Read-only against the sites, 5 min; before on `staging`'s code 3b9ca72 (from an archive of that commit), after on `handoff-98`.

- **Title matching** (`title_match.py`, all stream plugins, five requests: Series I S1E1, Series J S1E1, Anime D S1E6, Anime C: Sequel S1E6, Anime E: Sequel S1E1): the tables before (1 min 55 s) and after are equal line for line. The partial verdicts stay: Anime C beside Anime C: Sequel is dropped for aniworld and sto, Anime E alone counts for both.
- **Placements** (`series_episodes.py --plugins aniworld,sto`, seven requests: Anime D S1E6 and S2E10, Anime C: Sequel S1E6 and S2E9, the Anime E: Sequel S1E1 and S2E1, Anime A S22E4): the same 13 results before and after, each at the same row by the same rule (only their order of arrival differs), no leak; Anime D S2E10 finds nothing either way.
- **The reviewed cases do not occur on today's pages:** no site names Series I or Series J without the article or with "&", and the title client's German title of <Anime D> is "Anime D", not the review's "Anime D". The unit tests carry findings 10 and 11; the probes ask every mirror group member, so finding 4's standby path is unit-tested only.
- **Observation (outside row 98):** aniworld's "Series J: The Anime" scores 0.74 for a Series J request and is kept before and after (fireani's, which names its year, is dropped by the year rule): a sub-series whose name holds the parent's title whole passes the 0.7 threshold. Production does not serve it (Mr. Fable on 1d79ec7: `aniworld_episode_not_located` for the spin-off's slug): its rows carry English titles and so no absolute number, and no title matches the request's episode, so only the title rules ran and found nothing. A kept spin-off whose rows show placeholder titles would be placed by the number for a trusted position; unobserved, idea N27.

Limits: one run each, the sites' state of 2026-10-12, nothing in production.
