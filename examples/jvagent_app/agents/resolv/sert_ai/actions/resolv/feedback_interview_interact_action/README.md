# Feedback Interview InteractAction

Structured interview action for submitting feedback on existing reports or projects in the Resolv Incident Management System.

## Overview

The Feedback Interview InteractAction guides users through collecting feedback on completed work or existing reports. It features report matching, media attachment support, and seamless integration with the Resolv API.

## Features

- **Report Matching**: Automatically matches user descriptions to existing reports
- **Media Attachment Support**: Accepts photos and videos as feedback evidence
- **Conditional Branching**: Dynamic flow based on media availability
- **Custom Validation**: Field-level validation with user-friendly error messages
- **Custom Directives**: Shows matching reports for user selection
- **Flexible Feedback**: Supports feedback on both reports and projects

## Architecture

Inherits from `InterviewInteractAction` and uses a unified classification and extraction approach that detects user intent (CANCELLATION, CONFIRMATION, UPDATE, SUBMISSION, NONE) and extracts field values in a single LLM call.

**Session Management**: Sessions are identified by `interview_type='FeedbackInterviewInteractAction'` and attached to Conversation nodes for per-user persistence.

## Configuration

### Agent Configuration (agent.yaml)

```yaml
- action: resolv/feedback_interview_interact_action
  context:
    enabled: true
    description: "Feedback Interview action is used to create feedback for incidents and projects."
    weight: -50 # Runs before fallback actions
  config:
    model_action_type: "OpenAILanguageModelAction"
    model: "gpt-4o-mini"
    model_temperature: 0.1
    model_max_tokens: 4096
    use_history: true
    max_statement_length: 100
    history_limit: 3
```

### Routing Anchors

The action publishes anchors for InteractRouter routing:

- "User wants to provide feedback on a completed report or project"
- "User is giving feedback about work that was done"
- "User is providing an update on a previously reported issue"
- "User is sharing photos or evidence of completed work for feedback"
- "User is providing an update or follow-up on previously submitted feedback"
- "User is currently creating feedback and providing an incident that took place"
- "User is providing additional details about an incident likely related to ongoing feedback or report"

## Interview Flow

### Question Graph

1. **feedback_content** (required)
   - Full details about the feedback
   - Minimum 10 characters
   - Validation: Must be actual feedback content, not a request
   - Branches to media question if applicable

2. **feedback_media** (conditional)
   - Photos or videos related to feedback
   - Accepts multiple files
   - Optional field

3. **report_details** (optional)
   - Description to match existing reports
   - Used for report lookup
   - Triggers report matching

4. **selected_report_id** (required if reports found)
   - ID of the report to attach feedback to
   - Validation: Must be a valid report ID from matching results

## Custom Components

### Validators

- `validate_feedback_content`: Ensures sufficient detail (min 10 chars), proper whitespace handling
- `validate_report_details`: Handles report matching logic (mocked in this example)
- `validate_selected_report_id`: Validates report ID exists in matching results

### Directive Overrides

- `custom_report_details_directive`: Shows matching reports with IDs and descriptions for user selection

### Branch Functions

- `can_ask_for_media`: Determines if media question should be asked based on feedback type
- `search_for_report`: Checks if matching reports were found

### Review Override

- `adapt_review`: Formats data for review state, omits empty fields and formats media links

## Completion Handler

The `handle_interview_completion` function processes the collected data:

1. Extracts all interview responses
2. Calls ResolvAPIAction to submit feedback
3. Sends confirmation message to user
4. Cleans up interview session

## API Integration

Integrates with `ResolvAPIAction` to submit feedback to the Resolv IMS:

```python
result = await resolv_api_action.submit_comment(
    content=feedback_content,
    report_id=selected_report_id,
    attachments=feedback_media
)
```

## Usage

### Starting the Interview

User utterances matching the anchors will trigger the interview:

- "I want to give feedback on my report"
- "The pothole on Main Street has been fixed"
- "I have an update on the construction issue"

### Example Interaction

```
User: I want to give feedback on the pothole I reported
Agent: Please share your feedback.

User: The pothole has been fixed and the road is smooth now
Agent: Do you have any media to upload along with your feedback?

User: [uploads photo]
Agent: Can you describe the report you want to give feedback on?

User: The pothole on Main Street near the traffic light
Agent: I found 3 matching reports:
       Report ID: 223 - Large pothole on Main Street...
       Report ID: 224 - Road damage near traffic light...
       Report ID: 225 - Pothole causing vehicle damage...
       Which report would you like to give feedback on?

User: 223
Agent: [Review summary] Is this information correct?

User: Yes
Agent: Thank you! Your feedback has been submitted for report 223.
```

## Validation Rules

- **Feedback Content**: Minimum 10 characters, must be actual feedback (not a request)
- **Report ID**: Must exist in the matching reports list
- **Media Files**: Optional, accepts multiple files

## Report Matching

The action uses the `report_details` field to search for matching reports:

- Queries Resolv API for reports matching the description
- Presents top matches to the user with IDs and descriptions
- User selects the correct report by ID
- Validates selected ID against matching results

## Dependencies

- `jvagent/openai_lm` - Language model for classification and extraction
- `resolv/resolv_api_action` - API integration for feedback submission

## File Structure

```
feedback_interview_interact_action/
├── __init__.py                                # Package initialization
├── feedback_interview_interact_action.py      # Main action implementation
├── info.yaml                                  # Action metadata
└── README.md                                  # This file
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

### Modifying Report Matching

Update the `custom_report_details_directive` function to customize how matching reports are displayed:

```python
@input_directive_override
async def custom_report_details_directive(...):
    matching_reports = session.context.get("matching_reports")
    # Customize display format
    return ("replace", f"Custom message with {len(matching_reports)} reports")
```

## Testing

Test scenarios:

- Basic feedback submission with all required fields
- Report matching with various descriptions
- Media attachment upload
- Validation error handling for all fields
- Cancellation at various stages
- Review and correction of entered data
- Report ID validation with valid and invalid IDs
