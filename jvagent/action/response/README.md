# Channel Adapter System

This guide documents the channel adapter system for automatically delivering messages from the response bus to external destinations (WhatsApp, web, SMS, etc.).

## Overview

The channel adapter system provides a clean, automatic way to deliver messages published to the response bus to external communication channels. Channel adapters:

- **Auto-register** when actions are registered
- **Auto-receive** adhoc messages when published for their channel
- **Zero manual wiring** required

## Architecture

### Components

1. **ResponseBus**: Central message bus that routes adhoc messages directly to registered adapters
2. **ChannelAdapter**: Base class for all channel adapters
3. **Action Integration**: Actions create and register adapters in their `on_register()` method (for new registrations) and `on_startup()` method (for app restarts)

### How It Works

**Initial Registration:**
```
1. Action.on_register() creates ChannelAdapter instance
   ↓
2. Adapter.initialize() gets ResponseBus and registers itself
   ↓
3. ResponseBus stores single adapter per channel (replaces existing if any)
```

**App Restart:**
```
1. App starts and loads actions from database
   ↓
2. Action.on_startup() is called for all loaded actions
   ↓
3. Adapter.initialize() re-registers adapter with ResponseBus
   ↓
4. Adapter ready to receive messages
```

**Message Delivery:**
```
1. When adhoc message is published with channel="whatsapp"
   ↓
2. ResponseBus directly calls adapter.send(message)
   ↓
3. Adapter sends message to external API
```

### Key Features

- **Single Adapter Per Channel**: Only one adapter per channel is maintained (serverless-friendly)
- **Direct Routing**: Adapters receive messages directly - no session subscriptions needed
- **Zero Manual Wiring**: No code in InteractWalker or elsewhere needs to manage adapters
- **Automatic Discovery**: ResponseBus automatically routes messages to registered adapters
- **Simple Interface**: Single `send()` method to implement
- **Serverless-Safe**: Adapters are replaced on registration, preventing orphaned instances

## Implementing a Channel Adapter

### Step 1: Create the Adapter Class

Create a new adapter class that inherits from `ChannelAdapter`:

```python
from jvagent.action.response.channel_adapter import ChannelAdapter
from jvagent.action.response.message import ResponseMessage

class MyChannelAdapter(ChannelAdapter):
    """Adapter for MyChannel integration."""
    
    def __init__(self, action: Any = None):
        """Initialize adapter.
        
        Args:
            action: Your Action instance (for accessing config)
        """
        super().__init__(channel="mychannel")
        self.action = action  # Store action for accessing config
    
    async def send(self, message: ResponseMessage) -> bool:
        """Send message to external API.
        
        This method is called by ResponseBus when an adhoc message is published
        for this adapter's channel.
        
        Args:
            message: ResponseMessage to send (contains user_id, session_id, content, etc.)
            
        Returns:
            True if sent successfully, False otherwise
        """
        # Extract recipient from message.user_id (no database queries needed!)
        if not message.user_id:
            logger.error(f"Cannot send message {message.id} - no user_id in message")
            return False
        
        # Send to your external API
        try:
            # Your API call here using message.user_id
            # response = await my_api.send(message.user_id, message.content)
            return True
        except Exception as e:
            logger.error(f"Error sending message: {e}", exc_info=True)
            return False
```

### Step 2: Integrate with Your Action

In your Action class, create and initialize the adapter in both `on_register()` and `on_startup()`:

```python
from jvagent.action.base import Action
from .my_channel_adapter import MyChannelAdapter

class MyChannelAction(Action):
    """Action for MyChannel integration."""
    
    api_url: Optional[str] = attribute(default=None)
    api_key: Optional[str] = attribute(default=None)
    
    async def on_register(self) -> None:
        """Called when action is registered.
        
        Creates and initializes the channel adapter for automatic
        message delivery via the response bus.
        """
        # Create adapter instance with action reference
        adapter = MyChannelAdapter(action=self)
        
        # Initialize the adapter (gets ResponseBus and registers itself)
        if await adapter.initialize():
            # Adapter is now stored in ResponseBus registry, no need for private reference
            pass
    
    async def on_startup(self) -> None:
        """Called when app starts and action is loaded from database.
        
        Re-initializes the channel adapter when the app restarts.
        This ensures adapters work correctly after app restarts.
        """
        # Only initialize if action is enabled and configured
        if not self.enabled:
            return
        
        if not self.is_configured():
            return
        
        # Reinitialize adapter (create new instance for clean state)
        adapter = MyChannelAdapter(action=self)
        if await adapter.initialize():
            logger.info(f"MyChannelAdapter initialized on app startup for channel '{adapter.channel}'")
        else:
            logger.error("MyChannelAdapter initialization failed on startup. Messages will NOT be delivered.")
```

### Step 3: Publish Messages

Messages are automatically delivered to adapters when published with the matching channel:

```python
# In an InteractAction or anywhere with access to response_bus
await visitor.response_bus.publish_message(
    session_id=session_id,
    content="Hello, world!",
    channel="mychannel",  # Must match adapter's channel
    message_type="adhoc",  # Only adhoc messages are routed to adapters
    user_id="user@example.com"  # User identifier (recipient) - required for channel adapters
)
```

## Example: WhatsApp Adapter

See `jvagent/action/whatsapp/whatsapp_adapter.py` for a complete implementation example.

