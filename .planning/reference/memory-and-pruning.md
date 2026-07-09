# Memory & Pruning

> Deep dive on `User` / `Conversation` / `Interaction` lifecycle and the rolling-window pruning mechanism. Companion to [`SPEC.md`](../SPEC.md) §5, [`jvagent/memory/CLAUDE.md`](../../jvagent/memory/CLAUDE.md), [`adr/0003-interaction-limit-pruning.md`](../adr/0003-interaction-limit-pruning.md).

---

## 1. The memory subgraph

```
Agent
 └─ Memory               (singleton per agent)
      └─ User            (one per memory_id+user_id; compound unique index)
           └─ Conversation  (one per session_id per user)
                └─ Interaction*   (bidirectionally chained)
```

Files:
- `memory/manager.py:18` — `Memory` node (`get_user()` at `manager.py:76`, locks)
- `memory/user.py:25` — `User` node (compound index at lines 16–24)
- `memory/conversation.py:39` — `Conversation` node
- `memory/interaction.py:51` — `Interaction` node
- `memory/lock_manager.py` — per-`(memory_id, user_id)` async lock

---

## 2. User identity

A `User` is identified by **two fields together**:

| Field | Source | Purpose |
|---|---|---|
| `memory_id` | `Memory.id` of the agent's Memory node | Scopes the user to one agent's memory |
| `user_id` | Caller-provided (often from auth, channel, or session) | The logical user identifier |

The compound unique index at `user.py:16-24` enforces `(memory_id, user_id)` uniqueness. Concurrent creates with the same pair MUST go through `Memory.get_user()` ([`manager.py:76-97`](../../jvagent/memory/manager.py)), which acquires the per-pair lock before falling through to `_get_user_unlocked()`.

### What's on the User node

| Field | Type | Scope |
|---|---|---|
| `memory: Dict[str, str]` | dict[k → markdown blob] | cross-session, persistent |
| `memory_tags: Dict[str, List[str]]` | tag → memory key list | cross-session |
| `usage` | dict | aggregate usage counters |
| `name`, `display_name` | strings | display info |
| `created_at`, `last_seen` | datetimes | timestamps |

---

## 3. Conversation

One per `session_id` per user. Created lazily.

### What's on the Conversation node

| Field | Purpose |
|---|---|
| `session_id` | Session key (UUID, channel-thread-id, etc.) |
| `user_id` | Denormalized for query convenience |
| `status` | active / closed / archived |
| `channel` | originating channel (web / whatsapp / messenger / email / ...) |
| `created_at`, `last_interaction_at` | timestamps |
| `interaction_count` | running count of `Interaction`s in this conversation |
| `interaction_limit` | rolling-window cap; overrides agent default; `0` disables pruning |
| `context` | dict — session-scoped state |
| `last_interaction_id` | reference to most recent `Interaction` (for fast tail access) |

### Chain semantics

The first Interaction connects to the Conversation directly:

```
Conv ──out──> I1
```

When `I2` is appended:

```
Conv ──out──> I1 <──both──> I2
```

When `I3` is appended:

```
Conv ──out──> I1 <──both──> I2 <──both──> I3
```

Source: [`conversation.py:315-317`](../../jvagent/memory/conversation.py). The first edge is `direction="out"`; subsequent edges are `direction="both"`.

---

## 4. Interaction

One per user-message ⇄ agent-response exchange. Stores the full execution trace.

| Field | Purpose |
|---|---|
| `utterance` | user input text |
| `response` | final agent text response |
| `canned_response` | quick reply prior to engine output |
| `image_interpretation` | vision pre-processing (if any) |
| `actions: List` | record of which actions ran |
| `directives: List` | accumulated directives (from `add_directives`) |
| `events: List` | structured events during the turn |
| `parameters: List` | active parameters this turn |
| `observability_metrics: Dict` | aggregated model calls, embeddings, latencies, errors |
| `usage: Dict` | token counts + model-call tallies |
| `artifacts: Dict` | session-scoped structured data (pruned with the interaction) |
| `started_at`, `completed_at` | timestamps |
| `closed: bool` | whether the turn is finalized |
| `channel`, `session_id` | denormalized from Conversation |

---

## 5. Pruning algorithm

### When it runs

`Conversation.add_interaction()` ([`conversation.py:235`](../../jvagent/memory/conversation.py); locked variant `_add_interaction_unlocked` at 285) calls `_prune_old_interactions()` when:

```
self.interaction_limit > 0
AND self.interaction_count > self.interaction_limit
```

If `Agent.interaction_limit` is set and differs from `Conversation.interaction_limit`, the conversation adopts the agent's value first ([`conversation.py:326-330`](../../jvagent/memory/conversation.py)). Setting `interaction_limit = 0` on either disables pruning entirely.

### How it runs

