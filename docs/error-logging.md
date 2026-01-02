# Error Logging

jvagent automatically logs records at configurable levels to the database with automatic context extraction. Simply use the standard Python logger - no complex setup required.

## Overview

jvagent extends jvspatial's database logging with:

1. **Automatic context extraction**: Extracts `agent_id`, `app_id`, `user_id`, `session_id`, `interaction_id` from call stack
2. **App-level logging control**: Enable/disable logging per application
3. **Configurable log levels**: Capture DEBUG, INFO, WARNING, ERROR, CRITICAL logs
4. **Enhanced DBLogHandler**: Automatically installed when database is initialized
5. **Agent ID cross-referencing**: Query logs by agent_id via API endpoints

## Quick Start

Error logging is automatically configured when you run jvagent:

```bash
# Error logging is enabled by default (ERROR and CRITICAL)
python -m jvagent serve

# Configure log levels
JVAGENT_DB_LOGGING_LEVELS=WARNING,ERROR,CRITICAL python -m jvagent serve

# Disable error logging globally
JVAGENT_LOGGING_ENABLED=false python -m jvagent serve
```

## Configuration

### Log Levels

Configure which log levels to capture (default: ERROR, CRITICAL):

```bash
# Capture ERROR and CRITICAL only (default)
JVAGENT_DB_LOGGING_LEVELS=ERROR,CRITICAL

# Capture all levels
JVAGENT_DB_LOGGING_LEVELS=DEBUG,INFO,WARNING,ERROR,CRITICAL

# Capture warnings and above
JVAGENT_DB_LOGGING_LEVELS=WARNING,ERROR,CRITICAL
```

### Enable/Disable Logging

```bash
# Enable database logging (default)
JVAGENT_LOGGING_ENABLED=true

# Disable database logging
JVAGENT_LOGGING_ENABLED=false
```

### API Endpoints

```bash
# Enable API endpoints (default)
JVAGENT_DB_LOGGING_API_ENABLED=true

# Disable API endpoints
JVAGENT_DB_LOGGING_API_ENABLED=false
```

## Usage

### Simple Logging

Just use the standard logger:

```python
import logging

logger = logging.getLogger(__name__)
logger.error("Action failed")
logger.warning("Configuration issue")
logger.info("User logged in")
```

The handler automatically extracts context (agent_id, interaction_id, etc.) from the call stack.

### With HTTP Context

```python
logger.error(
    "User not found",
    extra={
        "status_code": 404,
        "error_code": "not_found",
        "path": "/api/agents/agent_123/actions",
        "method": "GET"
    }
)
```

### With Custom Details and Explicit Agent ID

```python
logger.info(
    "Action execution completed",
    extra={
        "status_code": 200,
        "error_code": "success",
        "details": {
            "action": "process_data",
            "duration": 0.5,
            "items_processed": 100
        },
        "agent_id": "agent_123",  # Optional - auto-extracted if not provided
        "user_id": "user_456"
    }
)
```

### With Exception

```python
try:
    process_action()
except Exception:
    logger.error(
        "Action processing failed",
        exc_info=True,  # Includes full traceback
        extra={
            "status_code": 500,
            "error_code": "action_error"
        }
    )
```

## Automatic Context Extraction

The `DBLogHandler` automatically extracts jvagent-specific context:

### From Call Stack

- **agent_id**: From Action or InteractWalker instances in call stack
- **interaction_id**: From InteractWalker.interaction
- **user_id**: From InteractWalker.user_id
- **session_id**: From InteractWalker.session_id

### From Context Variables

- **interaction_id**: From `get_interaction_id()` context variable
- **action_class**: From `get_calling_action_name()` context variable

### From Log Record Extra

- Any fields explicitly provided in `extra` parameter take precedence

## Configuration

### Environment Variables

```bash
# Enable/disable logging globally
JVAGENT_LOGGING_ENABLED=true

# Logging database configuration
JVSPATIAL_LOG_DB_TYPE=json
JVSPATIAL_LOG_DB_PATH=./jvagent_logs

# Minimum log level to persist (ERROR/CRITICAL always logged)
JVAGENT_LOG_DB_LEVEL=ERROR
```

