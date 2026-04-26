"""Handle user feedback on the proposal document — detect changes, apply revisions."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "authoring__handle_feedback",
        "description": (
            "Check for user feedback on the proposal document, apply revisions, "
            "and report what changed. Call this during the revision loop to "
            "detect resolved comments, user edits, or explicit approval signals."
        ),
        "parameters": {
            "type": "object",
            "parameters": {
                "doc_type": {
                    "type": "string",
                    "enum": ["google_doc", "markdown"],
                    "description": "Type of document being reviewed",
                },
                "document_id": {
                    "type": "string",
                    "description": "Google Doc ID (for google_doc type)",
                },
                "file_path": {
                    "type": "string",
                    "description": (
                        "Sandbox-relative path to the Markdown file (for markdown type), "
                        "e.g. output/20260101_Proposal.md"
                    ),
                },
                "mode": {
                    "type": "string",
                    "enum": ["poll", "apply", "approve"],
                    "description": "'poll' to check status, 'apply' to apply changes, 'approve' to finalize",
                },
            },
            "required": ["mode"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    """Poll for feedback, apply revisions, or mark as approved."""
    mode = arguments.get("mode", "poll")
    doc_type = arguments.get("doc_type", "google_doc")
    document_id = arguments.get("document_id", "")
    file_path = arguments.get("file_path", "")

    if mode == "poll":
        # Check document status by reading it and comparing with tracked revisions
        if doc_type == "google_doc" and document_id:
            return await _poll_google_doc(document_id, visitor)
        elif doc_type == "markdown" and file_path:
            return await _poll_markdown(file_path, visitor)
        else:
            return {
                "mode": "poll",
                "status": "unknown",
                "message": "Provide document_id (google_doc) or file_path (markdown) to poll.",
            }

    elif mode == "apply":
        # Apply user changes back to the DraftProposal
        # (The LLM reads the current doc content and updates the draft)
        return {
            "mode": "apply",
            "status": "ready",
            "message": (
                "Read the current document content and compare with the draft. "
                "Apply any user edits back to the proposal data."
            ),
        }

    elif mode == "approve":
        # Mark the document as approved for PDF generation
        return {
            "mode": "approve",
            "status": "approved",
            "message": "Document approved. Pass to pdf_generation (content + title + subtitle) for final PDF.",
        }

    return {"mode": mode, "status": "unknown"}


async def _poll_google_doc(document_id: str, visitor: Any) -> Dict[str, Any]:
    """Poll a Google Doc for resolved comments and content changes."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {
            "mode": "poll",
            "status": "unavailable",
            "message": "ActionResolver not available",
        }

    action = await resolver.resolve("GoogleDocsAction")
    if action is None:
        return {
            "mode": "poll",
            "status": "unavailable",
            "message": "GoogleDocsAction not available",
        }

    try:
        doc = await action.read_document(document_id=document_id)
        return {
            "mode": "poll",
            "status": "reviewing",
            "document_title": doc.get("title"),
            "paragraph_count": len(doc.get("paragraphs", [])),
            "message": (
                "Document reviewed. Check if revision markers in comments "
                "have been resolved. If the user has edited the doc, note the changes "
                "and apply them to the draft."
            ),
        }
    except Exception as e:
        return {
            "mode": "poll",
            "status": "error",
            "message": f"Could not read document: {e}",
        }


async def _poll_markdown(file_path: str, visitor: Any) -> Dict[str, Any]:
    """Poll a Markdown file in the user sandbox; sidecar is ``<path>.review``."""
    from jvagent.skills.fileinterface import _core

    try:
        path = _core.validate_relative_write_path(file_path.strip())
    except ValueError as e:
        return {
            "mode": "poll",
            "status": "invalid_path",
            "message": f"{e}",
        }

    review_path = f"{path}.review"

    try:
        await _core.read_text_file(visitor, path)
    except FileNotFoundError:
        return {
            "mode": "poll",
            "status": "not_found",
            "message": f"File not found in sandbox: {path}",
        }

    review_content = ""
    try:
        review_content = await _core.read_text_file(visitor, review_path)
    except FileNotFoundError:
        pass

    review_modified = bool(review_content.strip())

    return {
        "mode": "poll",
        "status": "reviewing",
        "file_path": path,
        "review_sidecar_present": review_modified or bool(review_content),
        "review_sidecar_content": review_content,
        "message": (
            "Content is in the user sandbox. Read via fileinterface if needed and compare "
            "with the draft. If the user has made edits, update the proposal accordingly."
        ),
    }
