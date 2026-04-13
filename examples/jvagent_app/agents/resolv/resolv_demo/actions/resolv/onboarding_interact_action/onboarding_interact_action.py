"""Onboarding interact action."""

from typing import List
from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker
from jvspatial.core.annotations import attribute
import logging
logger = logging.getLogger(__name__)

class OnboardingInteractAction(InteractAction):
    """Detects new users and adds their details to the default subscriber list and/or specified groups by ID on Resolv IS.

    This action automatically onboards new users by:
    1. Detecting first-time users
    2. Subscribing them to default groups
    3. Presenting channel selection options
    """

    # Configuration attributes
    default_groups: List[str] = attribute(
        default_factory=list,
        description="Default group IDs for new subscribers"
    )

    prompt: str = attribute(
        default="Introduce yourself and present the link to the channels page for the user to select the channels they want to join: {channels_page}",
        description="Welcome message template with channels page link"
    )

    always_execute: bool = attribute(
        default=True,
        description="Always execute regardless of routing (first-time user intro handler).",
    )


    async def execute(self, visitor: InteractWalker) -> None:
        """Execute onboarding process for new users."""


        # CHECK IF TO EXECUTE ACTION
        interaction = visitor.interaction
        user = await interaction.get_user()
        subscriber_display_name = user.get_display_name() if user else "user"
        subscriber_phone = user.user_id
        channel = getattr(interaction, 'channel', '')
        is_new_user = visitor.new_user
        api = await self.get_action("ResolvAPIAction")

        if not subscriber_phone or not subscriber_display_name or not api or channel != 'whatsapp' or not is_new_user:
            return

        # EXECUTE ACTION

        # Subscribe user to default groups
        if self.default_groups:
            for group in self.default_groups:
                await api.subscribe_user(
                    phone=subscriber_phone,
                    name=subscriber_display_name,
                    group_id=group
                )


        # Get channels page and add directive
        channels_page = await api.get_channels_page(subscriber_phone, subscriber_display_name)
        if channels_page:
            await visitor.add_directives([
                self.prompt.format(channels_page=channels_page)
            ])


    async def get_contact_groups(self) -> list[dict]:
        """Get contact groups."""

        api = await self.get_action("ResolvAPIAction")
        return await api.get_contact_groups()

    async def update_default_contact_groups(self, group: list[str]) -> list[str]:
        """Update default contact groups."""

        self.default_groups = group
        await self.save()
        return self.default_groups