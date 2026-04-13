# Examples

## Example 1: Basic Registration Interview

```python
from jvagent.action.interview import InterviewInteractAction
from jvspatial.core.annotations import attribute
from typing import Any, Dict, List

class RegistrationInterviewAction(InterviewInteractAction):
    """User registration interview with default state behavior.

    Sessions are identified by interview_type='RegistrationInterviewAction'
    and attached to Conversation nodes for per-user persistence.

    Note: question_graph can also be defined in agent.yaml context to override this.
    """

    description: str = "User registration interview flow"

    anchors: List[str] = attribute(
        default_factory=lambda: [
            "User wants to register",
            "User requests registration",
            "User asks to sign up",
            "User is providing registration information",
            "User is answering registration questions",
        ],
        description="Anchor statements for InteractRouter routing. Standard interview anchors are automatically included."
    )

    question_graph: List[Dict[str, Any]] = attribute(
        default_factory=lambda: [
            {
                "name": "user_name",
                "question": "What's your full name?",
                "constraints": {
                    "description": "The user's full name",
                    "instructions": "Must include first and last name",
                    "type": "string",
                },
                "required": True
            },
            {
                "name": "user_email",
                "question": "What is your email?",
                "constraints": {
                    "description": "The user's email address",
                    "type": "string",
                    "format": "email"
                },
                "required": True
            },
        ],
        description="List of question configurations. Can be overridden in agent.yaml context"
    )
```

## Example 2: Onboarding with Custom Completion Handler

```python
from jvagent.action.interview import (
    InterviewInteractAction,
    on_interview_complete,
)
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.interact.base import InteractAction
from jvspatial.core.annotations import attribute
from typing import Any, Dict, List
import logging

logger = logging.getLogger(__name__)


@on_interview_complete('OnboardingInterviewAction')
async def handle_onboarding_completion(
    session: InterviewSession,
    visitor: InteractWalker,
    action: InteractAction
) -> None:
    """Process onboarding data when interview completes."""
    data = session.extract_data()

    conversation = await visitor.interaction.get_conversation()
    user = await conversation.get_user()

    if user:
        user.preferences = {
            "communication_preference": data["responses"].get("comm_pref"),
            "interests": data["responses"].get("interests"),
            "timezone": data["responses"].get("timezone"),
        }
        await user.save()
        logger.info(f"Onboarding data saved to user profile: {user.id}")

    await action.respond(visitor, directives=["Welcome aboard! Your preferences have been saved."])
    await session.cleanup()


class OnboardingInterviewAction(InterviewInteractAction):
    """Onboarding interview with custom completion handling.

    Sessions identified by interview_type='OnboardingInterviewAction'.
    Uses @on_interview_complete decorator to process data on completion.
    """

    description: str = "User onboarding interview flow"

    question_graph: List[Dict[str, Any]] = attribute(
        default_factory=lambda: [
            {
                "name": "comm_pref",
                "question": "How would you like to receive updates?",
                "constraints": {
                    "description": "Communication preference (email, SMS, etc.)",
                    "type": "string",
                },
                "required": True
            },
            {
                "name": "interests",
                "question": "What topics interest you?",
                "constraints": {
                    "description": "User interests",
                    "type": "string",
                },
                "required": False
            },
            {
                "name": "timezone",
                "question": "What's your timezone?",
                "constraints": {
                    "description": "User timezone",
                    "type": "string",
                },
                "required": True
            },
        ],
        description="List of question configurations. Can be overridden in agent.yaml context"
    )
```

## Example 3: Appointment Booking with Separate Data Handler

```python
from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.interview import InterviewInteractAction
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interview.core.foundation.enums import InterviewState
from jvspatial.core.annotations import attribute
import logging

logger = logging.getLogger(__name__)


class AppointmentDataHandlerAction(InteractAction):
    """Separate action to process appointment data after interview.

    This demonstrates Pattern B: handling interview data in a separate
    InteractAction that runs after the interview completes.
    """

    description: str = "Process appointment booking data from completed interviews"
    weight: int = -30  # Runs after interview actions

    async def execute(self, visitor: InteractWalker) -> None:
        """Process completed appointment interview sessions."""
        conversation = await visitor.interaction.get_conversation()
        if not conversation:
            return

        session = await conversation.node(
            node=InterviewSession,
            interview_type="AppointmentInterviewAction",
            state=InterviewState.COMPLETED,
        )

        if session:
            if session.context.get("processed"):
                return

            data = session.extract_data()
            await self.create_appointment(data["responses"])

            session.context["processed"] = True
            await session.save()

            logger.info(f"Processed appointment from session {session.id}")

    async def create_appointment(self, responses: dict) -> None:
        """Create appointment from interview data."""
        appointment_time = responses.get("preferred_time")
        service_type = responses.get("service_type")
        contact_info = responses.get("contact_email")

        logger.info(f"Creating appointment: {service_type} at {appointment_time}")


class AppointmentInterviewAction(InterviewInteractAction):
    """Appointment booking interview.

    Data is processed by separate AppointmentDataHandlerAction.
    Sessions identified by interview_type='AppointmentInterviewAction'.
    """

    description: str = "Appointment booking interview flow"

    question_graph: List[Dict[str, Any]] = attribute(
        default_factory=lambda: [
            {
                "name": "service_type",
                "question": "What service would you like to book?",
                "constraints": {
                    "description": "Type of service",
                    "type": "string",
                },
                "required": True
            },
            {
                "name": "preferred_time",
                "question": "What time works best for you?",
                "constraints": {
                    "description": "Preferred appointment time",
                    "type": "string",
                },
                "required": True
            },
            {
                "name": "contact_email",
                "question": "What's your email for confirmation?",
                "constraints": {
                    "description": "Contact email",
                    "type": "string",
                    "format": "email"
                },
                "required": True
            },
        ],
        description="List of question configurations. Can be overridden in agent.yaml context"
    )
```

## Example 4: Signup Interview (Production Example)

See `examples/jvagent_app/agents/jvagent/example_agent/actions/jvagent/signup_interview_interact_action/` for a production example that replaces the original hardcoded questions.
