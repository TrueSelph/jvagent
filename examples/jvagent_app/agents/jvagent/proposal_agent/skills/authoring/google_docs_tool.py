"""Write proposal content to a Google Doc via GoogleDocsAction."""

from __future__ import annotations

from typing import Any, Dict


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "authoring__google_docs_write",
        "description": (
            "Create or update a Google Doc with proposal content using template-aware "
            "rendering, placeholder replacement, and revision comments."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "content": {
                    "type": "string",
                    "description": "Proposal markdown to render into Google Docs.",
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
                },
                "doc_id": {"type": "string"},
                "template_document_id": {
                    "type": "string",
                    "description": "Optional explicit Google Docs template ID.",
                },
                "folder_id": {
                    "type": "string",
                    "description": "Optional destination Drive folder ID.",
                },
                "placeholders": {
                    "type": "object",
                    "description": "Template placeholders to replace, e.g. {client_name: 'Acme'}.",
                },
                "replace_body": {
                    "type": "boolean",
                    "description": "Replace entire body before rendering (default true).",
                },
            },
            "required": ["title", "content"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("GoogleDocsAction")
    if action is None:
        return {"error": "GoogleDocsAction not found on this agent"}

    title = arguments.get("title", "Untitled Proposal")
    content = arguments.get("content", "")
    revision_markers = arguments.get("revision_markers", [])
    placeholders = arguments.get("placeholders", {}) or {}
    replace_body = arguments.get("replace_body", True)
    doc_id = arguments.get("doc_id")
    action_config = getattr(visitor, "_current_action", None)
    configured_template_id = getattr(action_config, "google_docs_template_id", None) if action_config else None
    configured_folder_id = getattr(action_config, "drive_output_folder_id", None) if action_config else None
    template_document_id = arguments.get("template_document_id") or configured_template_id
    folder_id = arguments.get("folder_id") or configured_folder_id

    created_from_template = False
    if doc_id:
        doc_info = {"document_id": doc_id, "title": title}
    elif template_document_id:
        doc_info = await action.copy_template_document(
            template_document_id=template_document_id,
            title=title,
            folder_id=folder_id,
        )
        doc_id = doc_info.get("document_id")
        created_from_template = True
    else:
        doc_info = await action.create_document(title=title)
        doc_id = doc_info.get("document_id")

    if placeholders:
        await action.replace_named_placeholders(document_id=doc_id, values=placeholders)

    if replace_body:
        await action.render_markdown_blocks(document_id=doc_id, markdown=content)
    else:
        await action.append_text(document_id=doc_id, text=f"\n\n{content}")

    comments = []
    for marker in revision_markers:
        try:
            comment = await action.insert_comment(
                document_id=doc_id,
                text=marker.get("text", "Review needed"),
                content=marker.get("location", "proposal"),
            )
            comments.append(comment)
        except Exception as e:
            comments.append({"error": str(e), "marker": marker})

    return {
        "document_id": doc_id,
        "title": title,
        "url": f"https://docs.google.com/document/d/{doc_id}/edit",
        "created_from_template": created_from_template,
        "template_document_id": template_document_id,
        "comments_inserted": len([c for c in comments if "error" not in c]),
        "comment_errors": [c for c in comments if "error" in c],
        "revision_markers": revision_markers,
        "note": "Document is ready for review. Use authoring feedback tools for revision tracking.",
    }
