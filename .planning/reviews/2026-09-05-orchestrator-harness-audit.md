# Orchestrator harness audit — 2026-09-05

**Scope:** the agentic core — `jvagent/action/orchestrator/` (loop, tool surface,
continuation, prompts), the model integration it depends on
(`jvagent/action/model/language/`), and the walker/egress path that carries a
turn (`jvagent/action/interact/`, `jvagent/action/reply/`). Channel adapters,
PageIndex, WhatsApp, scaffold and CLI were out of scope; the 2026-09-01 full
code review already covers them.

**Question asked:** is the Orchestrator a sound, predictable engine for running
skills and tools with mainstream models, with the failsafes a production
harness needs — and is the harness around it streamlined now that Rails is gone
(ADR-0029)?

**Baseline at audit time (main @ c2f9cec5):**

| Gate | Result |
|---|---|
| `pytest tests/ --ignore=tests/action/pageindex` | 3418 passed, 3 skipped |
| `pytest tests/action/orchestrator` | 533 passed, 1 skipped |
| Rails (`InteractRouter`, directive IAs, long memory) | removed in 0.1.1 — nothing left to strip |

---

## 1. Verdict

The Orchestrator is architecturally right (one loop, one tool surface, routing
= tool selection, deterministic turn-lock as a surface restriction) and its
guard set is unusually complete — repeat guard, chain contract, plan-drain,
grounding, budget/duration, partial-compose + salvage, locked-flow escape
streak, orphan sweep. What holds it below mainstream harness quality is **how it
talks to the model**, not what it does with the answer:

