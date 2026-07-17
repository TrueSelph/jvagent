# ADR-0033 — Identity & Locking Substrate for Bootstrap and Memory

- **Status:** Proposed
- **Date:** 2026-07-16
- **Supersedes / amends:** contracts in `jvagent/core/CLAUDE.md` §3, `jvagent/memory/CLAUDE.md` §3/§7 (the "compound index rejects on save" claim), and the singleton-enforcement narrative in `jvagent/action/actions.py`.
- **Related:** review [`.planning/reviews/2026-07-16-core-review.md`](../reviews/2026-07-16-core-review.md); ADR-0003 (interaction pruning), ADR-0020 (public auth / session tokens).

## Context

jvagent declares uniqueness and mutual-exclusion guarantees that the runtime does not enforce on the shipped defaults:

1. **Unique indexes are advisory on the default adapter.** `Action` declares a unique `agent_label` compound index and `Agent`/`User` declare unique indexes, but the default `json` adapter (`cli/server_config.py:44`) implements `create_index` as a documented no-op. So `find_one`-then-`create` races persist real duplicates. The user-reported symptom — duplicate action nodes for singleton action types — is the visible tip.

2. **Existence checks are blind to unimported subclasses.** `Action.find_one` filters by the set of *imported* `Action` subclasses (jvspatial `_build_database_query` → `__subclasses__()`). A persisted action whose concrete class is not imported at check time is invisible, so the check misses and a duplicate is created. Reconciliation (`agent_loader._reconcile_actions`) uses raw records correctly but runs only under `--update`; a plain `jvagent` restart never converges.

3. **Index key shapes disagree with identity.** `agent_label` is `(agent_id, label)` but identity is `(agent_id, namespace, label)`; `Agent` uniqueness is on `name` but install identity is `(namespace, name)`. On Mongo this throws E11000 and the action is silently lost (all exceptions swallowed in `register_action`).

4. **Locks are per-instance / per-process.** `Actions._lock` is an `asyncio.Lock` attribute on a deserialized node instance — not shared across workers, replicas, or even two in-memory copies of the same node. There is no distributed lease around bootstrap. The one real distributed lock (`distributed_conversation_lock`) has a 45s TTL with no renewal, shorter than a long orchestrator turn, and its holder contextvar leaks into background tasks.

The consequence is a broad class of duplicate-node and lost-update bugs across bootstrap and memory (see the review's high-severity band, C1–C9). Fixing individual call sites is whack-a-mole; the substrate must provide identity and locking primitives the rest of the code can rely on.

## Decision

### 1. Canonical identity tuples

- **Action:** `(agent_id, namespace, label)`.
- **Agent:** `(namespace, name)`.
- **User:** `(memory_id, user_id)`.
- **Conversation:** `(session_id)` (globally unique) — with the owning `Memory` as the scoping parent for reads.

Correct the compound-index key shapes to match (add `namespace` to `agent_label`; scope Agent uniqueness to `(namespace, name)`). Index shape is corrected regardless of adapter so Mongo deployments stop losing same-label-different-namespace actions.

### 2. Adapter-agnostic upsert-by-identity

Add a shared `upsert_by_identity(entity_cls, identity: dict, defaults: dict)` helper that:

- Resolves existing rows by **raw** query on `context.*` identity fields, **not** via `find_one`'s subclass-name filter (query with an explicit `entity`/type or raw record read), so unimported subclasses are still seen.
- Performs the create only while holding the appropriate lock (see §3).
- Returns `(node, created: bool)`.

Every ensure/create pair migrates to it: `install_agent`, `_ensure_actions_node`, `_ensure_memory_node`, `_ensure_agents_node`, `register_action`'s existence/singleton checks, `Memory.get_user`, `get_session`.

### 3. Locking

- **Distributed bootstrap lease.** Wrap graph bootstrap (`pre_startup_bootstrap` → `bootstrap_application_graph`) in a distributed lease keyed on app id so only one worker/replica mutates the graph; others wait then read. Falls back to a process lock when no distributed backend is configured (documented single-writer assumption for `json`).
- **Turn-lock lease renewal.** The conversation mutation lock heartbeats/extends while the turn runs; lease TTL is set above the max plausible turn and a real `max_duration_seconds` is enforced so a hung turn cannot hold the lock forever.
- **Contextvar hygiene.** Clear the lock-holder contextvar when spawning `run_in_background` tasks so a background task cannot inherit the holder and mutate the chain unlocked.

### 4. Boot-time reconciliation on every start

Run a lightweight identity-reconcile (dedupe by identity tuple, using raw records) on **every** boot, not only under `--update`, so a plain restart converges any duplicates left by earlier races or partial installs.

## Consequences

- **Positive:** the duplicate-singleton class (C1–C6) and the lost-update / double-claim class (C7–C9, H18) are closed at the substrate. Mongo deployments stop silently losing actions. The default `json` adapter gets a real single-writer guarantee via the bootstrap lease.
- **Costs:** raw-record queries are slightly more code than `find_one`; a distributed lease adds a dependency for multi-replica correctness (optional, with a documented single-writer fallback). Boot-time reconcile adds bounded startup work.
- **Contract changes:** update `core/CLAUDE.md` and `memory/CLAUDE.md` to state that uniqueness is enforced by the application layer (upsert-by-identity + lease), and remove the false "compound index rejects on save" claim for the default adapter.
- **Test debt:** requires the currently-absent concurrency suite (duplicate create, lease expiry, contextvar reentrancy, double-claim) — treated as acceptance criteria, not follow-up.

## Alternatives considered

- **Require a uniqueness-enforcing adapter (Mongo/Postgres) in production.** Rejected as the sole fix: it abandons the default `json`/`sqlite` deployments the CLI ships, and does not address the unimported-subclass blindness (#2) or the lock lease/leak issues.
- **Fix each call site independently.** Rejected: does not converge existing duplicates, and the same race reappears at the next un-migrated site.
