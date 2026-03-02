# Response Gating

Response gating determines whether a user utterance warrants a reply from the agent. It makes conversational AI more realistic by suppressing backchannels and closings, deferring incomplete fragments until they form a coherent intent, and responding only when appropriate.

## Overview

The `ResponseGatingInteractAction` runs early in the interact pipeline (before `InteractRouter`) and classifies each utterance into one of three postures:

| Posture | Behavior | Examples |
|---------|----------|----------|
| **RESPOND** | Agent replies normally | Greetings, questions, requests, substantive content; affirmative answers ("ok" after "Would you like X?"); gratitude for preceding help (allow "you're welcome") |
| **SUPPRESS** | No reply; walk path cleared | Hanging "ok" with nothing to answer; redundant thanks after "you're welcome"; repeat goodbyes after exchange concluded |
| **DEFER** | No reply; utterance buffered for later | "Actually...", "wait no I", trailing ellipsis — accumulates until a complete thought |

## Architecture

```
User Utterance → InteractWalker → ResponseGatingInteractAction (weight=-200)
                                       │
                    ┌──────────────────┼──────────────────┐
                    │                  │                  │
                    ▼                  ▼                  ▼
               SUPPRESS            DEFER              RESPOND
            Clear path,        Append to buffer,   Consume buffer,
            no response        clear path          inject directive,
                                                      proceed to Router
```

## Components

### ResponseGatingInteractAction

- **File**: `response_gating.py`
- **Weight**: `-200` (runs before InteractRouter at `-100`)
- **Always executes**: Yes — evaluates every utterance regardless of routing

### GatingResult

- **File**: `gating_result.py`
- Structured output: `posture`, `confidence`, `reasoning`
- `parse_gating_response(raw)` — parses LLM JSON; falls back to RESPOND on parse failure

### Prompts

- **File**: `prompts.py`
- **Conversation progression tracing**: The classifier traces the flow from history to the current message (last assistant message type, last user message, how current message relates) before classifying
- Context-aware classification: conversation position (OPENING / MID-CONVERSATION / CLOSING) informs posture rules
- **Progressive fragment completeness**: When prior deferred fragments exist, the prompt evaluates the combined sequence (fragments + current) for intelligible intent

## Configuration

Add to your agent's `agent.yaml`:

```yaml
actions:
  - action: jvagent/response_gating
    context:
      enabled: true
      model: gpt-4o-mini          # Fast model recommended
      history_limit: 5             # Interactions for context
      enable_accumulation: true    # Enable DEFER / fragment buffering
      max_fragment_buffer: 5       # Cap on deferred fragments
      pass_through_task_types: [INTERVIEW]  # Bypass gating for these task types (default)
```

### Attributes

| Attribute | Default | Description |
|-----------|---------|-------------|
| `model` | `gpt-4o-mini` | Model for gating (fast, low-cost) |
| `model_temperature` | `0.1` | Low for consistent classification |
| `model_max_tokens` | `150` | Lightweight output |
| `history_limit` | `5` | Previous interactions for context |
| `enable_accumulation` | `true` | Enable DEFER and fragment buffer |
| `max_fragment_buffer` | `5` | Max deferred fragments; oldest dropped when exceeded |
| `pass_through_task_types` | `("INTERVIEW",)` | Task types that bypass gating (pass-through mode). When an active task has one of these types, gating is skipped and the pipeline proceeds. Set to `[]` to disable. |
| `pass_through_when_media` | `true` | When true, bypass gating and use RESPOND when the user has attached media (images, documents, etc.) in `visitor.data["image_urls"]` or `visitor.data["whatsapp_media"]`. |

## Pass-Through Mode

When an active task has a `task_type` in `pass_through_task_types`, gating is bypassed entirely: no LLM call, no posture classification. The pipeline proceeds as RESPOND. This saves tokens and ensures structured flows (e.g. interviews) always receive user input. Default: `("INTERVIEW",)`. Set `pass_through_task_types: []` to disable.

**Media pass-through**: When `pass_through_when_media` is true (default) and the user has attached media (images, documents, etc.) in `visitor.data["image_urls"]` or `visitor.data["whatsapp_media"]`, gating is bypassed and the pipeline proceeds as RESPOND. This prevents single-image messages (e.g. "I've attached media" placeholder) from being classified as SUPPRESS or DEFER.

## Deferred Fragment Buffer

- **Storage**: `Conversation.context["gating_deferred_fragments"]`
- **Scope**: Per conversation (session)
- **Structure**: `List[Dict]` — each entry: `{utterance, interaction_id, timestamp}`

**Lifecycle**:
- **DEFER**: Append current utterance to buffer; clear walk path
- **RESPOND** (with non-empty buffer): Consume buffer, inject consolidated directive via `visitor.add_directive()`, clear buffer, proceed

The directive instructs downstream actions (e.g. PersonaAction) to treat prior fragments as a unified request.

## Integration

- **Interaction**: `response_posture` field stores `RESPOND | SUPPRESS | DEFER` for history
- **Conversation**: `get_interaction_history(..., with_posture=True)` emits `[SUPPRESSED]` / `[DEFERRED]` system messages so the router understands why no assistant reply followed
- **InteractRouter**: Uses `with_posture=True` when fetching history for routing context

## Posture Rules (Summary)

**RESPOND** — Greetings/openers always; questions; requests; affirmative answers to assistant questions/offers ("ok", "yes", "sure" after "Would you like X?"); gratitude for directly preceding assistant help (permit "you're welcome"); short but contextually coherent messages; when in doubt.

**SUPPRESS** — Only when the message is a hanging/contextually devoid acknowledgment ("ok" with nothing to answer); redundant gratitude after thanks already acknowledged; or a social closing *and* history shows the exchange has concluded or the same closing was already exchanged.

**DEFER** — Only when the utterance is genuinely unintelligible/fragmentary *and* history does not provide enough context. Short messages that make sense in context are RESPOND.

## Exports

```python
from jvagent.action.gating import (
    ResponseGatingInteractAction,
    GatingResult,
    POSTURE_RESPOND,
    POSTURE_SUPPRESS,
    POSTURE_DEFER,
    parse_gating_response,
)
```
