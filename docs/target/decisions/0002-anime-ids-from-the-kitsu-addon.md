# 0002: Anime ids from the Anime Kitsu addon, Fribb's list as the fallback

> **Purpose:** Records why anime ids come from the Anime Kitsu addon, with Fribb's list as the fallback.
> **Status:** target
> **As of:** 5b4303e4

## Context

The anime catalogs in Stremio hand out `kitsu:` ids, while the plugins, the search cache and the ranking work on IMDb requests.
A `kitsu:` request therefore needs the title's IMDb id and its episode's place in IMDb's season count.
Four sources were weighed: the Anime Kitsu addon, the public id lists (Fribb's anime-lists), Kitsu's own API and TMDB's id lookup.
The addon places every episode of an entry in IMDb's count.
The lists agree with the addon where they place an entry, but they leave a long-running series in its absolute count.
Kitsu's API places no episode, and TMDB's lookup needs an API key.

## Decision

A `kitsu:` request must be translated into an IMDb request before the rest of the pipeline sees it.
The Anime Kitsu addon's record of the title answers first, Fribb's list answers when the addon does not answer or does not map the entry, and with neither the request gets no streams.
The translated request is the search's cache key, and its content type comes from the source that answered.

## Consequences

- Everything after the translation (the title search, its cache, the episode filter, the ranking and the resolution) knows IMDb requests only.
- The addon's records are cached for 30 days, and a record is fetched again once when a request names an episode newer than the cached record.
- Fribb's list loads in the background at the app's start or on the first look-up, reduced to the entries with a Kitsu and an IMDb id, held in memory and cached for 7 days.
- While the addon is down, the list's placements answer, and they miss the season count of a long-running series.
- An entry that neither source maps answers without streams rather than with a guessed IMDb id.

## Status

accepted

## References

- `src/scavengarr/infrastructure/anime/`: `KitsuAnimeIdResolver`, `KitsuAddonClient` and `AnimeIdLists`.
- `src/scavengarr/domain/ports/anime_ids.py`: the `AnimeIdResolverPort`.
- [`openspec/specs/stremio-anime-ids/spec.md`](../../../openspec/specs/stremio-anime-ids/spec.md): the requirements.
- [`openspec/changes/archive/2026-10-12-add-anime-ids/design.md`](../../../openspec/changes/archive/2026-10-12-add-anime-ids/design.md): the design and its alternatives.
- [`docs/archive/plans/anime-ids-spike.md`](../../archive/plans/anime-ids-spike.md): the evidence on the four sources and the maintainer's choice.
