# Decorators & Handlers

## Input Handlers

Process raw input before validation (e.g., normalize time expressions).

**Handler context:** All handlers (input handlers, validators, review override, context providers, state handlers like completion/cancellation/review handlers) can optionally accept `visitor` (InteractWalker) and `interview_action` (InterviewInteractAction) for consistent access to the walker and action. These are passed only when the callable's signature accepts them, so existing handlers remain backward compatible.

**Recommended Approach: Use Decorators**

The cleanest way to register handlers is using the `@input_handler` decorator:

```python
from jvagent.action.interview import (
    InterviewInteractAction,
    input_handler,
)
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.memory import Interaction

@input_handler('available_times')
def normalize_time_expression(
    raw_input: str,
    session: InterviewSession,
    interaction: Interaction
) -> str:
    """Convert 'next Tuesday' to specific date."""
    # Can access interaction.user_id, interaction.utterance, etc.
    # Implementation here
    return normalized_date

class MyInterviewAction(InterviewInteractAction):
    question_graph = [
        {
            "name": "available_times",
            "constraints": {
                # Handler is automatically found via decorator
            }
        }
    ]
```

**Alternative: String References in question_graph**

You can also specify handlers as string references in `question_graph`:

```python
question_graph = [
    {
        "name": "available_times",
        "constraints": {
            "input_handler": "jvagent.actions.namespace.my_action.normalize_time_expression",
            # Or just function name if in the same module:
            # "input_handler": "normalize_time_expression",
        }
    }
]
```

## Validators

Validate responses with custom logic:

**Recommended Approach: Use Decorators**

The cleanest way to register validators is using the `@input_validator` decorator:

```python
from jvagent.action.interview import (
    InterviewInteractAction,
    input_validator,
)
from jvagent.action.interview.core.foundation.enums import ValidationStatus
from jvagent.action.interview.core.session.interview_session import InterviewSession
from typing import Optional, Tuple

@input_validator('user_email')
def validate_email_domain(value: str, session: InterviewSession) -> Tuple[ValidationStatus, Optional[str]]:
    """Check if email domain is allowed."""
    if "@company.com" not in value:
        return ValidationStatus.INVALID, "Only company emails are allowed"
    return ValidationStatus.VALID, None

class MyInterviewAction(InterviewInteractAction):
    question_graph = [
        {
            "name": "user_email",
            "constraints": {
                # Validator is automatically found via decorator
            }
        }
    ]
```

**Alternative: String References in question_graph**

```python
question_graph = [
    {
        "name": "user_email",
        "constraints": {
            "input_validator": "jvagent.actions.namespace.my_action.validate_email_domain",
        }
    }
]
```

**String Reference Formats:**
- Full module path: `"package.module.function_name"` (recommended for reliability)
- Function name only: `"function_name"` (searches loaded modules, may have collisions)
- Module-qualified: `"module_name.function_name"` (tries to import module)

**Resolution Priority:**
1. Decorator-registered handlers/validators (checked first)
2. String references in `question_graph` constraints (fallback)

Validators can return:
- `(ValidationStatus, message)`: Status and feedback message
- `(ValidationStatus, message, corrected_value)`: Status, feedback message, and autocorrected value
- `bool`: True for VALID, False for INVALID

**Autocorrection Support:**
Validators can return a corrected value as the third element of the tuple. If provided, the system will store the corrected value instead of the original input. This is useful for fuzzy matching scenarios (e.g., correcting "next tuesday" to a specific date, or matching "morning" to "9:00 AM").

## input_context_provider

Supply questions with extra context via dynamic provider functions. Use `@input_context_provider()` (no argument) to register by the function's `__name__`, or `@input_context_provider("custom_name")` to register under a custom name that you reference in the question's `input_context_provider` string.

```python
from jvagent.action.interview import (
    InterviewInteractAction,
    input_context_provider,
)
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interact.interact_walker import InteractWalker
from typing import Dict, Any

@input_context_provider()
async def get_available_times(
    session: InterviewSession,
    visitor: InteractWalker
) -> Dict[str, Any]:
    """Dynamically fetch available training times."""
    return {
        "available_times": [
            "Monday 9:00 AM - 11:00 AM",
            "Tuesday 2:00 PM - 4:00 PM",
            "Friday 5:00 PM - 7:00 PM"
        ],
        "timezone": "EST",
        "last_updated": "2024-01-15T10:00:00Z"
    }

class MyInterviewAction(InterviewInteractAction):
    question_graph = [
        {
            "name": "preferred_time",
            "question": "When would you like to schedule your training?",
            "input_context_provider": "get_available_times",
            "constraints": {
                "description": "User's preferred training time",
                "type": "string",
            },
            "required": True
        }
    ]
```

**Function Signature:** `(session: InterviewSession, visitor: InteractWalker) -> Dict[str, Any]` (sync or async).

## input_directive_override

Customize agent responses after a field value is successfully validated and stored. This allows you to conditionally replace or append to the default directive based on the stored value.

```python
from jvagent.action.interview import (
    InterviewInteractAction,
    input_directive_override,
)
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.memory import Interaction
from jvagent.action.interact.interact_walker import InteractWalker
from typing import Optional, Union, Tuple

@input_directive_override('user_email')
async def custom_email_directive(
    field_name: str,
    value: str,
    session: InterviewSession,
    interaction: Interaction,
    visitor: InteractWalker
) -> Optional[Union[str, Tuple[str, str]]]:
    """Custom directive after email is collected."""
    if '@example.com' in value:
        return "Tell the user: Thank you for using your work email! We'll send you special updates."
    return None

@input_directive_override('user_name')
async def append_name_directive(
    field_name: str,
    value: str,
    session: InterviewSession,
    interaction: Interaction,
    visitor: InteractWalker
) -> Optional[Union[str, Tuple[str, str]]]:
    """Append custom message after name is collected."""
    return ("append", f"Tell the user: Nice to meet you, {value}!")

@input_directive_override('user_email')
async def replace_email_directive(
    field_name: str,
    value: str,
    session: InterviewSession,
    interaction: Interaction,
    visitor: InteractWalker
) -> Optional[Union[str, Tuple[str, str]]]:
    """Replace default directive for email."""
    return ("replace", "Tell the user: Your email has been recorded. We'll send you updates soon!")
```

