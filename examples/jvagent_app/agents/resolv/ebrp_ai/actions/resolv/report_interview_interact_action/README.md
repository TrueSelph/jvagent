# Report Interview InteractAction

Structured interview action for creating incident reports in the Resolv Incident Management System.

## Overview

The Report Interview InteractAction guides users through a multi-step interview process to collect comprehensive incident information. It features conditional branching, validation, media attachment support, and privacy protection for sensitive reports.

## Features

- **Multi-step Interview Flow**: Collects incident details, location, media, and reporter or stakeholder information
- **Conditional Branching**: Dynamic flow based on user responses (sensitive content, reporting on behalf)
- **Similar Report Detection**: Checks for existing reports at the same location to prevent duplication
- **Media Attachment Support**: Accepts photos and videos as incident evidence
- **Privacy Protection**: Allows sensitive reports to be marked for anonymous submission
- **Custom Validation**: Field-level validation with user-friendly error messages
- **Custom Directives**: Provides context-aware guidance and matching report information
- **API Integration**: Seamlessly submits reports to the Resolv IMS via the Resolv API

## Architecture

Inherits from `InterviewInteractAction` and uses a unified classification and extraction approach that detects user intent (CANCELLATION, CONFIRMATION, UPDATE, SUBMISSION, NONE) and extracts field values in a single LLM call.

**Session Management**: Sessions are identified by `interview_type='ReportInterviewInteractAction'` and attached to Conversation nodes for per-user persistence.

## Configuration

### Agent Configuration (agent.yaml)

```yaml
- action: resolv/report_interview_interact_action
  context:
    enabled: true
    description: "Report Interview action is used to create a **new incident report or complaint** that does not yet exist in the system."
    weight: -50 # Runs before fallback actions
  config:
    model_action_type: "OpenAILanguageModelAction"
    model: "gpt-4o-mini"
    model_temperature: 0.1
    model_max_tokens: 8192
    use_history: true
    max_statement_length: 100
    history_limit: 3
```

### Routing Anchors

The action publishes anchors for InteractRouter routing:

- "User is reporting a new problem, hazard, or safety issue"
- "User needs to file a new complaint or incident report"
- "User is providing details for a new incident report"
- "User is uploading photos or evidence for a new incident report"
- "User is revising, canceling, updating, or confirming an active incident report being created"

## Interview Flow

### Question Graph

1. **incident_description** (required)
   - Detailed description of the incident
   - Minimum 10 characters
   - Validation: Ensures sufficient detail is provided

2. **incident_location** (required)
   - Specific address or landmark where the incident occurred
   - Minimum 10 characters
   - Triggers similar report check and matching display

3. **continue_report** (conditional)
   - Shown if similar reports are detected at the location
   - Options: yes/no
   - Branches to CANCELLED if the user decides not to proceed

4. **incident_media** (optional)
   - Photos or videos related to the incident
   - Accepts multiple files
   - Triggers sensitive content detection logic

5. **is_sensitive** (conditional)
   - Shown if sensitive content is detected in the description or media
   - Options: yes/no
   - Marks report as anonymous if "yes"

6. **reporting_on_behalf** (required)
   - Whether the user is filing for themselves or someone else
   - Options: yes/no
   - Branches to stakeholder details if "yes"

7. **stakeholder_name/address/phone** (conditional)
   - Full details of the person the report concerns
   - Only asked if reporting on behalf of another individual

8. **reporter_name/address** (required)
   - Full details of the person submitting the report
   - Validated against stakeholder information to ensure distinction

## Custom Components

### Validators

- `validate_incident_description`: Ensures sufficient detail (min 10 chars)
- `validate_incident_location`: Validates specific location details
- `validate_is_sensitive`: Ensures valid yes/no response for privacy
- `validate_reporting_on_behalf`: Validates filing status
- `validate_stakeholder_*`: Validates stakeholder name, address, and 10-digit phone
- `validate_reporter_*`: Validates reporter name and residential address

### Directive Overrides

- `custom_location_directive`: Displays matching reports with IDs and descriptions for user awareness
- `custom_continue_directive`: Provides a clear cancellation message if the user stops the report

### Branch Functions

- `detect_sensitive_content`: Detects sensitive keywords or the presence of media
- `check_for_similar_incidents`: (Mocked) Queries for existing reports at the same location

### Review Override

- `adapt_review`: Formats collected data for the final review state, handling empty fields and media links

## Completion Handler

The `handle_interview_completion` function processes the collected data:

1. Extracts all collected responses from the session
2. Determines anonymity based on the sensitivity response
3. Generates the final report payload
4. Calls `ResolvAPIAction` to submit the report
5. Sends a personalized confirmation message to the user
6. Cleans up the interview session

## API Integration

Integrates with `ResolvAPIAction` to create reports:

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

User utterances matching the anchors will trigger the flow:

- "I need to report a dangerous pothole"
- "There is an issue at my residential complex"
- "I want to file a new incident report"

### Example Interaction

```
User: I want to report a broken street light on Oak Street
Agent: Please provide a description of the incident.

User: The light has been out for three days and it's dangerous at night.
Agent: Where exactly did this incident occur?

User: 123 Oak Street, near the park entrance.
Agent: [Optional: Similar reports found...] Do you have any photos or videos?

User: No, skip that.
Agent: Are you submitting this on behalf of someone else?

User: No.
Agent: What is your full name?

User: Alice Johnson
Agent: What is your residential address?

User: 456 Pine Ave
Agent: [Review Summary] Is this information correct?

User: Yes
Agent: Thank you, Alice Johnson! Your report has been submitted.
```

## Validation Rules

- **Names**: Must include first and last name, minimum 2 characters per part
- **Addresses**: Minimum 10 characters
- **Phone Numbers**: Exactly 10 digits
- **Descriptions**: Minimum 10 characters with specific detail
- **Binary Choices**: Must be exactly "yes" or "no"

## Privacy Features

- Automated detection of sensitive keywords (abuse, violence, etc.)
- Conditional privacy question based on content sensitivity
- Redaction of sensitive details in standard report overviews

## Dependencies

- `jvagent/openai_lm` - Language model for intent classification and extraction
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

Add a new configuration to the `question_graph` attribute:

```python
{
    "name": "new_detail",
    "question": "Please provide [detail]?",
    "constraints": {
        "description": "Description for LLM",
        "type": "string"
    },
    "required": True
}
```

### Adding Custom Validators

Use the `@input_validator` decorator on a class method:

```python
@input_validator('field_name')
def validate_field(self, value: str, session: InterviewSession) -> Tuple[ValidationStatus, Optional[str]]:
    if not value:
        return ValidationStatus.INVALID, "Ask: Please provide a value."
    return ValidationStatus.VALID, None
```

## Testing

Recommended test scenarios:

- Full report submission with all fields provided
- Reporting on behalf of someone else (checking conditional branches)
- Sensitivity detection triggering (using keywords like "assault")
- Validation failure handling for names, phones, and addresses
- Cancellation flow when similar reports are found
- End-to-end review and confirmation process
