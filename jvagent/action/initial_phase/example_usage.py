"""Example usage of InitialPhaseAction.

This example demonstrates how to set up and use the InitialPhaseAction
for event-driven agent input processing.
"""

import asyncio
from jvagent.action.initial_phase import (
    InitialPhaseAction,
    Parameter,
    Competency,
    Workflow,
    ExecutionRequirement,
)


async def main():
    """Example usage of InitialPhaseAction."""

    # 1. Create an InitialPhaseAction instance
    action = InitialPhaseAction(
        label="initial_phase",
        agent_name="MyAgent",
        agent_role="AI Assistant",
        agent_description="A helpful AI assistant",
        agent_capabilities=[
            "Answer questions",
            "Process subscriptions",
            "Provide information",
        ],
        # Model configuration
        model_action_type="OpenAIModelAction",
        model_name="gpt-4o",
        model_temperature=0.3,
        # Typesense configuration
        typesense_host="localhost",
        typesense_port=8108,
        typesense_protocol="http",
        typesense_api_key="xyz",
        # Vector search
        vector_search_top_k=10,
    )

    # 2. Add some parameters
    # Parameters define behavioral conditions and responses

    # Greeting parameter (always execute on first interaction)
    await action.add_parameter({
        "condition": "User sends a greeting like hello, hi, or hey",
        "response": "Respond warmly and introduce yourself",
        "execution_requirement": "on_first_interaction",
        "enabled": True,
    })

    # Subscription parameter (conditional)
    await action.add_parameter({
        "condition": "User wants to subscribe or manage subscriptions",
        "response": "Initiate subscription workflow",
        "action": "subscription_action",
        "workflow": "subscription_workflow",
        "execution_requirement": "conditional",
        "enabled": True,
    })

    # Help parameter (conditional)
    await action.add_parameter({
        "condition": "User asks for help or information",
        "response": "Provide helpful information about capabilities",
        "execution_requirement": "conditional",
        "enabled": True,
    })

    # 3. Add a competency (for complex multi-turn flows)
    await action.add_competency({
        "name": "Interview Flow",
        "description": "Multi-turn interview competency for collecting structured information",
        "states": [
            {"name": "collect_name", "question": "What's your name?"},
            {"name": "collect_email", "question": "What's your email?"},
        ],
        "actions": ["interview_action"],
        "execution_requirement": "conditional",
        "enabled": True,
    })

    # 4. Add a workflow
    await action.add_workflow({
        "name": "Subscription Workflow",
        "description": "Handle subscription requests",
        "steps": [
            {"action": "validate_subscription", "order": 1},
            {"action": "process_payment", "order": 2},
            {"action": "confirm_subscription", "order": 3},
        ],
        "enabled": True,
    })

    # 5. Process a user utterance
    print("\\n" + "="*60)
    print("Processing user utterance: 'Hello!'")
    print("="*60 + "\\n")

    result = await action.process(
        utterance="Hello!",
        channel="web",
    )

    # 6. Inspect the result
    print(f"User ID: {result.user_id}")
    print(f"Session ID: {result.session_id}")
    print(f"Processing Duration: {result.processing_duration:.3f}s\\n")

    # View the generated instructions
    instructions = result.instructions
    print("INSTRUCTIONS GENERATED:")
    print(f"  Simplified Intent: {instructions.simplified_intent}")
    print(f"  Applicable Parameters: {len(instructions.applicable_parameters)}")
    print(f"  Required Actions: {instructions.required_actions}")
    print(f"  Required Workflows: {instructions.required_workflows}")
    print(f"  Context: {instructions.context}")
    print()

    # View applicable parameters
    if instructions.applicable_parameters:
        print("APPLICABLE PARAMETERS:")
        for param in instructions.applicable_parameters:
            print(f"  - {param.get('condition')}")
            print(f"    Response: {param.get('response')}")
            if param.get('action'):
                print(f"    Action: {param.get('action')}")
        print()

    # View events
    events = result.event_bus.get_events()
    print(f"EVENTS EMITTED: {len(events)}")
    for event in events:
        print(f"  [{event.timestamp.strftime('%H:%M:%S.%f')[:-3]}] {event.event_type}")
        if event.data:
            for key, value in event.data.items():
                if key != 'utterance':  # Skip verbose data
                    print(f"      {key}: {value}")
    print()

    # 7. Process another utterance
    print("\\n" + "="*60)
    print("Processing user utterance: 'I want to subscribe'")
    print("="*60 + "\\n")

    result2 = await action.process(
        utterance="I want to subscribe",
        session_id=result.session_id,  # Continue same conversation
        channel="web",
    )

    instructions2 = result2.instructions
    print("INSTRUCTIONS GENERATED:")
    print(f"  Simplified Intent: {instructions2.simplified_intent}")
    print(f"  Applicable Parameters: {len(instructions2.applicable_parameters)}")
    print(f"  Required Actions: {instructions2.required_actions}")
    print(f"  Required Workflows: {instructions2.required_workflows}")
    print()

    # Export full result as JSON
    print("\\n" + "="*60)
    print("FULL JSON OUTPUT (for downstream processing):")
    print("="*60)
    import json
    print(json.dumps(result2.to_dict(), indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
