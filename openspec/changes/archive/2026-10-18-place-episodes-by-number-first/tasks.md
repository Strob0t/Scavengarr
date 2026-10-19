## 1. Tests first (`tests/unit/infrastructure/`, the episode index's tests)

- [x] 1.1 The number's row wins against a neighbour whose title matches better: the second part of a two-part case, whose title is a word subset of the first part's (1.00), lands on its own number's row, not the earlier one.
- [x] 1.2 The number's row wins against a far title match, rules 2 and 3 not consulted.
- [x] 1.3 A row that shows the number in place of a title is located by the number, as before.
- [x] 1.4 Without a row that shows the number, the neighbour rule (within 5, at least 0.6), the exact title and the best title (at least 0.85) place the episode, as before; without any match nothing is located.
- [x] 1.5 A reference without an absolute number uses the title rules, as before.
- [x] 1.6 The `aniworld_episode_located` line names the rule that placed the episode.

## 2. Implementation

- [x] 2.1 `locate()` in `src/scavengarr/infrastructure/plugins/episode_index.py`: the row with the reference's absolute number first, whatever its title; the title rules only without it or without a number.

## 3. Live proof (dev container)

- [x] 3.1 Row 82's probe again (`PYTHONPATH=src poetry run python -P scripts/probes/anime_offsets.py --aniworld`, the same fourteen series and `--slug` pages):
  - N26's case and the placements at another row go to 0 for the series with numbers;
  - Anime K's and Anime L's title placements stay as they were;
  - the table before and after in the archived plan series-episodes → Row 82.
- [x] 3.2 sto's long-runners through `scripts/probes/series_episodes.py --plugins sto` show no regression against their last run.

## 4. Docs

- [x] 4.1 `docs/current/plugins.md`: the rules of `locate()`, and the reference's absolute number, which the Kitsu number now gives for IMDb anime requests too (rows 76 and 77).
- [x] 4.2 `CHANGELOG.md` under `## Unreleased (staging)`.

## 5. The wrong-series leak and the parent series (row 92)

- [x] 5.1 Tests: the partial test (one domain function). "Anime C" and sto's "Anime C - S01E06 - …" are partial for "Anime C: Sequel"; "Anime C: Sequel" is not; a site title that holds a request title whole is not.
- [x] 5.2 Tests: a plugin's partial matches are dropped beside its full match and kept without one; movie results are untouched.
- [x] 5.3 Tests: on a partial series `locate()` uses the title rules only (no number, no neighbour); without a title match it locates nothing.
- [x] 5.4 Implementation: the domain function, `EpisodeRef`'s series titles and their builder, the matcher and the search progress, `locate()`; the search cache key's change, if any, named in the CHANGELOG.
- [x] 5.5 Live proof, before and after:
  - the four Anime C: Sequel requests (S1E6, S2E9, S3E1, S9E10) answer no Anime C result through aniworld and sto;
  - Anime E: Sequel's four references (S1E1, S1E13, S2E1, S3E5): sto 4 of 4 right, aniworld 2 of 4 right, no wrong placement (measured 2026-10-12: aniworld 4 of 4 right, by title on Anime E's season 17);
  - `anime_offsets --aniworld` for row 82's fourteen series unchanged against row 87's after-table;
  - `series_episodes --plugins sto` unchanged apart from the leak's results (then 3.2).
- [x] 5.6 Docs: the title match in `docs/features/`, the archived plan series-episodes, `CHANGELOG.md`.
