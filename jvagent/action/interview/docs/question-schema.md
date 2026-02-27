# Question Schema

## Question Configuration Fields

- **name**: Unique identifier for the question (required)
- **question**: Question text to ask the user
- **constraints**: Validation constraints dictionary
  - **description**: Description of what information is needed
  - **instructions**: Additional instructions for the LLM
  - **type**: Expected data type ("string", "number", "integer") - automatically validated using standard validators
  - **format**: Format specification (e.g., "email", "phone", "url") - automatically validated using standard validators
  - **pattern**: Regex pattern for validation - automatically validated using standard "pattern" validator
  - **standard_validators**: List of standard validator names to apply (e.g., ["email", "no_disposable_email"])
  - **input_handler**: String reference to function that processes raw input before validation (or use `@input_handler` decorator)
  - **input_validator**: String reference to function that validates responses (or use `@input_validator` decorator)
  - **data_input_field**: Key name in `visitor.data` dictionary to extract value from (e.g., "whatsapp_media"). When specified, the field is excluded from LLM extraction and values are extracted directly from `visitor.data`. When the key is absent, the field is auto-populated with `"N/A"` for the current question only. Useful for file uploads and other data passed via REST calls.
  - **extraction_mode**: Explicit override for classification extraction mode: `"verbatim"` (preserve full response), `"normalized"` (trim/normalize), or `"select"` (match to options). If omitted, mode is auto-detected from description keywords, `options`, or `input_context_provider`.
  - **ambiguous_patterns**: Patterns that trigger VALID status with optional feedback message for clarification
- **input_context**: Optional dictionary of static context data to provide with the question (e.g., available options, metadata). See [Decorators](decorators.md) for details.
- **input_context_provider**: Optional string reference to a registered input context provider function (use `@input_context_provider` decorator). The function returns a dictionary of context data dynamically at runtime. See [Decorators](decorators.md) for details.
- **Branch Functions**: Register custom branch functions using `@branch_function` decorator for complex branching logic (see Branch Functions section below)
- **required**: Whether the question is required (default: False)
- **branches**: Optional list of conditional branches (see Tree-Based Questions below). Supports both operator-based conditions (`{"op": "equals", "value": "yes"}`) and function-based conditions (`{"function": "function_name"}` or `{"function": "function_name", "op": ">=", "value": 8}`)
- **default_next**: Optional fallback question name if no branch conditions match

## Tree-Based Branching

The interview system supports tree-based question arrangements where the next question can be determined conditionally based on previous answers. This enables dynamic interview flows that adapt to user responses.

### Branch Configuration

Each question can define `branches` with conditions that determine which question to ask next:

```python
question_graph = [
    {
        "name": "user_type",
        "question": "Are you a premium or standard user?",
        "constraints": {
            "description": "User account type",
            "type": "string"
        },
        "required": True,
        "branches": [
            {
                "condition": {"op": "equals", "value": "premium"},
                "target": "premium_features"
            },
            {
                "condition": {"op": "equals", "value": "standard"},
                "target": "standard_setup"
            }
        ],
        "default_next": "contact_info"  # If no condition matches
    },
    {
        "name": "premium_features",
        "question": "Which premium features interest you?",
        "branches": [
            {
                "condition": {"op": "equals", "value": "advanced"},
                "target": "advanced_config"
            }
        ],
        "default_next": "contact_info"
    },
    {
        "name": "standard_setup",
        "question": "Standard setup question",
        "default_next": "contact_info"
    },
    {
        "name": "contact_info",
        "question": "What's your contact information?"
    }
]
```

### Branch Condition Format

Each branch condition evaluates against the question that owns the branch (question is implicit). Two condition formats are supported:

**Operator-Based Condition:**
```python
{
    "condition": {
        "op": "equals",           # Operator (equals, >=, <=, in, exists, etc.)
        "value": "expected_value"  # Value to match (required for most operators)
    },
    "target": "next_question_name"  # Question name to traverse to if condition matches
}
```

