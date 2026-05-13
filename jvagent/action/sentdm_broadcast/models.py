"""Graph nodes for the SentDM broadcast action.

``SentDMBroadcastRecord`` persists one row per ``(message_id, recipient,
channel)`` we send through SentDM. Webhook delivery events feed updates into
the matching record (looked up by ``sentdm_message_id``), giving us a local
audit trail without re-fetching SentDM every time the status changes.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from jvspatial.core import Node
from jvspatial.core.annotations import attribute
from pydantic import field_validator


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SentDMBroadcastRecord(Node):
    """One persisted record per broadcast message we send via SentDM.

    Looked up by ``sentdm_message_id`` from inbound webhook events so that
    delivery status changes can be folded into ``status``, ``events`` and
    ``last_event_payload`` without re-fetching SentDM.
    """

    action_id: str = attribute(
        indexed=True,
        default_factory=str,
        description="ID of the SentDMBroadcastAction that produced this record",
    )
    agent_id: str = attribute(
        indexed=True,
        default_factory=str,
        description="ID of the agent that owns the action (cross-action lookups)",
    )
    sentdm_message_id: str = attribute(
        indexed=True,
        default_factory=str,
        description=(
            "SentDM-issued message id (POST /v3/messages returns one per "
            "(recipient, channel) pair). Primary lookup key for webhooks."
        ),
    )
    to: str = attribute(
        default_factory=str,
        description="Recipient phone number in E.164 format",
    )
    channel: str = attribute(
        default_factory=str,
        description="Delivery channel: 'sms' or 'whatsapp'",
    )
    template_id: Optional[str] = attribute(
        default=None,
        description="Template UUID used for this broadcast",
    )
    template_name: Optional[str] = attribute(
        default=None,
        description="Template name used for this broadcast",
    )
    parameters: Dict[str, Any] = attribute(
        default_factory=dict,
        description="Template parameters substituted into the message",
    )
    idempotency_key: Optional[str] = attribute(
        default=None,
        description="The idempotency-key header value used on the send call",
    )
    profile_id: Optional[str] = attribute(
        default=None,
        description="x-profile-id header value used on the send call",
    )
    sandbox: bool = attribute(
        default=False,
        description="True when the send was made with SentDM sandbox mode",
    )

    status: str = attribute(
        default="accepted",
        description=(
            "Last-known status: accepted | processing | sent | delivered | "
            "read | failed | rejected | unknown"
        ),
    )
    last_event_field: Optional[str] = attribute(
        default=None,
        description="Last webhook 'field' value applied (e.g. 'messages')",
    )
    last_event_payload: Optional[Dict[str, Any]] = attribute(
        default=None,
        description="Last webhook event payload, kept verbatim for debugging",
    )
    last_status_at: Optional[datetime] = attribute(
        default=None,
        description="When status last changed (server-truth or webhook)",
    )
    events: List[Dict[str, Any]] = attribute(
        default_factory=list,
        description=(
            "Bounded append-only audit log of webhook / refresh events. "
            "Cap controlled by SentDMBroadcastAction.record_event_history_limit."
        ),
    )
    error: Optional[Dict[str, Any]] = attribute(
        default=None,
        description="Populated when status is failed/rejected",
    )

    created_at: datetime = attribute(
        default_factory=_utcnow,
        description="When this record was first persisted",
    )
    updated_at: datetime = attribute(
        default_factory=_utcnow,
        description="When this record was last updated",
    )

    @field_validator("last_status_at", "created_at", "updated_at", mode="before")
    @classmethod
    def _coerce_datetime(cls, v: Any) -> Any:
        if v is None or isinstance(v, datetime):
            return v
        if isinstance(v, str):
            try:
                return datetime.fromisoformat(v.replace("Z", "+00:00"))
            except ValueError:
                return None
        return v


__all__ = ["SentDMBroadcastRecord"]