### App-Level Configuration

Control logging per application via the `App` node:

```python
from jvagent.core.app import App

# Disable logging for specific app
app = await App.get()
app.logging_enabled = False
await app.save()

# Re-enable logging
app.logging_enabled = True
await app.save()
```

## ErrorLog Fields

Errors are saved with these fields:

```python
{
    "status_code": 500,           # HTTP status code (default: 500)
    "error_code": "action_error",  # Error code (default: "internal_error")
    "path": "/api/agents/...",     # Request path (default: "")
    "method": "POST",               # HTTP method (default: "")
    "logged_at": "2024-01-15T...", # Timestamp
    "error_data": {
        "message": "Action failed",
        "details": {...},           # Optional details
        "traceback": "...",         # If exc_info=True
        # jvagent-specific fields (auto-extracted):
        "app_id": "app_123",
        "agent_id": "agent_456",
        "user_id": "user_789",
        "session_id": "session_abc",
        "interaction_id": "int_xyz"
    }
}
```

## API Endpoints for Log Querying

### Query Logs by Agent ID

```http
GET /api/logs?agent_id=agent_123&page=1&page_size=50
```

**Authentication Required**: Yes

**Query Parameters:**
- `agent_id` (optional): Filter by agent_id for cross-referencing
- `category` (optional): Filter by log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- `start_date` (optional): ISO format start date (e.g., `2024-01-01T00:00:00Z`)
- `end_date` (optional): ISO format end date
- `page` (optional, default: 1): Page number
- `page_size` (optional, default: 50, max: 200): Items per page

**Examples:**

```bash
# Get all logs for specific agent
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/logs?agent_id=agent_123"

# Get ERROR logs for agent
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/logs?agent_id=agent_123&category=ERROR"

# Get logs for agent in date range
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/logs?agent_id=agent_123&start_date=2024-01-01T00:00:00Z&end_date=2024-01-31T23:59:59Z"
```

**Response:**
```json
{
  "logs": [
    {
      "log_id": "log_123",
      "log_level": "ERROR",
      "status_code": 500,
      "error_code": "action_error",
      "message": "Action execution failed",
      "path": "/api/agents/agent_123/actions",
      "method": "POST",
      "agent_id": "agent_123",
      "logged_at": "2024-01-15T10:30:00Z",
      "error_data": {
        "message": "Action execution failed",
        "log_level": "ERROR",
        "agent_id": "agent_123",
        "app_id": "app_456",
        "user_id": "user_789",
        "session_id": "session_abc",
        "interaction_id": "interaction_xyz",
        "details": {...}
      }
    }
  ],
  "pagination": {
    "page": 1,
    "page_size": 50,
    "total": 100,
    "total_pages": 2
  }
}
```

## Querying Error Logs

Use the logging service to query error logs:

```python
from jvagent.logging import get_logging_service

service = get_logging_service()

# Get errors for specific agent
logs = await service.get_error_logs(agent_id="agent_123")

# Get errors for specific app
logs = await service.get_error_logs(app_id="app_456")

# Get errors for specific user
logs = await service.get_error_logs(user_id="user_789")

# Combine filters
from datetime import datetime, timedelta
yesterday = datetime.now() - timedelta(days=1)
logs = await service.get_error_logs(
    agent_id="agent_123",
    status_code=500,
    start_time=yesterday
)
```

## How It Works

1. **Initialize Database**: `initialize_logging_database()` installs `DBLogHandler`
2. **Log Errors**: Use `logger.error()` or `logger.critical()` as normal
3. **Handler Intercepts**: `DBLogHandler` intercepts ERROR/CRITICAL logs
4. **Context Extraction**: Extracts jvagent context from call stack and context variables
5. **App-Level Check**: Checks app-level logging configuration
6. **Save to Database**: Saves to ErrorLog database asynchronously

## Best Practices