```python
to_remove   = interaction_count - interaction_limit
max_prune   = min(to_remove, JVAGENT_MAX_INTERACTIONS_PRUNED_PER_CALL)   # default 100
walk_from   = await self.get_first_interaction()
removed     = 0

while current and removed < to_remove and removed < max_prune:
    nxt = await current.get_next_interaction()
    if nxt is None:
        break                       # NEVER remove the last interaction
    # Rewire: Conv ──> nxt  (was Conv ──> current)
    if await self.is_connected_to(current):
        await self.disconnect(current)
    await self.connect(nxt, direction="out")
    if await current.is_connected_to(nxt):
        await current.disconnect(nxt)
    await current.delete()          # cascade-deletes the interaction + its artifacts
    removed += 1
    self.interaction_count -= 1
    current = nxt

# Repair last_interaction_id if stale
if self.last_interaction_id and not await Interaction.get(self.last_interaction_id):
    last = await self._find_last_interaction()
    self.last_interaction_id = last.id if last else None

await self.save()
return removed
```

Source: [`conversation.py:490-575`](../../jvagent/memory/conversation.py).

### Invariants

1. **Never delete the last `Interaction`** ([`conversation.py:538-543`](../../jvagent/memory/conversation.py)). If `nxt` is `None`, halt.
2. **Bounded per call** by `JVAGENT_MAX_INTERACTIONS_PRUNED_PER_CALL` (default 100). Anything beyond runs on subsequent appends or via the manager.
3. **Edge rewiring is atomic-ish per iteration** — disconnect then connect, then delete. Failure mid-iteration may leave a temporarily inconsistent edge set but `_find_last_interaction()` can recover.
4. **`interaction_count` decrements with each removal.** Don't manually mutate it elsewhere.
5. **`last_interaction_id` is verified post-prune.** Stale references are rebuilt via traversal.

---

## 6. Tuning the window

| Config | Where | Effect |
|---|---|---|
| `Agent.interaction_limit` | `agent.yaml` → `interaction_limit:` field | Default cap for all conversations |
| `Conversation.interaction_limit` | At runtime (API) | Per-conversation override; takes precedence |
| `JVAGENT_MAX_INTERACTIONS_PRUNED_PER_CALL` | env | Per-call bound on work; trades completeness for latency |

### Recommended values

| Scenario | Suggested `interaction_limit` |
|---|---|
| Quick smalltalk bots | 50 |
| Customer support | 100 — 200 |
| Long autonomous tasks | 500+ (or disable: `0`) |
| Tests | 10 (faster turnover) |

Higher = richer history = larger context + more storage. Lower = tighter context window, faster traversal, less storage.

---

## 7. Bulk re-pruning

Lowering `interaction_limit` on an existing agent does NOT immediately prune everyone. It applies on each `Conversation`'s next `add_interaction()`. To force pruning across all users of an agent, call:

```python
await memory.apply_interaction_limit_pruning_for_connected_users()
```

This iterates Users → Conversations → triggers `_prune_old_interactions()` for each. Same per-call cap applies; large fleets may need multiple passes.

---

## 8. Distributed locking

Single-process: `lock_manager.py` provides per-`(memory_id, user_id)` `asyncio.Lock`. Sufficient when one worker handles a user at a time.

Multi-process: see `memory/distributed_conversation_lock.py`. When enabled, locks acquire a distributed token before `Conversation.add_interaction`. Configure via env vars (see [`configuration-keys.md`](configuration-keys.md)).

---

## 9. Long-term memory

Distinct from the rolling-window conversation memory:

- `User.memory: Dict[str, str]` — cross-session key → markdown blob. Orchestrator memory tools (`memory_set`, `memory_get`, …) write here. Cross-conversation; survives pruning.
- `User.memory_tags` — tag index over `memory`.
- **Domain knowledge** — use `PageIndexAction` tools (`pageindex__search`, `pageindex__assimilate`) via Orchestrator skills, not per-user long-memory graph nodes (removed in 0.1.1).

---

## 10. Testing pruning

Critical regression suites:

- `tests/test_comprehensive_pruning.py` — full coverage of cap + invariants.
- `tests/test_pruning_fix.py` — specific bug regressions.
- `tests/test_interview_path_pruning_and_convergence.py` — pruning interacting with branching.

Run before any pruning-related change:
```bash
pytest tests/test_comprehensive_pruning.py tests/test_pruning_fix.py tests/test_interview_path_pruning_and_convergence.py -v
```

---

## 11. Reading code in order

1. `memory/manager.py:76-135` — `get_user()` entry.
2. `memory/conversation.py:235-330` — append + chain.
3. `memory/conversation.py:490-575` — prune.
4. `memory/lock_manager.py` — locking primitives.
5. `memory/distributed_conversation_lock.py` — multi-process variant.
6. `memory/interaction.py` — Interaction shape + helpers.
