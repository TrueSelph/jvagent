"""VisionAction (ADR-0021) — multimodal image interpretation.

Runs a **dedicated, independently-configured** multimodal model over the images
in ``visitor.data["image_urls"]`` (the canonical cross-channel key) to produce a
text interpretation. Two consumers:

- The orchestrator's **pre-loop vision reflex** calls :meth:`describe` when a turn
  carries images and stores the result as a conversation **artifact**
  (``source:"vision"``) for the current turn's reply and future back-reference.
- The **``interpret_images`` tool** lets the model (re)interpret on demand.

The reusable helpers in ``interact/utils/vision_prompt`` do the actual model
call; this action just owns the model config and the tool surface. Suppression:
``visitor.data["image_interpretation"] = False`` skips vision (interview opt-out).
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action
from jvagent.action.interact.utils.vision_prompt import generate_image_interpretation

logger = logging.getLogger(__name__)


def image_urls_from_visitor(visitor: Any) -> List[Any]:
    """Canonical image list from a visitor, honoring the suppression flag."""
    data = getattr(visitor, "data", None) or {}
    if data.get("image_interpretation") is False:
        return []
    raw = data.get("image_urls") or []
    return list(raw) if isinstance(raw, (list, tuple)) else []


class VisionAction(Action):
    """Interpret images with a dedicated multimodal model (ADR-0021)."""

    model_action_type: str = attribute(
        default="OpenAILanguageModelAction",
        description="LanguageModelAction entity type for the vision pass.",
    )
    model: str = attribute(
        default="gpt-4o",
        description="Multimodal model id (must be vision-capable).",
    )
    model_temperature: Optional[float] = attribute(default=None)
    model_max_tokens: Optional[int] = attribute(default=None)

    async def describe(
        self,
        visitor: Any = None,
        images: Optional[List[Any]] = None,
        prompt: Optional[str] = None,
    ) -> str:
        """Return an extensive interpretation of the images, or "" when none."""
        urls = images if images is not None else image_urls_from_visitor(visitor)
        if not urls:
            return ""
        model_action = await self.get_model_action(required=False)
        if model_action is None:
            logger.warning(
                "VisionAction: no model action (%s) resolved; skipping vision",
                self.model_action_type,
            )
            return ""
        try:
            return await generate_image_interpretation(
                urls,
                model_action,
                model=self.model or None,
                temperature=self.model_temperature,
                max_tokens=self.model_max_tokens,
                prompt=prompt,
            )
        except Exception as exc:
            logger.warning("VisionAction.describe failed: %s", exc)
            return ""

    async def get_tools(self) -> List[Any]:
        from jvagent.tooling.tool import Tool

        return [
            Tool(
                name="interpret_images",
                description=(
                    "Describe the image(s) attached to the current message. "
                    "Returns an extensive text interpretation you can use to "
                    "answer the user's question about them."
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "Custom prompt for image interpretation. Defaults to exhaustive description.",
                        },
                    },
                },
                execute=self._tool_interpret,
            )
        ]

    async def _tool_interpret(
        self, visitor: Any = None, prompt: Optional[str] = None, **_: Any
    ) -> Any:
        from jvagent.tooling.tool_result import ToolResult

        text = await self.describe(visitor=visitor, prompt=prompt)
        if not text:
            return ToolResult(content="(no images on the current message to interpret)")
        return ToolResult(content=text)
