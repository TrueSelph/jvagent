"""Parse Meta Graph webhook configuration and detect stale callback URLs."""

import re
from typing import Any, Dict, List

_AGENT_ID_IN_PATH = re.compile(r"(n\.Agent\.[A-Za-z0-9]+)")


def agent_id_from_callback_url(url: str) -> str:
    """Extract agent node id from jvagent webhook callback URL, if present."""
    match = _AGENT_ID_IN_PATH.search(url or "")
    return match.group(1) if match else ""


def normalize_callback_url(url: str) -> str:
    """Strip query string and trailing slash for comparison."""
    s = (url or "").strip()
    q = s.find("?")
    if q >= 0:
        s = s[:q]
    return s.rstrip("/")


def extract_callback_urls_from_graph(graph: Any) -> List[Dict[str, str]]:
    """Collect callback URLs from Meta Graph webhook status responses."""
    entries: List[Dict[str, str]] = []
    if not isinstance(graph, dict):
        return entries

    # Combined status from get_webhook_override_status: {waba: {...}, phone: {...}}
    if "waba" in graph or "phone" in graph:
        waba = graph.get("waba")
        if isinstance(waba, dict):
            entries.extend(_extract_from_waba_subscribed_apps(waba))
        phone = graph.get("phone")
        if isinstance(phone, dict):
            entries.extend(_extract_from_phone_webhook_configuration(phone))
        return entries

    entries.extend(_extract_from_waba_subscribed_apps(graph))
    entries.extend(_extract_from_phone_webhook_configuration(graph))
    return entries


def _extract_from_phone_webhook_configuration(payload: dict) -> List[Dict[str, str]]:
    entries: List[Dict[str, str]] = []
    wc = payload.get("webhook_configuration")
    if isinstance(wc, dict):
        for key, url in wc.items():
            if isinstance(url, str) and url.strip().startswith("http"):
                entries.append({"source": f"phone.webhook_configuration.{key}", "url": url.strip()})
    elif isinstance(payload.get("webhook_configuration"), str):
        pass
    # Phone GET wraps fields at top level
    if "id" in payload and isinstance(payload.get("webhook_configuration"), dict):
        pass  # already handled
    return entries


def _extract_from_waba_subscribed_apps(payload: dict) -> List[Dict[str, str]]:
    entries: List[Dict[str, str]] = []
    data = payload.get("data")
    if isinstance(data, list):
        for idx, app in enumerate(data):
            if not isinstance(app, dict):
                continue
            override = app.get("override_callback_uri") or app.get("callback_uri")
            if isinstance(override, str) and override.strip().startswith("http"):
                entries.append(
                    {
                        "source": f"waba.subscribed_apps[{idx}].override_callback_uri",
                        "url": override.strip(),
                    }
                )
            wc = app.get("webhook_configuration")
            if isinstance(wc, dict):
                for key, url in wc.items():
                    if isinstance(url, str) and url.strip().startswith("http"):
                        entries.append(
                            {
                                "source": f"waba.subscribed_apps[{idx}].webhook_configuration.{key}",
                                "url": url.strip(),
                            }
                        )
    override = payload.get("override_callback_uri")
    if isinstance(override, str) and override.strip().startswith("http"):
        entries.append({"source": "waba.override_callback_uri", "url": override.strip()})
    return entries


def find_stale_callbacks(
    graph: Any,
    expected_callback_url: str,
    expected_agent_id: str = "",
) -> List[Dict[str, str]]:
    """Return callbacks that do not match expected URL or agent id."""
    expected_norm = normalize_callback_url(expected_callback_url)
    expected_aid = (expected_agent_id or agent_id_from_callback_url(expected_callback_url)).strip()
    stale: List[Dict[str, str]] = []

    for entry in extract_callback_urls_from_graph(graph):
        url = entry.get("url") or ""
        source = entry.get("source") or "unknown"
        url_norm = normalize_callback_url(url)
        url_aid = agent_id_from_callback_url(url)

        mismatch = False
        reason = ""
        if expected_norm and url_norm != expected_norm:
            mismatch = True
            reason = "url_mismatch"
        if expected_aid and url_aid and url_aid != expected_aid:
            mismatch = True
            reason = "agent_id_mismatch" if not reason else f"{reason},agent_id_mismatch"

        if mismatch:
            stale.append(
                {
                    "source": source,
                    "url": url,
                    "agent_id": url_aid,
                    "reason": reason,
                }
            )

    return stale


def dashboard_action_for_stale(stale_callbacks: List[Dict[str, str]]) -> str:
    """Human-readable fix hint for operators."""
    if not stale_callbacks:
        return ""
    has_application = any(
        "application" in (s.get("source") or "") for s in stale_callbacks
    )
    if has_application:
        return (
            "Update Meta App Dashboard → WhatsApp → Configuration callback URL to match "
            "expected_callback_url (application layer is not updated by Graph override alone). "
            "Then call POST .../meta/webhook-register or restart the server."
        )
    return (
        "Call POST .../meta/webhook-register or restart without --purge so overrides "
        "re-register to the current agent id."
    )
