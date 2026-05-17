# AUDIT — jvagent/memory/

**Date**: 2026-05-17
**Scope**: `jvagent/memory/` directory + adjacent service helpers
**Contract refs**: `.planning/SPEC.md` §5, `.planning/memory-and-pruning.md`, `.planning/adr/0003-interaction-limit-pruning.md`, `jvagent/memory/CLAUDE.md`

---

## Summary

The memory subsystem has competent locking primitives, careful pruning invariants in `_prune_old_interactions`, and a reasonable separation of concerns. However, several CRITICAL issues exist:

1. An **authz bypass** in `get_my_memory` that lets any authenticated caller read another user's long-term memory by passing `?user_id=...` as a query parameter.
2. A **broken cross-process lock**: the Redis and DynamoDB conversation locks instantiate a fresh client per acquisition attempt under a `while True` poll, never sleep on contention bounded by max retries, and on Redis the `set NX` poll never exits if a holder dies inside its lease window without explicit TTL expiration handling at the caller level (it does work eventually via TTL, but holders that crash can starve callers for the full TTL with no backoff visibility).
3. The **pruning algorithm's edge-rewire sequence is not atomic** and is reordered relative to what the SPEC documents (`memory-and-pruning.md` §5: disconnect→connect→delete; actual: disconnect Conv→current, connect Conv→nxt, disconnect current↔nxt, delete current). The ADR §Invariant 4 states `Conv → current` is disconnected and `Conv → next` is established **before** deleting `current` — code matches the ADR, but `memory-and-pruning.md` step-by-step pseudocode disagrees on the relative order of disconnecting `current↔nxt` vs `connecting Conv→nxt`. Documentation drift.
4. A **runtime TypeError** waiting to happen in `LongMemoryService.resolve_collection` — passes `suffix=` kwarg that `resolve_long_memory_collection` does not accept.
5. **No exclusive locking on `Conversation.add_interaction` is acquired before mutating** `interaction_count`/`last_interaction_id`/edges _in the in-process fallback case_; the per-conversation lock IS acquired via the distributed wrapper, but the fallback path uses the lock-manager whose TTL cleanup may evict an in-use lock entry if a request is held longer than 30s. See HIGH-04.

There are also numerous HIGH-severity race conditions in cross-loop singleton state, count drift mechanics, and silent-failure points around `User.get_agent()` resolution.

---

## CRITICAL

### CRIT-01 — Authz bypass: `get_my_memory` reads `user_id` from query string, not from the authenticated principal

`jvagent/memory/endpoints.py:432-463`

```python
async def get_my_memory(
    agent_id: str,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    ...
    user = await memory_manager.get_user(user_id)
    ...
    return {"memory": await _long_memory_service.get_memory_content(user)}
```

The docstring claims:
> Authenticated caller user identifier (injected by auth middleware)

But FastAPI/jvspatial route parameters resolve from the query string (or path) by default; `Optional[str] = None` with no `Depends(...)` makes this a plain query parameter. Any authenticated principal can call `GET /api/agents/{agent_id}/memory/me?user_id=victim` and receive `victim`'s long-term memory categories (interests, facts & preferences, open threads, recent events). The endpoint is marked `auth=True` but NOT `roles=["admin"]`, so any logged-in account is sufficient.

Impact: cross-user PII leakage. Long-memory categories store profile data, preferences, and notes the user expects to be private.

Fix surface: bind `user_id` to the verified principal (e.g. via `Depends(get_current_user)`) and ignore any caller-supplied override, OR add `roles=["admin"]` and rename.

### CRIT-02 — `LongMemoryService.resolve_collection` calls helper with wrong kwarg

`jvagent/memory/services/long_memory_service.py:29-30`

```python
def resolve_collection(self, *, agent_id: str, suffix: str) -> str:
    return resolve_long_memory_collection(agent_id=agent_id, suffix=suffix)
```

But `resolve_long_memory_collection` (long_memory_retrieval_utils.py:9-25) signature is:

```python
def resolve_long_memory_collection(
    agent_id: Optional[str],
    collection_attr: Optional[str],
    config: Optional[Dict[str, Any]],
) -> str:
```

There is no `suffix` parameter. Any caller of `LongMemoryService.resolve_collection` triggers `TypeError: resolve_long_memory_collection() got an unexpected keyword argument 'suffix'`.

