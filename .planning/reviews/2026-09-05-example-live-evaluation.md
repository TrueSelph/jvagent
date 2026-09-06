# Example app live evaluation — 2026-09-05

**Subject:** `examples/jvagent_app`, agent `jvagent/orchestrator_agent`, run
from the `dev` line after the model-integration remediation (ADR-0044 native
tool protocol, ADR-0045 capability registry, ADR-0046 resilience, ADR-0047
transport delegation) and the tick extraction (audit follow-up S1).

**Method.** Server started with `jvagent . --debug` from the example root,
JSON store (`examples/jvagent_app/jvdb`), admin login via `POST
/api/auth/login`, turns via `POST /api/agents/{id}/interact`. Each turn was
run once on the httpx transport and once with `JVAGENT_MODEL_TRANSPORT=litellm`
(same gpt-4.1 model, same key). Numbers below are from the persisted
`Interaction.usage` and the `orchestrator_activation` telemetry event on
`Interaction.observability_metrics`; they are single samples, not benchmarks.

## 1. What worked

| Turn | Transport | Wall | Ticks | Model calls | Prompt tokens | Cost (USD) | Tools |
|---|---|---|---|---|---|---|---|
| "Hello! Quick check-in." | httpx | 3.7s | 1 | 2 | ~4.3k | 0.0075 | `respond` |
| "Hello! Quick check-in." | litellm | 5.6s | 1 | 2 | 4,277 | 0.0075 | `respond` |
| "What time is it right now?" | httpx | 3.1s | 3 | 4 | ~12.8k | 0.025 | `(guard)`, `get_current_datetime`, `reply` |
| "What time is it right now?" | litellm | 2.7s | 3 | 3 | 12,117 | 0.0249 | `(guard)`, `get_current_datetime`, `reply` |
| web search question | httpx | 4–6s | 3–4 | 4–5 | — | 0.031–0.062 | `web_search`, `reply` |
| `café → naïve — “quotes” ✓` (see §2.2) | litellm | — | 1 | 2 | 3,979 | 0.0069 | `respond` |

- **Grounded answers.** The time question calls `get_current_datetime` and
  replies with the tool's value and zone; the model does not guess.
- **Memory carries across turns** within a session (`session_id` round-trip);
  the second turn's prompt includes the first.
- **Both transports behave identically at the loop level** — same tick
  shape, same tools, same protocol (`tool_protocol: native` resolved from
  `auto`); LiteLLM's own `completion` log lines confirm the delegation path.
- **Telemetry is sufficient to explain a turn after the fact**: protocol,
  tick count, per-tool durations, gearing (`ticks_light` / `ticks_heavy`,
  `escalated`), `ended_via`, `turn_cost_usd`.

## 2. Findings

### 2.1 JSON decision text leaked to the user (fixed — PR #185)

On the first greeting under the native protocol gpt-4.1 answered with
`{"action":"reply","text":"..."}` as plain text and that string reached the
user. Root cause: the persisted `system_prompt` was the JSON-era built-in, but
the store had folded two `→` to `?` and an em-dash to `-`, so the exact
comparison in `_compose_system_prompt` treated it as an operator override and
kept the JSON-text contract alive alongside native tools.

Fix: legacy detection compares both sides through the store's own fold
(`normalize_text_to_ascii`); overrides that still mention the JSON contract are
kept but warned about; and under native, model text that parses as a decision
object is treated as a decision, never delivered as prose. Re-run on the fixed
tree: greeting is prose, 1 tick.

### 2.2 jvspatial folds every persisted string to ASCII by default (open)

`JVSPATIAL_TEXT_NORMALIZATION_ENABLED` defaults to **true** in jvspatial
0.0.17 (`jvspatial/utils/normalization.py`, applied in `core/context.py` on
every save). Observed on a real utterance:

| Sent | Stored |
|---|---|
| `Quick test: café → naïve — “quotes” ✓.` | `Quick test: cafe ? naive - "quotes" ?.` |
| `日本語のテキスト` | `????????` |
| `Привет мир` | `?????? ???` |
| `Hola señor, ¿qué tal? 🙂` | `Hola senor, ?que tal? ?` |
| `Straße` | `Stra?e` |

