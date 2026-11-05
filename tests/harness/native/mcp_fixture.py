"""Disposable stdio MCP service for native compatibility qualification only."""

import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

server = MCPServer("JV native compatibility fixture")


@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
def lookup_record(reference: str) -> dict[str, str]:
    """Read a synthetic rehearsal record by exact reference; no external I/O."""
    audit = os.environ.get("JV_MCP_FIXTURE_AUDIT")
    if audit:
        with Path(audit).open("a") as stream:
            stream.write(reference + "\n")
    if reference != "REHEARSAL-47":
        raise ValueError("Reference unavailable")
    return {"reference": reference, "room": "Cedar", "start": "14:30"}


@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
def misleading_write() -> str:
    """Unreviewed tool: its remote read hint must not authorize native execution."""
    raise AssertionError("Unreviewed MCP tool was executed")


if __name__ == "__main__":
    if pid_file := os.environ.get("JV_MCP_FIXTURE_PID"):
        Path(pid_file).write_text(str(os.getpid()))
    server.run(transport="stdio")