Today there are no in-tree callers (`grep -rn "LongMemoryService().resolve_collection" /jvagent` returns nothing), so the bug is dormant — but the method is exported and any consumer expecting it to work will fail at runtime.

Fix: either translate suffix into `collection_attr` or remove the dead method.

### CRIT-03 — `purge_conversations(user_id=...)` ignores `user_id` when `conversation_id` is set

`jvagent/memory/manager.py:565-619`

```python
if conversation_id:
    conversation = await Conversation.get(conversation_id)
    if not conversation:
        return None
    conversations_to_purge = [conversation]
```

When admin calls `DELETE /api/agents/{agent_id}/memory/purge?user_id=alice&conversation_id=conv_X`, the function looks up `conv_X` globally with `Conversation.get(conversation_id)` and adds it to the purge list **without verifying** that `conv_X` belongs to `alice` OR to this `Memory`. The endpoint route already enforces admin role, but a multi-agent deployment with shared DB lets an admin of agent A delete agent B's conversations by ID. Cross-tenant data destruction.

`Memory._resolve_conversation_for_session_or_raise_foreign` already exists for this exact ownership check; `purge_conversations` does not call it.

Fix: when `conversation_id` is set, verify `await self._conversation_belongs_to_memory(conversation)` before adding to the purge list. Raise `ValueError`/404 on mismatch.

### CRIT-04 — Module-level singleton `_long_memory_service` and `_user_lock_manager` reuse data structures across event loops

`jvagent/memory/lock_manager.py:84-85`

```python
_user_lock_manager = MemoryLockManager()
_conversation_lock_manager = MemoryLockManager()
```

`MemoryLockManager._locks: Dict[str, asyncio.Lock]` is populated lazily by `acquire()`. In serverless / worker-restart scenarios, two requests on different event loops can share the same `_locks` dict but each `asyncio.Lock` is bound to the loop that created it. While the class does pin a per-loop global lock (`_global_locks_by_loop`), the per-key locks in `_locks` are NOT per-loop. If event loop A creates `_locks["mem:alice"]` and is destroyed, event loop B picks up the stale lock and `await lock.acquire()` raises `RuntimeError: ... bound to a different loop` (asyncio semantics).

The class comment at lock_manager.py:36-39 hints at this issue but only addresses the global lock, not the per-key locks. The TTL cleanup at lock_manager.py:67-80 only evicts after `_LOCK_TTL_SECONDS=30` of inactivity, so the window of broken state is large.

Impact: intermittent `RuntimeError` under warm-start serverless deployments; manifests as 500s on `Memory.get_user()` calls, which under the same `(memory_id, user_id)` then bypass the unlocked path entirely.

Fix: key `_locks` by `(loop_id, key)` instead of just `key`, or rebuild the manager per loop on first access.

---

## HIGH

### HIGH-01 — Compound index bypass in `Memory._get_user_unlocked()` via `User.find_one` fallback

`jvagent/memory/manager.py:100-108`

```python
scoped = await User.find_one(
    {"context.memory_id": self.id, "context.user_id": user_id}
)
if scoped:
    if not await self.is_connected_to(scoped):
        await self.connect(scoped)
    scoped.last_seen = now
    await scoped.save()
    return scoped
```

If a User exists in the DB but is NOT connected to this Memory (e.g., a previous repair-pass disconnect, a foreign import, or the result of `_reconnect_orphaned_users` running mid-flight), this code reconnects it. The compound index `(memory_id, user_id) unique` lives at user.py:16-24 with `partial_filter_expression={"context.memory_id": {"$gt": ""}, "context.user_id": {"$gt": ""}}` — but the index protects against **duplicates**, not against ownership semantics. If User X has `memory_id=""` (legacy / unscoped), this branch happily attaches them to the new Memory, mutating `memory_id` implicitly? Actually no — the code does NOT update `memory_id` here. So you can end up with `User.memory_id=""` connected to a Memory via edge but the index doesn't fire on the partial filter, allowing a second User row for the same `user_id` to slip in later when `memory_id` finally gets set. This is the exact mode the ADR/SPEC says the index prevents.

Fix: when reconnecting via the `find_one` fallback, also set `scoped.memory_id = self.id` before save, or refuse to reconnect users with empty `memory_id` and force the create path.

### HIGH-02 — `Memory.get_user(user_id, create_if_missing=True)` first check returns a possibly foreign User

`jvagent/memory/manager.py:91-98`

