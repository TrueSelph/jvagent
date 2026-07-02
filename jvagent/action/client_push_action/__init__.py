"""ClientPushAction package.

Publishes messages to a client session and provides a long-lived SSE
endpoint for the client to receive them in real time.
"""

from . import endpoints  # noqa: F401 — registers HTTP endpoints
from jvagent.action.client_push_action.client_push_action import ClientPushAction

__all__ = ["ClientPushAction"]
