"""Public SDK MediaStore over the existing object-spatial graph, without URLs."""

import base64
import binascii
import hashlib
import json
from collections.abc import Mapping
from typing import Any

from jvspatial import flush_deferred_entities
from pydantic import TypeAdapter
from pydantic_ai_harness.media import MediaContext, media_uri_for, parse_media_uri
from pydantic_ai_harness.step_persistence import RunRecord

from jvagent.memory.conversation import Conversation
from jvagent.memory.native_media import MAX_NATIVE_MEDIA_BYTES, NativeMedia

MEDIA_THRESHOLD_BYTES = 65_536
MAX_MEDIA_BYTES = MAX_NATIVE_MEDIA_BYTES
_EMPTY_CONTEXT = MediaContext()


class NativeMediaError(ValueError):
    """A stored payload or its host binding cannot be trusted."""


class NativeMediaMissing(FileNotFoundError):
    """A payload is absent in the authorized conversation scope."""


class GraphMediaStore:
    """Immutable bytes scoped by server-established SDK and conversation identity."""

    def __init__(self, interaction: Any, *, agent_id: str | None = None) -> None:
        self.interaction = interaction
        self.agent_id = agent_id
        self.binding = {
            key: getattr(interaction, key, None)
            for key in ("conversation_id", "user_id", "session_id")
        }

    async def _scope(self) -> tuple[dict[str, str], Conversation]:
        if any(not isinstance(v, str) or not v for v in self.binding.values()) or any(
            getattr(self.interaction, key, None) != value
            for key, value in self.binding.items()
        ):
            raise NativeMediaError("Native media requires stable host identity")
        try:
            records = TypeAdapter(list[RunRecord]).validate_python(
                self.interaction.native_steps.get("runs", [])
            )
        except (TypeError, ValueError):
            raise NativeMediaError("Native media SDK registration is invalid") from None
        roots = [record for record in records if record.parent_run_id is None]
        agents = [record.metadata.get("jv_agent_id") for record in roots]
        if (
            not agents
            or not all(isinstance(a, str) and a for a in agents)
            or len(set(agents)) != 1
        ):
            raise NativeMediaError("Native media requires a registered host agent")
        agent = agents[0]
        if self.agent_id is not None and self.agent_id != agent:
            raise NativeMediaError("Native media agent identity changed")
        expected = {
            "jv_interaction_id": self.interaction.id,
            **{"jv_" + key: value for key, value in self.binding.items()},
        }
        if any(
            record.conversation_id != self.binding["session_id"]
            or any(record.metadata.get(k) != v for k, v in expected.items())
            for record in roots
        ):
            raise NativeMediaError("Native media SDK binding does not match its host")
        rows = await Conversation.find({"id": self.binding["conversation_id"]})
        if len(rows) != 1 or any(
            getattr(rows[0], key) != self.binding[key]
            for key in ("user_id", "session_id")
        ):
            raise NativeMediaError("Native media conversation ownership is unavailable")
        return {"agent_id": agent, **self.binding}, rows[0]

    @staticmethod
    def _id(scope: dict[str, str], uri: str) -> str:
        try:
            parse_media_uri(uri)
        except (ValueError, TypeError, AttributeError):
            raise NativeMediaError("Invalid native media reference") from None
        key = json.dumps([scope, uri], sort_keys=True, separators=(",", ":"))
        return "n.NativeMedia." + hashlib.sha256(key.encode()).hexdigest()

    async def _node(self, uri: str) -> NativeMedia:
        scope, _ = await self._scope()
        rows = await NativeMedia.find({"id": self._id(scope, uri)})
        if not rows:
            raise NativeMediaMissing("Native media is unavailable in this scope")
        if len(rows) != 1:
            raise NativeMediaError("Native media identity is ambiguous")
        node = rows[0]
        if node.scope != scope or node.uri != uri:
            raise NativeMediaError("Native media scope or identity is invalid")
        return node

    @staticmethod
    def _bytes(node: NativeMedia) -> bytes:
        if (
            not isinstance(node.payload, str)
            or type(node.byte_count) is not int
            or not 0 <= node.byte_count <= MAX_MEDIA_BYTES
            or len(node.payload) > 4 * ((MAX_MEDIA_BYTES + 2) // 3)
        ):
            raise NativeMediaError("Native media exceeds storage bounds")
        try:
            raw = base64.b64decode(node.payload, validate=True)
        except (ValueError, binascii.Error):
            raise NativeMediaError("Native media encoding is invalid") from None
        if len(raw) != node.byte_count or media_uri_for(raw) != node.uri:
            raise NativeMediaError("Native media integrity check failed")
        return raw

    async def put(self, data: bytes, *, context: MediaContext = _EMPTY_CONTEXT) -> str:
        if not isinstance(data, bytes) or len(data) > MAX_MEDIA_BYTES:
            raise NativeMediaError("Native media exceeds storage bounds")
        metadata = dict(context.metadata)
        if (
            any(
                not isinstance(k, str) or not isinstance(v, str)
                for k, v in metadata.items()
            )
            or len(json.dumps(metadata).encode()) > 8192
        ):
            raise NativeMediaError("Native media metadata exceeds storage bounds")
        scope, conversation = await self._scope()
        uri = media_uri_for(data)
        identity = self._id(scope, uri)
        rows = await NativeMedia.find({"id": identity})
        if len(rows) > 1:
            raise NativeMediaError("Native media identity is ambiguous")
        if rows:
            node = rows[0]
        else:
            node, _ = await NativeMedia.create_if_absent(
                id=identity,
                scope=scope,
                uri=uri,
                payload=base64.b64encode(data).decode("ascii"),
                byte_count=len(data),
                media_metadata=metadata,
            )
        if node.scope != scope or node.uri != uri or self._bytes(node) != data:
            raise NativeMediaError("Native media identity is not immutable")
        edge = await conversation.connect(node, direction="out")
        await flush_deferred_entities(node, edge, conversation, strict=True)
        return uri

    async def get(self, uri: str, *, context: MediaContext = _EMPTY_CONTEXT) -> bytes:
        return self._bytes(await self._node(uri))

    async def exists(self, uri: str, *, context: MediaContext = _EMPTY_CONTEXT) -> bool:
        try:
            await self.get(uri, context=context)
        except NativeMediaMissing:
            return False
        return True

    async def public_url(
        self, uri: str, *, context: MediaContext = _EMPTY_CONTEXT
    ) -> None:
        await self._node(uri)
        return None

    async def get_metadata(
        self, uri: str, *, context: MediaContext = _EMPTY_CONTEXT
    ) -> Mapping[str, str]:
        return dict((await self._node(uri)).media_metadata)
