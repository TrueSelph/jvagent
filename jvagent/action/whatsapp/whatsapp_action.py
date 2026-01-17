"""WhatsApp Action Implementation."""
import logging
from typing import Any, Dict, Optional, Union

from jvagent.action.base import Action
from jvspatial.core.annotations import attribute
from .whatsapp_adapter import WhatsAppAdapter
from .whatsapp_modules.wppconnect import WPPConnectAPI
from .whatsapp_modules.wwebjs_api import WWebJSAPI

logger = logging.getLogger(__name__)


class WhatsAppAction(Action):
    """Action for WhatsApp integration using multiple providers."""

    provider: str = attribute(
        default="wppconnect", description="WhatsApp provider (wppconnect, ultramsg, ts-whatsapp)"
    )

    api_url: Optional[str] = attribute(default=None, description="WhatsApp API Endpoint URL")

    api_key: Optional[str] = attribute(default=None, description="WhatsApp API Key / Token")

    session: Optional[str] = attribute(default=None, description="WhatsApp session")

    token: Optional[str] = attribute(default=None, description="WhatsApp token")

    base_url: Optional[str] = attribute(
        default="http://localhost:8000", description="WhatsApp base URL"
    )

    webhook_url: Optional[str] = attribute(default=None, description="WhatsApp webhook URL")

    request_timeout: int = attribute(default=30, description="WhatsApp request timeout in seconds")

    chunk_length: int = attribute(default=2000, description="WhatsApp chunk length")

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

        # Register session
        await self.register_session()

    async def on_enable(self) -> None:
        """Called when action is enabled."""
        pass

    async def send_message(self, to: str, message: str) -> Dict[str, Any]:
        """Send a WhatsApp message."""
        # Logic to delegate to specific provider will go here
        return {"status": "sent", "to": to, "message": message, "provider": self.provider}

    async def set_typing(self, phone: str, value: bool = True) -> None:
        """Set or clear typing status for a phone number."""
        if hasattr(self, "_channel_adapter"):
            await self._channel_adapter.set_typing(phone, value)
        else:
            # Fallback if adapter not yet initialized
            try:
                await self.api().set_typing_status(phone=phone, value=value)
            except Exception as e:
                logger.warning(f"WhatsAppAction: Failed to set typing status without adapter: {e}")

    def api(self) -> Union[WPPConnectAPI, WWebJSAPI]:
        if self.provider == "wppconnect":
            return WPPConnectAPI(
                api_url=self.api_url,
                session=self.session,
                token=self.token,
                secret_key=self.api_key,
                timeout=self.request_timeout,
            )
        elif self.provider == "wwebjs":
            return WWebJSAPI(
                api_url=self.api_url,
                session=self.session,
                token=self.token,
                secret_key=self.api_key,
                timeout=self.request_timeout,
            )
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")

    async def register_session(self) -> Dict[str, Any]:
        agent = await self.get_agent()

        # set agent name as session if not set
        if not self.session:
            self.session = agent.name

        # create webhook url if not set
        if not self.webhook_url:
            await self.api().close_session()

            self.webhook_url = f"{self.base_url}/api/whatsapp/interact/webhook/{str(agent.id)}"

        # register session
        return await self.api().register_session(
            webhook_url=self.webhook_url,
            wait_qr_code=True,
            auto_register=True,
        )
