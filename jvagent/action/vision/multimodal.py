"""Multimodal prompt builders + interpretation call (self-contained, ADR-0021).

Channel-agnostic helpers owned by the vision action. Any action may import
them, but they live here so the vision action carries its own prompts and
model operations with no dependency on the interact subsystem.

Standard key: ``visitor.data["image_urls"]`` is the canonical key for image
URLs across channels (WhatsApp, Interact API, email, etc.). Media sources
populate it with a list of URL strings, ``{url, detail?}`` dicts, or
``{base64, mime_type?}`` dicts (inline images; preferred when remote URLs are
not fetchable by the model provider).

- ``build_prompt_for_vision()``: builds multimodal content for a main response.
- ``generate_image_interpretation()``: produces an extensive image description
  behind the scenes. ``VisionAction`` stores the result as a conversation
  artifact (ADR-0021) for follow-up reference.

Suppression: set ``visitor.data["image_interpretation"] = False`` to skip vision
(e.g. when images are document uploads for an interview, not for interpretation).
"""

from typing import TYPE_CHECKING, Any, List, Optional, Sequence, Union

from jvagent.action.vision.prompts import IMAGE_INTERPRETATION_PROMPT

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.action.model.language.base import LanguageModelAction


def _normalize_image_urls(raw: Any) -> List[Any]:
    """Normalize image URLs to list of {url} or {base64} dicts."""
    result: List[Any] = []
    if not raw:
        return result
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                result.append({"url": item})
            elif isinstance(item, dict) and ("url" in item or "base64" in item):
                result.append(item)
    return result


def build_prompt_for_vision(
    text: str,
    visitor: Optional["InteractWalker"],
    model_action: "LanguageModelAction",
    image_data_keys: Sequence[str] = ("image_urls",),
) -> Union[str, List[Any]]:
    """Build prompt as text or multimodal content if images are present.

    Checks visitor.data for image_data_keys (default: image_urls, the standard key).
    Skips vision when visitor.data["image_interpretation"] is False.
    If image URLs found, returns List[ContentPart] via model_action.create_multimodal_content().
    Otherwise returns text unchanged.

    Args:
        text: The text prompt/utterance
        visitor: Optional InteractWalker with data dict
        model_action: LanguageModelAction with create_multimodal_content
        image_data_keys: Keys to check in visitor.data for image URLs (default: image_urls)

    Returns:
        text if no images; List[ContentPart] if images present
    """
    if not visitor or not getattr(visitor, "data", None):
        return text

    data = visitor.data
    if data.get("image_interpretation") is False:
        return text

    image_urls: List[Any] = []

    for key in image_data_keys:
        raw = data.get(key)
        if not raw:
            continue
        image_urls = _normalize_image_urls(raw)
        break  # Use first key that has data

    if not image_urls:
        return text

    return model_action.create_multimodal_content(text=text, images=image_urls)


async def generate_image_interpretation(
    image_urls: Any,
    model_action: "LanguageModelAction",
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    prompt: Optional[str] = IMAGE_INTERPRETATION_PROMPT,
) -> str:
    """Generate an extensive image interpretation behind the scenes.

    Uses a dedicated prompt to produce an exhaustive description of the image(s)
    for storage and follow-up question answering. Call only when
    visitor.data.get("image_interpretation") is not False.

    Args:
        image_urls: List of image URLs or [{url, detail?}] dicts
        model_action: LanguageModelAction with create_multimodal_content and generate
        model: Optional model override for this call only. When None the model_action's
            own default is used (preserving existing behavior).
        temperature: Optional temperature override. When None the provider default is used.
        max_tokens: Optional max-tokens override. When None the provider default is used.
        prompt: Instruction sent alongside the image(s). ``None``/empty falls back to
            ``IMAGE_INTERPRETATION_PROMPT`` so the model never receives a null text part.

    Returns:
        Raw interpretation string, or empty string if no valid images
    """
    normalized = _normalize_image_urls(image_urls)
    if not normalized:
        return ""

    prompt = prompt or IMAGE_INTERPRETATION_PROMPT
    content = model_action.create_multimodal_content(text=prompt, images=normalized)
    extra: dict = {}
    if model is not None:
        extra["model"] = model
    if temperature is not None:
        extra["temperature"] = temperature
    if max_tokens is not None:
        extra["max_tokens"] = max_tokens

    result = await model_action.generate(
        prompt=content,
        stream=False,
        history=None,
        calling_action_name="VisionAction",
        transient=True,
        **extra,
    )
    return (result or "").strip()
