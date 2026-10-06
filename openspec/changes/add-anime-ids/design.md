## Context

Scavengarr's Stremio pipeline starts from an IMDb or TMDB id: the title client gives title and year per plugin language, the plugins search by title with season and episode, the title matcher filters the hits. Anime catalogs in Stremio hand out `kitsu:` ids with Kitsu's numbering. This change translates such a request into the pipeline's own request and otherwise leaves the pipeline alone.

What the sources offer (to be confirmed by the spike, task 1.1):

- **Kitsu API** (`https://kitsu.io/api/edge`, JSON:API, no key): `GET /anime/<id>?include=mappings` gives `canonicalTitle`, `titles` (`en`, `en_jp`, `ja_jp`), `startDate`, `subtype` (`TV`, `movie`, `OVA`, `ONA`, `special`), `episodeCount`, and `mappings` with `externalSite` values such as `thetvdb/series`, `thetvdb/season`, `myanimelist/anime`, `anilist/anime`, `anidb` and their `externalId`. `GET /anime/<id>/episodes?filter[number]=<n>` gives the episode with `seasonNumber`, `number` and `relativeNumber`, which is how Kitsu places an episode of a one-entry long-runner in TheTVDB's seasons.
- **TMDB** `/find/{tvdb id}?external_source=tvdb_id` gives the TMDB series and movie for a TheTVDB id, from which the existing path gets the German title and the IMDb id (`external_ids`).
- **anime-lists** (Fribb, one JSON with kitsu, mal, anilist, anidb, thetvdb, themoviedb and imdb ids) is the fallback mapping table and the way to `mal:` and `anilist:` later; it is not needed for `kitsu:` when Kitsu's own mappings suffice.

## Goals / Non-Goals

- Goals: a `kitsu:` title opened in Stremio gets the same streams it gets from Cinemeta's `tt` id; one-entry long-runners land on the sites' season and episode; no change for the other id prefixes; bounded requests to Kitsu (cached 30 days).
- Non-Goals: an anime catalog of our own; `mal:`/`anilist:`/`anidb:` prefixes in this change (follow-up through the same port); scraping Kitsu's website; absolute-numbering support on the plugin side (the sites are season-based).

## Decisions

- **Decision: translate in front of the pipeline, not inside the plugins.** The router parses `kitsu:…` into a `StremioStreamRequest` whose id is still `kitsu:<id>`; the use case's first step asks `AnimeIdResolverPort.translate(request)` and continues with the translated request (`tt…` or `tmdb:…`, season, episode). Plugins, title matcher, cache and links stay unchanged. Alternative considered: teaching the anime plugins Kitsu ids and absolute numbers — four plugins, every new one again, and the non-anime plugins (s.to lists anime too) would stay blind.
- **Decision: Kitsu's mappings first, TMDB's `/find` second, titles as the last resort.** Order per request: (1) Kitsu entry (cached) → TheTVDB id and, for `thetvdb/season` mappings, the season; (2) TMDB `/find` by TheTVDB id → TMDB id, IMDb id, German title (the existing title path); (3) no mapping or no TMDB key: the request carries Kitsu's `en_jp` and `en` titles and `startDate`'s year as `TitleInfo` objects for every plugin language, season from the mapping or 1, episode from Kitsu. Alternative considered: anime-lists as the only source — one more download to keep fresh, and it has no season or episode placement.
- **Decision: episode placement by Kitsu's episode record.** For `kitsu:<id>:<n>` the episode's `seasonNumber` and `relativeNumber` (when present) give the sites' season and episode; without them, season from the `thetvdb/season` mapping (or 1) and episode `n`. Movies (`subtype` `movie`) carry no season or episode.
- **Decision: the cache key is the translated request.** `search_cache_key` works on the translated id, so Kitsu and Cinemeta requests for one title share the search cache and the links.
- **Decision: content type from Kitsu, not from the route.** The Anime Kitsu addon lists everything under Stremio's type `series` except movies; the translation sets the internal content type from `subtype` (`movie` → movie, else series) so the category sent to the plugins is right.

## Risks / Trade-offs

- Season split differs between TheTVDB and the German sites for some titles (split cours as separate seasons, recap seasons) → the spike measures ten titles; the title matcher filters wrong seasons as today; log `anime_id_translated` with the chosen season so mismatches show in the logs (no titles in the event: Kitsu id, season, episode, source of the season).
- Kitsu API outages → cached entries live 30 days; on a miss with Kitsu down the request answers with no streams and logs `anime_id_lookup_failed` (no retry storm: one attempt, 5 s timeout).
- Kitsu rate limits are undocumented → at most two requests per new entry and episode, all cached; no background fan-out.
- TMDB-less deployments (IMDb fallback) → path (3) only: Kitsu titles, German titles unavailable; aniworld and fireani list romaji titles, so the hit rate is expected to hold (the spike measures it with the TMDB key removed).

## Migration Plan

No data migration. Deploy, then open an anime from the Anime Kitsu addon in Stremio and check `anime_id_translated` in the log. Rollback: the manifest without `kitsu` (Stremio stops asking) — a config switch is not needed, the feature is additive.

## Open Questions

- The exact `externalId` format of Kitsu's `thetvdb/season` mapping (`<series id>/<season>` is expected): task 1.1.
- Whether Kitsu's episode records carry `seasonNumber`/`relativeNumber` for the long-runners that matter (One Piece, Detective Conan, Naruto): task 1.1.
- Whether `mal:` ids appear in the maintainer's catalogs at all (if the MyAnimeList addon emits `kitsu:` ids, as the Anime Kitsu meta suggests, the follow-up is unnecessary).
