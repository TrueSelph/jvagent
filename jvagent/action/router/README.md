# InteractRouter Action

InteractRouter is a specialized InteractAction that performs **unified posture classification and intent-based routing** in a single LLM call. It analyzes conversational state, classifies response posture (RESPOND/SUPPRESS/DEFER), and routes to appropriate InteractActions based on user needs.

## Core Principle

**Understand what the user actually needs, then route to the action(s) that can fulfill that need.**

The router does NOT mechanically classify messages or automatically route to ongoing activities. Instead, it:

1. Understands what the user is expressing
2. Determines what they actually need from the system
3. Matches to actions that can fulfill that need

## Unified Flow (Posture + Routing)

InteractRouter combines posture classification and routing in one LLM call:

- **RESPOND**: Proceed with routing; consume deferred fragments if any; publish canned response; finalize walk path
- **SUPPRESS**: Clear walk path; no response (e.g., closing exchanges, backchannels)
- **DEFER**: Append utterance to buffer; clear walk path; no response (fragmentary input; wait for completing message)

Fragment accumulation is enabled by default. When DEFER is returned for utterances like "Actually..." or "wait no", the system buffers them. On the next RESPOND, prior fragments are injected as a directive so the agent receives the full context.

## How It Works

### Analysis Process

1. **Understand the User's Message**
   - Are they making a request?
   - Are they asking a question?
   - Are they responding to something the assistant asked?
   - Are they expressing something social (gratitude, greeting)?
   - Are they signaling a topic change?

2. **Understand the Conversational State**
   - What was the last thing the assistant asked?
   - Is there an ongoing activity?
   - Is the user directly engaging with that activity?

3. **Determine User Needs**
   - Does the user need the system to do something specific?
   - Or is this social/acknowledgment that may not need routing?

4. **Match to Action Anchors**
   - Based on their actual need, which actions can help?

### Ongoing Activities

An ongoing activity (detected via `[EVENT] Ongoing Activity:` messages) does **NOT** automatically capture all messages.

Route to an ongoing activity only when the user is **directly engaging** with it:
- Answering a question that activity asked
- Providing information that activity requested

Do **NOT** route to ongoing activity for:
- Social expressions ("thanks", "cool", "ok")
- New requests unrelated to the activity
- Questions about different topics
- Greetings or smalltalk

### Intent Types

The router tracks what the user is expressing:

| Intent Type | Description |
|-------------|-------------|
| **CONVERSATIONAL** | Greeting, gratitude, acknowledgment, smalltalk |
| **INFORMATIONAL** | User is asking a question or seeking information |
| **INTERACTIVE** | User is directly answering assistant's question |
| **DIRECTIVE** | User wants the system to do something specific |
| **UNCLEAR** | Cannot determine what user needs |

### Routing Guidelines

| User Expression | Routing |
|-----------------|---------|
| New request or question | Match to relevant action anchors |
| Direct answer to ongoing activity's question | Route to ongoing activity |
| Providing info that ongoing activity requested | Route to ongoing activity |
| Gratitude/acknowledgment ("thanks", "cool") | `[]` unless they also make a request |
| Greeting or smalltalk | General conversation handler or `[]` |
| Topic change / cancellation | Match to anchors or `[]` |

## Configuration

```yaml
actions:
  - action: jvagent/interact_router
    context:
      enabled: true
      model: "gpt-4o-mini"
      model_temperature: 0.1
      history_limit: 3
      exceptions:
        - "SomeAlwaysRunAction"
      # Routing cache - skip LLM for repeated context (requires enable_interact_router_cache in app.yaml)
      enable_routing_cache: true
```

### Properties

- `model`: Model identifier (default: "gpt-4o-mini")
- `model_temperature`: Temperature for LLM (default: 0.1)
- `model_max_tokens`: Max tokens (default: 400)
- `history_limit`: Previous interactions to include (default: 3)
- `confidence_threshold`: Minimum confidence to proceed without clarification (default: 0.7)
- `enable_clarification`: Request clarification when confidence is below threshold (default: false; Converse handles unclear cases when disabled)
- `weight`: Execution weight (default: -200; runs first to subsume posture classification)
- `exceptions`: Action names that always execute
- `enable_routing_cache`: Skip LLM for repeated context when cache hit (default: false; requires `enable_interact_router_cache` in app.yaml)
- `pass_through_task_types`: Task types that bypass LLM when active (default: `("INTERVIEW",)`)
- `pass_through_when_media`: Bypass LLM when user has attached media (default: true)
- `media_bypass_actions`: When non-empty and media attached, route to these actions without LLM (default: [])
- `bypass_canned_response`: Instant canned response for bypass paths (default: "One moment")
- `enable_accumulation`: Enable DEFER posture and fragment accumulation (default: true)
- `max_fragment_buffer`: Max deferred fragments to retain (default: 5)

### App-Level Configuration (app.yaml)

The routing cache is gated by global config:

```yaml
config:
  performance:
    enable_interact_router_cache: false   # default
    interact_router_cache_ttl: 45
```

Environment variables: `JVAGENT_ENABLE_INTERACT_ROUTER_CACHE`, `JVAGENT_INTERACT_ROUTER_CACHE_TTL`

## Usage

### Publishing Anchors

```python
from jvagent.action.interact.base import InteractAction
from typing import List

class ReportAction(InteractAction):
    anchors: List[str] = [
        "User requests a report",
        "User asks about report status",
        "User wants to check report progress"
    ]

    async def execute(self, visitor):
        if "ReportAction" not in visitor.interaction.anchors:
            return

        # Use intent_type for context
        intent = visitor.interaction.intent_type
        interpretation = visitor.interaction.interpretation
```

### Routing Exceptions

Actions that should always execute:

1. **Static exceptions** in configuration:
   ```yaml
   exceptions:
     - "SomeInteractAction"
   ```

2. **Dynamic exceptions** via `always_execute` flag:
   ```python
   class MyAction(InteractAction):
       always_execute: bool = True
   ```

## Examples

### "Cool thanks" During News Activity

```
Conversation:
  [EVENT] Ongoing Activity: NewsInteractAction
  Assistant: "Here are today's top headlines..."
  User: "Cool thanks"

Analysis:
  - User is expressing gratitude (CONVERSATIONAL)
  - NOT directly engaging with NewsInteractAction
  - No specific need from the system

Result:
  posture: RESPOND
  intent_type: CONVERSATIONAL
  actions: []
```

### New Request During Signup

```
Conversation:
  [EVENT] Ongoing Activity: SignupInterviewSkill
  Assistant: "What is your email?"
  User: "What's the weather like?"

Analysis:
  - User is asking a question (INFORMATIONAL)
  - NOT answering the signup question
  - Needs weather information

Result:
  posture: RESPOND
  intent_type: INFORMATIONAL
  actions: ["WeatherAction"]
```

### Direct Response to Activity

```
Conversation:
  [EVENT] Ongoing Activity: SignupInterviewSkill
  Assistant: "What is your name?"
  User: "John Doe"

Analysis:
  - User is providing information (INTERACTIVE)
  - Directly answering SignupInterviewSkill's question

Result:
  posture: RESPOND
  intent_type: INTERACTIVE
  actions: ["SignupInterviewSkill"]
```
