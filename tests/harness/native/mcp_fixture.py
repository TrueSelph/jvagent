"""Disposable stdio MCP service for native compatibility qualification only."""

import asyncio
import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

server = MCPServer("JV native compatibility fixture")


def _audit(value: str) -> None:
    if audit := os.environ.get("JV_MCP_FIXTURE_AUDIT"):
        with Path(audit).open("a") as stream:
            stream.write(value + "\n")


@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
def lookup_record(reference: str) -> dict[str, str]:
    """Read a synthetic rehearsal record by exact reference; no external I/O."""
    _audit(reference)
    if reference != "REHEARSAL-47":
        raise ValueError("Reference unavailable")
    return {"reference": reference, "room": "Cedar", "start": "14:30"}


@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def slow_lookup() -> str:
    """A deliberately slow read for public MCP client deadline qualification."""
    _audit("SLOW-start")
    try:
        await asyncio.sleep(10)
    finally:
        _audit("SLOW-stop")
    return "unreachable slow read"


@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
def misleading_write() -> str:
    """Unreviewed tool: its remote read hint must not authorize native execution."""
    _audit("UNREVIEWED-WRITE")
    raise AssertionError("Unreviewed MCP tool was executed")


if __name__ == "__main__":
    if pid_file := os.environ.get("JV_MCP_FIXTURE_PID"):
        Path(pid_file).write_text(str(os.getpid()))
    server.run(transport="stdio")
