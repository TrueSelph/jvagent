# ADR 0051 — Try native first: capability trust, runtime demotion, and text-shaped tool calls

**Status**: Accepted
**Date**: 2026-09-06
**Relation**: Amends the `tool_protocol: auto` rule of [ADR-0045](0045-capability-driven-model-integration.md) and extends the native-protocol safety net of [ADR-0044](0044-native-tool-calling-protocol.md). Resolves defects 1 and 3 of [issue #203](https://github.com/TrueSelph/jvagent/issues/203); defect 2 (reasoning continuity between ticks) is the next ADR. Plan: [`.planning/specs/2026-09-06-thinking-model-harness-remediation.md`](../specs/2026-09-06-thinking-model-harness-remediation.md).

---

## 1. Context

ADR-0045 made `auto` pick the JSON-text contract whenever the capability
registry said `supports_tools is False`. The registry's LiteLLM layer maps
`supports_function_calling` straight through. For Ollama, LiteLLM does not
*know* that flag — its provider code asks the Ollama server `/api/show` and
answers `False` unless the word `tools` appears in the model template. Ollama
Cloud models expose no template. `ollama/glm-5.3:cloud`, which tool-calls
perfectly well, was therefore classified `False`, `auto` chose `json`, nothing
said so, and a reasoning model spent every tick re-deriving its plan from
`TOOL … → …` lines until the repeat guard ended the turn (#203: 6 of 6 turns
failed; the same input on `openai/gpt-4.1` finished in 11 s).

Forcing `native` did not help on that route either: LiteLLM's `ollama/`
provider is the `/api/generate` route and emulates tool calling by parsing a
JSON object out of the content. A call the model phrased differently arrived
as text, and `decisions_from_native_result` delivered it to the user as the
reply — an internal struct on the screen.

The asymmetry that decides this ADR: a wrong `False` degrades the whole loop
silently; a wrong `True` produces a 400 we can catch on the first call.

## 2. Decision

1. **An inferred `False` is unknown.** `litellm_capabilities` drops
   `supports_tools=False` unless the model (or its bare id) is a row in
   `litellm.model_cost` — i.e. a maintainer wrote the flag. The dropped field
   is visible in `source` (`litellm(-tools)`). Bundled and operator overrides
   are unaffected: our own table (`gemma2` → no tools) and
   `model_capabilities: {supports_tools: false}` still force `json`.
2. **`auto` explains itself.** `_resolve_protocol` records
   `protocol_reason` on the turn (`configured:<p>`, `auto:native`,
   `auto:json:supports_tools=False(<source>)`, `demoted:json:<error>`) and
   the `orchestrator_activation` event carries it. Every `auto → json` is
   logged at WARNING with the two overrides (`tool_protocol: native`,
   `model_capabilities`).
3. **Runtime demotion.** When a native call fails and the provider's error
   says the model/route does not take `tools` (`_looks_like_tools_unsupported`),
   the (action class, model id) pair is demoted to `json` for the rest of the
   process (`_protocol_demotions`), logged once at WARNING, and the same tick
   is redone on the JSON contract — the model never saw the failed request.
   Other provider errors keep the ADR-0046 `model_error` path. Restart clears
   demotions; `tool_protocol: json` pins a model that is known to need it.
4. **Ollama goes through `ollama_chat/`.** `OllamaLanguageModelAction`'s
   LiteLLM prefix and `LiteLLMLanguageModelAction._route_model_id` send
   `ollama/<m>` as `ollama_chat/<m>` — the `/api/chat` route with native tools
   (Ollama ≥ 0.4). `litellm_ollama_route: generate` opts out.
5. **Text-shaped tool calls are salvaged, never replied.** Under native, when
   a response carries no `tool_calls` but its content is entirely a tool-call
   structure (`Tool Calls: [...]`, or a JSON array/object of
   `{name|function, arguments}`), `salvage_tool_call_text` converts it to
   OpenAI-shaped calls (ids prefixed `salvaged_` so telemetry shows they were
   not provider-native) and the loop dispatches them. Prose and JSON
   *decisions* (the ADR-0044 safety net from #185) are untouched. Content
   that looks like a call but does not parse is not salvaged — it falls to
   the existing no-decision nudge rather than to the user.

## 3. Consequences

- A tool-capable model that LiteLLM misdescribes now runs native on the
  first tick; a model that truly lacks tools costs one failed call per
  process before settling on `json`. Both are visible in the log and in
  `protocol_reason`.
- Providers that *accept* `tools` and ignore them (answering in prose) are
  not helped by demotion — that is what the salvage and the existing guards
  are for, and the next ADR's reasoning replay is what keeps such a model on
  track over a long turn.
- Operators who configured `ollama/...` ids see their calls go to
  `ollama_chat/`. Capabilities are still resolved for the configured id; the
  `ollama_chat/` lookup often has no LiteLLM row, which is the "unknown →
  native" case this ADR wants.
- The error-text heuristic is deliberately narrow (`tool`/`function` near
  `not support`/`unsupported`/`unknown parameter`/`invalid`). A provider
  wording it differently gets the plain `model_error` path — no worse than
  before.

## 4. Verification

`tests/action/model/test_capabilities.py` (inferred vs explicit False, bare
id, inferred True), `tests/action/orchestrator/test_protocol_trust.py`
(resolution and reasons; warning on `json`; provider refusal demotes and
redoes the tick, next tick skips the probe; other errors stay `model_error`;
salvage of the exact text from #203, bare arrays, prose/decisions untouched;
text-shaped call dispatched not replied), `tests/action/model/test_litellm_action.py`
and `test_transport.py` (route mapping and opt-out). Live:
`scripts/live_smoke.py --provider litellm --model ollama/glm-5.3:cloud` runs
native on the chat route.
