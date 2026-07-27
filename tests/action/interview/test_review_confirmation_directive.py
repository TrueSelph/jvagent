"""review_confirmation_directive keeps compose/literal meta off the user half."""

from __future__ import annotations

from jvagent.action.interview.hooks import review_confirmation_directive
from jvagent.action.reply.reply_action import (
    DIRECTIVE_GUIDANCE_MARKER,
    user_facing_directive,
)


def test_confirmation_meta_is_model_only():
    summary = (
        "User Name: Eldon Marks\n"
        "Available Times: Monday 9:00 AM - 11:00 AM\n"
        "User Email: eldon@mail.com\n"
        "Employer Name: V75 Inc."
    )
    directive = review_confirmation_directive(summary)
    facing = user_facing_directive(directive)
    guidance = directive.split(DIRECTIVE_GUIDANCE_MARKER, 1)[1]

    assert "Please review the details." in facing
    assert "Eldon Marks" in facing
    assert "reply 'Confirm' or 'Yes' to continue" in facing
    assert "make changes" in facing.lower()

    assert "Close with exactly" not in facing
    assert "Do NOT call interview__complete" not in facing
    assert "Close with exactly" in guidance
    assert "Do NOT call interview__complete" in guidance
