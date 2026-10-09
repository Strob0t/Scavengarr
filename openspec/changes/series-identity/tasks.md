# Tasks: series-identity

TDD throughout (AGENTS.md §6); each numbered group is one commit with its
tests and docs. Mock patterns: `CinemetaClient` with `respx`; the matcher
and relevance tests are pure; the use case and search tests use `AsyncMock`
for the async ports and `MagicMock` for `PluginRegistryPort`; plugin tests
use captured pages (`scripts/capture_pages.py`, `tests/unit/infrastructure/
test_real_pages.py`); `TelemetryPort` → `NO_TELEMETRY`. Order: 1 → 2 → 3 →
4 → 5 → 6 → 7; groups 3 and 5 are the two behaviour changes the maintainer
sees. Worktree `handoff-35` from `origin/staging`.

## 1. Series meta and the entities

- [ ] 1.1 `SeriesMeta(name, year, genres, episodes)` and `EpisodeMeta(season, episode, name, released)` (frozen, `domain/entities/stremio.py`), and `EpisodeRef(season, episode, title, aired, absolute)` with a docstring naming the sources of each field.
- [ ] 1.2 `CinemetaClient` (`infrastructure/stremio/cinemeta.py`): `meta(content_type, imdb_id) -> SeriesMeta | None`; GET `https://v3-cinemeta.strem.io/meta/<type>/<imdb>.json` with the default User-Agent and a 5 s timeout; the reduced record cached for 7 days under `cinemeta:v1:<type>:<imdb>`; a 404 caches "none" for a day; an HTTP error logs `cinemeta_failed` (reason, no URL) and returns `None`. respx tests: series with videos, movie without, 404, timeout, cache hit without a request.
- [ ] 1.3 `TitleMatchInfo.imdb_id: str | None = None` and `animation: bool | None = None` with docstring lines.

## 2. Title resolution

- [ ] 2.1 `TitleResolver` takes the `CinemetaClient`; `_title_info` sets `imdb_id` from the request and `animation` from the meta's genres (`"Animation" in genres`; `None` without a meta); one meta request per request shared by the languages. The lookup runs as `telemetry.stage("stremio_phase", phase="series_meta")` with the outcome `found`, `not_found` or `error`. Tests: animation set, not set, unknown; the stage outcome.
- [ ] 2.2 `TitleResolver.episode_ref(request, meta, *, absolute=None) -> EpisodeRef | None`: `title` and `aired` from the list entry of the request's season and episode; `absolute` as given, else the entry's position among the regular seasons (season ≥ 1) ordered by season and episode; `None` without a meta or for a request without an episode. Tests with the One Piece shape (S5E2 → 62) and a given absolute.
- [ ] 2.3 Remove `title_year_penalty` from `StremioConfig`, `title_resolution.py`, `docs/features/configuration.md`, `tests/benchmark/conftest.py`; a loader test proves a YAML that still sets `stremio.title_year_penalty` loads.

## 3. The matcher decides by identity

- [ ] 3.1 `_extract_result_year` falls back to `metadata["year"]` (int, or a string of digits; `None` otherwise). Tests: int, string, garbage, absent.
- [ ] 3.2 `score_title_match`: a known result year outside the tolerance gives 0.0; within it the bonus as today; the `year_penalty` parameter goes. Tests: the exact title with a wrong year (series, 1999 against 2023) scores 0; "Dune (1984)" against Dune 2021 scores 0 (the existing test asserts `< 1.0`, make it exact); within tolerance unchanged.
- [ ] 3.3 IMDb id: `metadata["imdb"]` or `["imdb_id"]` normalised to `tt` plus digits; equal to `reference.imdb_id` → 1.2, different → 0.0, else the text rules. Tests for the three cases and a malformed id.
- [ ] 3.4 Category: reference `animation is False` and `result.category == 5070` → 0.0; `animation is True`, category 5000 and non-empty `metadata["genres"]` → 0.0; `animation is None` → ignored; a 5000 result without genres → text and year rules. Tests for each line.
- [ ] 3.5 `filter_by_title_match` logs `title_match_filtered` with `reason` (`score`, `year`, `imdb`, `category`): `score_title_match` returns the reason next to the score (a small frozen `TitleScore(score, reason)`, or a second function the filter calls); `title_match_summary` counts the drops per reason. Tests on the log output (`structlog.testing.capture_logs`).
- [ ] 3.6 `relevant_hits`: a trailing `(YYYY)` in a hit's title is stripped before `query_words`; "One Piece (2023)" next to "One Piece" are both exact hits and both kept. Tests in `test_plugin_relevance.py`; `sto.py` keeps the suffix in the result title (test on a captured search page with both One Piece entries, `scripts/capture_pages.py`).
- [ ] 3.7 `scripts/probes/title_match.py` prints the reference's identity (`imdb_id`, `animation`) and each dropped result's reason.