```python
user = await self.node(node=User, user_id=user_id)
if user:
    if user.memory_id and user.memory_id != self.id:
        user = None
    else:
        user.last_seen = now
        await user.save()
        return user
```

If the graph edge from this Memory points at a User whose `memory_id == ""`, the `if user.memory_id and ...` short-circuits and the User is returned as if owned. Combined with HIGH-01, an unscoped User is permanently bound to whichever Memory walked it first, but only by edge — `memory_id` is never written back. Subsequent calls from another Memory using the `find_one` path can also claim that User (no `memory_id` filter discriminates them).

Fix: on first ownership claim, set `user.memory_id = self.id` and save before returning.

### HIGH-03 — Pruning capacity miscounted when `interaction_count` drifts

`jvagent/memory/conversation.py:310-329`

```python
to_remove = self.interaction_count - self.interaction_limit
...
while current and removed < to_remove and removed < max_prune:
    ...
    current = next_interaction
```

If `interaction_count` has drifted (e.g., underreports the actual edge count due to a crash mid-append after the `self.connect(...)` but before `await self.save()`), the loop exits early and the conversation stays over-limit. There's no fallback to count via the graph here, only at repair time. Conversely, if `interaction_count` over-reports, the loop may try to walk past the last interaction and bail (which is correct), but the `interaction_count -= 1` lines never run, leaving the over-report in place forever.

Worse: the `current = next_interaction` line at conversation.py:349 advances even when `removed == max_prune-1` enters the next iteration check — if `to_remove > max_prune`, we exit after the cap, leaving `interaction_count` decremented by `max_prune` exactly but the chain physically has `max_prune` fewer nodes. That part is correct. The drift case is the concern.

Fix: at the start of `_prune_old_interactions`, reconcile `interaction_count` against an actual count query; OR add a defensive `if self.interaction_count <= 1: return 0` (never prune below 1).

### HIGH-04 — Per-conversation lock can be silently evicted during a long-running `add_interaction`

`jvagent/memory/lock_manager.py:67-80` + `conversation.py:232`

`MemoryLockManager._cleanup_stale()` removes entries from `_locks` if:
- `now - timestamps[k] > 30s`  AND
- `not self._locks[k].locked()`

The `locked()` check protects against deleting an actively-held lock, but `_timestamps[k]` is only updated on `acquire()`. If a long `_add_interaction_unlocked` body (model retries, slow DB) keeps the lock held for >30s, the next acquirer skips the timestamp update because `_cleanup_stale` is gated on `now - last_cleanup > 120s`. If the cleanup races right after the long holder releases, the stale entry is removed. The next call creates a NEW `asyncio.Lock`, breaking mutual exclusion: a concurrent retry that started while the lock was still held now operates on a different lock instance.

Window is narrow but non-zero. Triggers duplicate chain-edge inserts and `interaction_count` double-increments under stress.

Fix: update `_timestamps[k]` on every `acquire`, AND on lock release (requires wrapping the returned lock with a release hook); OR skip cleanup of entries that have an active waiter queue (asyncio.Lock exposes this).

### HIGH-05 — `conversation_mutation_lock` Redis/DynamoDB polling has no maximum-wait bound

`jvagent/memory/distributed_conversation_lock.py:107-113`, `203-208`

```python
try:
    while True:
        acquired = await client.set(name=key, value=token, nx=True, ex=ttl)
        if acquired:
            break
        await asyncio.sleep(0.05)
    yield
```

The acquire loop has no timeout, no backoff, and no cancellation handling. A poisoned holder (crashed mid-section before TTL expiry) blocks every other request for the entire `_lock_ttl_seconds()` (default 45s). On a hot conversation with steady traffic this serializes all callers behind the TTL clock. Worse, there's no metric/log emitted while waiting, so operators have no visibility.

Additionally, every iteration of the loop creates a `redis.from_url(...)` client at line 98 (outside the loop, OK), but DynamoDB rebuilds `boto3.client("dynamodb", ...)` on every `try_acquire` call (line 155). That's an expensive per-poll allocation when contended.

Fix: add an explicit max-wait with `asyncio.wait_for`, exponential backoff (0.05 → 0.5 cap), and a counter log every N attempts. Cache the DynamoDB client across retries.

### HIGH-06 — Redis lock release uses Lua script that depends on consistent `decode_responses` setting

`jvagent/memory/distributed_conversation_lock.py:98, 116`

