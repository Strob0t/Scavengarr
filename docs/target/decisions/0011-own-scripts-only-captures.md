# 0011: Captures that opt in load only their page's own scripts

> **Purpose:** Records why captures that opt in load only their page's own scripts.
> **Status:** target
> **As of:** 9d2fc4c7

## Context

On the production host the players of Filemoon and Vixeo load an ad network's script that takes every click and opens a popup, and the player never starts its stream.
Where the ad script fails to load, the same players play.
Without the block every click opened a popup: three clicks, three popups, in both runs.
With the ad script blocked, a capture on the production host found its playlist in 6.9 s.
A list of ad domains would go stale, since the ad network switches domains, and a DNS blocklist lies outside the app and also blocks hoster domains.

## Decision

A capture that opts in (`own_scripts_only=True`) must abort every script request whose site is neither the page's nor one of its frames' sites.
A script's site is the last two labels of its host, so a player's own scripts from its page's or its frame's site pass.
Cloudflare's challenge host must always pass, so a Turnstile widget still loads.
The Filemoon and Vixeo resolvers opt in, and the other captures load every script.

## Consequences

- The rule needs no list of ad domains and follows the ad network to any new domain.
- A player that needs a script from a foreign CDN breaks under the rule, so a resolver opts in only on evidence.
- Each aborted script is logged at debug level with its domain (`stealth_capture_script_blocked`), never as a metric label.

## Alternatives

- A list of ad domains: the ad network can switch domains.
- A DNS blocklist: it belongs to the maintainer's network configuration, and such lists also block hoster domains.
- Allowing the whole `cloudflare.com` site: `cdnjs.cloudflare.com` serves any library and stays foreign.
- Every capture opted in: the other captures are measured on the production host first.

## Status

accepted

## References

- `src/scavengarr/infrastructure/browser/stealth_pool.py`: `_foreign_script()` and the capture's route.
- `src/scavengarr/infrastructure/hoster_resolvers/filemoon.py` and `src/scavengarr/infrastructure/hoster_resolvers/vixeo.py`: the resolvers that opt in.
