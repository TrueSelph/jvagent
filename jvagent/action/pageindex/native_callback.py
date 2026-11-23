"""Bounded native completion for an authenticated PageIndex callback."""

from typing import Any

from jvspatial.api.exceptions import ValidationError

from jvagent.harness.inputs import NativeInputError, native_user_prompt

from .native_search import Complete, admitted_operation_model, run_native_operation

CHANNEL = "pageindex_webhook_completion"


async def run_native_callback(
    action: Any,
    *,
    agent_id: str,
    actor: str,
    payload: dict[str, Any],
    configured_model: str = "",
) -> dict[str, Any]:
    """Use host provider and media policy while preserving the callback response."""
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 100_000:
        raise ValidationError(
            "A nonempty PageIndex prompt of at most 100,000 characters is required"
        )
    prompt = prompt.strip()
    operation_model = configured_model or action.model
    model = admitted_operation_model(operation_model, payload.get("model"))

    encoded = payload.get("image_base64")
    media = None
    if encoded is not None and encoded != "":
        media = {
            "attachments": [
                {
                    "filename": "PageIndex image",
                    "base64": encoded,
                    "mime_type": payload.get("mime_type") or "image/jpeg",
                }
            ]
        }
    try:
        content = native_user_prompt(
            prompt,
            media,
            allowed_media_types=[
                mime for mime in action.input_media_types if mime.startswith("image/")
            ],
        )
    except NativeInputError:
        raise ValidationError("Invalid or disabled PageIndex callback image") from None

    async def operation(complete: Complete) -> dict[str, Any]:
        result = await complete(content)
        output: dict[str, Any] = {"text": result.output, "model": model}
        usage = result.usage
        if usage.total_tokens:
            output["usage"] = {
                "prompt_tokens": usage.input_tokens,
                "completion_tokens": usage.output_tokens,
                "total_tokens": usage.total_tokens,
            }
        return output

    return await run_native_operation(
        action,
        agent_id=agent_id,
        actor=actor,
        query=prompt,
        channel=CHANNEL,
        operation=operation,
        configured_model=operation_model,
    )
