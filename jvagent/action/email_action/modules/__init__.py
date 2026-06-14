"""Email provider modules (Gmail, Outlook, SendGrid)."""

from .base import EmailProvider, default_inbound_webhook_unsupported
from .gmail import GmailEmailProvider
from .outlook import OutlookEmailProvider
from .sendgrid import SendGridEmailProvider, merge_mail_overrides

__all__ = [
    "EmailProvider",
    "GmailEmailProvider",
    "OutlookEmailProvider",
    "SendGridEmailProvider",
    "default_inbound_webhook_unsupported",
    "merge_mail_overrides",
]
