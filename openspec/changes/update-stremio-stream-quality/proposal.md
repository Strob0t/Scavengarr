# Change: Measure bitrate and keep the best streams

## Why

Three problems in the streams Stremio gets, all seen on the production host with
`tt0304141` (Harry Potter and the Prisoner of Azkaban, 2026-11-17/18):

- **A site's link flood hid another site's 1080p.** `movie4k` returned 50 links
  for the same hosters, nearly all labelled `HD` (720p, score 1400). hdfilme's
  unlabelled dropload link (score 1000, actually 1080p) ranked 47th of 135, and
  `stremio.max_probe_count` (40) cuts the list *before* resolving, so it was
  never probed. The first request answered before `movie4k`'s results arrived,
  which is why the 1080p appeared once and vanished on reload.
- **The same upload answered three times.** `rb.gy` and `bit.ly` links redirect
  to one `voe.sx` upload. The registry followed the redirect and resolved it,
  but the answer kept the site's labels `rb` and `bit` as hosters, so one
  stream occupied three slots.
- **A 1 MB placeholder counted as a film.** `bigwarp.io` answered
  `bigwarp.pro/player/321b.mp4`, a 1 MB 720p MP4. It is a real, playable file,
  so the playback check accepted it.

The playback check also already fetches an HLS master playlist and an MP4
`moov` box, so the bitrate is available without another request and can settle
streams of the same quality and language.

## What Changes

- **One bitrate per stream, measured for free.** The best HLS variant (most
  pixels, then the higher bitrate) contributes its `AVERAGE-BANDWIDTH` (else
  `BANDWIDTH`); an MP4 file contributes `total size * 8 / mvhd duration`. A
  CRLF playlist and an attribute a sniff cut are read correctly. The stream
  line shows `2.2 Mbps`, and the rank adds 1 point per Mbps, capped below the
  smallest language gap and below one quality step.
- **A per-hoster cap before the probe window.** `stremio.max_links_per_hoster`
  (default 2) keeps each hoster and language's best links, so a site listing
  dozens of links cannot fill `stremio.max_probe_count`. Links the registry
  already knows work (cached alive, or resolved before a better-labelled
  newcomer) are exempt from both the cap and the truncation.
- **One playable stream per hoster after resolution.** The registry's resolver
  name becomes the stream's hoster (a shortener that lands on voe is voe), and
  the measured answer keeps one resolved direct video per hoster, language and
  episode. An echoed embed page or a wrong-season result never suppresses the
  hoster's real stream.
- **A stub floor.** `stremio.min_stream_size_mb` (default 20, `0` off) drops a
  resolved file below it and caches it dead like an unplayable stream; it does
  not count against the resolver's circuit breaker. A playlist body has no
  size, and the content-type probe is not checked.
- **Unchanged defaults.** Quality still ranks below language by default
  (`quality_multiplier: 10`); a deployment that wants `1080p` above a German
  `720p` sets `quality_multiplier: 1000` (about 10,000 per quality step).

## Impact

- Affected specs: `stremio-stream-quality`.
- Affected code: `application/stremio/{answer,resolution,stream_builder}.py`,
  `application/use_cases/stremio_stream.py`,
  `domain/entities/stremio.py`,
  `infrastructure/config/schema.py`,
  `infrastructure/hoster_resolvers/{_mp4,_verify,registry}.py`,
  `infrastructure/stremio/stream_sorter.py`,
  `interfaces/composition.py`.
- Configuration: two new settings, `stremio.max_links_per_hoster` and
  `stremio.min_stream_size_mb`, with generated docs.
- Tests: probe-window cap and exemptions, bitrate parsing and ranking, MP4
  duration, redirect hosters, deduplication, stub floor, CRLF/cut playlists.
- Operation: a title's resolution state is unchanged; the new fields default
  empty, so an old snapshot reads exactly as before.
