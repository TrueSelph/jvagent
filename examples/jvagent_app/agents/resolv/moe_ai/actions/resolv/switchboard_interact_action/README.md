# SwitchboardInteractAction

Agent routing action that presents available agents and routes users to their selected agent.

## Overview

The `SwitchboardInteractAction` manages multi-agent routing by presenting a list of available agents to users and seamlessly routing them to their selected agent via sub-walker spawning. It maintains agent selection state in conversation context and handles automatic routing when an agent is already selected.

## Features

- **Agent Discovery**: Automatically discovers and lists all available agents in the system
- **Dynamic Agent List**: Fetches agents from the Agents node if not configured
- **Sub-Walker Routing**: Spawns sub-walkers to route users to selected agents
- **State Persistence**: Maintains agent selection in conversation context
- **Always Execute**: Runs on every interaction to handle routing (weight: 0)
- **Routing Exception**: Marked with `always_execute=True` so InteractRouter always allows it to execute
- **Seamless Handoff**: Transfers user context and session data to target agent

## Architecture

Inherits from `InteractAction` and uses the `always_execute` flag to run on every interaction, checking for agent routing requests and handling them automatically.

## Configuration

### Agent Configuration (agent.yaml)

```yaml
actions:
  - action: resolv/switchboard_interact_action
    context:
      enabled: true
      description: "Switchboard action for presenting and routing to available agents"
      weight: 0 # Runs at normal priority
      available_switchboard_agents_directive: |
        Present these agents to the user and ask them to choose a single agent from the list:

        {agents}
      switchboard_agents: [] # Optional: Pre-configure agents, otherwise auto-discovered
```

### Configuration Properties

- **switchboard_agents** (List[dict]): Optional list of pre-configured agents with id, name, alias, and description
- **available_switchboard_agents_directive** (str): Template for presenting agents to users (uses `{agents}` placeholder)
- **always_execute** (bool): Always runs regardless of routing (default: True)
- **weight** (int): Execution order (default: 0)

## Execution Logic

### Routing Decision Flow

The action executes in two modes:

1. **Routing Mode** (when `switchboard_agent` is set in conversation context):
   - Retrieves target agent by ID
   - Falls back to name-based lookup if ID not found
   - Creates sub-walker with copied context
   - Spawns walker on target agent
   - Returns without presenting agent list

2. **Presentation Mode** (when no agent is selected):
   - Fetches available agents
   - Formats agent list
   - Adds directive to present agents
   - Delegates to PersonaAction for response generation

### Conditions for Execution

The action skips execution if:

- `SwitchboardInterviewInteractAction` is already running (prevents conflicts)

## Methods

### execute

Main execution method that handles routing logic.

```python
async def execute(self, visitor: InteractWalker) -> None:
    # Check if interview is running
    # Route to agent if selected
    # Otherwise present agent list
```

### get_switchboard_agents

Retrieve list of available switchboard agents.

```python
agents = await action.get_switchboard_agents()
```

**Returns:** List of agent dictionaries with id, name, alias, and description

**Behavior:**

- Returns pre-configured agents if available
- Otherwise fetches from Agents node
- Excludes current agent from list
- Caches results for future calls

## Usage

### Automatic Agent Presentation

When no agent is selected, the action presents available agents:

```
User: I need help
[SwitchboardInteractAction executes]
- Fetches available agents
- Adds directive with agent list

Agent: I can connect you to one of these agents:
       - Support Agent (for technical support)
       - Sales Agent (for product inquiries)
       - Billing Agent (for payment questions)

       Which agent would you like to talk to?
```

### Automatic Routing

When an agent is selected (via SwitchboardInterviewInteractAction):

```
[Conversation context has switchboard_agent set]
User: Hello
[SwitchboardInteractAction executes]
- Detects selected agent in context
- Creates sub-walker for target agent
- Spawns walker on target agent
- User is now interacting with selected agent
```

## Integration with SwitchboardInterviewInteractAction

The two actions work together:

1. **SwitchboardInteractAction** presents the agent list
2. **SwitchboardInterviewInteractAction** collects user's selection
3. **SwitchboardInterviewInteractAction** stores selection in conversation context
4. **SwitchboardInteractAction** routes to selected agent on next interaction

## Sub-Walker Spawning

When routing to a target agent, the action:

