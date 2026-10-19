# Deploying the Stremio addon

> **Purpose:** Deploying the addon with a streaming server, a VPN container and AIOStreams.
> **Status:** how-to
> **As of:** 19b7e2c8

## Goal

Stremio plays Scavengarr's streams: in its apps, in Stremio Web through a streaming server of its own, with Scavengarr or the streaming server behind a VPN container, and optionally behind AIOStreams.
How the addon works is in [the Stremio addon](../current/stremio-addon.md).

## Prerequisites

- Scavengarr runs and answers at a base URL that the players and the streaming server reach, here `https://scavengarr.example.lan` behind a reverse proxy with TLS ([Deploy Scavengarr](deploy.md)).
- For Stremio Web: a Stremio streaming server, here [tsaridas/stremio-docker](https://github.com/tsaridas/stremio-docker) at `https://stremio.example.lan` behind the same reverse proxy.
- For a VPN container: one that Scavengarr or the streaming server shares its network with, here gluetun.
- For AIOStreams: an instance that reaches Scavengarr's base URL.

## Steps

### 1. Install the addon

Open Stremio's addon page, paste the manifest's URL into its search field and install the addon:

```text
https://scavengarr.example.lan/api/v1/stremio/manifest.json
```

The desktop and Android apps play through their own streaming server; Stremio Web needs the one of step 2.

### 2. Run a streaming server of your own

Stremio Web hands every stream to the streaming server's `/hlsv2/probe` before it plays it.
It names a stream with `proxyHeaders` by the server's own public URL (`https://stremio.example.lan/proxy/…`) and a stream of Scavengarr's proxies by Scavengarr's URL.
The server must therefore resolve and reach both names, its own and Scavengarr's.
Point Stremio Web at the server in its settings.

Keep `stremio.allow_hls_transcoding` off, the default.
The streaming server's converter re-encodes an HLS stream with libx264: on the production host, which has no usable hardware encoder, a stream of 1480×620 took one to two cores, and 1080p stuttered.
With the setting off the HLS proxy refuses the converter the playlist, and Stremio Web plays the playlist itself with hls.js.

### 3. Put Scavengarr behind a VPN container

When the sites must see a VPN address, run Scavengarr in the VPN container's network namespace, as [Run behind a VPN container](deploy.md#6-run-behind-a-vpn-container) shows.
Turn gluetun's `BLOCK_MALICIOUS` off (`BLOCK_MALICIOUS=off`): it refuses DNS answers whose address is on its lists, and its lists hold MixDrop's CDN (`*.mxcontent.net`).
The lookups stay in the tunnel without it, unfiltered, through gluetun's own DNS server over TLS.
Keep gluetun's own upstream: a filtering resolver of your own as its upstream (`DNS_UPSTREAM_RESOLVER_TYPE=plain`) sends every lookup of the VPN stack outside the tunnel.

### 4. Put the streaming server behind a VPN container

A streaming server in a VPN container's network uses the VPN container's DNS, which knows no LAN names, and its firewall, which blocks the LAN.
Map the reverse proxy's names to its address with `extra_hosts` on the VPN container:

```yaml
services:
  stremio:
    image: tsaridas/stremio-docker
    network_mode: service:vpn
  vpn:
    image: qmcgaw/gluetun
    extra_hosts:
      - "scavengarr.example.lan:<reverse proxy address>"
      - "stremio.example.lan:<reverse proxy address>"
    environment:
      - FIREWALL_OUTBOUND_SUBNETS=<the reverse proxy's subnet>
```

`FIREWALL_OUTBOUND_SUBNETS` lets the server reach that subnet past the VPN container's firewall.

### 5. Add Scavengarr to AIOStreams

Add Scavengarr to an AIOStreams user as a `custom` addon with these settings:

```json
{
  "manifestUrl": "https://scavengarr.example.lan/api/v1/stremio/manifest.json",
  "resources": ["stream"],
  "mediaTypes": ["movie", "series"],
  "timeout": 62000
}
```

- `timeout` is in milliseconds per addon and must exceed `stremio.stream_deadline_seconds`: take the deadline plus 2 s (62000 with the default deadline), since AIOStreams' default of 7000 cuts most of Scavengarr's answers.
- Set the user's `preferredLanguages` to `["German", "Multi", "Dual Audio", "English", "Unknown"]` and its `sortCriteria.global` to language, resolution and quality, all `desc`.
- AIOStreams' deduplication and result limits need no settings for Scavengarr, which answers one working stream per hoster and language.
- AIOStreams checks the manifest when the user's configuration is saved, so Scavengarr must be reachable from the AIOStreams host at that moment.

These settings follow AIOStreams' own source (`packages/core/src/presets/custom.ts`, `packages/core/src/db/schemas.ts`).

## Verify

- `curl -s https://scavengarr.example.lan/api/v1/stremio/manifest.json` answers the manifest with `"id": "community.scavengarr"`.
- `curl -s -o /dev/null -w '%{http_code}\n' https://scavengarr.example.lan/api/v1/stremio/health` prints `200`, and the body names the stream plugins and the resolvers.
- `curl -sD - -o /dev/null https://scavengarr.example.lan/api/v1/stremio/stream/movie/tt1234567.json` shows `X-Cache` and `X-Search-Complete`, and the same request a minute later answers `HIT` once the first one found a result.
- From the player's machine, `poetry run python scripts/stremio_playcheck.py --base https://scavengarr.example.lan movie/tt1234567` fetches every stream as a player does and prints a verdict per stream: a stream bound to the address that resolved it fails from elsewhere.
- In Stremio Web a stream of the title plays.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Every stream fails in Stremio Web, and the server's probe answers 500. | The streaming server cannot resolve or reach Scavengarr or its own public name from the VPN container's network. | Map the names with `extra_hosts` and allow the subnet with `FIREWALL_OUTBOUND_SUBNETS` (step 4). |
| Only the streams the browser can play directly start in Stremio Web, and MKV, HEVC or AC3 files do not. | The server's probe fails, so Stremio Web plays what the browser plays after a `HEAD` for the content type and transcodes nothing. | Fix the probe's reach (step 4). |
| An HLS stream of some hosters fails with error 81, "Error occurred when downloading". | tsaridas/stremio-docker's nginx nests a regex location for web assets inside `location /`, which answers 404 for segments disguised with such an extension (`…_000.css`). | Play those streams in the desktop or Android app, whose own streaming server runs without that nginx; a change to the image's nginx configuration is untested. |
| MixDrop streams resolve but never play, and the log shows `hoster_resolve_unplayable` with `cannot resolve` and the CDN's domain. | gluetun's `BLOCK_MALICIOUS` refuses the CDN's DNS answers, and `DNS_UNBLOCK_HOSTNAMES` applies to its hostname list only, not to blocked addresses. | Turn `BLOCK_MALICIOUS` off (step 3). |
| Scavengarr reports healthy but finds nothing after the VPN container was recreated. | A container in another one's network namespace loses its network when that one is recreated. | Recreate Scavengarr after the VPN container ([Run behind a VPN container](deploy.md#6-run-behind-a-vpn-container)). |
| Every container on the VPN container's network goes down. | An unhealthy VPN container takes down every container that shares its network, and one turned unhealthy with a filtering resolver as its upstream. | Keep gluetun's own upstream (step 3). |
| AIOStreams shows no Scavengarr streams for most titles. | The addon's `timeout` lies below `stremio.stream_deadline_seconds`, so AIOStreams cuts the answers. | Set `timeout` to the deadline plus 2 s (step 5). |
| AIOStreams refuses to save the user's configuration. | AIOStreams could not fetch the manifest at that moment. | Make the base URL reachable from the AIOStreams host, then save again. |
| AIOStreams shows the wrong language or resolution for Scavengarr's streams. | AIOStreams parses them from the stream names, which is unverified for Scavengarr's names. | Set `formatPassthrough: true`, which keeps Scavengarr's own labels. |
| HLS streams stutter in Stremio Web, and the streaming server's CPU runs high. | `stremio.allow_hls_transcoding` is on, and the server re-encodes the streams without a hardware encoder. | Turn the setting off (step 2). |
