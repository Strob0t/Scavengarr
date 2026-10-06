## 1. Spike (before any production code)

- [x] 1.1 (done 2026-10-06: [anime-ids-spike.md](../../../docs/plans/anime-ids-spike.md); Kitsu's mappings place no season and no deployment has a TMDB key, the Anime Kitsu addon's meta places all ten; the source revision waits for the maintainer) `scripts/probes/anime_ids.py` (read-only, home network): for ten titles — a one-entry long-runner (One Piece), a split-cour series (Attack on Titan), a current seasonal series (Frieren), a movie (Your Name), a two-season series with recaps, an OVA, a title without TheTVDB mapping, two aniworld staples, one fireani staple — fetch the Kitsu entry with mappings and one episode record, then TMDB `/find` by TheTVDB id, and print per title: mapping sites present, `thetvdb/season` format, episode `seasonNumber`/`relativeNumber`, TMDB/IMDb id found, German title, and whether aniworld's search finds the title by the Kitsu `en_jp` title. Record the table in `docs/plans/anime-ids-spike.md`. Decide from it whether path (3) (titles only) is needed at all and whether the season heuristic needs a change.

## 2. Port and client

- [ ] 2.1 `domain/ports/anime_ids.py`: `AnimeIdResolverPort.translate(request) -> AnimeTranslation | None` with `imdb_id`/`tmdb_id`, `content_type`, `season`, `episode`, `titles` (romaji, English), `year`, `season_source` (`episode_record` | `tvdb_mapping` | `default`); `NO_ANIME_IDS` null object (tests first).
- [ ] 2.2 `infrastructure/anime/kitsu.py`: `KitsuClient` (shared httpx client, 5 s timeout, one attempt): `anime(id)` and `episode(id, number)` from Kitsu's JSON:API, cached 30 days through `CachePort` under `kitsu:v1:…`; respx tests with recorded JSON fixtures (`tests/fixtures/json/kitsu/`).
- [ ] 2.3 `TmdbClientPort.find_by_tvdb_id(tvdb_id) -> tuple[int, str] | None` (TMDB id and media type) in the TMDB client (cached 30 days like external ids); the IMDb fallback client returns `None`. Tests with respx.
- [ ] 2.4 `infrastructure/anime/resolver.py`: `KitsuAnimeIdResolver(AnimeIdResolverPort)` implementing the three paths of `design.md` and the episode placement; unit tests per path (mapping with season, mapping without season, long-runner episode record, movie, no mapping, Kitsu down).

## 3. Pipeline

- [ ] 3.1 Router: `idPrefixes` += `kitsu`; `_parse_stream_id` accepts `kitsu:<id>` and `kitsu:<id>:<episode>` (series type from the route, content type decided later by the translation); tests.
- [ ] 3.2 Use case: translation step before title resolution (in `_answer`, or in the title-resolution module once `docs/plans/stremio-stream-split.md` is done); path (3) injects the Kitsu titles as `TitleInfo` for every plugin language; `search_cache_key` uses the translated request; `anime_id_translated` / `anime_id_lookup_failed` log events (ids and numbers only); tests with a fake port (`AsyncMock`).
- [ ] 3.3 `composition.py`: wire `KitsuClient` and the resolver (cache backend, TMDB client); `/api/v1/stremio/health` reports `anime_ids: true`.
- [ ] 3.4 E2E test: a `kitsu:` series request answers with streams from a fake plugin through the translated `tt` id; a `kitsu:` movie request sends category 2000.

## 4. Verification and docs

- [ ] 4.1 Live check from the home network (`scripts/stremio_measure.py` or a probe): the ten spike titles as `kitsu:` requests, streams per title compared with their `tt` requests; record in `docs/plans/anime-ids-spike.md`.
- [ ] 4.2 `docs/features/stremio-addon.md`: section "Anime ids" (what is accepted, how the translation works, the log events, limits); `docs/features/configuration.md` if a switch is added (none planned); `CHANGELOG.md`.
- [ ] 4.3 Follow-up noted in `docs/plans/ideas-backlog.md`: `mal:`/`anilist:` through anime-lists if the maintainer's catalogs emit them.
