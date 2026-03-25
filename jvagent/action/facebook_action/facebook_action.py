"""Facebook Graph API action (Pages and Messenger)."""

import asyncio
import logging
import mimetypes
import os
import random
import string
from typing import Any, Dict, List, Optional, Union

import httpx
from jvspatial.api.auth.api_key_service import APIKeyService
from jvspatial.core.annotations import attribute
from jvspatial.core.context import GraphContext
from jvspatial.db import get_prime_database
from jvspatial.exceptions import DatabaseError, ValidationError

from jvagent.action.base import Action
from jvagent.action.whatsapp.webhook_auth import get_or_create_system_user

from .facebook_api import FacebookAPI

logger = logging.getLogger(__name__)


class FacebookAction(Action):
    """Action for Facebook Graph API (page management, Messenger, webhooks)."""

    api_url: Optional[str] = attribute(
        default=None,
        description="Graph API base URL (e.g. https://graph.facebook.com/v25.0/)",
    )
    app_secret: Optional[str] = attribute(
        default=None, description="Facebook app secret"
    )
    app_id: Optional[str] = attribute(default=None, description="Facebook app ID")
    page_id: Optional[str] = attribute(
        default=None, description="Facebook Page ID for API calls"
    )
    access_token: Optional[str] = attribute(
        default=None,
        description=(
            "User OAuth access token for /me and /me/accounts (Page token resolution)"
        ),
    )
    page_access_token: Optional[str] = attribute(
        default=None,
        description="Page access token for Page-scoped Graph API (feed, Messenger, etc.)",
    )
    verify_token: Optional[str] = attribute(
        default=None,
        description="Webhook verify token (hub.verify_token) for Meta subscriptions",
    )
    fields: Optional[str] = attribute(
        default="feed,messages,messaging_postbacks,message_deliveries,standby,mention",
        description="Webhook fields for Feed, Messenger, and Page Mentions.",
    )
    timeout: int = attribute(
        default=10, description="HTTP timeout in seconds for Graph requests", ge=1, le=120
    )
    published: bool = attribute(
        default=False,
        description="If False, page posts are created as unpublished drafts where supported",
    )
    base_url: Optional[str] = attribute(
        default=None,
        description=(
            "Application base URL for Messenger webhook generation "
            "(APP_BASE_URL env, e.g. https://myapp.example.com)"
        ),
    )
    webhook_url: Optional[str] = attribute(
        default=None,
        description="Facebook webhook URL (auto-generated if not provided)",
    )

    webhook_api_key_id: Optional[str] = attribute(
        default=None, description="ID of the API key used for webhook authentication"
    )

    stt_action: Optional[str] = attribute(
        default="DeepgramSTTAction",
        description=(
            "Label or class for speech-to-text (e.g. DeepgramSTTAction). "
            "Messenger audio is downloaded with the Page token and transcribed via "
            "invoke_base64 (same pattern as WhatsApp voice). Ignored if unset."
        ),
        min_length=1,
    )

    tts_action: Optional[str] = attribute(
        default="ElevenLabsTTSAction",
        description=(
            "Label or class for text-to-speech when replying to voice messages "
            "(e.g. ElevenLabsTTSAction). When set with inbound audio, responses use "
            "MessengerVoiceResponseFilter (WhatsApp parity)."
        ),
        min_length=1,
    )

    messenger_message_window: float = attribute(
        default=2.0,
        description=(
            "Debounce window (seconds) to merge separate Messenger webhook deliveries "
            "(e.g. caption text + image) into one interaction. Set to 0 to disable."
        ),
        ge=0.0,
        le=60.0,
    )

    def _apply_env_defaults(self) -> None:
        """Fill missing config from FACEBOOK_* and optional GRAPH_API_VERSION."""
        env_map = [
            ("api_url", "FACEBOOK_API_URL"),
            ("app_secret", "FACEBOOK_APP_SECRET"),
            ("app_id", "FACEBOOK_APP_ID"),
            ("page_id", "FACEBOOK_PAGE_ID"),
            ("access_token", "FACEBOOK_ACCESS_TOKEN"),
            ("verify_token", "FACEBOOK_VERIFY_TOKEN"),
            ("fields", "FACEBOOK_WEBHOOK_FIELDS"),
        ]
        for attr, env_key in env_map:
            current = getattr(self, attr, None)
            if current is None or (isinstance(current, str) and not current.strip()):
                val = os.environ.get(env_key, "").strip()
                if val:
                    setattr(self, attr, val)
                    logger.debug("Using %s from environment for Facebook action", env_key)

        pat = getattr(self, "page_access_token", None)
        if pat is None or (isinstance(pat, str) and not pat.strip()):
            val = os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN", "").strip()
            if val:
                self.page_access_token = val

        if not self.api_url or not str(self.api_url).strip():
            base = os.environ.get("FACEBOOK_GRAPH_BASE", "").strip()
            version = os.environ.get("FACEBOOK_GRAPH_VERSION", "v25.0").strip() or "v25.0"
            if base:
                self.api_url = base.rstrip("/") + f"/{version}/"
            else:
                self.api_url = f"https://graph.facebook.com/{version}/"

        if not self.base_url or not str(self.base_url).strip():
            env_base = os.environ.get("APP_BASE_URL", "").strip()
            if env_base:
                self.base_url = env_base
                logger.debug("Using APP_BASE_URL from environment for Facebook action")

        env_win = os.environ.get("MESSENGER_MESSAGE_WINDOW", "").strip()
        if env_win:
            try:
                self.messenger_message_window = float(env_win)
            except ValueError:
                logger.debug("Invalid MESSENGER_MESSAGE_WINDOW, keeping configured value")

    @staticmethod
    def meta_callback_url_for_subscription(webhook_url: str) -> str:
        """Strip ``?api_key=...`` for Meta Graph subscribe (hub.verify GET has no API key)."""
        s = (webhook_url or "").strip()
        if not s:
            return s
        q = s.find("?")
        return s[:q] if q >= 0 else s

    def _base_graph_config_issues(self) -> List[str]:
        issues: List[str] = []
        if not self.api_url or not str(self.api_url).strip():
            issues.append("api_url (FACEBOOK_API_URL / FACEBOOK_GRAPH_*) is not configured")
        elif not str(self.api_url).strip().startswith(("http://", "https://")):
            issues.append("api_url must be a valid HTTP/HTTPS URL")
        for name, label in [
            ("app_secret", "FACEBOOK_APP_SECRET"),
            ("app_id", "FACEBOOK_APP_ID"),
            ("page_id", "FACEBOOK_PAGE_ID"),
        ]:
            val = getattr(self, name, None)
            if not val or not str(val).strip():
                issues.append(f"{name} ({label}) is not configured")
        return issues

    def _config_issues(self) -> List[str]:
        issues = self._base_graph_config_issues()
        has_user = bool(self.access_token and str(self.access_token).strip())
        has_page = bool(self.page_access_token and str(self.page_access_token).strip())
        if not has_user and not has_page:
            issues.append(
                "Set FACEBOOK_ACCESS_TOKEN (user) and/or FACEBOOK_PAGE_ACCESS_TOKEN (Page)"
            )
        return issues

    def is_configured(self) -> bool:
        return len(self._config_issues()) == 0

    def _make_api(self) -> Optional[FacebookAPI]:
        self._apply_env_defaults()
        if self._base_graph_config_issues():
            return None
        page_tok = str(self.page_access_token).strip() if self.page_access_token else ""
        if not page_tok:
            return None
        api_url = str(self.api_url).strip()
        if not api_url.endswith("/"):
            api_url = api_url + "/"
        user_tok = str(self.access_token).strip() if self.access_token else ""
        return FacebookAPI(
            api_url=api_url,
            app_secret=str(self.app_secret).strip(),
            app_id=str(self.app_id).strip(),
            page_id=str(self.page_id).strip(),
            page_access_token=page_tok,
            verify_token=str(self.verify_token or "").strip(),
            fields=(str(self.fields).strip() if self.fields else None) or None,
            timeout=int(self.timeout),
            published=bool(self.published),
            user_access_token=user_tok or None,
            app_access_token=None,
        )

    def _build_api_for_page_discovery(self) -> Optional[FacebookAPI]:
        """Graph client for ``me/accounts`` only (user token; empty Page token)."""
        self._apply_env_defaults()
        if self._base_graph_config_issues():
            return None
        user_tok = str(self.access_token).strip() if self.access_token else ""
        if not user_tok:
            return None
        api_url = str(self.api_url).strip()
        if not api_url.endswith("/"):
            api_url = api_url + "/"
        return FacebookAPI(
            api_url=api_url,
            app_secret=str(self.app_secret).strip(),
            app_id=str(self.app_id).strip(),
            page_id=str(self.page_id).strip(),
            page_access_token="",
            verify_token=str(self.verify_token or "").strip(),
            fields=(str(self.fields).strip() if self.fields else None) or None,
            timeout=int(self.timeout),
            published=bool(self.published),
            user_access_token=user_tok,
            app_access_token=None,
        )

    def discovery_api(self) -> FacebookAPI:
        """Client for user-scoped calls (``/me``, ``/me/accounts``)."""
        api = self._build_api_for_page_discovery()
        if api is None:
            raise ValidationError(
                message=(
                    "User access_token (FACEBOOK_ACCESS_TOKEN) is required for "
                    "/me and /me/accounts"
                ),
                details={"action_id": getattr(self, "id", None)},
            )
        return api

    def app_api(self) -> FacebookAPI:
        """Client for app-scoped Graph (e.g. webhook subscriptions); uses app access token."""
        self._apply_env_defaults()
        issues = self._base_graph_config_issues()
        if issues:
            raise ValidationError(
                message="Facebook action is not configured: " + "; ".join(issues),
                details={"action_id": getattr(self, "id", None)},
            )
        api_url = str(self.api_url).strip()
        if not api_url.endswith("/"):
            api_url = api_url + "/"
        return FacebookAPI(
            api_url=api_url,
            app_secret=str(self.app_secret).strip(),
            app_id=str(self.app_id).strip(),
            page_id=str(self.page_id).strip(),
            page_access_token="",
            verify_token=str(self.verify_token or "").strip(),
            fields=(str(self.fields).strip() if self.fields else None) or None,
            timeout=int(self.timeout),
            published=bool(self.published),
            user_access_token=None,
            app_access_token=None,
        )

    def api(self) -> FacebookAPI:
        """Return a FacebookAPI client with Page token; raises if not ready."""
        self._apply_env_defaults()
        if self._base_graph_config_issues():
            raise ValidationError(
                message=(
                    "Facebook action is not configured: "
                    + "; ".join(self._base_graph_config_issues())
                ),
                details={"action_id": getattr(self, "id", None)},
            )
        page_tok = str(self.page_access_token).strip() if self.page_access_token else ""
        if not page_tok:
            raise ValidationError(
                message=(
                    "page/access_token for Page is missing. Set FACEBOOK_PAGE_ACCESS_TOKEN "
                    "or keep FACEBOOK_ACCESS_TOKEN (user) and reload/register so the Page "
                    "token can be resolved from me/accounts."
                ),
                details={"action_id": getattr(self, "id", None)},
            )
        client = self._make_api()
        if client is None:
            raise ValidationError(
                message=(
                    "Facebook action is not configured: "
                    + "; ".join(self._config_issues())
                ),
                details={"action_id": getattr(self, "id", None)},
            )
        return client

    async def _maybe_resolve_page_access_token(self) -> None:
        if self.page_access_token and str(self.page_access_token).strip():
            return
        user_tok = str(self.access_token).strip() if self.access_token else ""
        page_id = str(self.page_id).strip() if self.page_id else ""
        if not user_tok or not page_id:
            return
        if self._base_graph_config_issues():
            return
        await self.ensure_page_access_token()

    def parse_messenger_webhook_verify(
        self, query: Dict[str, Any]
    ) -> Union[str, Dict[str, Any]]:
        """Meta GET webhook verification (hub.* query params). No Graph token required."""
        self._apply_env_defaults()
        expected = str(self.verify_token or "").strip()
        mode = query.get("hub.mode")
        hub_verify = query.get("hub.verify_token")
        challenge = query.get("hub.challenge")
        if hub_verify == expected and mode == "subscribe":
            return str(challenge) if challenge is not None else ""
        return {"message": "Invalid token or mode", "code": 403}

    async def _ensure_messenger_webhook_url(self) -> None:
        """Create and persist ``webhook_url`` / ``webhook_api_key_id`` when possible."""
        self._apply_env_defaults()
        action_id = getattr(self, "id", None)
        action_label = getattr(self, "label", None)
        if not self.is_configured():
            issues = self._config_issues()
            logger.warning(
                "FacebookAction id=%s label=%s: skip ensure messenger webhook_url "
                "(not configured); issues=%s",
                action_id,
                action_label,
                issues[:5] if issues else [],
            )
            return
        if not self.base_url or not str(self.base_url).strip():
            logger.warning(
                "FacebookAction id=%s label=%s: skip ensure messenger webhook_url "
                "(set base_url or APP_BASE_URL)",
                action_id,
                action_label,
            )
            return
        try:
            agent = await self.get_agent()
            if not agent:
                logger.warning(
                    "FacebookAction id=%s: skip ensure messenger webhook_url "
                    "(agent not found)",
                    action_id,
                )
                return
            base = str(self.base_url).strip().rstrip("/")
            expected_prefix = f"{base}/api/messenger/interact/webhook/{str(agent.id)}"
            if (
                self.webhook_url
                and "?api_key=" in self.webhook_url
                and self.webhook_url.startswith(expected_prefix)
            ):
                logger.debug(
                    "FacebookAction id=%s: messenger webhook_url already set for "
                    "this base_url/agent",
                    action_id,
                )
                return
            await self.get_webhook_url(regenerate=False)
            meta_url = self.meta_callback_url_for_subscription(self.webhook_url or "")
            logger.info(
                "FacebookAction id=%s: Messenger webhook URL persisted (meta_callback_url=%s; "
                "full stored URL includes api_key, not logged)",
                action_id,
                meta_url,
            )
        except ValidationError as e:
            logger.warning(
                "FacebookAction id=%s: ensure messenger webhook_url failed: %s",
                action_id,
                e,
                exc_info=True,
            )
        except Exception as e:
            logger.warning(
                "FacebookAction id=%s: ensure messenger webhook_url unexpected error: %s",
                action_id,
                e,
                exc_info=True,
            )

    async def get_webhook_url(
        self, allowed_ip: Optional[str] = None, regenerate: bool = False
    ) -> str:
        """Generate or retrieve Messenger webhook URL with API key (for manual/testing tools).

        Meta ``register_session`` must use :meth:`meta_callback_url_for_subscription` so
        the subscribed URL does not require ``api_key`` on GET verify.
        """
        self._apply_env_defaults()
        if not self.base_url or not str(self.base_url).strip():
            raise ValidationError(
                "base_url (APP_BASE_URL) is required for webhook URL generation"
            )
        if not str(self.base_url).strip().startswith(("http://", "https://")):
            raise ValidationError(
                f"base_url must be a valid HTTP/HTTPS URL, got: {self.base_url}"
            )

        try:
            agent = await self.get_agent()
            if not agent:
                raise ValidationError("Agent not found for FacebookAction")
            agent_id = str(agent.id)
            base = str(self.base_url).strip().rstrip("/")
            expected_url_base = f"{base}/api/messenger/interact/webhook/{agent_id}"

            prime_ctx = GraphContext(database=get_prime_database())
            api_key_service = APIKeyService(context=prime_ctx)

            if (
                not regenerate
                and self.webhook_url
                and "?api_key=" in self.webhook_url
                and self.webhook_url.startswith(expected_url_base)
            ):
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
                    except Exception:
                        pass
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
                name=f"Messenger Webhook - {agent.name}",
                permissions=["webhook:messenger"],
                expires_in_days=None,
                allowed_ips=[allowed_ip] if allowed_ip else [],
                allowed_endpoints=["/api/messenger/interact/webhook/*"],
                key_prefix="jv_",
            )

            self.webhook_api_key_id = api_key.id
            self.webhook_url = f"{expected_url_base}?api_key={plaintext_key}"
            await self.save()
            return self.webhook_url

        except DatabaseError:
            raise
        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(f"Webhook URL generation failed: {e}") from e

    async def register_messenger_webhook_subscription(self) -> Dict[str, Any]:
        """Subscribe Page webhook with Meta (callback URL without ``api_key`` query)."""
        self._apply_env_defaults()
        if not self.is_configured():
            return {
                "status": "skipped",
                "reason": "Facebook action is not configured",
                "issues": self._config_issues(),
            }
        if not self.base_url or not str(self.base_url).strip():
            return {
                "status": "skipped",
                "reason": "base_url (APP_BASE_URL) is not set",
            }
        try:
            if not self.webhook_url:
                logger.warning(
                    "register_messenger_webhook_subscription: webhook_url empty "
                    "(action_id=%s); calling get_webhook_url",
                    getattr(self, "id", None),
                )
                await self.get_webhook_url()
            callback = self.meta_callback_url_for_subscription(self.webhook_url or "")
            if not callback:
                return {"status": "skipped", "reason": "no webhook_url"}

            def _register() -> Dict[str, Any]:
                return self.app_api().register_session(callback)

            result = await asyncio.to_thread(_register)
            if isinstance(result, dict) and result.get("error"):
                err_msg = str(result.get("error", ""))
                logger.warning(
                    "Meta webhook subscription Graph error: %s",
                    result.get("error"),
                )
                if "502" in err_msg or "Callback verification" in err_msg:
                    logger.warning(
                        "Meta could not verify the callback URL (often HTTP 502 from "
                        "your edge). Ensure APP_BASE_URL is public HTTPS, your tunnel "
                        "or load balancer is up, and GET "
                        "/api/messenger/interact/webhook/{agent_id}?hub.mode=subscribe&… "
                        "returns 200 before subscribing. Try "
                        "FACEBOOK_WEBHOOK_REGISTER_DELAY_SECONDS (default 8) or register "
                        "after the app is reachable (FACEBOOK_SKIP_STARTUP_WEBHOOK_"
                        "REGISTRATION=true then call admin webhook-url + Graph register)."
                    )
            else:
                logger.info(
                    "Messenger webhook subscribed with Meta (callback=%s)",
                    callback,
                )
            return {"status": "ok", "callback_url": callback, "result": result}
        except ValidationError as e:
            logger.warning("register_messenger_webhook_subscription: %s", e)
            return {"status": "error", "error": str(e)}
        except Exception as e:
            logger.error(
                "register_messenger_webhook_subscription failed: %s",
                e,
                exc_info=True,
            )
            return {"status": "error", "error": str(e)}

    async def on_register(self) -> None:
        self._apply_env_defaults()
        if self._base_graph_config_issues():
            logger.debug("Facebook action missing base Graph config")
            return
        await self._maybe_resolve_page_access_token()
        await self._ensure_messenger_webhook_url()
        logger.debug("Facebook action registered")

    async def on_reload(self) -> None:
        self._apply_env_defaults()
        await self._maybe_resolve_page_access_token()
        if not self.is_configured() or not self.enabled:
            logger.warning(
                "FacebookAction on_reload id=%s: skip webhook ensure/subscribe "
                "(configured=%s enabled=%s)",
                getattr(self, "id", None),
                self.is_configured(),
                self.enabled,
            )
            return
        await self._ensure_messenger_webhook_url()
        skip_subscribe = (
            os.environ.get("FACEBOOK_RELOAD_WEBHOOK_SUBSCRIBE", "true").lower()
            == "false"
        )
        if skip_subscribe:
            logger.info(
                "FacebookAction on_reload: Meta subscribe skipped "
                "(FACEBOOK_RELOAD_WEBHOOK_SUBSCRIBE=false)"
            )
        else:
            reg = await self.register_messenger_webhook_subscription()
            if reg.get("status") not in ("ok",):
                logger.warning(
                    "FacebookAction on_reload: register_messenger_webhook_subscription: %s",
                    reg,
                )

    async def on_startup(self) -> None:
        """Register Messenger response-bus filter and adapter when configured."""
        self._apply_env_defaults()
        if not self.is_configured() or not self.enabled:
            return
        agent = await self.get_agent()
        if not agent:
            logger.warning(
                "FacebookAction: agent not found; skipping Messenger bus registration"
            )
            return

        if self.base_url and str(self.base_url).strip():
            await self._ensure_messenger_webhook_url()
            if not self.webhook_url or "?api_key=" not in (self.webhook_url or ""):
                logger.warning(
                    "FacebookAction on_startup id=%s: webhook_url still empty or missing "
                    "api_key after _ensure_messenger_webhook_url; check warnings above",
                    getattr(self, "id", None),
                )

        from .messenger_adapter import MessengerAdapter
        from .messenger_filter import MessengerFilter
        from .messenger_voice_filter import MessengerVoiceResponseFilter

        if not await MessengerFilter(channels=["messenger"], priority=100).initialize(
            agent=agent
        ):
            logger.warning("MessengerFilter initialization failed")

        if self.tts_action:
            voice_filter = MessengerVoiceResponseFilter(
                action=self, channels=["messenger"], priority=105
            )
            if not await voice_filter.initialize(agent=agent):
                logger.warning("MessengerVoiceResponseFilter initialization failed")

        if not await MessengerAdapter(action=self).initialize(agent=agent):
            logger.error("MessengerAdapter initialization failed")

        skip_reg = (
            os.environ.get(
                "FACEBOOK_SKIP_STARTUP_WEBHOOK_REGISTRATION", ""
            ).lower()
            == "true"
        )
        if skip_reg:
            logger.info(
                "Facebook Messenger Graph webhook registration skipped "
                "(FACEBOOK_SKIP_STARTUP_WEBHOOK_REGISTRATION=true). "
                "Use admin GET .../facebook/messenger/webhook-url or register manually."
            )
        elif self.base_url and str(self.base_url).strip():

            async def _deferred_messenger_webhook_register() -> None:
                """Let the HTTP server and any tunnel come up before Meta probes the URL."""
                try:
                    delay_raw = os.environ.get(
                        "FACEBOOK_WEBHOOK_REGISTER_DELAY_SECONDS", "8"
                    )
                    delay_sec = max(0.0, float(delay_raw))
                except (ValueError, TypeError):
                    delay_sec = 8.0
                if delay_sec > 0:
                    logger.info(
                        "Deferring Meta webhook subscription by %.1fs (%s)",
                        delay_sec,
                        "FACEBOOK_WEBHOOK_REGISTER_DELAY_SECONDS",
                    )
                    await asyncio.sleep(delay_sec)
                if not self.webhook_url or "?api_key=" not in (self.webhook_url or ""):
                    logger.warning(
                        "FacebookAction id=%s: webhook_url still empty before deferred "
                        "Meta subscribe; register_messenger_webhook_subscription will try "
                        "get_webhook_url",
                        getattr(self, "id", None),
                    )
                reg = await self.register_messenger_webhook_subscription()
                if reg.get("status") not in ("ok",):
                    logger.warning(
                        "Facebook deferred webhook registration: %s",
                        reg,
                    )

            asyncio.create_task(_deferred_messenger_webhook_register())

    async def ensure_adapter_registered(self) -> bool:
        """Ensure Messenger ChannelAdapter is registered (e.g. Lambda cold start)."""
        if not self.is_configured():
            return False
        try:
            agent = await self.get_agent()
            if not agent:
                return False
            response_bus = await agent.get_response_bus()
            if not response_bus:
                return False
            existing = response_bus._channel_adapters.get("messenger")
            if existing and getattr(existing, "_initialized", False):
                return True
            from .messenger_adapter import MessengerAdapter

            return await MessengerAdapter(action=self).initialize(agent=agent)
        except Exception as e:
            logger.error(
                "FacebookAction: ensure_adapter_registered failed: %s",
                e,
                exc_info=True,
            )
            return False

    async def ensure_page_access_token(self) -> Dict[str, Any]:
        """Resolve Page token from ``me/accounts`` when unset; persist on match."""
        self._apply_env_defaults()
        if self.page_access_token and str(self.page_access_token).strip():
            return {"updated": False, "reason": "already_set"}
        page_id = str(self.page_id).strip() if self.page_id else ""
        if not page_id:
            return {"updated": False, "reason": "no_page_id"}
        user_tok = str(self.access_token).strip() if self.access_token else ""
        if not user_tok:
            return {"updated": False, "reason": "no_user_access_token"}

        api = self._build_api_for_page_discovery()
        if api is None:
            return {"updated": False, "reason": "not_configured"}

        def _list() -> Union[List[Any], Dict[str, Any]]:
            return api.list_all_pages(limit=500)

        pages = await asyncio.to_thread(_list)
        if isinstance(pages, dict) and pages.get("error"):
            return {"updated": False, "reason": "graph_error", "graph": pages}
        if not isinstance(pages, list):
            return {"updated": False, "reason": "unexpected_response"}

        for entry in pages:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("id", "")) != page_id:
                continue
            token = entry.get("access_token")
            if not token or not str(token).strip():
                return {"updated": False, "reason": "page_entry_missing_token"}
            self.page_access_token = str(token).strip()
            await self.save()
            self._apply_env_defaults()
            return {"updated": True, "page_id": page_id}
        return {
            "updated": False,
            "reason": "page_not_in_accounts",
            "page_id": page_id,
        }

    async def healthcheck(self) -> Union[bool, Dict[str, Any]]:
        self._apply_env_defaults()
        if not self.is_configured():
            return {
                "healthy": False,
                "issues": self._config_issues(),
            }
        if not (self.page_access_token and str(self.page_access_token).strip()):
            return {
                "healthy": False,
                "issues": [
                    "page_access_token is not set; provide it or user access_token "
                    "for auto-resolve on register/reload"
                ],
            }

        def _probe() -> Dict[str, Any]:
            return self.api().get_page_details()

        try:
            result = await asyncio.to_thread(_probe)
        except ValidationError as e:
            return {"healthy": False, "error": str(e)}

        if isinstance(result, dict) and result.get("error"):
            return {"healthy": False, "error": result.get("error"), "details": result}
        if isinstance(result, dict) and "id" in result:
            return True
        return {"healthy": False, "error": "Unexpected Graph API response", "details": result}

    async def download_url_to_public_url(self, url: str) -> Optional[str]:
        """Download a remote file into this action's storage and return a file URL.

        Replaces the former FacebookAPI.download_file (jvserve) with App-backed storage.
        """
        if not url or not url.startswith(("http://", "https://")):
            return None
        try:
            async with httpx.AsyncClient() as client:
                head = await client.head(url, follow_redirects=True)
                content_type = head.headers.get("Content-Type", "")
                main_type = content_type.split(";")[0].strip()
                extension = mimetypes.guess_extension(main_type) if main_type else None
                if not extension:
                    guessed, _ = mimetypes.guess_type(url)
                    extension = mimetypes.guess_extension(guessed or "") if guessed else None
                if not extension:
                    extension = ".bin"
                filename = (
                    "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
                    + extension
                )
                rel_path = f"fb/{filename}"
                resp = await client.get(url, follow_redirects=True)
                if resp.status_code != 200:
                    return None
                ok = await self.save_file(rel_path, resp.content)
                if not ok:
                    return None
                return await self.get_file_url(rel_path)
        except Exception as e:
            logger.error("download_url_to_public_url failed: %s", e, exc_info=True)
            return None

    def get_capabilities(self) -> List[str]:
        if not self.enabled:
            return []
        return [
            "Post and read content on a connected Facebook Page via the Graph API",
            "Send Messenger responses when wired to Meta webhooks (configure verify token and subscriptions)",
        ]
