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
