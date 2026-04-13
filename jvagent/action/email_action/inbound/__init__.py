"""Provider-specific inbound webhook parsers → canonical jvagent tuples."""

from .sendgrid import parse_sendgrid_inbound

__all__ = [
    "parse_sendgrid_inbound",
]
