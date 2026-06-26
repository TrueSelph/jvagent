"""WhatsApp Action Implementation."""

import hmac
import logging
import os
from typing import Any, ClassVar, Dict, List, Optional, Union

from jvspatial.api.auth.api_key_service import APIKeyService
from jvspatial.core.annotations import attribute
from jvspatial.core.context import GraphContext
from jvspatial.db import get_prime_database
from jvspatial.env import env
from jvspatial.exceptions import DatabaseError, ValidationError

from jvagent.action.base import Action
from jvagent.core.public_url import get_public_base_url

from .modules.meta_api import MetaWhatsAppAPI
from .modules.ultramsg import UltraMsgAPI
from .modules.wppconnect import WPPConnectAPI
from .modules.wwebjs_api import WWebJSAPI
from .utils.typing_state_manager import TypingStateManager
from .webhook_auth import get_or_create_system_user
from .whatsapp_adapter import WhatsAppAdapter
from .whatsapp_filter import WhatsAppFilter
from .whatsapp_voice_filter import WhatsAppVoiceResponseFilter

logger = logging.getLogger(__name__)


class WhatsAppAction(Action):
    """Action for WhatsApp integration using multiple providers.

    This action is optional and will gracefully skip initialization if the
    required bridge URL, credentials, and public base URL are not configured
    (via ``agent.yaml`` or environment variables). When unconfigured, the action
    remains inactive but does not cause errors during agent startup.

    Bridge URL and credentials may be set in ``agent.yaml`` (``api_url``, ``api_key``,
    ``token``) or via environment variables ``WHATSAPP_API_URL``, ``WHATSAPP_API_KEY``,
    and ``WHATSAPP_TOKEN``. YAML values take precedence when non-empty.
    Public base URL always comes from ``JVAGENT_PUBLIC_BASE_URL``.

    The WhatsApp session name is resolved in order: ``WHATSAPP_SESSION`` env, then
    optional ``session`` on this action, then the current agent's name.
    """

    # AUDIT-actions XC-4: declare non-conforming endpoint paths so deregister
    # cleanup unregisters them along with the standard /actions/{id}/ ones.
    # ``/whatsapp/{action_id}/...`` paths are unique to this action instance;
    # ``/whatsapp/interact/webhook/{agent_id}`` is per-agent.
    additional_endpoint_path_templates: ClassVar[List[str]] = [
        "/whatsapp/{action_id}/",
        "/whatsapp/interact/webhook/{agent_id}",
    ]

    provider: str = attribute(
        default="wwebjs",
        description="WhatsApp provider (wppconnect, ultramsg, ts-whatsapp, wwebjs, meta)",
        pattern=r"^(wppconnect|ultramsg|ts-whatsapp|wwebjs|meta)$",
    )

    api_url: str = attribute(
        default="",
        description="WhatsApp bridge API base URL; when empty, WHATSAPP_API_URL is used",
    )
    api_key: str = attribute(
        default="",
        description="Provider secret / API key; when empty, WHATSAPP_API_KEY is used",
    )
    token: str = attribute(
        default="",
        description="Provider token when distinct from api_key; when empty, WHATSAPP_TOKEN then WHATSAPP_API_KEY",
    )
    session: Optional[str] = attribute(
        default=None,
        description="WhatsApp session identifier",
        max_length=100,
    )

    phone_number_id: str = attribute(
        default="",
        description="Meta Cloud API phone number ID; when empty, WHATSAPP_PHONE_NUMBER_ID is used",
    )
    access_token: str = attribute(
        default="",
        description="Meta Cloud API access token; when empty, WHATSAPP_ACCESS_TOKEN is used",
    )
    app_secret: str = attribute(
        default="",
        description="Meta app secret for webhook signature; WHATSAPP_APP_SECRET or FACEBOOK_APP_SECRET",
    )
    verify_token: Optional[str] = attribute(
        default=None,
        description="Meta webhook verify token; when empty, WHATSAPP_VERIFY_TOKEN is used",
    )
    waba_id: str = attribute(
        default="",
        description="WhatsApp Business Account ID (optional; WHATSAPP_WABA_ID)",
    )
    app_id: str = attribute(
        default="",
        description="Meta app ID (optional; WHATSAPP_APP_ID or FACEBOOK_APP_ID)",
    )
    graph_version: str = attribute(
        default="v25.0",
        description="Graph API version for meta provider (WHATSAPP_GRAPH_VERSION)",
        max_length=20,
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
        default=1.5,
        description="Time window in seconds to batch multiple media messages together",
        ge=0.1,
        le=30.0,
    )

    ignore_list: List[str] = attribute(
        default_factory=lambda: ["status@broadcast"],
        description="Keywords to block: messages from senders or to receivers containing any keyword are ignored. Default includes status@broadcast to ignore WhatsApp status updates.",
    )

    stt_action: Optional[str] = attribute(
        default="DeepgramSTTAction",
        description="Label or Class used to transcribe voice messages or audio files",
        min_length=1,
    )

    tts_action: Optional[str] = attribute(
        default="ElevenLabsTTSAction",
        description="Label or Class used to convert text to speech",
        min_length=1,
    )

    # Internal state tracking (not persisted)
    _session_registered: bool = False

    def is_meta_provider(self) -> bool:
        return (self.provider or "").strip() == "meta"

    # action configuration

    def _env_api_key(self) -> str:
        k = (self.api_key or "").strip()
        if k:
            return k
        return env("WHATSAPP_API_KEY") or ""

    def _env_token(self) -> str:
        t = (self.token or "").strip()
        if t:
            return t
        return env("WHATSAPP_API_KEY") or env("WHATSAPP_TOKEN") or ""

    def _whatsapp_api_url(self) -> str:
        u = (self.api_url or "").strip()
        if u:
            return u
        return (
            env("WHATSAPP_API_URL") or os.environ.get("WHATSAPP_API_URL") or ""
        ).strip()

    def _env_phone_number_id(self) -> str:
        p = (self.phone_number_id or "").strip()
        if p:
            return p
        return (env("WHATSAPP_PHONE_NUMBER_ID") or "").strip()

    def _env_access_token(self) -> str:
        t = (self.access_token or "").strip()
        if t:
            return t
        return (env("WHATSAPP_ACCESS_TOKEN") or "").strip()

    def _env_app_secret(self) -> str:
        s = (self.app_secret or "").strip()
        if s:
            return s
        return (env("WHATSAPP_APP_SECRET") or env("FACEBOOK_APP_SECRET") or "").strip()

    def _env_verify_token(self) -> str:
        configured = self.verify_token
        if isinstance(configured, str) and configured.strip():
            return configured.strip()
        return (env("WHATSAPP_VERIFY_TOKEN") or "").strip()

    def _meta_graph_api_url(self) -> str:
        version = (self.graph_version or "").strip()
        if not version:
            version = (
                env("WHATSAPP_GRAPH_VERSION") or os.environ.get("WHATSAPP_GRAPH_VERSION") or "v25.0"
            ).strip()
        if not version.startswith("v"):
            version = f"v{version}"
        return f"https://graph.facebook.com/{version}/"

    @staticmethod
    def _whatsapp_session_env() -> str:
        return (
            env("WHATSAPP_SESSION") or os.environ.get("WHATSAPP_SESSION") or ""
        ).strip()

    async def _effective_whatsapp_session(self) -> str:
        name = self._whatsapp_session_env()
        if name:
            return name
        stored = (self.session or "").strip()
        if stored:
            return stored
        agent = await self.get_agent()
        return (agent.name if agent else "") or ""

    def is_configured(self) -> bool:
        """Check if the WhatsApp action has required configuration."""
        base_url = get_public_base_url()
        if not base_url or not base_url.startswith(("http://", "https://")):
            return False

        if self.is_meta_provider():
            if not self._env_phone_number_id():
                return False
            if not self._env_access_token():
                return False
            if not self._env_app_secret():
                return False
            if not self._env_verify_token():
                return False
            return True

        api_url = self._whatsapp_api_url()
        if not api_url:
            return False
        if not (self._env_api_key() or self._env_token()):
            return False
        if not api_url.startswith(("http://", "https://")):
            return False
        return True

    def get_capabilities(self) -> List[str]:
        """Return WhatsApp capabilities for PersonaAction when enabled."""
        if not self.enabled:
            return []
        if self.is_meta_provider():
            return [
                "Send and receive text messages over WhatsApp (Cloud API)",
            ]
        return [
            "Join WhatsApp groups and send / receive messages to groups",
            "Send, receive and listen to voice notes over WhatsApp",
            "Receive and view images shared over WhatsApp",
        ]

    def _config_issues(self) -> list[str]:
        """Get list of configuration issues."""
        issues: list[str] = []
        base_url = get_public_base_url()
        if not base_url:
            issues.append("base_url (JVAGENT_PUBLIC_BASE_URL) is not configured")
        elif not base_url.startswith(("http://", "https://")):
            issues.append("base_url must be a valid HTTP/HTTPS URL")

        if self.is_meta_provider():
            if not self._env_phone_number_id():
                issues.append(
                    "phone_number_id (action.phone_number_id or WHATSAPP_PHONE_NUMBER_ID) "
                    "is not configured"
                )
            if not self._env_access_token():
                issues.append(
                    "access_token (action.access_token or WHATSAPP_ACCESS_TOKEN) "
                    "is not configured"
                )
            if not self._env_app_secret():
                issues.append(
                    "app_secret (WHATSAPP_APP_SECRET or FACEBOOK_APP_SECRET) is not configured"
                )
            if not self._env_verify_token():
                issues.append(
                    "verify_token (action.verify_token or WHATSAPP_VERIFY_TOKEN) "
                    "is not configured"
                )
            return issues

        api_url = self._whatsapp_api_url()
        if not api_url:
            issues.append(
                "api_url (action.api_url or WHATSAPP_API_URL) is not configured"
            )
        elif not api_url.startswith(("http://", "https://")):
            issues.append("api_url must be a valid HTTP/HTTPS URL")
        if not (self._env_api_key() or self._env_token()):
            issues.append(
                "api_key / token (action fields or WHATSAPP_API_KEY / WHATSAPP_TOKEN) "
                "is not configured"
            )
        return issues

    @staticmethod
    def meta_callback_url_for_subscription(webhook_url: str) -> str:
        """Strip ``?api_key=...`` for Meta App Dashboard callback URL."""
        s = (webhook_url or "").strip()
        if not s:
            return s
        q = s.find("?")
        return s[:q] if q >= 0 else s

    def parse_webhook_verify(self, query: Dict[str, Any]) -> Union[str, Dict[str, Any]]:
        """Meta GET webhook verification (hub.* query params)."""
        if not self.is_meta_provider():
            return {"message": "Webhook verify only applies to meta provider", "code": 403}
        expected = self._env_verify_token()
        mode = query.get("hub.mode")
        hub_verify = query.get("hub.verify_token")
        challenge = query.get("hub.challenge")
        token_ok = hmac.compare_digest(
            str(hub_verify or "").encode("utf-8"),
            str(expected or "").encode("utf-8"),
        )
        if token_ok and mode == "subscribe":
            return str(challenge) if challenge is not None else ""
        return {"message": "Invalid token or mode", "code": 403}

    async def on_register(self) -> None:
        """Called when action is registered. Validates configuration."""
        if not self.is_configured():
            logger.debug("WhatsApp action not configured")
            return
        logger.debug("WhatsApp action registered")

    async def on_reload(self) -> None:
        """Called when action is reloaded. Re-registers session with current webhook URL."""
        if not self.is_configured():
            logger.debug("WhatsApp action not configured, skipping reload")
            return

        if self.is_meta_provider():
            if not self.webhook_url:
                await self.get_webhook_url(regenerate=False)
            self._session_registered = True
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

    async def _warn_lambda_local_storage(self) -> None:
        """Log warning when on Lambda with local file storage and non-/tmp path."""
        if not os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
            return
        try:
            from jvagent.core.app import App

            app = await App.get()
            if not app:
                return
            provider = getattr(app, "file_storage_provider", "") or "local"
            root = (getattr(app, "file_storage_root_dir", "") or "./.files").strip()
            if provider == "local" and root and not root.startswith("/tmp"):
                logger.warning(
                    "WhatsApp media on Lambda: file_storage is local with "
                    "root_dir=%r. Lambda /var/task is read-only. Configure "
                    "file_storage.provider: s3 (or JVSPATIAL_FILE_INTERFACE=s3) "
                    "or set JVSPATIAL_FILES_ROOT_PATH=/tmp for ephemeral storage.",
                    root,
                )
        except Exception:
            pass

    async def on_startup(self) -> None:
        """Initialize filter and adapter, attempt session registration with configurable timeout."""
        if not self.is_configured() or not self.enabled:
            return

        await self._warn_lambda_local_storage()

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

        if self.tts_action:
            voice_filter = WhatsAppVoiceResponseFilter(
                action=self, channels=["whatsapp"], priority=105
            )
            if not await voice_filter.initialize(agent=agent):
                logger.warning("WhatsAppVoiceResponseFilter initialization failed")

        original_timeout = self.request_timeout
        skip_registration = (
            os.environ.get("WHATSAPP_SKIP_STARTUP_REGISTRATION", "").lower() == "true"
        )
        try:
            if desired_timeout > self.request_timeout:
                self.request_timeout = desired_timeout

            if skip_registration or self.is_meta_provider():
                if self.is_meta_provider():
                    logger.info(
                        "WhatsApp meta provider: skipping bridge session registration. "
                        "Configure webhook in Meta App Dashboard."
                    )
                    result = {
                        "status": "skipped",
                        "reason": "meta_cloud_api",
                        "ok": True,
                    }
                else:
                    logger.info(
                        "WhatsApp startup registration skipped (WHATSAPP_SKIP_STARTUP_REGISTRATION=true). "
                        "Use POST /api/actions/{action_id}/session/register to register manually."
                    )
                    result = {
                        "status": "skipped",
                        "reason": "WHATSAPP_SKIP_STARTUP_REGISTRATION=true",
                    }
            else:
                result = await self.register_session()
            if (
                isinstance(result, dict)
                and result.get("ok", True)
                and result.get("status") != "ERROR"
            ):
                self._session_registered = True
                if self.is_meta_provider():
                    logger.info("WhatsApp meta provider ready (Cloud API)")
                else:
                    sess = await self._effective_whatsapp_session()
                    logger.info(
                        "WhatsApp session %r registered on startup", sess or "(unknown)"
                    )
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

    async def api(
        self,
    ) -> Union[WPPConnectAPI, WWebJSAPI, UltraMsgAPI, MetaWhatsAppAPI]:
        """Get API instance for the configured provider."""
        if not self.is_configured():
            raise ValidationError(
                f"WhatsApp action is not configured: {'; '.join(self._config_issues())}"
            )

        timeout = self.request_timeout
        if timeout == 60:
            env_timeout = os.environ.get("WHATSAPP_REQUEST_TIMEOUT")
            if env_timeout:
                try:
                    timeout = min(timeout, int(env_timeout))
                except ValueError:
                    pass

        try:
            if self.provider == "meta":
                phone_id = self._env_phone_number_id()
                return MetaWhatsAppAPI(
                    api_url=self._meta_graph_api_url(),
                    session=phone_id,
                    token=self._env_access_token(),
                    secret_key=self._env_app_secret(),
                    timeout=timeout,
                    phone_number_id=phone_id,
                )

            session = await self._effective_whatsapp_session()
            if not session or not session.strip():
                raise ValidationError(
                    "WhatsApp session name is unavailable (set WHATSAPP_SESSION, "
                    "configure session on the action, or ensure agent name is available)"
                )

            api_url = self._whatsapp_api_url()

            if self.provider == "wppconnect":
                return WPPConnectAPI(
                    api_url=api_url,
                    session=session,
                    token=self._env_token(),
                    secret_key=self._env_api_key(),
                    timeout=timeout,
                )
            elif self.provider == "wwebjs":
                return WWebJSAPI(
                    api_url=api_url,
                    session=session,
                    token=self._env_token(),
                    secret_key=self._env_api_key(),
                    timeout=timeout,
                )
            elif self.provider == "ultramsg":
                return UltraMsgAPI(
                    api_url=api_url,
                    session=session,
                    token=self._env_token(),
                    secret_key=self._env_api_key(),
                    timeout=timeout,
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
        base_url = get_public_base_url()
        if not base_url or not base_url.strip():
            raise ValidationError(
                "base_url (JVAGENT_PUBLIC_BASE_URL) is required for webhook URL generation"
            )
        if not base_url.startswith(("http://", "https://")):
            raise ValidationError(
                f"base_url must be a valid HTTP/HTTPS URL, got: {base_url}"
            )

        try:
            agent = await self.get_agent()
            agent_id = str(agent.id)
            expected_url_base = f"{base_url}/api/whatsapp/interact/webhook/{agent_id}"

            prime_ctx = GraphContext(database=get_prime_database())
            api_key_service = APIKeyService(context=prime_ctx)

            if (
                not regenerate
                and self.webhook_url
                and "?api_key=" in self.webhook_url
                and self.webhook_url.startswith(expected_url_base)
            ):
                # When allowed_ip is specified, verify existing key's IPs match
                if allowed_ip is not None and self.webhook_api_key_id:
                    try:
                        existing_key = await api_key_service.get_key(
                            self.webhook_api_key_id
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

            if regenerate and self.webhook_api_key_id:
                try:
                    await api_key_service.revoke_key(
                        self.webhook_api_key_id, system_user_id
                    )
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
            api = await self.api()
            await api.set_recording_status(
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

        if self.is_meta_provider():
            if not self.webhook_url:
                self.webhook_url = await self.get_webhook_url()
            wa = await self.api()
            return await wa.register_session()

        try:
            agent = await self.get_agent()
            if not agent:
                return {
                    "status": "ERROR",
                    "ok": False,
                    "error": "Agent not available for WhatsApp session registration",
                }

            if not self.webhook_url:
                self.webhook_url = await self.get_webhook_url()

            session_name = await self._effective_whatsapp_session()
            wa = await self.api()
            result = await wa.register_session(
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
                    f"Session registration failed for '{session_name}': {error_msg}"
                )
                return result

            logger.debug("Session registered: %s", session_name)
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
            "meta",
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
        if not self._session_registered and not self.is_meta_provider():
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
            if self.is_meta_provider():
                result["phone_number_id"] = self._env_phone_number_id()
                result["meta_callback_url"] = self.meta_callback_url_for_subscription(
                    self.webhook_url or ""
                )
            else:
                result["api_url"] = self._whatsapp_api_url()

        return result
