"""Subscription interact action for managing user channel subscriptions."""

from typing import List
from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker
from jvspatial.core.annotations import attribute
import logging

logger = logging.getLogger(__name__)


class SubscriptionInteractAction(InteractAction):
    """Provides users with subscription management capabilities for Resolv IMS channels.

    This action allows users to:
    1. View their current subscriptions
    2. Subscribe to new channels or groups
    3. Unsubscribe from existing channels or groups
    4. Access the subscription management page

    The action is triggered when users express intent to manage their subscriptions.
    """

    prompt: str = attribute(
        default="Inform the user they can subscribe and unsubscribe anytime using the link below:\n{subscription_page}",
        description="Message template with subscription page link placeholder"
    )

    anchors: List[str] = attribute(
        default=[
            "The user wants to subscribe or unsubscribe from a channel or group",
            "The user wants to change their subscription preferences",
            "The user wants to know more about the subscription options"
        ],
        description="Anchor statements for InteractRouter routing"
    )


    async def execute(self, visitor: InteractWalker) -> None:
        """Execute subscription management process.

        Retrieves the user's subscription page link and provides it to the user
        via a directive for PersonaAction to incorporate into the response.

        Args:
            visitor: InteractWalker instance containing interaction context
        """
        # Get user information
        interaction = visitor.interaction
        user = await interaction.get_user()
        subscriber_display_name = user.get_display_name() if user else "user"
        subscriber_phone = user.user_id

        # Get Resolv API action
        api = await self.get_action("ResolvAPIAction")

        # Get channels page and add directive
        subscription_page = await api.get_channels_page(subscriber_phone, subscriber_display_name)
        if subscription_page:
            await visitor.add_directives([
                self.prompt.format(subscription_page=subscription_page)
            ])
