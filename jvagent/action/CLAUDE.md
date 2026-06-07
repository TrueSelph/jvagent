# jvagent/action/ — Agent Guide

> Local guide for the action plugin layer. Cross-link: root [`/CLAUDE.md`](../../CLAUDE.md), [`/.planning/reference/action-authoring.md`](../../.planning/reference/action-authoring.md), [`/.planning/reference/actions-catalog.md`](../../.planning/reference/actions-catalog.md).

---

## 1. What this directory owns

The plugin-loadable extension surface of jvagent:

- **`Action` base** ([`base.py:48`](base.py)) — Node subclass with lifecycle hooks, attribute config, endpoint registration, child-cascade delete, tool exposure to the Executive's Skills center.
- **`InteractAction`** ([`interact/base.py:32`](interact/base.py)) — see `interact/CLAUDE.md`.
- **Specialized bases**: `BaseModelAction`, `LanguageModelAction`, `BaseWebSearchAction`, `BaseSTTAction`, `BaseTTSAction`, `VectorStore`.
- **Concrete plugins** organized by topic: language models, response/bus, Orchestrator, memory-related, channel adapters, productivity integrations, tasks. Catalog in [`/.planning/reference/actions-catalog.md`](../../.planning/reference/actions-catalog.md).
- **Loader/registry** in `loader/`.
- **Plugin contracts** in `plugin_contracts.py`.

---

## 2. Key files

| File | Purpose |
|---|---|
| `base.py:48` | `Action` base class (canonical) |
| `base.py:192` | `get_tools()` — for the Executive's Skills center tool registry |
| `base.py:180` | `get_capabilities()` — for PersonaAction prompt aggregation |
| `base.py:225` | `delete(cascade=True)` — walks outgoing edges and cascade-deletes children |
| `base.py:256-348` | Lifecycle hook contracts (`on_register`, `on_reload`, `post_register`, `on_startup`, `on_enable`, `on_disable`, `on_deregister`, `pulse`, `healthcheck`) |
| `base.py:354-460` | Endpoint discovery + unregistration (relies on `/actions/{action_id}/` path prefix) |
| `base.py:462-542` | Module unload safety (skips core + shared modules) |
| `base.py:710-852` | `get_action()` / `get_action_by_base_class()` / `get_model_action()` — cross-action lookup |
| `base.py:881-944` | Package metadata accessors (namespace, version, type) |
| `base.py:946-1100` | File storage helpers (action-scoped paths) |
| `interact/base.py:32` | `InteractAction` (see `interact/CLAUDE.md`) |
| `actions.py` | `Actions` manager node |
| `endpoints.py` | Top-level action HTTP routes (~9 routes) |
| `loader/` | Action loader, registry, plugin discovery |
| `plugin_contracts.py` | Plugin protocol definitions |
| `streaming.py` | Streaming response helpers |

---

## 3. Contracts (don't break)

1. **`Action` subclasses MUST set `archetype` in `info.yaml`** to match the Python class name. The loader uses it.
2. **Action endpoints MUST live under `/actions/{action_id}/...`** ([`base.py:373`](base.py)). Deregister scans this prefix; non-conforming endpoints leak after `on_deregister`.
3. **`get_action()` is `O(1)`; `get_action_by_base_class()` is `O(n)`.** Don't use the latter in hot paths.
4. **Lifecycle hooks MUST not swallow exceptions** ([`base.py:569+`](base.py)). The framework's `enable()`/`disable()`/`reload()` wrappers log errors automatically with the action context — silencing them hides bugs.
5. **`Action.metadata` is owned by the loader.** Mutations to it are not persisted across restarts. Use `attribute(...)` fields for persistent state.
6. **Child Nodes attached via outgoing edges are cascade-deleted** when the action is deleted ([`base.py:225`](base.py)). Always connect via `await self.connect(child, direction="out")`.
7. **`is_singleton` default is `True`** ([`base.py:221`](base.py)). Override `config.singleton: false` in `info.yaml` if multiple instances per agent are allowed.

---

## 4. The four-file pattern

Every action package MUST contain:

```
{namespace}/{action_name}/
├── __init__.py              # exports class + imports endpoints (for @endpoint registration)
├── {action_name}.py         # Action subclass
├── endpoints.py             # @endpoint-decorated routes
└── info.yaml                # package metadata
```

Skeleton and full templates: [`/.planning/reference/action-authoring.md`](../../.planning/reference/action-authoring.md).

---

## 5. Cross-action lookup table

| Need | Method | Cost |
|---|---|---|
| One specific class | `await self.get_action(MyActionClass)` | O(1) (cached index) |
| Class by name string | `await self.get_action("MyActionClass")` | O(1) |
| Any subclass of a base | `await self.get_action_by_base_class(Base)` | O(n) isinstance scan |
| Any LM provider | `await self.get_model_action(required=True)` | O(1) if `model_action_type` set, else falls back to base scan |
| The agent | `await self.get_agent()` | Cached |
| The App | `await self.get_app()` | Cached singleton |

---

## 6. Tests

- `tests/action/{name}/` per-action tests.
- `tests/action/test_action_loader.py` — plugin loading.
- `tests/action/test_action_endpoints.py` — endpoint discovery.
- `tests/action/test_plugin_system.py` — plugin contracts.
- `tests/test_tool_schema_audit.py` — tool schema sanity.

```bash
pytest tests/action/ -v
```

---

## 7. Adding a new Action

The detailed walkthrough lives at [`/.planning/reference/action-authoring.md`](../../.planning/reference/action-authoring.md) and the runbook at [`/.planning/runbooks/add-action.md`](../../.planning/runbooks/add-action.md). Short version:

1. Pick base class (Action / InteractAction / specialized).
2. Choose namespace (`jvagent/` for core, otherwise `contrib/` or `custom/`).
3. Create the 4-file directory.
4. Define `attribute(...)` fields; implement lifecycle hooks + `execute` (if InteractAction) + `get_tools()` (if exposed as a tool).
5. Wire endpoints under `/actions/{action_id}/...`.
6. Add tests under `tests/action/{name}/`.
7. Update [`actions-catalog.md`](../../.planning/reference/actions-catalog.md).

---

## 8. Traps specific to action/

| Trap | Fix |
|---|---|
| Missing `from . import endpoints` in `__init__.py` | Add it; otherwise routes don't register |
| Class name doesn't match `archetype` in info.yaml | Loader silently skips the package |
| Heavy work in `__init__` | Use lifecycle hooks (`on_register`, `on_enable`) instead |
| Forgetting `@compound_index` when adding a queried field | Slow queries at scale |
| Writing to `self.metadata` | Not persisted; use `attribute(...)` |
| Endpoints not under `/actions/{action_id}/` | Deregister leaks them |
| Recursive `await self.get_action(MyAction)` calls | OK (cache returns same instance) but expensive isinstance walks aren't |

---

## 9. Subdirectory pointers

| Subdir | Local guide |
|---|---|
| `interact/` | [`interact/CLAUDE.md`](interact/CLAUDE.md) |
| `interview_action/` | [`interview_action/CLAUDE.md`](interview_action/CLAUDE.md) |
| `orchestrator/` | (see [`/.planning/adr/0012-skill-executive-architecture.md`](../../.planning/adr/0012-skill-executive-architecture.md)) |
| All other action dirs | Per-package `info.yaml` + class docstring |

---

## 10. Out of scope here

- Walker mechanics: see `interact/CLAUDE.md`.
- Executive prompt/loop specifics: see [`/docs/ORCHESTRATOR.md`](../../docs/ORCHESTRATOR.md).
- Memory graph: see `jvagent/memory/CLAUDE.md`.
