"""Provider-specific inbound webhook parsers → canonical jvagent tuples."""

from .auth import assert_inbound_auth, dkim_passes, spf_passes
from .sendgrid import parse_sendgrid_inbound

__all__ = [
    "assert_inbound_auth",
    "dkim_passes",
    "parse_sendgrid_inbound",
    "spf_passes",
]
