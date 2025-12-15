# InteractRouter Action

InteractRouter is a specialized InteractAction that runs first to analyze incoming utterances, generate interpretations using an LLM, and match against published anchors from other InteractActions to determine routing.

## Overview

InteractRouter serves as the entry point for intent-based routing in the interact subsystem. It:

1. Collects anchors from all registered InteractActions
2. Builds conversation history context from previous interactions
3. Calls an LLM to generate an interpretation of the user's intent
4. Matches the interpretation against available anchor statements
5. Stores routing results (interpretation, matched entities, confidence) on the Interaction node

## Architecture

### Anchor System

InteractActions can publish anchors that describe when they should be used. Anchors are defined as a simple list of anchor statements. The action's class/entity name is automatically used as the key when collected by InteractRouter:

```python
class MyInteractAction(InteractAction):
    anchors: List[str] = [
        "User requests a report update",
        "User asks about report status",
        "User wants to check report progress"
    ]
```

The InteractRouter automatically uses the class name (`MyInteractAction`) as the entity name when collecting anchors, so you don't need to specify it yourself.

### Routing Flow

1. **InteractRouter executes first** (weight: -100)
2. **Collects anchors** from all enabled InteractActions
3. **Builds context** from conversation history (configurable limit, default: 10 interactions)
4. **Calls LLM** with prompt containing:
   - Current utterance
   - Conversation history (utterances, responses, events)
   - Available anchors dictionary
5. **Parses JSON response**:
   ```json
   {
     "interpretation": "User has requested a report update bearing reference number 12345",
     "entities": ["MyAction", "ReportAction"],
     "confidence": 0.85
   }
   ```
6. **Stores on Interaction**:
   - `interpretation`: LLM-generated interpretation (< 50 words)
   - `anchors`: List of matched entity names
   - `routing_confidence`: Confidence score (0.0-1.0)

### Action Execution

After InteractRouter stores routing information, other InteractActions can:

1. Check `interaction.anchors` to see if they were routed to
2. Optionally skip execution if not in the anchors list
3. Use `interaction.interpretation` for context-aware processing

## Configuration

### Properties

- `model_action_type`: Type of LanguageModelAction to use (e.g., "OpenAILanguageModelAction"). If empty, uses first available LanguageModelAction.
- `history_limit`: Number of previous interactions to include in conversation history (default: 10)
- `weight`: Execution weight (default: -100 to run first)
- `exceptions`: Optional list of InteractAction entity names (class names) that must always execute, regardless of routing. Most routing‑exception use cases can also be handled via the `always_execute` flag on InteractAction (see below).

### Example Configuration

```yaml
actions:
  - action: jvagent/interact_router
    context:
      enabled: true
      model_action_type: "OpenAILanguageModelAction"
      history_limit: 15
      # Optional static exceptions (dynamic ones come from always_execute=True)
      # exceptions:
      #   - "SomeInteractAction"
```

## Usage

### Publishing Anchors

To enable routing to your InteractAction, publish anchors:

```python
from jvagent.action.interact.base import InteractAction
from typing import List

class ReportAction(InteractAction):
    anchors: List[str] = [
        "User requests a report update",
        "User asks about report status",
        "User wants to check report progress",
        "User needs report information"
    ]
    
    async def execute(self, visitor):
        interaction = visitor.interaction
        
        # Check if this action was routed to
        # The entity name is automatically the class name
        if "ReportAction" not in interaction.anchors:
            return  # Skip if not routed
        
        # Process the request using interpretation for context
        interpretation = interaction.interpretation
        # ... process report request ...
```

### Checking Routing Results

InteractActions can check routing results:

