## Context

`stream_file` opens a file with the stored headers over the browser User-Agent, `Accept-Encoding: identity` and the player's `Range` and `If-Range`, with a 60 s read timeout (a CDN's first byte can take about 30 s).
`_cdn_body` is the one generator of a proxied body; it closes the CDN's answer itself, since a wrapper that closed it raced the event loop's finalizer.
It resumes a file once per transfer (`_Transfer.resumed`), with a `Range` request from the next byte, and cuts the transfer (`FileCut`) after that.
A file's body is only read while the player reads: during a pause the generator waits at its `yield`, uvicorn's write waits for the client's window, and the CDN's connection sits idle.

## Goals / Non-Goals

- **Goals:**
  - A paused playback of an idle-resetting CDN goes on after the pause within about one second, with no byte lost or doubled.
  - A CDN that falls silent mid-body is asked again well before a player's proxy gives up (60 s).
  - The recovery is bounded: a CDN that refuses every resume ends the transfer within 33 s.
- **Non-Goals:**
  - Keeping the CDN's connection alive during the pause (read-ahead into memory).
  - Changing the HLS segments' resume.
  - A per-hoster stall timeout.

## Decisions

- **The flag `idle_reset`** sits beside `address_bound` on the resolver, `ResolvedStream` and `CachedStreamLink`; the builder and the file route accept either. One combined flag would hide why a hoster is proxied.
- **The resume lives in `_cdn_body`**, guarded by the file's `FileResume` (stall timeout, refresh callback, telemetry, hoster label, waits): one generator still owns the CDN's answer.
- **The stall bound** wraps each read of a file's body after the answer's first byte in `asyncio.timeout(stall)`; the opening answer's headers and first byte keep the 60 s read timeout. A resume attempt's request and its answer's first byte share one deadline of the stall timeout (`asyncio.timeout_at`), the CDN semaphore and the client's retries included, so an attempt ends within the stall timeout.
- **Attempts are counted per offset** on the transfer (`attempts`, `attempt_at`): a resume that answers 206 and dies before a byte counts against the same budget, so a CDN that resets every new connection at once ends after three attempts.
- **The refresh is a callback** (`refresh(status) -> (url, headers) | None`) the route builds from `StremioLinks.after_refusal`, so the infrastructure imports nothing of the application layer; it runs once per interruption.
- **The interruption's reason** comes from the error: `httpx.ReadTimeout` or the stall bound is `stall`, `httpx.RemoteProtocolError` and a clean end short of the promised length are `eof`, every other transport error is `reset`.
- **Telemetry** is a stage `file_resume` with the labels `hoster` (a resolver name, else `other`) and `reason`; its outcome is `resumed` or `gave_up`, its duration the time from the interruption to the resumed answer.

## Risks / Trade-offs

- Bandwidth: every VEEV playback runs through the production host. The bytes counter (`scavengarr_hls_proxy_bytes_total{kind="file"}`) shows it.
- Restarts: a deploy, a restart or an exit of the stall monitor (`watchdog.exit_on_stall`, off) cuts running VEEV playbacks; the player asks again with a range and plays on.
- A slow CDN's resume (a first byte later than 10 s) gives up after three attempts where the old single resume waited 60 s; the player's next request is an opening request with 60 s.
- The stall bound could cut a body that flows with gaps of more than 10 s; measured gaps of flowing bodies stayed under 2 s.

## Migration Plan

Links stored before the flag read `idle_reset` as false and keep `/play` until their hoster resolves again (at most an hour). Rollback: revert the commits and deploy; stored links with the flag then play through `/play` again, since the old code ignores the field.

## Open Questions

None.
