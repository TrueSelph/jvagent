# InteractRouter Action

InteractRouter is a specialized InteractAction that intelligently analyzes conversational state and routes to appropriate InteractActions based on user needs.

## Core Principle

**Understand what the user actually needs, then route to the action(s) that can fulfill that need.**

The router does NOT mechanically classify messages or automatically route to ongoing activities. Instead, it:

1. Understands what the user is expressing
2. Determines what they actually need from the system
3. Matches to actions that can fulfill that need

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
| **REQUEST** | User wants the system to do something |
| **QUERY** | User is asking a question |
| **RESPONSE** | User is directly answering assistant's question |
| **SOCIAL** | Greeting, gratitude, acknowledgment, smalltalk |
| **NAVIGATION** | Topic change, cancellation, "stop" |
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
```

### Properties

- `model`: Model identifier (default: "gpt-4o-mini")
- `model_temperature`: Temperature for LLM (default: 0.1)
- `model_max_tokens`: Max tokens (default: 500)
- `history_limit`: Previous interactions to include (default: 3)
- `weight`: Execution weight (default: -100)
- `exceptions`: Action names that always execute

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
  - User is expressing gratitude (SOCIAL)
  - NOT directly engaging with NewsInteractAction
  - No specific need from the system
  
Result:
  intent_type: SOCIAL
  actions: []
```

### New Request During Signup

```
Conversation:
  [EVENT] Ongoing Activity: SignupInterviewInteractAction
  Assistant: "What is your email?"
  User: "What's the weather like?"
  
Analysis:
  - User is asking a question (QUERY)
  - NOT answering the signup question
  - Needs weather information
  
Result:
  intent_type: QUERY
  actions: ["WeatherAction"]
```

### Direct Response to Activity

```
Conversation:
  [EVENT] Ongoing Activity: SignupInterviewInteractAction
  Assistant: "What is your name?"
  User: "John Doe"
  
Analysis:
  - User is providing information (RESPONSE)
  - Directly answering SignupInterviewInteractAction's question
  
Result:
  intent_type: RESPONSE
  actions: ["SignupInterviewInteractAction"]
```
