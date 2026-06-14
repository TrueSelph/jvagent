"""Structural typing for email providers (Protocol, not ABC)."""

from typing import Any, Dict, Protocol

from jvagent.action.email_action.email_payload import CanonicalSendMessage


class EmailProvider(Protocol):
    """Structural contract for transactional send and optional inbound webhook registration.

    Implementations are plain classes (e.g. ``GmailEmailProvider``); no inheritance required.
    """

    async def send_canonical(self, msg: CanonicalSendMessage) -> Dict[str, Any]:
        """Send one message from the canonical jvagent payload.

        Returns:
            Dict with at least ``ok`` (bool); on success may include ``messageId`` / ``message_id``.
        """
        ...

    async def create_inbound_webhook(
        self,
        *,
        url: str,
        domain: str,
        description: str = "",
    ) -> Dict[str, Any]:
        """Register an inbound-parse webhook with the provider.

        Providers that do not support this return
        ``{"ok": False, "error": "inbound_webhook_registration_not_supported"}``.
        """
        ...


def default_inbound_webhook_unsupported() -> Dict[str, Any]:
    """Return value for providers without inbound webhook API."""
    return {
        "ok": False,
        "error": "inbound_webhook_registration_not_supported",
    }
