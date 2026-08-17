"""Map google-workspace-mcp tools to Google OAuth scopes.

MCP ``tools`` / ``denied_tools`` already decide which Workspace APIs the agent
can call. OAuth must request the same subset so a Sheets-only agent does not
ask Google for Gmail, Docs, Calendar, Slides, or Forms.
"""

from __future__ import annotations

import fnmatch
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

IDENTITY_SCOPES: List[str] = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]

# google-workspace-mcp Google-API tools only. manage_accounts / scratchpad /
# workspace / queue_operations do not need extra scopes.
MCP_TOOL_SCOPES: Dict[str, List[str]] = {
    "manage_sheets": [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
    ],
    "manage_docs": [
        "https://www.googleapis.com/auth/documents",
    ],
    "manage_drive": [
        "https://www.googleapis.com/auth/drive",
    ],
    "manage_email": [
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/gmail.settings.basic",
    ],
    "manage_calendar": [
        "https://www.googleapis.com/auth/calendar",
    ],
}

SERVICE_TOOLS: Dict[str, Tuple[str, ...]] = {
    "sheets": ("manage_sheets",),
    "gmail": ("manage_email",),
    "docs": ("manage_docs",),
    "drive": ("manage_drive",),
    "calendar": ("manage_calendar",),
}

# Union with GoogleAction.SCOPES so MCP tools and in-process actions both work.
SERVICE_ACTION_SCOPES: Dict[str, List[str]] = {
    "sheets": [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
    ],
    "gmail": [
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.modify",
    ],
    "docs": [
        "https://www.googleapis.com/auth/documents",
        "https://www.googleapis.com/auth/drive.file",
    ],
    "drive": [
        "https://www.googleapis.com/auth/drive",
    ],
    "calendar": [
        "https://www.googleapis.com/auth/calendar",
    ],
}

GOOGLE_ACTION_SERVICES: Tuple[Tuple[str, str], ...] = (
    ("GoogleSheetsAction", "sheets"),
    ("GoogleGmailAction", "gmail"),
    ("GoogleDocsAction", "docs"),
    ("GoogleDriveAction", "drive"),
    ("GoogleCalendarAction", "calendar"),
)

GOOGLE_WORKSPACE_SERVER = "google_workspace"

GOOGLE_SERVICE_LABELS: Dict[str, str] = {
    "sheets": "Google Sheets",
    "gmail": "Gmail",
    "docs": "Google Docs",
    "drive": "Google Drive",
    "calendar": "Google Calendar",
}


class GoogleOAuthServiceNotEnabled(ValueError):
    """``?service=`` asked for a Google API this agent does not enable."""


def google_workspace_tool_config(
    servers: Optional[Sequence[Any]] = None,
) -> Tuple[Any, List[str]]:
    """Return ``(tools_selector, denied_tools)`` for the google_workspace MCP server."""
    for raw in servers or []:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("name") or "") != GOOGLE_WORKSPACE_SERVER:
            continue
        tools = raw.get("tools", "-all")
        denied_raw = raw.get("denied_tools", [])
        denied = [str(p) for p in denied_raw] if isinstance(denied_raw, list) else []
        return tools, denied
    return "-all", []


def _select_tools(
    tools_selector: Any,
    denied_tools: Optional[Sequence[str]],
) -> Set[str]:
    available = set(MCP_TOOL_SCOPES)
    if isinstance(tools_selector, str) and tools_selector.strip() == "-all":
        allowed = set(available)
    elif tools_selector is None or tools_selector == "":
        allowed = set(available)
    elif isinstance(tools_selector, list):
        allowed = set()
        for pattern in tools_selector:
            allowed.update(fnmatch.filter(available, str(pattern)))
    else:
        allowed = set(available)

    denied: Set[str] = set()
    for pattern in denied_tools or []:
        denied.update(fnmatch.filter(allowed, str(pattern)))
    return allowed - denied


def google_oauth_services(
    tools_selector: Any = "-all",
    denied_tools: Optional[Sequence[str]] = None,
    enabled_services: Optional[Iterable[str]] = None,
) -> List[str]:
    """Ordered Google services enabled by MCP tools and/or sibling actions."""
    selected = _select_tools(tools_selector, denied_tools)
    enabled = {
        str(s).strip().lower() for s in (enabled_services or []) if str(s).strip()
    }
    for svc in enabled:
        if svc in SERVICE_TOOLS:
            selected.update(SERVICE_TOOLS[svc])
    return [
        svc
        for svc, tools in SERVICE_TOOLS.items()
        if any(t in selected for t in tools) or svc in enabled
    ]


