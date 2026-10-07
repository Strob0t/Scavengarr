# Anime Ids Spike (I11, `add-anime-ids` task 1.1)

Status 2026-10-07: spike done; the maintainer chose option 2 (the addon, the public list as the fallback; `openspec/changes/add-anime-ids/` revised). One list suffices for the fallback: Fribb's records carry `imdb_id`, `season.tvdb` and `episode_offset.tvdb` (Attack on Titan Season 3 Part 2: season 3, offset 12), so Kometa's list is not needed.

## Setup

`scripts/probes/anime_ids.py` (read-only, no cache writes, prints no URLs or keys), run from the dev container on the home network, twice for all ten titles and once more for the ONA. Per title it asks:

- **Kitsu's API** for the entry (subtype, titles, mappings) and the episode record (`seasonNumber`, `relativeNumber`): the sources of the approved design;
- the **public id lists**: Fribb's anime-lists (by Kitsu id: IMDb, TheTVDB and TMDB ids, seasons) and Kometa's Anime-IDs (by AniDB id: TheTVDB season and episode offset);
- the **Anime Kitsu addon's meta** (`/meta/series/kitsu:<id>.json`, the addon whose catalogs hand out the `kitsu:` ids): the IMDb id, and per episode `imdbSeason`/`imdbEpisode`, the fields Torrentio maps Kitsu requests with. It answers 403 to httpx's default User-Agent and 200 to Scavengarr's (`APP_USER_AGENT`, with the contact URL);
- **TMDB's `/find` by TheTVDB id**: not run. Neither the dev environment nor production has a TMDB key; production logs `tmdb_client_fallback` and resolves every title through the IMDb fallback client (IMDb Suggest, Wikidata);
- the **sites**: aniworld's and fireani's `search()` with the title the IMDb fallback gives for the mapped IMDb id (what a `tt` request gets today) and with Kitsu's romaji title, at the addon's season and episode.

## Results

Second run (with the addon). Site columns: search results of aniworld / fireani; a result is a title with the requested episode's links, not yet a played stream.

| Request | Case | Kitsu: TheTVDB mapping; episode record | Lists: IMDb, placement | Addon: IMDb, placement | Title (IMDb fallback) | Sites by that title | Sites by romaji |
|---|---|---|---|---|---|---|---|
| `kitsu:12:1000` | long-runner (One Piece) | series id, no season; S1, no relative | tt0388629, absolute (TheTVDB season `-1`) | tt0388629 S21E109 | One Piece | 1 / 1 | same title |
| `kitsu:41982:3` | split cour (Attack on Titan Season 3 Part 2) | none; S1 | tt2560140 S3E15 (offset 12) | tt2560140 S3E15 | Attack on Titan | 1 / 0 | 0 / 0 |
| `kitsu:49240:2` | current season (Frieren 2nd Season) | none; S1 | tt22248376 S2E2 (TMDB counts it as season 1) | tt22248376 S2E2 | Frieren: Beyond Journey's End | 0 / 1 | 0 / 1 |
| `kitsu:11614` | movie (Your Name) | none | tt5311514 | tt5311514 | Your Name. – Gestern, heute und für immer | 0 / 0 | 0 / 0 |
| `kitsu:43247:2` | split second season with recaps (Re:Zero 2nd Season Part 2) | none; S1 | tt5607616 S2E15 (offset 13) | tt5607616 S2E15 | Re:ZERO – Starting Life in Another World | 0 / 1 | 0 / 1 |
| `kitsu:695:1` | OVA (Hellsing Ultimate) | none; S1, relative 1 | tt0495212 S1E1 | tt0495212 S1E1 | Hellsing Ultimate | 1 / 1 | same title |
| `kitsu:48105:1` | ONA (Frieren: Marumaru no Mahou) | none; S1 | not listed | tt22248376 S0E1 (a special of the series) | Frieren: Beyond Journey's End | 0 / 0 | 0 / 0 |
| `kitsu:44081:2` | aniworld staple (Demon Slayer: Entertainment District Arc) | none; S1 | tt9335498 S3E2 | tt9335498 S3E2 | Demon Slayer: Kimetsu no Yaiba | 1 / 0 | 0 / 0 |
| `kitsu:210:1000` | long-runner, aniworld staple (Detective Conan) | series id, no season; S1 | tt0131179, absolute | tt0131179 S29E8 | Detektiv Conan | 1 / 1 | 1 / 0 |
| `kitsu:48671:2` | fireani staple (Solo Leveling Season 2) | none; S1 | tt21209876 S2E2 | tt21209876 S2E2 | Solo Leveling | 1 / 1 | 0 / 1 |

