"""Handle user feedback on proposal documents."""

from __future__ import annotations

import hashlib
from typing import Any, Dict


def _state(visitor: Any) -> Dict[str, Any]:
    state = getattr(visitor, "_skill_state", None)
    if state is None:
        state = {}
        setattr(visitor, "_skill_state", state)
    return state


def _hash_text(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "authoring__handle_feedback",
        "description": (
            "Poll review artifacts, detect baseline deltas, and mark approval for the "
            "proposal revision loop."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "doc_type": {
                    "type": "string",
                    "enum": ["google_doc", "markdown"],
                    "description": "Review artifact type.",
                },
                "document_id": {
                    "type": "string",
                    "description": "Google Docs document ID.",
                },
                "file_path": {
                    "type": "string",
                    "description": "Sandbox-relative Markdown path (e.g. output/proposal.md).",
                },
                "mode": {
                    "type": "string",
                    "enum": ["poll", "apply", "approve"],
                    "description": "poll status, apply revision request, or approve.",
                },
                "revision_request": {
                    "type": "string",
                    "description": "Plain-language request to apply in mode=apply.",
                },
                "current_content": {
                    "type": "string",
                    "description": "Current markdown content for apply mode.",
                },
            },
            "required": ["mode"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    mode = arguments.get("mode", "poll")
    doc_type = arguments.get("doc_type", "google_doc")
    document_id = arguments.get("document_id", "")
    file_path = arguments.get("file_path", "")
    if mode == "poll":
        if doc_type == "google_doc" and document_id:
            return await _poll_google_doc(document_id, visitor)
        if doc_type == "markdown" and file_path:
            return await _poll_markdown(file_path, visitor)
        return {
            "mode": "poll",
            "status": "unknown",
            "message": "Provide document_id (google_doc) or file_path (markdown) to poll.",
        }

    if mode == "apply":
        revision_request = arguments.get("revision_request", "").strip()
        current_content = arguments.get("current_content", "")
        if not revision_request:
            return {
                "mode": "apply",
                "status": "needs_input",
                "message": "revision_request is required for apply mode.",
            }
        return {
            "mode": "apply",
            "status": "ready",
            "revision_request": revision_request,
            "current_content_hash": _hash_text(current_content),
            "message": (
                "Apply revision_request to current_content, update proposal_state, "
                "then rewrite the current review artifact."
            ),
        }

    if mode == "approve":
        st = _state(visitor)
        st["proposal_approved"] = True
        return {
            "mode": "approve",
            "status": "approved",
            "message": "Document approved. Continue to source-aware PDF generation.",
        }

    return {"mode": mode, "status": "unknown"}


async def _poll_google_doc(document_id: str, visitor: Any) -> Dict[str, Any]:
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"mode": "poll", "status": "unavailable", "message": "ActionResolver not available"}

    action = await resolver.resolve("GoogleDocsAction")
    if action is None:
        return {"mode": "poll", "status": "unavailable", "message": "GoogleDocsAction not available"}

    try:
        doc = await action.read_document(document_id=document_id)
        comments = await action.list_comments(document_id=document_id)
        plain_text = doc.get("plain_text", "")
        doc_hash = _hash_text(plain_text)
        st = _state(visitor)
        key = f"doc_hash:{document_id}"
        baseline_hash = st.get(key)
        changed = bool(baseline_hash and baseline_hash != doc_hash)
        st[key] = doc_hash
        return {
            "mode": "poll",
            "status": "reviewing",
            "document_title": doc.get("title"),
            "paragraph_count": len(doc.get("paragraphs", [])),
            "plain_text": plain_text,
            "baseline_hash": baseline_hash,
            "current_hash": doc_hash,
            "changed_since_baseline": changed,
            "comments": comments,
            "resolved_comment_count": len([c for c in comments if c.get("resolved")]),
            "open_comment_count": len([c for c in comments if not c.get("resolved")]),
            "message": "Diff against baseline and apply requested changes to draft source.",
        }
    except Exception as e:
        return {"mode": "poll", "status": "error", "message": f"Could not read document: {e}"}


async def _poll_markdown(file_path: str, visitor: Any) -> Dict[str, Any]:
    from jvagent.skills.fileinterface.scripts import _core

    try:
        path = _core.validate_relative_write_path(file_path.strip())
    except ValueError as e:
        return {"mode": "poll", "status": "invalid_path", "message": f"{e}"}

    review_path = f"{path}.review"
    try:
        content = await _core.read_text_file(visitor, path)
    except FileNotFoundError:
        return {"mode": "poll", "status": "not_found", "message": f"File not found in sandbox: {path}"}

    review_content = ""
    try:
        review_content = await _core.read_text_file(visitor, review_path)
    except FileNotFoundError:
        pass

    doc_hash = _hash_text(content)
    st = _state(visitor)
    key = f"md_hash:{path}"
    baseline_hash = st.get(key)
    changed = bool(baseline_hash and baseline_hash != doc_hash)
    st[key] = doc_hash

    return {
        "mode": "poll",
        "status": "reviewing",
        "file_path": path,
        "plain_text": content,
        "review_sidecar_content": review_content,
        "baseline_hash": baseline_hash,
        "current_hash": doc_hash,
        "changed_since_baseline": changed,
        "message": "Diff against baseline and apply revisions to the draft source.",
    }