This is silent data loss on user utterances, agent responses, interview
answers and any skill output that reaches the graph. A conversation in any
non-Latin script is unrecoverable from memory, and the model sees the mangled
history on the next turn. The example `.env` does not set the key and jvagent
never documents it.

**Recommendation.** jvagent should default the key to `false` at boot
(`os.environ.setdefault`, so an operator's explicit setting still wins), the
way `JVSPATIAL_ENABLE_DEFERRED_SAVES` is seeded in `jvagent/cli/server.py`,
and list the key in `docs/environment-keys-reference.md`. Raise upstream
whether the default should flip in jvspatial itself. Tracked as a follow-up
to this evaluation.

### 2.3 Zero prompt-cache hits (resolved — ADR-0049)

> **Resolved 2026-09-06.** Measured once `cached_tokens` reached telemetry: identical first turns in two sessions cached **0** tokens (prompts diverged at character ~220, the clock); within a turn the provider did cache but the count was dropped in the harness. After ADR-0049 (block rendered last, breakdowns carried through, persisted old template recognised as the default): new-session greeting **1,920 / 3,584** cached, turn cost $0.0073 → $0.0044; second tick of a turn **4,352 / 4,505**. Details in [ADR-0049](../adr/0049-session-context-placement-and-cache-telemetry.md).

OpenAI's usage never reported cached prompt tokens across any turn. The
system prompt is several thousand tokens and stable, so the prefix should hit
the cache after the first call. Likely cause: the SESSION CONTEXT block
(ADR-0042, clock + channel) sits early in the system prompt and changes every
turn, so the cacheable prefix is shorter than OpenAI's 1024-token minimum.

**Recommendation.** Record `prompt_tokens_details.cached_tokens` in
`Interaction.usage` (the field is currently dropped, so this cannot be measured
from telemetry), then move the volatile session block to the end of the system
prompt or into the user turn. Amend ADR-0042 if the position moves. Expected
effect on the time question: ~12k prompt tokens per turn of which most would be
cache reads at a quarter of the price.

### 2.4 `planning: true` pins the heavy gear; the example's light model is dead config

Telemetry on every turn: `ticks_light: 0, ticks_heavy: N, escalated: true`.
Gear selection (`orchestrator_interact_action.py:359`) sends tick 0 to the heavy
model whenever planning is on, because the model must reason about
`update_plan`. The example turns planning on "to exercise the feature" and also
configures `light_model: gpt-4.1-mini`, which therefore never runs. The example
now carries a comment saying so; the design question (should planning force
heavy on *every* tick, or only when a plan exists?) is left open.

### 2.5 A guard tick precedes the datetime call

"What time is it right now?" takes three ticks: a `(guard)` observation, then
the tool call, then the reply. The guard fires on the model's first decision
and costs one extra model call (~4k prompt tokens, about a third of the turn's
cost). Which guard fired is not recorded in the activation event — the same
`(guard)` marker is emitted by four sites (`loop.py`: the parameter-enforcement
detectors, two chain-deflection checks and the repeat guard) — so the trace has
to be read from debug logs. Recording the guard name alongside the marker would
make this diagnosable from telemetry alone.

### 2.6 Smaller items

- **README paths lacked the `/api` prefix** (`/auth/login`,
  `/agents/{id}/interact`). Fixed in this PR; the first login attempt 404'd
  because of it.
- **`curate_walk_path: 2 caller-supplied action(s) were not in the queue`**
  is logged at debug on every turn. Harmless, but noise in a debug session.
- **The memory content endpoint omits telemetry.** `observability_metrics`
  and `usage` are only reachable by reading the node; a `?include=telemetry`
  flag on the interaction read would help operators.
- **The live store directory is `jvdb`**, not `jvagent_db` as some older notes
  say.

## 3. LiteLLM adapter end-to-end (2026-09-06)

The example was then switched to run its orchestrator **through the LiteLLM
adapter** (`jvagent/litellm_lm`; heavy `openai/gpt-4.1`, light
`openai/gpt-4.1-mini`, `LiteLLMLanguageModelAction` in both slots) and
re-tested — first with the smoke harness, then the live server.

**Smoke** (`scripts/live_smoke.py --provider litellm --model openai/gpt-4.1`):
3/3 — greeting (1 tick), datetime tool then reply (2 ticks), act-don't-announce
(3 ticks, one guard). Cents.

**Live server**, same four turns as §1, all `provider: litellm` in the
`model_call` telemetry, 16 LiteLLM completions in the log, `tool_protocol:
native` resolved from `auto` via LiteLLM's model metadata:

| Turn | Wall | Ticks | Model calls | Prompt tokens | Cost (USD) | Per-call latency |
|---|---|---|---|---|---|---|
| "Hello! Quick check-in." | 4.6s | 1 | 2 | 4,219 | 0.0073 | 1.4s (+0.7s reply compose on `openai_lm`) |
| "What time is it right now?" | 3.3s | 4 | 4 | 16,092 | 0.0328 | 0.5–0.7s |
| web search (2026 Tour de France) | 3.8s | 2 | 2 | 8,786 | 0.0180 | 0.7s |
| "Summarise … in one line." | 1.6s | 1 | 1 | 4,136 | 0.0085 | 0.9s |

Same answers, same tick shapes, same guards and costs as the first-party wire
(§1) — the harness behaves identically on the adapter, which is the point of
the contract. Memory carried across the four turns and survived the config
sync. Pricing came from LiteLLM's metadata (the model id in telemetry is the
resolved `gpt-4.1-2025-04-14`).