### WhatsAppAction Integration

```python
class WhatsAppAction(Action):
    api_url: Optional[str] = attribute(default=None)
    api_key: Optional[str] = attribute(default=None)
    
    async def on_register(self) -> None:
        """Called when action is first registered."""
        adapter = WhatsAppAdapter(action=self)
        if await adapter.initialize():
            # Adapter is now stored in ResponseBus registry, no need for private reference
            pass
    
    async def on_startup(self) -> None:
        """Called when app starts and action is loaded from database.
        
        Re-initializes the channel adapter when the app restarts.
        """
        if not self.enabled or not self.is_configured():
            return
        adapter = WhatsAppAdapter(action=self)
        if await adapter.initialize():
            logger.info(f"WhatsAppAdapter initialized on app startup for channel '{adapter.channel}'")
        else:
            logger.error("WhatsAppAdapter initialization failed on startup. Messages will NOT be delivered.")
```

### WhatsAppAdapter Implementation

```python
class WhatsAppAdapter(ChannelAdapter):
    def __init__(self, action: Any = None):
        super().__init__(channel="whatsapp")
        self.action = action
    
    async def send(self, message: ResponseMessage) -> bool:
        # Use message.user_id directly (no database queries needed!)
        if not message.user_id:
            return False
        
        # Use self.action.api_url and self.action.api_key
        # Send to WhatsApp API using message.user_id
        ...
```

## API Reference

### ChannelAdapter Methods

#### `async def initialize() -> bool`

Initializes the adapter by:
1. Getting ResponseBus instance from App
2. Registering itself with ResponseBus

**Must be called** after instantiation. Typically called in:
- `on_register()` - When action is first registered
- `on_startup()` - When app restarts and action is loaded from database

Returns `True` if initialization succeeded, `False` otherwise.

#### `async def send(message: ResponseMessage) -> bool`

Sends the message to the external API. This method is called by ResponseBus when an adhoc message is published for this adapter's channel.

**Must be implemented** by subclasses.

Returns `True` if message was sent successfully, `False` otherwise.

### ResponseBus Methods

#### `async def register_channel_adapter(adapter: ChannelAdapter) -> None`

Registers a channel adapter with the response bus. Replaces any existing adapter for the same channel (single adapter per channel). This ensures serverless-friendly behavior where adapters are replaced on cold starts. Called automatically by `adapter.initialize()`.

#### `async def publish_message(...) -> ResponseMessage`

Publishes a message to the bus. For adhoc messages, automatically routes to registered adapters for the message's channel.

**Parameters:**
- `session_id`: Session identifier (required)
- `content`: Message content (required)
- `channel`: Target communication channel (default: "default")
- `message_type`: Type of message - "adhoc", "stream_chunk", or "final" (default: "adhoc")
- `interaction_id`: Parent interaction ID (optional)
- `user_id`: User identifier (recipient) - **required for channel adapters** (optional)
- `metadata`: Additional metadata (optional)
- `message_id`: Optional message ID to reuse (optional)

## Message Types

Channel adapters only receive **`adhoc`** messages. ResponseBus routes adhoc messages directly to adapters when published with a matching channel.

- **`adhoc`**: Standalone messages published explicitly - routed to channel adapters
- **`final`**: Complete responses when an interaction is finalized - not routed to adapters
- **`stream_chunk`**: Individual chunks from streaming responses - not routed to adapters

## Best Practices

1. **Pass Action Instance**: Always pass your action instance to the adapter so it can access configuration (API keys, URLs, etc.)

2. **Don't Store Adapter References**: Don't store adapter references on Action instances. The adapter is stored in ResponseBus and can be accessed via `get_adapter()` helper method if needed.

3. **Handle Errors Gracefully**: Always wrap API calls in try/except and return `False` on failure

4. **Return Boolean**: Always return `True` on success and `False` on failure from `send()`

5. **Pass user_id**: Always pass `user_id` parameter when publishing adhoc messages for channel adapters. The user_id is included directly in the ResponseMessage object, eliminating the need for database queries in adapters.

6. **No Database Queries**: Adapters should use `message.user_id` directly - never query the database for recipient information. This ensures low latency and loose coupling.

7. **Log Appropriately**: Use appropriate log levels (warning for recoverable errors, error for failures)

7. **Serverless Considerations**: The single adapter per channel design ensures that adapters are automatically replaced on registration, preventing orphaned instances in serverless environments

## Troubleshooting

### Adapter Not Receiving Messages

1. **Check channel name**: Ensure the channel in `publish_message()` matches the adapter's channel
2. **Verify initialization**: Make sure `adapter.initialize()` was called and returned `True` in `on_register()` or `on_startup()`
3. **Check message type**: Only `adhoc` messages are routed to adapters
4. **App restart**: If adapter worked before restart but not after, ensure `on_startup()` is implemented to re-initialize the adapter

### Messages Not Being Delivered

1. **Check `send()` return value**: Ensure it's returning `True` on success
2. **Check logs**: Look for error messages in adapter logs
3. **Verify user_id**: Ensure `user_id` parameter is passed when publishing adhoc messages. Adapters require `message.user_id` to be set.
4. **Verify initialization**: Check that adapter was successfully initialized
5. **Check for errors**: ResponseBus now properly awaits adapter.send() and logs errors immediately
