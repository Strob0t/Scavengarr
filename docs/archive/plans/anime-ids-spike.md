# Anime Ids Spike (I11, `add-anime-ids` task 1.1)

> **Status:** archived
> **Superseded by:** `docs/current/stremio-addon.md` (row 113)

Status 2026-10-07: implemented (`openspec/changes/add-anime-ids/`, all tasks done; live check of the resolver at the end of this file); spike done; the maintainer chose option 2 (the addon, the public list as the fallback; `openspec/changes/add-anime-ids/` revised). One list suffices for the fallback: Fribb's records carry `imdb_id`, `season.tvdb` and `episode_offset.tvdb` (Anime G Season 3 Part 2: season 3, offset 12), so Kometa's list is not needed.

## Setup

`scripts/probes/anime_ids.py` (read-only, no cache writes, prints no URLs or keys), run from the dev container on a residential network, twice for all ten titles and once more for the ONA. Per title it asks:

- **Kitsu's API** for the entry (subtype, titles, mappings) and the episode record (`seasonNumber`, `relativeNumber`): the sources of the approved design;
- the **public id lists**: Fribb's anime-lists (by Kitsu id: IMDb, TheTVDB and TMDB ids, seasons) and Kometa's Anime-IDs (by AniDB id: TheTVDB season and episode offset);
- the **Anime Kitsu addon's meta** (`/meta/series/kitsu:<id>.json`, the addon whose catalogs hand out the `kitsu:` ids): the IMDb id, and per episode `imdbSeason`/`imdbEpisode`, the fields Torrentio maps Kitsu requests with. It answers 403 to httpx's default User-Agent and 200 to Scavengarr's (`APP_USER_AGENT`, with the contact URL);
- **TMDB's `/find` by TheTVDB id**: not run. Neither the dev environment nor production has a TMDB key; production logs `tmdb_client_fallback` and resolves every title through the IMDb fallback client (IMDb Suggest, Wikidata);
- the **sites**: aniworld's and fireani's `search()` with the title the IMDb fallback gives for the mapped IMDb id (what a `tt` request gets today) and with Kitsu's romaji title, at the addon's season and episode.

## Results

Second run (with the addon). Site columns: search results of aniworld / fireani; a result is a title with the requested episode's links, not yet a played stream.

| Request | Case | Kitsu: TheTVDB mapping; episode record | Lists: IMDb, placement | Addon: IMDb, placement | Title (IMDb fallback) | Sites by that title | Sites by romaji |
|---|---|---|---|---|---|---|---|
| `kitsu:<Anime A>:1000` | long-runner (Anime A) | series id, no season; S1, no relative | <Anime A>, absolute (TheTVDB season `-1`) | <Anime A> S21E109 | Anime A | 1 / 1 | same title |
| `kitsu:<Anime H>:3` | split cour (Anime G Season 3 Part 2) | none; S1 | <Anime G> S3E15 (offset 12) | <Anime G> S3E15 | Anime G | 1 / 0 | 0 / 0 |
| `kitsu:<Anime I>:2` | current season (Anime I) | none; S1 | <Anime I> S2E2 (TMDB counts it as season 1) | <Anime I> S2E2 | Anime I | 0 / 1 | 0 / 1 |
| `kitsu:<Anime O>` | movie (Anime O) | none | <Anime O> | <Anime O> | Anime O – Gestern, heute und für immer | 0 / 0 | 0 / 0 |
| `kitsu:<Anime V>:2` | split second season with recaps (Anime V) | none; S1 | <Anime V> S2E15 (offset 13) | <Anime V> S2E15 | Anime V | 0 / 1 | 0 / 1 |
| `kitsu:<Anime Q>:1` | OVA (Anime Q) | none; S1, relative 1 | <Anime Q> S1E1 | <Anime Q> S1E1 | Anime Q | 1 / 1 | same title |
| `kitsu:<Anime I>:1` | ONA (Anime I) | none; S1 | not listed | <Anime I> S0E1 (a special of the series) | Anime I | 0 / 0 | 0 / 0 |
| `kitsu:<Anime H>:2` | aniworld staple (Anime H: Entertainment District Arc) | none; S1 | <Anime H> S3E2 | <Anime H> S3E2 | Anime H | 1 / 0 | 0 / 0 |
| `kitsu:<Anime F>:1000` | long-runner, aniworld staple (Anime F) | series id, no season; S1 | <Anime F>, absolute | <Anime F> S29E8 | Detektiv Conan | 1 / 1 | 1 / 0 |
| `kitsu:<Anime U>:2` | fireani staple (Anime U) | none; S1 | <Anime U> S2E2 | <Anime U> S2E2 | Anime U | 1 / 1 | 0 / 1 |

## Findings