## 4. The episode reference reaches the plugins

- [ ] 4.1 The use case builds the `EpisodeRef` after the title lookup: `absolute` is the Kitsu number for a request that came as `kitsu:` (kept from before the translation), else `None`; the reference travels through `TitleSearch.progress` and `PluginSearch` next to `season` and `episode`. A `stremio_episode_ref` debug log (season, episode, absolute, has_title).
- [ ] 4.2 `_dispatch_search` passes `episode_ref=` only to a plugin with `locates_episodes = True` (`getattr`, default `False`); `isolated_search` of the Playwright base forwards it the same way. `PluginProtocol`'s docstring documents the optional capability. Tests: a plugin without the attribute is called as today; one with it receives the reference.
- [ ] 4.3 `search_cache_key` becomes `stremio:search:v2:...`; the e2e search-cache test reads the new prefix.

## 5. aniworld locates the episode

- [ ] 5.1 `_EpisodeIndex` in `plugins/aniworld.py`: the series page's season links (`/staffel-N`), each season page's rows (`td.seasonEpisodeTitle`: href, German title, English title, `[Episode NNN]` when present), fetched under the detail-page semaphore and cached 7 days under `aniworld:episodes:v1:<slug>`. Tests on captured season pages: One Piece staffel-2 (16 rows, numbers 62–77), Demon Slayer staffel-3 (11 rows, no numbers).
- [ ] 5.2 Locate rules 1 to 4 of the design (`_locate(index, ref)`): the numbered row and its neighbours within 5 confirmed by title ≥ 0.6, the exact normalised title, the best fuzzy title ≥ 0.85, else `None`. Tests: One Piece S5E2 with absolute 62 → staffel-2/episode-1; S22E4 with absolute 1088 and the title → staffel-22/episode-1 (number off by one, title confirms); S1E1 with the variant title → episode-1 by number; Demon Slayer S4E1 "Someone's Dream" → staffel-3/episode-1 by exact title; an unknown title → `None`.
- [ ] 5.3 `search` with a reference fetches the located page, sets `metadata["season"]`/`["episode"]` to the request's, `site_season`, `site_episode`, `episode_located_by`, logs `aniworld_episode_located`; without a row it logs `aniworld_episode_not_located` and returns no result for that series; without a reference it builds the URL from the numbers as today. `locates_episodes = True`. Tests for the three paths; the e2e series test `test_high_season_high_episode` gains a located case with fake pages.

## 6. sto and fireani

- [ ] 6.1 Check s.to's season page rows for an anime from the dev container (`scripts/capture_pages.py`; s.to refuses the Pi): absolute numbers, English titles. Record the finding in `docs/plans/series-episodes.md`.
- [ ] 6.2 If the rows carry the number or the English title: `sto.py` declares `locates_episodes`, shares the index and locate code with aniworld (one module in `infrastructure/plugins/`, the two plugins pass their selectors), and applies it to its 5070 results only; tests as in 5.2 on captured pages. If not: nothing, and the gap stays documented.
- [ ] 6.3 fireani: when the site answers again (`scripts/probes/domains.py`), read `GetAnime`'s `animeSeasons[].animeEpisodes[]` fields, implement the same rules on them, `locates_episodes = True`, tests on a captured answer. Until then this task stays open and `fireani.py` is unchanged.

## 7. Docs, probes and acceptance

- [ ] 7.1 `scripts/probes/series_episodes.py` prints the located page (`site_season`, `site_episode`, `episode_located_by`) per result; run for One Piece S1E1, S5E2, S22E4 and Demon Slayer S2E1, S4E1 against aniworld (and sto if 6.2 applied); the table goes to `docs/plans/series-episodes.md` under "After, series identity".
- [ ] 7.2 The title probe over the series set of `docs/plans/title-matching.md` plus `series/tt0388629:1:1`, `series/tt11737520:1:1` and `series/tt30476502:1:1` before (`origin/staging`) and after: no new wrong drop; the 1999 request keeps no 2023 result and the 2023 request keeps no 5070 result; a plugin whose stored year is not the start year is excluded from the year rule by name (3.1) with the probe's line as evidence. The tables go to `docs/plans/title-matching.md`.
- [ ] 7.3 Docs: `docs/features/stremio-addon.md` (Title Matching: the identity rules and reasons; Anime Ids: the episode reference and the located page; the request flow), `docs/features/python-plugins.md` (the `locates_episodes` capability), `docs/features/observability.md` (the `series_meta` phase), AGENTS.md §2 (flow) and §7 (anime plugins locate episodes), `CHANGELOG.md` (Unreleased: Fixed, series identity; Changed, removed setting; Added, episode placement).
