# Response Gating

Response gating determines whether a user utterance warrants a reply from the agent. It makes conversational AI more realistic by suppressing backchannels and closings, deferring incomplete fragments until they form a coherent intent, and responding only when appropriate.

## Overview

The `ResponseGatingAction` runs early in the interact pipeline (before `InteractRouter`) and classifies each utterance into one of three postures:

| Posture | Behavior | Examples |
|---------|----------|----------|
| **RESPOND** | Agent replies normally | Greetings, questions, requests, substantive content |
| **SUPPRESS** | No reply; walk path cleared | Repeat goodbyes after exchange concluded, "thanks again" after thanks already acknowledged |
| **DEFER** | No reply; utterance buffered for later | "Actually...", "wait no I", trailing ellipsis — accumulates until a complete thought |

## Architecture

```
User Utterance → InteractWalker → ResponseGatingAction (weight=-200)
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

### ResponseGatingAction

- **File**: `response_gating_action.py`
- **Weight**: `-200` (runs before InteractRouter at `-100`)
- **Always executes**: Yes — evaluates every utterance regardless of routing

### GatingResult

- **File**: `gating_result.py`
- Structured output: `posture`, `confidence`, `reasoning`
- `parse_gating_response(raw)` — parses LLM JSON; falls back to RESPOND on parse failure

### Prompts

- **File**: `prompts.py`
- Context-aware classification: conversation position (OPENING / MID-CONVERSATION / CLOSING) informs posture rules
- **Progressive fragment completeness**: When prior deferred fragments exist, the prompt evaluates the combined sequence (fragments + current) for intelligible intent

## Configuration

Add to your agent's `agent.yaml`:

```yaml
actions:
  - action: jvagent/response_gating_action
    context:
      enabled: true
      model: gpt-4o-mini          # Fast model recommended
      history_limit: 5             # Interactions for context
      enable_accumulation: true    # Enable DEFER / fragment buffering
      max_fragment_buffer: 5       # Cap on deferred fragments
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

**RESPOND** — Greetings/openers always; questions; requests; short but contextually coherent messages ("yes" after a question, "pricing" after discussing products); when in doubt.

**SUPPRESS** — Only when the message is a social closing *and* history shows the exchange has concluded or the same closing was already exchanged.

**DEFER** — Only when the utterance is genuinely unintelligible/fragmentary *and* history does not provide enough context. Short messages that make sense in context are RESPOND.

## Exports

```python
from jvagent.action.gating import (
    ResponseGatingAction,
    GatingResult,
    POSTURE_RESPOND,
    POSTURE_SUPPRESS,
    POSTURE_DEFER,
    parse_gating_response,
)
```
