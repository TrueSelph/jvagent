"""Avatar action for managing agent profile images.

Provides storage and retrieval of base64 encoded avatar images.
"""

import logging
import base64
import aiohttp
from typing import Dict, Optional, Union, Any

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action

logger = logging.getLogger(__name__)


class AvatarAction(Action):
    """Action for managing agent avatar images.

    Stores a base64 encoded image and its mimetype.
    """

    image_data: str = attribute(
        default="", description="Base64 encoded image data"
    )
    mimetype: str = attribute(
        default="", description="MIME type of the image (e.g. image/png, image/jpeg)"
    )

    async def pull_avatar_from_whatsapp(self, phone: Optional[str] = None) -> bool:
        """Pull avatar from whatsapp and save it locally."""
        whatsapp_action = await self.get_action("WhatsAppAction")
        if not whatsapp_action:
            raise Exception("WhatsApp action not found")
        
        if not phone:
            # Try to get own device number
            device_info = await whatsapp_action.api().get_host_device()
            if device_info.get("ok", True):
                # Try various common response formats for WPPConnect/WWebJS
                phone = (
                    device_info.get("sessionInfo", {}).get("me", {}).get("user")
                    or device_info.get("wid", {}).get("user")
                    or device_info.get("id", {}).get("user")
                )
            
            if not phone:
                raise Exception("Phone number not provided and couldn't be determined from session")

        result = await whatsapp_action.api().get_profile_picture(phone=phone)
        if isinstance(result, dict) and not result.get("ok", True):
            raise Exception(f"Failed to get profile picture: {result.get('error', 'Unknown error')}")
             
        # Extract URL from various possible response formats
        url = None
        if isinstance(result, str):
            url = result
        elif isinstance(result, dict):
            url = result.get("profile_picture") or result.get("url") or result.get("response")
            
        if not url or not isinstance(url, str) or not url.startswith("http"):
            raise Exception(f"Could not find a valid profile picture URL in response: {result}")

        # Download image
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    content = await response.read()
                    self.image_data = base64.b64encode(content).decode("utf-8")
                    self.mimetype = response.content_type or "image/jpeg"
                    await self.save()
                    logger.info(f"Successfully pulled avatar for {phone}")
                    return True
                else:
                    raise Exception(f"Failed to download profile picture: HTTP {response.status}")

    async def set_whatsapp_avatar(self) -> bool:
        """Set WhatsApp profile picture using the current local avatar."""
        whatsapp_action = await self.get_action("WhatsAppAction")
        if not whatsapp_action:
            raise Exception("WhatsApp action not found")
        
        if not self.image_data:
            raise Exception("No avatar image set in AvatarAction")

        # Convert base64 data back to bytes for the API
        file_data = base64.b64decode(self.image_data)
        
        result = await whatsapp_action.api().set_profile_pic(file_data=file_data)
        if not result.get("ok", True):
            raise Exception(f"Failed to set profile picture: {result.get('error', 'Unknown error')}")

        logger.info(f"Successfully set WhatsApp profile picture from current avatar")
        return True
        

    async def set_avatar(self, image_data: str, mimetype: str) -> bool:
        """Set the avatar image data and mimetype.

        Args:
            image_data: Base64 encoded image string (without data: prefix)
            mimetype: MIME type of the image

        Returns:
            True if successfully updated, False otherwise
        """
        self.image_data = image_data
        self.mimetype = mimetype
        
        try:
            await self.save()
            return True
        except Exception as e:
            logger.error(f"Failed to save avatar for agent {self.agent_id}: {e}")
            return False

    def get_avatar(self, with_prefix: bool = True) -> Optional[str]:
        """Get the base64 encoded avatar image.

        Args:
            with_prefix: If True, returns data URI (data:mimetype;base64,data)

        Returns:
            Avatar string or None if not set
        """
        if not self.image_data:
            return None
            
        if with_prefix:
            return f"data:{self.mimetype};base64,{self.image_data}"
        return self.image_data

    async def delete_avatar(self) -> bool:
        """Clear the avatar image data.

        Returns:
            True if successfully cleared, False otherwise
        """
        self.image_data = ""
        self.mimetype = ""
        
        try:
            await self.save()
            return True
        except Exception as e:
            logger.error(f"Failed to delete avatar for agent {self.agent_id}: {e}")
            return False

    async def healthcheck(self) -> Union[bool, Dict[str, Any]]:
        """Perform health check for the Avatar action.

        Returns:
            True if healthy (avatar set), False or warning dict otherwise
        """
        if not self.image_data:
            return {
                "status": "warning",
                "message": "Avatar image is not set.",
                "healthy": True
            }
        return True
