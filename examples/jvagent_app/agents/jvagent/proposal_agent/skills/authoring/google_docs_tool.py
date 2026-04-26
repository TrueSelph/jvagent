"""Write proposal content to a Google Doc via GoogleDocsAction."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "authoring__google_docs_write",
        "description": (
            "Create or update a Google Doc with proposal content. "
            "Insert revision markers as comments for items needing review. "
            "Returns the document URL and ID."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Document title (e.g., 'Proposal: Acme Corp Platform Modernization')",
                },
                "content": {
                    "type": "string",
                    "description": "Full proposal content in Markdown format (will be formatted as rich text)",
                },
                "revision_markers": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "description": "The comment or suggestion text"},
                            "location": {"type": "string", "description": "Section or paragraph identifier"},
                        },
                    },
                    "description": "List of revision markers to insert as comments",
                },
                "doc_id": {
                    "type": "string",
                    "description": "If updating an existing doc, provide its document ID. Omit to create new.",
                },
            },
            "required": ["title", "content"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    """Create or update a Google Doc with proposal content and revision markers."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("GoogleDocsAction")
    if action is None:
        return {"error": "GoogleDocsAction not found on this agent"}

    title = arguments.get("title", "Untitled Proposal")
    content = arguments.get("content", "")
    revision_markers = arguments.get("revision_markers", [])
    doc_id = arguments.get("doc_id")

    if doc_id:
        # Update existing document
        await action.append_text(document_id=doc_id, text=f"\n\n{content}")
        doc_info = {"document_id": doc_id, "title": title}
    else:
        # Create new document
        doc_info = await action.create_document(title=title)
        doc_id = doc_info.get("document_id")

        # Write content in batches (Docs API limitations)
        # For simplicity, append the full content
        if content:
            await action.append_text(document_id=doc_id, text=content)

    # Insert revision markers as comments
    comments = []
    for marker in revision_markers:
        try:
            comment = await action.insert_comment(
                document_id=doc_id,
                text=marker.get("text", "Review needed"),
                content=marker.get("location", ""),
            )
            comments.append(comment)
        except Exception as e:
            comments.append({"error": str(e), "marker": marker})

    return {
        "document_id": doc_id,
        "title": title,
        "url": f"https://docs.google.com/document/d/{doc_id}/edit",
        "comments_inserted": len(comments),
        "revision_markers": revision_markers,
        "note": "Review the document and resolve comments. Call authoring__handle_feedback when ready.",
    }
