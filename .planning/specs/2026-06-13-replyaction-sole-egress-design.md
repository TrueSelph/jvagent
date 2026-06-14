# ReplyAction as jvagent's single output contract (retire PersonaAction) — design

**Date:** 2026-06-13
**Status:** Proposed (forward-only — no PersonaAction compatibility retained)
**Scope:** `jvagent/action/reply/`, `jvagent/action/persona/` (delete), `jvagent/action/base.py` (`get_responder`), `jvagent/action/interact/base.py` (`respond`), rails IAs with direct PersonaAction coupling (`handoff`), `jvagent/memory/interaction.py` (directive `standalone` flag), scaffold profiles, examples, tests.

---

## 1. Context

jvagent has two output idioms: (A) the orchestrator's model authors a reply from `response_directive` guidance; (B) rails IAs queue `interaction.directives` and a responder voices them. ADR-0014 split PersonaAction's three concerns — identity → Agent node, egress → ReplyAction, Rails-coordination → *retained in PersonaAction*. That retained coordinator is the last competing authority and the seam behind the duplicate-egress class fixed in ADR-0024.

**Decision (owner):** retire PersonaAction. ReplyAction is jvagent's single output contract: the one entity that **gathers all queued response directives across IAs/skills and the orchestrator and delivers one unified output**. `publish` is reframed as the **single-directive fast path** (no compose model call when exactly one directive and no shaping apply).

## 2. Goals / non-goals

**Goals**
- ReplyAction is the sole responder; `get_responder()` never returns PersonaAction.
- One output contract: queue a directive → ReplyAction gathers → one unified emission/turn (with the ADR-0024 `emitted` latch enforcing "one").
- `publish` = the N=1 internal fast path of that same contract, not a parallel egress.
- Deliberate multi-message turns remain expressible via an explicit `standalone` directive flag.
- PersonaAction package + its dedicated tests are deleted; the stack runs without it.

**Non-goals**
- Backward compatibility with PersonaAction (forward-only).
- Re-architecting the orchestrator loop (only its responder resolution + egress).
- Changing the interview/skill `response_directive` contract (model-facing guidance unchanged).

## 3. Locked decisions

| # | Decision | Choice |
|---|---|---|
| 1 | Responder | **ReplyAction only.** `get_responder()` returns the enabled ReplyAction; no PersonaAction fallback. Agents must enable ReplyAction. |
| 2 | Output contract | **`interaction.directives` is the one output queue.** ReplyAction gathers all queued directives + params + any passed text into one voiced compose. |
| 3 | `publish` | **N=1 fast path** — literal publish of a single directive/text with no shaping; not a caller-facing parallel channel. |
| 4 | Multi-message | **`standalone` directive flag** — a directive marked standalone is emitted as its own message immediately (e.g. catalog content), separate from the gathered compose. Default: gather-into-one. |
| 5 | History | **ReplyAction subsumes the history-aware respond** signature (`use_history`, `history_limit`, `with_*`, `max_statement_length`) so it replaces PersonaAction for rails agents. |
| 6 | PersonaAction | **Deleted** — package, endpoints, prompt builder, dedicated tests; references removed/migrated. |

## 4. The output contract (canonical)

```
Any IA / skill / orchestrator that wants to say something:
    visitor.add_directive("Tell the user: …", action_name)   # or standalone=True
        │
        ▼  (turn end / egress)
ReplyAction gathers ALL unexecuted user directives for this turn
        │
        ├─ standalone directive(s)  → publish each literally, immediately (own message)
        ├─ exactly 1 gathered, no shaping (params/format) → publish literal (N=1 fast path; no model call)
        └─ ≥1 gathered with shaping / multiple → respond(): one compose model call,
              voicing all directives + params + channel format in the Agent identity
        │
        ▼
response_bus → SSE / channel adapter   (sets interaction.emitted — ADR-0024)
```

`reply(text)` becomes "queue the text as a directive, then gather-and-emit" — uniform with every other producer; `publish` is the internal N=1 optimization it calls when no shaping applies.

## 5. Detailed design

### 5.1 `Interaction` directive `standalone` flag
`add_directive(directive, action_name, *, standalone=False)` stores `{"action_name", "content", "executed", "standalone"}`. `get_unexecuted_directives()` unchanged; add `get_standalone_directives()` / filter helpers. Standalone directives are delivered as their own message (preserves the catalog + closing multi-message case without a second uncontrolled egress).

### 5.2 ReplyAction = sole responder (`get_responder`)
`Action.get_responder()` ([base.py:232-254](../../jvagent/action/base.py)) returns the enabled ReplyAction; remove the PersonaAction import + fallback. If no ReplyAction is enabled, return None (callers already null-guard) — agents must enable it.

