"""Email provider modules (Gmail, SendGrid)."""

from .base import EmailProvider, default_inbound_webhook_unsupported
from .gmail import GmailEmailProvider
from .sendgrid import SendGridEmailProvider, merge_mail_overrides

__all__ = [
    "EmailProvider",
    "GmailEmailProvider",
    "SendGridEmailProvider",
    "default_inbound_webhook_unsupported",
    "merge_mail_overrides",
]
