# Channel Adapter System

This guide documents the channel adapter system for automatically delivering messages from the response bus to external destinations (WhatsApp, web, SMS, etc.).

## Overview

The channel adapter system provides a clean, automatic way to deliver messages published to the response bus to external communication channels. Channel adapters:

- **Auto-discover** when actions are registered
- **Auto-subscribe** to the response bus
- **Auto-receive** messages when published for their channel
- **Zero manual wiring** required

## Architecture

### Components

1. **ResponseBus**: Central message bus that manages message publishing and adapter subscriptions
2. **ChannelAdapter**: Base class for all channel adapters
3. **Action Integration**: Actions create and register adapters in their `on_register()` method

### How It Works

```
1. Action.on_register() creates ChannelAdapter instance
   ↓
2. Adapter.initialize() gets ResponseBus and registers itself
   ↓
3. ResponseBus maintains registry of adapters by channel
   ↓
4. When message is published with channel="whatsapp"
   ↓
5. ResponseBus automatically subscribes adapter to session (lazy subscription)
   ↓
6. Adapter.handle_message() is called
   ↓
7. Adapter.send_to_destination() delivers message to external API
```

### Key Features

- **Lazy Auto-Subscription**: Adapters are automatically subscribed to sessions when messages are published for their channel
- **Zero Manual Wiring**: No code in InteractWalker or elsewhere needs to manage adapters
- **Automatic Discovery**: ResponseBus automatically finds and uses registered adapters
- **Session Isolation**: Each adapter only receives messages for sessions it's subscribed to

## Implementing a Channel Adapter

### Step 1: Create the Adapter Class

Create a new adapter class that inherits from `ChannelAdapter`:

```python
from jvagent.action.response.channel_adapter import ChannelAdapter
from jvagent.action.response.message import ResponseMessage

class MyChannelAdapter(ChannelAdapter):
    """Adapter for MyChannel integration."""
    
    def __init__(
        self,
        channel: str = "mychannel",
        action: Any = None,
        response_bus: Optional[ResponseBus] = None,
    ):
        """Initialize adapter.
        
        Args:
            channel: Channel name (e.g., "mychannel")
            action: Your Action instance (for accessing config)
            response_bus: Optional ResponseBus (auto-set via initialize())
        """
        super().__init__(channel, response_bus)
        self.action = action  # Store action for accessing config
    
    async def handle_message(self, message: ResponseMessage) -> None:
        """Handle incoming message from response bus.
        
        This is called automatically when a message is published
        for this adapter's channel.
        
        Args:
            message: ResponseMessage object
        """
        # Check if adapter should handle this message
        if not self.should_handle(message):
            return
        
        # Only handle final and adhoc messages (not stream chunks)
        if message.message_type in ("adhoc", "final"):
            success = await self.send_to_destination(message)
            if success:
                message.mark_delivered()
    
    async def send_to_destination(self, message: ResponseMessage) -> bool:
        """Send message to external API.
        
        Args:
            message: ResponseMessage to send
            
        Returns:
            True if sent successfully, False otherwise
        """
        # Extract recipient from message metadata
        recipient = message.metadata.get("recipient")
        if not recipient:
            logger.warning(f"No recipient in message metadata")
            return False
        
        # Send to your external API
        try:
            # Your API call here
            # response = await my_api.send(recipient, message.content)
            return True
        except Exception as e:
            logger.error(f"Error sending message: {e}", exc_info=True)
            return False
```

### Step 2: Integrate with Your Action

In your Action class, create and initialize the adapter in `on_register()`:

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
        adapter = MyChannelAdapter(
            channel="mychannel",
            action=self,  # Pass action instance for config access
        )
        
        # Initialize the adapter (gets ResponseBus and registers itself)
        await adapter.initialize()
        
        # Store adapter instance for reference (optional)
        self._channel_adapter = adapter