1. **Use Standard Logger**: Always use `logging.getLogger()` - don't create custom logging services
2. **Include Context**: Add `status_code`, `error_code`, `path`, `method` in `extra` for HTTP errors
3. **Use Details**: Put structured data in `details` field
4. **Include Tracebacks**: Use `exc_info=True` for exceptions
5. **Don't Block**: Handler saves asynchronously - logging never blocks

## Environment Variables Reference

```bash
# Enable/disable database logging
JVAGENT_LOGGING_ENABLED=true  # Default: true
JVAGENT_DB_LOGGING_ENABLED=true  # Alternative name

# Log levels to capture (comma-separated)
JVAGENT_DB_LOGGING_LEVELS=ERROR,CRITICAL  # Default: ERROR,CRITICAL
# Options: DEBUG,INFO,WARNING,ERROR,CRITICAL

# Enable/disable API endpoints
JVAGENT_DB_LOGGING_API_ENABLED=true  # Default: true

# Log retention (days)
JVAGENT_LOG_RETENTION_DEFAULT_DAYS=60  # Default: 60

# Database type
JVAGENT_LOG_DB_TYPE=json  # Default: json
# Options: json, sqlite, mongodb, dynamodb

# JSON database path
JVAGENT_LOG_DB_PATH=./jvagent_logs  # Default: ./jvagent_logs

# SQLite database path
JVAGENT_LOG_DB_PATH=./logs/jvagent_logs.db

# MongoDB configuration
JVAGENT_LOG_DB_URI=mongodb://localhost:27017
JVAGENT_LOG_DB_NAME=jvagent_logs

# DynamoDB configuration
JVAGENT_LOG_DB_TABLE_NAME=jvspatial_logs
JVAGENT_LOG_DB_REGION=us-east-1
JVAGENT_LOG_DB_ENDPOINT_URL=http://localhost:8000  # For local testing
```

## Best Practices

1. **Use Standard Logger**: Always use `logging.getLogger()` - don't create custom logging services
2. **Configure Log Levels**: Only capture levels you need to reduce database size
3. **Include Context**: Add `status_code`, `error_code`, `path`, `method` in `extra` for HTTP logs
4. **Let Auto-Extraction Work**: agent_id, app_id, interaction_id are automatically extracted
5. **Use Details**: Put structured data in `details` field
6. **Include Tracebacks**: Use `exc_info=True` for exceptions
7. **Don't Block**: Handler saves asynchronously - logging never blocks
8. **Monitor Database Size**: Implement log retention policies for production
9. **Use API for Cross-Referencing**: Leverage agent_id filtering to analyze agent-specific logs
10. **Check App-Level Config**: Use per-app logging controls when needed

## Troubleshooting

### Errors Not Being Logged

1. **Check global config**: `JVAGENT_LOGGING_ENABLED=true`
2. **Check app-level config**: `app.logging_enabled == True`
3. **Check database**: Logging database must be initialized
4. **Check log level**: Only ERROR/CRITICAL are logged by default
5. **Check logs**: Look for "Logging disabled" or "database not available" messages

### Missing Context Fields

1. **agent_id**: Ensure Action or InteractWalker is in call stack
2. **interaction_id**: Set by interaction processing or context variables
3. **user_id**: Set by InteractWalker or explicitly in `extra`
4. **session_id**: Set by InteractWalker or explicitly in `extra`

### Performance Issues

1. **Use pagination**: Don't fetch all errors at once
2. **Add indexes**: Custom filters on non-indexed fields are slower
3. **Implement retention**: Purge old logs regularly
4. **Use time filters**: Limit queries to specific date ranges

## Related Documentation

- [Logging System](logging.md) - Comprehensive logging system documentation
- [Interaction Logging](interaction-logging.md) - INTERACTION log level and interaction logging
- [jvspatial Logging Service](../../jvspatial/docs/md/logging-service.md) - Base logging service
- [jvspatial Custom Log Levels](../../jvspatial/docs/md/custom-log-levels.md) - Custom log level system