**Return Values:**
- `None` - Use default directive (no override)
- `str` - Replace default directive with this string
- `Tuple[str, str]` - First element is mode ("append" or "replace"), second is directive string

**Append Mode Behavior:**
- **With next question**: Next question directive is queued first, then the custom directive(s)
- **Without next question**: Only the custom directive(s) are queued (interview may be complete)
- Custom directives are **always** queued in append mode, regardless of whether a next question exists

**When Overrides Are Triggered:**
- After a field value is successfully validated and stored (VALID status)
- In both SUBMISSION flow (when answering questions) and UPDATE flow (when updating existing values)
- Before the default directive is sent to the user

## input_review_override

Customize the list of values shown in the Review state (before the user confirms). The override receives a key-value map of collected interview data (field name to value) and returns a dict used only for rendering the summary. Modifications are **for display only** and do not alter the values stored in the interview session. The decorator has no parameters and applies only to the `InterviewInteractAction` subclass defined in the same module.

**Handler signature:** `(session: InterviewSession, data: Dict[str, Any]) -> Dict[str, Any]` (sync or async). Omit fields by dropping keys from the returned dict; format by changing values. Display name is derived from the key when rendering.

```python
from jvagent.action.interview import (
    InterviewInteractAction,
    input_review_override,
)
from jvagent.action.interview.core.session.interview_session import InterviewSession
from typing import Any, Dict

@input_review_override
def adapt_review_for_display(
    session: InterviewSession,
    data: Dict[str, Any],
) -> Dict[str, Any]:
    """Omit optional fields when empty; display only, session unchanged."""
    return {k: v for k, v in data.items() if v not in (None, "", "n/a")}

class MyInterviewAction(InterviewInteractAction):
    question_graph = [...]
```

## data_input_field

For fields that receive data directly from REST calls (e.g., file uploads, binary data), use `data_input_field` in constraints to extract values directly from the `visitor.data` dictionary, bypassing LLM extraction entirely.

**How It Works:**
- When `data_input_field` is specified in a question's constraints, the system checks `visitor.data` for a matching key
- If the key exists, the value is extracted and treated as SUBMISSION (new data) or UPDATE (if field already has a value)
- If the key is absent, the field is auto-populated with `"N/A"` for the current question only (first unanswered); other data_input_field questions are not pre-populated
- The field is automatically excluded from LLM extraction
- Values go through the same validation pipeline (input handlers and validators) as LLM-extracted values

**Example: File Upload via WhatsApp Media**

```python
question_graph = [
    {
        "name": "report_media",
        "question": "Please upload any images of the incident if you have them.",
        "constraints": {
            "description": "Images of the incident uploaded via WhatsApp media.",
            "type": "list",
            "data_input_field": "whatsapp_media"  # Maps to visitor.data["whatsapp_media"]
        },
        "required": False
    }
]
```

**Use Cases:**
- File uploads (images, documents, etc.)
- Binary data passed via REST calls
- Pre-processed data that shouldn't be extracted from text
- Data that comes from external systems rather than user utterances

## Standard Validators

The interview system includes a library of reusable standard validators for common field types. Standard validators run **before** custom `@input_validator` decorators, allowing custom validators to add domain-specific logic on top of format validation.

**Built-in Standard Validators:**

- **Type Validators:** `string`, `number`, `integer`
- **Format Validators:** `email`, `phone`, `url`
- **Pattern Validator:** `pattern` (validates value matches regex pattern from constraints)
- **Domain-Specific:** `no_disposable_email`, `no_test_domain`

**Automatic Application:**

Standard validators are automatically applied based on constraint keys:

```python
question_graph = [
    {
        "name": "user_email",
        "question": "What is your email?",
        "constraints": {
            "type": "string",        # Applies "string" validator
            "format": "email",       # Applies "email" validator
        },
        "required": True
    }
]
```

**Explicit Application:**

Use `standard_validators` list to apply additional validators:

```python
question_graph = [
    {
        "name": "user_email",
        "constraints": {
            "type": "string",
            "format": "email",
            "standard_validators": [
                "no_disposable_email",
                "no_test_domain"
            ]
        }
    }
]
```

**Validation Order:**
1. Empty value check (if required)
2. Standard validators (type, format, pattern, explicit list)
3. Custom `@input_validator` decorators
4. Ambiguous pattern checks

**Creating Custom Standard Validators:**

```python
from jvagent.action.interview.core.foundation.standard_validators import standard_validator
from jvagent.action.interview.core.foundation.enums import ValidationStatus
from typing import Any, Dict, Optional, Tuple

@standard_validator("credit_card")
def validate_credit_card(
    value: Any, constraints: Dict[str, Any]
) -> Optional[Tuple[ValidationStatus, Optional[str], Optional[Any]]]:
    """Validate credit card number format."""
    if not isinstance(value, str):
        return ValidationStatus.INVALID, "Credit card must be a string", None

    digits = value.replace(" ", "").replace("-", "")
    if not digits.isdigit() or len(digits) not in (13, 15, 16):
        return ValidationStatus.INVALID, "Invalid credit card number", None

    return None  # Valid
```
