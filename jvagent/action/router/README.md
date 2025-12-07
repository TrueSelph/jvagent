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

InteractActions can publish anchors that describe when they should be used. Anchors are defined as a dictionary mapping entity names to lists of anchor statements:

```python
class MyInteractAction(InteractAction):
    anchors: Dict[str, List[str]] = {
        "MyAction": [
            "User requests a report update",
            "User asks about report status",
            "User wants to check report progress"
        ]
    }
```

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

- `model_action_type`: Type of ModelAction to use (e.g., "OpenAIModelAction"). If empty, uses first available ModelAction.
- `history_limit`: Number of previous interactions to include in conversation history (default: 10)
- `weight`: Execution weight (default: -100 to run first)

### Example Configuration

```yaml
actions:
  - action: jvagent/interact_router
    context:
      enabled: true
      model_action_type: "OpenAIModelAction"
      history_limit: 15
```

## Usage

### Publishing Anchors

To enable routing to your InteractAction, publish anchors:

```python
from jvagent.action.interact.base import InteractAction
from typing import Dict, List

class ReportAction(InteractAction):
    anchors: Dict[str, List[str]] = {
        "ReportAction": [
            "User requests a report update",
            "User asks about report status",
            "User wants to check report progress",
            "User needs report information"
        ]
    }
    
    async def execute(self, here, visitor):
        interaction = visitor.interaction
        
        # Check if this action was routed to
        if "ReportAction" not in interaction.anchors:
            return  # Skip if not routed
        
        # Process the request using interpretation for context
        interpretation = interaction.interpretation
        # ... process report request ...
```

### Checking Routing Results

InteractActions can check routing results:

```python
async def execute(self, here, visitor):
    interaction = visitor.interaction
    
    # Check if routed to this action
    if self.label not in interaction.anchors:
        logger.debug(f"{self.label} not routed, skipping")
        return
    
    # Use interpretation for context
    if interaction.interpretation:
        logger.info(f"Processing: {interaction.interpretation}")
    
    # Process with confidence awareness
    if interaction.routing_confidence and interaction.routing_confidence < 0.5:
        logger.warning("Low routing confidence, may need fallback")
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

- Requires a ModelAction to be registered (e.g., OpenAIModelAction)
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
class WeatherAction(InteractAction):
    anchors: Dict[str, List[str]] = {
        "WeatherAction": [
            "User asks about weather",
            "User wants weather forecast",
            "User requests weather information",
            "User asks what's the weather like"
        ]
    }
    
    async def execute(self, here, visitor):
        if "WeatherAction" not in visitor.interaction.anchors:
            return
        # Process weather request...
```

### Example 2: Action with Multiple Entity Names

```python
class ReportAction(InteractAction):
    anchors: Dict[str, List[str]] = {
        "ReportAction": [
            "User requests a report update",
            "User asks about report status"
        ],
        "ReportGenerator": [
            "User wants to generate a report",
            "User requests new report creation"
        ]
    }
```

## Troubleshooting

### No Anchors Found

If InteractRouter reports "No anchors available":
- Ensure other InteractActions have published anchors
- Check that InteractActions are enabled
- Verify anchors are defined as `Dict[str, List[str]]`

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

