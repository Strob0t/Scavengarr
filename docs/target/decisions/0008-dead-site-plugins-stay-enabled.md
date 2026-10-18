# 0008: A plugin whose site is down stays enabled

> **Status:** target
> **As of:** 5b4303e4

## Context

The sites of several plugins stopped answering on every known domain: megakino_to, movie4k, fireani, kinox and boerse.
Disabling or removing such a plugin loses it for the day its site returns.
Keeping it costs a domain check per health interval, since a search leaves out a plugin whose site is down.
The maintainer decided: the plugins stay.

## Decision

A plugin whose site is unreachable must stay enabled: no rule and no threshold disables or removes it.
The plugin must report its site unreachable (`PluginUnreachableError`), and the health monitor's domain check must keep watching for the site's return.
Keeping, disabling or removing a plugin whose site died is the maintainer's call, made from the plugin history.

## Consequences

- A Stremio search leaves out the plugins whose last health check found the site down, so a dead site takes no search slot.
- The plugin history counts the checks that found the site down and the searches that found no domain, per plugin and day: the maintainer's evidence.
- A site that returns on one of the plugin's domains serves again after its next health check, without a code change.
- Proposing to disable or remove such a plugin is not the agents' call.

## Status

accepted

## References

- `src/scavengarr/domain/plugins/base.py`: `PluginUnreachableError`.
- `src/scavengarr/infrastructure/plugins/health_monitor.py`: the periodic domain check.
- `src/scavengarr/application/stremio/plugin_search.py`: `_reachable()`.
- `src/scavengarr/infrastructure/plugins/history.py`: the plugin history.
- [`docs/plans/ideas-backlog.md`](../../plans/ideas-backlog.md): the maintainer's decision, in the Decisions section.
- [`docs/archive/plans/plugin-domains.md`](../../archive/plans/plugin-domains.md): the domain checks and the sites' returns.
