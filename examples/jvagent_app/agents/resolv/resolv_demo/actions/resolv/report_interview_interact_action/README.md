# Report Interview InteractAction

Structured interview action for creating incident reports in the Resolv Incident Management System.

## Overview

The Report Interview InteractAction guides users through a multi-step interview process to collect comprehensive incident information. It features conditional branching, validation, media attachment support, and privacy protection for sensitive reports.

## Features

- **Multi-step Interview Flow**: Collects incident details, location, media, and reporter information
- **Conditional Branching**: Dynamic flow based on user responses (sensitive content, reporting on behalf)
- **Similar Report Detection**: Checks for existing reports at the same location
- **Media Attachment Support**: Accepts photos and videos as evidence
- **Privacy Protection**: Sensitive reports can be marked as anonymous
- **Custom Validation**: Field-level validation with user-friendly error messages
- **Custom Directives**: Context-aware guidance throughout the interview
- **DSPy Integration**: Optimized classification and extraction using DSPy teleprompters

## Architecture

Inherits from `InterviewInteractAction` and uses a unified classification and extraction approach that detects user intent (CANCELLATION, CONFIRMATION, UPDATE, SUBMISSION, NONE) and extracts field values in a single LLM call.

**Session Management**: Sessions are identified by `interview_type='ReportInterviewInteractAction'` and attached to Conversation nodes for per-user persistence.

## Configuration

### Agent Configuration (agent.yaml)

```yaml
- action: resolv/report_interview_interact_action
  context:
    enabled: true
    description: "Report Interview action is used to create reports."
    weight: -50  # Runs before fallback actions
  config:
    model_action_type: "OpenAILanguageModelAction"
    model: "gpt-4.1-mini"
    model_temperature: 0.1
    model_max_tokens: 4096
    use_history: true
    max_statement_length: 100
    history_limit: 3
    use_dspy: true
```

### Routing Anchors

The action publishes anchors for InteractRouter routing:
- "User is reporting a new problem, hazard, or safety issue"
- "User needs to file a new complaint or incident report"
- "User is providing details for a new incident report"
- "User is uploading photos or evidence for a new incident report"
- "User is revising, canceling, updating or confirming an active incident report being created"

## Interview Flow

### Question Graph

1. **incident_description** (required)
   - Detailed description of the incident
   - Minimum 10 characters
   - Validation: Must include sufficient detail

2. **incident_location** (required)
   - Exact location where incident occurred
   - Minimum 10 characters
   - Triggers similar report check
   - Validation: Must be specific, not vague

3. **continue_report** (conditional)
   - Shown if similar reports found
   - Options: yes/no
   - Branches to CANCELLED if "no"

4. **incident_media** (optional)
   - Photos or videos of the incident
   - Accepts multiple files
   - Triggers sensitive content detection

5. **is_sensitive** (conditional)
   - Shown if sensitive content detected
   - Options: yes/no
   - Marks report as anonymous if "yes"

6. **reporting_on_behalf** (required)
   - Whether reporting for someone else
   - Options: yes/no
   - Branches to stakeholder questions if "yes"

7. **stakeholder_name** (conditional)
   - Full name of person being reported for
   - Validation: First and last name required

8. **stakeholder_address** (conditional)
   - Residential address of stakeholder
   - Minimum 10 characters

9. **stakeholder_phone** (conditional)
   - Contact number of stakeholder
   - Validation: 10-digit phone number

10. **reporter_name** (required)
    - Full name of person submitting report
    - Validation: Cannot match stakeholder name
    - Must include first and last name

11. **reporter_address** (required)
    - Residential address of reporter
    - Validation: Cannot match incident location or stakeholder address
    - Minimum 10 characters

## Custom Components

### Validators

- `validate_incident_description`: Ensures sufficient detail (min 10 chars)
- `validate_incident_location`: Validates specific location (min 10 chars)
- `validate_is_sensitive`: Validates yes/no response
- `validate_reporting_on_behalf`: Validates yes/no response
- `validate_stakeholder_name`: Validates full name format
- `validate_stakeholder_address`: Validates address length
- `validate_stakeholder_phone`: Validates 10-digit phone format
- `validate_reporter_name`: Validates full name, checks against stakeholder
- `validate_reporter_address`: Validates address, checks against incident location

### Directive Overrides

