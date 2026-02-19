# Short Pass Interview InteractAction

Structured interview action for ranks to request short passes in the ARC AI System.

## Overview

The Short Pass Interview InteractAction guides users through the process of requesting a short pass. It handles different types of passes (Traditional, Overseas, and Confinement) and integrates with the ARC API for submission.

## Features

- **Dynamic Pass Categorization**: Automatically determines pass type (Traditional, Overseas, or Confinement) based on user responses
- **Supervisor Auto-Lookup**: Attempts to retrieve supervisor details from the rank's profile
- **Conditional Branching**: Adjusts questions based on travel plans and confinement status
- **Media Attachment Support**: Accepts photos and videos as evidence
- **Custom Validation**: Ensures dates, contact numbers, and addresses are properly formatted
- **Custom Directives**: Provides context-aware guidance throughout the interview

## Architecture

Inherits from `InterviewInteractAction` and uses a unified classification and extraction approach that detects user intent (CANCELLATION, CONFIRMATION, UPDATE, SUBMISSION, NONE) and extracts field values in a single LLM call.

**Session Management**: Sessions are identified by `interview_type='ShortPassInterviewInteractAction'` and attached to Conversation nodes for per-user persistence.

## Configuration

### Agent Configuration (agent.yaml)

```yaml
- action: gdf/short_pass_interview_interact_action
  context:
    enabled: true
    description: "Short Pass Interview action is used to request short passes."
    weight: -50 # Runs before fallback actions
  config:
    model_action_type: "OpenAILanguageModelAction"
    model: "gpt-4.1"
    model_temperature: 0.1
    model_max_tokens: 8192
    use_history: true
    max_statement_length: 100
    history_limit: 3
```

### Routing Anchors

The action publishes anchors for InteractRouter routing:

- "User is requesting a short pass"
- "User needs to file a new short pass request"
- "User is providing details for a new short pass request"

## Interview Flow

### Question Graph

1. **start_date** (required)
   - Proposed start date of the short pass
   - Format: '%A, %B %d, %Y'
   - Validation: Ensures a non-empty string

2. **end_date** (optional)
   - Proposed end date of the short pass
   - Format: '%A, %B %d, %Y'
   - Validation: Ensures a non-empty string

3. **overseas_travel** (required)
   - Indicates if traveling overseas
   - Options: yes/no
   - Branches to `overseas_address` if "yes", otherwise to `under_confinement`

4. **overseas_address** (conditional)
   - Full address for overseas travel
   - Minimum 5 words
   - Required if `overseas_travel` is "yes"

5. **overseas_contact_number** (conditional)
   - Contact number for overseas travel
   - Must be exactly 10 digits
   - Required if `overseas_travel` is "yes"

6. **under_confinement** (required)
   - Indicates if currently under base confinement
   - Options: yes/no

7. **reason_for_pass** (required)
   - Comprehensive reason for the request
   - Minimum 3 words
   - Branches to `supervisor_name` if supervisor details are missing from profile

8. **supervisor_name** (conditional)
   - Name of the rank's supervisor
   - Must include first and last name

9. **supervisor_phone_number** (conditional)
   - Contact number of the supervisor
   - Must be exactly 10 digits

## Custom Components

### Validators

- `validate_start_date` / `validate_end_date`: Checks for valid date strings
- `validate_overseas_travel`: Ensures "yes" or "no" input
- `validate_overseas_address`: Requires a detailed address (min 5 words)
- `validate_overseas_contact_number`: Validates 10-digit phone number format
- `validate_under_confinement`: Ensures "yes" or "no" input
- `validate_reason_for_pass`: Ensures sufficient detail (min 3 words)
- `validate_supervisor_name`: Ensures full name is provided (first and last)
- `validate_supervisor_contact_number`: Validates 10-digit phone number format

### Context Providers

- `get_current_date`: Provides the current date for resolving relative date mentions (e.g., "next Monday")

### Branch Functions

- `can_ask_for_supervisor_name`: Checks rank profile to see if supervisor info is already known

### Review Override

- `adapt_review`: Formats the summary data for the Review state, omitting empty fields and appending supervisor details

## Completion Handler

The `handle_interview_completion` function processes the collected data:

1. Determines the final `pass_type` (Traditional, Overseas, or Confinement)
2. Formats all data for the final submission
3. Calls ArcAPIAction to submit the request
4. Generates a reference number for tracking
5. Sends a confirmation message to the user
6. Cleans up the interview session

## API Integration

Integrates with `ArcAPIAction` for submission and profile lookup:

```python
result = await arc_api_action.submit_short_pass(
    start_date=start_date,
    end_date=end_date,
    pass_type=pass_type,
    reason=reason_for_pass,
    overseas_address=overseas_address,
    overseas_contact=overseas_contact_number,
    supervisor_name=supervisor_name,
    supervisor_phone=supervisor_phone_number
)
```

## Usage

### Starting the Interview

User utterances matching the anchors will trigger the interview:

- "I need to apply for a short pass"
- "Can I request a pass for this weekend?"
- "I'm going overseas next week and need a short pass"

### Example Interaction

```
User: I want to request a short pass
Agent: What is the proposed start date of your short pass?

User: Next Friday
Agent: What's the proposed end date of your short pass?

User: Sunday
Agent: Will you be traveling overseas during this short pass?

User: No
Agent: Are you currently under base confinement?

User: No
Agent: What's the reason you're requesting the short pass?

User: Family emergency
Agent: [Review summary] Is this information correct?

User: Yes
Agent: Thank you for your short pass submission! Your reference number for follow-up is: 95.
```

## Validation Rules

- **Dates**: Must be valid parsable date strings
- **Contact Numbers**: Must be exactly 10 digits
- **Overseas Address**: Must be comprehensive (at least 5 parts)
- **Binary Choices**: Must be exactly "yes" or "no"
- **Reason**: Must have sufficient detail (minimum 3 words)

## Dependencies

- `jvagent/openai_lm` - Language model integration.
- `gdf/arc_api_action` - API integration for short pass submission.

## File Structure

```
short_pass_interview_interact_action/
├── __init__.py                                # Package initialization
├── short_pass_interview_interact_action.py # Main action implementation
├── info.yaml                              # Action metadata
└── README.md                              # This file
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

- Traditional pass request (local, not confined)
- Overseas pass request (requires address and contact)
- Confinement status check
- Validation for invalid phone numbers or short addresses
- Multi-turn corrections during the Review state
- Supervisor auto-lookup from rank profile
- End-to-end review and confirmation process
