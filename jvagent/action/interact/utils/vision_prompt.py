"""Vision prompt builder for multimodal LLM input.

Channel-agnostic helper that any InteractAction can use to build
multimodal prompts from visitor.data when image URLs are present.

Standard key: visitor.data["image_urls"] is the canonical key for image URLs
across channels (WhatsApp, Interact API, etc.). Media sources should populate
this key with a list of URLs or [{url, detail?}] dicts.
"""

from typing import TYPE_CHECKING, Any, List, Optional, Sequence, Union

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.action.model.language.base import LanguageModelAction


def build_prompt_for_vision(
    text: str,
    visitor: Optional["InteractWalker"],
    model_action: "LanguageModelAction",
    image_data_keys: Sequence[str] = ("image_urls",),
) -> Union[str, List[Any]]:
    """Build prompt as text or multimodal content if images are present.

    Checks visitor.data for image_data_keys (default: image_urls, the standard key).
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
