# Supervisor Short Pass Interview InteractAction

Structured interview action for supervisors to review and approve or deny short pass requests in the ARC AI System.

## Overview

The Supervisor Short Pass Interview InteractAction guides supervisors through the process of reviewing a rank's short pass request. It allows supervisors to provide a decision (approval or denial) and feedback, which is then recorded via the ARC API.

## Features

- **Reference Number Extraction**: Automatically detects short pass reference numbers from quoted messages
- **Decision Tracking**: Captures and validates approval or denial decisions
- **Feedback Collection**: Ensures supervisors provide remarks for their decision
- **API Integration**: Updates the short pass status in the ARC AI System
- **Custom Validation**: Field-level validation with user-friendly error messages
- **Custom Directives**: Provides context-aware guidance for supervisors

## Architecture

Inherits from `InterviewInteractAction` and uses a unified classification and extraction approach that detects user intent (CANCELLATION, CONFIRMATION, UPDATE, SUBMISSION, NONE) and extracts field values in a single LLM call.

**Session Management**: Sessions are identified by `interview_type='SupervisorShortPassInterviewInteractAction'` and attached to Conversation nodes for per-user persistence.

## Configuration

### Agent Configuration (agent.yaml)

```yaml
- action: gdf/supervisor_short_pass_interview_interact_action
  context:
    enabled: true
    description: "Supervisor Short Pass Interview action is used to review and approve short pass requests."
    weight: -50 # Runs before fallback actions
  config:
    model_action_type: "OpenAILanguageModelAction"
    model: "gpt-4.1"
    model_temperature: 0.1
    model_max_tokens: 4096
    use_history: true
    max_statement_length: 100
    history_limit: 3
```

### Routing Anchors

The action publishes anchors for InteractRouter routing:

- "Supervisor wants to review a short pass request"
- "Supervisor is approving or denying a short pass"
- "Supervisor is providing remarks on a short pass"

## Interview Flow

### Question Graph

1. **short_pass_reference_number** (required)
   - The reference ID for the short pass being reviewed
   - Automatically skipped if the number is found in a quoted message
   - Validation: Verifies the ID exists in the ARC AI System

2. **approval_status** (required)
   - The supervisor's decision (either 'approved' or 'denied')
   - Validation: Must be one of the two allowed values

3. **supervisor_feedback** (required)
   - Remarks or feedback explaining the decision
   - Minimum 5 characters
   - Validation: Ensures remarks are provided with sufficient detail

## Custom Components

### Validators

- `validate_short_pass_reference_number`: Verifies the ID exists in the ARC AI System and fetches details
- `validate_approval_status`: Ensures the decision is clear ('approved' or 'denied')
- `validate_supervisor_feedback`: Ensures remarks are provided with sufficient detail (min 5 chars)

### Branch Functions

- `skip_ref_if_known`: Extracts the reference number from the conversation context or quoted messages to streamline the interaction

### Review Override

- `adapt_review`: Formats the summary data for the Review state, ensuring the reference number is displayed and omitting empty fields

## Completion Handler

The `handle_interview_completion` function processes the supervisor's input:

1. Retrieves the final decision and feedback
2. Calls ArcAPIAction to update the short pass status
3. Sends a confirmation message to the supervisor
4. Cleans up the interview session

## API Integration

Integrates with `ArcAPIAction` for short pass lookup and status updates:

```python
result = await arc_api_action.update_short_pass(
    reference_number=short_pass_reference_number,
    status=approval_status,
    comments=comments
)
```

## Usage

### Starting the Interview

Supervisors can trigger the review by responding to a short pass notification or using matching utterances:

- "I want to approve short pass 95"
- "Deny the request for rank 15264"
- "Reviewing the short pass request now"

### Example Interaction

```
User: I want to review short pass 95
Agent: Do you approve or deny this short pass request?

User: Approved
Agent: Please provide your feedback or remarks regarding this decision.

User: All clear for travel
Agent: [Review summary] Is this information correct?

User: Yes
Agent: Your decision to approved the short pass request (95) has been processed successfully. The rank will be notified.
```

## Validation Rules

- **Approval Status**: Must be exactly 'approved' or 'denied'
- **Feedback**: Must be at least 5 characters long
- **Reference Number**: Must exist in the ARC AI System

## Dependencies

- `jvagent/openai_lm` - Language model integration.
- `gdf/arc_api_action` - API integration for short pass management.

## File Structure

```
supervisor_short_pass_interview_interact_action/
├── __init__.py                                        # Package initialization
├── supervisor_short_pass_interview_interact_action.py # Main action implementation
├── info.yaml                                          # Action metadata
└── README.md                                          # This file
```

## Customization

### Adding New Questions

Add a new configuration to the `question_graph` attribute:

```python
{
    "name": "new_field",
    "question": "Your question here?",
    "constraints": {
        "description": "Field description",
        "type": "string"
    },
    "required": True
}
```

### Adding Custom Validators

Use the `@input_validator` decorator on a class method:

```python
@input_validator('field_name')
def validate_field(value: str, session: InterviewSession) -> Tuple[ValidationStatus, Optional[str]]:
    if not value:
        return ValidationStatus.INVALID, "Ask: Please provide a value."
    return ValidationStatus.VALID, None
```

## Testing

Test scenarios:

- Direct approval of a short pass request
- Denial of a request with specific feedback
- Auto-extraction of reference numbers from quoted messages
- Validation for empty feedback or invalid approval status
- Review and correction of entered data
- Session cleanup after submission