1. Copies visitor data and adds `switchboard_agent_id`
2. Creates new `InteractWalker` with:
   - Target agent ID
   - Original utterance
   - Original channel
   - Copied data with agent context
   - Same session, user, and stream
3. Spawns walker on target agent
4. Target agent processes interaction independently

## Agent Discovery

If `switchboard_agents` is not configured:

1. Fetches `Agents` node from graph
2. Retrieves all connected agents
3. Filters out current agent
4. Extracts id, name, alias, and description
5. Caches in `switchboard_agents` attribute
6. Saves action state

## Execution Flow

```
1. User sends message
   ↓
2. InteractRouter processes interaction
   ↓
3. SwitchboardInteractAction executes (always_execute=True)
   ↓
4. Check: Is SwitchboardInterviewInteractAction running?
   ↓ NO
5. Check: Is switchboard_agent set in context?
   ↓ YES: Route to agent via sub-walker
   ↓ NO: Present agent list
6. PersonaAction generates response with agent list
```

## Conversation Context Structure

The action uses `conversation.context["switchboard_agent"]` to store:

```python
{
    "id": "agent_uuid",
    "name": "support_agent",
    "alias": "Support Agent",
    "description": "Technical support specialist"
}
```

## Dependencies

- `jvagent.core.agent.Agent` - Agent node access
- `jvagent.core.agents.Agents` - Agents collection node
- `jvagent.action.interact.base.InteractAction` - Base action class
- `jvagent.action.interact.interact_walker.InteractWalker` - Walker for sub-agent spawning

## File Structure

```
switchboard_interact_action/
├── __init__.py                        # Package initialization
├── switchboard_interact_action.py     # Main action implementation
├── info.yaml                          # Action metadata
└── README.md                          # This file
```

## Customization

### Custom Agent Presentation

Update the directive template in agent.yaml:

```yaml
available_switchboard_agents_directive: |
  Here are the available specialists:

  {agents}

  Please type the name of the specialist you'd like to connect with.
```

### Pre-Configure Agents

Manually specify agents instead of auto-discovery:

```yaml
switchboard_agents:
  - id: "agent-uuid-1"
    name: "support_agent"
    alias: "Support Agent"
    description: "Technical support specialist"
  - id: "agent-uuid-2"
    name: "sales_agent"
    alias: "Sales Agent"
    description: "Product and pricing expert"
```

### Custom Routing Logic

Extend the `execute` method to add custom routing rules:

```python
async def execute(self, visitor: InteractWalker) -> None:
    # Check for priority routing
    if visitor.data.get('priority') == 'high':
        # Route to priority agent
        conversation = visitor.conversation
        conversation.context["switchboard_agent"] = {
            "id": "priority_agent_id",
            "name": "priority_agent",
            "alias": "Priority Agent",
            "description": "Handles urgent requests"
        }

    # Continue with normal execution
    await super().execute(visitor)
```

### Agent Filtering

Filter agents based on user attributes:

```python
async def get_switchboard_agents(self) -> list[dict]:
    all_agents = await super().get_switchboard_agents()

    # Filter based on user role
    user = await self.get_user()
    if user.role == "premium":
        return [a for a in all_agents if a.get("tier") == "premium"]

    return all_agents
```

## Error Handling

- Gracefully handles missing Agents node (returns empty list)
- Falls back to name-based lookup if agent ID not found
- Skips routing if target agent doesn't exist
- Continues execution even if agent discovery fails

## Testing

Test scenarios:

- Agent list presentation when no agent selected
- Routing to selected agent
- Agent discovery from Agents node
- Pre-configured agent list
- Missing target agent handling
- Sub-walker spawning with context
- Concurrent interview prevention
- Agent filtering (current agent excluded)

## Known Issues

- None currently documented

## Performance Considerations

- Runs on every interaction (always_execute=True)
- Early return when interview is running (minimal overhead)
- Caches agent list after first fetch
- Async agent discovery (non-blocking)
- Efficient sub-walker spawning

## Future Enhancements

1. **Agent Availability**: Check agent availability before presenting
2. **Load Balancing**: Route to least busy agent automatically
3. **Agent Capabilities**: Match user needs to agent capabilities
4. **Routing Analytics**: Track routing patterns and success rates
5. **Multi-Agent Collaboration**: Support multiple agents working together
6. **Agent Recommendations**: AI-powered agent suggestions based on user query
7. **Fallback Routing**: Automatic fallback if selected agent unavailable