## Findings

1. **Kitsu's own data places nothing.** Its mappings carry a TheTVDB id for 2 of 10 entries (the two long-runners, series ids only) and a `thetvdb/season` mapping for none; every TV entry's episode record says season 1 without `relativeNumber` (the OVA: relative 1). The approved order (Kitsu's mappings, then TMDB's `/find`) would place none of the later or split seasons, and without a TMDB key it ends in path (3), titles only, for all ten.
2. **The addon maps all ten and places every episode** the way Cinemeta counts it: the split cours at the season's running number (Season 3 Part 2 episode 3 is S3E15), the long-runners at IMDb's seasons (One Piece episode 1000 is S21E109, Detective Conan episode 1000 is S29E8), the ONA as a special (S0E1). The translated request is the `tt` request Cinemeta would send for the same episode.
3. **The lists agree with the addon wherever they place** (6 of the 9 series entries), but leave the long-runners absolute and do not list the ONA. They are a 7.5 MB and a 1.7 MB JSON file on GitHub.
4. **The sites find the titles through the IMDb title, not the romaji one.** At the addon's placement, aniworld or fireani found 8 of the 9 series episodes by the IMDb fallback's title (aniworld 6, fireani 6); by Kitsu's romaji title aniworld found only the two whose romaji title is the English one (fireani 5). Path (3) is weaker than the translation in every case and is not needed while the translation maps every entry.
5. **Not found**: the movie (aniworld and fireani answer category 5070 only; movie plugins search it as for any `tt` movie) and the ONA's special (neither site lists it as S0E1).
6. **Variance**: fireani's answers changed between the two runs (Attack on Titan and Demon Slayer found only in the first, Frieren's second season only in the second; its episode pages answered 404 in places), and the IMDb fallback gave the English title for Detective Conan and Your Name in the first run, the German one in the second. Ten titles, two runs: the numbers show the direction, not rates.

## Decision

The spike falsifies the approved design's source order: Kitsu's mappings place no season, and TMDB's `/find` needs a key that no deployment here has. Options for the revision (task 1.1 asks to decide from the spike; the source is the maintainer's call):

1. **The Anime Kitsu addon's meta as the only source (recommended).** One request per title (all episodes in one answer), cached; the `kitsu:` request becomes the `tt` request with IMDb's season and episode, and everything after it (titles through TMDB or the IMDb fallback, plugins, cache key, links) is the existing path. No Kitsu API client, no TMDB `/find`, no path (3). Pro: 10 of 10 mapped in the spike, long-runners included; the numbering Cinemeta and Torrentio use; one small adapter. Con: one community service (Cloudflare in front; it refused httpx's default User-Agent); while it is down, titles not in the cache get no streams (`anime_id_lookup_failed`).
2. **The addon, with the public lists as fallback.** Pro: survives an addon outage for the entries the lists place (6 of 9 series in the spike, no long-runners). Con: a second adapter with a weekly 9 MB download and its tests, for a fallback.
3. **The public lists only, Kitsu's API for the titles of unlisted entries.** Pro: two static files instead of a service per request. Con: no long-runners (One Piece, Detective Conan stay absolute), 9 MB a week, path (3) stays.
4. **The approved design unchanged, with a TMDB key.** Con: places none of the ten in the spike; needs a TMDB key in production.

With option 1 the OpenSpec change shrinks: `AnimeIdResolverPort.translate(request)` returns the translated request (no titles, no `season_source`), the adapter is `infrastructure/anime/kitsu_addon.py`, and the spec's lookups are bounded per title (one meta request, cached; a requested episode beyond the cached list refetches once).
