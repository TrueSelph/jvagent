# Onboarding InteractAction

Automatic user onboarding action that detects new users and subscribes them to default contact groups in the Resolv Incident Management System.

## Overview

The Onboarding InteractAction automatically onboards new WhatsApp users by detecting first-time interactions, subscribing them to default groups, and presenting a channels selection page. It runs on every interaction to catch new users immediately.

## Features

- **Automatic Detection**: Identifies first-time users automatically
- **Default Group Subscription**: Subscribes new users to configured default groups
- **Channel Selection**: Provides personalized channels page link
- **WhatsApp-Specific**: Only executes for WhatsApp channel interactions
- **Always Execute**: Runs on every interaction to catch new users (weight: -100)
- **Configurable Groups**: Default groups can be updated via API
- **Non-Blocking**: Returns immediately if conditions aren't met

## Architecture

Inherits from `InteractAction` and uses the `always_execute` flag to run on every interaction, checking for new users and onboarding them automatically.

## Configuration

### Agent Configuration (agent.yaml)

```yaml
- action: resolv/onboarding_interact_action
  context:
    enabled: true
    description: "Onboarding action is used to register user and subscribe them to default groups."
    weight: -100 # Runs very early to catch new users
    default_groups: ["28"]
    prompt: "Introduce yourself and present the link to the channels page for the user to select the channels they want to join: {channels_page}"
```

### Configuration Properties

- **default_groups** (List[str]): List of group IDs to subscribe new users to
- **prompt** (str): Welcome message template with `{channels_page}` placeholder
- **always_execute** (bool): Always runs regardless of routing (default: True)
- **weight** (int): Execution order (-100 = very early)

## Execution Logic

### Conditions for Execution

The action only executes when ALL conditions are met:

1. User has a phone number (user_id)
2. User has a display name
3. ResolvAPIAction is available
4. Channel is "whatsapp"
5. User is marked as new (`visitor.new_user`)

If any condition fails, the action returns immediately without processing.

### Onboarding Process

When conditions are met:

1. **Subscribe to Default Groups**
   - Iterates through configured `default_groups`
   - Calls `api.subscribe_user()` for each group
   - Creates contact if doesn't exist

2. **Generate Channels Page**
   - Calls `api.get_channels_page()` with user details
   - Receives personalized subscription link

3. **Add Welcome Directive**
   - Formats prompt template with channels page URL
   - Adds directive to visitor for PersonaAction to process

## Methods

### execute

Main execution method that handles the onboarding process.

```python
async def execute(self, visitor: InteractWalker) -> None:
    # Check conditions
    # Subscribe to default groups
    # Get channels page
    # Add welcome directive
```

### get_contact_groups

Retrieve all available contact groups from Resolv API.

```python
groups = await action.get_contact_groups()
```

**Returns:** List of group dictionaries with IDs and names

### update_default_contact_groups

Update the default groups configuration and persist changes.

```python
new_groups = await action.update_default_contact_groups(
    group=["28", "29", "30"]
)
```

**Parameters:**

- `group` (List[str]): New list of default group IDs

**Returns:** Updated list of default groups

## API Integration

Integrates with `ResolvAPIAction` for:

- `subscribe_user()` - Subscribe user to groups
- `get_channels_page()` - Generate personalized subscription link
- `get_contact_groups()` - Retrieve available groups

## Usage

### Automatic Onboarding

The action runs automatically on every WhatsApp interaction:

```
New User: Hello
[Onboarding Action Executes]
- Subscribes user to group 28
- Generates channels page
- Adds welcome directive

Agent: Welcome! I'm Navi, your assistant. Here's a link to select
       the channels you want to join: https://app.resolv-ims.com/...
```

### Manual Group Management

Update default groups via API:

```python
# Get the action
onboarding_action = await action.get_action("OnboardingInteractAction")

# Get available groups
groups = await onboarding_action.get_contact_groups()

# Update default groups
await onboarding_action.update_default_contact_groups(
    group=["28", "29"]
)
```

