# ConverseInteractAction

Fallback action for smalltalk and casual conversation when no other action has
generated a response.

## Overview

`ConverseInteractAction` is an `InteractAction` that runs late (high positive
weight) as a safety net. It triggers when the current `Interaction` has no
response, or when there are unexecuted directives (even if a response exists).
It provides conservative guidance to PersonaAction for handling smalltalk while
avoiding unsafe knowledge answers.

## Features

- **Fallback Execution**: Runs when no response has been generated yet, OR when
  there are unexecuted directives that need to be executed (even if a response
  already exists). This ensures directives furnished by other actions without
  responses are properly executed.
- **Smalltalk Handling**: Provides a directive for simple, friendly replies to
  smalltalk and casual conversation.
- **Conservative Knowledge Behavior**: Instructs PersonaAction to *never* answer
  knowledge-based or factual questions without sufficient certainty and to opt
  out instead of guessing.
- **Behavioral Parameters**: Supplies default parameters that enforce or offset
  the directive (e.g., decline factual questions, respond briefly to smalltalk).
- **Routing Exception**: Marked with `always_execute=True` so InteractRouter
  always allows it to execute (treated as a routing exception).
- **Health Check**: Validates that the directive is configured.

## Installation

### 1. Add to agent.yaml

Add the ConverseInteractAction to your agent's configuration:

```yaml
actions:
  - action: jvagent/converse_interact_action
    context:
      enabled: true
      description: "Fallback action for smalltalk and casual conversation"
      weight: 100  # Runs last as a safety net
      directive: |
        Offer a simple, friendly response to smalltalk and casual conversation. 
        NEVER attempt to answer knowledge-based questions without having certainty 
        about the context. If the conversation does not warrant a reply or you 
        lack sufficient information, politely opt out rather than guessing.
      parameters:
        - condition: "User asks a knowledge-based or factual question"
          response: "Politely decline to answer, explaining that you don't have sufficient context or certainty to provide an accurate answer. Suggest they check official sources or provide more specific context."
        - condition: "User engages in smalltalk, greetings, or casual conversation"
          response: "Respond naturally and conversationally, keeping it brief and friendly."
        - condition: "The conversation does not warrant a substantive reply"
          response: "Politely acknowledge the message but indicate that no specific response is needed, or ask how you can help."
        - condition: "User asks about something outside your knowledge or role"
          response: "Politely explain that this falls outside your area of knowledge or role, and suggest alternative ways they might find the information they need."
```

## Execution Semantics

- **Weight and Ordering**  
  The default weight is `100`, so it runs after other InteractActions. It
  proceeds if `interaction.has_response()` is `False` OR if there are unexecuted
  directives present.

- **Routing Exception**  
  The action defines:
  ```python
  always_execute: bool = attribute(
      default=True,
      description="Always execute as a last-resort smalltalk fallback regardless of routing.",
  )
  ```
  InteractRouter discovers this flag and automatically adds
  `ConverseInteractAction` to `interaction.anchors` as a routing exception, so
  `InteractWalker` will not skip it due to routing.

- **Fallback Logic**  
  In `execute()`:
  - If there is no `Interaction`, it calls `visitor.unrecord_action_execution()`
    and returns.
  - If `interaction.has_response()` is true AND there are no unexecuted directives,
    it unrecords itself and returns.
  - If there are unexecuted directives (even if a response exists), or if no
    response exists, it proceeds to execute:
    ```python
    await self.respond(
        visitor,
        directives=[self.directive],
        parameters=self.parameters if self.parameters else None,
    )
    ```
  This ensures that directives furnished by other actions without generating
  responses are properly executed and result in a generated response.

## When to Use

Use `ConverseInteractAction` when you want a safe, conversational fallback that:

- Handles chit-chat and smalltalk gracefully.
- Avoids hallucinating answers to factual or knowledge-based questions.
- Allows the agent to politely decline or opt out when a response is not
  warranted or would be unreliable.

