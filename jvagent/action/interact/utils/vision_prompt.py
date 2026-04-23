"""Vision prompt builder for multimodal LLM input.

Channel-agnostic helper that any InteractAction can use to build
multimodal prompts from visitor.data when image URLs are present.

Standard key: visitor.data["image_urls"] is the canonical key for image URLs
across channels (WhatsApp, Interact API, etc.). Media sources should populate
this key with a list of URL strings, ``{url, detail?}`` dicts, or ``{base64, mime_type?}``
dicts (inline images; preferred when remote URLs are not fetchable by the model provider).

- build_prompt_for_vision(): Builds multimodal content for the main response.
- generate_image_interpretation(): Produces an extensive image description behind
  the scenes for storage on Interaction.image_interpretation (enables follow-up
  questions). Call only when visitor.data.get("image_interpretation") is not False.

Suppression: Set visitor.data["image_interpretation"] = False to skip vision
(e.g. when images are document uploads for an interview, not for interpretation).
"""

from typing import TYPE_CHECKING, Any, List, Optional, Sequence, Union

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.action.model.language.base import LanguageModelAction

IMAGE_INTERPRETATION_PROMPT = """Describe this image in exhaustive detail. Capture every visible element: objects, colors, text, layout, people, setting, background, foreground, any writing or labels, spatial relationships, and any other relevant details. Be thorough so follow-up questions can be answered from this description alone. Output only the description, no preamble."""


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
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, str):
                    image_urls.append({"url": item})
                elif isinstance(item, dict) and ("url" in item or "base64" in item):
                    image_urls.append(item)
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

    Returns:
        Raw interpretation string, or empty string if no valid images
    """
    normalized = _normalize_image_urls(image_urls)
    if not normalized:
        return ""

    prompt = model_action.create_multimodal_content(
        text=IMAGE_INTERPRETATION_PROMPT, images=normalized
    )
    extra: dict = {}
    if model is not None:
        extra["model"] = model
    if temperature is not None:
        extra["temperature"] = temperature
    if max_tokens is not None:
        extra["max_tokens"] = max_tokens

    result = await model_action.generate(
        prompt=prompt,
        stream=False,
        history=None,
        calling_action_name="PersonaAction",
        transient=True,
        **extra,
    )
    return (result or "").strip()