```

### Step 3: Publish Messages

Messages are automatically delivered to adapters when published with the matching channel:

```python
# In an InteractAction or anywhere with access to response_bus
await visitor.response_bus.publish_message(
    session_id=session_id,
    content="Hello, world!",
    channel="mychannel",  # Must match adapter's channel
    message_type="final",
    metadata={"recipient": "user@example.com"}
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
        adapter = WhatsAppAdapter(
            channel="whatsapp",
            action=self,
        )
        await adapter.initialize()
        self._channel_adapter = adapter
```

### WhatsAppAdapter Implementation

```python
class WhatsAppAdapter(ChannelAdapter):
    def __init__(self, channel: str = "whatsapp", action: Any = None, ...):
        super().__init__(channel, response_bus)
        self.action = action
    
    async def handle_message(self, message: ResponseMessage) -> None:
        if not self.should_handle(message):
            return
        
        if message.message_type in ("adhoc", "final"):
            success = await self.send_to_destination(message)
            if success:
                message.mark_delivered()
    
    async def send_to_destination(self, message: ResponseMessage) -> bool:
        # Use self.action.api_url and self.action.api_key
        # Send to WhatsApp API
        ...
```

## API Reference

### ChannelAdapter Methods

#### `async def initialize() -> None`

Initializes the adapter by:
1. Getting ResponseBus instance from App
2. Subscribing to the response bus
3. Registering itself with ResponseBus for automatic session subscription

**Must be called** after instantiation (typically in action's `on_register()`).

#### `async def handle_message(message: ResponseMessage) -> None`

Called automatically when a message is published for this adapter's channel.

**Must be implemented** by subclasses.

#### `async def send_to_destination(message: ResponseMessage) -> bool`

Sends the message to the external API.

**Must be implemented** by subclasses.

#### `def should_handle(message: ResponseMessage) -> bool`

Checks if this adapter should handle the message. Default implementation checks if `message.channel == self.channel`.

Override if you need custom filtering logic.

#### `async def subscribe_to_session(session_id: str, receive_chunks: bool = False) -> None`

Manually subscribe to a specific session. Usually not needed - ResponseBus handles this automatically via lazy subscription.

### ResponseBus Methods

#### `async def register_channel_adapter(adapter: ChannelAdapter) -> None`

Registers a channel adapter with the response bus. Called automatically by `adapter.initialize()`.

#### `async def publish_message(...) -> ResponseMessage`

Publishes a message to the bus. Automatically:
- Subscribes adapters for the message's channel to the session (if not already subscribed)
- Delivers message to all subscribed adapters

## Message Types

Channel adapters receive different message types:

- **`adhoc`**: Standalone messages published explicitly
- **`final`**: Complete responses when an interaction is finalized
- **`stream_chunk`**: Individual chunks from streaming responses (only if `receive_chunks=True`)

By default, adapters only receive `adhoc` and `final` messages. Set `receive_chunks=True` in `subscribe_to_session()` to also receive stream chunks.

## Best Practices

1. **Pass Action Instance**: Always pass your action instance to the adapter so it can access configuration (API keys, URLs, etc.)

2. **Handle Errors Gracefully**: Always wrap API calls in try/except and return `False` on failure

3. **Check should_handle()**: Always check `should_handle()` in `handle_message()` to ensure the message is for your channel

4. **Mark Messages Delivered**: Call `message.mark_delivered()` after successfully sending

5. **Use Metadata**: Store recipient information and other delivery details in `message.metadata` when publishing

6. **Log Appropriately**: Use appropriate log levels (warning for recoverable errors, error for failures)

## Troubleshooting

### Adapter Not Receiving Messages

1. **Check channel name**: Ensure the channel in `publish_message()` matches the adapter's channel
2. **Verify initialization**: Make sure `adapter.initialize()` was called in `on_register()`
3. **Check message type**: Adapters only receive `adhoc` and `final` by default (not `stream_chunk`)

### Adapter Not Subscribing to Sessions

This should happen automatically. If not:
1. Check that `adapter.initialize()` was called successfully
2. Verify ResponseBus is available (check logs for warnings)
3. Ensure the adapter was registered (check ResponseBus logs)

### Messages Not Being Delivered

1. **Check `should_handle()`**: Verify the message channel matches
2. **Check `send_to_destination()`**: Ensure it's returning `True` on success
3. **Check logs**: Look for error messages in adapter logs
4. **Verify metadata**: Ensure recipient and other required data is in `message.metadata`
