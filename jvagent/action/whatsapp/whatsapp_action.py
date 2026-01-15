"""WhatsApp Action Implementation."""

from typing import Any, Dict, Optional, Union

from jvagent.action.base import Action
from jvspatial.core.annotations import attribute
from .whatsapp_adapter import WhatsAppAdapter
from .whatsapp_modules.wppconnect import WPPConnectAPI
from .whatsapp_modules.wwebjs_api import WWebJSAPI


class WhatsAppAction(Action):
    """Action for WhatsApp integration using multiple providers."""

    provider: str = attribute(
        default="wppconnect",
        description="WhatsApp provider (wppconnect, ultramsg, ts-whatsapp)"
    )

    api_url: Optional[str] = attribute(
        default=None,
        description="WhatsApp API Endpoint URL"
    )

    api_key: Optional[str] = attribute(
        default=None,
        description="WhatsApp API Key / Token"
    )

    session: Optional[str] = attribute(
        default=None,
        description="WhatsApp session"
    )

    secret_key: Optional[str] = attribute(
        default=None,
        description="WhatsApp API Secret Key"
    )

    chunk_length: int = attribute(
        default=2000,
        description="WhatsApp chunk length"
    )
    
    async def on_register(self) -> None:
        """Called when action is registered.
        
        Creates and initializes the WhatsApp channel adapter for automatic
        message delivery via the response bus.
        """
        # Create WhatsAppAdapter instance
        adapter = WhatsAppAdapter(action=self)
        
        # Initialize the adapter (gets ResponseBus and registers itself)
        await adapter.initialize()
        
        # Store adapter instance for reference
        self._channel_adapter = adapter

    async def on_enable(self) -> None:
        """Called when action is enabled."""
        self.api()

    async def send_message(self, to: str, message: str) -> Dict[str, Any]:
        """Send a WhatsApp message."""
        # Logic to delegate to specific provider will go here
        return {"status": "sent", "to": to, "message": message, "provider": self.provider}


    def api(self) -> Union[WPPConnectAPI, WWebJSAPI]:

        if self.provider == "wppconnect":
            return WPPConnectAPI(
                api_url=self.api_url,
                session=self.session,
                token=self.api_key,
                secret_key=self.secret_key
            )
        elif self.provider == "wwebjs":
            return WWebJSAPI(
                api_url=self.api_url,
                session=self.session,
                token=self.api_key,
                secret_key=self.secret_key
            )
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")