```python
client = redis.from_url(redis_url, decode_responses=True)
...
await client.eval(unlock_script, 1, key, token)
```

With `decode_responses=True`, `GET` returns `str` but the Lua `ARGV[1]` is compared via `==` to the value stored. If a different process wrote the lock value with `decode_responses=False` (bytes) — possible if multiple worker types coexist — the comparison fails silently, the release loop returns 0, and the lock stays until TTL. The current code only complains via `logger.debug` (line 121), making this hard to spot in production.

Fix: log release failures at `warning` level when `eval` returns 0; standardize all conversation-lock clients to `decode_responses=True` via a shared helper.

### HIGH-07 — `User.add_usage_from_interaction` race against itself across event loops

`jvagent/memory/user.py:275-329`

```python
lock_mgr = get_user_lock_manager()
lock = await lock_mgr.acquire(f"usage:{self.id}")
async with lock:
    if not self.usage:
        self.usage = { ... }
    ...
    self.usage["total_tokens"] = self.usage.get("total_tokens", 0) + usage.get("total_tokens", 0)
    ...
    await self.save()
```

The lock is **in-process only**; under multi-worker / serverless, two workers reading the same `User` row, adding 100 tokens each, last-write-wins to `self.save()`. The save serializes the entire `usage` dict, so the loser's add is lost. The compound-index won't fire because we're not changing identity columns.

Same pattern recurs in `update_user_model` (user.py:247-267) — no lock and no atomic increment.

Fix: use `ctx.atomic_increment(self.id, "usage.total_tokens", n)` for each scalar, mirroring how `Memory.total_users` is handled.

### HIGH-08 — `Memory._cleanup_orphaned_interactions` swallows errors but still increments deleted counter inconsistently

`jvagent/memory/manager.py:1029-1054`

```python
for row in rows:
    ...
    try:
        interaction = await Interaction.get(interaction_id)
        if interaction:
            await interaction.delete(cascade=True)
            deleted += 1
    except Exception as exc:
        logger.warning(...)
```

If `Interaction.get` returns the object but `delete(cascade=True)` throws partway (cascade has already deleted some children), `deleted` is NOT incremented. The orphan partially exists. On the next repair pass, the partially-deleted node may or may not be picked up depending on which fields survived. The current code returns a count that under-reports actual deletions and leaves no breadcrumb for the operator to trace which IDs failed.

Fix: log the failing `interaction_id` (already done) AND add a `failed: List[str]` collector returned alongside `deleted`. The repair endpoint should expose both numbers.

### HIGH-09 — `get_next_interaction` / `get_previous_interaction` fall back to returning the wrong neighbor when timestamps are missing or equal

`jvagent/memory/interaction.py:710-755`

```python
async def get_next_interaction(self) -> Optional["Interaction"]:
    next_int = await self.node(
        node=Interaction, direction="out", conversation_id=self.conversation_id
    )
    if next_int:
        a, b = _normalize_dt(next_int.started_at), _normalize_dt(self.started_at)
        if a is not None and b is not None and a >= b:
            return next_int
        if a is None or b is None:
            return next_int
    return None
```

The fallback `if a is None or b is None: return next_int` returns the neighbor even when timestamps are missing — meaning a forked chain where ordering is undetermined will still hand out a "next." Combined with the pruning algorithm's reliance on `get_next_interaction` to halt (conversation.py:331-336), a chain with two `direction="out"` interactions and missing timestamps can have either one chosen non-deterministically, and pruning may prune the wrong one. This is the same hazard `interaction_sort_key` is supposed to guard against, but `get_next_interaction` doesn't use `interaction_sort_key`.

Fix: when timestamps are absent or equal, use `interaction_sort_key`-style tiebreak (id ordering) to pick a deterministic neighbor. Or reject the call and force a repair pass.

### HIGH-10 — `_add_interaction_unlocked` increments counter, then conditionally saves twice

`jvagent/memory/conversation.py:273-294`

```python
self.last_interaction_id = interaction.id
self.interaction_count += 1
self.last_interaction_at = now
await self.save()

agent = await self.get_agent()
if (... and self.interaction_limit != agent.interaction_limit):
    self.interaction_limit = agent.interaction_limit
    await self.save()

if (self.interaction_limit > 0 and self.interaction_count > self.interaction_limit):
    await self._prune_old_interactions()
```