def google_services_from_scopes(scopes: Sequence[str]) -> List[str]:
    """Services evidenced by granted Google OAuth scopes (not drive.file alone)."""
    scope_set = set(scopes)
    gmail_scopes = {
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/gmail.settings.basic",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.readonly",
    }
    checks = {
        "sheets": "https://www.googleapis.com/auth/spreadsheets" in scope_set,
        "gmail": bool(gmail_scopes & scope_set),
        "docs": "https://www.googleapis.com/auth/documents" in scope_set,
        "drive": "https://www.googleapis.com/auth/drive" in scope_set,
        "calendar": "https://www.googleapis.com/auth/calendar" in scope_set,
    }
    return [svc for svc in SERVICE_TOOLS if checks.get(svc)]


def resolve_google_oauth_scopes(
    tools_selector: Any = "-all",
    denied_tools: Optional[Sequence[str]] = None,
    service: Optional[str] = None,
    enabled_services: Optional[Iterable[str]] = None,
) -> List[str]:
    """Return identity scopes plus Google API scopes for enabled MCP tools/actions.

    ``service`` (sheets/gmail/docs/drive/calendar) further intersects the set.
    A service is allowed when MCP ``tools`` includes the matching ``manage_*``
    tool **or** ``enabled_services`` contains that service (a Google* action
    is registered on the agent). Raises :class:`GoogleOAuthServiceNotEnabled`
    when the service is unknown or not enabled either way.
    """
    selected = _select_tools(tools_selector, denied_tools)
    enabled = {
        str(s).strip().lower() for s in (enabled_services or []) if str(s).strip()
    }
    for svc in enabled:
        if svc in SERVICE_TOOLS:
            selected.update(SERVICE_TOOLS[svc])

    if service and str(service).strip():
        key = str(service).strip().lower()
        if key not in SERVICE_TOOLS:
            raise GoogleOAuthServiceNotEnabled(
                f"Unknown Google service '{service}'. "
                f"Expected one of: {', '.join(sorted(SERVICE_TOOLS))}."
            )
        if not (set(SERVICE_TOOLS[key]) & selected) and key not in enabled:
            raise GoogleOAuthServiceNotEnabled(
                f"Google service '{key}' is not enabled by this agent's "
                "MCP tools or Google actions."
            )
        selected = set(SERVICE_TOOLS[key])

    scopes: List[str] = list(IDENTITY_SCOPES)
    seen = set(scopes)
    for tool, tool_scopes in MCP_TOOL_SCOPES.items():
        if tool not in selected:
            continue
        for scope in tool_scopes:
            if scope not in seen:
                scopes.append(scope)
                seen.add(scope)

    selected_services = {
        svc for svc, tools in SERVICE_TOOLS.items() if any(t in selected for t in tools)
    }
    for svc in selected_services:
        for scope in SERVICE_ACTION_SCOPES.get(svc, []):
            if scope not in seen:
                scopes.append(scope)
                seen.add(scope)
    return scopes


# Unrestricted MCP tools ("-all"): today's Workspace set minus unused Slides/Forms.
GOOGLE_SCOPES: List[str] = resolve_google_oauth_scopes("-all")


def describe_google_oauth_access(scopes: Sequence[str]) -> str:
    """One-sentence consent-page copy for the resolved scopes."""
    scope_set = set(scopes)
    labels: List[str] = []
    if "https://www.googleapis.com/auth/spreadsheets" in scope_set:
        labels.append("Google Sheets")
    if "https://www.googleapis.com/auth/drive" in scope_set:
        labels.append("Google Drive")
    elif "https://www.googleapis.com/auth/drive.file" in scope_set:
        labels.append("Drive files created by this app")
    if "https://www.googleapis.com/auth/documents" in scope_set:
        labels.append("Google Docs")
    if "https://www.googleapis.com/auth/gmail.modify" in scope_set or (
        "https://www.googleapis.com/auth/gmail.settings.basic" in scope_set
        or "https://www.googleapis.com/auth/gmail.send" in scope_set
        or "https://www.googleapis.com/auth/gmail.readonly" in scope_set
    ):
        labels.append("Gmail")
    if "https://www.googleapis.com/auth/calendar" in scope_set:
        labels.append("Google Calendar")
    if not labels:
        return "Authorize this application to identify your Google account."
    if len(labels) == 1:
        return f"Authorize this application to access {labels[0]}."
    if len(labels) == 2:
        return f"Authorize this application to access {labels[0]} and {labels[1]}."
    return (
        "Authorize this application to access "
        + ", ".join(labels[:-1])
        + f", and {labels[-1]}."
    )


def granted_scopes_from_token_response(
    tokens: Dict[str, Any],
    fallback: Iterable[str],
) -> List[str]:
    """Prefer Google's ``scope`` field; otherwise keep the requested list."""
    raw = tokens.get("scope")
    if raw is None:
        raw = tokens.get("scopes")
    if isinstance(raw, str) and raw.strip():
        return raw.split()
    if isinstance(raw, list) and raw:
        return [str(s) for s in raw if str(s).strip()]
    return list(fallback)
