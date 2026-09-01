"""Custom hooks for example_leadgen skill."""

from __future__ import annotations

_SKILL_NAME = "example_leadgen"


def enrich_from_channel(ctx):
    """Post-capture: auto-fill phone/name from WhatsApp channel when missing."""
    interaction = getattr(ctx.visitor, "interaction", None)
    if not interaction:
        return ctx
    channel = getattr(interaction, "channel", "") or ""
    if channel.lower() != "whatsapp":
        return ctx
    user = ctx.user
    if user and not ctx.fields.get("phone") and getattr(user, "user_id", None):
        ctx.fields.setdefault("phone", user.user_id)
    if user and not ctx.fields.get("name") and getattr(user, "name", None):
        ctx.fields.setdefault("name", user.name)
    return ctx