If `await self.save()` on line 277 succeeds but the process is killed before the `agent.interaction_limit` sync or the prune runs, the conversation persists with `interaction_count` incremented but stays over-limit (because the new limit was not applied). On the next request, pruning eventually catches up — so this is recoverable. However, the SPEC §5.3 says the conversation **adopts the agent's value first**, then prunes. Current code does it AFTER the count increment and AFTER the first save, which means an observer between save calls sees a state where the count is high and the limit is stale. Latency-bound observability dashboards may briefly report incorrect numbers.

Fix: re-order to sync `interaction_limit` first, then increment+save once, then prune. (Note: `_ensure_conversation_interaction_limit` in manager.py:261-286 already does this in the correct order for the resume path.)

### HIGH-11 — `_prune_old_interactions` may produce a dangling Conv→`current` edge if the chain has only one interaction over limit

`jvagent/memory/conversation.py:329-349`

The loop body:
1. Gets `next_interaction = await current.get_next_interaction()` — if None, break.
2. Disconnects Conv → current.
3. Connects Conv → next_interaction.
4. Disconnects current ↔ next_interaction.
5. Deletes current.

If between (2) and (3) the process dies, we lose the Conv → first edge entirely. `_repair_interaction_chain_invariants` (manager.py:735-846) is designed to restore it — but it only runs on the admin repair endpoint, not automatically. Until then, `get_first_interaction()` returns None even though interactions exist, and `interaction_count > 0`. Subsequent appends will create a NEW Conv-out edge to the freshly created interaction, branching the chain.

Fix: invert (2) and (3) — connect Conv → next first, then disconnect Conv → current. Atomicity is still not guaranteed, but the failure mode shifts from "no first-edge" (lost) to "two first-edges" (recoverable via the existing dup-edge repair). The latter is safer.

This contradicts the docstring at memory-and-pruning.md §5 which says "Rewire: Conv ──> nxt (was Conv ──> current)" implying connect-then-disconnect. Code disagrees with the documented intent.

### HIGH-12 — `Conversation.delete` decrements `total_conversations` without checking whether deletion succeeded

`jvagent/memory/conversation.py:954-977`

```python
await super().delete(cascade=cascade)

if memory:
    ctx = await memory.get_context()
    await ctx.atomic_increment(memory.id, "total_conversations", -1)
```

If `super().delete(cascade=cascade)` raises after partially deleting children but before removing the conversation node, the decrement runs anyway via the exception bubbling? Actually it does NOT — the exception propagates and the decrement is skipped. But the conversation may be in a corrupt half-deleted state, which the counter doesn't reflect. Conversely, if `delete` succeeds but `get_context` / `atomic_increment` fails, the conversation is gone and the counter is unchanged — drift.

Combined with `Memory.purge_user_memory` (manager.py:516-548) which **calls `count_neighbors` before `delete(cascade=True)`** and decrements based on that count: if a user's conversations are partially deleted in some other way concurrently, `n_convs` overcounts and the decrement goes negative.

Fix: wrap in try/finally; on exception, schedule a counter reconcile via `refresh_memory_counters_from_graph`. Or stop trying to maintain `atomic_increment` parity and rely solely on the repair pass.

---

## MEDIUM

### MED-01 — Duplicate `add_event` method on `StepHandle` (silent override)

`jvagent/memory/task_store.py:303-321` and `:323-341`

Two identical `async def add_event(...)` definitions on `StepHandle`. Python silently keeps the second one. Bug if someone modifies the first thinking they're updating the canonical method.

Fix: delete one. (Same pattern would also be worth checking for `TaskHandle`.)

### MED-02 — `_prune_old_interactions` cap parsing tolerates negative values poorly

`jvagent/memory/conversation.py:317-323`

```python
try:
    max_prune = max(
        1,
        int(os.environ.get("JVAGENT_MAX_INTERACTIONS_PRUNED_PER_CALL", "100")),
    )
except ValueError:
    max_prune = 100
```

If the operator sets `JVAGENT_MAX_INTERACTIONS_PRUNED_PER_CALL=-50`, the `max(1, -50)` clamps to 1. Setting to "0" likewise clamps to 1. So the env var has no way to be "effectively disable per-call cap" (which would require setting it to a huge value). Conversely there's no upper safety bound — `=100000` runs unconstrained.

Fix: document the bounds and consider clamping to `[1, 10000]` to prevent latency footguns.

### MED-03 — `Memory._reconnect_orphaned_users` may steal users with empty `memory_id`

