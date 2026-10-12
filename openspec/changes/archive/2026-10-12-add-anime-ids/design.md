## Context

Scavengarr's Stremio pipeline starts from an IMDb or TMDB id: the title client gives title and year per plugin language, the plugins search by title with season and episode, the title matcher filters the hits. Anime catalogs in Stremio hand out `kitsu:` ids with Kitsu's numbering. This change translates such a request into the pipeline's own request and otherwise leaves the pipeline alone.

What the sources offer (measured by the spike, `docs/plans/anime-ids-spike.md`, ten titles):

- **The Anime Kitsu addon** (`https://anime-kitsu.strem.fun`, the addon whose catalogs hand out the ids): `GET /meta/<type>/kitsu:<id>.json` gives `meta.imdb_id`, `meta.type` (`movie` or `series`) and `meta.videos`, one per episode with `episode` (Kitsu's number), `season` (Kitsu's, usually 1), `imdbSeason` and `imdbEpisode` (how IMDb and Cinemeta count it). All ten spike titles mapped, One Piece episode 1000 to S21E109, a split cour's episode 3 to S3E15, an ONA to the series' S0E1. The answer holds every episode of the title (One Piece: 1,410 videos, about 400 KB). Cloudflare answers 403 to httpx's default User-Agent and 200 to Scavengarr's (`http_user_agent`, with the contact URL).
- **Fribb's anime-lists** (`anime-list-full.json` on GitHub, about 7.5 MB, refreshed by its maintainer): one record per AniDB entry with `kitsu_id`, `imdb_id` (a list), `type` (`TV`, `MOVIE`, `OVA`, `ONA`, `SPECIAL`), `season` (`{"tvdb": 3, "tmdb": 3}`) and `episode_offset` (`{"tvdb": 12}`): a split cour's episode 3 is TheTVDB's S3E15. Long-runners carry no season (absolute numbering), 22,353 records have a Kitsu id, 8,243 an IMDb id.
- **Kitsu's own API**: its mappings name TheTVDB for two of ten entries (series ids, never a season) and every episode record says season 1 without a relative number; it cannot place an episode. Not used.
- **TMDB `/find` by TheTVDB id**: needs a TMDB key; neither the dev environment nor production has one (production resolves titles through the IMDb fallback client). Not used.

## Goals / Non-Goals

- Goals: a `kitsu:` title opened in Stremio gets the same streams it gets from Cinemeta's `tt` id; one-entry long-runners land on the sites' season and episode; no change for the other id prefixes; bounded requests to the addon (cached 30 days) and to the list (cached 7 days).
- Non-Goals: an anime catalog of our own; `mal:`/`anilist:`/`anidb:` prefixes in this change (follow-up through the same port and list); Kitsu's API; TMDB lookups by TheTVDB id; a titles-only search path (the spike showed the sites find titles through the IMDb title, not Kitsu's romaji one); absolute-numbering support on the plugin side (the sites are season-based).

## Decisions

- **Decision: translate in front of the pipeline, not inside the plugins.** The router parses `kitsu:…` into a `StremioStreamRequest` whose id is still `kitsu:<id>` (episode set, season `None`); the use case's first step asks `AnimeIdResolverPort.translate(request)` and continues with the translated `tt` request. Plugins, title matcher, cache and links stay unchanged. Alternative considered: teaching the anime plugins Kitsu ids and absolute numbers — four plugins, every new one again, and the non-anime plugins (s.to lists anime too) would stay blind.
- **Decision: the addon's meta first, the list second, nothing third.** Per request: (1) the addon's record for the title (cached): its IMDb id and the episode's `imdbSeason`/`imdbEpisode`; a record without IMDb numbering for the episode uses the video's own season and episode (first seasons are right that way); (2) the addon unreachable, its answer without an IMDb id, or the episode unknown to a fresh record: the list's record for the Kitsu id — IMDb id, `season.tvdb` (1 when absent) and episode plus `episode_offset.tvdb`; (3) neither: no streams, `anime_id_lookup_failed`. Alternatives considered: Kitsu's mappings and TMDB (place nothing, see Context); the list alone (no long-runners, the most-watched anime among them); the addon alone (the maintainer wanted the fallback for the addon's outages).
- **Decision: an episode newer than the cached record refetches once.** An ongoing series gains episodes after its record was cached; a requested episode missing from the cached record fetches the record again (bypassing the cache, once per request) before the list is asked. Movies and series without an episode skip this.
- **Decision: the cache key is the translated request.** `search_cache_key` works on the translated id, season and episode, so Kitsu and Cinemeta requests for one episode share the search cache and the links.
- **Decision: content type from the source, not from the route.** The Anime Kitsu addon lists everything under Stremio's type `series` except movies; the translation sets the internal content type from the addon's `meta.type` or the list's `type` (`MOVIE` → movie, else series) so the category sent to the plugins is right. The addon is asked with the route's type (its meta ids are per type).
- **Decision: the list is loaded lazily and reduced.** The 7.5 MB file is fetched on the first fallback, parsed in a worker thread and reduced to the records with a Kitsu id (IMDb id, type, season, offset), held in memory and cached 7 days in the cache backend (`anime_ids:lists:v1`), so a restart does not download it again. A failed download is logged (`anime_id_lists_failed`) and retried after an hour at the earliest; meanwhile the fallback answers nothing. No background refresh task: the first fallback after the cache entry expired reloads it.
- **Decision: records in the cache, not raw answers.** The addon's record is reduced to `{imdb_id, type, episodes: {kitsu number: [season, episode, imdb-mapped?]}}` before caching (One Piece: about 30 KB instead of 400 KB); the key is `anime_ids:addon:v1:<type>:<kitsu id>`.

## Risks / Trade-offs

- Season split differs between TheTVDB/IMDb and the German sites for some titles (split cours as separate seasons, recap seasons) → the title matcher filters wrong seasons as today; `anime_id_translated` logs the chosen placement (no titles in the event: Kitsu id, episode, IMDb id, season, episode, source of the placement).
- Addon outages → cached records answer for 30 days; on a miss the list places what it can (no long-runners); otherwise the request answers with no streams and logs `anime_id_lookup_failed` (no retry storm: one attempt, 5 s timeout).
- The addon's rate limits are undocumented → at most one request per title (and one refetch per newer episode), all cached; no background fan-out.
- The list is 7.5 MB → fetched only when the fallback is needed, at most once per 7 days per deployment (the cache backend keeps it across restarts), 60 s timeout.
- The addon's User-Agent policy → the shared httpx client sends `http_user_agent` (Scavengarr's, with the contact URL, by default); a bare or browser-like User-Agent may be refused.

## Migration Plan

No data migration. Deploy, then open an anime from the Anime Kitsu addon in Stremio and check `anime_id_translated` in the log. Rollback: the manifest without `kitsu:` (Stremio stops asking) — a config switch is not needed, the feature is additive.

## Open Questions

- Whether `mal:` ids appear in the maintainer's catalogs at all (if the MyAnimeList addon emits `kitsu:` ids, as the Anime Kitsu meta suggests, the follow-up is unnecessary).
