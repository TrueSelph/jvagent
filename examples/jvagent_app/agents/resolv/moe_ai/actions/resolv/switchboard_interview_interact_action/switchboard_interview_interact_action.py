"""Switchboard interview action for agent selection."""
from __future__ import annotations

# Standard library
from typing import Any, Dict, List, Optional, Tuple

# Third-party / external packages
from jvspatial.core.annotations import attribute

# Local application imports
from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.interview import (
    InterviewInteractAction,
    input_context_provider,
    input_validator,
    on_interview_complete,
    on_interview_cancelled,
)
from jvagent.action.interview.core.foundation.enums import ValidationStatus
from jvagent.action.interview.core.session.interview_session import InterviewSession



class SwitchboardInterviewInteractAction(InterviewInteractAction):
    """This action allows the user to choose the agent they want to interact with.

    This action allows users to choose which agent they want to interact with
    through a structured interview flow. It presents available agents and
    validates the user's selection before routing them to the chosen agent.

    This is a concrete implementation of InterviewInteractAction that defines
    a specific interview flow. Sessions are identified by
    interview_type='SwitchboardInterviewInteractAction' and attached to Conversation nodes.

    The question_graph can be overridden in agent.yaml to customize questions.

    Architecture:
        The interview system uses a unified classification and extraction approach
        that detects user intent (CANCELLATION, CONFIRMATION, UPDATE, SUBMISSION, NONE)
        and extracts field values in a single LLM call. All state management and
        directive generation is handled within the main InterviewInteractAction.

    Model Configuration:
        Model settings (model_action_type, model, model_temperature, model_max_tokens,
        use_history, max_statement_length, history_limit) are inherited from
        InterviewInteractAction and can be configured in agent.yaml. These settings
        control the unified classification/extraction LLM call and all directive
        generation prompts.
    """

    description: str = (
        "Interview action that guides users through selecting an agent to interact with"
    )

    # REQUIRED when using InteractRouter: Anchors for intelligent routing
    # Must cover both initial entry and intermediate states (when answering questions)
    anchors: List[str] = attribute(
        default_factory=lambda: [
            "User requests to switch to a different agent by name",
            "User asks to connect to a specific agent",
            "User requests to disconnect from the current agent",
            "User asks to change department or location",
            "User explicitly mentions switching, connecting, or disconnecting from an agent or department",
        ],
        description="Anchor statements for InteractRouter routing",
    )

    _standard_interview_anchor_templates: List[str] = []

    question_graph: List[Dict[str, Any]] = attribute(
        default_factory=lambda: [
            {
                "name": "selected_agent",
                "question": "Please select an agent you wish to be routed to",
                "input_context_provider": "get_switchboard_agents",  # Dynamic context from decorator
                "constraints": {
                    "description": "Select the correct agent the user wishes to route to",
                    "instruction": "IF user intent is to disconnect, leave, remove, exit, or stop interacting, THEN classify as CANCELLATION",
                    "type": "string",
                },
                "required": True
            }
        ],
        description="List of question configurations defining the interview graph. Can be overridden in agent.yaml. "
                    "Supports conditional branching via 'branches' and 'default_next'. "
                    "Enhanced condition operators are supported (==, !=, >, >=, <, <=, in, contains, exists, matches). "
                    "Example: {\"condition\": {\"question\": \"age\", \"operator\": \">=\", \"value\": 18}, \"target\": \"next_question\"} "
                    "Handlers, validators, and directive overrides can be registered via decorators "
                    "(@input_handler, @input_validator, @input_directive_override) or specified as string "
                    "references in constraints (input_handler, input_validator)."
    )

    # Input validator
    @input_validator("selected_agent")
    async def validate_selected_agent(
        value: str,
        session: InterviewSession,
        visitor: Optional[InteractWalker] = None,
        interview_action: Optional[Any] = None,
    ) -> Tuple[ValidationStatus, Optional[str]]:
        """Validate that the selected agent is valid.
        
        Args:
            value: The agent alias selected by the user
            session: Interview session containing context
            visitor: Walker for accessing graph context
            interview_action: Interview action instance
            
        Returns:
            Tuple of (ValidationStatus, Optional error message)
        """
        if not value or not isinstance(value, str):
            return ValidationStatus.INVALID, "Ask: Please select an agent you wish to be routed to."

        return ValidationStatus.VALID, None


    @input_context_provider("get_switchboard_agents")
    async def get_switchboard_agents(
        session: InterviewSession,
        visitor: Optional[InteractWalker] = None,
        interview_action: Optional[InteractAction] = None
    ) -> Dict[str, Any]:
        """Provide available switchboard agents dynamically for interview context.

        Args:
            session: Interview session for context storage
            visitor: Walker for accessing graph context
            interview_action: Interview action instance for accessing other actions

        Returns:
            Dictionary with 'agents' key containing comma-separated agent aliases
        """
        conversation = visitor.conversation
        conversation.context["switchboard_agent"] = {}
        
        switchboard_action = await interview_action.get_action("SwitchboardInteractAction")
        agents = await switchboard_action.get_switchboard_agents()
        agents_str = ", ".join(agent["alias"] for agent in agents)
        return {"agents": agents_str}



@on_interview_cancelled('SwitchboardInterviewInteractAction')
async def handle_interview_cancellation(
    session: InterviewSession,
    visitor: InteractWalker,
    interview_action: InteractAction
) -> None:
    """Handle cancellation of switchboard interview.
    
    Clears the switchboard_agent context and notifies the user that
    they are not connected to any agent.
    
    Args:
        session: Interview session being cancelled
        visitor: Walker for accessing conversation context
        interview_action: Interview action instance for responding
    """
    conversation = visitor.conversation
    conversation.context["switchboard_agent"] = {}
    
    completion_message = "Politely respond to the user and also let them know they are not connected to any agent."
    await visitor.add_directives([completion_message])
    await interview_action.respond(visitor)
    await session.cleanup()


@on_interview_complete('SwitchboardInterviewInteractAction')
async def handle_interview_completion(
    session: InterviewSession,
    visitor: InteractWalker,
    action: InteractAction
) -> None:
    """Handle completion of switchboard interview.
    
    Stores the selected agent in conversation context and notifies the user
    that they are now connected to the chosen agent.
    
    Args:
        session: Completed interview session
        visitor: Walker for accessing conversation context
        action: Interview action instance for responding
    """
    switchboard_action = await action.get_action("SwitchboardInteractAction")
    agents = await switchboard_action.get_switchboard_agents()

    # Find the selected agent by matching alias
    selected_agent = {}
    for agent in agents:
        if agent['alias'] == session.responses.get('selected_agent', ''):
            selected_agent = agent
            break
    
    # Store selected agent in conversation context
    if selected_agent:
        conversation = visitor.conversation
        conversation.context["switchboard_agent"] = selected_agent

    # Notify user of successful connection
    if selected_agent:
        completion_message = f"Tell the user: You're now connected to {selected_agent.get('alias')}. Say hi to get started."
        await visitor.add_directives([completion_message])
        await action.respond(visitor)

    await session.cleanup()


