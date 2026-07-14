"""WhatsApp Action Implementation."""

import asyncio
import hmac
import logging
import os
from typing import Annotated, Any, ClassVar, Dict, List, Optional, Union

from jvspatial.api.auth.api_key_service import APIKeyService
from jvspatial.core.annotations import attribute
from jvspatial.core.context import GraphContext
from jvspatial.db import get_prime_database
from jvspatial.env import env
from jvspatial.exceptions import DatabaseError, ValidationError

from jvagent.action.base import Action
from jvagent.core.public_url import get_public_base_url
from jvagent.tooling.tool_decorator import tool
from jvagent.tooling.tool_executor import get_dispatch_context, get_tool_visitor

from .modules.jvconnect_api import JvconnectWhatsAppAPI
from .modules.meta_api import MetaWhatsAppAPI
from .modules.registry import get_provider_factory
from .modules.ultramsg import UltraMsgAPI
from .modules.wppconnect import WPPConnectAPI
from .modules.wwebjs_api import WWebJSAPI
from .utils.meta_verify_token import derive_meta_verify_token
from .utils.meta_webhook_verify import (
    agent_id_from_callback_url,
    dashboard_action_for_stale,
    find_stale_callbacks,
)

# Re-exported for consumers/tests that import it from this module.
from .utils.typing_state_manager import TypingStateManager  # noqa: F401
from .webhook_auth import get_or_create_system_user
from .whatsapp_adapter import WhatsAppAdapter
from .whatsapp_filter import WhatsAppFilter
from .whatsapp_voice_filter import WhatsAppVoiceResponseFilter

logger = logging.getLogger(__name__)