`jvagent/memory/manager.py:848-875`

```python
all_users = await User.find({"context.memory_id": self.id})
...
for user in all_users:
    if user.id in connected_ids:
        continue
    mem_in = await user.nodes(direction="in", node=Memory)
    if mem_in:
        continue
    user.memory_id = self.id
    await user.save()
    await self.connect(user)
```

The query `User.find({"context.memory_id": self.id})` scopes correctly. But if the index has any users whose `memory_id` was explicitly set to this Memory's id but who got disconnected (race in delete?), this reconnects them — which is fine. The dangerous case is the earlier check at `_get_user_unlocked` path: a User with `memory_id=""` connected by edge to this Memory is NOT picked up by this find. So orphan reconnect is one-directional. Not a bug, but an asymmetry worth surfacing.

### MED-04 — `Memory.users_scoped_to_this_memory` includes "legacy users with empty memory_id" — silent multi-tenant leak risk

`jvagent/memory/manager.py:171-183`

```python
return [
    u
    for u in await self.nodes(node=User)
    if not u.memory_id or u.memory_id == self.id
]
```

In a multi-tenant deployment that shares a graph DB, a User connected to two Memory nodes via edge but with `memory_id == ""` will appear in BOTH memories' `users_scoped_to_this_memory` result. `purge_user_memory`, `purge_conversations`, and `memory_healthcheck` all use this method, so:
- A purge against agent A also nukes the user (and their conversations) for agent B.
- Health stats double-count.

This may be intentional (the SPEC says `User` is unique per `(memory_id, user_id)`), but the legacy `not u.memory_id` clause undermines uniqueness for any user predating the index.

Fix: migrate legacy users to set `memory_id` once on first touch (already partially done in `get_user`); remove the `not u.memory_id` clause when migration is complete; add a one-shot backfill step.

### MED-05 — `Conversation.get_interactions(reverse=True)` and bidirectional chain integrity

`jvagent/memory/conversation.py:391-430`

When `reverse=True`, traversal starts from `get_last_interaction()` and walks `get_previous_interaction()`. If the chain is broken (e.g., a missing back-edge after a partial prune), traversal silently stops mid-chain. The chronological-forward path likewise stops at the first missing forward edge. Neither variant logs the truncation or compares the returned count against `interaction_count`. Downstream callers (e.g., `get_interaction_history`) receive truncated history without knowing.

Fix: when `len(interactions) < min(limit, interaction_count)` and `interaction_count > 0`, emit a warning log with the conv_id; consider auto-triggering `_repair_interaction_chain_invariants` if drift exceeds a threshold.

### MED-06 — `Interaction.unrecord_action_execution` does not save

`jvagent/memory/interaction.py:263-293`

The method mutates `self.actions`, `self.parameters`, `self.directives` in place but never calls `await self.save()`. Callers must remember to save. If they don't, the unrecord is lost on the next reload, and the persona prompt may still see the deleted action.

Documentation does not warn about this. The `add_event`/`add_directive`/`add_parameter` family also doesn't save — but they're typically called inside a turn where the conversation walker eventually saves the interaction. `unrecord` is the only one that mutates BOTH actions and metadata; its consumers are less obvious.

Fix: either auto-save or document the save requirement in the docstring with a code example.

### MED-07 — `Conversation.get_first_interaction` always loads every outgoing Interaction edge before sorting

`jvagent/memory/conversation.py:128-143`

```python
interactions = await self.nodes(node=Interaction, direction="out")
if not interactions:
    return None
interactions.sort(key=interaction_sort_key)
return interactions[0]
```

For a normal healthy chain there is exactly one outgoing Interaction edge, so this is fine. After a partial prune where dual edges accumulate, this method loads all of them. Not a perf hazard in v1 scope, but worth noting: an unrepaired conversation with N dual edges takes O(N) per call, and `_repair_interaction_chain_invariants` itself calls `get_first_interaction` once per conversation.

### MED-08 — `User.add_usage_from_interaction` uses lock keyed by `User.id` but counters live in `self.usage` dict

`jvagent/memory/user.py:286-329`

The lock prevents concurrent writers WITHIN a process. But the implementation reads `self.usage.get("total_tokens", 0)`, adds the delta, and writes back — classic read-modify-write. Two concurrent calls in **different processes** clobber each other. The lock is misleading: it gives the appearance of correctness in tests run on one process and breaks in production multi-worker.

