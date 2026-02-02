"""WhatsApp Action Implementation."""
import logging
import os
from typing import Any, Dict, Optional, Union

from jvagent.action.base import Action
from jvspatial.api.auth.api_key_service import APIKeyService
from jvspatial.api.auth.models import APIKey
from jvspatial.core.annotations import attribute
from jvspatial.core.context import GraphContext
from jvspatial.db import get_prime_database
from jvspatial.exceptions import ValidationError, DatabaseError
from .whatsapp_adapter import WhatsAppAdapter
from .whatsapp_filter import WhatsAppFilter
from .modules.wppconnect import WPPConnectAPI
from .modules.wwebjs_api import WWebJSAPI
from .modules.ultramsg import UltraMsgAPI
from .webhook_auth import get_or_create_system_user

logger = logging.getLogger(__name__)


class WhatsAppAction(Action):
    """Action for WhatsApp integration using multiple providers.
    
    This action is optional and will gracefully skip initialization if the
    required environment variables (WHATSAPP_API_URL, WHATSAPP_API_KEY, etc.)
    are not configured. When unconfigured, the action will remain inactive
    but will not cause errors during agent startup.
    """

    provider: str = attribute(
        default="wppconnect",
        description="WhatsApp provider (wppconnect, ultramsg, ts-whatsapp, wwebjs)",
        pattern=r"^(wppconnect|ultramsg|ts-whatsapp|wwebjs)$"
    )

    # Optional configuration fields - no strict validators to allow empty/unconfigured state
    # Validation is done in is_configured() and healthcheck() methods
    api_url: Optional[str] = attribute(
        default=None, 
        description="WhatsApp API Endpoint URL (e.g., https://api.whatsapp.example.com)"
    )

    api_key: Optional[str] = attribute(
        default=None, 
        description="WhatsApp API Key / Token"
    )

    session: Optional[str] = attribute(
        default=None, 
        description="WhatsApp session identifier",
        max_length=100
    )

    token: Optional[str] = attribute(
        default=None, 
        description="WhatsApp token (alternative to api_key for some providers)"
    )

    base_url: Optional[str] = attribute(
        default=None, 
        description="Application base URL for webhook generation (APP_BASE_URL env var, e.g., https://myapp.example.com)"
    )

    webhook_url: Optional[str] = attribute(
        default=None, 
        description="WhatsApp webhook URL (auto-generated if not provided)"
    )

    webhook_api_key_id: Optional[str] = attribute(
        default=None, 
        description="ID of the API key used for webhook authentication"
    )

    request_timeout: int = attribute(
        default=60, 
        description="WhatsApp request timeout in seconds",
        ge=1,
        le=300
    )

    chunk_length: int = attribute(
        default=4000, 
        description="WhatsApp chunk length",
        ge=100,
        le=10000
    )

    media_batch_window: float = attribute(
        default=2.5,
        description="Time window in seconds to batch multiple media messages together",
        ge=0.1,
        le=30.0
    )

    stt_action: Optional[str] = attribute(
        default="STTAction",
        description="Label or Class used to transcribe voice messages or audio files",
        min_length=1
    )

    tts_action: Optional[str] = attribute(
        default="TTSAction",
        description="Label or Class used to convert text to speech",
        min_length=1
    )

    # Internal state tracking (not persisted)
    _session_registration_done: bool = False

    # action configuration
    
    def _apply_env_defaults(self) -> None:
        """Apply environment variable defaults for missing configuration.
        
        Sets the following from environment variables if not already configured:
        - api_url from WHATSAPP_API_URL
        - api_key from WHATSAPP_API_KEY
        - base_url from APP_BASE_URL
        
        This allows users to set these values once in their .env file
        instead of configuring them per-action in agent.yaml.
        """
        # WhatsApp API URL
        if not self.api_url or not self.api_url.strip():
            env_api_url = os.environ.get("WHATSAPP_API_URL", "").strip()
            if env_api_url:
                self.api_url = env_api_url
                logger.debug(f"Using WHATSAPP_API_URL from environment: {env_api_url}")
        
        # WhatsApp API Key
        if not self.api_key or not self.api_key.strip():
            env_api_key = os.environ.get("WHATSAPP_API_KEY", "").strip()
            if env_api_key:
                self.api_key = env_api_key
                logger.debug("Using WHATSAPP_API_KEY from environment")
        
        # Application Base URL
        if not self.base_url or not self.base_url.strip():
            env_base_url = os.environ.get("APP_BASE_URL", "").strip()
            if env_base_url:
                self.base_url = env_base_url
                logger.debug(f"Using APP_BASE_URL from environment: {env_base_url}")
    
    def is_configured(self) -> bool:
        """Check if the WhatsApp action has required configuration.
        
        Required configuration:
        - api_url: WhatsApp API endpoint URL
        - api_key: WhatsApp API authentication key
        - base_url: Application base URL for webhook generation
        
        Returns:
            True if required configuration is present and valid, False otherwise.
        """
        # Check for required fields - must be non-empty strings
        if not self.api_url or not self.api_url.strip():
            return False
        if not self.api_key or not self.api_key.strip():
            return False
        if not self.base_url:
            return False
        
        # Validate URL formats
        if not self.api_url.startswith(("http://", "https://")):
            return False
        if not self.base_url.startswith(("http://", "https://")):
            return False
            
        return True
    
    def get_configuration_status(self) -> Dict[str, Any]:
        """Get detailed configuration status.
        
        Returns:
            Dict with configuration status and any missing/invalid fields.
        """
        issues = []
        
        if not self.api_url or not self.api_url.strip():
            issues.append("api_url (WHATSAPP_API_URL) is not configured")
        elif not self.api_url.startswith(("http://", "https://")):
            issues.append("api_url must be a valid HTTP/HTTPS URL")
            
        if not self.api_key or not self.api_key.strip():
            issues.append("api_key (WHATSAPP_API_KEY) is not configured")
            
        if not self.base_url:
            issues.append("base_url (APP_BASE_URL) is not configured - required for webhook generation")
        elif not self.base_url.startswith(("http://", "https://")):
            issues.append("base_url must be a valid HTTP/HTTPS URL")
            
        return {
            "configured": len(issues) == 0,
            "issues": issues,
            "provider": self.provider,
            "api_url_set": bool(self.api_url and self.api_url.strip()),
            "api_key_set": bool(self.api_key and self.api_key.strip()),
            "session_set": bool(self.session and self.session.strip()),
            "base_url_set": bool(self.base_url),
        }
    
    async def on_register(self) -> None:
        """Called when action is registered.

        Performs initial validation and configuration checks.
        Session registration is performed lazily on first use (e.g., first webhook)
        for Lambda and long-running server compatibility.
        
        If the action is not properly configured (missing API URL, API key, etc.),
        it will log a warning and skip initialization gracefully. The action will
        remain inactive but will not cause errors during agent startup.
        """
        # Apply environment variable defaults (e.g., APP_BASE_URL)
        self._apply_env_defaults()
        
        # Check if action is configured
        if not self.is_configured():
            config_status = self.get_configuration_status()
            issues = config_status.get("issues", [])
            logger.debug(
                f"WhatsApp action not configured. "
                f"Missing/invalid: {'; '.join(issues)}. "
                f"Set the required environment variables to enable WhatsApp integration."
            )
            return
        
        # Optional: Early healthcheck validation
        try:
            health_result = await self.healthcheck()
            if isinstance(health_result, dict) and not health_result.get("healthy", True):
                errors = health_result.get('errors', [])
                logger.debug(
                    f"WhatsApp action healthcheck failed during registration: {'; '.join(errors)}"
                )
        except Exception as e:
            logger.debug(
                f"Could not perform healthcheck during registration: {e}"
            )
        
        # Initialize filter (transforms messages before adapter)
        filter = WhatsAppFilter(channels=["whatsapp"], priority=100)
        if await filter.initialize():
            logger.info(
                f"WhatsAppFilter initialized for channel 'whatsapp'"
            )
        else:
            logger.warning(
                "WhatsAppFilter initialization failed. Message transformations will not be applied."
            )
        
        logger.info(
            f"WhatsApp action registered. Session will be initialized on app startup."
        )

    async def on_reload(self) -> None:
        """Called when action is reloaded (e.g., after update).

        Ensures webhook URL and session registration are properly set up
        immediately after code updates. This is critical for actions that
        were updated via --update flag, as it ensures the session is
        re-registered with the current webhook URL without waiting for
        the next request.
        
        Note: Session registration is normally performed lazily on first use
        (e.g., first webhook). This method provides immediate re-registration
        after updates.
        
        If the action is not properly configured, it will skip reinitialization
        gracefully.
        """
        # Apply environment variable defaults (e.g., APP_BASE_URL)
        self._apply_env_defaults()
        
        # Check if action is configured
        if not self.is_configured():
            config_status = self.get_configuration_status()
            issues = config_status.get("issues", [])
            logger.debug(
                f"WhatsApp action not configured, skipping reload. "
                f"Missing/invalid: {'; '.join(issues)}"
            )
            return

        # Ensure webhook URL is set and valid
        # This is critical as webhook_url might be None after an update
        if not self.webhook_url:
            logger.info("Webhook URL not set during reload, generating new one")
            # Generate webhook URL (will reuse existing if valid, or create new)
            await self.get_webhook_url(regenerate=False)
        elif self.base_url:
            # Verify webhook URL is still valid (only if base_url is configured)
            try:
                agent = await self.get_agent()
                agent_id = str(agent.id)
                expected_url_base = f"{self.base_url}/api/whatsapp/interact/webhook/{agent_id}"
                
                # Check if webhook URL is for the correct agent
                if not self.webhook_url.startswith(expected_url_base):
                    logger.debug(
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
                        logger.debug(
                            f"API key {self.webhook_api_key_id} is inactive during reload. Regenerating webhook URL."
                        )
                        await self.get_webhook_url(regenerate=True)
            except Exception as e:
                logger.debug(
                    f"Error verifying webhook URL during reload: {e}. Regenerating webhook URL."
                )
                await self.get_webhook_url(regenerate=True)

        # Re-register session to ensure it's properly configured immediately after reload
        # This ensures the session is registered with the current webhook URL without
        # waiting for the next request. Session registration is normally lazy (first use).
        try:
            registration_result = await self.register_session()
            
            # Check if registration actually succeeded
            if isinstance(registration_result, dict):
                if registration_result.get("status") == "ERROR" or not registration_result.get("ok", True):
                    error_msg = registration_result.get("error") or registration_result.get("message", "Unknown error")
                    logger.error(
                        f"Session re-registration failed during reload: {error_msg}"
                    )
                else:
                    # Mark session as registered to avoid redundant API call on next request
                    self._session_registration_done = True
        except TypeError as e:
            # Handle aiohttp/Python 3.12+ compatibility issues
            error_str = str(e)
            if "BaseException" in error_str:
                logger.debug(
                    f"WhatsApp API server ({self.api_url}) is unreachable during reload. "
                    f"Session will be registered when the server becomes available."
                )
            else:
                logger.error(f"Type error re-registering session during reload: {e}", exc_info=True)
        except Exception as e:
            logger.error(
                f"Error re-registering session during reload: {e}",
                exc_info=True,
            )
            # Don't raise - allow action to continue even if session registration fails
            # The session can be registered later when needed

    async def on_startup(self) -> None:
        """Called when app starts and action is loaded.
        
        Initializes the WhatsApp filter and channel adapter when the app restarts
        and loads this action from the database. Session registration is performed
        lazily on first use (e.g., first webhook) for Lambda and long-running
        server compatibility, avoiding blocking/failing when the WhatsApp API is
        unreachable during bootstrap.
        """
        # Apply environment variable defaults (e.g., APP_BASE_URL)
        self._apply_env_defaults()
        
        # Check if action is configured
        if not self.is_configured():
            config_status = self.get_configuration_status()
            issues = config_status.get("issues", [])
            logger.debug(
                f"WhatsApp action not configured on startup. "
                f"Missing/invalid: {'; '.join(issues)}. "
                f"Set the required environment variables to enable WhatsApp integration."
            )
            return
        
        # Only proceed if enabled
        if not self.enabled:
            return
        
        try:
            # Initialize filter (transforms messages before adapter)
            filter = WhatsAppFilter(channels=["whatsapp"], priority=100)
            if await filter.initialize():
                logger.info(
                    f"WhatsAppFilter initialized on startup for channel 'whatsapp'"
                )
            else:
                logger.warning(
                    "WhatsAppFilter initialization failed on startup. Message transformations will not be applied."
                )
            
            # Initialize adapter (create new instance to ensure clean state)
            adapter = WhatsAppAdapter(action=self)
            if await adapter.initialize():
                logger.info(
                    f"WhatsAppAdapter initialized on startup for channel '{adapter.channel}'"
                )
            else:
                logger.error(
                    "WhatsAppAdapter initialization failed on startup. Messages will NOT be delivered."
                )
                return
                
        except Exception as e:
            logger.error(
                f"Error during WhatsApp action startup: {e}",
                exc_info=True
            )


    async def ensure_session_registered(self) -> bool:
        """Ensure WhatsApp session is registered with the API provider (lazy initialization).
        
        This method provides Lambda-compatible session registration that works even when
        on_startup() hasn't run. It registers the session at most once per process using
        an internal guard flag to avoid redundant API calls.
        
        Returns:
            True if session is registered successfully, False otherwise
        """
        # Return early if already registered in this process
        if self._session_registration_done:
            logger.debug("WhatsApp session already registered in this process")
            return True
        
        # Check if action is configured
        if not self.is_configured():
            logger.debug("WhatsApp action not configured, cannot register session")
            return False
        
        try:
            # Call register_session
            registration_result = await self.register_session()
            
            # Check if registration succeeded
            if isinstance(registration_result, dict):
                if registration_result.get("status") == "ERROR" or not registration_result.get("ok", True):
                    error_msg = registration_result.get("error") or registration_result.get("message", "Unknown error")
                    logger.warning(
                        f"WhatsApp session registration failed (lazy init): {error_msg}"
                    )
                    return False
                else:
                    # Mark as registered to avoid redundant calls
                    self._session_registration_done = True
                    status = registration_result.get("status", "UNKNOWN")
                    logger.info(
                        f"WhatsApp session registered successfully (lazy init): {self.session} (status: {status})"
                    )
                    return True
            else:
                logger.warning(f"WhatsApp session registration returned unexpected type: {type(registration_result)}")
                return False
                
        except TypeError as e:
            # Handle aiohttp/Python 3.12+ compatibility issues
            error_str = str(e)
            if "BaseException" in error_str:
                logger.warning(
                    f"WhatsApp API server ({self.api_url}) is unreachable during lazy session registration. "
                    f"Session will be registered when the server becomes available."
                )
            else:
                logger.error(f"Type error during lazy session registration: {e}", exc_info=True)
            return False
        except Exception as e:
            logger.error(f"Error during lazy session registration: {e}", exc_info=True)
            return False

    async def ensure_adapter_registered(self) -> bool:
        """Ensure WhatsApp adapter is registered with ResponseBus (lazy initialization).
        
        This method provides Lambda-compatible initialization that works even when 
        on_startup() hasn't run (e.g., cold start, first request in a container).
        It ensures both session registration (with the WhatsApp API provider) and
        adapter registration (with the ResponseBus) are completed before processing.
        
        Returns:
            True if adapter is registered and initialized, False if action not configured
        """
        # Check if action is configured
        if not self.is_configured():
            logger.debug("WhatsApp action not configured, cannot register adapter")
            return False
        
        # Ensure session is registered first (lazy, once per process)
        session_ready = await self.ensure_session_registered()
        if not session_ready:
            logger.warning(
                "WhatsApp session registration failed during lazy initialization. "
                "Continuing with adapter registration anyway."
            )
        
        try:
            from jvagent.core.app import App
            app = await App.get()
            if not app:
                logger.warning("App node not found, cannot register WhatsApp adapter")
                return False
            
            response_bus = await app.get_response_bus()
            if not response_bus:
                logger.warning("ResponseBus not available, cannot register WhatsApp adapter")
                return False
            
            # Check if adapter already registered
            existing_adapter = response_bus._channel_adapters.get("whatsapp")
            if existing_adapter and existing_adapter._initialized:
                logger.debug("WhatsApp adapter already registered and initialized")
                return True
            
            # Register adapter (same as on_startup)
            logger.info("Lazy-registering WhatsApp adapter (Lambda cold start or first use)")
            adapter = WhatsAppAdapter(action=self)
            if await adapter.initialize():
                logger.info("WhatsApp adapter successfully registered via lazy initialization")
                return True
            else:
                logger.error("WhatsApp adapter initialization failed during lazy registration")
                return False
                
        except Exception as e:
            logger.error(f"Error ensuring WhatsApp adapter registration: {e}", exc_info=True)
            return False
    
    def api(self) -> Union[WPPConnectAPI, WWebJSAPI, UltraMsgAPI]:
        """Get API instance for the configured provider.
        
        Returns:
            API instance for the configured provider
            
        Raises:
            ValidationError: If action is not configured, provider is unsupported,
                           or configuration is invalid
        """
        # Check if action is configured
        if not self.is_configured():
            config_status = self.get_configuration_status()
            issues = config_status.get("issues", [])
            raise ValidationError(
                f"WhatsApp action is not configured: {'; '.join(issues)}"
            )
            
        try:
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
            elif self.provider == "ultramsg":
                return UltraMsgAPI(
                    api_url=self.api_url,
                    session=self.session,
                    token=self.token,
                    secret_key=self.api_key,
                    timeout=self.request_timeout,
                )
            else:
                raise ValidationError(f"Unsupported provider: {self.provider}")
        except ValidationError:
            raise
        except Exception as e:
            logger.error(f"Failed to create API instance for provider {self.provider}: {e}")
            raise ValidationError(f"API initialization failed: {e}")

    async def get_webhook_url(self, allowed_ip: Optional[str] = None, regenerate: bool = False) -> str:
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
            ValidationError: If base_url is not configured, API key generation fails,
                           or agent cannot be retrieved
            DatabaseError: If database operations fail
        """
        # Check if base_url is configured (from attribute or APP_BASE_URL env var)
        if not self.base_url or not self.base_url.strip():
            raise ValidationError(
                "base_url (APP_BASE_URL) is required for webhook URL generation. "
                "Set this to your application's public URL (e.g., https://myapp.example.com)"
            )
            
        if not self.base_url.startswith(("http://", "https://")):
            raise ValidationError(
                f"base_url must be a valid HTTP/HTTPS URL, got: {self.base_url}"
            )
            
        try:
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
                        except DatabaseError as e:
                            logger.debug(
                                f"Database error checking existing API key {self.webhook_api_key_id}: {e}. Regenerating."
                            )
                            regenerate = True
                        except Exception as e:
                            logger.debug(
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
                except DatabaseError as e:
                    logger.debug(f"Database error revoking old API key: {e}")
                except Exception as e:
                    logger.debug(f"Error revoking old API key: {e}")

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
            
        except DatabaseError as e:
            logger.error(f"Database error in get_webhook_url: {e}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Failed to generate webhook URL: {e}", exc_info=True)
            raise ValidationError(f"Webhook URL generation failed: {e}")

    async def set_recording_status(
        self, phone: str, value: bool = True, is_group: bool = False, duration: int = 5
    ) -> None:
        """Set or clear recording status for a phone number.

        Args:
            phone: Phone number
            value: True to start recording, False to stop
            is_group: Whether the chat is a group
            duration: Duration of recording status in seconds
        """
        # Skip if not configured
        if not self.is_configured():
            return
            
        try:
            await self.api().set_recording_status(
                phone=phone, value=value, is_group=is_group, duration=duration
            )
        except Exception as e:
            logger.debug(
                f"WhatsAppAction: Failed to set recording status for {phone}: {e}"
            )


    async def register_session(self) -> Dict[str, Any]:
        """Register WhatsApp session with proper error handling.
        
        Returns:
            Dict containing session registration result, or status dict if not configured.
            
        Raises:
            ValidationError: If session registration fails (when configured)
            DatabaseError: If database operations fail
        """
        # Check if action is configured
        if not self.is_configured():
            config_status = self.get_configuration_status()
            logger.debug(
                f"WhatsApp action not configured, cannot register session. "
                f"Missing: {'; '.join(config_status.get('issues', []))}"
            )
            return {
                "status": "skipped",
                "reason": "WhatsApp action is not configured",
                "issues": config_status.get("issues", []),
            }
            
        try:
            agent = await self.get_agent()

            # set agent name as session if not set
            if not self.session or not self.session.strip():
                self.session = agent.name
                # Save session if it was just set
                await self.save()

            # create webhook url if not set
            if not self.webhook_url:
                # Generate secure webhook URL with API key
                self.webhook_url = await self.get_webhook_url()

            # register session
            result = await self.api().register_session(
                webhook_url=self.webhook_url,
                wait_qr_code=True,
                auto_register=True,
            )
            
            # Ensure result is a dict
            if not isinstance(result, dict):
                logger.error(f"register_session returned unexpected type: {type(result)}")
                return {
                    "status": "ERROR",
                    "ok": False,
                    "error": f"Invalid response type: {type(result)}",
                    "message": "Session registration returned unexpected response format"
                }
            
            # Check if registration actually succeeded before logging success
            if result.get("status") == "ERROR" or not result.get("ok", True):
                error_msg = result.get("error") or result.get("message", "Unknown error")
                logger.warning(
                    f"WhatsApp session registration failed for '{self.session}': {error_msg}. "
                    f"Full result: {result}"
                )
                return result
            
            # Only log success if registration actually succeeded
            status = result.get("status", "UNKNOWN")
            logger.info(
                f"WhatsApp session registered successfully: {self.session} (status: {status})"
            )
            return result
            
        except DatabaseError as e:
            logger.error(f"Database error during session registration: {e}", exc_info=True)
            raise
        except (OSError, ConnectionError, ConnectionRefusedError, ConnectionResetError) as e:
            # Handle network/connection errors gracefully
            logger.error(
                f"Network error during WhatsApp session registration: {e}. "
                f"Check if the WhatsApp API server ({self.api_url}) is reachable."
            )
            return {
                "status": "ERROR",
                "message": "Network error: Could not connect to WhatsApp API server",
                "error": str(e),
                "api_url": self.api_url,
            }
        except TypeError as e:
            # Handle aiohttp/aiohappyeyeballs compatibility issues with Python 3.12+
            error_str = str(e)
            if "BaseException" in error_str:
                logger.error(
                    f"Connection failed during WhatsApp session registration. "
                    f"Check if the WhatsApp API server ({self.api_url}) is reachable."
                )
                return {
                    "status": "ERROR",
                    "message": "Connection failed: WhatsApp API server unreachable",
                    "error": "Server unreachable",
                    "api_url": self.api_url,
                }
            logger.error(f"Type error during session registration: {e}", exc_info=True)
            raise ValidationError(f"Session registration failed: {e}")
        except Exception as e:
            logger.error(f"Failed to register WhatsApp session: {e}", exc_info=True)
            raise ValidationError(f"Session registration failed: {e}")

    async def healthcheck(self) -> Union[bool, Dict[str, Any]]:
        """Perform health check for WhatsApp action.
        
        Returns:
            Dict with health status. If not configured, returns a status indicating
            the action is inactive but not in error state.
        """
        # Check if action is configured
        if not self.is_configured():
            config_status = self.get_configuration_status()
            return {
                "healthy": True,  # Not an error, just not configured
                "configured": False,
                "status": "inactive",
                "message": "WhatsApp action is not configured. Set WHATSAPP_API_URL and WHATSAPP_API_KEY to enable.",
                "issues": config_status.get("issues", []),
            }
        
        errors = []
        warnings = []
        
        # Validate provider
        if not self.provider:
            errors.append("provider is required")
        elif self.provider not in ["wppconnect", "wwebjs", "ultramsg"]:
            errors.append(f"Unsupported provider: {self.provider}")
            
        # Validate numeric settings
        if self.request_timeout <= 0:
            errors.append("request_timeout must be positive")
            
        if self.chunk_length <= 0:
            errors.append("chunk_length must be positive")
            
        if self.media_batch_window <= 0:
            errors.append("media_batch_window must be positive")
        
        # Check adapter initialization (get from ResponseBus registry)
        from jvagent.core.app import App
        app = await App.get()
        adapter = None
        adapter_initialized = False
        if app:
            response_bus = await app.get_response_bus()
            if response_bus:
                adapter = response_bus._channel_adapters.get("whatsapp")
                if adapter:
                    if not adapter._initialized:
                        errors.append(
                            "WhatsAppAdapter exists but is not initialized. "
                            "Messages will NOT be delivered. Try reloading the action."
                        )
                    adapter_initialized = adapter._initialized
            
        if errors:
            return {
                "healthy": False,
                "configured": True,
                "errors": errors,
                "warnings": warnings if warnings else None,
            }
            
        # Test API connection
        try:
            api = self.api()
            # Basic connectivity test - this will vary by provider
            # For now, just check if API instance can be created
            # adapter_initialized already set above from ResponseBus registry
            result = {
                "healthy": True, 
                "configured": True,
                "status": "active",
                "provider": self.provider, 
                "api_url": self.api_url,
                "adapter_initialized": adapter_initialized,
            }
            if warnings:
                result["warnings"] = warnings
            return result
        except Exception as e:
            # adapter_initialized already set above from ResponseBus registry
            return {
                "healthy": False,
                "configured": True,
                "errors": [f"API connection failed: {e}"],
                "adapter_initialized": adapter_initialized,
            }


