"""Email action with pluggable providers (Gmail via OAuth, SendGrid)."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional, Tuple, Union

from jvspatial.api.auth.api_key_service import APIKeyService
from jvspatial.core.annotations import attribute
from jvspatial.core.context import GraphContext
from jvspatial.db import get_prime_database
from jvspatial.env import env
from jvspatial.exceptions import DatabaseError, ValidationError

from jvagent.action.base import Action
from jvagent.core.public_url import get_public_base_url

from .modules.base import EmailProvider
from .modules.gmail import GmailEmailProvider
from .modules.sendgrid import SendGridEmailProvider

logger = logging.getLogger(__name__)

_gmail_poll_tasks: Dict[str, asyncio.Task] = {}


class EmailAction(Action):
    """Send and receive email via Gmail (OAuth + poll) or SendGrid (API key + webhook).

    ``is_configured()`` does **not** require ``JVAGENT_PUBLIC_BASE_URL`` for Gmail
    outbound send. SendGrid inbound still needs a public URL for webhook generation.
    """

    provider: str = attribute(
        default="gmail",
        description="Email provider: gmail or sendgrid",
        pattern=r"^(gmail|sendgrid)$",
    )

    api_base: Optional[str] = attribute(
        default=None,
        description="SendGrid API base URL (default https://api.sendgrid.com/v3)",
    )

    base_url: Optional[str] = attribute(
        default=None,
        description=(
            "Public app base URL for inbound webhook (JVAGENT_PUBLIC_BASE_URL "
            "when unset); used for SendGrid inbound only"
        ),
    )

    webhook_url: Optional[str] = attribute(
        default=None,
        description="Inbound webhook URL (auto-generated with api_key query when unset)",
    )

    webhook_api_key_id: Optional[str] = attribute(
        default=None,
        description="ID of the API key used for inbound webhook authentication",
    )

    gmail_action_label: Optional[str] = attribute(
        default=None,
        description=(
            "Label of the agent's GoogleGmailAction to use when multiple exist; "
            "otherwise the first GoogleGmailAction on the agent is used"
        ),
    )

    gmail_list_query: str = attribute(
        default="is:unread in:inbox",
        description="Gmail search query for inbox polling (users.messages.list)",
    )

    gmail_list_max_results: int = attribute(
        default=25,
        description="Max message stubs to scan per poll (1–100)",
        ge=1,
        le=100,
    )

    gmail_poll_interval_seconds: float = attribute(
        default=60.0,
        description="Background poll interval; 0 disables the asyncio poll loop",
        ge=0.0,
        le=3600.0,
    )

    request_timeout: float = attribute(
        default=30.0,
        description="HTTP timeout seconds for provider API calls (SendGrid)",
        ge=5.0,
        le=120.0,
    )

    utterance_max_length: int = attribute(
        default=500_000,
        description="Maximum inbound email body length (characters) accepted",
        ge=100,
        le=2_000_000,
    )

    @staticmethod
    def _env_google_client_secrets() -> str:
        raw = env("GOOGLE_CLIENT_SECRETS_JSON") or os.environ.get(
            "GOOGLE_CLIENT_SECRETS_JSON", ""
        )
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        return ""

    @staticmethod
    def _env_sendgrid_key() -> str:
        v = (env("SENDGRID_API_KEY") or os.environ.get("SENDGRID_API_KEY") or "").strip()
        return v

    @staticmethod
    def _env_default_sender() -> str:
        fe = (env("EMAIL_DEFAULT_SENDER") or os.environ.get("EMAIL_DEFAULT_SENDER") or "").strip()
        if fe:
            return fe
        return (
            env("SENDGRID_FROM_EMAIL") or os.environ.get("SENDGRID_FROM_EMAIL") or ""
        ).strip()

    @staticmethod
    def _env_default_sender_name() -> str:
        fn = (
            env("EMAIL_DEFAULT_SENDER_NAME")
            or os.environ.get("EMAIL_DEFAULT_SENDER_NAME")
            or ""
        ).strip()
        if fn:
            return fn
        return (env("SENDGRID_FROM_NAME") or os.environ.get("SENDGRID_FROM_NAME") or "").strip()

    def _apply_env_defaults(self) -> None:
        if not self.base_url or not str(self.base_url).strip():
            env_base = get_public_base_url()
            if env_base:
                self.base_url = env_base
        prov = (self.provider or "gmail").strip().lower()
        if prov == "sendgrid":
            env_sg = (os.environ.get("SENDGRID_API_BASE_URL") or "").strip()
            if env_sg:
                self.api_base = env_sg.rstrip("/")
            elif not self.api_base or not str(self.api_base).strip():
                self.api_base = "https://api.sendgrid.com/v3"

    @staticmethod
    def _effective_api_key(action: "EmailAction") -> str:
        action._apply_env_defaults()
        prov = (action.provider or "gmail").strip().lower()
        if prov == "sendgrid":
            return EmailAction._env_sendgrid_key()
        return ""

    @staticmethod
    def _effective_sender_email(action: "EmailAction") -> str:
        action._apply_env_defaults()
        return EmailAction._env_default_sender()

    @staticmethod
    def _effective_sender_name(action: "EmailAction") -> Optional[str]:
        action._apply_env_defaults()
        n = EmailAction._env_default_sender_name()
        return n or None

    async def resolve_outbound_sender(self) -> Tuple[str, Optional[str]]:
        """Resolve From email and display name for outbound sends."""
        self._apply_env_defaults()
        prov = (self.provider or "gmail").strip().lower()
        name = self._effective_sender_name(self)
        if prov == "gmail":
            env_e = self._env_default_sender()
            if env_e:
                return env_e, name
            g = await self.get_linked_gmail_action()
            if g:
                try:
                    prof = await g.get_profile()
                    e = (prof.get("emailAddress") or "").strip()
                    if e:
                        return e, name
                except Exception:
                    logger.debug("Gmail profile fetch for sender failed", exc_info=True)
            return "", name
        return self._effective_sender_email(self), name

    async def get_linked_gmail_action(self) -> Any:
        """Return ``GoogleGmailAction`` for this agent, if any."""
        from jvagent.action.google.google_gmail_action.google_gmail_action import (
            GoogleGmailAction,
        )

        agent = await self.get_agent()
        if not agent:
            return None
        label = (self.gmail_action_label or "").strip()
        if label:
            act = await agent.get_action_by_label(label)
            if isinstance(act, GoogleGmailAction):
                return act
            return None
        act = await agent.get_action_by_type("GoogleGmailAction")
        if isinstance(act, GoogleGmailAction):
            return act
        return None

    def get_capabilities(self) -> List[str]:
        """Capabilities for PersonaAction when enabled."""
        if not self.enabled:
            return []
        prov = (self.provider or "gmail").strip().lower()
        base_caps = [
            "Send transactional email (HTML or plain text) with optional attachments "
            "via metadata key attachments (list of {filename, content_base64, type?}) "
            "or email_attachments the same shape; optional html_content / text_content / subject on the response.",
        ]
        if prov == "gmail":
            base_caps.append(
                "Receive inbound email via Gmail polling (first unread in inbox query that passes access control); "
                "requires GoogleGmailAction on the same agent with OAuth."
            )
        else:
            base_caps.append(
                "Receive inbound email via SendGrid Inbound Parse webhooks on channel email."
            )
        return base_caps

    def _config_issues(self) -> List[str]:
        self._apply_env_defaults()
        issues: List[str] = []
        prov = (self.provider or "gmail").strip().lower()
        if prov not in ("gmail", "sendgrid"):
            issues.append(f"Unsupported provider: {self.provider}")
        if prov == "gmail":
            if not self._env_google_client_secrets():
                issues.append(
                    "GOOGLE_CLIENT_SECRETS_JSON is not set in the environment "
                    "(required for Google OAuth / Gmail)"
                )
        elif prov == "sendgrid":
            if not self._env_sendgrid_key():
                issues.append("SENDGRID_API_KEY is not set in the environment")
            if not self._effective_sender_email(self):
                issues.append(
                    "EMAIL_DEFAULT_SENDER (or SENDGRID_FROM_EMAIL) is not set in the environment"
                )
        base = get_public_base_url() or (self.base_url or "")
        if base and not str(base).strip().startswith(("http://", "https://")):
            issues.append("base_url must be a valid HTTP/HTTPS URL")
        api_b = (self.api_base or "").strip()
        if api_b and not api_b.startswith(("http://", "https://")):
            issues.append("api_base must be a valid HTTP/HTTPS URL")
        return issues

    def is_configured(self) -> bool:
        self._apply_env_defaults()
        return len(self._config_issues()) == 0

    async def api(self) -> EmailProvider:
        """Return provider client for outbound/inbound API calls."""
        if not self.is_configured():
            raise ValidationError(
                "Email action is not configured: "
                + "; ".join(self._config_issues())
            )
        prov = (self.provider or "gmail").strip().lower()
        timeout = float(self.request_timeout or 30.0)

        if prov == "gmail":
            ga = await self.get_linked_gmail_action()
            if not ga:
                raise ValidationError(
                    "No GoogleGmailAction on this agent; add action jvagent/google_gmail_action "
                    "or set gmail_action_label to an existing Gmail action label"
                )
            return GmailEmailProvider(gmail_action=ga)

        if prov == "sendgrid":
            key = self._effective_api_key(self)
            base = (self.api_base or "https://api.sendgrid.com/v3").strip()
            sender = self._effective_sender_email(self)
            sname = self._effective_sender_name(self)
            return SendGridEmailProvider(
                api_key=key,
                api_base=base,
                timeout=timeout,
                default_from_email=sender,
                default_from_name=sname,
            )

        raise ValidationError(f"Unsupported provider: {self.provider}")

    async def healthcheck(self) -> Union[bool, Dict[str, Any]]:
        """Health status (WhatsApp-style dict when configured)."""
        if not self.is_configured():
            return {
                "healthy": True,
                "configured": False,
                "status": "inactive",
                "message": "Email action is not configured",
                "issues": self._config_issues(),
            }

        errors: List[str] = []
        prov = (self.provider or "gmail").strip().lower()
        if prov not in ("gmail", "sendgrid"):
            errors.append(f"Invalid provider: {self.provider}")
        if self.request_timeout <= 0:
            errors.append("request_timeout must be positive")
        if self.utterance_max_length <= 0:
            errors.append("utterance_max_length must be positive")

        adapter_initialized = False
        try:
            agent = await self.get_agent()
            if agent:
                response_bus = await agent.get_response_bus()
                if response_bus:
                    adapter = response_bus._channel_adapters.get("email")
                    if adapter:
                        adapter_initialized = bool(
                            getattr(adapter, "_initialized", False)
                        )
                        if not adapter_initialized:
                            errors.append("EmailAdapter not initialized")
        except Exception:
            pass

        result: Dict[str, Any] = {
            "healthy": len(errors) == 0,
            "configured": True,
            "adapter_initialized": adapter_initialized,
        }
        if errors:
            result["errors"] = errors

        if result["healthy"] and prov == "sendgrid":
            try:
                client_any = await self.api()
                if hasattr(client_any, "fetch_user_profile"):
                    await client_any.fetch_user_profile()
            except Exception as e:
                result["healthy"] = False
                result["errors"] = result.get("errors", []) + [str(e)]

        if result["healthy"] and prov == "gmail":
            try:
                ga = await self.get_linked_gmail_action()
                if not ga:
                    result["healthy"] = False
                    result["errors"] = result.get("errors", []) + [
                        "No GoogleGmailAction on this agent"
                    ]
                else:
                    await ga.get_profile()
            except Exception as e:
                result["healthy"] = False
                result["errors"] = result.get("errors", []) + [str(e)]

        if result["healthy"]:
            result["status"] = "active"
            result["provider"] = prov
            base = (self.api_base or "").strip()
            if base:
                result["api_base"] = base
            if prov == "sendgrid":
                result["api_key_configured"] = bool(self._effective_api_key(self))

        return result

    async def _ensure_email_webhook_url(self) -> None:
        """Persist webhook_url with API key when base_url and agent exist (SendGrid)."""
        if not self.enabled:
            return
        if (self.provider or "").strip().lower() != "sendgrid":
            return
        action_id = getattr(self, "id", None)
        if not action_id:
            return
        self._apply_env_defaults()
        base = get_public_base_url() or (self.base_url or "")
        if not base or not str(base).strip():
            logger.debug(
                "EmailAction id=%s: skip ensure webhook_url (no JVAGENT_PUBLIC_BASE_URL)",
                action_id,
            )
            return
        try:
            await self.get_webhook_url(regenerate=False)
        except ValidationError as e:
            logger.warning(
                "EmailAction id=%s: ensure webhook_url failed: %s",
                action_id,
                e,
                exc_info=True,
            )
        except Exception as e:
            logger.warning(
                "EmailAction id=%s: ensure webhook_url unexpected error: %s",
                action_id,
                e,
                exc_info=True,
            )

    async def get_webhook_url(
        self, allowed_ip: Optional[str] = None, regenerate: bool = False
    ) -> str:
        """Build or return inbound webhook URL with api_key query (for provider config)."""
        self._apply_env_defaults()
        base_in = get_public_base_url() or (self.base_url or "")
        base = str(base_in).strip() if base_in else ""
        if not base:
            raise ValidationError(
                "base_url (JVAGENT_PUBLIC_BASE_URL) is required for webhook URL generation"
            )
        if not base.startswith(("http://", "https://")):
            raise ValidationError(
                f"base_url must be a valid HTTP/HTTPS URL, got: {base_in}"
            )
        try:
            agent = await self.get_agent()
            if not agent:
                raise ValidationError("Agent not found for EmailAction")
            agent_id = str(agent.id)
            expected_url_base = f"{base.rstrip('/')}/api/email/interact/webhook/{agent_id}"

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

            from .webhook_auth import get_or_create_system_user

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
                name=f"Email Webhook - {agent.name}",
                permissions=["webhook:email"],
                expires_in_days=None,
                allowed_ips=[allowed_ip] if allowed_ip else [],
                allowed_endpoints=["/api/email/interact/webhook/*"],
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

    def _start_gmail_poll_loop_if_needed(self) -> None:
        if (self.provider or "").strip().lower() != "gmail":
            return
        interval = float(self.gmail_poll_interval_seconds or 0.0)
        if interval <= 0:
            return
        action_id = str(getattr(self, "id", "") or "")
        if not action_id:
            return

        prev = _gmail_poll_tasks.get(action_id)
        if prev and not prev.done():
            prev.cancel()

        async def _loop() -> None:
            from .gmail_poll import poll_gmail_inbox_once

            while True:
                try:
                    fresh = await EmailAction.get(action_id)
                    if (
                        not fresh
                        or not fresh.enabled
                        or (fresh.provider or "").strip().lower() != "gmail"
                    ):
                        break
                    if not fresh.is_configured():
                        await asyncio.sleep(interval)
                        continue
                    await poll_gmail_inbox_once(fresh)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(
                        "EmailAction Gmail poll error (id=%s): %s",
                        action_id,
                        e,
                        exc_info=True,
                    )
                await asyncio.sleep(interval)

        _gmail_poll_tasks[action_id] = asyncio.create_task(_loop())

    async def on_register(self) -> None:
        self._apply_env_defaults()
        if not self.is_configured():
            logger.debug("Email action not configured")
            return
        await self._ensure_email_webhook_url()
        logger.debug("Email action registered")

    async def on_reload(self) -> None:
        self._apply_env_defaults()
        if not self.is_configured() or not self.enabled:
            return
        await self._ensure_email_webhook_url()

    async def on_startup(self) -> None:
        if not self.is_configured() or not self.enabled:
            return
        agent = await self.get_agent()
        if not agent:
            logger.warning(
                "EmailAction: agent not found; skipping email channel registration"
            )
            return

        if (self.base_url or "").strip() or get_public_base_url():
            await self._ensure_email_webhook_url()

        from .email_adapter import EmailAdapter
        from .email_filter import EmailFilter

        if not await EmailFilter(channels=["email"], priority=100).initialize(
            agent=agent
        ):
            logger.warning("EmailFilter initialization failed")

        if not await EmailAdapter(action=self).initialize(agent=agent):
            logger.error("EmailAdapter initialization failed")

        self._start_gmail_poll_loop_if_needed()

    async def ensure_adapter_registered(self) -> bool:
        if not self.is_configured():
            return False
        try:
            agent = await self.get_agent()
            if not agent:
                return False
            response_bus = await agent.get_response_bus()
            if not response_bus:
                return False
            existing = response_bus._channel_adapters.get("email")
            if existing and getattr(existing, "_initialized", False):
                return True
            from .email_adapter import EmailAdapter

            return await EmailAdapter(action=self).initialize(agent=agent)
        except Exception as e:
            logger.error(
                "EmailAction: ensure_adapter_registered failed: %s", e, exc_info=True
            )
            return False
