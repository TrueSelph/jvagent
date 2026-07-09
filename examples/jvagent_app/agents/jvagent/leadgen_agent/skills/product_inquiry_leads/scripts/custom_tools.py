"""Hooks for product_inquiry_leads skill."""


def enrich_from_channel(ctx):
    interaction = getattr(ctx.visitor, "interaction", None)
    if not interaction:
        return ctx
    if (getattr(interaction, "channel", "") or "").lower() != "whatsapp":
        return ctx
    user = ctx.user
    if user and not ctx.fields.get("phone") and getattr(user, "user_id", None):
        ctx.fields.setdefault("phone", user.user_id)
    if user and not ctx.fields.get("name") and getattr(user, "name", None):
        ctx.fields.setdefault("name", user.name)
    return ctx
