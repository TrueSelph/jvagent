# InteractRouter Action

Intent-based routing action that analyzes utterances and routes to appropriate InteractActions.

## Overview

InteractRouter is a specialized InteractAction that runs first (weight: -100) to analyze incoming user utterances and determine which InteractActions should handle them. It uses an LLM to:

1. Generate a concise interpretation of the user's intent
2. Match the interpretation against published anchor statements from other InteractActions
3. Store routing results on the Interaction node

## Configuration

The InteractRouter can be configured in `agent.yaml`:

```yaml
- action: jvagent/interact_router
  context:
    enabled: true
    model_action_type: "OpenAILanguageModelAction"  # Entity type to find dynamically
    history_limit: 10  # Number of previous interactions to include in context
    exceptions: []  # Optional: List of InteractAction entity names that must always execute
```

## How It Works

1. **Runs First**: InteractRouter executes before other InteractActions (weight: -100)
2. **Collects Anchors**: Gathers anchor statements from all enabled InteractActions
3. **Builds Context**: Extracts conversation history (configurable limit)
4. **LLM Analysis**: Calls the model action to generate interpretation and match anchors
5. **Stores Results**: Saves routing results (`interpretation`, `anchors`, `routing_confidence`) on the Interaction node

## InteractWalker Integration

The InteractWalker automatically filters top-level InteractActions based on routing results:
- Only actions whose entity names match the routed anchors are executed
- Exceptions are always included regardless of routing
- Order is preserved (weight-based sorting happens after filtering)

## Note

This is a stub that imports the actual implementation from the core `jvagent.action.router.interact_router` module.

