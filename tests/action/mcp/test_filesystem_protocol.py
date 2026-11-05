"""The bundled filesystem service starts and reads over the pinned MCP SDK."""

import sys
from pathlib import Path

import pytest

from jvagent.action.mcp.client import MCPClientWrapper
from jvagent.action.mcp.mcp_action import normalize_call_result


@pytest.mark.asyncio
async def test_bundled_filesystem_reads_its_configured_prefix(tmp_path: Path) -> None:
    (tmp_path / "alice").mkdir()
    (tmp_path / "alice" / "note.txt").write_text("Alice-only protocol fixture")
    client = MCPClientWrapper(
        "stdio",
        command=sys.executable,
        args=["-m", "jvagent.action.mcp.jvspatial_fs_server", "--root-prefix", "alice"],
        env={
            "JVSPATIAL_FILES_ROOT_PATH": str(tmp_path),
            "JVSPATIAL_FILE_STORAGE_PROVIDER": "local",
        },
    )
    try:
        tools = await client.list_tools()
        assert "read_text_file" in {t.name for t in tools}
        result = normalize_call_result(
            await client.call_tool("read_text_file", {"path": "note.txt"}),
            "read_text_file",
        )
        assert not result.is_error
        assert result.text == "Alice-only protocol fixture"
    finally:
        await client.disconnect()
