# 0004: A located episode is placed by its number first

> **Purpose:** Records why a located episode is placed by its number first.
> **Status:** target
> **As of:** 01a39f90

## Context

The anime sites list a season's episodes as rows with the episode's absolute number and often an English title.
A plugin that locates episodes (`locates_episodes = True`) must pick the row a request means from the request's episode reference: the English title, the air date, the absolute number and the number's source.
Titles differ between a site and the metadata (dubs, translations, a part's suffix), so a title match can pick a wrong row where the number names the right one.
The number is the sites' count: a `kitsu:` request carries Kitsu's number, and an IMDb anime request gets its episode's Kitsu number where one Kitsu entry counts the series from its start.
The maintainer decided for the number first.

## Decision

`locate()` must take the row that shows the reference's absolute number, whatever its title.
Only without such a row, or without a number, does the title decide: a titled row near the number, then an equal title, then the best title match above a threshold.
A number the anime ids do not vouch for (`source="unchecked"`) must place only a row without an English title, or a reference without one; a titled row needs its title to confirm it.
On a series the site names only by a shorter form of the request's titles, the number counts another series, so only the title must place.
When no row matches, the plugin must answer nothing for the series, never a row picked by the request's season and episode numbers.

## Consequences

- A wrong number places a wrong row, so the number's sources carry the placement: the Kitsu number for IMDb anime requests, and a position in Cinemeta's list that skips the episode 0 entries of regular seasons.
- Every plugin that locates episodes uses the shared index of `infrastructure/plugins/episode_index.py`, so one rule set serves them all.

## Alternatives

- Narrower rules that let the number's row win only where no nearby row matched the title well enough, or only where it was also the best title match: they left episodes without a row that this rule places.
- Titles first: a title whose words are a subset of another's scores as an equal match, so a part number does not count.
- No change: the episodes without a row would have stayed without a result from the plugin.

## Status

accepted

## References

- `src/scavengarr/infrastructure/plugins/episode_index.py`: `locate()` and its rules.
- `src/scavengarr/application/stremio/title_resolution.py`: the episode reference and its position rule.
- [`openspec/specs/stremio-anime-episodes/spec.md`](../../../openspec/specs/stremio-anime-episodes/spec.md): the requirements.
- [`openspec/changes/archive/2026-10-18-place-episodes-by-number-first/proposal.md`](../../../openspec/changes/archive/2026-10-18-place-episodes-by-number-first/proposal.md): the change and the maintainer's decision.
- [`docs/archive/plans/series-episodes.md`](../../archive/plans/series-episodes.md): the evidence on the sites' numbering, the Kitsu number and the episode 0 entries.
