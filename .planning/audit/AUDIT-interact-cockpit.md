# AUDIT — jvagent/action/interact/ + jvagent/action/cockpit/

**Date**: 2026-05-17
**Scope**: `jvagent/action/interact/` and `jvagent/action/cockpit/` (lifecycle, walker, router, engine, tools)
**Contract refs**: `.planning/SPEC.md` §3 / §3.3 / §7, `.planning/adr/0002-walker-revisit-cockpit.md`, `docs/COCKPIT.md`, `jvagent/action/interact/CLAUDE.md`, `jvagent/action/cockpit/CLAUDE.md`

---

## Summary

The interact pipeline + cockpit subsystem is the largest in the codebase and shows generally careful design: the walker contract is well-respected, the cockpit's walker-revisit pattern works as the ADR describes, access control is applied at multiple layers, and termination conditions are explicit. However, the audit surfaced several CRITICAL and HIGH issues that can degrade or break the contract:

1. A **TypeError-on-error**: the walker's exception handler at `interact_walker.py:678` passes a `details=` kwarg to the standard-library logger which **does not accept it** — meaning every InteractAction execution failure causes the *handler itself* to raise a second TypeError, swallowing the original exception's traceback and breaking the "continue to next action" contract documented in `interact/CLAUDE.md` §7.
2. **Silent dropping of routed nested InteractActions**: `InteractWalker.curate_walk_path` only re-prepends actions that were *already in the queue*; the router can resolve a nested IA via `get_all_actions(enabled_only=True)` (which recurses into sub-actions per `actions.py:533`), but the walker queue is populated only with top-tier IAs from `on_actions`. Nested routed IAs get dropped at curate with no warning.
3. **Cockpit revisit can silently no-op**: if `_phase_continue` is invoked but the session engine has been reset elsewhere, the cockpit logs a debug message and returns *without delivering any response* — the user gets nothing.
4. **`visitor.append` is bounded by `max_queue_size=1000`** and silently drops; the "IA-only" finalize step uses `append`, so under saturated queues the cockpit's final persona delivery never runs and the user sees no response.
5. **Cockpit tool error envelope can include raw `Error: {exc}` strings**: many harness tools return raw stringified exceptions in their return value (`memory_set`, `artifact_add`, `task_create_plan`, etc.). When the cockpit's all-errors short-circuit fires, `_emit_tool_error_thought` publishes the raw concatenation to the response bus as a `category="thought"`. Whether this reaches the SSE client depends on consumer behavior, but it routes through the same publish path as user content — bypassing the `sanitize_tool_errors` flag, which only governs unhandled exceptions inside `ToolExecutionEngine._dispatch_one`.
6. **Cockpit `max_iterations` not constrained relative to walker `max_visits_per_node=100`**: operators can set `max_iterations=200` via `agent.yaml`; the walker tripping at visit 100 raises `ProtectionViolation`, the cockpit catches it as a generic Exception, the trace task is finalized with a stale state, and there is no contract-level guard.

The cockpit also has multiple latent bugs around tool-name collisions, skill-tool caching across runs, stale `interpretation` on cached routing results, and a router cache that does not key on `user_id` (only `conversation_id`) — fine in normal use but breaks per-user differentiation if a single conversation can be re-used across principals.

---

## CRITICAL

### CRIT-01 — `logger.error(..., details=...)` raises TypeError, masks original exception

`jvagent/action/interact/interact_walker.py:675-693`

```python
except Exception as e:
    ...
    logger.error(
        f"Error processing InteractAction {here.label}: {e}",
        exc_info=True,
        details={
            "agent_id": agent_id,
            "interaction_id": interaction_id,
            ...
        },
    )
```

Python's stdlib `Logger.error` does not accept a `details=` keyword argument; the correct argument is `extra=`. Verified:

```
>>> logger.error('x', details={'a': 1})
TypeError: Logger._log() got an unexpected keyword argument 'details'
```

Effect: the **exception handler itself raises a TypeError**, suppressing the original exception and breaking the walker's "log and continue to next action" contract (per `interact/CLAUDE.md` §7 and SPEC §3.1). The TypeError propagates out of `on_interact_action`, the `finally` block still runs (commits pending adhoc), but the walker's outer loop sees a raised exception — depending on jvspatial's `@on_visit` dispatcher behavior, this may halt the walker entirely.

