# Series episodes per plugin

What the Stremio episode filter (`infrastructure/stremio/episode_filter.py`) does with each plugin's series results, measured before and after its change in step 21 (OpenSpec `continue-cut-searches`, decisions 6 and 7). Acceptance: no leaks after the change, and no plugin keeps fewer results of the right episode than before.

Measured with `scripts/probes/series_episodes.py`: it starts the app's composition (plugins, browser fallback, title lookup) and runs every stream plugin's search directly, with the queries, category, season and episode of a Stremio request, under the plugin timeout (30 s) and the plugin concurrency of the runner; mirror groups are not collapsed. Requests: the series of `docs/plans/round-titles.txt` (Breaking Bad S01E01 and S01E02, Dark, Stranger Things S04E01, Haus des Geldes, The Last of Us, One Piece, Attack on Titan, Demon Slayer, Frieren) plus Severance S01E05 and S02E05, 12 requests.

Columns: title-matching results (the title filter's survivors); their numbering from the plugin's `metadata` ints, else the release name (guessit): season and episode, episode only, season only, none; the filter's outcome: kept unchanged, narrowed (decided by the links' episode labels; a result whose labelled links all match counts here too), dropped; right: kept results whose title or a link label names the requested episode; leaks: kept results whose title or a link label (the filter's `1x5`/`S01E05` patterns, plus `Episode 5`, `Folge 5`, `E05`) names another episode; failed: searches that timed out or raised.

## Before (2026-10-07, dev container, `staging` 79a73c7)

The home connection without the VPN, fresh cache. Wall time per request 15–31 s.

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
- **Series results without episode numbers:** 42 of 76 results name no episode in the title. 26 of them carry episode labels on their links and are decided by them (below). 16 carry no episode information at all and are kept unchanged, and so are kinoger's 2 season packs ("Breaking Bad - Staffel 01-05"): aniworld 4, fireani 4, kinoger 10, all with empty link labels. aniworld and fireani open the requested episode's page themselves (aniworld `/staffel-<season>/episode-<episode>`, fireani's `GetEpisode` with season and episode), so their hits are the right episode, but nothing in the result says so; the stricter filter of decision 6 would drop all of them unless the plugins set `metadata["season"]` and `metadata["episode"]` (ints). kinoger gives one unlabelled link per title: the series' episode list (`1x1`-labelled links) was not found on those pages, so whether the link is the requested episode is open.
- **sto** stores its season in `metadata` as a string (`"season": str(season)`); the filter reads ints, so the release name (`S01E01`) decides there. Harmless today; with decision 6 the metadata should carry ints.
- **Decided by labels** (30: 27 narrowed, 3 dropped): hdfilme 7, streamcloud 7, streamkiste 7, movie2k 2, megakino 7 (4 season pages, 3 without a season in the title). The 3 drops are megakino pages of another season (One Piece "2 Staffel", Stranger Things "5 Stafffel" for S04, Severance S02E05 on a season 1 page): correct.
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
- **kinoger** (10): all narrowed by their `<season>x<episode>` labels to the requested episode. Before, every series page gave the script's first URL, S01E01, without a label (Stranger Things S04E01 got S01E01 from all four player tabs).
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
- **Fewer results than before, none of them lost to the filter:** kinoking 10 (3 searches timed out), megakino 6 with 3 right (1 timeout: the "Haus des Geldes - Staffel 1" page), filmpalast 6 (its search gave no "The Last of Us" hit this time), kinoger 9 (no "Dark" hit); no record of these results exists, so they never reached the filter. A rerun of the four plugins ten minutes later (`--plugins filmpalast,kinoger,kinoking,megakino`) gave the counts of the table before: filmpalast 7, kinoger 10, kinoking 13, megakino 7 (4 right, 3 dropped), no failures: 34 of 37 right. No plugin keeps fewer right episodes than before.
- **kinoger** (10 in the rerun): all narrowed by their labels, as before. The first run after the filter change found kinoger unreachable in all 14 searches: kinoger.com and kinoger.to answer HEAD and GET with 403 and `cf-mitigated: challenge`. Until step 21's group 6 the domain check fell back to the primary domain and the search went on through the browser, which solves the challenge; group 6 raised instead. de6032c counts an answer below 500 or a challenge page as reachable, the health monitor's rule, and kinoger searches again.
- **Unreachable sites:** megakino_to and movie4k answer neither HEAD nor GET on any domain (read timeouts). Their searches end in `PluginUnreachableError` (13 and 14 of 14; one megakino_to domain check timed out as a whole) instead of an empty result or a timeout, and the health monitor keeps them out of the next searches until a recheck finds the site.
- **Without episode information:** none; the 33 results without an episode in the title are decided by their link labels.
- **Failed:** kinoking 3 timeouts, megakino 1, megakino_to 13 unreachable and 1 timeout, movie4k 14 unreachable; in the rerun of the four plugins none.

### Parser follow-ups

- **megakino** (dropped 3, all correct): "Stranger Things - 5 Stafffel" for S04E01 and "One Piece - 2 Staffel" for S01E01 are pages of another season, "Severance" for S02E05 carries only the label `1x5 Voe`. The labels show a parser limit: `_add_episode` labels each episode from the page's `<select id="ep<n>">` as `1x<n>`, the season fixed at 1, so a season page of season 2 or later carries season-1 labels (`1x1 Voe` on both pages above). Asked for those pages' own season (`series/tt4574334:5:1`, `series/tt0388629:2:1`, `--plugins megakino`), both pages came back and both were dropped by their labels: 0 of 2 right, a loss, not a leak. Follow-up (backlog row 28, step 22's area, `plugins/megakino.py`): label with the page's season from its title ("N Staffel", "Staffel N"; 1 without one) and the filter narrows them like hdfilme's.

**After step 28** (2026-10-07, dev container, `handoff-28` on `staging` 2ba88c5): megakino labels a page's episodes with the season its title names (`2x1 Voe` on "One Piece - 2 Staffel", `5x1 Voe` on "Stranger Things - 5 Stafffel"; 1 without one) and states season and episode in the metadata of an episode request. `series/tt4574334:5:1` and `series/tt0388629:2:1` with `--plugins megakino`: 2 results, both season_episode from the metadata, both kept and right (before: 0 of 2). Asked for another season (`series/tt4574334:4:1`, `series/tt0388629:1:1`), megakino gives no result: `_other_season` now also reads the number before the word ("5 Stafffel"), so the search skips those pages before their detail fetch instead of the filter dropping them.

## s.to season rows (task 6.1 of step 35, 2026-10-09)

Captured from the dev container (s.to refuses the Pi) with `scripts/capture_pages.py sto "One Piece" --season 2 --episode 1` and the same for *The Last of Us*: `serienstream.to` answered every page (search, series page, season page, episode page) with 200 and the real markup (`<title>One Piece Staffel 2 | SerienStream (S.to)</title>`), no challenge page. The two season pages are fixtures: `tests/fixtures/html/sto/season-one-piece-2.html.gz` and `season-the-last-of-us-2.html.gz`.

**Rows.** An episode is `tr.episode-row` (the header row is `tr.text-uppercase` and has no number); `th.episode-number-cell` holds the season-relative number, `td.episode-title-cell` holds `strong.episode-title-ger` (the German title) and `span.episode-title-eng` (the English title). There is no `td.seasonEpisodeTitle` on s.to; that is aniworld's markup, so the empty answer for that selector on 2026-10-09 was the markup, not a challenge. One Piece, season 2, episode 1 (whitespace collapsed):

```html
<tr class="episode-row " onclick="window.location='/serie/one-piece/staffel-2/episode-1'">
  <th scope="row" class="text-center fw-semibold episode-number-cell">1</th>
  <td class="fw-medium episode-title-cell">
    <strong class="d-block episode-title-ger" title="Ein Bad in Magensäure">Ein Bad in Magensäure</strong>
    <span class="text-white-50 episode-title-eng" title="Episode 062"> Episode 062 </span>
  </td>
  <td class="episode-watch-cell"> ... <img src="/storage/providers/voe.svg" alt="VOE" ...> ... </td>
  <td class="text-end episode-language-cell"> ... svg-flag-german ... svg-flag-english-german ... </td>
</tr>
```

**Answer for 6.2.** Yes on both counts. For an anime the English span carries the absolute number the way aniworld does (`Episode 062`, without aniworld's brackets): the 16 rows of One Piece season 2 run `Episode 062` to `Episode 077`, and the series page lists 24 `staffel-N` links. For a series the span carries the English title: *The Last of Us* S02E02 has `Durch das Tal` in the `strong` and `Through the Valley` in the span. So `sto.py` can share aniworld's index and locate code with its own selectors (`span.episode-title-eng` for the number or English title, `strong.episode-title-ger` for the German one). Today's `_SeriesDetailParser` reads the `strong` only and leaves `en_title` empty.

**Applied (6.2).** `sto.py` declares `locates_episodes` and locates its anime results (5070) on the shared module with the selectors above (`sto:episodes:v1:<slug>`, 7 days). An anime row has no English title, so the number must match exactly (rule 1's number-only case); a series (5000, 5080) keeps the request's numbers, since IMDb's seasons are the site's for them. The two captured season pages are the fixtures of `test_real_pages.py`.

## After, series identity (2026-10-09, dev container, `worktree-handoff-35` after 026fca2 with the `isolated_search` fix)

Task 7.1 of step 35. `xvfb-run -a poetry run python -P scripts/probes/series_episodes.py --plugins aniworld,sto series/tt0388629:1:1 series/tt0388629:5:2 series/tt0388629:22:4 series/tt9335498:2:1 series/tt9335498:4:1 series/kitsu:12:1089` (One Piece and Demon Slayer: Kimetsu no Yaiba; the `kitsu:` id is translated first, as the use case does). The probe builds each request's episode reference from the Cinemeta meta and hands it to the two plugins, which declare `locates_episodes`; `located` counts the results whose metadata names the site's page.

The first run failed every search with `TypeError 8` per plugin: `HttpxPluginBase.isolated_search`, the search runner's entry, did not take the `episode_ref` keyword the runner passes (the Playwright base did), so no httpx plugin had ever received a reference outside the unit tests, which call `search()` directly. Fixed in this step with tests on the base and through both plugins' `isolated_search`.

| plugin | results | season_episode | episode | season | none | kept | narrowed | dropped | right | leaks | located | failed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| aniworld | 6 | 6 | 0 | 0 | 0 | 6 | 0 | 0 | 6 | 0 | 6 | 0 |
| sto | 5 | 5 | 0 | 0 | 0 | 5 | 0 | 0 | 5 | 0 | 5 | 0 |
| **total** | 11 | 11 | 0 | 0 | 0 | 11 | 0 | 0 | 11 | 0 | 11 | 0 |

References:

- series/tt0388629:1:1: S1E1 absolute 1 title "I'm Luffy! The Man Who's Gonna Be King of the Pirates!" aired 1999-10-20
- series/tt0388629:5:2: S5E2 absolute 62 title 'The First Line of Defense? The Giant Whale Laboon Appears!' aired 2001-03-21
- series/tt0388629:22:4: S22E4 absolute 1088 title "Entering a New Chapter! Luffy and Sabo's Paths!" aired 2024-01-07
- series/tt9335498:2:1: S2E1 absolute 27 title 'Flame Hashira Kyojuro Rengoku' aired 2021-10-10
- series/tt9335498:4:1: S4E1 absolute 45 title "Someone's Dream" aired 2023-04-09
- series/kitsu:12:1089: S22E4 absolute 1089 title "Entering a New Chapter! Luffy and Sabo's Paths!" aired 2024-01-07

Placements:

- aniworld, series/tt0388629:1:1: 'One Piece' -> staffel-1/episode-1 by number
- sto, series/tt0388629:1:1: 'One Piece - S01E01 - Hier kommt Ruffy, der künftige König der Piraten!' -> staffel-1/episode-1 by number
- aniworld, series/tt0388629:5:2: 'One Piece' -> staffel-2/episode-1 by number
- sto, series/tt0388629:5:2: 'One Piece - S05E02 - Ein Bad in Magensäure' -> staffel-2/episode-1 by number
- aniworld, series/tt0388629:22:4: 'One Piece' -> staffel-22/episode-1 by number
- sto, series/tt0388629:22:4: 'One Piece - S22E04 - Ruffys Traum' -> staffel-21/episode-197 by number
- sto, series/tt9335498:2:1: 'Kimetsu no Yaiba - S02E01 - Flammensäule Rengoku Kyojurou' -> staffel-2/episode-1 by title
- aniworld, series/tt9335498:2:1: 'Demon Slayer: Kimetsu no Yaiba' -> staffel-2/episode-1 by title
- aniworld, series/tt9335498:4:1: 'Demon Slayer: Kimetsu no Yaiba' -> staffel-3/episode-1 by title
- aniworld, series/kitsu:12:1089: 'One Piece' -> staffel-22/episode-1 by number
- sto, series/kitsu:12:1089: 'One Piece - S22E04 - Der Beginn eines neuen Kapitels! Ruffys und Sabos Pfade!' -> staffel-22/episode-1 by number

**Reading.** 11 of the 12 answers (6 requests, 2 plugins) are located, and every located result is kept and right by the episode filter; before step 35 the plugins built the page from the request's numbers in the sites' own Staffel numbering (One Piece S5E2: the site's staffel-5, episode 2). Three cases carry the findings:

- **One Piece S22E4 on s.to is one episode off.** Cinemeta's regular list puts the entry at position 1088; the sites number the episode 1089 (Kitsu agrees: `kitsu:12:1089` carries the same title and air date). aniworld's rows show the English title, so rule 1 picks the right row among the five neighbours (staffel-22/episode-1); s.to's anime rows show `Episode 1088` in place of the English title, so the number alone decides and the row is "Ruffys Traum" (staffel-21/episode-197, the episode before). The Kitsu number places it right on both sites. Where IMDb's list lacks an episode the sites count, every later IMDb request is off by that gap on s.to; a request from the anime catalogs is not.
- **Demon Slayer S4E1 on s.to finds no row.** The site's "Kimetsu no Yaiba" has 63 rows in five Staffeln. Its staffel-3 (the Swordsmith Village arc) shows the German titles of that arc ("Jemandes Traum" is "Someone's Dream") but the English titles of the Entertainment District arc ("Sound Hashira Tengen Uzui", "Infiltrating the Entertainment District", ...), a mistake in the site's data; the rows carry no numbers. The reference's English title matches no row, so sto answers nothing for the series, as the spec wants (never the request's numbers: staffel-4/episode-1 would have been the Hashira Training arc). aniworld's staffel-3 names the episode and is placed by the title.
- **Demon Slayer S2E1** is placed by the title on both sites (Cinemeta's S2E1 is the Mugen Train arc's first episode, the sites' staffel-2/episode-1 too), and the `kitsu:` path answers the One Piece episode on both sites in 7 s with the translation.

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

- **Right episode:** 57 of 58 results (2026-10-07: 70 of 73); every result the filter let through is the right episode. **Leaks:** none, by the oracle. **Dropped:** 1, megakino's "Severance" page for S02E05: its metadata names season 1 (the title names none, step 28's rule) and its only label is `1x5 Voe`, the page the 2026-10-07 audit dropped too.
- **The narrowing of a title-named episode** touched 4 megakino results, the only ones whose metadata names the episode and whose links carry labels (hdfilme, kinoger, movie2k, streamcloud and streamkiste name no episode and are decided by their labels as before; aniworld, filmpalast, kinoking and sto carry no labels): "Haus des Geldes - Staffel 1", "The Last of Us - Staffel 1", "The Last of Us - 1 Staffel" and "Severance" for S01E05, each with one link labelled with the requested episode, so no link was removed and nothing was dropped by the new rule (before the change the same results passed as kept, with the same link). The leak class of the step 21 review (a page titled after the episode but linking the season's) did not occur in these 12 requests; its proof is `TestTitleNamedEpisodes` in `tests/unit/infrastructure/test_episode_filter.py`.
- **Episode words:** no site labelled a link with `Folge`, `Episode` or `E<n>` in this run (the labels are `1x5 dropload`, `1x1 Voe`, `1x1 Stream HD`, `1x1 vinovo.to` or empty), so the word pattern is proven by `TestEpisodeWords` only.
- **Fewer results than on 2026-10-07** (58 against 73), none lost to the filter: fireani 0 (its search timed out in all 14 searches, `fireani_timeout`; before 4), kinoking 7 (5 timeouts; before 10 with 3), hdfilme, streamcloud and streamkiste 6 each (before 7), kinoger 7 (before 9), megakino 5 (before 6), movie2k 1 (before 2), sto 10 (before 11). The probe records only results that reached the filter; a site search that gave no hit or timed out leaves none. Per request 2 to 8 results: Dark 2 (kinoger, sto), Frieren 2 (aniworld, kinoking), One Piece, Demon Slayer and Haus des Geldes 3 each, both Severance requests 8.
- **Unreachable sites:** megakino_to and movie4k in all 14 searches (`PluginUnreachableError`), as before. cineby's search API host resolves to a private address in the dev container, which the request guard refuses (`private_address_refused`, all 14 searches); whether production, which resolves through the VPN, sees the same was not measured.
- **Located:** aniworld 4 of 4 (One Piece S1E1 by number; Attack on Titan, Demon Slayer and Frieren S1E1 by title), sto 2 (One Piece and Kimetsu no Yaiba S1E1); sto's other 8 results are its non-anime series.

## Row 71: does a German episode title add streams? (2026-10-11, dev container, `handoff-71` on `staging` ad74cd3)

N21's question, measured before it is built. `PYTHONPATH=src xvfb-run -a poetry run python -P scripts/probes/series_episodes.py --plugins sto,aniworld <ids>`, read-only against the sites, 13:33 to 13:34 UTC. The ten requests are step 35's six plus Attack on Titan (`tt2560140`) and Frieren (`tt22248376`), each with S01E01 and the first episode of the last season Cinemeta lists (AoT season 4 of 4, Frieren season 2 of 2). With both sites among the plugins, the probe now compares the links of the kept results that show the episode: aniworld's `/redirect/<n>` links are followed as the registry follows them at play time (`_follow_redirects()`, three at a time), each link becomes its hoster and file id, and only counts per hoster are printed.

| request | sto | aniworld | hoster | both | sto only | aniworld only |
|---|---|---|---|---:|---:|---:|
| One Piece 1:1 | staffel-1/episode-1 by number | staffel-1/episode-1 by number | byse, filemoon, playmogo, vidmoly, voe | 0 | voe 2 | 1, 2, 3, 3, 3 |
| One Piece 5:2 | staffel-2/episode-1 by number | staffel-2/episode-1 by number | byse, filemoon, playmogo, vidmoly, voe | 0 | voe 2 | 1, 2, 3, 3, 3 |
| One Piece 22:4 | staffel-21/episode-197 by number (one off) | staffel-22/episode-1 by number | byse, filemoon, playmogo, vidmoly, voe | 0 | voe 2 | 1, 2, 3, 3, 3 |
| Demon Slayer 2:1 | staffel-2/episode-1 by title | staffel-2/episode-1 by title | byse, filemoon, playmogo, vidmoly, voe | 0 | voe 1 | 1, 2, 3, 3, 3 |
| Demon Slayer 4:1 | nothing | staffel-3/episode-1 by title | byse, filemoon, playmogo, vidmoly, voe | 0 | 0 | 1, 2, 3, 3, 3 |
| `kitsu:12:1089` | staffel-22/episode-1 by number | staffel-22/episode-1 by number | byse, filemoon, playmogo, vidmoly, voe | 0 | voe 2 | 1, 2, 3, 3, 3 |
| Attack on Titan 1:1 | nothing | staffel-1/episode-1 by title | filemoon, playmogo, vidmoly, voe | 0 | 0 | 3, 3, 3, 3 |
| Attack on Titan 4:1 | staffel-4/episode-1 by title | staffel-4/episode-1 by title | filemoon, playmogo, vidmoly, voe | 0 | voe 2 | 3, 3, 3, 3 |
| Frieren 1:1 | nothing | staffel-1/episode-1 by title | byse, filemoon, playmogo, vidmoly, voe | 0 | 0 | 1, 2, 3, 3, 3 |
| Frieren 2:1 | nothing | staffel-2/episode-1 by title | byse, playmogo, vidmoly, voe | 0 | 0 | 3, 3, 3, 3 |

(byse is `bysezejataos.com`, filemoon `filemoon.to`, playmogo `playmogo.com`, vidmoly `vidmoly.biz`, voe `voe.sx`; the "aniworld only" counts follow the hoster column's order. No link stayed unresolved.)

- **Placement:** aniworld placed all 10 episodes, 4 by number (One Piece, the kitsu id) and 6 by title. sto placed 6: 4 by number and 2 by title. One of its number placements is one off: for One Piece 22:4 it places staffel-21/episode-197, while aniworld places staffel-22/episode-1, step 35's known case. sto answered nothing for 4 requests (Demon Slayer 4:1, Attack on Titan 1:1, Frieren 1:1 and 2:1), and aniworld placed all four by title.
- **Overlap:** sto links VOE only, 1 or 2 files per episode. aniworld links 4 or 5 hosters with 3 files each on most, VOE among them. The two sites share no file: the comparison was checked in-process for One Piece 1:1. All 14 links have the same form (`/e/<id>` or `/<name>-<id>.html`, 12-character ids), and the 14 ids are distinct. sto's VOE files are therefore other uploads of the episode, not missed matches.
- **Verdict:** aniworld places every episode sto cannot place, and where both place one, sto adds no hoster, only 1 or 2 further VOE files next to aniworld's three. Under the decision rule N21 is shelved: a German episode title would let sto place the four episodes it misses, but aniworld already serves them with more hosters.
- **Limits:** ten requests across four anime, one run, the sites' state at 13:33 UTC. Only sto and aniworld were asked. The overlap compares file ids, so a re-upload of the same video under a new id counts as a different file. The one-off number placement (One Piece 22:4) is outside the question, and a German title would fix it only with N21's rule change: `locate()` takes the row with the number when that row shows no English title (s.to's anime rows) and compares English titles only, while N21 compares the German column among the neighbours too (step 35's Open). The stream builder keeps the best-ranked stream per hoster and language (`deduplicate_by_hoster()`), so sto's VOE of the episode before can take aniworld's VOE slot; that is a decision of its own for the maintainer (Mr. Fable's review, 2026-10-11). Whether sto places non-anime series that aniworld lacks was not asked: N21 is about anime episode titles.

## Row 72: how far an IMDb request numbers a long-running anime (2026-10-11, dev container, `handoff-72` on `staging` 6cebe24)

The maintainer's question of 2026-10-11 (13:54 UTC), measured before a fix. `PYTHONPATH=src poetry run python -P scripts/probes/anime_offsets.py`, read-only against Cinemeta and the Anime Kitsu addon, 14:24 UTC. The eleven series are long-runners whose Kitsu entry spans the series, so the Kitsu number counts as the sites do; each id was checked against the name the addon's meta gives. For each Kitsu episode the addon maps to an IMDb season and episode (`imdbSeason`/`imdbEpisode`), the probe asks the app's episode reference (`TitleResolver.episode_ref()`) for the absolute number an IMDb request for that episode gets from the mapped id's Cinemeta list, and subtracts the Kitsu number.

| Series | Kitsu | Episodes | Mapped | Equal | +1 | −1 | Off more | Not in Cinemeta | Season 0 | Unmapped | Not in Kitsu | First off |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| One Piece | 12 | 1410 | 1409 | 589 | 0 | 590 | 0 | 230 | 0 | 1 | 0 | 591 (S16E12, −1) |
| Detective Conan | 210 | 1585 | 1585 | 1217 | 0 | 0 | 0 | 368 | 0 | 0 | 0 | – |
| Naruto: Shippuuden | 1555 | 500 | 500 | 500 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – |
| Naruto | 11 | 220 | 220 | 220 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – |
| BLEACH | 244 | 366 | 366 | 366 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – |
| Boruto: Naruto Next Generations | 13051 | 293 | 293 | 293 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – |
| Black Clover | 13209 | 170 | 170 | 170 | 0 | 0 | 0 | 0 | 0 | 0 | 13 | – |
| Fairy Tail | 4676 | 175 | 175 | 175 | 0 | 0 | 0 | 0 | 0 | 0 | 153 | – |
| Hunter x Hunter (2011) | 6448 | 148 | 148 | 148 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – |
| Dragon Ball Super | 10879 | 131 | 131 | 131 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | – |
| Yu☆Gi☆Oh! Duel Monsters | 442 | 224 | 224 | 49 | 95 | 0 | 80 | 0 | 0 | 0 | 2 | 50 (S2E1, +1) |

(Not in Cinemeta: mapped episodes Cinemeta does not list. Season 0: mapped to a special, which has no absolute number. Not in Kitsu: regular Cinemeta entries that no Kitsu episode maps to. The causes below were read from the same metas.)

- **One Piece:** the addon maps every Kitsu episode except 590 ("History's Strongest Collaboration vs. Glutton of the Sea", 2013-04-07, the crossover special), which IMDb lists in no season. From Kitsu 591 (S16E12) on, Cinemeta's position is one below the Kitsu number: 590 episodes, up to Kitsu 1180 (S23E25, the newest episode Cinemeta lists). Row 71's S22E04 (1088 for 1089) is one of them. The 230 episodes not in Cinemeta (Kitsu 1181 to 1410, S23E26 to S23E255) are mostly the addon's entries ahead of the broadcast; it dates the first and the last 2026-09-27.
- **Detective Conan:** Kitsu 1 to 1217 (S1E1 to S34E31) are equal. The 368 episodes not in Cinemeta (Kitsu 1218 to 1585, S34E32 to S34E399) are entries ahead of the broadcast; the addon dates the first and the last 2026-10-10.
- **Yu☆Gi☆Oh! Duel Monsters:** Cinemeta lists S2E0 and S4E0 among the regular seasons. No Kitsu episode maps to them, but the position counts them: Kitsu 50 to 144 (S2E1 to S3E47) are one high, Kitsu 145 to 224 (S4E1 to S5E40) two high.
- **Black Clover and Fairy Tail:** Cinemeta lists later seasons that Kitsu keeps as entries of their own (Black Clover's season 2, 13 entries; Fairy Tail's seasons 5 to 8, 153 entries, the series of 2014 and 2018). They follow the mapped episodes and shift no number.
- **The other six** number every mapped episode as Kitsu does.
- **What it sizes:**
  - sto today: its anime rows show no English title, so `locate()` takes the row with the reference's number (`episode_index.py`, rule 1). Among these eleven series that is wrong for One Piece from S16E12 on (590 of the 1179 mapped episodes Cinemeta lists) and for Duel Monsters from S2E1 on (175 of 224), and right for the other nine. aniworld's rows show the English title, and the title picks among the rows within 5 of the number (`_NUMBER_SLACK`), so an offset of 1 or 2 leaves the right row among them.
  - The Kitsu number fix (the addon's mapping read backwards: the Kitsu episode of the request's IMDb episode) covers every mapped episode, so all 765 episodes that are off here (590 + 95 + 80). It cannot help the regular Cinemeta entries no Kitsu episode of the series maps to (Duel Monsters' S2E0 and S4E0, the later seasons of Black Clover and Fairy Tail); those keep the position.
  - A narrower candidate the table shows: a position that skips episode 0 entries would give Duel Monsters' 175 episodes their Kitsu number and change nothing else here (checked on the same metas). One Piece's −1 needs Kitsu's number: IMDb lacks the episode, so no count over Cinemeta's list can find it.
- **Limits:** eleven series, one run, the catalogs' state at 14:24 UTC. The addon's mapping counts as the truth; that the sites number as Kitsu does was checked for One Piece only (row 71: sto and aniworld show Kitsu's 1089 for S22E04). The probe compares numbers, not placements: no site was asked, and whether a plugin serves the wrong episode also depends on its rows' titles. Most anime have a Kitsu entry per season, whose number counts that season alone; they are out of scope here (a `kitsu:` request of such an entry keeps the position when its number lies more than 5 from it, `_KITSU_SLACK`).

## Row 77: the position skips episode 0 entries of regular seasons (2026-10-11, dev container, `handoff-77` on `staging` 8ad03c8)

`TitleResolver.episode_ref()` counted the position among all entries of season 1 and up. Cinemeta lists some episode 0 entries in regular seasons (Duel Monsters' S2E0 "Opening" and S4E0, the Pyramid of Light film), so every later episode got a number one or two above Kitsu's and the sites'. The position now counts the entries with season 1 and up and episode 1 and up. An episode 0 entry of a regular season has no position, as a special has none: its reference keeps the title and air date, and the absolute number only when a `kitsu:` request names it. `_KITSU_SLACK` measures against the new position. The search cache's key carries the number, so each affected episode misses the cache once after the deploy.

Row 72's probe again (`PYTHONPATH=src poetry run python -P scripts/probes/anime_offsets.py`, 2026-10-11 ~15:30 UTC): Duel Monsters' row is now 224 equal, 0 off (was 49 equal, 95 one high, 80 two high), and the other ten series give the same counts as in row 72's table.

Which anime carry such entries: of the 898 IMDb ids behind the Anime Kitsu addon's popular, top-rated and airing catalogs (1000 entries each), the Cinemeta metas of three list an episode 0 in a regular season. Before and after, the probe's comparison per Kitsu entry with HEAD's and the new position:

| Series | Kitsu | Episode 0 entries | Equal before | Equal after | Site's number of one affected episode |
|---|---|---|---:|---:|---|
| Yu☆Gi☆Oh! Duel Monsters (tt0249327) | 442 | S2E0, S4E0 | 49 of 224 (S2E1: 51, S4E1: 147) | 224 of 224 (S2E1: 50, S4E1: 145) | aniworld: S2E1 shows 50, S4E1 shows 145 (seasons 1..5 numbered 1–49, 50–97, 98–144, 145–184, 185–224); sto: rows without numbers, placed by title |
| Yu Yu Hakusho (tt0185133) | 359 | S1E0 | 0 of 112 (all one high; S1E1: 2) | 112 of 112 (S1E1: 1) | aniworld: S1E1 shows 1 (seasons numbered 1–50, 51–112); sto: no such series |
| Tsubasa Chronicle (tt0477543) | 154 | S1E0 | 0 of 26 (all one high; S1E1: 2) | 26 of 26 (S1E1: 1) | aniworld: rows without numbers, placed by title; sto: no such series |

Tsubasa: Tokyo Revelations (`kitsu:2447`, three OVAs) maps to Cinemeta's season 3 of the same IMDb id: its own Kitsu entry counts from 1 (+53 before, +52 after), the case of row 72's Black Clover and Fairy Tail entries, out of scope here.

`series_episodes` (`--plugins sto,aniworld`) for tt0249327:

- S2E1, reference 50: aniworld places staffel-2/episode-1 by number (its row shows 50, the English title "The Mystery Duelist Part 1"); sto places staffel-2/episode-1 by title (its rows show no number). Both right.
- S4E1, reference 145: sto places staffel-4/episode-1 by title. aniworld answers nothing. Its row 145 is the right one, but its English title is the dub's ("Pulse of a New Darkness" against Cinemeta's "A New Evil (Part 1 of 2)", 0.53 below the 0.6 a neighbour needs), and `locate()` trusts an exact number only for a row without a title. With the old 147 it found nothing either, so the change costs nothing here. The miss is `locate()`'s, not the position's: a follow-up candidate.

Limits: one run, the catalogs' state at the time; the scan covers the addon's three catalogs and no full list (a series outside them with such entries is not counted). The sites' count was read on aniworld only; sto lists Duel Monsters without numbers and the other two not at all.

## Row 76: an IMDb request carries the Kitsu number (2026-10-11, dev container, `handoff-76` on `staging` ac3b279)

`AnimeIdResolverPort.kitsu_episode()` gives an IMDb series request's episode its number in the one Kitsu entry that counts the series from its start, and the episode reference takes it on a `kitsu:` request's terms (within 5 of the position, else the position). The candidates are the id list's series entries without a TheTVDB season for the IMDb id (`AnimeIdLists.whole_series()`); the addon's record of each (a missing one fetched once per title, the fetch shared by concurrent requests) counts when it names the IMDb id and places its episode 1 at S1E1, and exactly one of the counting records' episodes must sit at the request's season and episode. In the list of 2026-10-11 (7956 entries with both ids), 376 IMDb ids have such entries, and one has more than one: Fairy Tail (`tt1528406`: 4676, 8203, 13658).

Row 72's probe through the app's look-up (`anime_offsets.py` now asks `kitsu_episode()` with the id list loaded first and counts the episodes it numbered; `--position` gives the numbering without it), 15:49 to 16:05 UTC:

| Series | Kitsu | Numbered | Equal, position | Equal, with the number |
|---|---|---:|---:|---:|
| One Piece | 12 | 1179 | 589 (590 one low) | 1179 |
| Detective Conan | 210 | 1217 | 1217 | 1217 |
| Naruto: Shippuuden | 1555 | 500 | 500 | 500 |
| Naruto | 11 | 220 | 220 | 220 |
| BLEACH | 244 | 366 | 366 | 366 |
| Boruto: Naruto Next Generations | 13051 | 0 | 293 | 293 |
| Black Clover | 13209 | 0 | 170 | 170 |
| Fairy Tail | 4676 | 175 | 175 | 175 |
| Hunter x Hunter (2011) | 6448 | 148 | 148 | 148 |
| Dragon Ball Super | 10879 | 131 | 131 | 131 |
| Yu☆Gi☆Oh! Duel Monsters | 442 | 0 | 224 | 224 |

(Numbered: the mapped episodes Cinemeta lists whose IMDb request got a Kitsu number. The episodes Cinemeta does not list stay out of the comparison, as in row 72: One Piece's 230, Detective Conan's 368. The other columns (episodes, mapped, not in Cinemeta, season 0, unmapped, not in Kitsu) match row 72's table.)

- **Boruto** (`tt6342474`) and **Black Clover** (`tt7441658`): the list gives their entries TheTVDB season 1, so they are no candidates; their position equals Kitsu's number already.
- **Duel Monsters:** the list names `tt0247902` for `kitsu:442`, the addon `tt0249327`, which Cinemeta uses: out of the look-up's reach. Its position is right since row 77.
- **Fairy Tail:** `--kitsu 4676,8203,13658` numbers 4676's 175 episodes and none of the later series' episodes (8203: 102 episodes, 0 numbered, +175 from S5E1 on; 13658: 51, 0 numbered, +277 from S8E1 on, as with the position alone): their episode 1 is no S1E1, so their own count from 1 is never taken.

`series_episodes` (`--plugins sto,aniworld`) for One Piece, before (the probe at ac3b279) and after:

| Request | Reference | sto before | sto after | aniworld before and after |
|---|---|---|---|---|
| S22E04 | 1088 → 1089 | staffel-21/episode-197, "Ruffys Traum": the episode before | staffel-22/episode-1, "Der Beginn eines neuen Kapitels! Ruffys und Sabos Pfade!" (Cinemeta: "Entering a New Chapter! Luffy and Sabo's Paths!") | staffel-22/episode-1 |
| S16E12 | 590 → 591 | nothing | staffel-15/episode-11, "Choppers Zorn! Die tyrannischen Experimente des Meisters!" (Cinemeta: "Chopper Enraged Master's Tyrannical Experiments") | staffel-15/episode-11 |

The metadata phase (`stremio_phase_seconds{phase="metadata"}`, read from `/metrics` around each request): the dev server with a fresh diskcache, One Piece S22E04, S22E05 and S22E06 ten seconds apart, before at ac3b279.

| Request | Before | After |
|---|---:|---|
| S22E04, nothing cached | 0.681 s | 0.696 s: the list not in memory yet, so no number; it loaded in the background |
| S22E05, the titles cached | 0.003 s | 0.319 s: the addon's One Piece record fetched; 1090 |
| S22E06, everything cached | 0.004 s | 0.005 s; 1091 |

Limits: one run each, the catalogs' and the sites' state of the day; sto's placement was read for two One Piece episodes. The first requests after a start go without the number until the list is in memory (S22E04 above). Before Mr. Fable's review, a request that came while another one fetched a record it needed went without the number too: the Fairy Tail run, its three series compared at once, numbered none of 4676's episodes. Concurrent requests now share the one fetch, and the same run numbers all 175 (16:05 UTC). Production follows the deploy.
