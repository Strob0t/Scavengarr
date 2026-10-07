# Change: Add Anime Ids (`kitsu:` Stream Requests)

Revised 2026-10-07 after the spike (`docs/plans/anime-ids-spike.md`): the sources changed, the pipeline step did not. The maintainer chose "the Anime Kitsu addon's meta, the public anime id list as the fallback" over the approved order (Kitsu's own mappings, then TMDB `/find`), which placed no season in the spike.

## Why

Anime is on the maintainer's focus list and four plugins serve it (aniworld, fireani, animeloads, haschcon), but the Stremio manifest accepts `tt` and `tmdb:` ids only. The anime catalogs people use in Stremio (the Anime Kitsu addon and the list addons built on it: MyAnimeList, AniList, Anime Catalogs) open a title as `kitsu:<anime id>` (movies) or `kitsu:<anime id>:<episode>` (series), so Stremio never asks Scavengarr for such a title: no streams, although the sites have them. Kitsu entries are one season or cour each, episodes count within the entry, and long-runners (One Piece) are one entry with absolute numbers, while the German sites list seasons and episodes as IMDb and TheTVDB count them. Reaching them needs an id and numbering translation in front of the pipeline that exists.

## What Changes

- **Manifest**: `idPrefixes` gains `kitsu:`; the stream route parses `kitsu:<id>` and `kitsu:<id>:<episode>`.
- **Anime id translation** (new infrastructure adapters behind a new application port): a `kitsu:` request becomes the `tt` request the pipeline knows (IMDb id, content type, season, episode as IMDb counts them). Source: the Anime Kitsu addon's meta for the title (its IMDb id and, per episode, `imdbSeason`/`imdbEpisode`: the numbering Cinemeta, and so the sites, use; it placed all ten spike titles, long-runners included). Fallback when the addon does not answer or does not map the entry: Fribb's anime-lists (one public JSON: Kitsu id to IMDb id, TheTVDB season and episode offset; it placed six of nine spike series, no long-runners). Neither source maps the entry: no streams, logged.
- **Caching**: the addon's meta is cached 30 days per title (one request per title; one more for an episode newer than the cached record), the reduced id list 7 days in the cache backend, as TMDB's external ids are.
- **Search cache key** includes the translated id, so a title opened from Cinemeta and from Kitsu shares one cache entry when the translation lands on the same IMDb id, season and episode.
- Nothing changes for `tt` and `tmdb:` requests, catalogs, `bingeGroup`, `/play` or the HLS proxy. `mal:` and `anilist:` are a documented follow-up (the same list carries them, no new design).

## Impact

- Affected specs: new capability `stremio-anime-ids`.
- Affected code:
  - `src/scavengarr/interfaces/api/stremio/router.py`: `idPrefixes`, `_parse_stream_id` for `kitsu:`, `anime_ids_configured` in the health answer.
  - `src/scavengarr/domain/ports/anime_ids.py` (new): `AnimeIdResolverPort.translate(request)` and the null object `NO_ANIME_IDS`.
  - `src/scavengarr/infrastructure/anime/` (new): `kitsu_addon.py` (the addon's meta, httpx, cached through `CachePort`), `id_lists.py` (the reduced anime-lists, cached), `resolver.py` (`KitsuAnimeIdResolver`: addon first, list second).
  - `src/scavengarr/application/use_cases/stremio_stream.py`: the translation as the first step of `_answer`, before plugin selection and title resolution.
  - `src/scavengarr/interfaces/composition.py`, `interfaces/app_state.py`: wiring; `docs/features/stremio-addon.md`: a section "Anime ids"; `CHANGELOG.md`.
- Dependencies: none new (httpx, the cache backend).
- Risk: the addon is a community service (Cloudflare in front; it refuses httpx's default User-Agent and accepts Scavengarr's): while it is down, titles not in the cache fall back to the list, which cannot place long-runners. The German sites' season split differs from TheTVDB's for some titles (split cours, recap seasons): the title matcher's filtering keeps wrong seasons out of the answer as it does for `tt` requests today, and `anime_id_translated` logs the chosen season.
