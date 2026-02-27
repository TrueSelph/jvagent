"""WhatsApp Action Implementation."""

import logging
import os
from typing import Any, Dict, Optional, Union

from jvspatial.api.auth.api_key_service import APIKeyService
from jvspatial.api.auth.models import APIKey
from jvspatial.core.annotations import attribute
from jvspatial.core.context import GraphContext
from jvspatial.db import get_prime_database
from jvspatial.exceptions import DatabaseError, ValidationError

from jvagent.action.base import Action

from .modules.ultramsg import UltraMsgAPI
from .modules.wppconnect import WPPConnectAPI
from .modules.wwebjs_api import WWebJSAPI
from .utils.typing_state_manager import TypingStateManager
from .webhook_auth import get_or_create_system_user
from .whatsapp_adapter import WhatsAppAdapter
from .whatsapp_filter import WhatsAppFilter

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
        pattern=r"^(wppconnect|ultramsg|ts-whatsapp|wwebjs)$",
    )

    # Optional configuration fields - no strict validators to allow empty/unconfigured state
    # Validation is done in is_configured() and healthcheck() methods
    api_url: Optional[str] = attribute(
        default=None,
        description="WhatsApp API Endpoint URL (e.g., https://api.whatsapp.example.com)",
    )

    api_key: Optional[str] = attribute(
        default=None, description="WhatsApp API Key / Token"
    )

    session: Optional[str] = attribute(
        default=None, description="WhatsApp session identifier", max_length=100
    )

    token: Optional[str] = attribute(
        default=None,
        description="WhatsApp token (alternative to api_key for some providers)",
    )

    base_url: Optional[str] = attribute(
        default=None,
        description="Application base URL for webhook generation (APP_BASE_URL env var, e.g., https://myapp.example.com)",
    )

    webhook_url: Optional[str] = attribute(
        default=None,
        description="WhatsApp webhook URL (auto-generated if not provided)",
    )

    webhook_api_key_id: Optional[str] = attribute(
        default=None, description="ID of the API key used for webhook authentication"
    )

    request_timeout: int = attribute(
        default=60, description="WhatsApp request timeout in seconds", ge=1, le=300
    )

    chunk_length: int = attribute(
        default=4000, description="WhatsApp chunk length", ge=100, le=10000
    )

    utterance_max_length: int = attribute(
        default=3000, description="Maximum length of utterance text", ge=100, le=10000
    )

    media_batch_window: float = attribute(
        default=2.5,
        description="Time window in seconds to batch multiple media messages together",
        ge=0.1,
        le=30.0,
    )

    stt_action: Optional[str] = attribute(
        default="STTAction",
        description="Label or Class used to transcribe voice messages or audio files",
        min_length=1,
    )

    tts_action: Optional[str] = attribute(
        default="TTSAction",
        description="Label or Class used to convert text to speech",
        min_length=1,
    )

    # Internal state tracking (not persisted)
    _session_registered: bool = False

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

    def _config_issues(self) -> list[str]:
        """Get list of configuration issues."""
        issues = []
        if not self.api_url or not self.api_url.strip():
            issues.append("api_url (WHATSAPP_API_URL) is not configured")
        elif not self.api_url.startswith(("http://", "https://")):
            issues.append("api_url must be a valid HTTP/HTTPS URL")
        if not self.api_key or not self.api_key.strip():
            issues.append("api_key (WHATSAPP_API_KEY) is not configured")
        if not self.base_url:
            issues.append("base_url (APP_BASE_URL) is not configured")
        elif not self.base_url.startswith(("http://", "https://")):
            issues.append("base_url must be a valid HTTP/HTTPS URL")
        return issues

    async def on_register(self) -> None:
        """Called when action is registered. Validates configuration."""
        self._apply_env_defaults()
        if not self.is_configured():
            logger.debug("WhatsApp action not configured")
            return
        logger.debug("WhatsApp action registered")

    async def on_reload(self) -> None:
        """Called when action is reloaded. Re-registers session with current webhook URL."""
        self._apply_env_defaults()
        if not self.is_configured():
            logger.debug("WhatsApp action not configured, skipping reload")
            return

        if not self.webhook_url:
            await self.get_webhook_url(regenerate=False)

        try:
            result = await self.register_session()
            if (
                isinstance(result, dict)
                and result.get("ok", True)
                and result.get("status") != "ERROR"
            ):
                self._session_registered = True
            else:
                error_msg = (
                    result.get("error") or result.get("message", "Unknown error")
                    if isinstance(result, dict)
                    else "Unknown error"
                )
                logger.error(
                    f"Session re-registration failed during reload: {error_msg}"
                )
        except Exception as e:
            logger.error(
                f"Error re-registering session during reload: {e}", exc_info=True
            )

    async def on_startup(self) -> None:
        """Initialize filter and adapter, attempt session registration with configurable timeout."""
        self._apply_env_defaults()
        if not self.is_configured() or not self.enabled:
            return

        agent = await self.get_agent()
        if not agent:
            logger.warning(
                "WhatsAppAction: agent not found, skipping filter/adapter initialization"
            )
            return

        try:
            timeout_str = os.environ.get(
                "WHATSAPP_SESSION_REGISTER_TIMEOUT_SECONDS", "120"
            )
            desired_timeout = max(5, int(timeout_str))
        except (ValueError, TypeError):
            desired_timeout = 120

        filter = WhatsAppFilter(channels=["whatsapp"], priority=100)
        if not await filter.initialize(agent=agent):
            logger.warning("WhatsAppFilter initialization failed")

        original_timeout = self.request_timeout
        try:
            if desired_timeout > self.request_timeout:
                self.request_timeout = desired_timeout

            result = await self.register_session()
            if (
                isinstance(result, dict)
                and result.get("ok", True)
                and result.get("status") != "ERROR"
            ):
                self._session_registered = True
                logger.info(f"WhatsApp session '{self.session}' registered on startup")
            else:
                error_msg = (
                    result.get("error") or result.get("message", "Unknown error")
                    if isinstance(result, dict)
                    else "Unknown error"
                )
                logger.warning(f"Session registration failed on startup: {error_msg}")
        except Exception as e:
            logger.warning(f"Error during session registration on startup: {e}")
        finally:
            self.request_timeout = original_timeout

        adapter = WhatsAppAdapter(action=self)
        if not await adapter.initialize(agent=agent):
            logger.error("WhatsAppAdapter initialization failed")

    def is_session_registered(self) -> bool:
        """Return whether the WhatsApp session has been registered."""
        return self._session_registered

    async def ensure_adapter_registered(self) -> bool:
        """Ensure WhatsApp adapter is registered with ResponseBus (lazy initialization)."""
        if not self.is_configured():
            return False

        try:
            agent = await self.get_agent()
            if not agent:
                return False

            response_bus = await agent.get_response_bus()
            if not response_bus:
                return False

            existing_adapter = response_bus._channel_adapters.get("whatsapp")
            if existing_adapter and existing_adapter._initialized:
                return True

            adapter = WhatsAppAdapter(action=self)
            return await adapter.initialize(agent=agent)

        except Exception as e:
            logger.error(f"Error ensuring adapter registration: {e}", exc_info=True)
            return False

    def api(self) -> Union[WPPConnectAPI, WWebJSAPI, UltraMsgAPI]:
        """Get API instance for the configured provider."""
        if not self.is_configured():
            raise ValidationError(
                f"WhatsApp action is not configured: {'; '.join(self._config_issues())}"
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
            logger.error(
                f"Failed to create API instance for provider {self.provider}: {e}"
            )
            raise ValidationError(f"API initialization failed: {e}")

    async def get_webhook_url(
        self, allowed_ip: Optional[str] = None, regenerate: bool = False
    ) -> str:
        """Generate or retrieve secure webhook URL with API key authentication."""
        if not self.base_url or not self.base_url.strip():
            raise ValidationError(
                "base_url (APP_BASE_URL) is required for webhook URL generation"
            )
        if not self.base_url.startswith(("http://", "https://")):
            raise ValidationError(
                f"base_url must be a valid HTTP/HTTPS URL, got: {self.base_url}"
            )

        try:
            agent = await self.get_agent()
            agent_id = str(agent.id)
            expected_url_base = (
                f"{self.base_url}/api/whatsapp/interact/webhook/{agent_id}"
            )

            if (
                not regenerate
                and self.webhook_url
                and "?api_key=" in self.webhook_url
                and self.webhook_url.startswith(expected_url_base)
            ):
                # When allowed_ip is specified, verify existing key's IPs match
                if allowed_ip is not None and self.webhook_api_key_id:
                    try:
                        prime_db = get_prime_database()
                        context = GraphContext(database=prime_db)
                        existing_key = await context.get(
                            APIKey, self.webhook_api_key_id
                        )
                        if existing_key and existing_key.is_active:
                            requested_ips = [allowed_ip] if allowed_ip else []
                            existing_ips = (
                                getattr(existing_key, "allowed_ips", None) or []
                            )
                            if requested_ips == existing_ips:
                                return self.webhook_url
                        # IP mismatch or key invalid - fall through to regenerate
                    except Exception:
                        pass  # Fall through to regenerate on error
                else:
                    return self.webhook_url

            system_user_id = await get_or_create_system_user()
            prime_db = get_prime_database()
            context = GraphContext(database=prime_db)
            api_key_service = APIKeyService(context=context)

            if regenerate and self.webhook_api_key_id:
                try:
                    old_key = await context.get(APIKey, self.webhook_api_key_id)
                    if old_key:
                        old_key.is_active = False
                        old_key._graph_context = context
                        await context.save(old_key)
                except Exception:
                    pass

            plaintext_key, api_key = await api_key_service.generate_key(
                user_id=system_user_id,
                name=f"WhatsApp Webhook - {agent.name}",
                permissions=["webhook:whatsapp"],
                expires_in_days=None,
                allowed_ips=[allowed_ip] if allowed_ip else [],
                allowed_endpoints=["/api/whatsapp/interact/webhook/*"],
                key_prefix="jv_",
            )

            self.webhook_api_key_id = api_key.id
            self.webhook_url = f"{expected_url_base}?api_key={plaintext_key}"
            if not hasattr(self, "_graph_context") or self._graph_context is None:
                self._graph_context = context
            await self.save()
            return self.webhook_url

        except DatabaseError:
            raise
        except Exception as e:
            raise ValidationError(f"Webhook URL generation failed: {e}")

    async def set_recording_status(
        self, phone: str, value: bool = True, is_group: bool = False, duration: int = 5
    ) -> None:
        """Set or clear recording status for a phone number."""
        if not self.is_configured():
            return
        try:
            await self.api().set_recording_status(
                phone=phone, value=value, is_group=is_group, duration=duration
            )
        except Exception as e:
            logger.debug(f"Failed to set recording status for {phone}: {e}")

    async def register_session(self) -> Dict[str, Any]:
        """Register WhatsApp session with proper error handling."""
        if not self.is_configured():
            issues = self._config_issues()
            logger.debug(
                f"WhatsApp action not configured, cannot register session. Missing: {'; '.join(issues)}"
            )
            return {
                "status": "skipped",
                "reason": "WhatsApp action is not configured",
                "issues": issues,
            }

        try:
            agent = await self.get_agent()

            if not self.session or not self.session.strip():
                self.session = agent.name
                await self.save()

            if not self.webhook_url:
                self.webhook_url = await self.get_webhook_url()

            result = await self.api().register_session(
                webhook_url=self.webhook_url,
                wait_qr_code=True,
                auto_register=True,
            )

            if not isinstance(result, dict):
                return {
                    "status": "ERROR",
                    "ok": False,
                    "error": f"Invalid response type: {type(result)}",
                }

            if result.get("status") == "ERROR" or not result.get("ok", True):
                error_msg = result.get("error") or result.get(
                    "message", "Unknown error"
                )
                logger.warning(
                    f"Session registration failed for '{self.session}': {error_msg}"
                )
                return result

            logger.debug(f"Session registered: {self.session}")
            return result

        except DatabaseError:
            raise
        except (
            OSError,
            ConnectionError,
            ConnectionRefusedError,
            ConnectionResetError,
        ) as e:
            logger.error(f"Network error during session registration: {e}")
            return {"status": "ERROR", "message": "Network error", "error": str(e)}
        except TypeError as e:
            if "BaseException" in str(e):
                return {"status": "ERROR", "message": "Server unreachable"}
            raise ValidationError(f"Session registration failed: {e}")
        except Exception as e:
            raise ValidationError(f"Session registration failed: {e}")

    async def healthcheck(self) -> Union[bool, Dict[str, Any]]:
        """Perform health check for WhatsApp action."""
        if not self.is_configured():
            return {
                "healthy": True,
                "configured": False,
                "status": "inactive",
                "message": "WhatsApp action is not configured",
                "issues": self._config_issues(),
            }

        errors = []
        if not self.provider or self.provider not in [
            "wppconnect",
            "wwebjs",
            "ultramsg",
        ]:
            errors.append(f"Invalid provider: {self.provider}")
        if self.request_timeout <= 0:
            errors.append("request_timeout must be positive")
        if self.chunk_length <= 0:
            errors.append("chunk_length must be positive")
        if self.media_batch_window <= 0:
            errors.append("media_batch_window must be positive")

        adapter_initialized = False
        try:
            agent = await self.get_agent()
            if agent:
                response_bus = await agent.get_response_bus()
                if response_bus:
                    adapter = response_bus._channel_adapters.get("whatsapp")
                    if adapter:
                        adapter_initialized = adapter._initialized
                        if not adapter_initialized:
                            errors.append("WhatsAppAdapter not initialized")
        except Exception:
            pass

        warnings = []
        if not self._session_registered:
            warnings.append("Session not registered")

        result = {
            "healthy": len(errors) == 0,
            "configured": True,
            "adapter_initialized": adapter_initialized,
            "session_registered": self._session_registered,
        }
        if errors:
            result["errors"] = errors
        if warnings:
            result["warnings"] = warnings
        if result["healthy"]:
            result["status"] = "active"
            result["provider"] = self.provider
            result["api_url"] = self.api_url

        return result
