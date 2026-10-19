# 0007: A plugin's search gets 30 s, and the answer waits for its target

> **Purpose:** Records why a plugin's search gets 30 s, and the answer waits for its target.
> **Status:** target
> **As of:** 9d2fc4c7

## Context

With a plugin timeout of 10 s the first answer went out at a soft deadline, even when slow plugins would have delivered more.
The first answer often carried few streams while slow plugins were still delivering.
The maintainer asked for more time per search.

## Decision

`stremio.plugin_timeout_seconds` defaults to 30 s: a plugin's search may run that long from the moment it holds its slot.
The answer is due once `stremio.resolve_target_count` hosters (5 by default) have a video, or once the search is done or past its answer budget and no resolution runs.
At the latest the answer goes out `stremio.stream_deadline_seconds` (60 s by default) after the request's start, and resolutions still running then are cancelled.

## Consequences

- A plugin that runs into its timeout counts as a circuit breaker failure and in the plugin history's `timeouts`.
- The plugins still running at the answer go on and fill the search cache for the next request.
- A slow site holds its plugin's slot for up to the plugin timeout.
- The answer budget decides when a player gets the first answer, the plugin timeout how long a site may take (see [0003](0003-answer-budget-beside-the-plugin-timeout.md)).

## Alternatives

The other options weighed are not on record; only the setup this decision replaced is.

- A soft deadline after which the answer went out, with a resolve grace for the streams: the first answer often carried few streams while slow plugins were still delivering.
- Holding back the plugins predicted to miss the soft deadline, or a shorter resolve grace: both went with the soft deadline.

## Status

accepted

## References

- `src/scavengarr/application/stremio/plugin_search.py`: the timeout per plugin.
- `src/scavengarr/application/stremio/resolution.py`: when the answer is due.
- [`docs/reference/configuration.md`](../../reference/configuration.md): the settings.
- [`docs/plans/stremio-latency.md`](../../plans/stremio-latency.md): the request times before and after.
