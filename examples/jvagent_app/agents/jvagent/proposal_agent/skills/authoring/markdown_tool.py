"""Write proposal Markdown into the user sandbox (jvspatial file storage)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from jvagent.skills.fileinterface.scripts._core import (
    create_directory,
    normalize_sandbox_dir_prefix,
    write_text_file,
)


async def _resolve_pipeline_attr(visitor: Any, attr: str) -> Any:
    """Look up an attribute on _current_action or peer ProposalPipelineAction."""
    action = getattr(visitor, "_current_action", None)
    if action is not None and getattr(action, attr, None):
        return getattr(action, attr)
    if action is not None and hasattr(action, "get_action"):
        try:
            pipeline = await action.get_action("ProposalPipelineAction")
            if pipeline and getattr(pipeline, attr, None):
                return getattr(pipeline, attr)
        except Exception:
            return None
    return None


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "authoring__markdown_write",
        "description": (
            "Write proposal content to a Markdown file with YAML frontmatter in the "
            "user sandbox (jvspatial storage). Fallback when Google Docs is unavailable. "
            "Paths are sandbox-relative only (e.g. output/). Revision markers use [REVIEW: ...] blocks."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Document title",
                },
                "client_name": {
                    "type": "string",
                    "description": "Client name for frontmatter",
                },
                "content": {
                    "type": "string",
                    "description": "Full proposal content in Markdown",
                },
                "output_dir": {
                    "type": "string",
                    "description": (
                        "Sandbox-relative directory (e.g. output). No absolute paths or '..'. "
                        "Defaults to the proposal action output_dir or output."
                    ),
                },
                "revision_markers": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "location": {"type": "string"},
                        },
                    },
                    "description": "Revision markers to append as a review log section",
                },
            },
            "required": ["title", "content"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    """Write a Markdown file with proposal content and frontmatter."""
    title = arguments.get("title", "Untitled Proposal")
    client_name = arguments.get("client_name", "Client")
    content = arguments.get("content", "")
    configured = await _resolve_pipeline_attr(visitor, "output_dir")
    try:
        sandbox_dir = normalize_sandbox_dir_prefix(
            arguments.get("output_dir") or configured,
            default="output",
        )
    except ValueError as e:
        return {
            "error": str(e),
            "file_path": None,
            "title": title,
            "note": "Use a sandbox-relative output_dir (e.g. output), not a host absolute path.",
        }
    revision_markers = arguments.get("revision_markers", [])

    # Determine output path (sandbox-relative)
    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title)
    safe_title = safe_title.replace(" ", "_")[:80]
    date_str = datetime.now().strftime("%Y%m%d")
    filename = f"{date_str}_{safe_title}.md"

    await create_directory(visitor, sandbox_dir)

    # Build frontmatter + content
    frontmatter = (
        "---\n"
        f"title: \"{title}\"\n"
        f"client: \"{client_name}\"\n"
        f"date: \"{datetime.now().strftime('%Y-%m-%d')}\"\n"
        f"status: review\n"
        "---\n\n"
    )

    # Build review log section if there are markers
    review_section = ""
    if revision_markers:
        review_section = "\n---\n\n## Review Log\n\n"
        for marker in revision_markers:
            review_section += (
                f"- [ ] **{marker.get('location', 'General')}:** "
                f"{marker.get('text', 'Review needed')}\n"
            )

    full_content = frontmatter + content + review_section
    sandbox_path = f"{sandbox_dir}/{filename}"
    await write_text_file(visitor, sandbox_path, full_content)

    # Sidecar for feedback monitoring (see authoring__handle_feedback)
    await write_text_file(visitor, f"{sandbox_path}.review", "")

    return {
        "file_path": sandbox_path,
        "filename": filename,
        "title": title,
        "client_name": client_name,
        "revision_markers_count": len(revision_markers),
        "note": (
            f"Written to {sandbox_path}. Review the file, edit it, then "
            f"run authoring__track_revisions to check for changes."
        ),
    }
