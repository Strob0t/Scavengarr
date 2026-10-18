## Why

Row 82 of `docs/plans/ideas-backlog.md` measured on 2026-10-11 where `locate()` puts each Cinemeta episode on aniworld's index: fourteen series and 6708 episodes, with the reference the app builds for an IMDb request (`docs/archive/plans/series-episodes.md` → Row 82).

**Where aniworld shows absolute numbers, the number's row is the episode, and the title rules mislead.**
- **Nothing despite the number's row.** 307 episodes get nothing although the row with their number is there (N26). That row was the episode in all 69 cases checked by eye.
- **Wrong rows.** 191 of 4745 title-confirmed placements land on another row than the number's. Of the 115 checked, 114 are the wrong episode, and the number's row was right.
- **Why the titles mislead.** `token_set_ratio` scores a title whose words are a subset of another's at 1.00, so a part number does not count, and `_best` keeps the earlier row of equal scores. Anime F's 92 ties are all a later part placed on an earlier one.
- **The titles' one save.** Only once did a title show the number's row wrong: Anime F #1077, where aniworld counts one episode differently.

The maintainer's decision: the number first.

**Row 87's live proof found where the number must not count** (Ultracode, 2026-10-12, backlog row 92).
- **The wrong-series leak.** TMDB's German title "Anime C: Sequel" makes the app query "Anime C" too. The matcher keeps the series Anime C, since a result that only drops words scores 1.00, and the number places Anime C: Sequel's episode on Anime C's index. sto does so in production today for all four sampled requests, and aniworld would with the number first (235 of 500 references offline, against 42).
- **A sequel kept as the parent's season.** Both sites hold "Anime E: Anime E: Sequel" as Anime E's season 17. sto places its four references right by title. With the number first aniworld put Anime E: Sequel's #13 on Anime E's #13: the number counts the request's series, not the parent's.

## What Changes

- `locate()` takes the row that shows the reference's absolute number, whatever its title.
- The title rules apply only when no row shows that number, or the reference carries none. These are the neighbours within 5 confirmed at 0.6, then the exact normalised English title, then the best title at 0.85. Typical cases: a site without numbers, as aniworld's Anime K and Anime L pages, or a number the site lacks.
- The requirement "A Number-Only Row Is Located By The Number" goes, since its rule is now the general one; its scenario moves into "aniworld Locates The Episode".
- sto's anime results follow, through the same function ("sto And fireani Follow The Rule" is unchanged).
- A series that matches only as a shorter form of the request's titles is partial (one domain function). A plugin's partial matches yield to its full ones; where the plugin has none, they count ("A Shorter Title Yields To The Full One").
- On a partial series the episode is located by title only ("A Parent Series Is Located By Title Only"). The request's `EpisodeRef` carries its series titles for it.

## Impact

- Affected specs: `stremio-anime-episodes`, `stremio-series-identity`.
- Affected code: `src/scavengarr/infrastructure/plugins/episode_index.py` (`locate()`) and its tests; the partial test (a domain function), the title matcher and the search progress in `src/scavengarr/application/stremio/`, `EpisodeRef` and its builder, aniworld and sto.
- Affected docs: `docs/current/plugins.md` (the rules of `locate()`), `docs/archive/plans/series-episodes.md` (the numbers before and after), `CHANGELOG.md`.
- No setting, no cache key change. Search entries that hold the old placements age out with `cache.search_ttl_seconds` (30 minutes in production), and the episode index keeps its rows, not placements.
- Known limit: a site that numbers a whole series shifted against Kitsu's count (none among the fourteen) would be placed by the number, without a title's check.