1. **Kitsu's own data places nothing.** Its mappings carry a TheTVDB id for 2 of 10 entries (the two long-runners, series ids only) and a `thetvdb/season` mapping for none; every TV entry's episode record says season 1 without `relativeNumber` (the OVA: relative 1). The approved order (Kitsu's mappings, then TMDB's `/find`) would place none of the later or split seasons, and without a TMDB key it ends in path (3), titles only, for all ten.
2. **The addon maps all ten and places every episode** the way Cinemeta counts it: the split cours at the season's running number (Season 3 Part 2 episode 3 is S3E15), the long-runners at IMDb's seasons (Anime A episode 1000 is S21E109, Anime F episode 1000 is S29E8), the ONA as a special (S0E1). The translated request is the `tt` request Cinemeta would send for the same episode.
3. **The lists agree with the addon wherever they place** (6 of the 9 series entries), but leave the long-runners absolute and do not list the ONA. They are a 7.5 MB and a 1.7 MB JSON file on GitHub.
4. **The sites find the titles through the IMDb title, not the romaji one.** At the addon's placement, aniworld or fireani found 8 of the 9 series episodes by the IMDb fallback's title (aniworld 6, fireani 6); by Kitsu's romaji title aniworld found only the two whose romaji title is the English one (fireani 5). Path (3) is weaker than the translation in every case and is not needed while the translation maps every entry.
5. **Not found**: the movie (aniworld and fireani answer category 5070 only; movie plugins search it as for any `tt` movie) and the ONA's special (neither site lists it as S0E1).
6. **Variance**: fireani's answers changed between the two runs (Anime G and Anime H found only in the first, Anime I's second season only in the second; its episode pages answered 404 in places), and the IMDb fallback gave the English title for Anime F and Anime O in the first run, the German one in the second. Ten titles, two runs: the numbers show the direction, not rates.

## Decision

The spike falsifies the approved design's source order: Kitsu's mappings place no season, and TMDB's `/find` needs a key that no deployment here has. Options for the revision (task 1.1 asks to decide from the spike; the source is the maintainer's call):

1. **The Anime Kitsu addon's meta as the only source (recommended).** One request per title (all episodes in one answer), cached; the `kitsu:` request becomes the `tt` request with IMDb's season and episode, and everything after it (titles through TMDB or the IMDb fallback, plugins, cache key, links) is the existing path. No Kitsu API client, no TMDB `/find`, no path (3). Pro: 10 of 10 mapped in the spike, long-runners included; the numbering Cinemeta and Torrentio use; one small adapter. Con: one community service (Cloudflare in front; it refused httpx's default User-Agent); while it is down, titles not in the cache get no streams (`anime_id_lookup_failed`).
2. **The addon, with the public lists as fallback.** Pro: survives an addon outage for the entries the lists place (6 of 9 series in the spike, no long-runners). Con: a second adapter with a weekly 9 MB download and its tests, for a fallback.
3. **The public lists only, Kitsu's API for the titles of unlisted entries.** Pro: two static files instead of a service per request. Con: no long-runners (Anime A, Anime F stay absolute), 9 MB a week, path (3) stays.
4. **The approved design unchanged, with a TMDB key.** Con: places none of the ten in the spike; needs a TMDB key in production.

With option 1 the OpenSpec change shrinks: `AnimeIdResolverPort.translate(request)` returns the translated request (no titles, no `season_source`), the adapter is `infrastructure/anime/kitsu_addon.py`, and the spec's lookups are bounded per title (one meta request, cached; a requested episode beyond the cached list refetches once).

## Live check of the resolver (2026-10-07)

`scripts/probes/anime_ids.py` run from a residential network after the implementation (`KitsuAnimeIdResolver` with `KitsuAddonClient` and `AnimeIdLists`, an in-memory cache, Scavengarr's User-Agent): the resolver translated 10 of 10 titles, all from the addon's record (`source` `addon`), and the list adapter had entries for 9 of 10.

| Kitsu | Title | Resolver (searched as) | List adapter |
|---|---|---|---|
| 12:1000 | Anime A | <Anime A> S21E109 | season None, offset 0 |
| 41982:3 | Anime G Season 3 Part 2 | <Anime G> S3E15 | season 3, offset 12 |
| 49240:2 | Anime I | <Anime I> S2E2 | season 2, offset 0 |
| 11614 | Anime O (movie) | <Anime O> movie | movie |
| 43247:2 | Anime V | <Anime V> S2E15 | season 2, offset 13 |
| 695:1 | Anime Q | <Anime Q> S1E1 | season 1, offset 0 |
| 48105:1 | Anime I (ONA) | <Anime I> S0E1 | none |
| 44081:2 | Anime H: Entertainment District Arc | <Anime H> S3E2 | season 3, offset 0 |
| 210:1000 | Anime F | <Anime F> S29E8 | season None, offset 0 |
| 48671:2 | Anime U | <Anime U> S2E2 | season 2, offset 0 |

The list alone would place the two long-runners at season 1 (no season in their entries) and the ONA nowhere; with the addon up, none of that is used. Earlier the same day the container had no DNS: the probe then reported `ConnectError` for every source and the resolver answered nothing for every title, which is the designed outage behaviour (no streams, `anime_id_lookup_failed`).
