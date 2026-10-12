# Change: The right series and the right episode

## Why

The maintainer reported on 2026-10-09 that One Piece streams mix the 1999
anime, the 2023 live-action series and the 2027 remake, and that anime
requests get the wrong episode. The title probe (`scripts/probes/
title_match.py`, dev container, `staging` b8da78f) reproduces the first
report: for the anime (`series/tt0388629:1:1`) filmpalast's release
`One.Piece.2023.S01E01` is kept at 0.70 against the threshold 0.7; for the
live action (`series/tt11737520:1:1`) aniworld's and sto's anime pages are
kept at 1.00; every site answers the one query "One Piece" with one page,
and only a year or a genre could tell them apart. The trace of the pipeline
(2026-10-09) names the gaps: the reference carries title, year and alternative
titles only; the year is a penalty of 0.3, never decisive, and compared only
when the result's title string carries one (the `year` that kinoger, megakino,
streamkiste, hdfilme and others store in `metadata` is never read); no type,
genre or IMDb id reaches the matcher; a plugin's relevance filter drops
"One Piece (2023)" next to the exact hit "One Piece" before any page is
scraped, so the live-action page is never even seen.

The second report has a different cause. IMDb's season numbers, which
Cinemeta and the Stremio request use, go straight into the anime sites'
URLs: Cinemeta counts One Piece in arcs (season 1 = 8 episodes, season 2 =
22), aniworld in sagas (Staffel 1 = 61, Staffel 2 = 16); `aniworld.py`
builds `/staffel-{season}/episode-{episode}` and `fireani.py` calls
`GetEpisode(slug, season, episode)` with the request's numbers, both write
those numbers into the result's `metadata`, and the episode filter, which
compares exactly those fields, cannot see the mismatch. Live on 2026-10-09:
Cinemeta S5E2 "The First Line of Defense? The Giant Whale Laboon Appears!" is
aniworld `staffel-2/episode-1`, whose row reads "[Episode 062]"; Cinemeta
S22E4 "Entering a New Chapter! Luffy and Sabo's Paths!" is
`staffel-22/episode-1`, "[Episode 1089]"; Demon Slayer (tt9335498) S4E1
"Someone's Dream" is `staffel-3/episode-1`, because aniworld's Staffel 2
merges Cinemeta's seasons 2 and 3 (18 rows, no absolute numbers). The English
episode titles agree between Cinemeta and aniworld (TheTVDB feeds both)
except for translation variants such as One Piece S1E1, where aniworld's
absolute number decides.

## What Changes

- **Series meta from Cinemeta.** A `CinemetaClient` (`infrastructure/stremio/
  cinemeta.py`) reads `v3-cinemeta.strem.io/meta/<type>/<imdb>.json`, the
  catalog the player itself uses: name, year, genres and, for a series, the
  episode list (season, episode, English name, release date), cached for
  7 days. `TitleMatchInfo` gains `imdb_id` and `animation` (the genres name
  "Animation"; unknown without the meta).
- **The matcher decides by identity, not only by text.** A result whose year
  is known and outside the tolerance is dropped, not penalised; the result's
  year is read from `metadata["year"]` when the title and release name carry
  none; a result whose `metadata` names an IMDb id is kept or dropped by that
  id alone; a result labelled anime (5070) is dropped for a reference that is
  not animation, and a result labelled plain series (5000) with genres of its
  own is dropped for an animation reference. Each drop logs its reason.
  `stremio.title_year_penalty` is removed.
- **A year suffix is not a title word.** `relevant_hits` counts a trailing
  "(2023)" neither as an extra word nor as a mismatch, so "One Piece (2023)"
  is scraped next to "One Piece" and the matcher decides by the year.
- **Anime sites locate the episode.** The use case builds an `EpisodeRef`
  (season, episode, English title, release date, absolute number) from the
  Cinemeta list, and from the Kitsu number for a `kitsu:` request; a plugin
  that declares `locates_episodes = True` receives it. aniworld indexes the
  series' season pages once (cached 7 days) and finds the row by absolute
  number confirmed by title, else by exact English title, else by the best
  fuzzy title above 0.85; it answers nothing when it finds no row, never the
  request's numbers applied to the site's own numbering. fireani follows the
  same rule from its `GetAnime` answer once the site is back (dead since
  2026-10-08); sto does for its anime pages if its rows carry the number or
  the English title (verified first).
- **Observability.** `title_match_filtered` carries `reason`; the Cinemeta
  lookup is a `stremio_phase` stage (`phase="series_meta"`); a located or
  unlocated episode logs `<plugin>_episode_located` / `_not_located`.
- **Acceptance.** The title probe for the three One Piece ids and the series
  set of `docs/plans/title-matching.md` before and after (no new wrong drop);
  `scripts/probes/series_episodes.py` for One Piece S1E1, S5E2 and S22E4 and
  Demon Slayer S2E1 and S4E1 names the located pages.

## Impact

- Affected specs: new capabilities `stremio-series-identity` and
  `stremio-anime-episodes`.
- Affected code: `domain/entities/stremio.py` (`TitleMatchInfo`,
  `EpisodeRef`), `domain/plugins/base.py` (the `locates_episodes` capability
  in the protocol's docstring), `infrastructure/stremio/cinemeta.py` (new),
  `infrastructure/stremio/title_matcher.py`, `infrastructure/plugins/
  relevance.py`, `application/stremio/title_resolution.py`, `title_search.py`,
  `plugin_search.py`, `application/use_cases/stremio_stream.py`,
  `interfaces/composition.py`, `infrastructure/config/schema.py`,
  `plugins/aniworld.py`, `plugins/sto.py`, `plugins/fireani.py`, their
  tests, `scripts/probes/title_match.py` (prints the reason and the
  reference's identity), `scripts/probes/series_episodes.py` (prints the
  located page).
- Docs: `docs/features/stremio-addon.md` (Title Matching, Anime Ids),
  `docs/features/configuration.md` (the removed setting), `docs/features/
  python-plugins.md` (the capability), AGENTS.md §2 and §7, `CHANGELOG.md`,
  `docs/plans/title-matching.md` and `docs/plans/series-episodes.md` (the
  probe runs).
- No new configuration. One removed setting (`title_year_penalty`), ignored
  when a YAML still sets it.