**Function-Based Condition:**
```python
{
    "condition": {
        "function": "function_name"  # Name of registered branch function
    },
    "target": "next_question_name"
}
```

Or with operator evaluation:
```python
{
    "condition": {
        "function": "function_name",  # Function returns a value
        "op": ">=",                   # Operator to evaluate function result
        "value": 8                    # Expected value for comparison
    },
    "target": "next_question_name"
}
```

**Note**: The question is always implicit - conditions evaluate against the question that owns the branch. For example, if `is_sensitive` has a branch with condition `{"op": "equals", "value": "yes"}`, it evaluates `is_sensitive == "yes"`. For function-based conditions, the function receives the session and visitor, allowing it to access all session data and graph context.

### Branch Functions

Branch functions allow you to define custom Python functions that evaluate complex branching conditions with full access to session data and graph context. This enables sophisticated branching logic that goes beyond simple operator-based comparisons.

**Function Registration:**

Use the `@branch_function` decorator to register branch functions in your interview action class:

```python
from jvagent.action.interview import branch_function
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interact.interact_walker import InteractWalker

@branch_function()
def check_contains_sensitive_info(
    session: InterviewSession,
    visitor: InteractWalker
) -> bool:
    """Check if report contains sensitive keywords.

    Returns True to branch to is_sensitive question, False to continue normal flow.
    """
    description = session.responses.get('report_description', '').lower()
    sensitive_keywords = ['abuse', 'assault', 'violence', 'threat', 'harassment']

    # Use session.context to store analysis for later use
    has_sensitive = any(keyword in description for keyword in sensitive_keywords)
    session.context['contains_sensitive_keywords'] = has_sensitive

    return has_sensitive
```

**Function Signature:**

All branch functions must accept two parameters:
- `session: InterviewSession` - Full access to session responses, context, and question index
- `visitor: InteractWalker` - Access to graph traversal, conversation, and user data

Functions can be either sync or async.

**Return Types - Two Patterns:**

1. **Boolean Return (Direct Branching)**: Function returns `bool` directly
   ```python
   "condition": {"function": "check_contains_sensitive_info"}
   ```
   - If function returns `True`, branch is taken
   - If function returns `False`, branch is skipped
   - No operator needed - result is used directly

2. **Value Return with Operator**: Function returns any value, evaluated with an operator
   ```python
   "condition": {"function": "calculate_urgency_score", "op": ">=", "value": 8}
   ```
   - Function returns a value (e.g., `int`, `str`, etc.)
   - Value is evaluated using the specified operator and expected value
   - Supports all operators: `equals`, `>=`, `<=`, `in`, `contains`, `matches`, etc.

**Usage in question_graph:**

```python
question_graph = [
    {
        "name": "report_description",
        "question": "Describe the incident you'd like to report in a single message.",
        "constraints": {
            "description": "A full description of the incident or grievance being reported.",
            "type": "string",
        },
        "required": True
    },
    {
        "name": "report_media",
        "question": "Please upload any images of the incident if you have them.",
        "constraints": {
            "description": "Images of the incident uploaded via WhatsApp media.",
            "type": "list",
            "data_input_field": "whatsapp_media",
        },
        "branches": [
            {
                "condition": {"function": "check_contains_sensitive_info"},
                "target": "is_sensitive"
            }
        ],
        "default_next": "reporting_on_behalf",
        "required": False
    },
    {
        "name": "is_sensitive",
        "question": "I noticed that the report includes sensitive information. Would you like to keep it private?",
        "constraints": {
            "type": "string",
            "options": ["yes", "no"],
        },
        "branches": [
            {
                "condition": {"op": "equals", "value": "yes"},
                "target": "REVIEW"
            }
        ],
        "required": True
    }
]
```

**Key Behaviors:**

