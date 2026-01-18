"""WhatsApp Action Implementation."""
import logging
from typing import Any, Dict, Optional, Union

from jvagent.action.base import Action
from jvspatial.api.auth.api_key_service import APIKeyService
from jvspatial.api.auth.models import APIKey
from jvspatial.core.annotations import attribute
from jvspatial.core.context import GraphContext
from jvspatial.db import get_prime_database
from .whatsapp_adapter import WhatsAppAdapter
from .whatsapp_modules.wppconnect import WPPConnectAPI
from .whatsapp_modules.wwebjs_api import WWebJSAPI
from .webhook_auth import get_or_create_system_user

logger = logging.getLogger(__name__)


class WhatsAppAction(Action):
    """Action for WhatsApp integration using multiple providers."""

    provider: str = attribute(
        default="wppconnect",
        description="WhatsApp provider (wppconnect, ultramsg, ts-whatsapp)",
    )

    api_url: Optional[str] = attribute(default=None, description="WhatsApp API Endpoint URL")

    api_key: Optional[str] = attribute(default=None, description="WhatsApp API Key / Token")

    session: Optional[str] = attribute(default=None, description="WhatsApp session")

    token: Optional[str] = attribute(default=None, description="WhatsApp token")

    base_url: Optional[str] = attribute(
        default="http://localhost:8000", description="WhatsApp base URL"
    )

    webhook_url: Optional[str] = attribute(default=None, description="WhatsApp webhook URL")

    webhook_api_key_id: Optional[str] = attribute(
        default=None, description="ID of the API key used for webhook authentication"
    )

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

    async def on_reload(self) -> None:
        """Called when action is reloaded (e.g., after update).

        Reinitializes the WhatsApp channel adapter and ensures webhook URL
        and session registration are properly set up. This is critical for
        actions that were updated via --update flag.
        """
        # Reinitialize WhatsAppAdapter if not already initialized
        if not hasattr(self, "_channel_adapter") or self._channel_adapter is None:
            adapter = WhatsAppAdapter(action=self)
            await adapter.initialize()
            self._channel_adapter = adapter
        else:
            # Reinitialize existing adapter to ensure it's properly connected
            try:
                await self._channel_adapter.initialize()
            except Exception as e:
                logger.warning(
                    f"Error reinitializing WhatsAppAdapter during reload: {e}. Creating new instance."
                )
                adapter = WhatsAppAdapter(action=self)
                await adapter.initialize()
                self._channel_adapter = adapter

        # Ensure webhook URL is set and valid
        # This is critical as webhook_url might be None after an update
        if not self.webhook_url:
            logger.info("Webhook URL not set during reload, generating new one")
            # Generate webhook URL (will reuse existing if valid, or create new)
            await self.get_webhook_url(regenerate=False)
        else:
            # Verify webhook URL is still valid
            try:
                agent = await self.get_agent()
                agent_id = str(agent.id)
                expected_url_base = f"{self.base_url}/api/whatsapp/interact/webhook/{agent_id}"
                
                # Check if webhook URL is for the correct agent
                if not self.webhook_url.startswith(expected_url_base):
                    logger.warning(
                        f"Webhook URL agent mismatch during reload. Expected {expected_url_base}, "
                        f"got {self.webhook_url}. Regenerating."
                    )
                    await self.get_webhook_url(regenerate=True)
                elif self.webhook_api_key_id:
                    # Verify API key is still active
                    prime_db = get_prime_database()
                    context = GraphContext(database=prime_db)
                    existing_key = await context.get(APIKey, self.webhook_api_key_id)
                    if not existing_key or not existing_key.is_active:
                        logger.warning(
                            f"API key {self.webhook_api_key_id} is inactive during reload. Regenerating webhook URL."
                        )
                        await self.get_webhook_url(regenerate=True)
            except Exception as e:
                logger.warning(
                    f"Error verifying webhook URL during reload: {e}. Regenerating webhook URL."
                )
                await self.get_webhook_url(regenerate=True)

        # Re-register session to ensure it's properly configured
        # This ensures the session is registered with the current webhook URL
        try:
            await self.register_session()
        except Exception as e:
            logger.error(
                f"Error re-registering session during reload: {e}",
                exc_info=True,
            )
            # Don't raise - allow action to continue even if session registration fails
            # The session can be registered later when needed

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

    async def get_webhook_url(
        self, allowed_ip: Optional[str] = None, regenerate: bool = False
    ) -> str:
        """Generate secure webhook URL with API key authentication.

        Creates or retrieves an API key for webhook authentication and returns
        the full webhook URL with the API key embedded as a query parameter.

        Args:
            allowed_ip: Optional IP address to whitelist for this API key.
                       If None, all IPs are allowed.
            regenerate: If True, force regeneration of API key even if one exists.
                       If False, reuse existing webhook_url if it's already set and valid.

        Returns:
            Full webhook URL with embedded API key (e.g.,
            "http://localhost:8000/api/whatsapp/interact/webhook/{agent_id}?api_key=jv_...")

        Raises:
            Exception: If API key generation fails or agent cannot be retrieved
        """
        agent = await self.get_agent()
        agent_id = str(agent.id)
        expected_url_base = f"{self.base_url}/api/whatsapp/interact/webhook/{agent_id}"

        # Check if we can reuse existing webhook_url
        if not regenerate and self.webhook_url and "?api_key=" in self.webhook_url:
            # Verify the URL is for the correct agent
            if self.webhook_url.startswith(expected_url_base):
                # Check if we need to update IP restrictions
                if self.webhook_api_key_id:
                    try:
                        prime_db = get_prime_database()
                        context = GraphContext(database=prime_db)
                        existing_key = await context.get(APIKey, self.webhook_api_key_id)
                        if existing_key and existing_key.is_active:
                            # Check if IP restrictions match
                            if allowed_ip is None:
                                # No IP restriction requested, check if current key has none
                                if not existing_key.allowed_ips:
                                    # Can reuse existing URL
                                    logger.debug("Reusing existing webhook URL")
                                    return self.webhook_url
                            elif allowed_ip in existing_key.allowed_ips:
                                # IP matches, can reuse
                                logger.debug("Reusing existing webhook URL with matching IP")
                                return self.webhook_url
                            # IP restriction changed, need to regenerate
                            logger.debug("IP restriction changed, regenerating API key")
                            regenerate = True
                        else:
                            # Key is inactive, need to regenerate
                            logger.debug("Existing API key is inactive, regenerating")
                            regenerate = True
                    except Exception as e:
                        logger.warning(
                            f"Error checking existing API key {self.webhook_api_key_id}: {e}. Regenerating."
                        )
                        regenerate = True
                else:
                    # No key ID stored, but URL exists - might be from before upgrade
                    # Regenerate to ensure we have proper key tracking
                    logger.debug("No API key ID stored, regenerating for proper tracking")
                    regenerate = True
            else:
                # Agent ID changed, need to regenerate
                logger.debug("Agent ID changed, regenerating webhook URL")
                regenerate = True

        # Get or create system service user
        system_user_id = await get_or_create_system_user()

        # Set up API key service
        prime_db = get_prime_database()
        context = GraphContext(database=prime_db)
        api_key_service = APIKeyService(context=context)

        # Revoke old key if regenerating and one exists
        if regenerate and self.webhook_api_key_id:
            try:
                old_key = await context.get(APIKey, self.webhook_api_key_id)
                if old_key:
                    old_key.is_active = False
                    old_key._graph_context = context
                    await context.save(old_key)
                    logger.info(f"Revoked old API key: {self.webhook_api_key_id}")
            except Exception as e:
                logger.warning(f"Error revoking old API key: {e}")

        # Generate new API key
        key_name = f"WhatsApp Webhook - {agent.name}"
        allowed_ips = [allowed_ip] if allowed_ip else []
        allowed_endpoints = ["/api/whatsapp/interact/webhook/*"]

        plaintext_key, api_key = await api_key_service.generate_key(
            user_id=system_user_id,
            name=key_name,
            permissions=["webhook:whatsapp"],
            expires_in_days=None,  # No expiration
            allowed_ips=allowed_ips,
            allowed_endpoints=allowed_endpoints,
            key_prefix="jv_",
        )

        # Store API key ID in action
        self.webhook_api_key_id = api_key.id

        # Construct webhook URL with API key
        webhook_url = f"{expected_url_base}?api_key={plaintext_key}"

        # Store the webhook URL in the action
        self.webhook_url = webhook_url
        # Ensure the action has the correct context for saving
        if not hasattr(self, "_graph_context") or self._graph_context is None:
            self._graph_context = context
        await self.save()

        logger.info(
            f"Generated new API key for WhatsApp webhook: {api_key.id} "
            f"(prefix: {api_key.key_prefix})"
        )

        return webhook_url

    async def register_session(self) -> Dict[str, Any]:
        agent = await self.get_agent()

        # set agent name as session if not set
        if not self.session:
            self.session = agent.name
            # Save session if it was just set
            await self.save()

        # create webhook url if not set
        if not self.webhook_url:
            await self.api().close_session()

            # Generate secure webhook URL with API key
            self.webhook_url = await self.get_webhook_url()

        # register session
        return await self.api().register_session(
            webhook_url=self.webhook_url,
            wait_qr_code=True,
            auto_register=True,
        )