- `custom_location_directive`: Shows matching reports after location is provided
- `custom_continue_directive`: Provides cancellation confirmation message

### Branch Functions

- `detect_sensitive_content`: Checks for sensitive keywords or media presence
- `check_for_similar_incidents`: Queries for existing reports at same location

### Review Override

- `adapt_feedback_review_for_display`: Formats data for review state, omits empty fields

## Completion Handler

The `handle_report_completion` function processes the collected data:

1. Extracts all interview responses
2. Determines if report should be anonymous
3. Generates report metadata (title, priority, category)
4. Calls ResolvAPIAction to submit report
5. Sends confirmation message to user
6. Cleans up interview session

## API Integration

Integrates with `ResolvAPIAction` to submit reports to the Resolv IMS:

```python
result = await resolv_api_action.submit_report(
    title=title,
    is_anonymous=is_anonymous,
    description=generated_description,
    original_description=incident_description,
    attachments=incident_media,
    priority=priority,
    category_id=category_id,
    reporting_on_behalf=reporting_on_behalf,
    stakeholder_name=stakeholder_name,
    stakeholder_address=stakeholder_address,
    stakeholder_phone=stakeholder_phone,
    reporter_name=reporter_name,
    reporter_phone=reporter_phone,
    reporter_address=reporter_address,
    ai_overview=ai_overview
)
```

## Usage

### Starting the Interview

User utterances matching the anchors will trigger the interview:
- "I want to report a pothole on Main Street"
- "There's a safety hazard at the construction site"
- "I need to file a complaint about noise"

### Example Interaction

```
User: I want to report a pothole on Main Street
Agent: Please describe the incident you want to report...

User: There's a large pothole causing damage to vehicles
Agent: Where exactly did this incident occur?

User: Main Street near the traffic light
Agent: I found 2 reports that match your description...

User: Yes, continue
Agent: Do you have any photos or videos?

User: [uploads photo]
Agent: Are you submitting this report on behalf of someone else?

User: No
Agent: What is the full name of the person submitting the report?

User: John Smith
Agent: What is your residential address?

User: 123 Oak Street
Agent: [Review summary] Is this information correct?

User: Yes
Agent: Thank you, John Smith! Your report has been submitted.
```

## Validation Rules

- **Names**: Must include first and last name, only letters/spaces/hyphens/apostrophes
- **Addresses**: Minimum 10 characters
- **Phone Numbers**: 10 digits
- **Descriptions**: Minimum 10 characters with sufficient detail
- **Yes/No Fields**: Must be exactly "yes" or "no"

## Privacy Features

- Sensitive content detection based on keywords (abuse, assault, violence, etc.)
- Anonymous reporting option for sensitive incidents
- Privacy-aware logging (sensitive reports are redacted in logs)

## Dependencies

- `jvagent/openai_lm` - Language model for classification and extraction
- `resolv/resolv_api_action` - API integration for report submission

## File Structure

```
report_interview_interact_action/
├── __init__.py                              # Package initialization
├── report_interview_interact_action.py      # Main action implementation
├── info.yaml                                # Action metadata
└── README.md                                # This file
```

## Customization

### Adding New Questions

Add to the `question_graph` list:

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

Use the `@input_validator` decorator:

```python
@input_validator('field_name')
def validate_field(value: str, session: InterviewSession) -> Tuple[ValidationStatus, Optional[str]]:
    if not value:
        return ValidationStatus.INVALID, "Ask: Please provide a value"
    return ValidationStatus.VALID, None
```

### Adding Branch Functions

Use the `@branch_function` decorator:

```python
@branch_function('check_condition')
def check_condition(session: InterviewSession, visitor: InteractWalker) -> bool:
    # Return True to take the branch, False to continue
    return session.responses.get('field') == 'value'
```

## Known Issues

See [TARGETED_ACTION Updates](../../../../../../../jvsproject/README.md) for recent fixes:
- Fixed directive passing issue with function name collisions
- Fixed validation message consistency
- Enhanced phone validation to accept formatted numbers
- Improved privacy protection for sensitive reports

## Testing

Test scenarios:
- Basic report submission with all required fields
- Conditional flow when reporting on behalf of someone
- Similar report detection and user confirmation
- Sensitive content detection and privacy option
- Media attachment upload
- Validation error handling for all fields
- Cancellation at various stages
- Review and correction of entered data
