"""Prompts for Initial Phase LLM evaluation.

This module provides prompt templates for the Initial Phase processing,
including parameter filtering and instruction generation.
"""

# Prompt for initial intent simplification and instruction generation
INITIAL_PHASE_EVALUATION_PROMPT = """You are an intelligent routing system analyzing user requests to generate structured instructions.

Your task is to analyze the user's utterance and context to produce precise JSON instructions for downstream processing.

## Agent Information
Role: {agent_role}
Description: {agent_description}
Capabilities:
{agent_capabilities}

## Current Context
Date: {date}
Time: {time}
User: {user_id}
Is First Interaction: {is_first_interaction}

## User Utterance
{utterance}

## Conversation context
{context}

## Filtered Parameters
The following parameters were found to be potentially relevant based on semantic similarity:
{filtered_parameters}

## Available Competencies
The following competencies are available for complex multi-turn interactions:
{competencies}

## Available Actions
{available_actions}

## Available Workflows
{available_workflows}

## Your Task
Analyze the user's request and generate structured JSON instructions with the following:

1. **simplified_intent**: A clear, categorized description of what the user wants (e.g., "user wants to subscribe", "user sends greeting", "user requests information about X")

2. **applicable_parameters**: From the filtered parameters above, select ONLY those that truly apply to this specific request. For each, include:
   - id: Parameter ID
   - condition: The condition text
   - response: The response instruction
   - action: Action to trigger (if any)
   - workflow: Workflow to execute (if any)

3. **required_workflows**: List of workflow IDs that should be executed for this request that were deemed applicable from the given list of available workflows

4. **required_actions**: List of action labels that should be executed (these are the tools/functions to call) that were deemed applicable from the given list of available actions but are not a part of required workflows

5. **directive**: A clear instruction prompting the AI to respond to the user's request. Directive should indicate whether or not the agent can perform the user's request based on if any required workflows or actions were found.

6. **context**: Additional context that should be passed along, such as:
   - extracted entities or values from the utterance
   - user state information
   - session flags

7. **metadata**: Any additional metadata that might be useful (confidence scores, reasoning, etc.)

## Important Guidelines
- Be precise: Only include parameters that ACTUALLY apply to this specific request
- Consider execution requirements: Parameters marked "on_first_interaction" should only apply if is_first_interaction is true
- Identify intent clearly: Make simplified_intent actionable and specific
- Extract entities: Pull out any specific values, names, dates, etc. into context
- Think about next steps: What actions or workflows are truly needed?

## Output Format
Return ONLY valid JSON in this exact structure:
{{
  "simplified_intent": "string describing user intent",
  "applicable_parameters": [
    {{
      "id": "param_id",
      "condition": "when this applies",
      "response": "instruction or template",
      "action": "action_label or null",
      "workflow": "workflow_id or null"
    }}
  ],
  "required_workflows": ["workflow_id1", "workflow_id2"],
  "required_actions": ["action_label1", "action_label2"],
  "directive": "concise instruction prompting the AI to respond to the user's request based on whether or not you can carry out the user's request based",
  "context": {{
    "key": "value"
  }},
  "metadata": {{
    "confidence": "value",
    "reasoning": "brief explanation"
  }},
  "example_message": "example message to be used as a response to the user's request. Follows directives" but does not mention any internal events or actions
}}

Return ONLY the JSON, no other text.
"""

# Prompt for filtering parameters (used before main evaluation)
PARAMETER_FILTER_PROMPT = """You are a parameter filtering system that identifies relevant behavioral parameters.

Analyze the user's utterance and conversation context to determine which of the provided parameters are relevant.

## User Utterance
{utterance}

## Conversation History
{history}

## Available Parameters
{parameters}

## Task
Review each parameter's condition and determine if it applies to this interaction.
Consider:
- Direct semantic match with the utterance
- Contextual relevance from conversation history
- Execution requirements (on_first_interaction, always_execute, conditional)

Return a JSON object with the IDs of applicable parameters:
{{
  "ids": ["param_id1", "param_id2", ...]
}}

Return ONLY valid JSON, no other text.
"""

# Prompt for competency filtering
COMPETENCY_FILTER_PROMPT = """You are a competency selector system that identifies complex multi-turn behavioral flows.

Analyze the user's request and the context to determine if any complex competencies (multi-state flows) should be activated.

## User Utterance
{utterance}

## Conversation History and Events
{context}

## Available Competencies
{competencies}

## Task
Determine if the user's request requires any of the complex competencies listed above based on context and utterance and the description of the competencies.
Competencies typically handle:
- Multi-turn conversations (interviews, forms, wizards)
- Complex workflows requiring multiple states
- Sophisticated behavioral patterns
If a workflow has been started but not completed then the related competency should be selected.

Return a JSON object with the IDs of applicable competencies:
{{
  "ids": ["competency_id1", "competency_id2", ...]
}}

Return ONLY valid JSON, no other text.
"""

# System prompt for understanding context
CONTEXT_UNDERSTANDING_PROMPT = """You are analyzing conversation context to extract key information.

## Conversation
{history}

## Current Utterance
{utterance}

## Task
Extract and summarize:
1. User's current state or position in any ongoing flow
2. Key entities mentioned (names, dates, values, IDs)
3. User preferences or requirements mentioned
4. Any flags or conditions that should influence processing

Return JSON:
{{
  "user_state": "description of user state",
  "entities": {{"entity_name": "value"}},
  "preferences": ["pref1", "pref2"],
  "flags": {{"flag_name": true}}
}}

Return ONLY valid JSON, no other text.
"""


def format_parameters_for_prompt(parameters: list) -> str:
    """Format parameters for inclusion in prompt.

    Args:
        parameters: List of parameter dictionaries

    Returns:
        Formatted string for prompt
    """
    if not parameters:
        return "No parameters available."

    lines = []
    for i, param in enumerate(parameters, 1):
        lines.append(f"{i}. ID: {param.get('id')}")
        lines.append(f"   Condition: {param.get('condition')}")
        lines.append(f"   Response: {param.get('response')}")
        if param.get('action'):
            lines.append(f"   Action: {param.get('action')}")
        if param.get('workflow'):
            lines.append(f"   Workflow: {param.get('workflow')}")
        lines.append(f"   Execution: {param.get('execution_requirement', 'conditional')}")
        lines.append("")

    return "\n".join(lines)


def format_competencies_for_prompt(competencies: list) -> str:
    """Format competencies for inclusion in prompt.

    Args:
        competencies: List of competency dictionaries

    Returns:
        Formatted string for prompt
    """
    if not competencies:
        return "No competencies available."

    lines = []
    for i, comp in enumerate(competencies, 1):
        lines.append(f"{i}. ID: {comp.get('id')}")
        lines.append(f"   Name: {comp.get('name')}")
        lines.append(f"   Description: {comp.get('description')}")
        lines.append(f"   States: {len(comp.get('states', []))}")
        lines.append(f"   Actions: {', '.join(comp.get('actions', []))}")
        lines.append("")

    return "\n".join(lines)


def format_history_for_prompt(history: list, max_length: int = 10) -> str:
    """Format conversation history for prompt.

    Args:
        history: List of message dictionaries
        max_length: Maximum number of messages to include

    Returns:
        Formatted string for prompt
    """
    if not history:
        return "No conversation history."

    recent = history[-max_length:] if len(history) > max_length else history
    lines = []

    for msg in recent:
        if 'human' in msg:
            lines.append(f"User: {msg['human']}")
        if 'ai' in msg:
            lines.append(f"Agent: {msg['ai']}")

    return "\n".join(lines) if lines else "No conversation history."
