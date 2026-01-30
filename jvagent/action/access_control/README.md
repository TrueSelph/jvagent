# Access Control Action

Role-based access control with session tracking and permission validation for secure agent operations.

## Overview

The Access Control Action provides session-based role management and channel/resource permissions for jvagent applications. It supports complex permission structures with allow/deny rules for users and groups.

## Features

- **Session Management**: Create, validate, and remove user sessions
- **Channel/Resource Permissions**: Fine-grained access control by channel and resource
- **Group Management**: Assign users to groups for permission inheritance
- **Allow/Deny Rules**: Explicit allow and deny permissions with precedence
- **Graph Updates**: Automatic graph synchronization with configuration changes
- **Configuration Export/Import**: JSON/YAML format support with purge option
- **Exception Handling**: Actions exempt from permission checks

## Configuration

```yaml
actions:
  - action: jvagent/access_control
    context:
      enabled: true
      permissions:
        default:
          any:
            allow:
              - group: "all"
                enabled: true
            deny: []
      session_groups:
        admin: ["user1", "user2"]
        test: ["user3"]
      exceptions: ["health_check", "status"]
```

## Usage

### Basic Access Control
```python
# Check access to action
has_access = await action.has_action_access("user123", "my_action", "whatsapp")

# Update graph with current configuration
await action.update_graph()
```

### Configuration Management
```python
# Export configuration
config = action.export_config()

# Import configuration (merge)
await action.import_config(new_config, purge=False)

# Import configuration (replace)
await action.import_config(new_config, purge=True)
```

## Permission Structure

The permissions follow this hierarchy:
```
channel -> resource -> allow/deny -> rules
```

- **Channel**: Communication channel ("default", "whatsapp", "any")
- **Resource**: Specific resource/action ("any", "ActionName")
- **Allow/Deny**: Permission type (deny rules checked first)
- **Rules**: User or group specifications

### Rule Format
```python
{
    "user": "user_id",     # Direct user match
    "group": "group_name", # Group membership ("all"/"any" = everyone)
    "enabled": True        # Rule active/inactive
}
```

### Session Groups
```python
session_groups = {
    "admin": ["user1", "user2"],
    "test": ["user3", "user4"]
}
```

### Exceptions
```python
exceptions = ["health_check", "status", "ping"]
```

## API Endpoints

### Access Control
- `POST /actions/{action_id}/access` - Check access permissions

### Configuration Management
- `GET /actions/{action_id}/config/export?format=json|yaml` - Export configuration
- `POST /actions/{action_id}/config/import` - Import configuration

### Export Example
```bash
curl -X GET "/actions/123/config/export?format=yaml" \
  -H "Authorization: Bearer <token>"
```

### Import Example
```bash
curl -X POST "/actions/123/config/import" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "config_data": {
      "permissions": {...},
      "session_groups": {...},
      "exceptions": [...]
    },
    "purge": false
  }'
```

## Configuration Format

### JSON Format
```json
{
  "permissions": {
    "default": {
      "any": {
        "allow": [{"group": "all", "enabled": true}],
        "deny": []
      }
    }
  },
  "session_groups": {
    "admin": ["user1", "user2"]
  },
  "exceptions": ["health_check"]
}
```

### YAML Format
```yaml
permissions:
  default:
    any:
      allow:
        - group: all
          enabled: true
      deny: []
session_groups:
  admin:
    - user1
    - user2
exceptions:
  - health_check
```
