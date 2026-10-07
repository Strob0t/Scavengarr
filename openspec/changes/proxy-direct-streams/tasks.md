# Tasks: proxy-direct-streams

TDD throughout (AGENTS.md §6); each numbered group is one commit with its
tests and docs. Mock patterns: `stream_file` and the resolver attributes use
`respx`; the router and e2e tests use the fakes of
`tests/e2e/test_stremio_endpoint.py` (`_make_app`, `_make_hls_link`);
`TelemetryPort` → `NO_TELEMETRY` or the recording fake of
`TestProxyTelemetry`. Order: 1 → 2 → 3 → 4 → 5 → 6. Worktree `handoff-26`
from `origin/staging`, after step 21 is complete and before step 19.

## 1. The flag

- [x] 1.1 `ResolvedStream.address_bound: bool = False` (`domain/entities/stremio.py`) after `size_bytes`, one docstring line: the CDN binds the URL to the address that resolved it.
- [x] 1.2 Registry (`infrastructure/hoster_resolvers/registry.py`): read `address_bound` from the resolver where `needs_playback_check` is read, default `False`, and stamp it on the stream it returns as `resolved_at` is stamped; unit tests with a fake resolver that declares it and one that does not.
- [x] 1.3 `address_bound = True` on `DoodStreamResolver`, `MixdropResolver`, `VinovoResolver` and `FsstResolver`, one docstring line each naming the evidence (finding 17: `error_wrong_ip`, 403, 403, 410); each resolver's test asserts the attribute.

## 2. The stored link and the URL

- [x] 2.1 `CachedStreamLink.address_bound: bool = False`; `with_resolution` (`application/stremio/stream_builder.py`) copies it from the resolved stream; `infrastructure/persistence/stream_link_cache.py` stores and reads it, a record from before the change reads `False` (test with such a record).
- [x] 2.2 `FILE_NAME = "file"` next to `HLS_MASTER`; `build_stream_from_resolved`: `is_hls` unchanged; `address_bound` and not HLS → `/api/v1/stremio/proxy/<id>/file` with the HLS proxy stream's behaviour hints and no `proxyHeaders`; else unchanged. Tests in `tests/unit/application/test_stream_builder.py`: bound file, unbound file, HLS, VEEV stays on `/play`.

## 3. The pass-through

- [x] 3.1 `stream_file` in `infrastructure/stremio/hls_proxy.py` next to `stream_hls_segment`: headers from `_player_headers(stored)` plus `Accept-Encoding: identity` and the player's `Range` and `If-Range` when given; the GET streamed with `follow_redirects=True` under `_CDN_SEMAPHORE` until the headers arrive, read timeout `_FILE_READ_TIMEOUT_S = 60.0` (Vinovo's first byte); returns the CDN's status, the headers to pass (`Content-Type`, `Content-Length`, `Content-Range`, `Accept-Ranges`) and a chunk iterator of `_SEGMENT_CHUNK` pieces; HEAD closes the GET after the headers. respx tests in `tests/unit/infrastructure/test_hls_proxy.py`: whole file 200, range 206 with `Content-Range`, HEAD without a body, `Accept-Encoding: identity` and `Range` sent, 416 passed, connect error and timeout mapped.
- [x] 3.2 `_proxy_link` (`interfaces/api/stremio/router.py`) splits into the shared lookup (404 without a link, 503 without the repository, `links.current` for a stale link) and the per-route check: playlist and segment routes keep 400 for a non-HLS link, the file route answers 400 for an HLS or unbound link.
- [x] 3.3 Route `GET`/`HEAD /proxy/{stream_id}/file`: `stream_file`; CDN 403, 404 or 410 → `links.after_refusal` once and one retry, then 502; connect error or timeout → 502; `StreamingResponse` with the CDN's status, the passed headers and CORS, the body through `_counted`. Tests in `tests/unit/interfaces/test_stremio_router.py` and `tests/e2e/test_stremio_endpoint.py` (`TestProxyFileEndpoint`: whole, range, HEAD, stale, refusal then success, refusal twice, HLS link, old record without the flag).

## 4. Telemetry and docs

- [ ] 4.1 The file route records the `hls_proxy` stage with `kind="file"` and the status string as outcome, and the bytes to `hls_proxy_bytes{kind="file"}` through `_counted`, aborted transfers included; test in `TestProxyTelemetry`; log lines carry the CDN host only.
- [ ] 4.2 Middleware test pins that `/api/v1/stremio/proxy/<id>/file` is rate-limit exempt and its query masked (`_EXEMPT_PREFIXES` covers `/proxy/`).
- [ ] 4.3 Docs in the same commit as 4.1: `docs/features/stremio-addon.md` (the file proxy: the four hosters, the route, ranges, refusals, old records keep `/play`), `docs/features/hoster-resolvers.md` (`address_bound` next to `needs_playback_check`), `docs/features/observability.md` (`kind="file"` on the HLS proxy metrics), AGENTS.md §7 one sentence after the `needs_playback_check` sentence, `CHANGELOG.md` one entry for step 26.

## 5. The number for files

- [x] 5.1 `scripts/probes/hls_throughput.py --file <stream id>`: the first 32 MiB of the stored direct file in one connection, then as three parallel byte ranges in 1 MiB steps, with the stored headers over the default browser User-Agent; prints Mbit/s for both and whether the CDN honoured the ranges (206 with the asked `Content-Range`); host only, no URL or token; runs in the production container through `scripts/prodctl.py probe`.
- [x] 5.2 Run on the Pi for one MixDrop and one DoodStream id (no deploy needed: the probe reads the stored links and fetches their CDNs itself; run 2026-10-07 on links resolved before this change); the numbers into the backlog's row 26 and `docs/plans/stremio-latency.md`; a read-ahead for files is a change of its own if the ranges are faster.

## 6. Acceptance

- [ ] 6.1 After the deploy: `scripts/stremio_round.py` from the dev container against production for the seventh round's ids; the DoodStream, MixDrop, Vinovo and FSST streams play; the table under the seventh round in `docs/plans/stremio-latency.md`, failures by hoster and error class.
