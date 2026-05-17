# ADR 0004 — Namespace-isolated action plugins

**Status**: Accepted
**Date**: pre-2026 (foundational plugin architecture)

## Context

jvagent ships ~40 first-party actions and is designed to host third-party plugins. Without name isolation, two plugins could both ship an action called `summarize`, conflicting on Python module names, package directories, and discovery.

## Decision

Every action is identified by a fully qualified name in the form **`namespace/action_name`**.

- `namespace` is a short identifier owned by the publisher (`jvagent`, `contrib`, `custom`, `myorg`, etc.).
- `action_name` is unique within the namespace.

The on-disk layout mirrors the namespace:

```
jvagent/action/{namespace}/{action_name}/
```

`info.yaml` declares the canonical name:

```yaml
package:
  name: namespace/action_name
  archetype: ClassName
```

`agent.yaml` references actions by their fully qualified name:

```yaml
actions:
  - action: namespace/action_name
```

`jvagent/` is reserved for the core library. Third-party publishers SHOULD pick a distinct namespace.

## Consequences

### Positive
- **No name collisions** between unrelated plugins.
- **Discovery is path-driven** — the loader walks `{namespace}/{action_name}/` directories and uses the `archetype` in `info.yaml` to pick the class.
- **Dependencies are clear** — `info.yaml` lists dependent actions by namespace-qualified name (e.g., `jvagent/persona`).
- **Endpoint paths can be namespace-scoped** if a publisher wishes (`/contrib/slack/...`) — the framework doesn't require this, but it's natural.

### Negative
- **More verbose references** — `jvagent/persona` instead of `persona`. Acceptable cost.
- **Loader complexity** — has to handle namespace traversal, parent action coordination (e.g., `google` parent over `google/gmail` child).
- **No central registry** of namespaces — collisions between two third parties picking the same namespace remain possible. Convention + community discipline.

## Reserved namespaces

| Namespace | Owner |
|---|---|
| `jvagent` | Core library — first-party actions only |
| `contrib` | Convention for community-contributed actions accepted into the repo |
| `custom` | Convention for app-local actions (not shipped with jvagent) |

## Alternatives considered

1. **Flat names, alphabet-prefixed**: rejected — fragile, no ownership signal.
2. **Python package names**: rejected — too restrictive (e.g., `acme-actions-slack`), couples plugin shape to PyPI packaging.
3. **UUIDs as action identifiers**: rejected — unusable in YAML config.

## References

- [`SPEC.md`](../SPEC.md) §4.6
- [`action-authoring.md`](../action-authoring.md) §2 — directory layout
- [`actions-catalog.md`](../actions-catalog.md) — current namespace inventory
