# 0011: Captures that opt in load only their page's own scripts

> **Purpose:** Records why captures that opt in load only their page's own scripts.
> **Status:** target
> **As of:** 5b4303e4

## Context

On the production host the players of Filemoon and Vixeo load an ad network's script that takes every click and opens a popup, and the player never starts its stream.
Where the ad script fails to load, the same players play.
With the ad script blocked in a capture on the production host, the player started its stream again.
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

## Status

accepted

## References

- `src/scavengarr/infrastructure/browser/stealth_pool.py`: `_foreign_script()` and the capture's route.
- `src/scavengarr/infrastructure/hoster_resolvers/filemoon.py` and `src/scavengarr/infrastructure/hoster_resolvers/vixeo.py`: the resolvers that opt in.
- [`docs/archive/plans/filmo-plugin.md`](../../archive/plans/filmo-plugin.md): the evidence for Filemoon on the production host.
- [`docs/archive/plans/header-bound-hosters.md`](../../archive/plans/header-bound-hosters.md): the evidence for Vixeo and for Cloudflare's challenge host.
