# jvagent/action/interact/ — Agent Guide

> Local guide for the interaction subsystem. Cross-link: [`/.planning/SPEC.md`](../../../.planning/SPEC.md) §3, [`/.planning/architecture.md`](../../../.planning/architecture.md) §3.

---

## 1. What this directory owns

The HTTP-facing interaction pipeline:

- `InteractWalker` — the jvspatial Walker subclass that drives `/interact` traffic.
- `InteractAction` base — contract for executable actions in the pipeline.
- `endpoints.py` — `POST /agents/{id}/interact` and supporting routes.
- Walker payload bootstrap (User / Conversation / Interaction resolution).
- Background-action queueing and post-response execution.
- Access control enforcement per visit.

---

## 2. Key files

| File | Purpose |
|---|---|
| `base.py:32` | `InteractAction` abstract base |
| `base.py:64` | `weight` attribute (top-tier ordering) |
| `base.py:78` | `always_execute` flag |
| `base.py:88` | `run_in_background` flag |
| `base.py:99` | `anchors: List[str]` for routing |
| `base.py:108` | `parameters: List[Dict]` for behavioral guidance |
| `base.py:131-145` | `get_anchors(conversation)` — dynamic anchors override |
| `base.py:147-191` | `execute(visitor)` — abstract contract |
| `base.py:193-274` | `publish()` — direct write to response bus |
| `base.py:276-305` | `publish_thought()` — thought-category emit |
| `base.py:307-444` | `respond()` — generate via agent egress responder (ReplyAction / PersonaAction) |
| `interact_walker.py:38-48` | `InteractionInitResult` dataclass |
| `interact_walker.py:50-150` | `InteractWalker` core (state + properties) |
| `interact_walker.py:231` | `enforce_interact_action_access()` — access control |
| `interact_walker.py:277-450` | `_bootstrap_interaction()` — User / Conversation / Interaction resolve |
| `interact_walker.py:600-650` | `on_interact_action()` — per-visit callback |
| `endpoints.py:29-31` | `build_interact_response`, `create_sse_response`, `format_sse_chunk` |
| `endpoints.py:65-109` | `_run_background_actions()` post-response runner |
| `endpoints.py:174+` | `/interact` endpoint registration |

---

## 3. Contracts (don't break)

1. **`InteractAction.execute()` is the only entry point** the walker calls. Don't add side channels.
2. **Top-level actions order by `weight` ascending.** Sub-actions (connected to other InteractActions) do not — they run in graph order, only when the parent calls `await visitor.visit(child)`.
3. **`run_in_background=True` defers execution.** The walker queues the action; `_run_background_actions(walker)` fires it after response is sent. Each background action is isolated in try/except — failures must not propagate.
4. **`always_execute=True` bypasses routing exclusion** but does NOT bypass access control. The order is: access check → routing → execute.
5. **`execute()` is called inside a walker `visiting()` context** — `visitor.here` is set to the action node. Don't break that contract by mutating the walker queue from inside without using `visitor.visit()` / `visitor.prepend()`.
6. **`publish()` requires `visitor.response_bus` + `visitor.session_id`** ([`base.py:237-246`](base.py)). Early-return with a warning if either is missing.
7. **`publish()` with `stream=None` defaults to `visitor.stream`** ([`base.py:255`](base.py)). For non-streaming channels (WhatsApp), this is set False on the walker.
8. **Walker-revisit capability**: `visitor.prepend([self])` exists as a walker primitive — an action MAY enqueue itself (or another node) to be visited again. No shipped pattern currently relies on multi-visit turns; the Orchestrator runs its whole turn in a single `execute()` call (no revisit), carrying loop state locally inside `_run_loop`.

---

## 4. Response emission decision tree

```
Generated text from a model?
└─ Use respond() — goes through PersonaAction (polishes, applies parameters, persists)

Pre-built string (canned, system message, summary)?
├─ Visible to user → publish(content, stream=False)
└─ Internal trace (reasoning, plan)? → publish_thought(content, thought_type="reasoning")

Need direct write without persona polish or history?
└─ publish() with explicit channel/metadata
```

---

## 5. Background action contract

```python
class MyInteractAction(InteractAction):
    run_in_background: bool = attribute(default=True)

    async def execute(self, visitor):
        # This runs AFTER the user-facing response is sent.
        # visitor.interaction is closed by the time we run.
        # Failures here are caught — they don't impact the user response.
        ...
```

Implementation lives at `endpoints.py:65-109`. Each background action is wrapped:

```python
try:
    await action.execute(walker)
except Exception:
    logger.error(...)
```

Use for: analytics, model updates, follow-up emails, scheduled task creation.

---

## 6. Tests

- `tests/action/interact/` — walker + bootstrap unit tests.
- `tests/action/gating/` — access control + always_execute tests.
- `tests/action/test_interact_walker.py` — end-to-end visit semantics.

```bash
pytest tests/action/interact/ tests/action/gating/ -v
```

---

## 7. Traps specific to interact/

| Trap | Fix |
|---|---|
| Top-level `InteractAction` with children but no `visitor.visit(child)` call in `execute()` | Children never run. Explicitly route. |
| Calling `publish()` with `stream=True` on a non-streaming channel | Adapter mishandles. Pass `stream=False` or let visitor.stream propagate. |
| `await visitor.visit(self)` for re-visit | Cycle risk; walker may trip `max_visits_per_node=100`. If you must re-enqueue, use `visitor.prepend([self])` and persist state explicitly (no shipped pattern needs this — the Executive avoids re-visits entirely). |
| Setting `run_in_background=True` on an action that emits the user response | Response never reaches the client. Background = post-response only. |
| Long sleeps in `execute()` | Blocks the walker; latency spike. Use background or enqueue via `TaskMonitor` / `queue_task`. |
| Reading `visitor.interaction` in a background action | It's closed/saved by then — read-only, don't mutate. |
| Forgetting to call `await visitor.add_directives(...)` before `respond()` | Directives won't reach PersonaAction. |

---

## 8. Don't touch from outside interact/

- `InteractWalker._bootstrap_interaction()` semantics — they're tightly coupled to memory/.
- The order of pre-visit access control checks — bypassing creates security holes.
- Background-action try/except wrapping — without it, one failure cascades.

---

## 9. Out of scope here

- Memory graph mutation: see `jvagent/memory/CLAUDE.md`.
- Channel adapters: see `jvagent/action/response/`.
