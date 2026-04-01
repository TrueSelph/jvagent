import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from jvspatial.core.annotations import attribute
from jvspatial.env import env

from jvagent.action.base import Action
from jvagent.core.app import App

logger = logging.getLogger(__name__)


class PostizAction(Action):
    """Core action for interacting with the Postiz social media management API.

    Configure ``POSTIZ_API_KEY`` in ``.env``. Non-secret settings use attributes below.

    Attributes:
        base_url: The base URL for the Postiz Public API (v1).
    """

    base_url: str = attribute(
        default="http://localhost:4007/api/public/v1",
        description="Base URL for Postiz Public API",
    )

    timeout: int = attribute(
        default=30,
        description="Timeout for API requests in seconds",
    )

    @staticmethod
    def _env_api_key() -> str:
        return env("POSTIZ_API_KEY")

    async def _get_headers(self) -> Dict[str, str]:
        """Prepare headers for Postiz API requests."""
        return {
            "Authorization": f"{(self._env_api_key() or '').strip()}",
            "Content-Type": "application/json",
        }

    async def list_integrations(self) -> List[Dict[str, Any]]:
        """List all connected social media integrations (channels).

        Returns:
            List of integration dictionaries containing id, type, name, etc.
        """
        url = f"{self.base_url.rstrip('/')}/integrations"
        headers = await self._get_headers()

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.error(
                    f"Postiz API error listing integrations: {e.response.text}"
                )
                raise
            except Exception as e:
                logger.error(f"Unexpected error listing Postiz integrations: {e}")
                raise

    async def list_integrations_summary(self) -> str:
        """Return a formatted summary of connected social media integrations."""
        try:
            integrations = await self.list_integrations()
            if not integrations:
                return "No social media channels are currently connected in Postiz."
            lines = ["Available Social Media Channels:"]
            for i in integrations:
                name = i.get("name", "Unknown")
                it_type = i.get("type", "unknown")
                it_id = i.get("id", "no-id")
                lines.append(f"- {name} ({it_type}) [ID: {it_id}]")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"Error listing Postiz integrations: {e}")
            return f"Error retrieving social media channels: {str(e)}"

    async def get_auth_url(self, provider: str) -> str:
        """Get the OAuth authorization URL for a specific provider.

        Args:
            provider: The provider name (e.g., 'facebook', 'linkedin', 'x', 'instagram').

        Returns:
            The authorization URL to be visited by the user.
        """
        url = f"{self.base_url.rstrip('/')}/social/{provider}"
        headers = await self._get_headers()

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                data = response.json()
                auth_url = data.get("url")
                if not auth_url:
                    raise ValueError(
                        f"Postiz API response missing 'url' for {provider}"
                    )
                return str(auth_url)
            except httpx.HTTPStatusError as e:
                logger.error(
                    f"Postiz API error getting auth URL for {provider}: {e.response.text}"
                )
                raise
            except Exception as e:
                logger.error(f"Unexpected error getting Postiz auth URL: {e}")
                raise

        return ""  # Fallback to satisfy linters

    async def list_available_providers(self) -> List[Dict[str, str]]:
        """List all social media providers supported by Postiz.

        This method attempts to fetch available providers from the Postiz backend.
        If the API call fails or is unavailable on the current version, it returns
        a comprehensive fallback list of known supported integrations.

        Returns:
            List of dictionaries containing provider 'id' and 'name'.
        """
        # Try to hit the internal integrations endpoint (known to exist in some versions)
        # We use the base_url without the /public/v1 suffix for internal routes if needed,
        # but let's try the public-facing path first or a generic one.
        base_api = self.base_url.split("/public/v1")[0]
        url = f"{base_api.rstrip('/')}/integrations"

        # Comprehensive fallback list based on Postiz source code
        fallback_providers = [
            {"id": "x", "name": "X (Twitter)"},
            {"id": "linkedin", "name": "LinkedIn"},
            {"id": "facebook", "name": "Facebook"},
            {"id": "instagram", "name": "Instagram"},
            {"id": "threads", "name": "Threads"},
            {"id": "youtube", "name": "YouTube"},
            {"id": "tiktok", "name": "TikTok"},
            {"id": "reddit", "name": "Reddit"},
            {"id": "pinterest", "name": "Pinterest"},
            {"id": "discord", "name": "Discord"},
            {"id": "slack", "name": "Slack"},
            {"id": "telegram", "name": "Telegram"},
            {"id": "mastodon", "name": "Mastodon"},
            {"id": "bluesky", "name": "Bluesky"},
            {"id": "gmb", "name": "Google My Business"},
            {"id": "twitch", "name": "Twitch"},
            {"id": "kick", "name": "Kick"},
            {"id": "devto", "name": "Dev.to"},
            {"id": "hashnode", "name": "Hashnode"},
            {"id": "medium", "name": "Medium"},
            {"id": "wordpress", "name": "WordPress"},
            {"id": "listmonk", "name": "Listmonk"},
            {"id": "farcaster", "name": "Farcaster"},
            {"id": "lemmy", "name": "Lemmy"},
            {"id": "nostr", "name": "Nostr"},
            {"id": "vk", "name": "VK"},
            {"id": "dribbble", "name": "Dribbble"},
            {"id": "skool", "name": "Skool"},
            {"id": "mewe", "name": "MeWe"},
            {"id": "whop", "name": "Whop"},
        ]

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                # This request usually doesn't require auth on the internal route
                response = await client.get(url)
                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, list):
                        return [
                            {
                                "id": p.get("id"),
                                "name": p.get("name", p.get("id").capitalize()),
                            }
                            for p in data
                        ]
            except Exception as e:
                logger.debug(
                    f"Could not fetch available providers from API, using fallback: {e}"
                )

        return fallback_providers

    async def create_post(
        self,
        content: str,
        integrations: List[str],
        publish_date: Optional[str] = None,
        media: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """Create or schedule a post on one or more social media integrations.

        Args:
            content: The text content of the post.
            integrations: List of integration IDs (channels) to post to.
            publish_date: Optional ISO-8601 date string for scheduling.
                         If None, it defaults to now (timezone-aware).
            media: Optional list of media objects [{'id': '...', 'path': '...'}] to include.

        Returns:
            The created post objects from the API.
        """
        url = f"{self.base_url.rstrip('/')}/posts"
        headers = await self._get_headers()

        # Handle publish date
        if publish_date:
            date_str = publish_date
        else:
            app = await App.get()
            if app:
                now_val = await app.now()
                date_str = now_val if isinstance(now_val, str) else now_val.isoformat()
            else:
                date_str = datetime.utcnow().isoformat() + "Z"

        posts = []
        for integration_id in integrations:
            post_item = {
                "integration": {"id": integration_id},
                "value": [{"content": content, "image": media if media else []}],
                "settings": {
                    "title": str(content)[
                        :100
                    ],  # Default title for platforms like YouTube
                    "type": "public",  # Default visibility
                },
            }
            posts.append(post_item)

        payload: Dict[str, Any] = {
            "type": "now" if not publish_date else "schedule",
            "date": date_str,
            "shortLink": True,
            "tags": [],
            "posts": posts,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"Postiz API error creating post: {e.response.text}")
                raise
            except Exception as e:
                logger.error(f"Unexpected error creating Postiz post: {e}")
                raise

    async def upload_media(self, file_path: str) -> Dict[str, Any]:
        """Upload a local file to Postiz media storage.

        Args:
            file_path: Path to the local file to upload.

        Returns:
            The uploaded media object containing the ID and path.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        url = f"{self.base_url.rstrip('/')}/upload"
        headers = await self._get_headers()
        # Remove Content-Type as httpx will set it for multipart
        headers.pop("Content-Type", None)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                with open(file_path, "rb") as f:
                    files = {"file": (os.path.basename(file_path), f)}
                    response = await client.post(url, headers=headers, files=files)
                    response.raise_for_status()
                    return response.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"Postiz API error uploading media: {e.response.text}")
                raise
            except Exception as e:
                logger.error(f"Unexpected error uploading Postiz media: {e}")
                raise