See HIGH-07; this is the same issue, called out at MED severity for the in-process false-confidence aspect.

### MED-09 — `evidence_log.persist_to` does not call `conversation.save()`

`jvagent/memory/evidence_log.py:163-179`

The docstring is explicit:
> This method does NOT call ``conversation.save()``; the caller must do that.

But `SkillAction handles this automatically` (per the file's module docstring). If a caller invokes `persist_to` without save, the in-memory mutation is lost on process exit. The pattern works only because of an out-of-file contract.

Fix: either rename to `stage_to` to make the half-completion obvious, or autosave with an opt-out.

### MED-10 — `task_store.sweep_terminal` does not advance any `removed` for tasks that fail the ISO parse

`jvagent/memory/task_store.py:693-715`

```python
if older_than_seconds is not None and t.completed_at:
    try:
        completed = datetime.fromisoformat(t.completed_at)
        if (now - completed).total_seconds() > older_than_seconds:
            removed += 1
            continue
    except Exception:
        pass
kept.append(t)
```

If `t.completed_at` is malformed, the task is kept indefinitely. There's no path to clean up corrupted timestamps. Low risk, but the `pass` swallows a logging opportunity.

Fix: `logger.warning("sweep_terminal: bad completed_at on %s: %s", t.id, t.completed_at)`.

### MED-11 — `_user_context_matches` in `endpoints.py` interprets `$regex` without flags or input validation

`jvagent/memory/endpoints.py:50-53`

```python
elif "$regex" in expected:
    import re
    if not isinstance(val, str) or not re.search(expected["$regex"], val):
        return False
```

Admin-only endpoint, so the threat is limited to admin self-DoS via catastrophic regex (ReDoS). No timeout, no compile cache. An admin who pastes `(a+)+$` against a field with a long `name` will hang the worker.

Fix: cap regex length, use `re.compile` with a try/except, or run regex with a deadline. Or document that the admin endpoint trusts admins.

### MED-12 — SPEC drift: `Conversation.append_interaction` vs actual `Conversation.add_interaction`

The SPEC §5.2 and `memory-and-pruning.md` §5 all reference `Conversation.append_interaction()`. The actual method is `add_interaction()` (conversation.py:199); there is no `append_interaction`. The local CLAUDE.md inherits the same drift (memory/CLAUDE.md:28, :50, :97).

Fix: choose one name and update consistently. Renaming code is the safer option since the method has callers (`create_interaction`, distributed lock wrapper) that all use `add_interaction`.

---

## LOW

### LOW-01 — Dead import in `endpoints.py`

`jvagent/memory/endpoints.py:5`: `import json` is used only in `get_users` filter parsing. Fine. But `from jvagent.memory.user import User` (line 16) is unused in the module body outside type hints implied by `_user_context_matches` parameter — actually it is used. Skip. Real dead import would warrant a callout; none found in spot-checks.

### LOW-02 — `_normalize_dt` returns `None` only when input is `None`

`jvagent/memory/interaction.py:17-23`

Cosmetic: docstring says "handles naive/aware mix" but doesn't mention the None passthrough behavior. Minor.

### LOW-03 — `Interaction.add_directive` silently rejects empty directives but returns False — same return for "invalid input" and "duplicate"

`jvagent/memory/interaction.py:215, :206`

```python
return False  # Duplicate found, skip adding
...
return False  # Invalid input
```

Callers can't distinguish "duplicate dropped" from "garbage input rejected." If telemetry cares, you've conflated them. Trivial to fix — return None vs False, or use enums.

### LOW-04 — `Memory.get_session` Case 2 calls `_resolve_user(conversation.user_id, create=False)` after a non-existent conversation just got created

`jvagent/memory/manager.py:444-460`

The control flow:
1. `session_id` provided, `user_id` not.
2. `_resolve_conversation_for_session_or_raise_foreign(session_id)` returns None.
3. `_create_anonymous_user_and_conversation(session_id, ...)` creates new user + conv → returns.
4. (Other branch) `_resolve_user(conversation.user_id, create=False)` — only on the case-2 happy path when the conversation already existed.

The naming is fine but the `create=False` here implicitly assumes the user-row of the resumed conversation still exists. If the User was purged while a session_id remained, `_resolve_user(... create=False)` raises `RuntimeError`. That bubbles to the caller as a 500 unless they catch it. The docstring at manager.py:417 says "Raises: RuntimeError: If user creation/lookup fails" — covered, but a `session_id` outliving its owner is a stale-cookie scenario worth handling more gracefully (e.g., create a new anonymous session).

### LOW-05 — `Memory.export_memory` walks the entire chain per conversation with no pagination

`jvagent/memory/manager.py:1062-1101`

```python
interactions = await conv.nodes(node=Interaction)
for interaction in interactions:
    interaction_data = await interaction.export()
    conv_data["interactions"].append(interaction_data)
```

For a long conversation (1000+ interactions), this allocates one dict per interaction in memory and returns the full payload. Not a v1 perf issue per instructions, but on the line between correctness and performance: response builders downstream may OOM on a 100MB JSON.

### LOW-06 — `User.update_user_model` references "deprecated" field but is itself the writer

`jvagent/memory/user.py:247-267`

The field `user_model` is marked deprecated in its `attribute(description=...)` block (user.py:64-70), yet `update_user_model` continues to write to it. Cockpit's `_update_user_model` tool (cockpit/tools/memory.py:398) explicitly says it still works for back-compat and dual-writes to the new `memory` dict. The `User.update_user_model` instance method, however, only writes to `user_model`. So the cockpit's back-compat is incomplete if anything else calls `User.update_user_model` directly.

Fix: have `User.update_user_model` also mirror into `self.memory["user_model_facts"]` or similar to keep both views consistent; or mark the instance method as deprecated and route callers through the cockpit tool.

### LOW-07 — `UserLongMemory.ensure_default_categories` writes 4 nodes serially on first use

`jvagent/memory/user_long_memory.py:269-285`

First-time User → 4 sequential `create + connect` calls. Not a correctness issue, but a noticeable latency hit on every fresh user. Could be batched in a single transaction in a future patch.

### LOW-08 — `interaction_sort_key` uses `getattr(node, "id", "")` as a tiebreak — IDs are not chronologically sortable

`jvagent/memory/interaction.py:26-43`

If two interactions share `started_at` (rare but possible on fast clocks), the tiebreak by `id` may not reflect actual ordering. UUIDs and hash-based IDs are not lexically sortable by creation time. Acceptable degradation, but worth noting.

---

## SPEC drift

- **SPEC-DRIFT-01**: `SPEC.md` §5.2-§5.3 and `memory-and-pruning.md` §5 say `Conversation.append_interaction()`; the actual method is `Conversation.add_interaction()`. See MED-12.
- **SPEC-DRIFT-02**: `memory-and-pruning.md` §5 pseudocode shows the prune disconnect/connect order as: `disconnect(current)` → `connect(nxt)` → `disconnect(current, nxt)` → `delete(current)`. This matches code at conversation.py:338-345. The ADR `0003-interaction-limit-pruning.md` §Invariant 4 says the same. However, this order leaves a window where Conv has neither a first-edge nor knows about the new first interaction (between line 339 and 340). See HIGH-11 — the safer order is connect-then-disconnect.
- **SPEC-DRIFT-03**: SPEC §5.4 lists `User.user_model` as deprecated. The `User.update_user_model` instance method still exists and writes only to the deprecated field (no migration to `User.memory`). See LOW-06.
- **SPEC-DRIFT-04**: SPEC §5.1 names `lock_manager.py` as the enforcer of compound-index safety. In practice, the `Memory._get_user_unlocked` fallback path (manager.py:100-108) connects users found by `find_one` without acquiring the per-`(memory_id, user_id)` lock for the connect step — the outer `get_user` does hold the lock, so this is OK in single-process. In multi-process without distributed locking, two workers each running `get_user` for the same key can both find the same unlocked User row and both call `connect`. Subsequent state mutations may diverge.
- **SPEC-DRIFT-05**: SPEC §5 does not describe distributed locking for `add_interaction`. `distributed_conversation_lock.py` is referenced in the local CLAUDE.md as a future-proof feature but the SPEC doesn't state that it's required for production. Operators may run without it, hitting HIGH-04.

---

## Strengths

(Per instructions, no praise paragraphs. Skipped intentionally.)

---

## Out of scope

- Performance optimization of pruning (per v1 instructions).
- Storage-backend internals (handled by jvspatial).
- HTTP/auth wire format beyond the authz bypass in CRIT-01.
- The cockpit's memory tools (separate audit; see `jvagent/action/cockpit/`).

---

_End of audit._
