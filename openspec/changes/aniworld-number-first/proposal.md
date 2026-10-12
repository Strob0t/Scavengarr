## Why

Row 82 of `docs/plans/ideas-backlog.md` measured on 2026-10-11 where `locate()` puts each Cinemeta episode on aniworld's index: fourteen series and 6708 episodes, with the reference the app builds for an IMDb request (`docs/plans/series-episodes.md` → Row 82).

**Where aniworld shows absolute numbers, the number's row is the episode, and the title rules mislead.**
- **Nothing despite the number's row.** 307 episodes get nothing although the row with their number is there (N26). That row was the episode in all 69 cases checked by eye.
- **Wrong rows.** 191 of 4745 title-confirmed placements land on another row than the number's. Of the 115 checked, 114 are the wrong episode, and the number's row was right.
- **Why the titles mislead.** `token_set_ratio` scores a title whose words are a subset of another's at 1.00, so a part number does not count, and `_best` keeps the earlier row of equal scores. Detective Conan's 92 ties are all a later part placed on an earlier one.
- **The titles' one save.** Only once did a title show the number's row wrong: Detective Conan #1077, where aniworld counts one episode differently.

The maintainer's decision of 2026-10-12 06:36 UTC: the number first.

## What Changes

- `locate()` takes the row that shows the reference's absolute number, whatever its title.
- The title rules apply only when no row shows that number, or the reference carries none. These are the neighbours within 5 confirmed at 0.6, then the exact normalised English title, then the best title at 0.85. Typical cases: a site without numbers, as aniworld's Pokémon and Digimon pages, or a number the site lacks.
- The requirement "A Number-Only Row Is Located By The Number" goes, since its rule is now the general one; its scenario moves into "aniworld Locates The Episode".
- sto's anime results follow, through the same function ("sto And fireani Follow The Rule" is unchanged).

## Impact

- Affected specs: `stremio-anime-episodes`.
- Affected code: `src/scavengarr/infrastructure/plugins/episode_index.py` (`locate()`) and its tests.
- Affected docs: `docs/features/python-plugins.md` (the rules of `locate()`), `docs/plans/series-episodes.md` (the numbers before and after), `CHANGELOG.md`.
- No setting, no cache key change. Search entries that hold the old placements age out with `cache.search_ttl_seconds` (30 minutes in production), and the episode index keeps its rows, not placements.
- Known limit: a site that numbers a whole series shifted against Kitsu's count (none among the fourteen) would be placed by the number, without a title's check.