```python
async def execute(self, visitor):
    interaction = visitor.interaction
    
    # Check if routed to this action
    # Use the class name as the entity name
    entity_name = self.__class__.__name__
    if entity_name not in interaction.anchors:
        logger.debug(f"{entity_name} not routed, skipping")
        return
    
    # Use interpretation for context
    if interaction.interpretation:
        logger.info(f"Processing: {interaction.interpretation}")
    
    # Process with confidence awareness
    if interaction.routing_confidence and interaction.routing_confidence < 0.5:
        logger.warning("Low routing confidence, may need fallback")

## Routing Exceptions

In addition to LLM‑based routing, InteractRouter supports **routing exceptions**:
actions that must always be allowed to execute, regardless of whether the LLM
selected them.

There are two complementary mechanisms:

1. **Static exceptions via configuration**
   ```yaml
   - action: jvagent/interact_router
     context:
       exceptions:
         - "SomeInteractAction"
   ```
   These class names are always included in `interaction.anchors`.

2. **Dynamic exceptions via `always_execute` flag on InteractAction**
   ```python
   from jvagent.action.interact.base import InteractAction
   from jvspatial.core.annotations import attribute

   class MyInteractAction(InteractAction):
       always_execute: bool = attribute(
           default=True,
           description="Always execute regardless of routing.",
       )
   ```

   At runtime, InteractRouter:
   - Collects all enabled `InteractAction` instances.
   - Builds a list of dynamic exceptions from those with `always_execute=True`.
   - Merges that list with static `exceptions` from context.
   - Stores the combined set in `interaction.anchors`.

Because `InteractWalker` uses `interaction.anchors` to decide which actions to
skip, any action with `always_execute=True` is treated as a routing exception
and will not be skipped, even if the router did not explicitly route to it.

Examples:

- `IntroInteractAction` is marked `always_execute=True` so first‑time user
  intros can always run when applicable.
- `ConverseInteractAction` is marked `always_execute=True` so it can act as a
  last‑resort smalltalk fallback when no other action has produced a response.
```

## Prompt Engineering

The InteractRouter uses a carefully crafted prompt that:

1. Explains the routing task
2. Provides conversation history context
3. Lists all available anchors in structured format
4. Instructs the LLM to:
   - Generate a concise interpretation (< 50 words)
   - Match against anchor statements
   - Return JSON with interpretation, entities, and confidence

The system prompt emphasizes precision and only matching when there's clear alignment.

## Dependencies

- Requires a LanguageModelAction to be registered (e.g., OpenAILanguageModelAction)
  - Retrieved using `await self.get_model_action()` (recommended)
  - Define `model_action_type` attribute to specify a particular model, or omit for any available
- Requires other InteractActions to publish anchors for routing to work
- Uses conversation history from the Interaction chain

## Best Practices

1. **Publish Clear Anchors**: Use specific, descriptive anchor statements that clearly indicate when your action should be used
2. **Check Routing**: Always check `interaction.anchors` before processing
3. **Use Interpretation**: Leverage `interaction.interpretation` for context-aware processing
4. **Handle Low Confidence**: Consider fallback behavior when `routing_confidence` is low
5. **Keep Anchors Updated**: Update anchors as your action's capabilities evolve

## Examples

### Example 1: Simple Action with Anchors

```python
from typing import List

class WeatherAction(InteractAction):
    anchors: List[str] = [
        "User asks about weather",
        "User wants weather forecast",
        "User requests weather information",
        "User asks what's the weather like"
    ]
    
    async def execute(self, visitor):
        # The entity name is automatically the class name
        if "WeatherAction" not in visitor.interaction.anchors:
            return
        # Process weather request...
```

## Troubleshooting

### No Anchors Found

If InteractRouter reports "No anchors available":
- Ensure other InteractActions have published anchors
- Check that InteractActions are enabled
- Verify anchors are defined as `List[str]` (entity name is automatically the class name)

### No Matches Found

If routing returns empty entities:
- Review anchor statements for clarity
- Check if user intent aligns with any anchors
- Consider adding more anchor variations
- Review LLM response for interpretation quality

### Low Confidence Scores

If confidence scores are consistently low:
- Improve anchor statement specificity
- Add more context to conversation history
- Review and refine the routing prompt
- Consider adjusting LLM temperature (currently 0.3)

