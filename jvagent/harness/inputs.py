"""Bounded JV attachment envelopes as public Pydantic AI user content."""

import base64
import binascii
from typing import Any

from jvagent.action.interact.utils.uploads import (
    DEFAULT_UPLOAD_KEYS,
    MAX_UPLOAD_ITEM_BYTES,
)

MAX_ATTACHMENTS = 20
MAX_TOTAL_BYTES = 10 * 1024 * 1024
_BINARY_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "application/pdf",
}
INPUT_MEDIA_TYPES = tuple(
    sorted(_BINARY_TYPES | {"text/plain", "text/csv", "text/markdown"})
)
INPUT_NOTICE = (
    "The attachments could not be processed. Attachment formats must be enabled "
    "in the agent's input_media_types setting. Supported formats are inline PNG, JPEG, WebP, GIF, "
    "PDF or UTF-8 text files, at most 5 MiB each and 10 MiB total across 20 files. "
    "Remote attachment URLs are not supported by this native profile. "
    "Start a new conversation with the corrected attachments."
)


class NativeInputError(ValueError):
    """The complete incoming attachment set was refused before model work."""


def validate_input_media_types(value: Any) -> frozenset[str]:
    """Host configuration only; unsupported formats never reach a provider."""
    if (
        not isinstance(value, (list, tuple))
        or len(value) > len(INPUT_MEDIA_TYPES)
        or any(
            not isinstance(mime, str) or mime not in INPUT_MEDIA_TYPES for mime in value
        )
        or len(set(value)) != len(value)
    ):
        raise ValueError("input_media_types requires unique supported MIME types")
    return frozenset(value)


def native_user_prompt(
    utterance: str, data: Any, *, allowed_media_types: Any = INPUT_MEDIA_TYPES
) -> str | list[Any]:
    """Preserve each admitted file; never fetch, interpret or silently truncate."""
    from pydantic_ai.messages import BinaryContent

    allowed = validate_input_media_types(allowed_media_types)
    if not isinstance(data, dict):
        return utterance
    entries: list[Any] = []
    for key in DEFAULT_UPLOAD_KEYS:
        value = data.get(key)
        if value is None:
            continue
        if not isinstance(value, list) or len(value) + len(entries) > MAX_ATTACHMENTS:
            raise NativeInputError(INPUT_NOTICE)
        entries.extend(value)
    if not entries:
        return utterance
    result: list[Any] = [utterance]
    total = 0
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("url"):
            raise NativeInputError(INPUT_NOTICE)
        encoded = entry.get("base64")
        mime = entry.get("mime_type", entry.get("mime"))
        filename = entry.get("filename", "attachment")
        if (
            not isinstance(encoded, str)
            or not isinstance(mime, str)
            or not isinstance(filename, str)
            or not filename
            or len(filename) > 255
            or any(ord(c) < 32 for c in filename)
        ):
            raise NativeInputError(INPUT_NOTICE)
        mime = mime.strip().lower()
        if mime not in allowed:
            raise NativeInputError(INPUT_NOTICE)
        if encoded.startswith("data:"):
            prefix, separator, encoded = encoded.partition(",")
            if not separator or prefix != f"data:{mime};base64":
                raise NativeInputError(INPUT_NOTICE)
        if len(encoded) > 4 * ((MAX_UPLOAD_ITEM_BYTES + 2) // 3):
            raise NativeInputError(INPUT_NOTICE)
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise NativeInputError(INPUT_NOTICE) from None
        total += len(raw)
        if not raw or len(raw) > MAX_UPLOAD_ITEM_BYTES or total > MAX_TOTAL_BYTES:
            raise NativeInputError(INPUT_NOTICE)
        result.append(f"Attached file: {filename}")
        if mime in _BINARY_TYPES:
            result.append(BinaryContent(data=raw, media_type=mime))
        else:
            try:
                result.append(raw.decode("utf-8", errors="strict"))
            except UnicodeError:
                raise NativeInputError(INPUT_NOTICE) from None
    return result
