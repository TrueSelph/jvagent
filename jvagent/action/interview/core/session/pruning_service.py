"""Pruning utilities for composing structured branch-change payloads.

Provides a utility function that takes raw branch-change details (produced by
PostUpdateWalker or the legacy detect_and_prune flow) and structures them
into a standardised payload dict suitable for state handlers, directives,
and audit logging.
"""

from typing import Any, Dict, List


def compose_branch_change_payload(
    change_details: Dict[str, Any],
    session: Any,
    interview_walker: Any,
) -> Dict[str, Any]:
    """Compose a structured branch-change payload from raw change details.

    Args:
        change_details: Dict with keys ``branching_question``,
            ``old_target``, ``new_target``, ``pruned_questions``,
            and optionally ``is_default`` / ``condition_index``.
        session: InterviewSession (used for question_graph lookup).
        interview_walker: Walker instance with ``_is_state_target()``
            method for determining if the new target is a state node.

    Returns:
        Structured payload dict with ``type``, ``branching_question``,
        ``old_target``, ``new_target``, ``is_state_target``,
        ``pruned_questions``, and ``metadata``.
    """
    new_target = change_details.get("new_target", "")
    pruned_questions: List[str] = change_details.get("pruned_questions", [])

    is_state = bool(
        interview_walker._is_state_target(new_target)
        if hasattr(interview_walker, "_is_state_target")
        else False
    )

    return {
        "type": "branch_change",
        "branching_question": change_details.get("branching_question", ""),
        "old_target": change_details.get("old_target", ""),
        "new_target": new_target,
        "is_state_target": is_state,
        "pruned_questions": pruned_questions,
        "metadata": {
            "pruned_count": len(pruned_questions),
            "is_default": change_details.get("is_default", False),
            "condition_index": change_details.get("condition_index"),
        },
    }
