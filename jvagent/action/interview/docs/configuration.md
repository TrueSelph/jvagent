# Configuration

## Interaction-Only Design

The interview action operates through **InteractWalker** and conversation flow only. It has no REST/API endpoints—all configuration and overrides are applied via the action's context and attributes.

## Context-Only Overrides (No Config Block)

All overrides go under the action's **`context:`** block in `agent.yaml`. There is no separate `config:` block. The loader merges `context:` into the action's attributes, which the interview system consumes.

### agent.yaml Context Structure

```yaml
actions:
  - action: jvagent/my_interview_action
    context:
      enabled: true
      description: "My interview flow"
      weight: -50
      anchors: ["User wants to ..."]
      question_graph:
        - name: user_name
          question: "What's your full name?"
          constraints:
            description: "The user's full name"
            instructions: "Must include first and last name"
            type: "string"
          required: true
        - name: user_email
          question: "What is your email?"
          constraints:
            description: "The user's email address"
            type: "string"
            format: "email"
          required: true
      # Model overrides (model_ prefix for model-related keys)
      model_action_type: "OpenAILanguageModelAction"
      model: "gpt-4o-mini"
      model_temperature: 0.2
      model_max_tokens: 2048
      use_history: true
      max_statement_length: 400
      history_limit: 10
      # Template overrides
      completion_message: "Tell the user: All set! Your information has been saved."
      review_confirmation: |
        Here's a summary of your responses:
        {summary}

        {instructions}
        {prompt}
      # Classification overrides
      classification:
        context_list_compact_threshold: 10
        decline_value: "skipped"
        require_structured_reasoning: true
        include_few_shot_examples: true
        max_examples: 5
        enable_reference_resolution: true
        enable_composition: true
      # Auto-confirm
      auto_confirm: true  # Skip REVIEW confirmation prompt, go directly to COMPLETED
```

### Context Keys

| Key | Description |
|-----|-------------|
| `enabled` | Enable/disable the action |
| `description` | Action description |
| `weight` | Routing weight |
| `anchors` | Anchor statements for InteractRouter |
| `question_graph` | List of question configurations |
| `model_action_type` | Model action type (e.g., OpenAILanguageModelAction) |
| `model` | Model identifier (e.g., gpt-4o-mini) |
| `model_temperature` | Model temperature |
| `model_max_tokens` | Max tokens |
| `use_history` | Use conversation history in classification |
| `max_statement_length` | Max statement length |
| `history_limit` | History message limit |
| `completion_message` | Completion message template |
| `review_confirmation` | Review confirmation template |
| `classification` | Classification config (nested) |
| `auto_confirm` | Skip REVIEW confirmation when true |

## Accessing Attributes in Code

When extending the interview system, access attributes via `self`:

```python
class CustomInterviewAction(InterviewInteractAction):
    async def custom_method(self, session, visitor):
        # Access question graph
        questions = self.question_graph

        # Access templates (from config/context)
        templates = self.config.templates
        message = templates.completion_message

        # Format template
        directive = templates.question_directive.format(
            question="What is your name?",
            description="User's full name",
            context_section="",
            instructions="Please provide first and last name"
        )

        # Get state event message (COMPLETED, CANCELLED; ACTIVE/REVIEW use active tasks)
        event = templates.get_state_event_message("COMPLETED", self.get_class_name())
```

## Auto-Confirm Mode

Auto-confirm mode allows interviews to skip the REVIEW confirmation prompt and proceed directly to COMPLETED. This is useful for:
- Data collection flows where user confirmation is not required
- Automated data ingestion via REST APIs or file uploads
- Simplified user experiences where review is optional

### How It Works

When `auto_confirm` is set to `True` (via `context.auto_confirm`):

1. **Configuration**: Set `auto_confirm: true` in `context:` section of `agent.yaml`
2. **Session Creation**: New sessions inherit the `auto_confirm` flag from context
3. **ACTIVE State**: Questions are collected normally
4. **REVIEW State**: When all questions are answered and the walker visits the REVIEW node:
   - REVIEW performs the ACTIVE → REVIEW state transition
   - REVIEW returns `None` (no confirmation directive)
   - The Interview Walker follows the REVIEW → COMPLETED edge
   - COMPLETED node performs the REVIEW → COMPLETED transition
   - Completion handler runs and session is cleaned up
5. **User Experience**: The user never sees a confirmation prompt; the interview completes immediately after the last question

### Configuration Example

```yaml
actions:
  - action: jvagent/data_collection_interview
    context:
      enabled: true
      description: "Automated data collection flow"
      auto_confirm: true  # Skip REVIEW confirmation
      model: "gpt-4o-mini"
```

### State Transition Flow

**Without auto_confirm (default):**
```
ACTIVE → REVIEW (shows confirmation) → wait for user → COMPLETED
```

**With auto_confirm:**
```
ACTIVE → REVIEW (no directive) → COMPLETED (automatic)
```

### Important Notes

- **Existing sessions**: Sessions created before enabling `auto_confirm` default to `False` (normal confirmation behavior)
- **Graph edge**: The REVIEW → COMPLETED edge is always created in the question graph, regardless of `auto_confirm` setting. When `auto_confirm` is `False`, the edge is inert (unused).
- **Completion handlers**: `@on_interview_complete` handlers still run normally when auto_confirm is enabled
- **No user input**: With auto_confirm, the session never waits in REVIEW state for user input
