# Change: Add Anime Ids (`kitsu:` Stream Requests)

## Why

Anime is on the maintainer's focus list and four plugins serve it (aniworld, fireani, animeloads, haschcon), but the Stremio manifest accepts `tt` and `tmdb:` ids only. The anime catalogs people use in Stremio (the Anime Kitsu addon and the list addons built on it: MyAnimeList, AniList, Anime Catalogs) open a title as `kitsu:<anime id>` (movies) or `kitsu:<anime id>:<episode>` (series), so Stremio never asks Scavengarr for such a title: no streams, although the sites have them. Kitsu entries are one season or cour each, episodes count within the entry, and long-runners (One Piece) are one entry with absolute numbers, while the German sites list seasons and episodes in TheTVDB's order. Reaching them needs an id and numbering translation in front of the pipeline that exists.

## What Changes

- **Manifest**: `idPrefixes` gains `kitsu`; the stream route parses `kitsu:<id>` and `kitsu:<id>:<episode>`.
- **Anime id translation** (new infrastructure client behind a new application port): a `kitsu:` request becomes the internal request the pipeline knows (IMDb or TMDB id, season, episode), through Kitsu's public API (titles, year, subtype, TheTVDB mappings, per-episode `seasonNumber`/`relativeNumber`) and the TMDB client's `/find` by TheTVDB id (new method). Without a usable mapping the request carries Kitsu's titles (romaji and English) as the search titles and keeps Kitsu's numbering.
- **Caching**: Kitsu answers are cached 30 days in the cache backend, like TMDB's external ids; one request to Kitsu per entry and per episode at most.
- **Search cache key** includes the translated id, so a title opened from Cinemeta and from Kitsu shares one cache entry when the translation lands on the same IMDb id.
- Nothing changes for `tt` and `tmdb:` requests, catalogs, `bingeGroup`, `/play` or the HLS proxy. `mal:` and `anilist:` are a documented follow-up (the same mapping table, no new design).

## Impact

- Affected specs: new capability `stremio-anime-ids`.
- Affected code:
  - `src/scavengarr/interfaces/api/stremio/router.py`: `idPrefixes`, `_parse_stream_id` for `kitsu:`.
  - `src/scavengarr/domain/ports/anime_ids.py` (new): `AnimeIdResolverPort` with the translation result (internal id, season, episode, titles, year).
  - `src/scavengarr/infrastructure/anime/kitsu.py` (new): Kitsu JSON:API client (httpx, shared client, cached through `CachePort`).
  - `src/scavengarr/domain/ports/tmdb.py`, `src/scavengarr/infrastructure/tmdb/`: `find_by_tvdb_id` (TMDB `/find/{id}?external_source=tvdb_id`); the IMDb fallback client returns `None` for it.
  - `src/scavengarr/application/use_cases/stremio_stream.py` (or the title-resolution module after the split in `docs/plans/stremio-stream-split.md`): the translation step before title resolution; `application/stremio/queries.py`: the cache key.
  - `src/scavengarr/interfaces/composition.py`: wiring; `docs/features/stremio-addon.md`: a section "Anime ids"; `CHANGELOG.md`.
- Dependencies: none new (httpx, the cache backend).
- Risk: the German sites' season split differs from TheTVDB's for some titles (split cours, recap seasons); the spike in `tasks.md` measures the hit rate on ten titles before the implementation is finished, and the title matcher's filtering keeps wrong seasons out of the answer as it does for `tt` requests today.
