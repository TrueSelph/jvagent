"""jvconnect Flow data-exchange helpers (X-Jvconnect-Flow-Exchange)."""

from __future__ import annotations

from typing import Any, Dict, Mapping


def is_flow_data_exchange_payload(body: Any) -> bool:
    """True when the JSON body is a jvconnect Flow data-exchange forward."""
    return isinstance(body, dict) and body.get("type") == "whatsapp_flow_data_exchange"


def is_flow_data_exchange_request(
    headers: Mapping[str, str], body: Any
) -> bool:
    """Detect Flow data-exchange via header or body type."""
    header_val = ""
    for key, value in headers.items():
        if key.lower() == "x-jvconnect-flow-exchange":
            header_val = str(value or "").strip()
            break
    if header_val == "1":
        return True
    return is_flow_data_exchange_payload(body)


def build_flow_data_exchange_response(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Build ``{screen, data}`` (or error ``data``) for jvconnect to re-encrypt.

    Does not run InteractWalker. INIT / Request-data without a screen map
    returns ``endpoint_not_configured`` so jvconnect/Meta get a clear signal.
    Subsequent ``data_exchange`` / ``complete`` paths return a SUCCESS screen
    that completes the Flow with the submitted data.
    """
    action = str(payload.get("action") or "").strip()
    screen = payload.get("screen")
    flow_token = str(payload.get("flow_token") or "unused")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}

    if action == "ping":
        return {"data": {"status": "active"}}

    if data.get("error") or data.get("error_message"):
        return {"data": {"acknowledged": True}}

    # INIT / first Request-data turn must return a real first screen — we do
    # not invent Flow JSON screens here (thin harness).
    if action == "INIT" or (action == "data_exchange" and not screen):
        return {
            "data": {
                "error": "endpoint_not_configured",
                "error_message": (
                    "Agent received Flow data exchange but has no INIT screen "
                    "handler. Use a navigate Flow (\"No data\") or provide screens "
                    "via a custom Flow endpoint handler."
                ),
            }
        }

    merged: Dict[str, Any] = dict(data)
    return {
        "screen": "SUCCESS",
        "data": {
            "extension_message_response": {
                "params": {
                    "flow_token": flow_token,
                    **merged,
                }
            }
        },
    }