# Action ids that already registered a server startup hook for Meta webhook override.
_meta_webhook_startup_hooks: set[str] = set()


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

    # Stable tool prefix: whatsapp__list_templates / whatsapp__send_template /
    # whatsapp__list_flows / whatsapp__send_flow
    tool_namespace: ClassVar[str] = "whatsapp"

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

    jvconnect_url: str = attribute(
        default="",
        description="jvconnect base URL; when empty, JVCONNECT_URL / WHATSAPP_PROXY_URL env is used",
    )

    jvconnect_webhook_secret: str = attribute(
        default="",
        description=(
            "HMAC secret from jvconnect webhook/register; when empty, "
            "JVCONNECT_WEBHOOK_SECRET env is used"
        ),
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
        description=(
            "Optional Meta phone number ID override; for provider=meta, phone is "
            "resolved from the jvconnect API key (GET /account)"
        ),
    )
    access_token: str = attribute(
        default="",
        description="Deprecated for provider=meta (jvconnect holds the token); unused",
    )
    app_secret: str = attribute(
        default="",
        description="Meta app secret for webhook signature (bridge providers only; meta uses jvconnect)",
    )
    verify_token: Optional[str] = attribute(
        default=None,
        description="Optional Meta webhook verify token override; when empty, derived from agent_id + app secret",
    )
    waba_id: str = attribute(
        default="",
        description="WhatsApp Business Account ID; when empty, WHATSAPP_WABA_ID env is used",
    )
    app_id: str = attribute(
        default="",
        description="Meta app ID (unused for meta; use WHATSAPP_APP_ID env)",
    )
    graph_version: str = attribute(
        default="v25.0",
        description="Graph API version fallback when WHATSAPP_GRAPH_VERSION env is unset",
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

    template_allowlist: List[str] = attribute(
        default_factory=list,
        description=(
            "When non-empty, only these Meta template names may be sent/listed. "
            "Empty means all approved templates from the WABA are allowed."
        ),
    )

    default_template_language: str = attribute(
        default="en_US",
        description="Default language code for whatsapp__send_template when omitted",
        max_length=20,
    )

    flow_allowlist: List[str] = attribute(
        default_factory=list,
        description=(
            "When non-empty, only these Flow ids or names may be sent/listed. "
            "Empty means all Flows from the WABA are allowed."
        ),
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

    def _env_jvconnect_url(self) -> str:
        u = (self.jvconnect_url or "").strip()
        if u:
            return u.rstrip("/")
        return (
            (
                env("JVCONNECT_URL")
                or env("WHATSAPP_PROXY_URL")
                or os.environ.get("JVCONNECT_URL")
                or os.environ.get("WHATSAPP_PROXY_URL")
                or ""
            )
            .strip()
            .rstrip("/")
        )

    def _env_jvconnect_api_key(self) -> str:
        return (
            env("JVCONNECT_API_KEY") or os.environ.get("JVCONNECT_API_KEY") or ""
        ).strip()

    def _env_jvconnect_webhook_secret(self) -> str:
        s = (self.jvconnect_webhook_secret or "").strip()
        if s:
            return s
        return (
            env("JVCONNECT_WEBHOOK_SECRET")
            or os.environ.get("JVCONNECT_WEBHOOK_SECRET")
            or ""
        ).strip()

    def _env_app_secret(self) -> str:
        if self.is_meta_provider():
            # Inbound POSTs are signed by jvconnect with the per-agent webhook secret
            return self._env_jvconnect_webhook_secret()
        s = (self.app_secret or "").strip()
        if s:
            return s
        return (env("WHATSAPP_APP_SECRET") or env("FACEBOOK_APP_SECRET") or "").strip()

    def effective_verify_token(self, agent_id: str = "") -> str:
        """Return Meta hub.verify_token (yaml override or derived)."""
        configured = self.verify_token
        if isinstance(configured, str) and configured.strip():
            return configured.strip()
        if self.is_meta_provider():
            # Meta verifies against jvconnect (FB_VERIFY_TOKEN), not this agent
            return "jvconnect"
        return derive_meta_verify_token(agent_id, self._env_app_secret())

    def _env_waba_id(self) -> str:
        w = (self.waba_id or "").strip()
        if w:
            return w
        return (env("WHATSAPP_WABA_ID") or "").strip()

    def _meta_graph_api_url(self) -> str:
        version = (
            env("WHATSAPP_GRAPH_VERSION")
            or os.environ.get("WHATSAPP_GRAPH_VERSION")
            or ""
        ).strip()
        if not version:
            version = (self.graph_version or "v25.0").strip()
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
            if not self._env_jvconnect_url():
                return False
            if not self._env_jvconnect_api_key():
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
        """Return WhatsApp capabilities for ReplyAction prompt aggregation when enabled."""
        if not self.enabled:
            return []
        if self.is_meta_provider():
            return [
                "Send and receive text messages over WhatsApp (Cloud API)",
                "Send and receive images, documents, and video over WhatsApp (Cloud API)",
                "Send and receive voice notes over WhatsApp (Cloud API); STT/TTS via configured actions",
                "Typing indicators on inbound messages (Cloud API)",
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
            if not self._env_jvconnect_url():
                issues.append(
                    "jvconnect_url (action.jvconnect_url or JVCONNECT_URL / "
                    "WHATSAPP_PROXY_URL) is not configured"
                )
            if not self._env_jvconnect_api_key():
                issues.append(
                    "JVCONNECT_API_KEY is not configured "
                    "(create a phone-bound key in jvconnect API Credentials)"
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

    def parse_webhook_verify(
        self, query: Dict[str, Any], agent_id: str = ""
    ) -> Union[str, Dict[str, Any]]:
        """Meta GET webhook verification (hub.* query params)."""
        if not self.is_meta_provider():
            return {
                "message": "Webhook verify only applies to meta provider",
                "code": 403,
            }
        expected = self.effective_verify_token(agent_id)
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
            skip_subscribe = (
                os.environ.get("WHATSAPP_RELOAD_WEBHOOK_SUBSCRIBE", "true").lower()
                == "false"
            )
            if skip_subscribe:
                logger.info(
                    "WhatsApp meta on_reload: Graph webhook subscribe skipped "
                    "(WHATSAPP_RELOAD_WEBHOOK_SUBSCRIBE=false)"
                )
            else:
                reg = await self.register_meta_webhook_subscription()
                if reg.get("status") not in ("ok", "skipped"):
                    logger.warning(
                        "WhatsApp meta on_reload: register_meta_webhook_subscription: %s",
                        reg,
                    )
                elif reg.get("status") == "ok":
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

    def _schedule_deferred_meta_webhook_register(self) -> None:
        """Register Meta webhook override after the HTTP server is listening.

        ``on_startup`` runs inside ``asyncio.run(pre_startup_bootstrap)``; a bare
        ``asyncio.create_task`` there is cancelled when that loop closes. Hook
        into the jvspatial server lifecycle instead (same pattern as startup summary).
        """
        action_id = str(getattr(self, "id", "") or "")
        if action_id and action_id in _meta_webhook_startup_hooks:
            return
        try:
            from jvspatial.api.context import get_current_server

            server = get_current_server()
            if not server or not hasattr(server, "lifecycle_manager"):
                logger.warning(
                    "WhatsApp meta: cannot schedule webhook override (server not ready)"
                )
                return

            async def _deferred_meta_webhook_register() -> None:
                """Schedule Meta webhook override after uvicorn startup (non-blocking)."""

                async def _run_after_startup() -> None:
                    try:
                        delay_raw = os.environ.get(
                            "WHATSAPP_WEBHOOK_REGISTER_DELAY_SECONDS", "0"
                        )
                        delay_sec = max(0.0, float(delay_raw))
                    except (ValueError, TypeError):
                        delay_sec = 0.0

                    if delay_sec > 0:
                        logger.info(
                            "Deferring Meta WhatsApp webhook override by %.1fs "
                            "(after Application startup complete)",
                            delay_sec,
                        )
                        await asyncio.sleep(delay_sec)
                    else:
                        # Yield once so uvicorn finishes lifespan startup first.
                        await asyncio.sleep(0)

                    logger.info("Registering Meta WhatsApp webhook override on startup")
                    reg = await self.register_meta_webhook_subscription()
                    if reg.get("status") == "ok":
                        self._session_registered = True
                        logger.info(
                            "WhatsApp Meta webhook registration succeeded: %s",
                            reg.get("callback_url"),
                        )
                    elif reg.get("status") == "skipped":
                        logger.info(
                            "WhatsApp Meta webhook registration skipped: %s",
                            reg.get("reason"),
                        )
                    else:
                        logger.warning(
                            "WhatsApp Meta webhook registration: %s",
                            reg,
                        )

                asyncio.create_task(
                    _run_after_startup(),
                    name="meta_whatsapp_webhook_register",
                )

            server.lifecycle_manager.add_startup_hook(_deferred_meta_webhook_register)
            if action_id:
                _meta_webhook_startup_hooks.add(action_id)
        except Exception as e:
            logger.warning(
                "WhatsApp meta: failed to schedule deferred webhook registration: %s",
                e,
            )

    async def _meta_webhook_stale_check(
        self,
        callback: str,
        agent_id: str,
        wa: MetaWhatsAppAPI,
    ) -> Dict[str, Any]:
        """Fetch Graph webhook config and flag URLs that do not match this agent."""
        graph = await wa.get_webhook_override_status()
        stale = find_stale_callbacks(graph, callback, agent_id)
        if stale:
            for item in stale:
                logger.warning(
                    "Meta webhook stale callback (%s): %s (agent_id=%s, expected=%s)",
                    item.get("source"),
                    item.get("url"),
                    item.get("agent_id"),
                    agent_id,
                )
        return {
            "graph": graph,
            "stale_callbacks": stale,
            "dashboard_action": dashboard_action_for_stale(stale),
        }

    async def get_meta_webhook_override_status(self) -> Dict[str, Any]:
        """Return Meta Graph state for WABA/phone webhook override (not App Dashboard)."""
        if not self.is_meta_provider() or not self.is_configured():
            return {
                "status": "skipped",
                "reason": "meta provider not configured",
                "issues": self._config_issues(),
            }
        callback = self.meta_callback_url_for_subscription(self.webhook_url or "")
        if not callback and self.webhook_url:
            callback = self.meta_callback_url_for_subscription(self.webhook_url)
        if not self.webhook_url:
            try:
                url = await self.get_webhook_url()
                callback = self.meta_callback_url_for_subscription(url)
            except ValidationError:
                callback = ""
        agent = await self.get_agent()
        agent_id = str(agent.id) if agent else ""
        expected_agent_id = agent_id or agent_id_from_callback_url(callback)
        verify = self.effective_verify_token(agent_id)
        wa = await self.api()
        check = await self._meta_webhook_stale_check(callback, expected_agent_id, wa)
        return {
            "expected_callback_url": callback,
            "expected_agent_id": expected_agent_id,
            "verify_token": verify,
            "stale_callbacks": check["stale_callbacks"],
            "dashboard_action": check["dashboard_action"]
            or (
                "Meta App Dashboard shows the app default callback URL only. "
                "WABA/phone overrides appear here and in Graph subscribed_apps."
            ),
            "dashboard_note": (
                "Meta App Dashboard shows the app default callback URL only. "
                "WABA/phone overrides appear here and in Graph subscribed_apps."
            ),
            "graph": check["graph"],
        }

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

            if self.is_meta_provider():
                skip_meta_webhook = (
                    os.environ.get(
                        "WHATSAPP_SKIP_STARTUP_WEBHOOK_REGISTRATION", ""
                    ).lower()
                    == "true"
                )
                if skip_meta_webhook:
                    logger.info(
                        "WhatsApp meta Graph webhook registration skipped "
                        "(WHATSAPP_SKIP_STARTUP_WEBHOOK_REGISTRATION=true). "
                        "Use POST /api/actions/{action_id}/session/register or "
                        "POST .../meta/webhook-register."
                    )
                    result = {
                        "status": "skipped",
                        "reason": "WHATSAPP_SKIP_STARTUP_WEBHOOK_REGISTRATION=true",
                        "ok": True,
                    }
                else:
                    if not self.webhook_url:
                        self.webhook_url = await self.get_webhook_url()

                    self._schedule_deferred_meta_webhook_register()
                    result = {
                        "status": "pending",
                        "reason": "meta_webhook_register_scheduled",
                        "ok": True,
                    }
            elif skip_registration:
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
                and result.get("status") not in ("ERROR", "pending")
            ):
                self._session_registered = True
                if self.is_meta_provider():
                    logger.info("WhatsApp meta provider ready (Cloud API)")
                else:
                    sess = await self._effective_whatsapp_session()
                    logger.info(
                        "WhatsApp session %r registered on startup", sess or "(unknown)"
                    )
            elif (
                isinstance(result, dict)
                and result.get("status") == "pending"
                and self.is_meta_provider()
            ):
                logger.info(
                    "WhatsApp meta provider: webhook override registration scheduled"
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
    ) -> Union[
        WPPConnectAPI, WWebJSAPI, UltraMsgAPI, MetaWhatsAppAPI, JvconnectWhatsAppAPI
    ]:
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
                agent = await self.get_agent()
                agent_id = str(agent.id) if agent else ""
                factory = get_provider_factory("jvconnect")
                if factory is None:
                    raise ValidationError("jvconnect provider is not registered")
                client = factory(
                    api_url=self._env_jvconnect_url(),
                    session=phone_id or "jvconnect",
                    token=self._env_jvconnect_api_key(),
                    secret_key=self._env_jvconnect_webhook_secret(),
                    timeout=timeout,
                    phone_number_id=phone_id,
                    waba_id=self._env_waba_id(),
                    verify_token=self.effective_verify_token(agent_id),
                )
                # Resolve phone/WABA from the phone-bound API key when not set locally
                if not phone_id or not self._env_waba_id():
                    try:
                        account = await client.fetch_account()
                        if account.get("ok") and account.get("phone_number_id"):
                            resolved = str(account["phone_number_id"])
                            object.__setattr__(self, "phone_number_id", resolved)
                            if account.get("waba_id") and not self._env_waba_id():
                                object.__setattr__(
                                    self, "waba_id", str(account["waba_id"])
                                )
                    except Exception as acct_err:
                        logger.warning(
                            "jvconnect account lookup failed (will retry on use): %s",
                            acct_err,
                        )
                return client

            session = await self._effective_whatsapp_session()
            if not session or not session.strip():
                raise ValidationError(
                    "WhatsApp session name is unavailable (set WHATSAPP_SESSION, "
                    "configure session on the action, or ensure agent name is available)"
                )

            api_url = self._whatsapp_api_url()
            factory = get_provider_factory(self.provider)
            if factory is None:
                raise ValidationError(f"Unsupported provider: {self.provider}")
            return factory(
                api_url=api_url,
                session=session,
                token=self._env_token(),
                secret_key=self._env_api_key(),
                timeout=timeout,
            )
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

    async def register_meta_webhook_subscription(self) -> Dict[str, Any]:
        """Set Meta WhatsApp webhook override (WABA or phone number) to this agent's callback URL."""
        if not self.is_meta_provider():
            return {
                "status": "skipped",
                "reason": "not_meta_provider",
                "ok": True,
            }
        if not self.is_configured():
            return {
                "status": "skipped",
                "reason": "WhatsApp action is not configured",
                "issues": self._config_issues(),
            }
        base_url = get_public_base_url()
        if not base_url:
            return {
                "status": "skipped",
                "reason": "JVAGENT_PUBLIC_BASE_URL is not set",
            }
        try:
            if not self.webhook_url:
                await self.get_webhook_url()
            callback = self.meta_callback_url_for_subscription(self.webhook_url or "")
            if not callback:
                return {"status": "skipped", "reason": "no webhook_url", "ok": False}

            agent = await self.get_agent()
            agent_id = str(agent.id) if agent else ""
            verify = self.effective_verify_token(agent_id)
            logger.info(
                "Registering Meta WhatsApp webhook override (agent_id=%s callback=%s). "
                "Meta will GET hub.challenge on that URL before accepting it.",
                agent_id,
                callback,
            )
            wa = await self.api()
            result = await wa.register_webhook_subscription(callback, verify)
            # Persist jvconnect-issued webhook HMAC secret for inbound verification
            if self.is_meta_provider() and isinstance(result, dict):
                secret = str(result.get("webhook_secret") or "").strip()
                if secret:
                    object.__setattr__(self, "jvconnect_webhook_secret", secret)
                    try:
                        await self.save()
                    except Exception as save_err:
                        logger.warning(
                            "Failed to persist jvconnect_webhook_secret: %s", save_err
                        )
            ok = bool(result.get("success") or result.get("ok"))
            if not ok:
                err_msg = str(result.get("error") or result)
                logger.warning(
                    "Meta WhatsApp webhook override Graph error: %s", err_msg
                )
                if self.is_meta_provider():
                    logger.warning(
                        "jvconnect webhook registration failed. Ensure JVCONNECT_URL "
                        "and JVCONNECT_API_KEY are valid and APP_BASE_URL is set on "
                        "jvconnect so Meta can verify %s/api/webhooks",
                        self._env_jvconnect_url(),
                    )
                return {
                    "status": "error",
                    "ok": False,
                    "callback_url": callback,
                    "agent_id": agent_id,
                    "result": result,
                }
            logger.info(
                "WhatsApp Meta webhook override set (agent_id=%s callback=%s)",
                agent_id,
                callback,
            )
            stale_check = await self._meta_webhook_stale_check(callback, agent_id, wa)
            return {
                "status": "ok",
                "ok": True,
                "callback_url": callback,
                "agent_id": agent_id,
                "expected_agent_id": agent_id,
                "stale_callbacks": stale_check["stale_callbacks"],
                "dashboard_action": stale_check["dashboard_action"],
                "result": result,
                "graph": stale_check["graph"],
            }
        except ValidationError as e:
            logger.warning("register_meta_webhook_subscription: %s", e)
            return {"status": "error", "ok": False, "error": str(e)}
        except Exception as e:
            logger.error(
                "register_meta_webhook_subscription failed: %s", e, exc_info=True
            )
            return {"status": "error", "ok": False, "error": str(e)}

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
            return await self.register_meta_webhook_subscription()

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

    # ------------------------------------------------------------------
    # Meta message templates (Orchestrator tools)
    # ------------------------------------------------------------------

    _TEMPLATE_CHANNEL_ERROR = "whatsapp_templates_require_inbound_whatsapp"

    def _template_dispatch_gate(self) -> Optional[Dict[str, Any]]:
        """Require inbound WhatsApp channel + user_id. Returns error envelope or None."""
        ctx = get_dispatch_context()
        if (
            not ctx
            or (ctx.channel or "").lower() != "whatsapp"
            or not (ctx.user_id or "").strip()
        ):
            return {"ok": False, "error": self._TEMPLATE_CHANNEL_ERROR}
        return None

    def _template_allowlist_names(self) -> List[str]:
        raw = self.template_allowlist or []
        return [str(n).strip().lower() for n in raw if str(n).strip()]

    def _filter_templates_by_allowlist(
        self, templates: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        allowed = self._template_allowlist_names()
        if not allowed:
            return templates
        return [
            t
            for t in templates
            if isinstance(t, dict)
            and str(t.get("name") or "").strip().lower() in allowed
        ]

    def _is_template_allowed(self, template_name: str) -> bool:
        allowed = self._template_allowlist_names()
        if not allowed:
            return True
        return (template_name or "").strip().lower() in allowed

    async def _record_template_send(
        self, template_name: str, language: str, to: str, result: Dict[str, Any]
    ) -> None:
        """Tag the current Interaction so history notes the HSM send."""
        visitor = get_tool_visitor()
        interaction = getattr(visitor, "interaction", None) if visitor else None
        if interaction is None:
            return
        message_id = ""
        messages = result.get("messages") if isinstance(result, dict) else None
        if isinstance(messages, list) and messages:
            first = messages[0]
            if isinstance(first, dict):
                message_id = str(first.get("id") or "")
        try:
            interaction.add_parameter(
                {
                    "name": "whatsapp_template_sent",
                    "template_name": template_name,
                    "language": language,
                    "to": to,
                    "message_id": message_id,
                    "executed": True,
                },
                self.get_class_name(),
            )
            if hasattr(interaction, "save"):
                await interaction.save()
        except Exception:
            logger.debug("Failed to record template send on interaction", exc_info=True)

    @tool(name="whatsapp__list_templates")
    async def list_templates(self) -> str:
        """List approved WhatsApp message templates available to send on this inbound WhatsApp turn. Only works when the user messaged via WhatsApp."""
        import json

        gate = self._template_dispatch_gate()
        if gate:
            return json.dumps(gate)
        if not self.is_configured() or not self.is_meta_provider():
            return json.dumps(
                {
                    "ok": False,
                    "error": "WhatsApp Meta/jvconnect provider is not configured",
                }
            )
        try:
            api = await self.api()
            list_fn = getattr(api, "list_message_templates", None)
            if not callable(list_fn):
                return json.dumps(
                    {
                        "ok": False,
                        "error": "list_message_templates unsupported for this provider",
                    }
                )
            result = await list_fn()
            if not result.get("ok", True) or result.get("error"):
                return json.dumps(
                    {
                        "ok": False,
                        "error": result.get("error") or "failed to list templates",
                    }
                )
            templates = result.get("templates") or []
            if not isinstance(templates, list):
                templates = []
            filtered = self._filter_templates_by_allowlist(
                [t for t in templates if isinstance(t, dict)]
            )
            slim = [
                {
                    "name": t.get("name"),
                    "language": t.get("language"),
                    "status": t.get("status"),
                    "category": t.get("category"),
                }
                for t in filtered
            ]
            return json.dumps({"ok": True, "templates": slim})
        except Exception as e:
            logger.exception("whatsapp__list_templates failed")
            return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"})

    @tool(name="whatsapp__send_template")
    async def send_template(
        self,
        template_name: Annotated[
            str,
            "Exact Meta template name (e.g. signup). Must be allowlisted when allowlist is set.",
        ],
        language: Annotated[
            Optional[str],
            "Template language code (e.g. en_US). Defaults to action default_template_language.",
        ] = None,
        components: Annotated[
            Optional[List[Dict[str, Any]]],
            "Optional Meta template components array (header/body/button parameters).",
        ] = None,
    ) -> str:
        """Send an approved WhatsApp template to the same phone that messaged this turn. Only works on inbound WhatsApp; recipient is fixed to the sender — do not invent a phone number."""
        import json

        gate = self._template_dispatch_gate()
        if gate:
            return json.dumps(gate)
        ctx = get_dispatch_context()
        assert ctx is not None  # gate passed
        to = (ctx.user_id or "").strip()
        name = (template_name or "").strip()
        if not name:
            return json.dumps({"ok": False, "error": "template_name is required"})
        if not self._is_template_allowed(name):
            return json.dumps(
                {
                    "ok": False,
                    "error": f"template '{name}' is not on the agent allowlist",
                    "allowlist": self._template_allowlist_names(),
                }
            )
        if not self.is_configured() or not self.is_meta_provider():
            return json.dumps(
                {
                    "ok": False,
                    "error": "WhatsApp Meta/jvconnect provider is not configured",
                }
            )
        lang = (
            (language or "").strip()
            or (self.default_template_language or "en_US").strip()
            or "en_US"
        )
        try:
            api = await self.api()
            send_fn = getattr(api, "send_template_message", None)
            if not callable(send_fn):
                return json.dumps(
                    {
                        "ok": False,
                        "error": "send_template_message unsupported for this provider",
                    }
                )
            result = await send_fn(to, name, language=lang, components=components or [])
            if not result.get("ok", True) or result.get("error"):
                err = result.get("error")
                if isinstance(err, dict):
                    err = err.get("message") or err.get("error_user_msg") or str(err)
                return json.dumps(
                    {
                        "ok": False,
                        "error": err or "failed to send template",
                        "raw": result,
                    }
                )
            await self._record_template_send(name, lang, to, result)
            messages = result.get("messages") or []
            message_id = ""
            if (
                isinstance(messages, list)
                and messages
                and isinstance(messages[0], dict)
            ):
                message_id = str(messages[0].get("id") or "")
            return json.dumps(
                {
                    "ok": True,
                    "template_name": name,
                    "language": lang,
                    "to": to,
                    "message_id": message_id,
                }
            )
        except Exception as e:
            logger.exception("whatsapp__send_template failed")
            return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"})

    # ------------------------------------------------------------------
    # Meta WhatsApp Flows (Orchestrator tools)
    # ------------------------------------------------------------------

    _FLOW_CHANNEL_ERROR = "whatsapp_flows_require_inbound_whatsapp"

    def _flow_dispatch_gate(self) -> Optional[Dict[str, Any]]:
        ctx = get_dispatch_context()
        if (
            not ctx
            or (ctx.channel or "").lower() != "whatsapp"
            or not (ctx.user_id or "").strip()
        ):
            return {"ok": False, "error": self._FLOW_CHANNEL_ERROR}
        return None

    def _flow_allowlist_keys(self) -> List[str]:
        raw = self.flow_allowlist or []
        return [str(n).strip().lower() for n in raw if str(n).strip()]

    def _is_flow_allowed(self, flow_id: str = "", flow_name: str = "") -> bool:
        allowed = self._flow_allowlist_keys()
        if not allowed:
            return True
        candidates = {
            (flow_id or "").strip().lower(),
            (flow_name or "").strip().lower(),
        }
        candidates.discard("")
        return bool(candidates & set(allowed))

    def _filter_flows_by_allowlist(
        self, flows: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        allowed = self._flow_allowlist_keys()
        if not allowed:
            return flows
        out: List[Dict[str, Any]] = []
        for f in flows:
            if not isinstance(f, dict):
                continue
            if self._is_flow_allowed(str(f.get("id") or ""), str(f.get("name") or "")):
                out.append(f)
        return out

    async def _record_flow_send(
        self,
        *,
        flow_id: str,
        flow_name: str,
        to: str,
        result: Dict[str, Any],
    ) -> None:
        visitor = get_tool_visitor()
        interaction = getattr(visitor, "interaction", None) if visitor else None
        if interaction is None:
            return
        message_id = ""
        messages = result.get("messages") if isinstance(result, dict) else None
        if isinstance(messages, list) and messages:
            first = messages[0]
            if isinstance(first, dict):
                message_id = str(first.get("id") or "")
        try:
            interaction.add_parameter(
                {
                    "name": "whatsapp_flow_sent",
                    "flow_id": flow_id,
                    "flow_name": flow_name,
                    "to": to,
                    "message_id": message_id,
                    "executed": True,
                },
                self.get_class_name(),
            )
            if hasattr(interaction, "save"):
                await interaction.save()
        except Exception:
            logger.debug("Failed to record flow send on interaction", exc_info=True)

    @tool(name="whatsapp__list_flows")
    async def list_flows(self) -> str:
        """List WhatsApp Flows available to send on this inbound WhatsApp turn. Only works when the user messaged via WhatsApp."""
        import json

        gate = self._flow_dispatch_gate()
        if gate:
            return json.dumps(gate)
        if not self.is_configured() or not self.is_meta_provider():
            return json.dumps(
                {
                    "ok": False,
                    "error": "WhatsApp Meta/jvconnect provider is not configured",
                }
            )
        try:
            api = await self.api()
            list_fn = getattr(api, "list_flows", None)
            if not callable(list_fn):
                return json.dumps(
                    {"ok": False, "error": "list_flows unsupported for this provider"}
                )
            result = await list_fn()
            if not result.get("ok", True) or result.get("error"):
                return json.dumps(
                    {
                        "ok": False,
                        "error": result.get("error") or "failed to list flows",
                    }
                )
            flows = result.get("flows") or []
            if not isinstance(flows, list):
                flows = []
            filtered = self._filter_flows_by_allowlist(
                [f for f in flows if isinstance(f, dict)]
            )
            slim = [
                {
                    "id": f.get("id"),
                    "name": f.get("name"),
                    "status": f.get("status"),
                    "categories": f.get("categories"),
                    "endpoint_uri": f.get("endpoint_uri"),
                }
                for f in filtered
            ]
            return json.dumps({"ok": True, "flows": slim})
        except Exception as e:
            logger.exception("whatsapp__list_flows failed")
            return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"})

    @tool(name="whatsapp__send_flow")
    async def send_flow(
        self,
        flow_id: Annotated[
            Optional[str],
            "Meta Flow id (preferred). Provide flow_id or flow_name.",
        ] = None,
        flow_name: Annotated[
            Optional[str],
            "Meta Flow name if id is unknown. Prefer flow_id when known.",
        ] = None,
        body: Annotated[
            Optional[str],
            "Message body shown above the Flow button.",
        ] = "Please complete this form.",
        flow_cta: Annotated[
            Optional[str],
            "CTA button label (max 20 chars).",
        ] = "Open",
        flow_action: Annotated[
            Optional[str],
            "Optional: navigate or data_exchange. Omit to use Meta entry screen.",
        ] = None,
        screen: Annotated[
            Optional[str],
            "Optional entry screen id when flow_action is navigate.",
        ] = None,
        mode: Annotated[
            Optional[str],
            "published (default) or draft (testers only).",
        ] = None,
    ) -> str:
        """Send a WhatsApp Flow to the same phone that messaged this turn. Only works on inbound WhatsApp; recipient is fixed to the sender."""
        import json
        import time
        import uuid

        gate = self._flow_dispatch_gate()
        if gate:
            return json.dumps(gate)
        ctx = get_dispatch_context()
        assert ctx is not None
        to = (ctx.user_id or "").strip()
        fid = (flow_id or "").strip()
        fname = (flow_name or "").strip()
        if not fid and not fname:
            return json.dumps(
                {"ok": False, "error": "flow_id or flow_name is required"}
            )
        if not self._is_flow_allowed(fid, fname):
            return json.dumps(
                {
                    "ok": False,
                    "error": "flow is not on the agent allowlist",
                    "allowlist": self._flow_allowlist_keys(),
                }
            )
        if not self.is_configured() or not self.is_meta_provider():
            return json.dumps(
                {
                    "ok": False,
                    "error": "WhatsApp Meta/jvconnect provider is not configured",
                }
            )
        token = f"jvagent_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        try:
            api = await self.api()
            send_fn = getattr(api, "send_flow_message", None)
            if not callable(send_fn):
                return json.dumps(
                    {
                        "ok": False,
                        "error": "send_flow_message unsupported for this provider",
                    }
                )
            result = await send_fn(
                to,
                flow_id=fid,
                flow_name=fname,
                flow_cta=(flow_cta or "Open").strip() or "Open",
                body=(body or "").strip() or "Please complete this form.",
                flow_token=token,
                flow_action=(flow_action or "").strip(),
                screen=(screen or "").strip(),
                mode=(mode or "").strip(),
            )
            if not result.get("ok", True) or result.get("error"):
                err = result.get("error")
                if isinstance(err, dict):
                    err = err.get("message") or err.get("error_user_msg") or str(err)
                return json.dumps(
                    {
                        "ok": False,
                        "error": err or "failed to send flow",
                        "raw": result,
                    }
                )
            await self._record_flow_send(
                flow_id=fid, flow_name=fname, to=to, result=result
            )
            messages = result.get("messages") or []
            message_id = ""
            if (
                isinstance(messages, list)
                and messages
                and isinstance(messages[0], dict)
            ):
                message_id = str(messages[0].get("id") or "")
            return json.dumps(
                {
                    "ok": True,
                    "flow_id": fid,
                    "flow_name": fname,
                    "to": to,
                    "flow_token": token,
                    "message_id": message_id,
                }
            )
        except Exception as e:
            logger.exception("whatsapp__send_flow failed")
            return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"})

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
