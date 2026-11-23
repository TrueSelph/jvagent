"""Conversation-owned immutable payloads referenced by official SDK snapshots."""

from jvspatial.core import Node
from jvspatial.core.annotations import attribute

MAX_NATIVE_MEDIA_BYTES = 10 * 1024 * 1024


class NativeMedia(Node):
    """One content-addressed payload per authenticated conversation scope."""

    scope: dict[str, str] = attribute(default_factory=dict, protected=True)
    uri: str = attribute(default="", protected=True)
    payload: str = attribute(
        default="",
        protected=True,
        repr=False,
        max_length=4 * ((MAX_NATIVE_MEDIA_BYTES + 2) // 3),
    )
    byte_count: int = attribute(
        default=0, protected=True, ge=0, le=MAX_NATIVE_MEDIA_BYTES
    )
    media_metadata: dict[str, str] = attribute(default_factory=dict, protected=True)
