# Session and State

## Session Management

### Per-User Isolation

Sessions are attached to Conversation nodes, ensuring each user has their own sessions:

```python
# Session is created and attached to conversation
session = await InterviewSession.create(
    agent_id=self.agent_id,
    conversation_id=conversation.id,
    interview_type=self.get_class_name(),  # e.g., "RegistrationInterviewAction"
    question_graph=self.question_graph,
    state=InterviewState.ACTIVE,
)
await conversation.connect(session)
```

### Type-Based Loading

Sessions are queried by `interview_type` to support multiple interviews per agent:

```python
session = await conversation.node(
    node=[{'InterviewSession': {
        "state": {"$nin": [InterviewState.COMPLETED.value, InterviewState.CANCELLED.value]}
    }}],
    interview_type="RegistrationInterviewAction",
)
```

### Action Rebuild Resilience

Sessions store `interview_type` as metadata, so they persist even when interview actions are destroyed and rebuilt during agent reconfiguration.

## Session Lifecycle

### Reset Session

```python
await session.reset()  # Clears responses, resets to ACTIVE
```

### Cleanup Session

```python
await session.cleanup()  # Deletes session from graph
```

### Extract Data

```python
data = session.extract_data()  # Returns dict with responses and metadata
```

## Completion Handling

**Use `@on_interview_complete` Decorator**

Register a completion handler using the decorator:

```python
from jvagent.action.interview import (
    InterviewInteractAction,
    on_interview_complete,
)
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.interact.base import InteractAction

@on_interview_complete('InterviewActionName')
async def handle_completion(
    session: InterviewSession,
    visitor: InteractWalker,
    action: InteractAction
) -> None:
    """Process collected data when interview completes."""
    data = session.extract_data()
    # Process data, send notifications, etc.
    await action.respond(visitor, directives=["Thank you! Your data has been processed."])
    # Clean up session after processing
    await session.cleanup()
```

The completion handler is called automatically when the interview transitions to COMPLETED state.

## Cancellation Handling

**Use `@on_interview_cancelled` Decorator**

Register a cancellation handler to process events when an interview is cancelled:

```python
from jvagent.action.interview import (
    InterviewInteractAction,
    on_interview_cancelled,
)
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.interact.base import InteractAction

@on_interview_cancelled('InterviewActionName')
async def handle_cancellation(
    session: InterviewSession,
    visitor: InteractWalker,
    action: InteractAction
) -> None:
    """Handle interview cancellation."""
    # Log cancellation for analytics
    logger.info(f"Interview cancelled. Partial responses: {session.responses}")
    # Clean up any temporary resources
    await cleanup_temp_resources(session)
    # Send custom cancellation acknowledgment
    await action.respond(visitor, directives=["No problem! Feel free to start again anytime."])
```

The cancellation handler is called automatically when the interview transitions to CANCELLED state (when the user explicitly cancels).

## Review Handling

**Use `@on_interview_review` Decorator**

Register a review handler to customize the review experience before the user confirms their responses:

```python
from jvagent.action.interview import (
    InterviewInteractAction,
    on_interview_review,
)
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.interact.base import InteractAction

@on_interview_review('InterviewActionName')
async def handle_review(
    session: InterviewSession,
    visitor: InteractWalker,
    action: InteractAction
) -> Optional[str]:
    """Customize review summary with a custom prefix."""
    user_name = session.responses.get('user_name', 'there')
    return f"Great job, {user_name}! Let's review your information:"
```

The review handler is called automatically when the interview transitions to REVIEW state, before the summary is shown. It can optionally return a custom directive string to prepend to the review summary. If no string is returned (or handler is not registered), the default review summary is shown.

**Handler Return Values:**
- `str`: Custom message to prepend to the review summary
- `None`: Use default review summary

## Data Handling Patterns

### Pattern A: Use Completion Handler Decorator (Recommended)

Use the `@on_interview_complete` decorator to register a completion handler:

```python
from jvagent.action.interview import (
    InterviewInteractAction,
    on_interview_complete,
)
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interact.interact_walker import InteractWalker

@on_interview_complete('MyInterviewAction')
async def handle_interview_completion(
    session: InterviewSession,
    visitor: InteractWalker
) -> None:
    """Process data when interview completes."""
    data = session.extract_data()
    # Store in user profile, database, etc.
    user = await visitor.interaction.get_conversation().get_user()
    user.preferences = data["responses"]
    await user.save()

class MyInterviewAction(InterviewInteractAction):
    question_graph = [...]
```

**Note:** Completion handlers are the recommended approach. The system automatically calls registered handlers when the interview transitions to COMPLETED state.

### Pattern B: Separate Data Handler Action

Create a separate `InteractAction` that processes completed sessions:

```python
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interview.core.foundation.enums import InterviewState

class AppointmentDataHandlerAction(InteractAction):
    weight: int = -30  # Runs after interview

    async def execute(self, visitor: InteractWalker) -> None:
        conversation = await visitor.interaction.get_conversation()
        session = await conversation.node(
            node=InterviewSession,
            interview_type="AppointmentInterviewAction",
            state=InterviewState.COMPLETED,
        )
        if session and not session.context.get("processed"):
            data = session.extract_data()
            await self.create_appointment(data["responses"])
            session.context["processed"] = True
            await session.save()
```

### Pattern C: Callback in Interview Action

Override `on_interview_complete()` in your interview action (if implemented).

## Question Node Rebuilding

When `question_graph` changes (via `on_reload()`), question nodes are automatically rebuilt:

1. Detects changes by comparing existing node labels with expected labels
2. Disconnects and deletes old question nodes
3. Rebuilds question node chain from new `question_graph`

## Multiple Interviews Per Agent

You can run multiple interview types in the same agent:

```yaml
actions:
  - type: RegistrationInterviewAction
    enabled: true
  - type: OnboardingInterviewAction
    enabled: true
  - type: AppointmentInterviewAction
    enabled: true
```

Each maintains its own sessions via `interview_type` identification.
