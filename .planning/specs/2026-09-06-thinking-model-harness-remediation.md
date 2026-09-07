# Thinking models on the loop — remedial plan for issue #203

**Date:** 2026-09-06 · **Status:** Proposed · **Issue:** [TrueSelph/jvagent#203](https://github.com/TrueSelph/jvagent/issues/203) · **Builds on:** [ADR-0044](../adr/0044-native-tool-calling-protocol.md) (protocols), [ADR-0045](../adr/0045-capability-driven-model-integration.md) (capability registry, `tool_protocol: auto`), [ADR-0046](../adr/0046-model-resilience-policy.md) (fault decisions), [ADR-0049](../adr/0049-session-context-placement-and-cache-telemetry.md) (replay/caching)

## 0. One-paragraph answer

The report is correct on all three counts, and the three defects chain: a
capability guess puts a tool-capable reasoning model on the JSON-text protocol;
that protocol replays only `TOOL …(…) → …` lines between ticks, so a model
whose output is mostly reasoning re-derives its plan every tick and re-issues
the same first call; the repeat guard then does what it is for and ends the
turn empty. Forcing `native` on the same LiteLLM `ollama/` route does not help
because that route *emulates* tool calls through JSON in the content, which the
loop then delivers as prose. The remedy is: **stop trusting an inferred
`supports_tools=False`** (treat it as unknown, try native, demote on a real
provider refusal, and say so in the log and telemetry); **give the JSON
protocol continuity** (replay the model's own `thought` and a bounded excerpt
of its reasoning beside each tool result, and carry provider-native reasoning
back on the native path where the provider accepts it); and **never deliver
tool-call-shaped text as a reply** (salvage it into calls or fail the tick as a
model fault). Plus the one-line fix that avoids the whole `ollama/` emulation:
route Ollama through LiteLLM's `ollama_chat/` provider.

## 1. What was verified (2026-09-06, `dev` at 0.1.8rc6, litellm 1.100.0)

| Claim in #203 | Verified | Evidence |
|---|---|---|
| `resolve_capabilities("ollama/glm-5.3:cloud")` → `supports_tools=False`, `source="litellm"` | **Yes** | `litellm.get_model_info` returns `supports_function_calling: False`; the model is **not** in `litellm.model_cost`. LiteLLM's Ollama provider fills the field by asking the Ollama server `/api/show` and testing whether the word `tools` appears in the model *template* (`litellm/llms/ollama/completion/transformation.py::_supports_function_calling`). Cloud models expose no template → `False`. A wholly unknown Ollama model returns `None`, so today an *inferred* False and an *asserted* False are indistinguishable to us. |
| `auto` silently selects `json` on that | **Yes** | `_resolve_protocol` (`orchestrator_interact_action.py`): `json if caps.supports_tools is False else native`; nothing logs the downgrade; the activation event records `tool_protocol` but not why. |
| JSON protocol replays only tool I/O | **Yes** | `render_observations_section` (`tools.py`) emits `TOOL {tool}({args}) → {result}`. `thought` is a reserved decision key (`DECISION_RESERVED_KEYS`) read only by `_progress_line`; `reasoning_content` (`_thinking_from`, LiteLLM adapter) feeds the streamed REASONING disclosure only. Neither reaches `state.observations`. The JSON contract's own schema (`DECISION_SCHEMA`) has no `thought` property, so the model is not even asked for one. |
| Native replay also drops reasoning | **Yes** | `render_observation_messages` replays `assistant_text` (prose) + `tool_calls`; no `reasoning_content` / `thinking_blocks` on the assistant message. For providers that require signed thinking blocks on tool-use turns (Anthropic extended thinking) this is a correctness gap, not only a continuity one. |
| Repeat guard ends the turn on the second identical call | **Yes** | `_guard_tool_call`: nudge on the first repeat, `TickOutcome.stop("repeat_guard")` on the second (M7 window). Working as designed; the input to it is the defect. |
| Under `native`, glm-5.3's tool calls surface as reply text | **Consistent, not reproduced** | LiteLLM's `ollama/` provider is the `/api/generate` route: `transform_response` parses a `function_call` object out of the *content*; anything it fails to parse stays content and reaches `decisions_from_native_result` as text → `text_as_reply`. The `ollama_chat/` provider passes `tools` natively (Ollama ≥ 0.4). The literal `Tool Calls: [` prefix in the report is not a jvagent string — it is the model's/route's rendering. |
| Short tasks complete on the same model | **Yes** | `scripts/live_smoke.py --provider litellm --model ollama/glm-5.3:cloud` and `…ollama_chat/glm-5.3:cloud`: 3/3 each (greeting; datetime tool → reply; act-don't-announce), 1–2 ticks — on the JSON protocol that `auto` chose. The failure needs the multi-tick SOP the report measured. |

Not verified here: the 6-of-6 repeat-guard failures and the 6.5k-token
one-word reply. Both follow from the mechanism above; the acceptance test in §4
is how we make them reproducible.

## 2. Remedy

### R1 — Don't trust an inferred `supports_tools=False` (defect 1)

- `capabilities.litellm_capabilities`: when LiteLLM reports
  `supports_function_calling=False` **and the model id is not explicitly in
  `litellm.model_cost`**, record `supports_tools=None` (unknown). An explicit
  False in the table (a model LiteLLM's maintainers marked) still counts.
  `source` gains a suffix for the dropped field (`litellm(-tools)`), so the
  telemetry shows what was disbelieved.
- `_resolve_protocol`: unchanged rule (`json` only for a known False), so the
  above alone flips glm-5.3 to `native`. Add: `logger.warning` whenever `auto`
  resolves to `json`, naming the model, `caps.source`, and the two overrides
  (`tool_protocol: native`, `model_capabilities: {supports_tools: true}`).
- **Runtime demotion**: in `_run_model`'s native branch, a provider error that
  says tools are unsupported (a 400 whose body mentions `tools`/`tool_choice`/
  `function` and `support`) retries the same tick on the JSON contract and
  records `(action, model) → json` for the process (`_protocol_demotions`),
  logged at WARNING once. This is the "attempt native, fall back on error" the
  report asks for, and it makes `auto` safe to be optimistic.
- Telemetry: `orchestrator_activation.protocol_reason` —
  `configured:native` / `auto:native` / `auto:json:supports_tools=False(source)` /
  `demoted:json:<error class>`.

Files: `jvagent/action/model/capabilities.py`, `orchestrator_interact_action.py`
(`_resolve_protocol`, `_run_model`, `_record_orchestrator_activation`).
Tests: `tests/action/model/test_capabilities.py` (inferred vs explicit False),
`tests/action/orchestrator/test_capability_protocol.py` (auto → native for the
glm shape; warning on json; demotion on a tools-unsupported 400 and
persistence across ticks).

### R2 — Continuity on the JSON protocol (defect 2, cheapest first)

- **Ask for the thought.** Add `thought` to `DECISION_SCHEMA` and to the JSON
  protocol section: one or two sentences, *what you now know and what you will
  do next*. Optional (an omission never fails a decision).
- **Replay it.** In `_run_model`'s JSON branch set
  `parsed["_assistant_text"]` from `thought`, falling back to a bounded excerpt
  of `response.thinking_content` (new attribute
  `thought_replay_max_chars`, default 600, `0` disables). `_stamp_observations`
  already attaches `_assistant_text` to the step's observation; today only
  native decisions carry it.
- **Render it.** `render_observations_section` emits
  `THOUGHT: …` on the line before `TOOL …(…) → …` when the step has
  `assistant_text`, elided to `thought_replay_max_chars`. Rendered for the
  recent window only (same `full_recent` rule), so stale steps stay cheap.
- **Native path, provider-native reasoning.** `render_observation_messages`
  carries `reasoning_content` on the assistant message when the step recorded
  it, and `thinking_blocks` verbatim when the provider returned signed blocks
  (Anthropic). The LiteLLM adapter records both on the result (`_thinking_from`
  already extracts them; keep the raw blocks alongside). Providers that reject
  the field get it dropped by `drop_params` — verify in the conformance matrix.
- **Repeat nudge carries the result.** The first-repeat nudge says "its result
  is above"; include a short excerpt of that result inline (≤ 300 chars). A
  model that lost its thread gets the fact it needs in the same message, not a
  pointer.

Files: `constants.py` (`DECISION_SCHEMA`), `prompts.py` (JSON section),
`orchestrator_interact_action.py` (`_run_model` JSON branch, attribute),
`tools.py` (both renderers), `loop.py` (`_guard_tool_call` nudge),
`litellm_lm.py` (keep `thinking_blocks`). Tests: renderer tests for `THOUGHT:`
line and the cap; `_run_model` JSON branch stamps `_assistant_text` from
`thought`, then from `thinking_content`; native replay includes
`reasoning_content`/`thinking_blocks`; wire test that a JSON-protocol tick's
user prompt contains the previous tick's thought.

### R3 — Tool-call-shaped text is never a reply (defect 3)

- **Route fix.** `LiteLLMLanguageModelAction` and
  `OllamaLanguageModelAction.litellm_model_id()` map `ollama/` → `ollama_chat/`
  (LiteLLM's native-tools chat route; the `ollama/` generate route emulates
  tools through content). Opt-out attribute `litellm_ollama_route: chat|generate`
  for anyone who needs the generate API. Document in
  `docs/language-models.md`.
- **Salvage.** In `_run_model`'s native branch, when the response has no
  `tool_calls` and the text parses as a tool-call dump — a JSON array/object of
  `{name|function, arguments}` entries, optionally prefixed by a label such as
  `Tool Calls:` — convert it to decisions via `decisions_from_native_result`
  with synthetic call ids (`salvaged_` prefix) and log at INFO with the model
  id. Extends the ADR-0044 safety net added in #185 (JSON *decision* text).
- **Fail closed.** Text that *looks* like a tool call but does not parse
  (unbalanced JSON, `Tool Calls:` with garbage) returns
  `{"action": MODEL_ERROR_ACTION, "detail": "tool-call-shaped text"}` — the
  fault decision from ADR-0044 — never a reply. The activation event counts it
  under `model_failures`.

Files: `language/base.py` / `ollama.py` / `litellm_lm.py`, `orchestrator_interact_action.py`,
`tools.py` (a `parse_tool_call_dump` helper next to `parse_json_object`).
Tests: route mapping and opt-out; salvage of the exact shape from #203; fail
closed on malformed; the reply path never sees either.

### R4 — Make the failure reproducible (acceptance)

- A CUCS scenario `live.multistep_sop` in `jvagent/testing/live_smoke.py`: a
  three-step SOP (fetch schema → transform → write) over in-memory tools with
  a ~3k-char schema result, `must_reply: True`, `max_ticks: 6`, and a new
  assertion `no_repeat_guard: True`. Run in the nightly against
  `ollama_chat/glm-5.3:cloud` (needs `OLLAMA_API_KEY`) and `openai/gpt-4.1`.
- `stale_observation_max_chars` (default 2500) stays a knob; the scenario's
  schema is sized so the default truncates it, which is the report's setup.
  Document that raising it trades tokens for fidelity on large tool results.

## 3. Sequencing and size

| PR | Contents | Size | Risk |
|---|---|---|---|
| A | R1 (inferred-False → unknown, warning, runtime demotion, `protocol_reason`) + R3 route fix + R3 salvage/fail-closed | M | Optimistic `auto` may send tools to a model that truly lacks them: covered by demotion; providers that *accept* tools and ignore them fall into R3's fail-closed path rather than a silent reply |
| B | R2 (thought in schema/prompt, replay on both protocols, repeat nudge excerpt, `thinking_blocks` passthrough) | M | More prompt tokens per tick — bounded by `thought_replay_max_chars`; JSON-protocol tests that assert exact prompt text need the new line |
| C | R4 scenario + nightly wiring + docs (`language-models.md` Ollama section, `configuration-keys.md`, ADR-0051 recording R1/R2) | S | none |

A first, then B, then C. Each independently shippable; A alone should move
glm-5.3 from `json` to `native` on the `ollama_chat/` route and end the leak.
The reporter's question — one issue or three — one issue, three PRs, each
closing a numbered defect in its description.

## 4. Acceptance criteria

1. `resolve_capabilities("ollama/glm-5.3:cloud").supports_tools is None` and
   `_resolve_protocol` yields `native`; an explicit-False table model still
   yields `json`, with a WARNING naming the overrides.
2. A tools-unsupported 400 on the native path completes the tick on the JSON
   contract and the activation event says `demoted:json:…`; the next tick does
   not retry native.
3. On the JSON protocol, the tick-2 user prompt contains tick-1's `THOUGHT:`
   line; on the native protocol the assistant message carries
   `reasoning_content` when the model returned it.
4. The exact `Tool Calls: [ … ]` text from #203 is dispatched as a tool call
   (or, malformed, ends as `model_error`); it never appears in `response`.
5. `live.multistep_sop` passes on `ollama_chat/glm-5.3:cloud` with
   `guards` free of `repeat` in the activation event, and the one-word-reply
   scenario stays under 2k prompt tokens across the turn.

## 5. Open questions for the reporter

- Was the Integral deployment on `ollama/` or `ollama_chat/`? (The route fix
  in R3 is only a change if the former.)
- One transcript of the `Tool Calls: [` leak (the `model_call` event's
  `response` field) would let R3's salvage parser target the real shape rather
  than the excerpt.
- Whether `planning_heavy_first_tick: true` was set because the light model
  failed on `update_plan`, or as a precaution — relevant to ADR-0050's
  default.
