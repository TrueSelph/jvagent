# jvagent/memory/ — Agent Guide

> Local guide for the memory subsystem. Cross-link: [`/CLAUDE.md`](../../CLAUDE.md), [`/.planning/memory-and-pruning.md`](../../.planning/reference/memory-and-pruning.md), [`/.planning/adr/0003-interaction-limit-pruning.md`](../../.planning/adr/0003-interaction-limit-pruning.md).

---

## 1. What this directory owns

The per-agent, per-user state graph:

```
Memory (per-agent) → User (per memory_id+user_id) → Conversation (per session_id) → Interaction*
```

Plus the locking, pruning, and persistence helpers that keep this graph consistent under concurrency.

---

## 2. Key files

| File | Purpose |
|---|---|
| `manager.py:18` | `Memory` node + `Memory.get_user()` (locks → unlocked fetch) |
| `manager.py:60-100` | User lookup; lock-then-fetch |
| `user.py:25` | `User` node — `memory_id`, `user_id`, `memory`, `memory_tags` |
| `user.py:16-24` | Compound unique index on `(memory_id, user_id)` — DO NOT DROP |
| `conversation.py:39` | `Conversation` node |
| `conversation.py:199-238` | `add_interaction()` — public wrapper with conversation_mutation_lock |
| `conversation.py:240-295` | `_add_interaction_unlocked()` — chain edges + trigger prune |
| `conversation.py:297-367` | `_prune_old_interactions()` — bounded-work rolling window |
| `interaction.py:47` | `Interaction` node — utterance, response, actions, directives, events, parameters |
| `lock_manager.py` | Per-`(memory_id, user_id)` async lock to prevent duplicate User creation |
| `distributed_conversation_lock.py` | Cross-process conversation lock (when configured) |
| `user_long_memory.py` | Helpers for the long-memory PageIndex pattern |
| `long_memory_retrieval_utils.py` | Utilities for vectorless RAG retrieval |
| `task_store.py` | Task node CRUD on Conversation/Interaction |
| `evidence_log.py` | Evidence / citation logging for memory |
| `services/` | Memory-related service helpers |
| `endpoints.py` | HTTP routes for user/conversation/interaction queries |
| `README.md` | Existing user-facing memory notes — keep, don't duplicate here |

---

## 3. Contracts (don't break)

1. **`User` is unique per `(memory_id, user_id)`.** The compound index at `user.py:16-24` enforces this — never drop it. Concurrent creates MUST go through `lock_manager`.
2. **First `Interaction` connects to `Conversation` with `direction="out"`** ([`conversation.py:272`](conversation.py)). Subsequent ones connect to the previous `Interaction` with `direction="both"` ([`conversation.py:270`](conversation.py)).
3. **Pruning never removes the last `Interaction`** ([`conversation.py:333-336`](conversation.py)). If `next_interaction` is `None`, stop.
4. **Pruning is bounded per call** by `JVAGENT_MAX_INTERACTIONS_PRUNED_PER_CALL` (default 100, [`conversation.py:317-323`](conversation.py)). The remainder happens on subsequent appends or via `Memory.apply_interaction_limit_pruning_for_connected_users`.
5. **`Agent.interaction_limit = 0` disables pruning entirely.** Code paths MUST early-return when limit is `0`.
6. **`Conversation.interaction_count` and `last_interaction_at`** are written together with the edge insert in `add_interaction()` ([`conversation.py:272-277`](conversation.py)). Don't update one without the other.
7. **`User.user_model` is deprecated.** New code should use `User.memory` (dict) + `User.memory_tags`.

---

## 4. Pruning math (memorize)

```
to_remove   = interaction_count - interaction_limit
max_prune   = min(to_remove, JVAGENT_MAX_INTERACTIONS_PRUNED_PER_CALL)
walk from   = first interaction
stop when   = removed == max_prune  OR  next_interaction is None
```

After pruning, `last_interaction_id` is verified — if stale, rebuilt by traversal ([`conversation.py:354-364`](conversation.py)).

---

## 5. Adding to this directory

| If you're adding... | Where |
|---|---|
| A new field on User/Conversation/Interaction | Add via `attribute(...)`. Update `endpoints.py` query shape if external. |
| A new pruning strategy | Modify `_prune_old_interactions`; preserve bounded-work + last-interaction invariants. Add a regression test in `tests/test_comprehensive_pruning.py`. |
| Cross-conversation memory query | Use `Memory.get_user()` + walk; do not bypass locks. |
| Distributed locking | Extend `distributed_conversation_lock.py`. |

---

## 6. Tests

- `tests/memory/` — unit tests for User/Conversation/Interaction CRUD.
- `tests/test_comprehensive_pruning.py` — full pruning regression suite.
- `tests/test_interview_path_pruning_and_convergence.py` — branching + pruning interaction.
- `tests/test_pruning_fix.py` — specific pruning bug regression.

```bash
pytest tests/memory/ tests/test_comprehensive_pruning.py -v
```

---

## 7. Traps specific to memory/

| Trap | Fix |
|---|---|
| Creating a User without acquiring the lock | Duplicate rows; compound index rejects on save | Use `Memory.get_user()` which locks first |
| Editing `interaction_count` directly | Drift from actual edge count | Always mutate via `add_interaction` / `_prune_old_interactions` |
| Adding fields without `attribute()` | Not persisted | Use `attribute(...)` |
| Spawning a Walker over a long Conversation without limits | Walker `max_steps` (10000) trips | Either bound the traversal or paginate manually |
| Deleting an Interaction mid-chain manually | Leaves dangling bidirectional edges | Use the pruning routine or rewire both sides |
| Touching `User.user_model` | Deprecated path | Move new state into `User.memory` + tags |

---

## 8. Don't touch from outside memory/

- The compound index at `user.py:16-24` — call sites elsewhere assume `(memory_id, user_id)` uniqueness.
- `_prune_old_interactions` invariants — there are subtle correctness tests guarding these.
- `lock_manager` — bypassing produces duplicate Users that the DB then rejects, causing intermittent failures.

---

## 9. Out of scope here

- Action plugins: see `jvagent/action/CLAUDE.md`.
- The Executive's Skills center memory tools wrap this layer.
- PageIndex (long-memory document index): see `jvagent/action/pageindex/`.
