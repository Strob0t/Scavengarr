## 1. Tests first

- [ ] 1.1 The file body against a local server: a reset after a pause, an early end (FIN) and a silent stall are each resumed from the next byte, the bytes identical to the file and none doubled or skipped.
- [ ] 1.2 Resumes run for every interruption of one response, not once; three failed attempts at one offset cut the transfer, with waits of 0, 1 and 2 s; the count starts again once bytes flow.
- [ ] 1.3 A refusal during a resume resolves again once and goes on from the new URL with its headers; a second refusal at the same offset counts as a failed attempt.
- [ ] 1.4 The stall bound applies after the first byte, the opening request keeps 60 s, a resume reads with the stall timeout; HLS segments keep their one resume.
- [ ] 1.5 Telemetry: `file_resume` by hoster and reason, `resumed` and `gave_up`.
- [ ] 1.6 The flag: the registry stamps `idle_reset` for VEEV, the stored link keeps it (an old record reads false), the builder gives `/proxy/<id>/file`, the file route accepts it.
- [ ] 1.7 The setting `stremio.file_stall_timeout_seconds` (default 10, 1 to 30) reaches the file route.

## 2. Implementation

- [ ] 2.1 `hls_proxy.py`: `FileResume`, the resume per interruption in `_cdn_body`, the stall bound, the refresh, the telemetry.
- [ ] 2.2 `router.py`: the file route builds `FileResume` (setting, refresh from `after_refusal`, hoster label) and accepts `idle_reset`.
- [ ] 2.3 The flag through `veev.py`, `registry.py`, `ResolvedStream`, `CachedStreamLink`, the link cache and the stream builder.
- [ ] 2.4 Docs: `docs/current/stremio-addon.md`, `docs/current/hoster-resolvers.md`, `docs/how-to/new-resolver.md`, `AGENTS.md`, the generated `docs/reference/configuration.md` and `docs/reference/metrics.md`, the glossary, `CHANGELOG.md`.

## 3. Rollout and proof

- [ ] 3.1 CI and the image green; an independent review of the commits.
- [ ] 3.2 Deploy while nothing plays; the post-deploy checks.
- [ ] 3.3 The title's VEEV stream is a `/proxy/<id>/file` URL; a pause test through it (60 s and 180 s): data within 5 s after the resume, the bytes after the resume identical to a direct range fetch of the CDN, the resume counter raised.
