"""VisionAction (ADR-0021) — multimodal image interpretation.

Runs a **dedicated, independently-configured** multimodal model over the images
in ``visitor.data["image_urls"]`` (the canonical cross-channel key) to produce a
text interpretation. Two consumers:

- The orchestrator's **pre-loop vision reflex** calls :meth:`describe` when a turn
  carries images and stores the result as a conversation **artifact**
  (``source:"vision"``) for the current turn's reply and future back-reference.
- The **``interpret_images`` tool** lets the model (re)interpret on demand.

The reusable helpers in the sibling ``multimodal`` module do the actual model
call; this action just owns the model config and the tool surface. Everything
vision needs — prompts and model operations — lives under this folder so the
action is self-contained. Suppression:
``visitor.data["image_interpretation"] = False`` skips vision (interview opt-out).
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action
from jvagent.action.vision.multimodal import generate_image_interpretation
from jvagent.action.vision.prompts import IMAGE_INTERPRETATION_PROMPT
from jvagent.tooling.tool_decorator import tool

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
    interpretation_prompt: str = attribute(
        default=IMAGE_INTERPRETATION_PROMPT,
        description=(
            "Default instruction sent with the image(s). Override via agent.yaml "
            "to tune what the vision pass extracts. A per-call prompt (e.g. the "
            "interpret_images tool) still takes precedence."
        ),
    )

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
        prompt = prompt or self.interpretation_prompt or IMAGE_INTERPRETATION_PROMPT
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

    @tool(name="interpret_images")
    async def _t_interpret_images(
        self,
        prompt: Annotated[
            Optional[str],
            "Custom prompt for image interpretation. "
            "Defaults to exhaustive description.",
        ] = None,
        **kwargs: Any,
    ) -> Any:
        """Describe the image(s) attached to the current message. Returns an extensive text interpretation you can use to answer the user's question about them."""  # noqa: E501
        from jvagent.tooling.tool_executor import get_dispatch_visitor
        from jvagent.tooling.tool_result import ToolResult

        # ``visitor`` may be passed explicitly by a caller/executor; otherwise
        # resolve it from the dispatch context. (``**kwargs`` keeps it out of
        # the derived schema — the model only sees ``prompt``.)
        visitor = kwargs.get("visitor") or get_dispatch_visitor()
        text = await self.describe(visitor=visitor, prompt=prompt)
        if not text:
            return ToolResult(content="(no images on the current message to interpret)")
        return ToolResult(content=text)
