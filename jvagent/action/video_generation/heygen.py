"""HeyGen video generation action implementation."""

import logging
from typing import Any, Dict, List, Optional, Sequence

import requests
from httpx import AsyncClient, Timeout
from jvspatial.core.annotations import attribute
from jvspatial.env import env

from jvagent.action.base import Action

logger = logging.getLogger(__name__)


class HeygenVideoAction(Action):
    """Generate video content using the HeyGen API.

    This action wraps a very thin HTTP client around HeyGen's ``/videos``
    endpoint.  It exposes a single helper method ``create_video`` which takes
    a script string and optional provider-specific parameters.  The method
    returns the parsed JSON response from the API or ``None`` on error.

    Example usage::

        action = await agent.get_action("HeygenVideoAction")
        result = await action.create_video("Hello world", voice="en_us_female")
        url = result.get("video_url") if result else None

    Configure the API key via the ``HEYGEN_API_KEY`` environment variable (``.env``).
    """

    avatar_type: str = attribute(
        default="avatar",  # avatar or talking_photo
        description="wheter to use an avatar or a talking photo",
    )
    template_id: str = attribute(default="", description="Template ID")
    avatar_id: str = attribute(default="", description="Avatar ID")
    talking_photo_id: str = attribute(default="", description="Talking Photo ID")
    voice_id: str = attribute(default="", description="Voice ID to use for the avatar")
    emotion: str = attribute(default="Broadcaster", description="Emotion of the avatar")
    locale: str = attribute(default="en_US", description="Locale")
    elevenlabs_settings: Dict[str, Any] = attribute(
        default={}, description="ElevenLabs Settings"
    )
    audio_url: str = attribute(default="", description="Audio URL")
    audio_asset_id: str = attribute(default="", description="Audio Asset ID")
    duration: str = attribute(default="", description="Duration")
    background: Dict[str, Any] = attribute(default={}, description="Background")
    text: Dict[str, Any] = attribute(default={}, description="Text")
    dimension: Dict[str, Any] = attribute(default={}, description="Dimension")
    folder_id: str = attribute(default="", description="Folder ID")
    callback_url: str = attribute(default="", description="Callback URL")
    webhook_events: List[str] = attribute(
        default=[
            "avatar_video.success",
            "avatar_video.fail",
            "video_agent.success",
            "video_agent.fail",
        ],
        description="HeyGen webhook events to subscribe to when auto-registering a webhook endpoint.",
    )

    @staticmethod
    def _api_base() -> str:
        return "https://api.heygen.com"

    @staticmethod
    def _api_key() -> str:
        return env("HEYGEN_API_KEY")

    def _headers(self) -> Dict[str, str]:
        api_key = self._api_key()
        return {
            "accept": "application/json",
            "content-type": "application/json",
            "x-api-key": api_key,
        }

    def _normalize_url(self, url: str) -> str:
        return (url or "").strip().rstrip("/")

    def _webhook_list(self) -> List[Dict[str, Any]]:
        """List registered HeyGen webhook endpoints for the API key."""
        api_key = self._api_key()
        if not api_key:
            raise ValueError("HeygenVideoAction: API key is not configured")

        url = f"{self._api_base().rstrip('/')}/v1/webhook/endpoint.list"
        resp = requests.get(
            url, headers={"accept": "application/json", "x-api-key": api_key}
        )
        resp.raise_for_status()
        body = resp.json()
        if not isinstance(body, dict) or body.get("code") != 100:
            raise ValueError(f"HeygenVideoAction: webhook list failed: {body}")
        data = body.get("data") or []
        return data if isinstance(data, list) else []

    def _webhook_add(
        self,
        callback_url: str,
        events: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """Register a webhook endpoint with HeyGen."""
        api_key = self._api_key()
        if not api_key:
            raise ValueError("HeygenVideoAction: API key is not configured")

        callback_url = self._normalize_url(callback_url)
        # HeyGen rejects non-public/non-HTTPS URLs (docs: SSL security level 2+).
        # In practice this often means `http://localhost...` or `http://...` will 400.
        if not callback_url.startswith("https://"):
            raise ValueError(
                "HeygenVideoAction: webhook callback_url must be a public HTTPS URL. "
                f"Got: {callback_url!r}"
            )

        url = f"{self._api_base().rstrip('/')}/v1/webhook/endpoint.add"
        payload: Dict[str, Any] = {"url": callback_url}
        if events is not None:
            payload["events"] = list(events) if len(events) > 0 else None

        resp = requests.post(url, json=payload, headers=self._headers())
        # Don't raise until we've captured HeyGen's error payload for debugging.
        try:
            body: Any = resp.json()
        except ValueError:
            body = resp.text
        if not resp.ok:
            raise ValueError(
                "HeygenVideoAction: webhook add failed "
                f"(http={resp.status_code}, response={body!r}, payload_url={callback_url!r})"
            )
        if not isinstance(body, dict) or body.get("code") != 100:
            raise ValueError(f"HeygenVideoAction: webhook add failed: {body}")
        data = body.get("data") or {}
        return data if isinstance(data, dict) else {}

    def _webhook_delete(self, endpoint_id: str) -> None:
        api_key = self._api_key()
        if not api_key:
            raise ValueError("HeygenVideoAction: API key is not configured")
        url = f"{self._api_base().rstrip('/')}/v1/webhook/endpoint.delete"
        resp = requests.delete(
            url,
            params={"endpoint_id": endpoint_id},
            headers={"accept": "application/json", "x-api-key": api_key},
        )
        try:
            body: Any = resp.json()
        except ValueError:
            body = resp.text
        if not resp.ok:
            raise ValueError(
                "HeygenVideoAction: webhook delete failed "
                f"(http={resp.status_code}, response={body!r}, endpoint_id={endpoint_id!r})"
            )
        if not isinstance(body, dict) or body.get("code") != 100:
            raise ValueError(f"HeygenVideoAction: webhook delete failed: {body}")

    async def reconcile_webhook_endpoint(
        self,
        desired_url: str,
        *,
        manage_prefix: Optional[str] = None,
        manage_suffix: Optional[str] = None,
        events: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """Ensure HeyGen has exactly one endpoint for `desired_url`.

        - Prevents duplicates for the exact URL
        - Deletes stale endpoints (scoped by `manage_prefix` or `manage_suffix`)
        """
        desired_norm = self._normalize_url(desired_url)
        if not desired_norm:
            raise ValueError("HeygenVideoAction: desired_url is empty")

        prefix_norm = self._normalize_url(manage_prefix) if manage_prefix else None
        suffix_norm = self._normalize_url(manage_suffix) if manage_suffix else None
        effective_events = (
            events if events is not None else getattr(self, "webhook_events", None)
        )

        # The _webhook_* helpers use sync `requests`. Run them in worker
        # threads so the event loop is not stalled. AUDIT-actions XC-3.
        import asyncio

        endpoints = await asyncio.to_thread(self._webhook_list)

        exact_matches: List[Dict[str, Any]] = []
        stale_matches: List[Dict[str, Any]] = []

        for ep in endpoints:
            ep_url = self._normalize_url(str(ep.get("url") or ""))
            if not ep_url:
                continue
            if ep_url == desired_norm:
                exact_matches.append(ep)
            elif prefix_norm and ep_url.startswith(prefix_norm):
                stale_matches.append(ep)
            elif isinstance(suffix_norm, str):
                if ep_url.endswith(suffix_norm):
                    stale_matches.append(ep)

        deleted: List[str] = []
        for ep in stale_matches:
            endpoint_id = str(ep.get("endpoint_id") or "")
            if endpoint_id:
                try:
                    await asyncio.to_thread(self._webhook_delete, endpoint_id)
                    deleted.append(endpoint_id)
                except Exception as exc:
                    logger.warning(
                        "HeygenVideoAction: failed deleting stale webhook %s: %s",
                        endpoint_id,
                        exc,
                    )

        # If duplicates exist for exact URL, keep one and delete the rest.
        kept: Optional[Dict[str, Any]] = None
        if exact_matches:
            kept = exact_matches[0]
            for ep in exact_matches[1:]:
                endpoint_id = str(ep.get("endpoint_id") or "")
                if endpoint_id:
                    try:
                        await asyncio.to_thread(self._webhook_delete, endpoint_id)
                        deleted.append(endpoint_id)
                    except Exception as exc:
                        logger.warning(
                            "HeygenVideoAction: failed deleting duplicate webhook %s: %s",
                            endpoint_id,
                            exc,
                        )

        created: Optional[Dict[str, Any]] = None
        if not kept:
            created = await asyncio.to_thread(
                self._webhook_add, desired_norm, effective_events
            )
            kept = created

        # Surface relevant info to callers (and logs)
        endpoint_id = str((kept or {}).get("endpoint_id") or "")
        secret = str((kept or {}).get("secret") or "")
        if secret:
            logger.info(
                "HeygenVideoAction: webhook endpoint ready (endpoint_id=%s). "
                "If you verify signatures, ensure HEYGEN_WEBHOOK_SECRET matches this endpoint secret.",
                endpoint_id,
            )

        return {
            "desired_url": desired_norm,
            "endpoint": kept or {},
            "created": created is not None,
            "deleted_endpoint_ids": deleted,
        }

    async def create_video(
        self, script: str, title: str = "Video", **kwargs: Any
    ) -> Optional[Dict[str, Any]]:
        """Create a video and return the provider response."""
        api_key = self._api_key()
        if not api_key:
            logger.error("HeygenVideoAction: API key is not configured")
            return None

        url = "https://api.heygen.com/v2/video/generate"

        # callback_id correlates HeyGen's async webhook back to this request.
        # The previous default of the literal string "string" left every
        # request indistinguishable. AUDIT-actions video_generation.
        import uuid as _uuid

        callback_id = (
            kwargs.get("callback_id")
            or (kwargs.get("payload") or {}).get("callback_id")
            or _uuid.uuid4().hex
        )

        payload = {
            "caption": True,
            "title": title,
            "callback_id": callback_id,
            "video_inputs": [
                {
                    "character": {
                        "type": "avatar",  # avatar or talking_photo
                        "avatar_id": self.avatar_id,
                        "talking_photo_id": self.talking_photo_id,
                        "scale": 1,
                        "avatar_style": "normal",
                        # "talking_photo_style": "circle",
                        # "use_avatar_iv_model": True,
                        # "prompt": "string",
                        # "keep_original_prompt": True,
                        # "offset": {
                        #     "x": 0,
                        #     "y": 0
                        # },
                        "talking_style": "expressive",  # stable, expressive
                        "expression": "default",
                        "super_resolution": True,
                        "matting": True,
                        # "circle_background_color": "string"
                    },
                    "voice": {
                        "type": "text",
                        "voice_id": self.voice_id,
                        "input_text": script,
                        # "speed": 0,
                        # "pitch": 0,
                        "emotion": self.emotion,
                        "locale": self.locale,
                        # "elevenlabs_settings": {
                        #     "model": "eleven_monolingual_v1",
                        #     # "similarity_boost": 0,
                        #     # "stability": 0,
                        #     # "style": 0
                        # },
                        # "audio_url": "string",
                        # "audio_asset_id": "string",
                        # "duration": "1"
                    },
                    "background": {
                        "type": "color",
                        "value": "#FFFFFF",
                        "play_style": "freeze",
                        "fit": "cover",
                    },
                    "text": {
                        "type": "text",
                        "text": " ",
                        "font_family": "Arial",
                        "font_size": 12,
                        "font_weight": "bold",
                        # "color": "string",
                        # "position": {
                        #     "x": 0,
                        #     "y": 0
                        # },
                        "text_align": "left",
                        "line_height": 1,
                        # "width": 0
                    },
                }
            ],
            # "dimension": {
            #     "width": 0,
            #     "height": 0
            # },
            # "folder_id": "string",
            # "callback_url": "string"
        }

        if kwargs.get("payload"):
            payload.update(kwargs["payload"])

        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "x-api-key": api_key,
        }

        # AUDIT-actions XC-3: use httpx.AsyncClient to avoid blocking the loop.
        async with AsyncClient(timeout=Timeout(60.0)) as client:
            response = await client.post(url, json=payload, headers=headers)
        try:
            body: Any = response.json()
        except ValueError:
            logger.error("HeygenVideoAction: response was not valid JSON")
            return None
        if response.is_error:
            logger.error(
                "HeygenVideoAction: HTTP %s: %s",
                response.status_code,
                body,
            )
            return None
        if not isinstance(body, dict):
            logger.error("HeygenVideoAction: expected JSON object, got %s", type(body))
            return None
        return body

    async def create_video_from_template(
        self, script: dict, **kwargs: Any
    ) -> Optional[Dict[str, Any]]:
        """Generate a video from a HeyGen template, replacing scene scripts with the provided script dict.

        Args:
            script: Ordered dict of section_name -> script_text (e.g. {"Intro": "Hey there...", "Politics - ...": "..."})
            **kwargs: Optional overrides: template_id, variables
        """
        template_id = kwargs.get("template_id", getattr(self, "template_id", ""))
        template = await self.retrieve_template(template_id)
        data = template.get("data", {})

        scenes: list = data.get("scenes", [])
        template_variables: dict = data.get("variables", {})
        print(f"\033[96m{scenes}\033[0m")
        print(f"\033[95m{template_variables}\033[0m")

        # Identify speaking scenes — scenes that have a character-type variable
        # These are eligible to receive script text from the provided script dict.
        speaking_scene_indices = [
            i
            for i, scene in enumerate(scenes)
            if any(v.get("type") == "character" for v in scene.get("variables", []))
        ]

        # Map each script section (in order) to the next available speaking scene
        script_sections = list(script.values()) if isinstance(script, dict) else []
        for section_idx, script_text in enumerate(script_sections):
            if section_idx < len(speaking_scene_indices):
                scene_idx = speaking_scene_indices[section_idx]
                scenes[scene_idx]["script"] = str(script_text)
            else:
                # More sections than speaking scenes — append extra sections to the last speaking scene
                if speaking_scene_indices:
                    last_scene_idx = speaking_scene_indices[-1]
                    scenes[last_scene_idx]["script"] += f" {script_text}"

        logger.debug(
            f"create_video_from_template: mapped {len(script_sections)} script sections "
            f"across {len(speaking_scene_indices)} speaking scene(s)"
        )

        # Build the variables payload from the top-level template variables
        variables: dict = {}
        for var_name, var_data in template_variables.items():
            variables[var_name] = {
                "name": var_name,
                "type": var_data.get("type", "text"),
                "properties": var_data.get("properties", {}),
            }

        # Allow callers to inject or override variables directly
        if kwargs.get("variables"):
            variables.update(kwargs["variables"])

        api_key = self._api_key()
        if not api_key:
            logger.error("HeygenVideoAction: API key is not configured")
            return None

        url = f"https://api.heygen.com/v2/template/{template_id}/generate"

        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "x-api-key": api_key,
        }

        payload = {
            "caption": True,
            "dimension": {
                "width": 720,
                "height": 1280,
            },
            "include_gif": False,
            "variables": variables,
        }

        # AUDIT-actions XC-3: use httpx.AsyncClient to avoid blocking the loop.
        async with AsyncClient(timeout=Timeout(60.0)) as client:
            response = await client.post(url, json=payload, headers=headers)
        logger.debug("HeygenVideoAction: template response %s", response.text)

        return response.json()

    async def retrieve_template(self, template_id: str) -> Any:
        """Retrieve a template by ID."""
        api_key = self._api_key()
        if not api_key:
            logger.error("HeygenVideoAction: API key is not configured")
            return {}

        url = f"https://api.heygen.com/v3/template/{template_id}"

        headers = {"accept": "application/json", "x-api-key": api_key}

        # AUDIT-actions XC-3.
        async with AsyncClient(timeout=Timeout(60.0)) as client:
            response = await client.get(url, headers=headers)
        return response.json()

    async def create_video_from_prompt(self, prompt: str, **kwargs: Any) -> Any:
        """Generate a video from a prompt."""
        api_key = self._api_key()
        if not api_key:
            logger.error("HeygenVideoAction: API key is not configured")
            return None

        url = "https://api.heygen.com/v1/video_agent/generate"

        payload = {
            "config": {"orientation": "portrait", "avatar_id": self.avatar_id},
            "prompt": prompt,
        }
        if kwargs.get("payload"):
            payload.update(kwargs["payload"])
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "x-api-key": api_key,
        }

        # AUDIT-actions XC-3.
        async with AsyncClient(timeout=Timeout(60.0)) as client:
            response = await client.post(url, json=payload, headers=headers)

        logger.debug("HeygenVideoAction: prompt response %s", response.text)

        return response

    async def get_video(self, video_id: str) -> Any:
        """Get a video status by ID."""
        api_key = self._api_key()
        if not api_key:
            logger.error("HeygenVideoAction: API key is not configured")
            return None

        url = f"https://api.heygen.com/v1/video_status.get?video_id={video_id}"

        headers = {"accept": "application/json", "x-api-key": api_key}

        # AUDIT-actions XC-3.
        async with AsyncClient(timeout=Timeout(60.0)) as client:
            response = await client.get(url, headers=headers)
        status = response.json()["data"]["status"]

        if status == "completed":
            video_url = response.json()["data"]["video_url"]
            thumbnail_url = response.json()["data"]["thumbnail_url"]
            logger.info(
                "HeygenVideoAction: video ready url=%s thumbnail=%s",
                video_url,
                thumbnail_url,
            )

            # Save the video to a file
            video_filename = "generated_video.mp4"
            with open(video_filename, "wb") as video_file:
                # AUDIT-actions XC-3.
                async with AsyncClient(timeout=Timeout(120.0)) as client:
                    video_resp = await client.get(video_url)
                video_content = video_resp.content
                video_file.write(video_content)

        print(response.text)
        return response

    async def healthcheck(self) -> Any:
        """Basic health check for the HeyGen service.

        The real API may not expose a public health endpoint; if that's the
        case this implementation simply attempts to list videos or perform a
        lightweight noop.  For now we just validate that ``api_key`` is set.
        """
        api_key = self._api_key()
        if not api_key:
            return {
                "status": False,
                "message": "HeyGen API key is not configured",
                "severity": "error",
            }

        # attempt a simple request to verify credentials
        try:
            async with AsyncClient(timeout=Timeout(self.timeout)) as client:
                resp = await client.get(
                    f"{self.api_base.rstrip('/')}/videos",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                if resp.status_code == 200:
                    return True
                return {
                    "status": False,
                    "message": f"healthcheck returned {resp.status_code}",
                    "severity": "error",
                }
        except Exception as exc:  # pragma: no cover
            logger.error("HeygenVideoAction healthcheck error: %s", exc, exc_info=True)
            return {"status": False, "message": str(exc), "severity": "error"}
