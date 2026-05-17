# jvspatial Integration

> jvagent is built on top of [jvspatial](https://github.com/TrueSelph/jvspatial) — an async, entity-centric, object-spatial graph framework. This doc describes the boundary jvagent crosses into jvspatial, includes an inline concept summary so an agent need not read jvspatial source for common questions, and pins version policy.

---

## 1. Where jvspatial lives

- **Source**: `/Users/eldonmarks/Briefcase/dev/jv/jvspatial` (sibling directory in this workspace).
- **Pip install**: declared in [`pyproject.toml`](../pyproject.toml) as `jvspatial>=0.0.7`. Tested against `0.0.8`.
- **Own docs**: jvspatial has its own [`README.md`](../../jvspatial/README.md) and [`SPEC.md`](../../jvspatial/SPEC.md). Treat those as authoritative for anything below.

---

## 2. Concept summary (inline so agents don't have to chase docs)

### 2.1 Entity hierarchy

```
Object  ── persistence-capable Pydantic-style base
  └── Node          ── graph node, has edges + visitor support
        ├── Edge    ── relationship between two Nodes
        └── Walker  ── traverses a graph, visits Nodes
              └── Root  ── singleton Node, anchor for everything
```

- `Object` (`jvspatial/core/entities/object.py:19`) — base persistence-capable class with id, entity type, graph context. All entity types inherit. Pydantic-aware.
- `Node` (`jvspatial/core/entities/node.py:34`) — graph node. Holds `edge_ids: List[str]`, optional `visitor`, `@on_visit` hook registration. Subclass for graph entities.
- `Edge` (`jvspatial/core/entities/edge.py:29`) — relationship. Has `source`/`target` Node IDs. Directional or bidirectional.
- `Walker` (`jvspatial/core/entities/walker.py:83`) — traversal agent. Visit queue + trail. Built-in protection: `max_steps=10000`, `max_visits_per_node=100`, `max_execution_time=300s`, `max_queue_size=1000`.
- `Root` (`jvspatial/core/entities/root.py:11`) — singleton; id fixed at `"n.Root.root"`. Created once.

### 2.2 Walker API (read by every InteractAction author)

| Method | Signature | Purpose |
|---|---|---|
| `visit(nodes)` | `async def visit(nodes) -> list` | Enqueue nodes for later traversal |
| `spawn(node)` | `async def spawn(...)` | Visit immediately; run on_visit hooks |
| `visiting(node)` | `async def visiting(...)` | Context manager: records trail, enforces protection |
| `prepend(nodes)` | `async def prepend(nodes)` | Head-enqueue (next visit) — **cockpit uses this for revisit** |
| `dequeue(nodes)` | `async def dequeue(nodes)` | Remove from queue |
| `here` (property) | `→ Optional[Node]` | Current node |

Walker also tracks: `step_count`, `node_visit_counts`, `get_trail()`, `is_visited(node)`.

### 2.3 Annotations

```python
from jvspatial.core.annotations import attribute, compound_index, on_visit

class MyNode(Node):
    name: str = attribute(
        indexed=True,
        default="",
        description="...",
        protected=False,  # if True, blocks bulk YAML overwrites
        private=False,    # if True, transient (not persisted)
    )
```

- `@attribute(...)` — declares a typed persisted field with default, indexing, validation hints.
- `@compound_index([(...)])` — DB-level compound index declaration (e.g., `jvagent/action/base.py:37-47`).
- `@on_visit(WalkerType)` — registers a Node method as a visit callback for a specific walker class.

### 2.4 Endpoints

```python
from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response

@endpoint("/actions/{action_id}/foo", methods=["POST"], auth=True, roles=["admin"])
async def my_handler(...): ...
```

- `@endpoint(...)` registers an HTTP route on the active `Server` (FastAPI under the hood).
- `auth=True` enforces JWT auth via jvspatial middleware.
- `roles=[...]` restricts to those roles.
- Endpoints inside an action package's `endpoints.py` are auto-discovered at action register time.

### 2.5 Persistence

jvspatial supports four backends, selected via env vars:

| Backend | Use case | Env |
|---|---|---|
| **JSON** | Dev, demos, tests | `JVSPATIAL_DB_TYPE=json`, `JVSPATIAL_JSONDB_PATH=./jvdb/dev` |
| **SQLite** | Single-process serverless / embedded | `JVSPATIAL_DB_TYPE=sqlite` |
| **MongoDB** | Production, multi-process | `JVSPATIAL_DB_TYPE=mongodb`, `JVSPATIAL_MONGODB_URI`, `JVSPATIAL_MONGODB_DB_NAME` |
| **DynamoDB** | AWS Lambda / serverless | `JVSPATIAL_DB_TYPE=dynamodb`, table + AWS creds |

CRUD via entity methods (no separate ORM):
```python
node = await MyNode.create(field="value")     # Auto-persisted, cached
node = await MyNode.get(node_id)              # Cached by ID
node.field = "new"
await node.save()                              # Required after property mutation
results = await MyNode.find({"context.x": 1}) # Mongo-style query
```

`Object.count()` does not exist — use `len(await Entity.find(query))` (jvspatial SPEC.md). For high-cardinality counts, design indexes accordingly.

### 2.6 Context

```python
from jvspatial.core.context import get_default_context
ctx = get_default_context()  # DB context resolved from server/env
```

The `Server` instance auto-wires the default DB context for the request lifecycle. Outside HTTP (CLI scripts, scheduled jobs), use `GraphContext(database=...)` explicitly.

### 2.7 Errors

```python
from jvspatial.api.exceptions import ValidationError, ResourceNotFoundError
```

- `ValidationError` — schema / business-rule failures. Bubble up; the FastAPI handler returns 400.
- `ResourceNotFoundError` — missing entity. Returns 404.

### 2.8 Performance helpers

- `DeferredSaveMixin` — batches writes. Used by `Conversation` and `Interaction` ([`memory/conversation.py:1-20`](../jvagent/memory/conversation.py)).
- `ObjectPager` — cursor-based pagination.

---

## 3. What jvagent uses from jvspatial

Top imports by frequency across the jvagent codebase:

| Symbol | Uses | Imported as |
|---|---:|---|
| `attribute` | 87 | `from jvspatial.core.annotations import attribute` |
| `ValidationError` | 45 | `from jvspatial.api.exceptions import ValidationError` |
| `endpoint` | 41 | `from jvspatial.api import endpoint` |
| `env` | 37 | `from jvspatial.env import env` |
| `Node` | 37 | `from jvspatial.core import Node` |
| `ResourceNotFoundError` | 36 | `from jvspatial.api.exceptions import ResourceNotFoundError` |
| `success_response` | 33 | `from jvspatial.api.endpoints.response import success_response` |
| `ResponseField` | 33 | `from jvspatial.api.endpoints.response import ResponseField` |
| `get_default_context` | 23 | `from jvspatial.core.context import get_default_context` |
| `create_task` | 14 | `from jvspatial import create_task` |

Other recurring imports: `Root`, `compound_index`, `on_visit`, `APIRoutes`, `get_current_server`, `create_storage`, `get_proxy_manager`, `resolve_file_storage_root`.

---

## 4. The boundary

jvagent **uses** jvspatial. jvagent **does not** modify jvspatial. If a behavior must change at the framework level, file an issue against jvspatial.

Things to **leave to jvspatial**:
- Database adapter implementation, retries, transactions.
- HTTP server, auth middleware, JWT handling.
- Walker traversal protection (`max_steps`, `max_visits_per_node`, `max_execution_time`).
- Storage abstractions (local FS, S3).
- File storage URL proxy manager.

Things jvagent **owns**:
- The `Action` / `InteractAction` contract.
- The `App` / `Agent` / `Agents` / `Memory` / `User` / `Conversation` / `Interaction` node types.
- The interact subsystem (`InteractWalker`, response bus, channel adapters).
- The cockpit (router + engine + walker-revisit pattern).
- Action discovery, plugin loading, namespace resolution.
- Per-interaction observability + interaction-level logging.

---

## 5. Version policy

- Minimum required jvspatial: pinned in [`pyproject.toml`](../pyproject.toml) as `jvspatial>=X.Y.Z`. Current: `>=0.0.7`.
- When jvspatial introduces breaking changes (e.g., walker API rename, persistence shape change), bump the pin and update this section.
- When adding a new dependency on a jvspatial feature, document the symbol + version it was introduced in. Helps downstream consumers know the floor.
- Rationale: [`adr/0006-jvspatial-dependency.md`](adr/0006-jvspatial-dependency.md).

---

## 6. Common pitfalls when crossing the boundary

| Trap | Mitigation |
|---|---|
| Forgetting `await self.save()` after mutating a Node property | jvspatial only persists `.create()` results automatically. Property changes need explicit `save()`. |
| Calling `Object.count(query)` | Does not exist on all entity types. Use `len(await Entity.find(query))`. |
| Using `entity.field = X` on a `protected=True` field | Blocked silently in some paths. Use the explicit setter (e.g., `set_app_update_mode`, [`app.py:537`](../jvagent/core/app.py)) — those use `object.__setattr__`. |
| Spawning a Walker outside an HTTP request | No default `GraphContext`. Wrap your job in `GraphContext(database=...)` or use jvspatial's `create_task`. |
| Caching jvspatial objects across event loops | Locks/contexts are per-loop. See `App._get_lock()` ([`app.py:97-117`](../jvagent/core/app.py)) for the pattern. |
| Long-running Walker without `await asyncio.sleep(0)` | The walker is synchronous-feeling but cooperatively scheduled. Yield occasionally for fairness. |

---

## 7. Reading list

If you need more depth than this doc, in order:

1. [jvspatial `README.md`](../../jvspatial/README.md) — quick start + concepts.
2. [jvspatial `SPEC.md`](../../jvspatial/SPEC.md) — entity-centric coding guide, includes the patterns the AI is expected to follow.
3. jvspatial `examples/` — runnable walkers, API examples.
4. Specific modules: `jvspatial/core/entities/walker.py:508+` (visit/spawn), `jvspatial/db/database.py:48+` (CRUD interface).
