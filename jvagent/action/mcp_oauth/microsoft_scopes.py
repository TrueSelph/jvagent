"""Map Microsoft 365 MCP / Graph actions to delegated Entra scopes.

Softeria ``@softeria/ms-365-mcp-server`` exposes hundreds of Graph endpoints,
so we do not map those tool names 1:1. Consent is the four personal services
plus any enabled Microsoft* actions. ``?service=`` further narrows the set.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

IDENTITY_SCOPES: List[str] = [
    "openid",
    "offline_access",
    "User.Read",
]

SERVICE_SCOPES: Dict[str, List[str]] = {
    "outlook": [
        "Mail.Read",
        "Mail.ReadWrite",
        "Mail.Send",
    ],
    "calendar": [
        "Calendars.ReadWrite",
    ],
    "onedrive": [
        "Files.ReadWrite.All",
    ],
    "excel": [
        "Files.ReadWrite.All",
    ],
}

PERSONAL_SERVICES: Tuple[str, ...] = ("outlook", "calendar", "onedrive", "excel")

MICROSOFT_ACTION_SERVICES: Tuple[Tuple[str, str], ...] = (
    ("MicrosoftOutlookMailAction", "outlook"),
    ("MicrosoftOutlookCalendarAction", "calendar"),
    ("MicrosoftOneDriveAction", "onedrive"),
    ("MicrosoftExcelAction", "excel"),
)

MICROSOFT_365_SERVER = "microsoft_365"

SERVICE_LABELS: Dict[str, str] = {
    "outlook": "Outlook mail",
    "calendar": "Outlook calendar",
    "onedrive": "OneDrive",
    "excel": "Excel",
}


class MicrosoftOAuthServiceNotEnabled(ValueError):
    """``?service=`` asked for a Graph API this agent does not enable."""


def microsoft_365_tool_config(
    servers: Optional[Sequence[Any]] = None,
) -> Tuple[Any, List[str]]:
    """Return ``(tools_selector, denied_tools)`` for the microsoft_365 MCP server."""
    for raw in servers or []:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("name") or "") != MICROSOFT_365_SERVER:
            continue
        tools = raw.get("tools", "-all")
        denied_raw = raw.get("denied_tools", [])
        denied = [str(p) for p in denied_raw] if isinstance(denied_raw, list) else []
        return tools, denied
    return None, []


def _mcp_enables_personal_services(tools_selector: Any) -> bool:
    """True when the microsoft_365 MCP server is configured with a tool surface."""
    if tools_selector is None:
        return False
    if isinstance(tools_selector, str) and tools_selector.strip() == "":
        return False
    if isinstance(tools_selector, list) and not tools_selector:
        return False
    return True


def microsoft_oauth_services(
    tools_selector: Any = "-all",
    denied_tools: Optional[Sequence[str]] = None,
    enabled_services: Optional[Iterable[str]] = None,
) -> List[str]:
    """Ordered Microsoft services enabled by MCP tools and/or sibling actions."""
    _ = denied_tools
    selected: Set[str] = set()
    if _mcp_enables_personal_services(tools_selector):
        selected.update(PERSONAL_SERVICES)
    enabled = {
        str(s).strip().lower() for s in (enabled_services or []) if str(s).strip()
    }
    for svc in enabled:
        if svc in SERVICE_SCOPES:
            selected.add(svc)
    return [svc for svc in PERSONAL_SERVICES if svc in selected]


def microsoft_services_from_scopes(scopes: Sequence[str]) -> List[str]:
    """Services evidenced by granted Microsoft Graph scopes."""
    scope_set = set(scopes)
    out: List[str] = []
    if (
        "Mail.Read" in scope_set
        or "Mail.Send" in scope_set
        or "Mail.ReadWrite" in scope_set
    ):
        out.append("outlook")
    if "Calendars.ReadWrite" in scope_set:
        out.append("calendar")
    if "Files.ReadWrite.All" in scope_set:
        out.extend(("onedrive", "excel"))
    return out


def resolve_microsoft_oauth_scopes(
    tools_selector: Any = "-all",
    denied_tools: Optional[Sequence[str]] = None,
    service: Optional[str] = None,
    enabled_services: Optional[Iterable[str]] = None,
) -> List[str]:
    """Return identity scopes plus Graph scopes for enabled MCP/actions.

    ``denied_tools`` is accepted for call-site parity with Google; Softeria
    tool names are not mapped to Graph services, so it does not filter.
    """
    _ = denied_tools
    selected: Set[str] = set()
    if _mcp_enables_personal_services(tools_selector):
        selected.update(PERSONAL_SERVICES)
    enabled = {
        str(s).strip().lower() for s in (enabled_services or []) if str(s).strip()
    }
    for svc in enabled:
        if svc in SERVICE_SCOPES:
            selected.add(svc)

    if service and str(service).strip():
        key = str(service).strip().lower()
        if key not in SERVICE_SCOPES:
            raise MicrosoftOAuthServiceNotEnabled(
                f"Unknown Microsoft service '{service}'. "
                f"Expected one of: {', '.join(PERSONAL_SERVICES)}."
            )
        if key not in selected:
            raise MicrosoftOAuthServiceNotEnabled(
                f"Microsoft service '{key}' is not enabled by this agent's "
                "MCP tools or Microsoft actions."
            )
        selected = {key}

    scopes: List[str] = list(IDENTITY_SCOPES)
    seen = set(scopes)
    for svc in PERSONAL_SERVICES:
        if svc not in selected:
            continue
        for scope in SERVICE_SCOPES[svc]:
            if scope not in seen:
                scopes.append(scope)
                seen.add(scope)
    return scopes


MICROSOFT_SCOPES: List[str] = resolve_microsoft_oauth_scopes("-all")


def describe_microsoft_oauth_access(scopes: Sequence[str]) -> str:
    """One-sentence consent-page copy for the resolved Graph scopes."""
    scope_set = set(scopes)
    labels: List[str] = []
    if (
        "Mail.Read" in scope_set
        or "Mail.Send" in scope_set
        or "Mail.ReadWrite" in scope_set
    ):
        labels.append("Outlook mail")
    if "Calendars.ReadWrite" in scope_set:
        labels.append("Outlook calendar")
    if "Files.ReadWrite.All" in scope_set:
        labels.append("OneDrive and Excel")
    if not labels:
        return "Authorize this application to identify your Microsoft account."
    if len(labels) == 1:
        return f"Authorize this application to access {labels[0]}."
    if len(labels) == 2:
        return f"Authorize this application to access {labels[0]} and {labels[1]}."
    return (
        "Authorize this application to access "
        + ", ".join(labels[:-1])
        + f", and {labels[-1]}."
    )