## API Endpoints

The action provides standard API endpoints via `endpoints.py`:

- `GET /actions/{action_id}/groups` - List available contact groups
- `PUT /actions/{action_id}/groups/default` - Update default groups
- `GET /actions/{action_id}/status` - Get onboarding status

### Example: Update Default Groups

```http
PUT /api/actions/{action_id}/groups/default
Content-Type: application/json

{
  "groups": ["28", "29", "30"]
}
```

**Response:**

```json
{
  "default_groups": ["28", "29", "30"],
  "message": "Default groups updated successfully"
}
```

## Execution Flow

```
1. User sends first WhatsApp message
   ↓
2. InteractRouter processes interaction
   ↓
3. OnboardingInteractAction executes (weight: -100)
   ↓
4. Check: Is new user? Is WhatsApp? Has phone?
   ↓
5. YES: Subscribe to default groups
   ↓
6. Generate channels page link
   ↓
7. Add welcome directive
   ↓
8. PersonaAction processes directive
   ↓
9. User receives welcome message with link
```

## Channel-Specific Behavior

- **WhatsApp**: Full onboarding with group subscription
- **Web/Default**: Action skips execution (returns immediately)

This ensures onboarding only happens for WhatsApp users where phone numbers are available.

## New User Detection

The action relies on `visitor.new_user` flag, which is set by the framework when:

- No previous interactions exist for the user
- First conversation is being created
- User node is newly created in the graph

## Dependencies

- `resolv/resolv_api_action` - API integration for group subscriptions

## File Structure

```
resolv_onboarding_interact_action/
├── __init__.py                              # Package initialization
├── resolv_onboarding_interact_action.py     # Main action implementation
├── endpoints.py                             # API endpoints
├── info.yaml                                # Action metadata
└── README.md                                # This file
```

## Customization

### Custom Welcome Message

Update the prompt template in agent.yaml:

```yaml
prompt: "Welcome to Resolv! Click here to choose your notification preferences: {channels_page}"
```

### Additional Onboarding Steps

Extend the `execute` method:

```python
async def execute(self, visitor: InteractWalker) -> None:
    # ... existing checks ...

    # Subscribe to default groups
    for group in self.default_groups:
        await api.subscribe_user(...)

    # Custom: Send welcome email
    await self.send_welcome_email(subscriber_phone)

    # Custom: Log onboarding event
    await self.log_onboarding(subscriber_phone)

    # Get channels page and add directive
    channels_page = await api.get_channels_page(...)
    await visitor.add_directives([...])
```

### Dynamic Group Selection

Implement logic to select groups based on user attributes:

```python
# Determine groups based on user location or preferences
if user_location == "Georgetown":
    groups = ["28", "29"]
else:
    groups = ["30", "31"]

for group in groups:
    await api.subscribe_user(...)
```

## Error Handling

- Gracefully handles missing ResolvAPIAction
- Returns early if conditions aren't met
- Logs errors during group subscription
- Continues execution even if some groups fail

## Testing

Test scenarios:

- New WhatsApp user first interaction
- Existing user (should skip onboarding)
- Web channel user (should skip onboarding)
- Missing phone number (should skip onboarding)
- Multiple default groups subscription
- Channels page generation
- Welcome directive formatting
- Group subscription failures

## Known Issues

- None currently documented

## Performance Considerations

- Runs on every interaction (always_execute=True)
- Early return for non-new users (minimal overhead)
- Async group subscriptions (non-blocking)
- Cached API client (persistent session)

## Future Enhancements

1. **Onboarding Analytics**: Track onboarding success rates
2. **Custom Onboarding Flows**: Different flows for different user types
3. **Multi-Channel Support**: Extend beyond WhatsApp
4. **Onboarding Surveys**: Collect user preferences during onboarding
5. **Group Recommendations**: AI-powered group suggestions based on user profile