Fix surface: rename `details=` to `extra=` (and verify keys don't collide with `LogRecord` reserved fields like `name`, `message`, `args`).

### CRIT-02 — Cockpit revisit with stale engine silently drops the response

`jvagent/action/cockpit/cockpit_interact_action.py:494-501`

```python
async def _phase_continue(self, visitor):
    session = get_session(visitor)
    engine = session.engine
    if engine is None:
        logger.warning("CockpitInteractAction: revisit without engine, skipping")
        session.debug_state = None
        return
```

Trigger conditions:
- The previous step persisted state and prepended `self` for revisit.
- Between that prepend and the next visit, something cleared the engine (stale-state guard at `cockpit_interact_action.py:357-364` resets the session if `interaction_id` differs; an error handler called `clear_session`; a tool called `response_publish(finalize=True)` and `session.reset()` ran in the prior visit).

When `_phase_continue` sees `engine is None`, it returns **without calling `interaction.set_to_executed()`**, **without publishing any text**, **without unrecord_action_execution**. The user receives nothing for that turn. The walker continues to the next queued action (if any), but for an IA-only or skills-only cockpit the next action is the next IA in the curated queue, which has no idea the cockpit just no-op'd.

Fix surface: in the `engine is None` branch, publish a fallback message (or re-route through `_phase_route_and_setup`), and always mark the interaction executed so the bus finalize step runs.

### CRIT-03 — `curate_walk_path` silently drops routed InteractActions that are not in the walker queue

`jvagent/action/interact/interact_walker.py:721-769` (especially lines 746-748):

```python
actions_to_keep = [
    item for item in interact_actions_in_queue if item.id in actions_set
]
```

`curate_walk_path` only keeps an action if it was **already** in the walker queue. The cockpit's `resolve_routed_interact_actions` (`delivery/delegation.py:34-82`) calls `agent.get_actions_manager().get_all_actions(enabled_only=True)` which `actions.py:533` documents as **"including actions attached to actions (subactions). This recursively traverses the action graph."**

But the walker queue is populated by `InteractWalker.on_actions` (`interact_walker.py:567`):

```python
enabled_actions = await here.nodes(node=InteractAction, enabled=True)
```

That fetches only top-tier InteractActions (direct children of the `Actions` node). If the router LLM returns an `interact_actions: ["NestedFollowupAction"]` and that class is a **sub-InteractAction** (connected to a parent IA, not directly to `Actions`), the resolver finds it and includes it in `routed_ias`, but curate silently drops it because it was never in the queue. No warning, no report — the routed action just never runs.

Fix surface: either restrict `resolve_routed_interact_actions` to top-tier only (matching the walker queue), or have `curate_walk_path` `prepend` the missing actions instead of silently dropping. The current code's silent drop violates the interact subsystem's "explicit routing" contract.

### CRIT-04 — `visitor.append` for IA-only finalize step silently drops when queue is full

`jvagent/action/cockpit/cockpit_interact_action.py:457-472`

```python
if has_ias and not has_skills:
    ...
    session.ia_finalize_pending = True
    try:
        await visitor.append([self])
    except Exception as exc:
        logger.warning(
            "CockpitInteractAction: failed to append finalize step: %s",
            exc,
        )
    # Don't mark interaction executed yet — finalize step will do it.
    return
```

`Walker.append` → `WalkerQueue.append` at `jvspatial/.../walker_queue.py:82-85`:

```python
for n in nodes:
    if self._max_size <= 0 or len(self._backing) < self._max_size:
        self._backing.append(n)
```

**Silent drop** when `len(self._backing) >= max_size` (default 1000). The cockpit's try/except cannot detect this — it raises nothing. With `ia_finalize_pending=True` and no cockpit re-queue, the upstream IAs run, accumulate directives, and the cockpit's persona-delivery finalize step never executes — the user gets no response.

Probability today is low (queues rarely reach 1000), but the contract (`interact/CLAUDE.md` §3 ¶8 "Walker-revisit pattern") states finalize is guaranteed when `ia_finalize_pending` is set.

Fix surface: use `visitor.prepend` (unbounded) or check `len(await visitor.get_queue()) < max_queue_size - 1` before appending, with a fallback path.

### CRIT-05 — Harness-tool error messages leak through `_emit_tool_error_thought`

`jvagent/action/cockpit/engine.py:301-313` + `engine.py:1185-1212`

When every tool call in a batch errors, the engine builds:

```python
error_details = "\n".join(
    f"- {tc.get('function', {}).get('name', '?')}: {getattr(tr, 'content', '')[:200]}"
    for tc, tr in zip(result.tool_calls, tool_results)
)
await self._emit_tool_error_thought(error_details)
```

and publishes it via `ctx.response_bus.publish(..., category="thought", thought_type="tool_error")`.

Two problems:

1. **`sanitize_tool_errors` is bypassed for tool-return strings**. The flag at `tool_executor.py:201-207` only governs unhandled exceptions (`except Exception:` branch). Many harness tools handle their own errors and **return** the exception string directly:
   - `memory.py:247` `return f"Error saving memory: {exc}"`
   - `memory.py:301` `return f"Error saving memory: {exc}"` (append)
   - `artifact.py:161` `return f"Error saving artifact: {exc}"`
   - `task.py:107` `return f"Error creating plan: {exc}"`
   - `task.py:146` `return f"Error updating step: {exc}"`
   - `conversation.py:26` `return f"Error: {exc}"`
   - And many MCP / skill / action tools that bubble provider-side error bodies.

   These error strings flow straight into `error_details` and then onto the response bus.

2. **Thoughts go through the same SSE channel as user content**. The stream handler at `endpoints.py:919-921`:

   ```python
   yield format_sse_chunk({"type": "message", "message": message.to_dict()})
   ```

   yields every published message. A poorly-implemented SSE consumer that doesn't filter on `category="thought"` would show "Tool batch failed:\n- memory_set: Error saving memory: connection refused 127.0.0.1:6379" verbatim to the user — leaking internal hostnames, IPs, partial credentials, etc.

Fix surface: when `sanitize_tool_errors=True`, replace the per-tool-result content in `error_details` with a generic placeholder; or strictly gate emission of `tool_error` thoughts behind a separate `expose_tool_errors_in_stream` config flag that defaults to False in production.

---

## HIGH

### HIGH-01 — `_run_background_actions` can never fail-open: `enforce_interact_action_access` exception swallowed silently

`jvagent/action/interact/endpoints.py:78-109`

```python
for action in walker.background_actions:
    try:
        ...
        if not await walker.enforce_interact_action_access(action, stage="background"):
            continue
        ...
        await action.execute(walker)
    except Exception as e:
        ...
```

`enforce_interact_action_access` itself wraps `access_control.has_action_access` in cockpit/registry/access.py with `try/except → return False` (fail closed). But this background loop's outer try/except wraps the access check AND the execute call together. If `enforce_interact_action_access` itself raises an unexpected exception (e.g. `has_action_access` throws a non-Exception subclass, or `_apply_access_denied_to_interaction` raises while saving), the outer `except Exception` will treat it as an execute failure and `continue` — the background action is *neither denied nor run*. No metric, no report; just silent skipping.

The bigger issue: `enforce_interact_action_access` calls `interaction.save()` and `self.report()` — both have I/O. In the background phase, `walker.interaction` has already been closed and saved (`interaction.close_interaction()` ran before background actions fire). Calling `.save()` on a closed interaction may have unintended side effects depending on the memory backend; at minimum it bypasses the `set_interaction(None)` clearing that happened just before.

Fix surface: split the access check from the execute, and have the access check have its own narrow try/except logging "access_check_error" distinctly from "execute_error".

### HIGH-02 — Cockpit revisit chain has no per-interaction iteration cap; only engine-internal `_iteration` counter

`jvagent/action/cockpit/cockpit_interact_action.py:494-513` + `engine.py:172-219`

`max_iterations` (default 25) is enforced against `self._iteration` *on the engine instance*. But:

1. The engine is created in `_start_cockpit` and persisted on `session.engine`. The stale-state guard at `cockpit_interact_action.py:357-364` resets the session (clearing the engine) when `interaction_id` mismatches.
2. If the stale-state guard mis-fires inside a single interaction (e.g. `interaction.id` mutated between visits, which shouldn't happen but isn't asserted), a new engine is constructed → `self._iteration` resets to 0 → another 25 iterations allowed.
3. The walker's `max_visits_per_node=100` is the only ultimate backstop, but it raises `ProtectionViolation` — uncaught and unhandled by cockpit, propagating up to `on_interact_action` and into its broken `details=` logger call (CRIT-01).

The interaction-level invariant "no more than N model calls per turn" is not actually enforced — only "no more than N model calls per engine instance per turn." Add a hard counter on the interaction itself, or on `CockpitSession`, separate from the engine's `_iteration`.

### HIGH-03 — `tools/skill.py::_search_skills` calls `catalog.search().items()` but `search()` returns a string

`jvagent/action/cockpit/tools/skill.py:35-51`

```python
async def _search_skills(query: str) -> str:
    ...
    try:
        results = catalog.search(query)
        if not results:
            return f'No skills found matching "{query}".'
        lines = [f'Skills matching "{query}":']
        for name, data in results.items():
            ...
```

But `SkillCatalog.search` returns a **string** (a pre-formatted multi-line list), not a dict — see `catalog/skill_catalog.py:152-177`. The function ends with:

```python
return "\n".join(lines)
```

So `results.items()` raises `AttributeError: 'str' object has no attribute 'items'`. Caught by the outer `except Exception as exc: return f"Error searching skills: {exc}"`, so the model never gets the actual search results — only "Error searching skills: 'str' object has no attribute 'items'". The harness tool `skill_search` is effectively broken.

Fix surface: rewrite `_search_skills` to either consume the string directly (return it) or call a different SkillCatalog method that returns a dict.

### HIGH-04 — Router cache key does not include user_id

`jvagent/action/cockpit/routing/router.py:421-460` + `jvagent/core/cache.py:261-280`

```python
def router_cache_key(
    conversation_id, utterance, last_interaction_ids,
    buffer_fingerprint, active_task_fingerprint, ...
) -> str:
```

No `user_id` in the key. Today this is fine because a Conversation is uniquely keyed to one User (SPEC §2.1: "Exactly one `User` per `(memory_id, user_id)`"; `Conversation` per `session_id` per user). But:

1. A test fixture or admin tool that re-uses a `conversation_id` across users could cross-contaminate routing.
2. Future refactoring that allows conversation sharing (group chat) would silently leak routing decisions across users — and routing decisions include `interpretation` text which can be highly user-specific.
3. The `active_task_fingerprint` is per-conversation but doesn't include user state, so per-user permission contexts (e.g. tasks in flight that admin sees but regular user does not) wouldn't disambiguate cache entries.

Fix surface: add `user_id` to `router_cache_key` signature and to the hash payload.

### HIGH-05 — `CockpitInteractAction.execute` does not guard against concurrent revisits

`jvagent/action/cockpit/cockpit_interact_action.py:314-372`

The session object is shared across walker visits, but there is no lock or atomic check around the `session.engine is None / not None` transition. If two coroutines race (which can happen with the streaming endpoint's `walk_task` + concurrent `message_callback` work), the second one might observe the engine as set, call `step()`, and dispatch tools in parallel with the first.

In practice the walker dispatches via `@on_visit` which is serial per visit. But the cockpit's `_phase_continue` runs inside that same coroutine, and any `await` inside `engine.step()` yields control — meaning concurrent tasks scheduled on the same event loop can interleave. The risk: `session.finalized` set by `response_publish` in concurrent tool dispatch may be observed mid-execution and cause incorrect termination.

Fix surface: add an `asyncio.Lock()` on `CockpitSession` that `_phase_continue` acquires before reading `session.engine`.

### HIGH-06 — `__init__.py` import of `InteractWalker` at module load time can fail silently

`jvagent/action/interact/base.py:23-29`

```python
try:
    from jvagent.action.interact.interact_walker import InteractWalker
except ImportError:
    # If import fails, we'll use string matching in walker
    InteractWalker = None  # type: ignore
```

The comment claims "we'll use string matching in walker" but no such fallback exists. If `InteractWalker` fails to import (rare but possible during partial reloads / circular deps), the `@on_visit(InteractAction)` decorator at `interact_walker.py:599` may or may not work — `InteractAction` is what `on_visit` receives, not `InteractWalker`. So this defensive import isn't load-bearing; it's misleading dead code. But the silent `InteractWalker = None` could be invoked elsewhere via the `if TYPE_CHECKING` guard at line 21 — and then `isinstance(x, InteractWalker)` would raise `TypeError: isinstance() arg 2 must be a type`.

Fix surface: remove the dead fallback or replace the `try/except` with explicit failure (the dependency is required).

### HIGH-07 — `RoutingResult.from_dict` accepts arbitrary intent_type without normalization in cached path

`jvagent/action/cockpit/routing/router.py:462-486` (`_restore_cached_routing_result`)

The cache write at line 311 stores `result.to_dict()`. The cached `intent_type` is whatever the LLM returned (e.g. "DIRECTIVE"). On cache read, `RoutingResult.from_dict` calls `_normalize_intent_type` which validates against `INTENT_TYPES = ["CONVERSATIONAL", "INFORMATIONAL", "DIRECTIVE", "INTERACTIVE", "UNCLEAR"]`. So far OK.

But the cache stores `result.to_dict()` AFTER the post-parse mutation at `router.py:297-304`:

```python
if (
    result.intent_type == "CONVERSATIONAL"
    and not result.actions
    and not result.interact_actions
):
    for converse_name in CONVERSE_SKILL_NAMES:
        if converse_name in skill_descriptors:
            result.actions = [converse_name]
            break
```

i.e., the cached result has `intent_type=CONVERSATIONAL` AND `actions=["converse"]`. But `parse_routing_response` (in `types.py:264-273`) enforces the inverse: CONVERSATIONAL intent CLEARS actions. So when the cache restoration calls `from_dict(cached, ...)`, the `parse_routing_response` path's CONVERSATIONAL-clears-actions invariant is NOT re-applied — but `from_dict` doesn't re-apply it either; it just rebuilds the dataclass fields. So the cached state with `intent=CONVERSATIONAL` + `actions=["converse"]` is restored as-is. This is actually the desired post-injection state, so it works. But the invariant is fragile: if `parse_routing_response`'s CONVERSATIONAL-rule is updated to also clear `["converse"]`, the cache hit and cache miss paths will diverge.

Fix surface: either explicitly apply the converse-injection logic inside `_restore_cached_routing_result` so cache hits and misses follow the same code path, or make the invariant enforcement happen in `from_dict` so both paths automatically agree.

### HIGH-08 — Always-execute IAs are not filtered by per-user access control before being prepended

`jvagent/action/cockpit/cockpit_interact_action.py:421-429`

```python
always_run_ias = await collect_always_execute_interact_actions(
    agent, exclude_class_names={self.__class__.__name__}
)
await curate_walk_path_for_cockpit(
    visitor, self, routed_ias, always_execute=always_run_ias,
)
```

`collect_always_execute_interact_actions` does NOT call `filter_routed_interact_actions_by_access`. So denied always-execute IAs get into the curated walker queue. They're later denied at the walker's `enforce_interact_action_access` per-visit, but during the window between curate and visit, observability data shows them as "queued for execution" — and the deny event is the only signal they're not running. More importantly, the access-denied-directive (`deny_access_directive`) is added to the interaction every time, which is correct *if denied* but spammy if every request triggers the same deny path.

Fix surface: filter `always_run_ias` through `filter_routed_interact_actions_by_access` in `_phase_route_and_setup` for consistency.

### HIGH-09 — `_emit_tool_call` / `_emit_tool_result` only dedupe within engine lifetime, not across engine restarts

`jvagent/action/cockpit/engine.py:1375-1448` + `engine.py:1450-1522`

The `_emitted_envelopes` set lives on the engine instance. If the cockpit's stale-state guard resets the session and a new engine is constructed for the same interaction (HIGH-02), envelopes for the SAME tool_call_id can be emitted twice (once per engine instance). That breaks the SPEC §7.3 idempotency guarantee documented in the code's own comment ("a single logical envelope is never emitted twice — even if … walker re-walks, retry paths, parallel tasks, etc.").

Fix surface: move `_emitted_envelopes` onto `CockpitSession` so the dedupe set survives engine resets within a single interaction.

### HIGH-10 — `parse_routing_response`'s JSON extraction can produce truncated payloads

`jvagent/action/cockpit/routing/types.py:226-279`

```python
if "{" in json_str:
    start = json_str.find("{")
    depth = 0
    end = start
    for i, char in enumerate(json_str[start:], start):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    json_str = json_str[start:end]
```

This brace-counting scanner ignores string literals — if any JSON value contains a `}` character (e.g. `"interpretation": "user said \"check this } out\""`), the scanner exits early, producing truncated invalid JSON. The `try: data = json.loads(json_str)` falls through to the `JSONDecodeError` branch, returning `RoutingResult.error_result` — which then re-routes to `POSTURE_RESPOND` with `needs_clarification=True`. This is graceful degradation, not a crash, but the router silently mis-classifies utterances containing `}` in their text.

Fix surface: use a JSON parser that handles strings correctly (e.g., `json.JSONDecoder().raw_decode`) or strip code-block fences only without manual bracket-counting.

### HIGH-11 — Action tool registration in cockpit doesn't check for `action__` prefix; can collide with skill prefix

`jvagent/action/cockpit/registry/assembler.py:213-231` + `tools/`

```python
async def _register_action_tools(registry, ctx):
    ...
    all_tools = await actions_mgr.get_all_tools()
    for tool in all_tools:
        registry.register(tool, prefix="action")
```

The registry uses `prefix="action"`. Skill tools at line 382: `registry.register(tool, prefix=safe_name)` where `safe_name = skill_name.replace("-", "_")`. If a skill is named `action` (or `action_x`), the resulting prefix is `action__{tool_name}` — same shape as `action__{action_tool_name}`. The skill author has no path to know that `action` is reserved; the collision triggers a registry error from `register()` (depending on its dedupe semantics) or silently shadows the action tool. The cockpit/CLAUDE.md §5 says "Action tools … action__{action_name}__{tool}" but skill tools use `{skill_name}__{tool_name}` — the action layer adds an extra `__action_name__` segment that the skill layer doesn't, so the collision is in theory bounded, but skill names `action__something` would collide.

Fix surface: reserve the `action` namespace explicitly in the SkillCatalog discovery (skip skills with that name and warn).

### HIGH-12 — `enable_interact_router_cache=False` means caches written previously are kept stale

`jvagent/action/cockpit/routing/router.py:205-219`

The `caller_enabled` arg gates BOTH read and write. If the operator toggles `enable_interact_router_cache=True → False` during runtime (e.g. via SIGHUP-style config reload), the in-process cache `cache_manager._router_cache` is never cleared — its entries are inert (won't be read), but stay in memory. Not a security issue, but a memory leak under config churn. Also: there's no API to manually purge the router cache for an operator who's debugging a routing bug.

Fix surface: clear router cache on `enable_interact_router_cache=True → False` transition; expose an admin endpoint `POST /admin/cache/router/clear` for ad-hoc purge.

---

## MEDIUM

### MED-01 — Background actions never see `relay_to_adapters=False` overridden by channel

`jvagent/action/interact/endpoints.py:65-109`

Background actions run after `interaction.close_interaction()`. If a background action calls `publish_thought` with `relay_to_adapters=True`, the thought is relayed to channel adapters (WhatsApp, etc.) — possibly **after the user has already received the final response**. Race condition: out-of-order delivery on WhatsApp where the thought arrives after the "final" message.

Fix surface: in `_run_background_actions`, set `walker.stream = False` and ensure `relay_to_adapters` defaults to False for any publish in this phase.

### MED-02 — `_run_background_actions` re-uses the same walker but the `walker.interaction` is closed

`jvagent/action/interact/endpoints.py:91-93`

```python
walker._current_action = action
walker._skip_current_action_record = False
await action.execute(walker)
```

A background action calling `visitor.add_directive(...)` would mutate a closed interaction and `await interaction.save()`. Saves may fail or no-op depending on the memory backend. Per `interact/CLAUDE.md` §7: "Reading `visitor.interaction` in a background action — It's closed/saved by then — read-only, don't mutate." This is documented as a trap but not enforced. A background action that mutates state has no warning.

Fix surface: wrap `visitor.interaction` with a read-only proxy during the background phase, raising on mutation.

### MED-03 — Cockpit `_handle_step_result` does not `set_to_executed` when `final_response` is empty

`jvagent/action/cockpit/cockpit_interact_action.py:683-708`

```python
final_response = getattr(step_result, "final_response", "") or ""

if final_response.strip():
    ...
    await deliver_final_response(...)
```

If `final_response` is empty (e.g. the prepare_* short-circuit at engine.py:371 returns `final_response=""`), the code falls through to the function end without delivering anything. `interaction.set_to_executed()` was already called at line 681. But no response is published, no thoughts, nothing — the interaction is "complete" but yielded no output. The `prepare_*` comment says the staged-change card *is* the response, but there's no assertion that any tool actually published a card. If the prepare_* tool failed to call `response_publish` (model bug, tool bug), the user sees nothing.

Fix surface: when terminating with empty `final_response` and no preceding publish in this iteration, publish a fallback message ("Your request is being processed" or similar) so the SSE stream isn't empty.

### MED-04 — `delivery/helpers.py::deliver_final_response` early-returns on whitespace-only final response

`jvagent/action/cockpit/delivery/helpers.py:65-79`

```python
if not final_response or not final_response.strip():
    return
```

Silent return — no fallback, no warning, no metric. If the model returns `"   \n   "` as its final text, the interaction is left without a response. Combined with MED-03, this is a class of silent failures.

Fix surface: emit a structured warning log when this branch is hit; publish a fallback.

### MED-05 — Skill module cache (`_SKILL_MODULE_CACHE`) is process-wide but unbounded

`jvagent/action/cockpit/registry/assembler.py:430-431`

```python
_SKILL_MODULE_CACHE: Dict[Tuple[str, float], _CachedSkillModule] = {}
_SKILL_MODULE_CACHE_LOCK = threading.Lock()
```

The cache key is `(absolute_path, mtime)` — so when a skill file is edited, a new entry is added but the old one stays. Over time with many edits, this grows. Documented as ~1KB per entry and typical agents have <100 files, but a hot-reload-heavy dev workflow could accumulate.

Fix surface: add a max-size and an LRU eviction.

### MED-06 — `SkillCatalog._cache` LRU eviction is by timestamp, not access frequency

`jvagent/action/cockpit/catalog/skill_catalog.py:287-291`

```python
if len(cls._cache) >= cls._CACHE_MAX_ENTRIES:
    oldest_key = min(cls._cache, key=lambda k: cls._cache[k][1])
    del cls._cache[oldest_key]
```

`oldest_key` is by `cached_at` time, not last-accessed time. Frequently-accessed but old entries get evicted before never-accessed but recently-cached ones. For 200-agent deployments with mixed access patterns, this hurts.

Fix surface: track `last_accessed` timestamp and evict on that.

### MED-07 — `_check_stuck` Jaccard fires on multi-tool batches with disjoint args

`jvagent/action/cockpit/engine.py:514-530`

The Jaccard similarity is computed on **sets** of signatures. If the engine calls `[memory_set(k=a), memory_set(k=b)]` then `[memory_set(k=c), memory_set(k=d)]`, the signature sets share zero elements (different `args` hash) — jaccard=0, no false positive. Good.

But: if the engine calls `[search(q=foo), parse(q=foo)]` for two consecutive iterations with the same `q`, the signature sets are identical → jaccard=1.0 → stuck. But this could be a legitimate two-step pipeline that the model intentionally repeats with refined `q` per iteration. The check needs N iterations of similarity (`all_similar = True` requires every adjacent pair). For 4-window, that's 4 consecutive identical batches — usually legitimately stuck. OK in practice; flagging because the threshold is sensitive to skill design patterns.

Fix surface: when jaccard fires, log the signatures explicitly so skill authors can debug; consider per-skill jaccard exemption metadata.

### MED-08 — `format_interaction_history` ignores entries that aren't dict/str

`jvagent/action/cockpit/routing/types.py:340-389`

Last-resort branch: `elif isinstance(entry, str): lines.append(entry)`. Any other entry type (e.g. an int from a malformed history) is silently dropped. For routing accuracy this is minor, but the history might be missing a key turn.

Fix surface: log unexpected entry types at DEBUG level.

### MED-09 — `_render_user_identity_block` checks both `display_name` and `name` but only one prevails

`jvagent/action/cockpit/engine.py:736-785`

When `display_name == canonical_name`, the "Canonical name:" line is suppressed (good). But the check uses `display_name and canonical_name and display_name != canonical_name` — meaning if BOTH are set and DIFFERENT, both lines appear. If only `display_name` is set, just one line. OK.

But: `chosen = display_name or canonical_name` — if `display_name=""` and `canonical_name="X"`, `chosen="X"`. The prompt then says "Preferred name: X" — but X is the canonical, not preferred. The model is told to address by preferred name, but is given the canonical. Minor naming inconsistency.

Fix surface: rename `chosen` to `effective_name` and clarify the prompt text.

### MED-10 — `_emit_tool_progress` and `_emit_tool_call` both publish with the same `segment_id` — possible consumer confusion

`jvagent/action/cockpit/engine.py:1303-1347` and `engine.py:1375-1448`

Both use `segment_id=tool_call_id or f"iter{iteration}-{tool_name}"`. The code comment at 1340-1347 says this is intentional (so consumers can dedupe), but they have different `thought_type` (`tool_progress` vs `tool_call`/`tool_result`). A consumer that groups by `segment_id` alone would conflate the structured envelope with the one-line summary.

Fix surface: document the segment_id/thought_type pairing contract more clearly; consider distinct segment_id suffixes.

### MED-11 — Rate limiter is keyed by IP+agent_id only, no per-user-id rate limit

`jvagent/action/interact/rate_limiter.py:58-75`

```python
key = f"{ip}:{agent_id}"
```

No `user_id` in the key. A shared-IP deployment (corporate proxy, mobile carrier NAT) means one user's request count exhausts the limit for everyone behind the same IP. The SPEC mentions rate limiting once at §11.

Fix surface: optionally include user_id in the key when authenticated; expose `JVAGENT_INTERACT_RATE_LIMIT_BY` config (ip|user|ip_and_user).

### MED-12 — `extract_client_ip` trusts `X-Forwarded-For` first hop blindly

`jvagent/action/interact/rate_limiter.py:177-218`

```python
x_forwarded_for = request.headers.get("x-forwarded-for")
if x_forwarded_for:
    ip = x_forwarded_for.split(",")[0].strip()
    if ip:
        return ip
```

An untrusted reverse proxy or a client setting `X-Forwarded-For: 1.2.3.4` can spoof the IP. Most deployments terminate XFF at a trusted proxy, but if the cockpit/interact endpoint is exposed directly (or through misconfigured TLS termination), rate limiting is trivially bypassable.

Fix surface: gate XFF parsing behind `JVAGENT_TRUSTED_PROXIES` config; default to `request.client.host` when no trusted proxy is set.

### MED-13 — `interact_walker.curate_walk_path` swallows AttributeError on `.id` access

`jvagent/action/interact/interact_walker.py:742-748`

```python
interact_actions_in_queue = [
    item for item in current_queue if isinstance(item, InteractAction)
]
actions_set = {a.id for a in actions}
```

If any action in `actions` has no `.id` (e.g. transient instances not persisted), the set-comprehension raises AttributeError, propagating out. Caller (`delivery/delegation.py:159`) wraps with `try/except` and logs a warning, but the entire curation step is skipped — meaning the routed IAs are NOT prepended and the original walker queue persists (uncurated).

Fix surface: filter actions without `.id` before the set-comprehension; log explicitly.

### MED-14 — `_publish` in response.py doesn't honor `transient` for the SSE stream gating

`jvagent/action/cockpit/tools/response.py:13-43`

```python
async def _publish(
    content: str,
    streaming: bool = False,
    transient: bool = False,
    finalize: bool = True,
) -> str:
    ...
    await ctx.response_bus.publish(
        ...
        transient=transient,
        category="user",
    )
    if finalize:
        get_session(ctx.visitor).finalized = True
```

If the model calls `response_publish(content="...", finalize=True, transient=True)`, the content is published as transient (not appended to `interaction.response`), but the cockpit engine still terminates and reports "Final response delivered." But the interaction has no response saved, so subsequent log queries see "Interaction completed" with `response=None`. The user does see the SSE event but the interaction history is broken.

Fix surface: when `finalize=True`, force `transient=False` (or warn loudly when both are True).

### MED-15 — `_publish` doesn't check that `response_bus` is available before setting `finalized`

`jvagent/action/cockpit/tools/response.py:24-43`

```python
if not ctx.response_bus or not ctx.session_id or not ctx.interaction:
    return "Error: response bus or session unavailable."

await ctx.response_bus.publish(...)

if finalize:
    get_session(ctx.visitor).finalized = True
```

OK in this flow — early-return prevents `finalize` from being set. But the engine doesn't know the difference: if `response_publish` returns the "Error: response bus or session unavailable." string, the model sees it as a successful tool result (no `is_error` flag set on the ToolResult constructed from the string). The model proceeds as if the publish worked. The engine then doesn't see `session.finalized` set and keeps looping.

Fix surface: return a ToolResult with `is_error=True` so the model sees the failure and can adjust.

### MED-16 — `routing/preclassifier.py::has_active_tasks` catches `Exception` from `store.list` but treats as "no active tasks"

`jvagent/action/cockpit/routing/preclassifier.py:172-180`

```python
try:
    active = store.list(status="active")
except Exception:
    return False
```

If the task store is broken (DB unavailable), the function returns False — the preclassifier fires its smalltalk fast-path and routes to converse. But there might actually be an active interview in flight whose state we couldn't read. The user's "yes" answer to a question is routed to converse instead of the interview action. **MEDIUM** because cache invalidation should recover this on the next request, but the immediate request is wrong.

Fix surface: when task store fails, return True (assume active tasks exist) to fail closed — defer to the LLM router which will see no active tasks but also won't take the smalltalk shortcut.

### MED-17 — `cockpit_interact_action.py` `_strip_model_action_type` returns None for empty value but `get_model_action` reads `getattr(self, ..., None)` not the result

`jvagent/action/cockpit/cockpit_interact_action.py:260-269` + `:271-296`

```python
def _language_model_action_type_for_purpose(self, purpose):
    skill = self._strip_model_action_type(getattr(self, "model_action_type", None))
    router = self._strip_model_action_type(getattr(self, "router_model_action_type", None))
    if purpose == "skill":
        return skill
    if purpose == "router":
        return router or skill
    return skill
```

If `model_action_type=""` (operator set it explicitly to empty string), `skill` becomes None. Then `purpose="skill"` returns None. `get_model_action` then falls through to `await self.get_action(LanguageModelAction)` which might find ANY LM action — possibly the wrong one. Operators expecting type-specific binding silently get a fallback. Minor: usually `model_action_type` defaults to `"AnthropicLanguageModelAction"`.

Fix surface: log a warning when `model_action_type` is empty.

### MED-18 — `auto_track_tasks` create-then-reuse path can leak tasks if engine fails mid-init

`jvagent/action/cockpit/engine.py:988-1020`

```python
async def _auto_task_start(self):
    try:
        ...
        task = await store.create(title=title, description=utterance or ...)
        await task.start()
        self._trace_task = task
        session = get_session(self.ctx.visitor)
        session.trace_task_id = getattr(task, "id", None)
    except Exception as exc:
        logger.debug(...)
        self._trace_task = None
```

If `task.start()` raises after `store.create()` succeeded, we've created an unstarted task that's never finalized. The conversation's `tasks` list now has an `inactive` task entry that won't ever transition.

Fix surface: roll back the task creation in the exception handler.

### MED-19 — `_handle_error` writes to `interaction.response` even if a tool already published a response

`jvagent/action/cockpit/cockpit_interact_action.py:782-795`

```python
async def _handle_error(self, visitor, exc):
    clear_session(visitor)
    interaction = visitor.interaction
    if interaction:
        if not interaction.response:
            interaction.response = "I encountered an error processing your request. Please try again."
```

The check `if not interaction.response:` only catches the case where `response` is empty. But the response bus may have ALREADY streamed content to the user (via `response_publish`) without `interaction.response` being set (transient=True path). The user sees the streamed content + the "I encountered an error" fallback. Inconsistent.

Fix surface: check the response bus's accumulated state, not just `interaction.response`.

### MED-20 — `task_create_plan` does not validate `steps` is a list of strings

`jvagent/action/cockpit/tools/task.py:41-107`

`steps: List[str]` is the type hint, but Python type hints aren't enforced at runtime. The model could pass `steps=[{"description": "..."}, ...]` and the code at `await task.set_plan(steps)` would forward whatever it received. Downstream consumers expecting strings would break.

Fix surface: coerce each step to a string explicitly, drop empty entries.

### MED-21 — Skill catalog discovery doesn't handle race when `cls._cache` mutates during iteration

`jvagent/action/cockpit/catalog/skill_catalog.py:223-245`

```python
async with cls._cache_lock:
    if cache_key in cls._cache:
        cached_skills, cached_at = cls._cache[cache_key]
        ...
        if age < _SKILL_DISCOVERY_CACHE_TTL:
            return cls(cached_skills)
        else:
            ...
            del cls._cache[cache_key]
```

The expired-entry deletion is inside the lock, fine. But the subsequent `if len(cls._cache) >= cls._CACHE_MAX_ENTRIES` at line 288 uses `min(cls._cache, key=lambda k: cls._cache[k][1])` — this iterates `cls._cache` while reading values. If another coroutine (in the same event loop, different task) mutates the cache without the lock, the iteration sees inconsistency. Since all writes are inside `async with cls._cache_lock`, this should be safe in async context. OK.

### MED-22 — `assemble_cockpit_tools` does not actually verify access for tool registration when `agent` is None

`jvagent/action/cockpit/registry/assembler.py:87-105`

```python
removed = await filter_tool_registry_by_access(
    registry, ctx.agent, user_id=user_id, channel=channel
)
```

`filter_tool_registry_by_access` checks `if agent is None: return 0` (in `registry/access.py:_resolve_access_control`). So when `ctx.agent` is None, NO tools are filtered. In tests this is intentional, but a real cockpit invocation should always have a non-None agent. There's no assertion guard.

Fix surface: assert `ctx.agent is not None` at engine.initialize() so the failure mode is loud, not silent.

### MED-23 — `_check_stuck` may evaluate before `_recent_tool_signatures` is fully populated leading to early returns

`jvagent/action/cockpit/engine.py:492-539`

The function checks `if len(self._recent_tool_signatures) < min_iters: return False` — min_iters defaults to 4. But the trace task records `_iteration` as 1, 2, 3, etc. — the engine's first 3 iterations skip stuck detection. Combined with `max_iterations=25`, only iterations 4-25 are eligible. If `stuck_min_iterations` is reconfigured to 1 (or 0), the stuck detection can fire on iteration 1 — but with only 1 signature in the window, all_similar is trivially True. The fence at line 511 (`< min_iters`) prevents this.

OK in practice with defaults.

Fix surface: enforce `stuck_min_iterations >= max(2, stuck_detection_window // 2)` in healthcheck/config validation.

---

## LOW

### LOW-01 — Dead/misleading comment in `cockpit_interact_action.py:336-338`

```python
visitor._skill_state = (
    visitor._skill_state if hasattr(visitor, "_skill_state") else {}
)
```

The expression reassigns `_skill_state` to itself if present. This is a no-op unless `_skill_state` is not an attribute, in which case it sets to `{}`. A clearer form: `if not hasattr(visitor, "_skill_state"): visitor._skill_state = {}`.

### LOW-02 — `register/access.py` `interact_action_resource_label` is just identity

`jvagent/action/cockpit/registry/access.py:47-50`

```python
def interact_action_resource_label(class_name: str) -> str:
    # Matches the existing convention used by jvagent/access_control rules.
    return class_name
```

The function exists for symmetry with `skill_resource_label` and `tool_resource_label`, but adds no logic. Either remove or document that it MUST be used (so a future change can adjust the label format in one place).

### LOW-03 — Unused imports in `routing/router.py`

`jvagent/action/cockpit/routing/router.py:1-34`: imports `Path`, `re` (line 5-6) and `always_active_from_skill_dir` (line 10) are used; OK. But `POSTURE_DEFER` and `POSTURE_SUPPRESS` are imported but never used in this file — only checked indirectly via `result.is_suppress()` / `result.is_defer()`.

### LOW-04 — Inconsistent default values between `CockpitInteractAction` and `CockpitConfig`

`cockpit_interact_action.py:127` `history_limit: int = attribute(default=3)`
`cockpit/config.py:69` `history_limit: int = 5`

The dataclass `CockpitConfig` has `history_limit=5` as default; the action overrides with `default=3`. The action's value wins because `_build_cockpit_config` passes `self.history_limit` explicitly. But the inconsistency is a footgun — anyone reading `config.py` sees `5` and might assume that's the default.

Fix surface: align the defaults (use 3 in both) or document that the action's `attribute(default=...)` is authoritative.

### LOW-05 — `_finalize_via_persona` uses `int(self.history_limit or 0) or 4` which is convoluted

`jvagent/action/cockpit/cockpit_interact_action.py:739`

```python
history_limit=max(1, int(self.history_limit or 0) or 4),
```

`int(self.history_limit or 0)` — if `history_limit=0`, this is `int(0) = 0`, which is falsy → fallback to 4. So `history_limit=0` becomes `history_limit=4`. The operator's explicit "don't include history" is overridden silently.

Fix surface: handle `history_limit=0` explicitly (pass through, or document that 0 means "default 4").

### LOW-06 — `_build_history` uses `excluded=self.ctx.interaction.id` but `interaction.id` could be None for fresh interactions

`jvagent/action/cockpit/engine.py:961`

If `self.ctx.interaction.id` is None (interaction not yet persisted), `excluded=None` — the history fetch returns all interactions including the current one. The current interaction's response is `None` so it shouldn't affect the role/content rendering, but it's a footgun.

Fix surface: only pass `excluded` when interaction.id is non-None.

### LOW-07 — `_emit_tool_progress` builds `status` via an `or` chain that can mis-classify dict-shaped ToolResults

`jvagent/action/cockpit/engine.py:1316-1326`

```python
status = (
    "failed"
    if (
        getattr(tr, "is_error", False)
        or (
            isinstance(tr, dict)
            and tr.get("content", "").startswith("Error:")
        )
    )
    else "ok"
)
```

`tr` is supposed to be a `ToolResult` object — the dict branch is defensive but suggests historical inconsistency. If `tr.get("content")` returns None (key absent), `None.startswith("Error:")` raises AttributeError. Caught by surrounding `try/except` (line 1183), but it shouldn't happen.

Fix surface: replace with `(tr.get("content") or "")` chain.

### LOW-08 — `format_index_entry` defaults description to "Standard operating procedure" — misleading when skill has none

`jvagent/action/cockpit/catalog/skill_catalog.py:65-92`

```python
description = str(
    skill_data.get("description") or "Standard operating procedure."
)
```

If a skill has no description, the entry shows "Standard operating procedure." — a generic phrase that signals "the data is missing" only to readers familiar with the convention. The model sees this as the skill's actual description and may pick it up over more specific ones.

Fix surface: use a clearly-template phrase like "(No description — call `skill_read` for details)".

### LOW-09 — `interact_walker.py` `_apply_access_denied_to_interaction` always appends a directive even if one is already there

`jvagent/action/interact/interact_walker.py:208-229`

```python
if self.interaction and getattr(here, "deny_access_directive", None):
    self.interaction.directives.append(
        {
            "content": here.deny_access_directive,
            "action_name": action_label,
            "executed": False,
        }
    )
```

If the same denied action is reachable via multiple paths (e.g. always_execute_ias contains it AND routing dispatched it), the directive is appended twice. The walker's `add_directives` has dedup logic; this raw `.append` does not.

Fix surface: use `interaction.add_directives([...])` with dedup, or check membership before append.

### LOW-10 — `endpoints.py` request_id logged inconsistently — sometimes via `profile.request_id`, sometimes missing

`jvagent/action/interact/endpoints.py:692-694`

```python
logger.warning(f"Failed to log interaction: {e}")
```

vs `profile.request_id` used elsewhere. Operators correlating logs by request_id can't find this branch.

### LOW-11 — Cockpit `description` attribute is overly long (single 200+ char string)

`jvagent/action/cockpit/cockpit_interact_action.py:92-94`

The default description is one long string. The router includes this in `interact_action_descriptors`. Truncated at 240 chars by `routing/router.py:367` so it's bounded, but the truncation can cut mid-word.

### LOW-12 — `_format_results` doesn't escape skill names with markdown characters

`jvagent/action/cockpit/tools/search.py:166-186`

If a skill name contains `*` or `_`, the model's output may render it as markdown formatting. Skill names are author-controlled so this isn't user-injected — but a malicious skill bundle could craft a name that renders oddly.

### LOW-13 — `_skip_canned_for_intents` default has typo opportunity

`jvagent/action/cockpit/cockpit_interact_action.py:134-136`

```python
skip_canned_for_intents: List[str] = attribute(
    default_factory=lambda: ["CONVERSATIONAL", "UNCLEAR", "INTERACTIVE"],
)
```

A user who sets `skip_canned_for_intents=["INFORMATIONAL"]` (without spelling matters case) may not realize the comparison at `router.py:175` is case-sensitive. The intent_type comparison should normalize or document.

### LOW-14 — Cockpit init order: `await _maybe_pre_dispatch()` runs before `_initialized = True`

`jvagent/action/cockpit/engine.py:158-170`

If `_maybe_pre_dispatch` calls back into the engine somehow (it doesn't currently), it would see `_initialized=False`. Defensive — set `_initialized=True` before any other I/O.

### LOW-15 — `_publish_callback` in `cockpit_interact_action.py` ignores `transient` parameter

`jvagent/action/cockpit/cockpit_interact_action.py:751-780`

The callback receives `streaming_complete` and others but always calls `self.publish` / `self.publish_thought` without forwarding `transient`. Tools relying on transient flag would silently drop the flag.

### LOW-16 — `endpoints.py::_finalize_usage` may run twice if walker is restarted

`jvagent/action/interact/endpoints.py:39-62`

Called once in non-streaming path and once in streaming path; no idempotency check. If a request retries, `add_usage_from_interaction` double-counts.

### LOW-17 — `_check_stuck` returns True without distinguishing the cause in the result

The `CockpitStepResult(status="stuck", ...)` doesn't capture which check (jaccard vs primary-repeat) tripped. Operators debugging can't tell whether to tune jaccard or repeat thresholds.

### LOW-18 — `routing/types.py::ROUTING_PRIOR_FRAGMENTS_SECTION = ""` is reserved-but-unused

`jvagent/action/cockpit/routing/prompts.py:155`

```python
ROUTING_PRIOR_FRAGMENTS_SECTION = ""
```

Placeholder used in `prompt.format(..., prior_fragments_section="")` at `router.py:267`. The empty default is forwarded to the template — fine, but the variable's existence implies a future feature. Dead-code-like.

---

## SPEC drift

### DRIFT-01 — SPEC §3.3 says max_iterations default is 25, code shows max_visits_per_node walker cap

The cockpit's `max_iterations=25` sits well under the walker's `max_visits_per_node=100` (SPEC §3.3 footnote and ADR 0002 "Tuning"). But ADR 0002 says "max_iterations (default 25) sits well under this, but be careful not to bypass." There is NO runtime check that the operator-set value is < 100. Drift: docs warn but code doesn't enforce.

### DRIFT-02 — `cockpit/CLAUDE.md` §6 lists `stuck_detection_window` default as 3, but code uses 4

`jvagent/action/cockpit/cockpit_interact_action.py:187` shows `stuck_detection_window: int = attribute(default=4)`. The user-facing doc `docs/COCKPIT.md` says "default 3" in some sections and the local guide `cockpit/CLAUDE.md` §6 says "default 3" in description. The CockpitConfig dataclass at `config.py:25` shows `stuck_detection_window: int = 4`. The action wins; docs are stale.

### DRIFT-03 — SPEC §7 "Stream mode defaults to `visitor.stream`; pass `stream=False` for non-streaming publishes"

The `_publish` harness tool in `response.py:32` does:

```python
stream=streaming and ctx.stream,
```

Note: `streaming` is the model-supplied param (default False). So `stream=False and ctx.stream = False` — the tool always publishes non-streamed unless the model explicitly sets `streaming=True`. This contradicts SPEC §7 which says publish defaults to `visitor.stream`. The tool's behavior may surprise authors expecting SPEC behavior.

### DRIFT-04 — `docs/COCKPIT.md` lists `memory_update_user_model` as a tool name, code shows it deprecated

`docs/COCKPIT.md` section "Tool Categories" lists `memory_update_user_model` as active. `memory.py:398-404` marks it `[Deprecated alias for memory_set scope=user]`. The doc table doesn't note deprecation; new code should call `memory_set` directly.

### DRIFT-05 — `cockpit/CLAUDE.md` §4 says termination order is text → max_iterations → max_duration → stuck → degenerate. Code checks them differently

`engine.py:188-219` checks `max_duration_seconds` FIRST (line 188), then `max_iterations` (line 203). The doc has it reversed.

### DRIFT-06 — `interact/CLAUDE.md` §3 ¶3 says "Each background action is isolated in try/except — failures must not propagate"

Code at `endpoints.py:78-109` does isolate each iteration, but as noted in HIGH-01, the isolation also captures the access-check exceptions, hiding access-control logic errors as if they were execute errors.

### DRIFT-07 — SPEC §3.2 says "Initialize `walker.interaction`, `walker.conversation`, `walker.user`, `walker.session_id`, `walker.channel`, `walker.response_bus`, `walker.stream` before visiting any `InteractAction`"

`walker.user` is NOT initialized by `_bootstrap_interaction` — only `walker.user_id` is set. The `user` node is fetched inline as needed by harness tools via `memory.get_user(user_id)`. The walker has no `user` field. SPEC mentions `walker.user` but code uses `walker.user_id`.

### DRIFT-08 — `docs/COCKPIT.md` describes `block_raw_tool_invocation` as defending against `/skill X` and `tool_name(args)` patterns. Code only injects a prompt block

`cockpit_interact_action.py:228` and `engine.py:689-690`:

```python
if getattr(cfg, "block_raw_tool_invocation", False):
    security_block = getattr(cfg, "security_prompt", "") or SECURITY_BLOCK
```

The flag only appends a prompt block. There is NO actual blocking of tool dispatch from user-named tools — it relies on the model following the system prompt instruction. A jailbroken/malicious user prompt can still talk the model into calling tools.

Fix surface: add a structural check that user-text matching `^/(skill|tool|action)` patterns is sanitized in `interaction.utterance` before the model sees it, OR document clearly that this flag is prompt-level only, not enforcement.

### DRIFT-09 — `cockpit/CLAUDE.md` §5 says "Skill tools: ``{skill_name}__{tool_name}``", code uses `{safe_name}__{tool_name}`

`assembler.py:337`: `safe_name = skill_name.replace("-", "_")`. The qualified name is `{safe_name}__{tool_name}` — hyphens in skill names become underscores. If a skill `web-search` registers tools, they appear as `web_search__search`, not `web-search__search`. The doc should clarify the normalization.

### DRIFT-10 — `interact/CLAUDE.md` §3 ¶7 says `publish()` with `stream=None` defaults to `visitor.stream`. Code matches but warns inconsistently

`base.py:255`:
```python
use_stream = stream if stream is not None else getattr(visitor, "stream", False)
```

OK. But the `getattr(..., False)` falls back to False if the walker has no `stream` attribute — masking a programming bug (the walker SHOULD always have `stream` set). Drift: the doc says "defaults to `visitor.stream`" but the code silently allows missing attribute. Minor.

---

## Strengths

- The walker-revisit pattern is implemented cleanly and matches ADR 0002. State persistence on `CockpitSession` is well-encapsulated.
- The router's caching strategy is conservative (off by default, TTL-bounded) and includes active-task fingerprinting.
- Access control filters routed skills, routed IAs, and tool registry in three distinct passes — defense-in-depth.
- Tool execution is bounded by both timeout (`tool_call_timeout`) and concurrency (`max_concurrent_tools`).
- The pre-classifier is conservative (exact-match whitelist, length-capped, gated by active-task absence) — appropriate for a latency optimization.
- The structured tool-call/tool-result envelopes are deduped per envelope (good for streaming consumers), though see HIGH-09 for the engine-restart edge case.
- `parse_routing_response` handles markdown code fences and JSON braces robustly enough to recover from messy LLM output, with explicit `error_result` fallback.
- The CockpitSession refactor is a real improvement over scattered `_skill_state` keys — single-point reset is exactly what was needed.
- The harness tools (memory, artifact, task) generally validate keys, normalize tags, and handle save() errors gracefully.
- The rate limiter's sliding-window approach with per-key lazy cleanup is correct for single-process deployments.
- Background actions correctly run only after the interaction is closed, preserving the user-facing response latency.

---

## Out of scope

- The PersonaAction internals (`jvagent/action/persona/`) — referenced as `_require_persona`, `respond`, `respond_slim`.
- The InteractRouter action (`jvagent/action/interact_router/`) — separate routing action distinct from the cockpit router.
- The ResponseBus implementation (`jvagent/action/response/response_bus.py`) — referenced but not opened in this audit; some CRIT/HIGH findings touch the publish contract.
- The Tool / ToolRegistry / ToolExecutionEngine internals beyond timeout/sanitize plumbing.
- Model-side concerns (`jvagent/action/model/`) — `query_messages`, `translate_reasoning_config`, streaming consumer interfaces.
- The Skill bundle resolution helpers in `jvagent/scaffold/skill_resolve.py`.
- Memory subsystem (`jvagent/memory/`) — audited separately.
- Core subsystem (`jvagent/core/`) — audited separately, except for cache.py interactions which are referenced.
- The Walker base class internals (`jvspatial/jvspatial/core/entities/walker.py`) — only its `prepend`/`append`/`visit`/`max_queue_size`/`max_visits_per_node` semantics are referenced.
