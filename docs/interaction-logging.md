# Interaction Logging

## Overview

jvagent uses a custom **INTERACTION** log level (level 22) for logging user-agent interactions to the database. This allows interactions to be easily filtered and queried separately from other log types.

## Implementation

### Custom Log Level

The INTERACTION level is registered at level 22 (between INFO=20 and CUSTOM=25) and is automatically registered when the logging module is imported.

### Simple Logging Approach

Interactions are logged using the standard Python logger with the `interaction()` method:

```python
logger.interaction("Interaction message", extra={"event_code": "interaction_completed", ...})
```

This approach:
1. Uses the standard logging infrastructure
2. Automatically captured by DBLogHandler if INTERACTION level is configured
3. Simple and consistent with other log levels
4. No custom service needed - jvspatial's BaseLoggingService available if needed

### Automatic Logging

Interactions are automatically logged when they complete:

- In `interact_endpoint()` after interaction is saved
- In `_stream_interaction()` after streaming completes
- Uses `logger.interaction()` with comprehensive context data

## Usage

### Automatic Logging

Interactions are automatically logged when they complete. No code changes needed.

### Manual Logging

Log interactions using the standard logger with the `interaction()` method:

```python
import logging
from jvagent.logging.service import INTERACTION_LEVEL_NUMBER  # Ensures level is registered

logger = logging.getLogger(__name__)

logger.interaction(
    "User interaction occurred",
    extra={
        "event_code": "interaction_completed",
        "interaction_id": "int_123",
        "user_id": "user_456",
        "session_id": "sess_789",
        "agent_id": "agent_012",
        "app_id": "app_345",
        "details": {
            "utterance": "Hello",
            "response": "Hi there!",
            "channel": "default"
        }
    }
)
```

### Custom Logging Service (Optional)

If you need a custom logging service, you can extend jvspatial's `BaseLoggingService`:

```python
from jvspatial.logging.service import BaseLoggingService

class MyLoggingService(BaseLoggingService):
    async def log_custom_event(self, event_data):
        await self.log_error(
            error_code="custom_event",
            message="Custom event occurred",
            log_level="INTERACTION",  # Or any other level
            details=event_data
        )
```

## Configuration

The INTERACTION level is automatically included in database logging when jvagent starts. It's added to the `log_levels` set during initialization in `cli.py`.

### Environment Variables

You can control which levels are captured via:

```bash
# Include INTERACTION in levels to capture
JVSPATIAL_DB_LOGGING_LEVELS=INTERACTION,ERROR,CRITICAL
```

Or let jvagent automatically include it (default behavior).

## Querying Interaction Logs

### Via API

Query interaction logs by log level:

```bash
GET /api/logs/agents/{agent_id}?log_level=INTERACTION
```

### Via Service

Use jvspatial's logging service to query interaction logs:

```python
from jvspatial.logging.service import get_logging_service

service = get_logging_service()
interaction_logs = await service.get_error_logs(
    log_level="INTERACTION",
    agent_id="agent_123",
    page=1,
    page_size=50
)
```

## Log Entry Structure

Interaction logs include:

- **log_level**: "INTERACTION"
- **event_code**: "interaction_completed" or "interaction_closed"
- **message**: Summary of interaction (utterance → response)
- **Core identifiers in log_data**:
  - `app_id`: Application ID
  - `agent_id`: Agent ID
  - `user_id`: User ID
  - `session_id`: Session ID
  - `interaction_id`: Interaction ID
  - `conversation_id`: Conversation ID
- **Interaction properties in log_data**:
  - `utterance`: User input text
  - `response`: Agent response text
  - `channel`: Communication channel
  - `actions`: List of action names executed
  - `directives`: List of directives
  - `parameters`: List of parameters
  - `events`: List of events
  - `observability_metrics`: Observability data
  - `usage`: Aggregated usage (tokens, model_call_count, estimated_cost_usd, etc.)
  - `has_response`: Boolean indicating if response exists
  - `action_count`: Number of actions executed
  - `duration_seconds`: Interaction duration (if available)
  - `started_at`: ISO timestamp when interaction started
  - `completed_at`: ISO timestamp when interaction completed
  - `closed`: Boolean indicating if interaction is closed
  - `streamed`: Boolean indicating if interaction was streamed
