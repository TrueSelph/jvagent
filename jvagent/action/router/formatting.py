"""Shared formatting utilities for InteractRouter.

Extracted to avoid circular imports when tests need to exercise the same logic.
"""

from typing import Any, Dict, List, Optional


def format_interaction_history(
    interaction_history: List[Dict[str, Any]],
    conversation: Optional[Any] = None,
) -> str:
    """Format interaction history for the routing prompt with context signals.

    Prepends a context line highlighting key signals from the conversation:
    - Whether the MOST RECENT assistant message was a question

    Appends a clear transition marker to indicate where the current user message follows.

    Handles both formats from conversation.get_interaction_history():
    - formatted=True: list of dicts with 'role' and 'content' (user/assistant/system)
    - formatted=False: list of dicts with 'utterance', 'response', 'events' per interaction

    Args:
        interaction_history: List of interaction history entries (chronological order: oldest → newest)
        conversation: Optional Conversation (unused; kept for API compatibility)

    Returns:
        Formatted history string with context line and transition marker
    """
    if not interaction_history:
        return "(No previous conversation)"

    first_entry = interaction_history[0] if interaction_history else {}
    is_role_content = (
        isinstance(first_entry, dict)
        and "role" in first_entry
        and "content" in first_entry
    )

    context_signals = []
    last_assistant_msg = None

    if is_role_content:
        for entry in reversed(interaction_history):
            if isinstance(entry, dict) and entry.get("role") == "assistant":
                last_assistant_msg = entry.get("content") or ""
                break

        if last_assistant_msg and last_assistant_msg.strip().endswith("?"):
            context_signals.append("Most recent assistant message is a question")

        for e in reversed(interaction_history):
            if isinstance(e, dict) and e.get("role") == "system":
                content = e.get("content") or ""
                if content.startswith("[SUPPRESSED]"):
                    context_signals.append(
                        "Agent did not respond to recent message (suppressed)"
                    )
                    break
                if content.startswith("[DEFERRED]"):
                    context_signals.append("Deferred fragment(s) pending from user")
                    break
    else:
        for entry in reversed(interaction_history):
            if isinstance(entry, dict) and "ai" in entry:
                ai_msg = entry["ai"]
                if ai_msg and ai_msg.strip().endswith("?"):
                    context_signals.append(
                        "Most recent assistant message is a question"
                    )
                    break

    lines = []
    if context_signals:
        context_line = "Context: " + ". ".join(context_signals) + "."
        lines.append(context_line)
        lines.append("")

    for entry in interaction_history:
        if isinstance(entry, dict):
            if is_role_content:
                role = entry.get("role", "")
                content = entry.get("content") or ""
                if role == "user":
                    lines.append(f"User: {content}")
                elif role == "assistant":
                    if content.strip().endswith("?"):
                        lines.append(f"Assistant (question): {content}")
                    else:
                        lines.append(f"Assistant: {content}")
                elif role == "system":
                    if (content or "").startswith("[EVENT]"):
                        lines.append(content)
                    elif (content or "").startswith("[SUPPRESSED]") or (
                        content or ""
                    ).startswith("[DEFERRED]"):
                        lines.append(content)
                    elif (content or "").startswith("[INTERPRETATION]"):
                        lines.append(content)
                    elif content:
                        lines.append(content)
            else:
                if "human" in entry:
                    lines.append(f"User: {entry['human']}")
                elif "utterance" in entry:
                    lines.append(f"User: {entry['utterance']}")
                if "ai" in entry:
                    ai_msg = entry["ai"]
                    if ai_msg and ai_msg.strip().endswith("?"):
                        lines.append(f"Assistant (question): {ai_msg}")
                    else:
                        lines.append(f"Assistant: {ai_msg}")
                elif "response" in entry and entry["response"]:
                    resp = entry["response"]
                    if resp.strip().endswith("?"):
                        lines.append(f"Assistant (question): {resp}")
                    else:
                        lines.append(f"Assistant: {resp}")
                if "events" in entry:
                    for event in entry["events"]:
                        ev_str = (
                            event.get("content", event)
                            if isinstance(event, dict)
                            else str(event)
                        )
                        lines.append(f"[EVENT] {ev_str}")
        elif isinstance(entry, str):
            lines.append(entry)

    if lines:
        lines.append("")
        lines.append("---")
        lines.append(">>> USER RESPONDS NOW <<<")
        lines.append("---")

    return "\n".join(lines) if lines else "(No previous conversation)"