- Branch functions are only evaluated **after** the question is answered (prevents premature execution during graph traversal)
- Functions have full access to `session.responses`, `session.context`, and `session.question_graph`
- Functions can use `visitor` to traverse the graph, access conversation history, or query user data
- You can mix function-based and operator-based conditions in the same `branches` list
- Functions can store computed values in `session.context` for later use or inter-function communication

**When Functions Execute:**

- Functions execute when the question owning the branch is answered (via `_update_reachable_questions`)
- During graph traversal (before question is answered), function conditions return `False` without executing
- This ensures functions only run when they have meaningful data to evaluate

### Branch Function Caching

Branch functions are automatically cached for performance optimization. When a branch function is executed, its result is cached along with tracking of which responses it accessed. This enables:

**Automatic Dependency Tracking**

The `@branch_function` decorator automatically tracks which response keys are accessed during execution:

```python
@branch_function()
async def analyze_report(session: InterviewSession, visitor: InteractWalker) -> bool:
    # These accesses are automatically tracked:
    description = session.responses.get('report_description')  # Dependency tracked
    location = session.responses.get('report_location')        # Dependency tracked

    # Compute complex analysis...
    return len(description) > 100
```

**Transparent Result Caching**

- First execution: Function runs normally, result is cached with its dependencies
- Subsequent accesses: Cached result returned if all dependencies unchanged
- Dependency change: Cache invalidated, function re-executed only if dependency value changed

**Smart Cache Invalidation**

When a response is updated, only branch functions that depend on that response are invalidated.

### Response Pruning

When a branch function result changes due to a response update, and the branching path changes, responses from questions on the old path are automatically pruned (removed):

```python
# Flow:
# 1. User enters "Safe report" → check_contains_sensitive returns False → normal_flow path
# 2. User answers normal_flow question
# 3. User updates report_description to "Unsafe content"
# 4. check_contains_sensitive returns True → sensitive_handling path (path changed!)
# 5. Response from normal_flow question is automatically pruned (no longer on valid path)
# 6. Session follows sensitive_handling path instead
```

Pruned responses are recorded in audit trail for debugging and potential undo operations.

**Pruning Behavior Details**

- **Triggered by**: Path change detected during branch re-evaluation (old_target != new_target)
- **Scope**: Only responses from questions no longer reachable on the new path are removed
- **Preserved**: All responses on the new path remain intact
- **Audit Trail**: Pruned responses are recorded in `session.update_history` with timestamps for debugging
- **Automatic**: No manual intervention required - the system handles all path detection and pruning

### Branch Reset Behavior

**When Branches Are Re-Evaluated**

Branches are automatically re-evaluated in two scenarios:

1. **After a Question is Answered** (`_update_reachable_questions`): When a user completes a question, all branches owned by that question are evaluated to determine the next target
2. **When a Response is Updated** (`_update_reachable_questions`): When a user modifies a previous answer in the UPDATE state, branches are re-evaluated to detect path changes

### Linear Questions (No Branches)

Questions without `branches` work as before - they follow linear order or use `default_next`:

```python
question_graph = [
    {
        "name": "question1",
        "question": "First question"
        # No branches - will go to next question in list or default_next if specified
    },
    {
        "name": "question2",
        "question": "Second question"
    }
]
```

### Example: User Onboarding Flow

```python
question_graph = [
    {
        "name": "account_type",
        "question": "What type of account do you want? (personal/business)",
        "branches": [
            {"condition": {"op": "equals", "value": "business"}, "target": "business_details"},
            {"condition": {"op": "equals", "value": "personal"}, "target": "personal_details"}
        ]
    },
    {
        "name": "business_details",
        "question": "What's your company name?",
        "default_next": "contact_info"
    },
    {
        "name": "personal_details",
        "question": "What's your full name?",
        "default_next": "contact_info"
    },
    {
        "name": "contact_info",
        "question": "What's your email address?"
    }
]
```