### 5.3 ReplyAction.respond subsumes the history-aware signature
`interact/base.py respond()` ([:514-549](../../jvagent/action/interact/base.py)) branches on `isinstance(responder, PersonaAction)` vs `ReplyAction`. Collapse to one branch calling `ReplyAction.respond(...)`. ReplyAction.respond accepts `use_history`, `history_limit`, `with_utterance`, `with_interpretation`, `with_event`, `with_response`, `max_statement_length`, `transient` — loading conversation history for rails agents (port PersonaAction's `_get_conversation_history` into ReplyAction or a shared `reply/history.py` helper). Under the orchestrator, history is already in the loop prompt, so these default off and the call stays lean.

### 5.4 ReplyAction gathers (not just applies passed inputs)
Today ReplyAction applies the directives/params already on the interaction in its compose — that IS the gather. Make it explicit: at egress, ReplyAction reads ALL unexecuted user directives, marks them executed on compose, and emits once. Confirm the orchestrator's `_egress` calls ReplyAction such that every queued directive composes together (no per-directive emission).

### 5.5 Migrate direct PersonaAction consumers
- `handoff_interact_action.py:260` — replace `PersonaAction._get_conversation_history` with the shared history helper from §5.3.
- `parameters.py`, `model/context.py`, `core/agent.py`, `core/profiling.py`, `vectorstore/base.py`, channel actions — remove PersonaAction imports/branches; route through ReplyAction or the Agent identity/params subsystem.
- `get_capabilities` docstrings ([base.py:221-224](../../jvagent/action/base.py)) — reword "PersonaAction aggregates" → "ReplyAction/identity aggregates".

### 5.6 Scaffolding + examples
`scaffold/builtin_profiles/{minimal,orchestrator,research}.yaml` and `examples/jvagent_app/.../agent.yaml` — replace PersonaAction with ReplyAction in the action set; update READMEs/architecture docs.

### 5.7 Delete PersonaAction
Remove `jvagent/action/persona/` (action, endpoints, prompt_builder, prompts, info.yaml, README) and its dedicated tests (`test_persona_*.py`). Update `tests/CLAUDE.md`, `action/CLAUDE.md`, `interact/CLAUDE.md` egress decision trees to ReplyAction.

## 6. Files touched

Modify: `reply/reply_action.py` (history subsume, gather, N=1 publish), `reply/history.py` (new helper), `memory/interaction.py` (standalone flag), `base.py` (`get_responder`, docstrings), `interact/base.py` (respond collapse), `handoff_interact_action.py`, `parameters.py`, `model/context.py`, `core/agent.py`, `core/profiling.py`, scaffold profiles, examples, CLAUDE.md egress trees.
Delete: `jvagent/action/persona/**`, `tests/action/test_persona_*.py`.
ADR: refine ADR-0024 / new ADR-0025 "ReplyAction is the single output contract."

## 7. Forward-only phases

- **A — standalone flag** (`Interaction.add_directive(..., standalone=)` + helpers). Additive, low-risk.
- **B — ReplyAction.respond subsumes history** signature + helper; `interact/base.py respond()` collapses to one ReplyAction branch.
- **C — `get_responder` → ReplyAction only**; remove PersonaAction fallback + isinstance.
- **D — migrate direct consumers** (handoff history, parameters/model/agent/profiling references).
- **E — scaffold profiles + examples → ReplyAction**.
- **F — delete `persona/` + dedicated tests**; update CLAUDE.md egress trees.
- **G — full verification** + ADR.

Each phase keeps the suite green (excluding the unrelated `web_fetch` dep gap). Phases A-C make ReplyAction sufficient before any deletion.

## 8. Testing

- `standalone` directive emits as its own message; gathered directives compose into one (extend `tests/action/reply/`).
- `get_responder` returns ReplyAction; returns None when ReplyAction disabled (no PersonaAction).
- Rails IA (e.g. converse) voices via ReplyAction with history when `use_history=True`.
- Orchestrator egress: one emission/turn unchanged (ADR-0024 tests stay green).
- Suite green after PersonaAction deletion; no import of `jvagent.action.persona` remains (grep-guard test).

## 9. Risks / guardrails

- **History parity:** rails agents relied on PersonaAction loading history in `respond`. ReplyAction must reproduce it (§5.3) or rails replies lose context. Port, don't drop.
- **Multi-message regression:** the catalog + closing case must use `standalone` (§5.1); verify product-skill closing-line tests.
- **Wide reference surface:** ~45 source + 16 test files mention PersonaAction; most are docs/comments. Phases D-F sequence the real code cuts; a grep-guard test (§8) proves completeness.
- **Agents missing ReplyAction:** with no fallback, an agent without ReplyAction is mute. Scaffold defaults (§5.6) + a validate-time check (agent must enable a responder) mitigate.

## 10. Acceptance criteria

1. `get_responder()` returns ReplyAction only; no `jvagent.action.persona` import remains in source (grep-guard).
2. One output contract: queuing directives yields one gathered emission; a `standalone` directive yields its own message; the `emitted` latch still guarantees no accidental double.
3. Rails IAs voice via ReplyAction with history parity.
4. `jvagent/action/persona/` and `tests/action/test_persona_*.py` deleted; suite green (excluding `web_fetch`).
5. Scaffold profiles + examples enable ReplyAction; ADR recorded.
