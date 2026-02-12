# Supervisor Short Pass Interview InteractAction

Structured interview action for supervisors to review and approve/deny short pass requests in the ARC AI System.

## Overview

The Supervisor Short Pass Interview InteractAction guides supervisors through the process of reviewing a rank's short pass request. It allows supervisors to provide a decision (approval or denial) and feedback, which is then recorded via the ARC API.

## Features

- **Reference Number Extraction**: Automatically detects short pass reference numbers from quoted messages.
- **Decision Tracking**: Captures and validates approval or denial decisions.
- **Feedback Collection**: Ensures supervisors provide remarks for their decision.
- **API Integration**: Updates the short pass status in the ARC AI System.
- **DSPy Integration**: Optimized classification and extraction using DSPy.

## Architecture

Inherits from `InterviewInteractAction` and uses a unified classification and extraction approach that detects user intent (CANCELLATION, CONFIRMATION, UPDATE, SUBMISSION, NONE) and extracts field values in a single LLM call.

**Session Management**: Sessions are identified by `interview_type='SupervisorShortPassInterviewInteractAction'` and attached to Conversation nodes for per-user persistence.

## Configuration

### Agent Configuration (agent.yaml)

```yaml
- action: gdf/supervisor_short_pass_interview_interact_action
  context:
    enabled: true
    description: "Allows supervisors to review and approve/deny short pass requests."
    weight: -10
  config:
    model_action_type: "OpenAILanguageModelAction"
    model: "gpt-4.1-mini"
    model_temperature: 0.1
    use_dspy: true
```

### Routing Anchors

The action publishes anchors for InteractRouter routing:

- "Supervisor wants to review a short pass request"
- "Supervisor is approving or denying a short pass"
- "Supervisor is providing remarks on a short pass"

## Interview Flow

### Question Graph

1. **short_pass_reference_number** (required)
   - The reference ID for the short pass being reviewed.
   - Automatically skipped if the number is found in a quoted message.

2. **approval_status** (required)
   - The supervisor's decision (either 'approved' or 'denied').
   - Validation: Must be one of the two allowed values.

3. **supervisor_feedback** (required)
   - Remarks or feedback explaining the decision.
   - Validation: Minimum length of 5 characters.

## Custom Components

### Validators

- `validate_short_pass_reference_number`: Verifies the ID exists in the ARC AI System.
- `validate_approval_status`: Ensures the decision is clear ('approved' or 'denied').
- `validate_supervisor_feedback`: Ensures remarks are provided with sufficient detail.

### Branch Functions

- `skip_ref_if_known`: Extracts the reference number from the conversation context or quoted messages to streamline the interaction.

### Review Override

- `adapt_review`: Formats the summary data for the Review state, ensuring the reference number is displayed.

## Completion Handler

The `handle_interview_completion` function processes the supervisor's input:

1. Retrieves the final decision and feedback.
2. Calls `ArcAPIAction` to update the short pass status.
3. Sends a confirmation message to the supervisor.

## API Integration

Integrates with `ArcAPIAction` for short pass lookup and status updates.

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
Agent: Your decision to approved the short pass request (95) has been processed successfully.
```

## Validation Rules

- **Approval Status**: Must be EXACTLY 'approved' or 'denied'.
- **Feedback**: Must be at least 5 characters long.

## Dependencies

- `jvagent/openai_lm` - Language model integration.
- `gdf/arc_api_action` - API integration for short pass management.

## File Structure

```
supervisor_short_pass_interview_interact_action/
├── __init__.py
├── supervisor_short_pass_interview_interact_action.py
├── info.yaml
└── README.md
```

## Customization

### Adding New Questions

Modify the `question_graph` in the action class or override it in `agent.yaml`.

### Customizing Validation

Update the `@input_validator` methods within the `SupervisorShortPassInterviewInteractAction` class.

## Testing

Test scenarios:

- Direct approval of a short pass request.
- Denial of a request with specific feedback.
- Auto-extraction of reference numbers from quoted messages.
- Validation for empty feedback or invalid approval status.
- Session cleanup after submission.
