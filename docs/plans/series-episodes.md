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
