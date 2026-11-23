"""Native Action declarations; the public SDK owns execution and effect storage."""

import hashlib
import json
from dataclasses import fields, replace
from typing import TYPE_CHECKING, Any

from jvagent.harness.contracts import IdempotencyClass
from jvagent.harness.mutation_tools import (
    MutationAccessDenied,
    MutationOutcomeUnknown,
    NativeMutationTool,
)
from jvagent.tooling.channel_identity import require_verified_channel_sender
from jvagent.tooling.context import get_dispatch_context, require_tool_visitor
from jvagent.tooling.tool import Tool
from jvagent.tooling.tool_result import ToolResult

from .utils.message_receipts import acknowledged_message_id

if TYPE_CHECKING:
    from .whatsapp_action import WhatsAppAction

CHANNELS = frozenset({"whatsapp", "whatsapp_call"})
READS = frozenset({"whatsapp__list_templates", "whatsapp__list_flows"})
SENDS = frozenset({"whatsapp__send_template", "whatsapp__send_flow"})


def verified_sender(visitor: Any) -> str:
    """Sender provenance and named host permissions are independent boundaries."""
    try:
        if visitor is not require_tool_visitor():
            raise ValueError("Visitor does not match")
        return require_verified_channel_sender(channels=CHANNELS)
    except ValueError:
        raise MutationAccessDenied("Verified WhatsApp sender is required") from None


class WhatsAppSendTool(NativeMutationTool):
    """One semantic send per Interaction; unknown outcomes require reconciliation."""

    effect_summary = "jv:mutation:whatsapp-send:v1"

    def __init__(self, original: Tool, action: "WhatsAppAction") -> None:
        self._original = original
        self._action = action
        declaration = {
            field.name: getattr(original, field.name) for field in fields(Tool)
        }
        declaration.update(
            execute=self._send,
            binds_visitor=True,
            effect_class="write",
            requires_tool_permission=True,
            idempotency_class=IdempotencyClass.NON_RETRYABLE,
        )
        super().__init__(**declaration)

    async def effect_identity(
        self, *, run_id: str, tool_call_id: str, arguments: dict[str, Any]
    ) -> str:
        sender = verified_sender(require_tool_visitor())
        action = self._action
        if not action.is_configured() or not action.is_meta_provider():
            raise MutationAccessDenied("WhatsApp provider is not configured")
        if self.name == "whatsapp__send_template":
            name = arguments["template_name"].strip()
            if not name or not action._is_template_allowed(name):
                raise MutationAccessDenied("WhatsApp template is not allowed")
            payload = {
                "template_name": name,
                "language": (arguments.get("language") or "").strip()
                or (action.default_template_language or "en_US").strip()
                or "en_US",
                "components": arguments.get("components") or [],
            }
        else:
            fid = (arguments.get("flow_id") or "").strip()
            name = (arguments.get("flow_name") or "").strip()
            if bool(fid) == bool(name) or not action._is_flow_allowed(fid, name):
                raise MutationAccessDenied("WhatsApp flow is not allowed")
            data = arguments.get("screen_data") or None
            screen = (arguments.get("screen") or "").strip()
            operation = (arguments.get("flow_action") or "").strip().lower()
            if data and (not screen or operation == "data_exchange"):
                raise MutationAccessDenied("WhatsApp flow prefill is invalid")
            # Match the provider encoder: it ignores action without a screen,
            # and data_exchange ignores screen. Ignored options cannot create
            # another identity for the same outbound request.
            effective_action = (
                "data_exchange"
                if operation == "data_exchange"
                else "navigate" if screen else ""
            )
            payload = {
                "flow_id": fid,
                "flow_name": name,
                "body": (arguments.get("body") or "").strip()
                or "Please complete this form.",
                "flow_cta": (arguments.get("flow_cta") or "Open").strip() or "Open",
                "flow_action": effective_action,
                "screen": "" if effective_action == "data_exchange" else screen,
                "screen_data": data,
                "mode": (arguments.get("mode") or "").strip(),
            }
        context = get_dispatch_context()
        assert context is not None
        material = json.dumps(
            {
                "tool": self.name,
                "action": str(action.id),
                "sender": sender,
                "payload": payload,
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        return (
            f"jv:whatsapp-send:{context.interaction_id}:"
            + hashlib.sha256(material).hexdigest()
        )

    async def _send(self, *, visitor: Any, **arguments: Any) -> str:
        sender = verified_sender(visitor)
        try:
            result = await self._original.call(**arguments)
        except Exception:
            raise MutationOutcomeUnknown(
                "WhatsApp send requires reconciliation"
            ) from None
        verified_sender(visitor)
        try:
            envelope = json.loads(result.content)
        except (ValueError, TypeError):
            envelope = None
        if (
            result.is_error
            or not isinstance(envelope, dict)
            or envelope.get("ok") is not True
            or envelope.get("to") != sender
        ):
            raise MutationOutcomeUnknown("WhatsApp send requires reconciliation")
        try:
            acknowledged_message_id({"messages": [{"id": envelope.get("message_id")}]})
        except ValueError:
            raise MutationOutcomeUnknown(
                "WhatsApp send requires reconciliation"
            ) from None
        return result.content


def read_tool(original: Tool) -> Tool:
    """Preserve the catalog schema while enforcing host-established sender scope."""

    async def read(*, visitor: Any, **arguments: Any) -> ToolResult:
        try:
            verified_sender(visitor)
        except MutationAccessDenied:
            return ToolResult.error("Authenticated inbound WhatsApp sender is required")
        result = await original.call(**arguments)
        verified_sender(visitor)
        try:
            envelope = json.loads(result.content)
        except (ValueError, TypeError):
            envelope = None
        if (
            result.is_error
            or not isinstance(envelope, dict)
            or envelope.get("ok") is not True
        ):
            return ToolResult.error("WhatsApp catalog is unavailable")
        return result

    return replace(
        original,
        execute=read,
        binds_visitor=True,
        effect_class="read",
        requires_tool_permission=True,
    )
