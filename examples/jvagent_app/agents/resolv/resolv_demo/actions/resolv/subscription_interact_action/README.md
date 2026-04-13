# SubscriptionInteractAction

User subscription management action that provides access to channel subscription preferences in the Resolv Incident Management System.

## Overview

The `SubscriptionInteractAction` enables users to manage their channel subscriptions by providing a personalized subscription page link. Users can subscribe to or unsubscribe from notification channels and groups based on their preferences.

## Features

- **Subscription Management**: Provides access to subscription preferences page
- **Personalized Links**: Generates user-specific subscription page URLs
- **Intent-Based Routing**: Triggered when users express subscription management intent
- **Channel Flexibility**: Supports multiple channels and groups
- **Simple Integration**: Works seamlessly with ResolvAPIAction

## Architecture

Inherits from `InteractAction` and uses routing anchors to detect when users want to manage their subscriptions. The action generates a personalized subscription page link and passes it to PersonaAction via directives.

## Configuration

### Agent Configuration (agent.yaml)

```yaml
- action: resolv/subscription_interact_action
  context:
    enabled: true
    description: "Subscription management action for channel preferences"
    weight: -50  # Runs before fallback actions
    prompt: |
      Inform the user they can subscribe and unsubscribe anytime using the link below:
      {subscription_page}
```

### Configuration Properties

- **prompt** (str): Message template with `{subscription_page}` placeholder
- **anchors** (List[str]): Routing anchors for InteractRouter
- **weight** (int): Execution order (default: 0)

### Routing Anchors

The action publishes anchors for InteractRouter routing:

- "The user wants to subscribe or unsubscribe from a channel or group"
- "The user wants to change their subscription preferences"
- "The user wants to know more about the subscription options"

## Execution Logic

### Process Flow

1. **User Information Retrieval**
   - Gets user from interaction
   - Extracts display name and phone number

2. **API Integration**
   - Retrieves ResolvAPIAction instance
   - Calls `get_channels_page()` with user details

3. **Directive Generation**
   - Formats prompt template with subscription page URL
   - Adds directive for PersonaAction to process

## Methods

### execute

Main execution method that handles subscription management.

```python
async def execute(self, visitor: InteractWalker) -> None:
    # Get user information
    # Retrieve subscription page link
    # Add directive with formatted message
```

**Args:**
- `visitor` (InteractWalker): Walker instance containing interaction context

## API Integration

Integrates with `ResolvAPIAction` for:

- `get_channels_page()` - Generate personalized subscription page link

## Usage

### Automatic Triggering

The action is triggered when users express subscription management intent:

```
User: I want to change my notification settings
[InteractRouter routes to SubscriptionInteractAction]
- Retrieves user information
- Generates subscription page link
- Adds directive

Agent: You can subscribe and unsubscribe anytime using this link:
       https://app.resolv-ims.com/subscriptions/...
```

### Example Interactions

#### Subscription Request
```
User: How do I manage my subscriptions?
Agent: You can subscribe and unsubscribe anytime using the link below:
       https://app.resolv-ims.com/subscriptions/5926431530
```

#### Unsubscribe Request
```
User: I want to unsubscribe from some channels
Agent: You can subscribe and unsubscribe anytime using the link below:
       https://app.resolv-ims.com/subscriptions/5926431530
```

#### Subscription Information
```
User: Tell me about subscription options
Agent: You can subscribe and unsubscribe anytime using the link below:
       https://app.resolv-ims.com/subscriptions/5926431530
```

## Execution Flow

```
1. User expresses subscription management intent
   ↓
2. InteractRouter routes to SubscriptionInteractAction
   ↓
3. Action retrieves user information
   ↓
4. Action calls ResolvAPIAction.get_channels_page()
   ↓
5. Action formats prompt with subscription page URL
   ↓
6. Action adds directive to visitor
   ↓
7. PersonaAction incorporates directive into response
   ↓
8. User receives message with subscription link
```

## Subscription Page Features

The subscription page allows users to:

- View all available channels and groups
- See current subscription status
- Subscribe to new channels
- Unsubscribe from existing channels
- Update notification preferences
- Manage contact information

## Dependencies

- `resolv/resolv_api_action` - API integration for subscription page generation

## File Structure

```
subscription_interact_action/
├── __init__.py                          # Package initialization
├── subscription_interact_action.py      # Main action implementation
├── endpoints.py                         # API endpoints (if needed)
├── info.yaml                            # Action metadata
└── README.md                            # This file
```

## Customization

### Custom Prompt Message

Update the prompt template in agent.yaml:

```yaml
prompt: |
  Manage your notification preferences here:
  {subscription_page}

  You can update your subscriptions anytime!
```

### Additional User Information

Extend the `execute` method to include more user context:

```python
async def execute(self, visitor: InteractWalker) -> None:
    # Get user information
    interaction = visitor.interaction
    user = await interaction.get_user()
    subscriber_display_name = user.get_display_name() if user else "user"
    subscriber_phone = user.user_id
    subscriber_email = user.email  # Additional field

    # Get API action
    api = await self.get_action("ResolvAPIAction")

    # Get channels page with additional context
    subscription_page = await api.get_channels_page(
        subscriber_phone,
        subscriber_display_name,
        email=subscriber_email
    )

    if subscription_page:
        await visitor.add_directives([
            self.prompt.format(subscription_page=subscription_page)
        ])
```

### Multi-Language Support

Add language-specific prompts:

```python
prompts = {
    "en": "You can subscribe and unsubscribe anytime using the link below:\n{subscription_page}",
    "es": "Puede suscribirse y cancelar la suscripción en cualquier momento usando el enlace a continuación:\n{subscription_page}"
}

# Detect user language
user_language = user.get_language() or "en"
prompt = prompts.get(user_language, prompts["en"])

await visitor.add_directives([
    prompt.format(subscription_page=subscription_page)
])
```

### Conditional Messaging

Customize message based on user's current subscription status:

```python
# Get user's current subscriptions
current_subscriptions = await api.get_user_subscriptions(subscriber_phone)

if not current_subscriptions:
    message = f"You're not subscribed to any channels yet. Get started here:\n{subscription_page}"
else:
    message = f"Manage your {len(current_subscriptions)} subscriptions here:\n{subscription_page}"

await visitor.add_directives([message])
```

## Error Handling

- Gracefully handles missing ResolvAPIAction
- Returns silently if subscription page generation fails
- Handles missing user information
- Logs errors for debugging

## Testing

Test scenarios:

- User requests subscription management
- User asks about subscription options
- User wants to unsubscribe
- Missing user information
- API failure handling
- Prompt formatting with subscription link
- Multi-language support (if implemented)

## Known Issues

- None currently documented

## Performance Considerations

- Lightweight execution (single API call)
- Async subscription page generation
- Cached API client (persistent session)
- Minimal overhead on routing

## Future Enhancements

1. **Inline Subscription Management**: Allow subscription changes directly in chat
2. **Subscription Analytics**: Track subscription patterns and preferences
3. **Smart Recommendations**: Suggest relevant channels based on user activity
4. **Batch Operations**: Subscribe/unsubscribe from multiple channels at once
5. **Subscription Previews**: Show channel descriptions before subscribing
6. **Notification Frequency**: Allow users to set notification frequency per channel
7. **Temporary Subscriptions**: Support time-limited subscriptions

## Support

For issues or questions:
- Review the [jvagent documentation](../../../../../../../README.md)
- Check the [architecture documentation](../../../../../../docs/architecture.md)
- Review the [ResolvAPIAction README](../resolv_api_action/README.md) for API integration details