1. **Decisions ride as JSON-in-text, not native tool calls.** The loop renders
   tools as `- name: description` lines with **no argument schema**, asks the
   model to emit one JSON object, and parses it back. Every mainstream harness
   (OpenAI Agents SDK, Anthropic tool runner, Claude Code, LangGraph,
   smolagents' tool-calling agent) uses the provider's function-calling API with
   JSON-Schema'd tools and tool-result messages. The model layer already
   supports that end to end (`tools=`, `result.tool_calls`, Anthropic/Ollama
   message normalisation) — the orchestrator passes `tools=None`
   (`orchestrator_interact_action.py:3477`).
2. **The tolerance code is the symptom.** Because the model never sees argument
   names, the harness grew alias scanning and coercion everywhere:
   `_egress_exec` tries six text keys, `_coerce_plan_items` nine list keys,
   `_normalize` folds flattened calls and rewrites skill names, `use_skill`'s
   description embeds a JSON call example. That is "wobble" compensated in the
   server rather than removed at the source.
3. **A model outage is diagnosed as a parse failure.** `_run_model` swallows any
   exception and returns `None`; the loop then tells the model "Your previous
   response was not a single valid JSON object" and, after three misses, ends
   the turn with *"Sorry, I didn't quite catch that — could you rephrase?"*.
   The user is told they were unclear when the provider was down.
4. **No default wall-clock or per-tool bound.** `tool_call_timeout=0` and
   `max_duration_seconds=0` ship as defaults; a hung tool hangs the turn (and
   the conversation lock) indefinitely. Only the model HTTP client (120 s) and
   the tick budget bound a turn.
5. **The foundation is still interview-shaped.** `continuation.py` imports
   `jvagent.action.interview.{tasks,reaper,session}` and resolves an
   `InterviewAction` by type; `_append_directive_hint` imports
   `interview.hooks.append_hint`; `skill_tasks.py` calls
   `bound._clear_interview_session` and matches `"interview_type"`;
   `_result_is_completion` reads `interview_complete`. The guard test
   (`test_no_interview_coupling.py`) greps only `interview__|interview_action|set_field`
   so all of this passes. Thin-harness invariants 6 and 8 are violated.

Everything in §3 was verified by reading the code paths cited; nothing below is
from the earlier reviews unless marked.

---

## 2. What is sound (keep)

- **Turn shape.** One `execute()`, curate walk path → assemble surface →
  locked dispatch or loop → egress (`orchestrator_interact_action.py:733`).
  `TurnState` split (`loop.py:_prepare_turn`) makes prep vs tick explicit.
- **Deterministic continuation.** `active_flow_owner` is a pure TaskStore read;
  locked dispatch makes zero model calls (`test_flow_e2e.py`).
- **Bounded loop.** `activation_budget` (24), repeat guard, `nd_streak` (3),
  chain/plan/grounding deflection caps, partial-compose on exhaustion, salvage
  when finalize still returns a tool call, locked-flow escape after 2
  consecutive dead turns, orphan sweep skipped when action enumeration failed.
- **Directive trust boundary.** `next_tool`/`response_directive` honoured only
  from first-party namespaces (`constants.is_untrusted_directive_source`).
- **Surface policy.** One `ToolSurfacePolicy` for deny / MCP selection /
  AC labels shared by assembly and late materialisation; skill-only gate
  wraps the tool object so every dispatch path is covered.
- **Model layer.** Retries with Retry-After honoured, per-loop HTTP client,
  streaming tool-call assembly on OpenAI/Anthropic/Ollama, cache-token
  accounting, `tool`/`tool_calls` role normalisation for Anthropic and Ollama.
- **Prompt engineering is measured, not guessed.** Section order, operating-
  rules position and the user-turn reminder each cite an A/B
  (`prompts.py:12-33`, `docs/ORCHESTRATOR.md` "Prompt cost").
- **Prior review follow-through.** Of the 2026-09-01 orchestrator findings,
  C5 (prompt cache on `self` → `ContextVar`), C6 (`_normalize` type checks),
  `wrap_action_tool` access-label inheritance and the continuation streak
  persistence are fixed on main.

---

## 3. Findings

Severity: **H** = wrong or unsafe in production, **M** = degrades quality or
predictability, **L** = hygiene. "Fixed" marks items addressed in this pass
(branch `audit/orchestrator-harness`).

### 3.1 Model integration (the engine ↔ model contract)

| # | Sev | Where | Finding | Status |
|---|---|---|---|---|
| M1 | H | `orchestrator_interact_action.py:3477` (`"tools": None`), `prompts.py:36-52`, `tools.py:SkillTool` | Decisions are JSON-in-text; tools are surfaced with no argument schema (`SkillTool` drops `Tool.parameters_schema`). Model guesses argument names; provider cannot validate; parse failures on prose/fences/truncation; no tool-result role so results sit inside the *user* turn (weaker grounding, larger injection surface — the SAFEGUARDS reminder exists to patch this). | **Fixed** — native tool-calling protocol (`tool_protocol: native`, default) with JSON-text fallback (`json`). Schemas carried on `SkillTool` and serialised per provider; observations replayed as assistant `tool_calls` + `tool` messages. |
| M2 | H | `orchestrator_interact_action.py:3492-3498`, `loop.py:~690` | Model call exception → `None` → treated as unparseable output: misleading "(not valid JSON)" nudge, up to 3 wasted re-calls into a dead provider, then `clarify_text` ("could you rephrase?"). Finalize tick re-calls the same dead model. | **Fixed** — `_run_model` returns a typed `model_error` decision; loop ends the turn after 2 consecutive failures with `model_unavailable_text`, skips finalize, records `ended_via=model_error`. |
| M3 | M | `orchestrator_interact_action.py:3499` | `finish_reason == "length"` is never inspected; a truncated decision is indistinguishable from garbage, and the nudge ("keep it short") is generic. | **Fixed** — truncation surfaces as an explicit `model_truncated` note telling the model its output was cut off. |
| M4 | M | `anthropic.py:_build_payload` | `enforce_json_mode` is silently ignored on Anthropic (no `response_format` equivalent) — the JSON protocol relies on prompt obedience there. | Mitigated by M1 (native protocol needs no JSON mode). Documented. |
| M5 | M | `orchestrator_interact_action.py:3287-3309` | Loop history is fetched with `max_statement_length=None` — an unbounded prior reply is resent on every tick. | **Fixed** — bounded by `history_statement_max_chars` (default 4000). |
| M6 | M | `catalog.py:391-396` (`use_skill` description), `core_tools.py` (`update_plan`, `queue_task` descriptions) | Tool descriptions embed JSON call examples and prose argument lists because there was no schema channel. | **Fixed** — schemas added; descriptions trimmed to capability text. |
| M7 | L | `loop.py` repeat guard | Compares only the last signature — A/B/A/B oscillation is not caught (2026-09-01 LOW, still open). | Open; bounded by the budget. |

### 3.2 Failsafes

| # | Sev | Where | Finding | Status |
|---|---|---|---|---|
| F1 | H | `orchestrator_interact_action.py` attrs `tool_call_timeout=0.0`, `max_duration_seconds=0.0` | No default bound on a tool call or on the turn. A hung MCP/HTTP tool blocks the turn and holds the conversation mutation lock. | **Fixed** — `tool_call_timeout` default 120 s (0 still disables). `max_duration_seconds` left at 0 (documented as an operator choice; the tool bound plus tick budget now bound the turn). |
| F2 | M | `loop.py` (`nd_streak`) | Three unparseable decisions end the turn with `clarify_text` even when tools already ran — partial-compose only runs when `observations` is non-empty, fine; but the text blames the user. | **Fixed** via M2 (separate model-failure copy). |
| F3 | M | `interact/endpoints.py:848` | Any exception in the walker path returns HTTP 422 `ValidationError` with generic text — a server fault is reported as a client validation error. | Open — recommend a 5xx typed error; out of orchestrator scope. |
| F4 | L | `loop.py` locked dispatch | Locked IA dispatch honours `tool_call_timeout` but not the channel override (`_channel_cfg`). | **Fixed**. |
| F5 | L | `egress.py:_egress` | Fallback `clarify_text` is used for *every* silent ending; no distinction for `model_error`. | **Fixed** via M2. |

### 3.3 Thin-harness / domain coupling

| # | Sev | Where | Finding | Status |
|---|---|---|---|---|
| T1 | M | `continuation.py:383-470` | Imports `jvagent.action.interview.*`, looks up `InterviewAction` by type, reads interview session internals for the soft-abandon rule. | **Fixed** — soft-abandon delegates to duck-typed bound-action hooks (`task_lock_progress_count`, `task_lock_title`, `task_lock_abandon`); `InterviewAction` implements them. |
| T2 | M | `orchestrator_interact_action.py:213-235` | `_append_directive_hint` imports `interview.hooks.append_hint` for a generic U+2063 guidance marker. | **Fixed** — generic implementation in the orchestrator. |
| T3 | M | `skill_tasks.py:468` | Prerequisite push calls `bound._clear_interview_session` (private, interview-named). | **Fixed** — generic `clear_task_lock_session` hook (interview keeps a compat alias). |
| T4 | L | `skill_tasks.py:993`, `orchestrator_interact_action.py:2411` | Activation-catalog and completion detection key on `interview_type` / `interview_complete`. | **Fixed** — the orchestrator reads the generic `task_complete` / `task_lock_skill` keys plus whatever a plugin registers at load (`register_task_completion_flag`, `register_task_lock_skill_key`); `InterviewAction` registers its two keys. Interview payloads are unchanged. |
| T5 | L | `tests/action/orchestrator/test_no_interview_coupling.py` | Guard regex too narrow to catch T1–T4. | **Fixed** — pattern widened to `jvagent.action.interview`, `InterviewAction`, `interview_complete`, `interview_type`, `_clear_interview_session`. |

### 3.4 Streamlining

| # | Sev | Where | Finding | Status |
|---|---|---|---|---|
| S1 | M | `loop.py:_run_loop` | 47-name unpack of `TurnState` into locals; the tick body is ~700 lines in one `while`. Correct, but every new guard widens the same function. | Partially addressed — decision acquisition/fault classification (`_next_decision`) and the companion gate + soft-abandon rule (`_companion_gate`) extracted; `_run_loop` is ~745 lines under the 800 ratchet (`test_turn_boundary.py`). A full tick extraction is recommended as its own PR (see §5). |
| S2 | L | `orchestrator_interact_action.py` (3.5k lines) | Mixes config surface, surface assembly, skill-task orchestration, gearing and model call. Already split into mixins; `_assemble_tools` (500 lines) and the skill-task resume block are the remaining candidates. | Open. |
| S3 | L | `constants.py` `_TEXT_KEYS`/`_STEER_EXEMPT` aliases, `catalog.py` re-exports | Backward-compat aliases with no remaining callers outside tests. | Open (harmless). |

---

## 4. What changed in this pass

See `CHANGELOG.md` [Unreleased] and ADR-0044. Summary:

- **Native tool-calling protocol** (`tool_protocol: native`, default; `json` keeps the
  previous behaviour byte for byte). Tools carry JSON Schemas to the provider;
  the model's `tool_calls` are the decision; text is the reply; tool results
  replay as assistant `tool_calls` + `tool` messages with the provider's call
  ids. Legacy persisted prompt defaults are recognised and swapped for the
  protocol-correct built-ins. `tool_choice`/`parallel_tool_calls` pass through
  OpenAI-family actions and map onto Anthropic's `tool_choice`.
- **Model-failure semantics**: provider failure and output truncation are
  first-class decisions (`model_error`, `model_truncated`) with their own copy,
  retry rule and telemetry (`ended_via=model_error`, `model_unavailable_text`).
- **Bounds**: `tool_call_timeout` default 120 s; locked IA dispatch honours the
  channel override; `history_statement_max_chars` default 4000.
- **Interview decoupling** via bound-action hooks and load-time vocabulary
  registration; the coupling guard test widened.
- **Loop streamlining**: `_next_decision` and `_companion_gate` extracted from
  `_run_loop`.

Verification: `pytest tests/` (see CHANGELOG for counts) and
`pre-commit run --all-files` green on the branch. New tests:
`tests/action/orchestrator/test_native_tool_protocol.py`,
`tests/action/orchestrator/test_model_failure.py`,
`tests/action/interview/test_task_lock_hooks.py`,
`tests/action/model/test_tool_choice_passthrough.py`.

---

## 5. Recommended next steps (not done here)

1. **Tick extraction.** Turn the `while budget > 0` body into `_tick(state)`
   returning a `TickOutcome` (continue / return / break + `ended_via`), with
   the guard chain as a list of small predicates. Pure refactor; land with
   the existing 530 orchestrator tests as the net.
2. **Parallel tool calls.** The native protocol records grouped calls; the
   loop dispatches sequentially. Concurrent dispatch for non-terminal,
   non-side-effecting tools (`max_concurrent_tools`) is a contained follow-up.
3. **Typed HTTP faults** at `/interact` (F3).
4. **Repeat-guard window** of the last N signatures (M7).
5. **Live CUCS runs** against real providers for the native protocol (the
   `LiveScenarioRunner` supports this; the suite here is canned).

Model-integration remediation plan (contract + LiteLLM adapter + conformance + resilience policy): [`../specs/2026-09-05-model-integration-remediation.md`](../specs/2026-09-05-model-integration-remediation.md).