### 3.1 Merge-mode sync silently kept the old model class (trap, documented)

The first attempt ran `jvagent . --update` (merge) after editing the YAML. It
registered the new `jvagent/litellm_lm` action but **kept** the orchestrator's
persisted `model_action_type: OpenAILanguageModelAction`, so four "LiteLLM"
turns ran on the first-party wire (zero LiteLLM log lines, `provider: openai`)
and looked fine. Merge adds actions and keys; it does not overwrite existing
values. `--update --source --yes` replaced the slots and left conversation
memory intact (interaction count identical before and after); without `--yes`
it prompts and, under `nohup`, aborts. Now spelled out in
`docs/language-models.md` "Migrating a deployed agent to LiteLLM" — any
deployed-agent migration that skips this step is a no-op.

### 3.2 The grounding guard fights the session clock (open — harness fix)

The two `(guard)` ticks on "What time is it right now?" are now attributable
from the persisted `model_call` payloads: on tick 1 the model replied *"It is
currently Saturday, September 5, 2026, 11:59 PM (Eastern Time)"* — correct,
read from the SESSION CONTEXT block that ADR-0042 declares authoritative — and
the grounding guard deflected it because no substantive tool had run. Tick 2:
same answer, same deflection (`grounding_max_deflections` = 2). Tick 3 finally
called `get_current_datetime`, tick 4 replied with the same time. Two model
calls, ~$0.016, about half the turn's cost, spent contradicting ground truth
the harness itself supplied. Fix: include the SESSION CONTEXT facts in the
grounding corpus (`_grounding_corpus`) so a reply grounded in them passes, or
exempt replies whose claims appear there. Small, self-contained; not part of
the examples change.

### 3.3 Persisted prompts carry the store's fold

The live system prompt read *"research ? write a file ? save it"* — the
built-in template's arrows, folded to `?` by the store and rendered from the
persisted attribute (§2.2). Harmless to the model here, but it is the same
mechanism that broke legacy-prompt detection (#185); #187 stops it for future
syncs (re-sync in source mode after upgrading).

## 4. Follow-ups raised

| # | Item | Where |
|---|---|---|
| 1 | Default `JVSPATIAL_TEXT_NORMALIZATION_ENABLED=false` at boot + document | `jvagent/cli/server.py`, `docs/environment-keys-reference.md` |
| 2 | Persist `cached_tokens`; move SESSION CONTEXT below the stable prefix | `jvagent/action/model/language/openai`, `prompts.py`, ADR-0042 amendment |
| 3 | Record the guard name in `orchestrator_activation` | `jvagent/action/orchestrator/loop.py` |
| 4 | Decide whether planning should force heavy on every tick | ADR-0016 / ADR-0019 |
| 5 | Silence or fix the `curate_walk_path` debug line | `jvagent/action/interact/interact_walker.py` |
| 6 | Grounding guard: treat SESSION CONTEXT facts as grounded (§3.2) | `jvagent/action/orchestrator/loop.py::_grounding_corpus` |
| 7 | Merge-mode sync keeps existing values — consider warning when a YAML value differs from the persisted one | `jvagent/cli/bootstrap.py` |
