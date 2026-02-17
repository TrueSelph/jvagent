"""Qr code interact action."""
from jvspatial.core import Node
from typing import List, Dict, Any
from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker
from jvspatial.core.annotations import attribute

import logging
logger = logging.getLogger(__name__)



# qrcode imports
import io
import qrcode


# media manager imports
from jvagent.action.whatsapp.utils.media_manager import MediaManager


class QrCodeInteractAction(InteractAction):
    """This action only allows verified ranks to interact with the agent."""
    
    successful_message: str = attribute(
        default="Here you go {user}. Your ident code is attached above.",
        description="Message to present the qr code to the user."
    )

    failure_directive: str = attribute(
        default="Tell the user: 'Sorry, I'm unable to provide you with your QR Code. Please try again later or contact support.'.",
        description="Directive to show when agent fail to get QR Code."
    )

    anchors: List[str] = attribute(
        default=[
            "The user requests for a qr code or indent code"
        ],
        description="Anchor statements for InteractRouter routing"
    )




    async def execute(self, visitor: InteractWalker) -> None:
        """Execute onboarding process for new users."""

        
        # # CHECK IF TO EXECUTE ACTION
        interaction = visitor.interaction
        user = await interaction.get_user()
        subscriber_display_name = user.get_display_name() if user else "user"
        subscriber_phone = user.user_id
        # rank_profile = getattr(user, 'rank_profile', {})
        # channel = getattr(interaction, 'channel', '')
        # is_new_user = visitor.new_user
        logger.warning("test1")
        arc_api_action = await self.get_action("ArcAPIAction")

        logger.warning("test2")
        

        if arc_api_action:
            logger.warning("test3")
            rank_info = await arc_api_action.retrieve_rank_info(subscriber_phone)
            logger.warning("test4")

            if rank_info.get("ident_code"):
                logger.warning("test5")
                successful_message_str = self.successful_message.format(user=subscriber_display_name)

                qr_code_data_bytes = await self.generate_qr_code_bytes(rank_info.get("ident_code"))
                logger.warning("test6")
                
                # Save the qr code and get the file url
                media_manager = MediaManager()
                save_path = await media_manager.save_media(
                    user_id=subscriber_phone,
                    media_bytes=qr_code_data_bytes,
                    mime_type="image/png",
                    filename="qrcode.png"
                )
                logger.warning("test7")

                if save_path:
                    logger.warning("test8")
                    whatsapp_action = await self.get_action("WhatsAppAction")
                    # Construct full URL using base_url from WhatsAppAction
                    file_url = whatsapp_action.base_url + save_path
                    logger.warning("test9")
                    
                    await whatsapp_action.api().send_image(
                        phone=subscriber_phone,
                        file_url=file_url,
                        caption=successful_message_str
                    )
                    logger.warning("test10")
                    
                    # Record the response in the interaction without re-sending it as text
                    interaction.response = successful_message_str
                    await interaction.save()
                    logger.warning("test11")
                    return 

        logger.warning("test12")
        await visitor.add_directive(self.failure_directive)




    async def generate_qr_code_bytes(self, data: str) -> bytes:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(data)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")

        buffered = io.BytesIO()
        img.save(buffered, format="PNG")
        
        return buffered.getvalue()