- **Full interaction state**:
  - `interaction_data`: Complete interaction state (full export from `get_state()`)

## Example Log Entry

```json
{
  "log_id": "log_abc123",
  "status_code": null,
  "event_code": "interaction_completed",
  "message": "Interaction: Hello → Hi there! How can I help?",
  "path": "",
  "method": "",
  "logged_at": "2025-01-02T12:00:00Z",
  "log_data": {
    "message": "Interaction: Hello → Hi there! How can I help?",
    "log_level": "INTERACTION",
    "event_code": "interaction_completed",
    "app_id": "app_789",
    "agent_id": "agent_012",
    "user_id": "user_345",
    "session_id": "sess_678",
    "interaction_id": "int_123",
    "conversation_id": "conv_456",
    "utterance": "Hello",
    "response": "Hi there! How can I help?",
    "channel": "default",
    "has_response": true,
    "action_count": 3,
    "duration_seconds": 1.5,
    "started_at": "2025-01-02T12:00:00Z",
    "completed_at": "2025-01-02T12:00:01.5Z",
    "closed": true,
    "streamed": false,
    "actions": ["OrchestratorInteractAction", "ReplyAction"],
    "directives": [],
    "tasks": [],
    "parameters": [],
    "events": [],
    "observability_metrics": [],
    "interaction_data": {
      "id": "int_123",
      "conversation_id": "conv_456",
      "user_id": "user_345",
      "session_id": "sess_678",
      "utterance": "Hello",
      "response": "Hi there! How can I help?",
      "actions": ["OrchestratorInteractAction", "ReplyAction"],
      "directives": [],
      "parameters": [],
      "events": [],
      "observability_metrics": [],
      "usage": {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "model_call_count": 0,
        "estimated_cost_usd": 0.0,
        "total_duration_seconds": 0.0,
        "last_updated": null
      },
      "started_at": "2025-01-02T12:00:00Z",
      "completed_at": "2025-01-02T12:00:01.5Z",
      "closed": true,
      "streamed": false
    }
  }
}
```

## Level Hierarchy

```
50  CRITICAL  - System failures
45  SECURITY  - Security events (custom)
40  ERROR     - Errors
35  AUDIT     - Audit trails (custom)
30  WARNING   - Warnings
25  CUSTOM    - Custom events (pre-registered)
22  INTERACTION - User-agent interactions (jvagent)
20  INFO      - Information
10  DEBUG     - Debug info
5   TRACE     - Detailed traces (custom)
0   NOTSET    - Not set
```

## Files Modified

1. **`jvagent/logging/service.py`**
   - INTERACTION level registration
   - Exports INTERACTION_LEVEL_NUMBER

2. **`jvagent/logging/__init__.py`**
   - Exports INTERACTION_LEVEL_NUMBER

3. **`jvagent/action/interact/endpoints.py`**
   - Uses `logger.interaction()` to log interactions
   - Helper function `_build_interaction_log_data()` to build log context

4. **`jvagent/cli.py`**
   - Registers INTERACTION level before database initialization
   - Includes INTERACTION in log_levels

## Benefits

1. **Simple Approach**: Uses standard logger - no custom service needed
2. **Separate from Errors**: Interactions logged separately from errors/warnings
3. **Easy Filtering**: Query all interactions with `log_level="INTERACTION"`
4. **Complete Context**: Full interaction state included in logs
5. **Automatic**: No code changes needed - interactions logged automatically
6. **Flexible**: Can extend jvspatial's BaseLoggingService if custom service needed

## Related Documentation

- [Logging System](logging.md) - Comprehensive logging system documentation
- [Error Logging](error-logging.md) - Error logging and querying
- For jvspatial logging service and custom log levels, see the jvspatial documentation.

