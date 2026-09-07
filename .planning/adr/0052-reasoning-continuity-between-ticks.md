# ADR 0052 — Reasoning continuity between ticks

**Status**: Accepted
**Date**: 2026-09-06
**Relation**: Resolves defect 2 of [issue #203](https://github.com/TrueSelph/jvagent/issues/203); companion to [ADR-0051](0051-protocol-trust-and-runtime-demotion.md). Extends the observation replay of [ADR-0044](0044-native-tool-calling-protocol.md). Plan: [`.planning/specs/2026-09-06-thinking-model-harness-remediation.md`](../specs/2026-09-06-thinking-model-harness-remediation.md) R2.

---

## 1. Context

Between ticks the loop replays what happened: on the JSON contract as
`TOOL name(args) → result` lines, on the native protocol as assistant
`tool_calls` + `tool` messages. Neither carried anything the model *thought*.
The JSON contract's optional `thought` was read only to build the UI progress
line; a provider's `reasoning_content` fed only the streamed reasoning trace.
For a completion model that is survivable — its visible output is most of its
decision. For a reasoning model it discards the majority of the work of each
tick: the model reasons at length, emits one call, and next tick sees only tool
I/O, so it re-derives from scratch and frequently re-issues the same first
call. The repeat guard then ends the turn (#203: 6 of 6 turns; a one-word
answer cost 6.5k tokens of reasoning generated, streamed and thrown away).

Two ways to restore continuity were on the table: carry provider-native
reasoning back (Anthropic signed `thinking_blocks`, DeepSeek/GLM
`reasoning_content`, OpenAI's opaque reasoning items) or replay the model's
own words as ordinary assistant content. The first is more faithful and
provider-specific: each provider has its own field, its own rules about when
it may be echoed (DeepSeek forbids passing `reasoning_content` back; Anthropic
requires unmodified signed blocks on tool-use turns; Ollama's chat API takes a
`thinking` field on some models), and a mistake is a 400 mid-turn. The second
is provider-agnostic, bounded, and testable offline.

## 2. Decision

**One mechanism, both protocols: the step's recorded reasoning rides as
`assistant_text`.**

1. **Ask for it.** `DECISION_SCHEMA` gains an optional `thought` and the JSON
   protocol section tells the model what it is for ("replayed to you next to
   the step's result, so you never have to re-derive a plan you already
   made").
2. **Record it.** `_run_model` sets `_assistant_text` on the decision: the
   JSON contract's `thought` (or `reasoning`), else a bounded excerpt of
   `result.thinking_content`; on the native path, the excerpt is used only
   when the model wrote no prose with its call. `_stamp_observations` already
   attaches `_assistant_text` to the step (ADR-0044).
3. **Replay it.** `render_observations_section` emits `THOUGHT: …` before the
   step's `TOOL …` line; `render_observation_messages` carries it as the
   assistant message's `content` beside the `tool_calls`. Both elide at
   `thought_replay_max_chars` (new attribute, default 600; `0` disables).
4. **The repeat nudge carries the result.** The first-repeat guard quotes up
   to 300 characters of the earlier result inline ("it returned: …") instead of
   "its result is above" — a model that lost its thread gets the fact in the
   same message.

Provider-native reasoning replay is **not** done here. Anthropic's signed
thinking blocks on tool-use turns remain a separate correctness item for the
first-party Anthropic adapter, tracked in the plan.

## 3. Consequences

- Each replayed step costs up to `thought_replay_max_chars` more characters;
  with ADR-0049's stable-prefix layout the older steps are cache hits, so the
  marginal cost is the newest step's thought. Operators can lower or zero the
  cap per agent.
- The excerpt is the model's reasoning shown to the model as its own prior
  content — a summary it wrote, not a signed block. It is labelled as such by
  position (assistant role) and never shown to the user.
- On the native path a model that writes prose beside its calls sees no
  change; the excerpt fills the gap only when content is empty.
- A model that ignores `thought` on the JSON contract loses nothing versus
  before; the reasoning excerpt fallback still applies when the provider
  exposes reasoning.

## 4. Verification

`tests/action/orchestrator/test_thought_replay.py`: the contract asks for a
thought; the JSON renderer replays it before the result, capped, off at 0;
the native renderer carries it as assistant content, capped; `_run_model`
stamps `_assistant_text` from `thought`, from provider reasoning on both
protocols, and not at all when the cap is 0; an end-to-end turn shows tick 2's
observations carrying tick 1's thought and the repeat nudge quoting the
earlier result.
