# SwitchboardInterviewInteractAction

Structured interview action for guiding users through agent selection in a multi-agent system.

## Overview

The `SwitchboardInterviewInteractAction` provides a conversational interview flow for agent selection. It presents available agents, validates user selection, and stores the chosen agent in conversation context for routing by `SwitchboardInteractAction`.

## Features

- **Guided Selection**: Structured interview flow for agent selection
- **Dynamic Agent List**: Fetches available agents dynamically via context provider
- **Input Validation**: Validates user's agent selection
- **Cancellation Handling**: Gracefully handles user cancellation with context cleanup
- **Completion Handling**: Stores selected agent and confirms connection
- **Intelligent Routing**: Uses anchors for InteractRouter to detect agent switching intent
- **Unified Classification**: Single LLM call for intent detection and value extraction

## Architecture

Inherits from `InterviewInteractAction` and uses a unified classification and extraction approach that detects user intent (CANCELLATION, CONFIRMATION, UPDATE, SUBMISSION, NONE) and extracts field values in a single LLM call.

**Session Management**: Sessions are identified by `interview_type='SwitchboardInterviewInteractAction'` and attached to Conversation nodes for per-user persistence.

## Configuration

### Agent Configuration (agent.yaml)

```yaml
- action: jvagent/switchboard_interview_interact_action
  context:
    enabled: true
    description: "Interview action for guiding users through agent selection"
    weight: -50  # Runs before fallback actions
  config:
    model_action_type: "OpenAILanguageModelAction"
    model: "gpt-4o-mini"
    model_temperature: 0.1
    model_max_tokens: 4096
    use_history: true
    max_statement_length: 100
    history_limit: 3
```

### Routing Anchors

The action publishes anchors for InteractRouter routing:

- "MESSAGE references an agent by name AND includes an explicit action such as connect, switch, talk to, or disconnect"
- "MESSAGE explicitly asks to be removed from the current agent or stop interacting with them"
- "MESSAGE explicitly asks to switch to a different agent or project"
- "MESSAGE includes a clear request to change location or department"
- "MESSAGE clearly says connect me to <agent> or switch to <agent>"
- "MESSAGE references an agent by name and asks to interact with them instead of the current one"
- "MESSAGE is Extract selected_agent as the Id of the agent explicitly mentioned by name from the provided list. Only return the Id if the user directly references an agent by name."

## Interview Flow

### Question Graph

1. **selected_agent** (required)
   - Question: "Please select an agent you wish to be routed to"
   - Type: string
   - Validation: Must be a valid agent alias
   - Context Provider: `get_switchboard_agents` (provides dynamic agent list)
   - Special Instruction: Detects disconnect/exit intent as CANCELLATION

## Custom Components

### Context Provider

- `get_switchboard_agents`: Dynamically provides available agents for the question prompt

### Validators

- `validate_selected_agent`: Ensures the selected value is a valid string (basic validation)

### Completion Handler

- `handle_interview_completion`: Matches selected agent alias to agent data and stores in conversation context

### Cancellation Handler

- `handle_interview_cancellation`: Clears switchboard_agent context and notifies user

## Completion Handler

The `handle_interview_completion` function processes the collected data:

1. Retrieves available agents from SwitchboardInteractAction
2. Matches selected agent alias to agent data
3. Stores selected agent in `conversation.context["switchboard_agent"]`
4. Sends confirmation message to user
5. Cleans up interview session

## Cancellation Handler

The `handle_interview_cancellation` function handles user cancellation:

1. Clears `conversation.context["switchboard_agent"]`
2. Notifies user they are not connected to any agent
3. Cleans up interview session

## Integration with SwitchboardInteractAction

The two actions work together in sequence:

1. **SwitchboardInteractAction** presents available agents
2. User expresses intent to switch agents (triggers routing anchors)
3. **SwitchboardInterviewInteractAction** starts interview
4. User selects agent
5. **SwitchboardInterviewInteractAction** stores selection in context
6. **SwitchboardInteractAction** routes to selected agent on next interaction

## Usage

### Starting the Interview

User utterances matching the anchors will trigger the interview:

- "I want to talk to the support agent"
- "Switch me to the sales team"
- "Connect me to billing"
- "I need to speak with a different agent"

### Example Interaction

```
User: I want to switch to the support agent
Agent: Please select an agent you wish to be routed to:
       - Support Agent (for technical support)
       - Sales Agent (for product inquiries)
       - Billing Agent (for payment questions)

User: Support Agent
Agent: You're now chatting with Support Agent. Say hi to get started.

[Next interaction routes to Support Agent]
```

### Cancellation Example

```
User: I want to switch agents
Agent: Please select an agent you wish to be routed to:
       - Support Agent
       - Sales Agent
       - Billing Agent

User: Never mind, cancel that
Agent: No problem! You are not connected to any agent at the moment.
```

## Validation Rules

- **Selected Agent**: Must be a non-empty string (basic validation)
- **Cancellation Detection**: Keywords like "disconnect", "leave", "exit", "stop" trigger cancellation

## Context Provider Details

The `get_switchboard_agents` function:

1. Initializes `conversation.context["switchboard_agent"]` to empty dict
2. Retrieves SwitchboardInteractAction instance
3. Calls `get_switchboard_agents()` to fetch available agents
4. Formats agent aliases as comma-separated string
5. Returns dict with "agents" key for prompt interpolation

## Conversation Context Structure

After completion, stores in `conversation.context["switchboard_agent"]`:

```python
{
    "id": "agent_uuid",
    "name": "support_agent",
    "alias": "Support Agent",
    "description": "Technical support specialist"
}
```

## Dependencies

- `jvagent/interview` - Interview framework
- `jvagent/switchboard_interact_action` - For agent discovery and routing
- `jvagent/openai_lm` - Language model for classification and extraction

## File Structure

```
switchboard_interview_interact_action/
├── __init__.py                                  # Package initialization
├── switchboard_interview_interact_action.py     # Main action implementation
├── info.yaml                                    # Action metadata
└── README.md                                    # This file
```

## Customization

### Adding Additional Questions

Extend the `question_graph` to collect more information:

```python
question_graph: List[Dict[str, Any]] = attribute(
    default_factory=lambda: [
        {
            "name": "selected_agent",
            "question": "Please select an agent you wish to be routed to",
            "input_context_provider": "get_switchboard_agents",
            "constraints": {
                "description": "Select the correct agent the user wishes to route to",
                "type": "string",
            },
            "required": True
        },
        {
            "name": "reason",
            "question": "What do you need help with?",
            "constraints": {
                "description": "Reason for contacting the agent",
                "type": "string",
            },
            "required": False
        }
    ]
)
```

### Custom Validation

Add more sophisticated validation:

```python
@input_validator("selected_agent")
async def validate_selected_agent(
    value: str,
    session: InterviewSession,
    visitor: Optional[InteractWalker] = None,
    interview_action: Optional[Any] = None,
) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate that the selected agent exists and is available."""
    
    if not value or not isinstance(value, str):
        return ValidationStatus.INVALID, "Ask: Please select an agent you wish to be routed to."
    
    # Get available agents
    switchboard_action = await interview_action.get_action("SwitchboardInteractAction")
    agents = await switchboard_action.get_switchboard_agents()
    
    # Check if selected agent exists
    agent_aliases = [agent["alias"] for agent in agents]
    if value not in agent_aliases:
        return ValidationStatus.INVALID, f"Ask: Please select from the available agents: {', '.join(agent_aliases)}"
    
    return ValidationStatus.VALID, None
```

### Custom Completion Message

Modify the completion handler:

```python
@on_interview_complete('SwitchboardInterviewInteractAction')
async def handle_interview_completion(
    session: InterviewSession,
    visitor: InteractWalker,
    action: InteractAction
) -> None:
    """Handle completion with custom message."""
    
    switchboard_action = await action.get_action("SwitchboardInteractAction")
    agents = await switchboard_action.get_switchboard_agents()

    selected_agent = {}
    for agent in agents:
        if agent['alias'] == session.responses.get('selected_agent', ''):
            selected_agent = agent
            break
    
    if selected_agent:
        conversation = visitor.conversation
        conversation.context["switchboard_agent"] = selected_agent
        
        # Custom completion message
        completion_message = f\"\"\"
        Great! I'm connecting you to {selected_agent.get('alias')}.
        
        {selected_agent.get('description')}
        
        They'll be with you shortly. Feel free to start the conversation!
        \"\"\"
        await visitor.add_directives([completion_message])
        await action.respond(visitor)

    await session.cleanup()
```

### Agent Recommendations

Add intelligent agent recommendations:

```python
@input_context_provider()
async def get_switchboard_agents_with_recommendation(
    session: InterviewSession,
    visitor: Optional[InteractWalker] = None,
    interview_action: Optional[InteractAction] = None
) -> Dict[str, Any]:
    """Provide agents with AI-powered recommendation."""
    
    conversation = visitor.conversation
    conversation.context["switchboard_agent"] = {}
    
    switchboard_action = await interview_action.get_action("SwitchboardInteractAction")
    agents = await switchboard_action.get_switchboard_agents()
    
    # Analyze user's recent messages to recommend agent
    recent_messages = conversation.get_recent_messages(limit=5)
    user_intent = await analyze_intent(recent_messages)
    
    # Find best matching agent
    recommended_agent = find_best_agent(agents, user_intent)
    
    agents_str = ", ".join(agent["alias"] for agent in agents)
    recommendation = f"Based on your request, I recommend {recommended_agent['alias']}."
    
    return {
        "agents": agents_str,
        "recommendation": recommendation
    }
```

## Error Handling

- Gracefully handles missing SwitchboardInteractAction
- Returns validation errors for invalid selections
- Cleans up session on cancellation or completion
- Handles missing agent data gracefully

## Testing

Test scenarios:

- Basic agent selection flow
- Cancellation at selection stage
- Invalid agent selection
- Agent not found in list
- Context provider execution
- Completion handler storing correct data
- Cancellation handler clearing context
- Multiple agents with similar names
- Case-insensitive agent matching

## Known Issues

- None currently documented

## Performance Considerations

- Single LLM call for classification and extraction
- Cached agent list from SwitchboardInteractAction
- Async context provider execution
- Efficient session cleanup
- Minimal conversation context storage

## Future Enhancements

1. **Agent Availability Check**: Verify agent is online before selection
2. **Queue Management**: Show estimated wait times for each agent
3. **Agent Ratings**: Display agent ratings and reviews
4. **Smart Routing**: AI-powered agent recommendations based on query
5. **Multi-Language Support**: Agent selection in user's preferred language
6. **Agent Specializations**: Filter agents by expertise or department
7. **Priority Routing**: VIP users get priority agent access
8. **Agent Handoff**: Seamless transfer between agents with context
