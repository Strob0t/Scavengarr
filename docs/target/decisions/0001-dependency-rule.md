# 0001: Source code depends inward only

> **Purpose:** Records why source code depends inward only.
> **Status:** target
> **As of:** 9d2fc4c7

## Context

The code base mixed its layers: a validation framework (Pydantic) sat in the domain, one entity was defined twice, adapter logic was spread over three packages, and the composition root lived in `infrastructure/`.
Use cases (what the system does) and adapters (how it does it) were not separated.
Clean Architecture's layers promised tests of the inner layers without frameworks or I/O, adapters that change without touching the business logic, and one responsibility and one dependency direction per layer.

## Decision

The code under `src/scavengarr/` is split into four layers: `domain/`, `application/`, `infrastructure/` and `interfaces/`.
A layer must import only the layers inside it: `application/` and `infrastructure/` import `domain/`, and `interfaces/` imports every layer.
`infrastructure/` must never import `application/`.
The domain must stay free of frameworks and I/O, and its ports must be `Protocol` classes.
One composition root in `interfaces/` must build the adapters and wire them to the ports.

## Consequences

- A use case's test replaces every adapter with a fake behind its port, without a framework or a network.
- An adapter changes without touching a use case, and a new adapter implements a port.
- State or a rule that the application and an adapter both need must live in the domain: shared context variables, shared predicates and the null objects of optional ports.
- Infrastructure behaviour that no port covers reaches the application as an injected callable.
- Plugins and hoster resolvers are infrastructure: they import only `domain/` and `infrastructure/`.
- When the rule was decided no test checked the imports, so a breach showed up only in a review; `tests/unit/test_dependency_rule.py` has failed on an import against the layer graph since.

## Alternatives

No other architecture is on record; the alternatives are the states the rule replaced.

- Pydantic models as domain entities: convenient, but they broke the dependency rule.
- The composition root in the infrastructure layer: it belongs in `interfaces/`, the outermost layer that knows every concrete type.
- Infrastructure drawn as a ring around the application layer: under "dependencies point inward" it would let infrastructure import application, which the rule table and the code forbid.

## Status

accepted

## References

- [`docs/target/architecture.md`](../architecture.md): the rule table, the layer graph, the layer contracts and the invariants.
- [`docs/current/architecture.md`](../../current/architecture.md): the module map per layer and the composition root's order.
- `src/scavengarr/interfaces/composition.py`: the composition root.
