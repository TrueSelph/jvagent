"""Official media walking backed by scoped immutable object-spatial payloads."""

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.messages import (
    BinaryContent,
    ModelMessagesTypeAdapter,
    ModelRequest,
    UserPromptPart,
)
from pydantic_ai_harness.media import MediaContext, MediaStore, media_uri_for
from pydantic_ai_harness.step_persistence import ContinuableSnapshot, RunRecord

from jvagent.harness.media_store import GraphMediaStore
from jvagent.harness.step_store import InteractionStepStore
from jvagent.memory.conversation import Conversation
from jvagent.memory.interaction import Interaction
from jvagent.memory.native_media import NativeMedia


async def host(
    *, register: bool = True
) -> tuple[Conversation, Interaction, InteractionStepStore]:
    conversation = Conversation(user_id="caller", session_id="session")
    await conversation.save()
    interaction = Interaction(
        conversation_id=conversation.id, user_id="caller", session_id="session"
    )
    await interaction.save()
    store = InteractionStepStore(interaction, agent_id="agent")
    if register:
        await store.register_run(
            RunRecord(
                run_id=interaction.id,
                conversation_id="session",
                metadata=store.registration_metadata(),
            )
        )
    from jvspatial import flush_deferred_entities

    await flush_deferred_entities(conversation, interaction, strict=True)
    return conversation, interaction, store


@pytest.mark.asyncio
async def test_graph_media_dedup_scope_integrity_and_conversation_cascade() -> None:
    conversation, interaction, store = await host()
    media = GraphMediaStore(interaction, agent_id="agent")
    assert isinstance(media, MediaStore)
    data = b"\x89PNG\r\n" + b"a" * 70000
    uri = await media.put(data, context=MediaContext(metadata={"purpose": "fixture"}))
    assert await media.put(data) == uri == media_uri_for(data)
    assert await media.get(uri) == data and await media.exists(uri)
    assert await media.public_url(uri) is None
    assert await media.get_metadata(uri) == {"purpose": "fixture"}
    assert len(await NativeMedia.find({})) == 1
    other, foreign, foreign_store = await host()
    assert not await GraphMediaStore(foreign, agent_id="agent").exists(uri)
    with pytest.raises(FileNotFoundError):
        await GraphMediaStore(foreign).get(uri)
    await conversation.delete()
    assert not await NativeMedia.find({})
    assert await Conversation.get(other.id) is not None


@pytest.mark.asyncio
async def test_offload_and_restore_exact_bytes_without_rewriting_old_snapshot() -> None:
    conversation, interaction, store = await host()
    data = b"\x89PNG\r\n" + b"a" * 70000
    snapshot = ContinuableSnapshot(
        run_id=interaction.id,
        step_index=1,
        conversation_id="session",
        messages=[
            ModelRequest(
                parts=[
                    UserPromptPart([BinaryContent(data=data, media_type="image/png")])
                ]
            )
        ],
    )
    await store.save_snapshot(snapshot)
    raw = interaction.native_steps["snapshots"][0]
    assert raw["jv_media_version"] == 1
    assert len(str(raw)) < 2000
    assert len(await NativeMedia.find({})) == 1
    assert ModelMessagesTypeAdapter.dump_python(
        (await store.latest_snapshot(run_id=interaction.id)).messages, mode="json"
    ) == ModelMessagesTypeAdapter.dump_python(snapshot.messages, mode="json")
    assert ModelMessagesTypeAdapter.dump_python(
        (await store.first_snapshot(run_id=interaction.id)).messages, mode="json"
    ) == ModelMessagesTypeAdapter.dump_python(snapshot.messages, mode="json")
    await store.save_snapshot(snapshot)
    assert len(await NativeMedia.find({})) == 1
    inline = raw.copy()
    from pydantic import TypeAdapter

    inline = TypeAdapter(ContinuableSnapshot).dump_python(snapshot, mode="json")
    interaction.native_steps = {
        "runs": interaction.native_steps["runs"],
        "snapshots": [inline],
    }
    assert ModelMessagesTypeAdapter.dump_python(
        (await store.latest_snapshot(run_id=interaction.id)).messages, mode="json"
    ) == ModelMessagesTypeAdapter.dump_python(snapshot.messages, mode="json")
    assert interaction.native_steps["snapshots"][0] == inline


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper",
    [
        "user",
        "session",
        "conversation",
        "agent",
        "registration",
        "payload",
        "count",
        "node_scope",
        "missing",
    ],
)
async def test_media_corruption_and_scope_changes_refuse(tamper: str) -> None:
    conversation, interaction, store = await host()
    media = GraphMediaStore(interaction, agent_id="agent")
    uri = await media.put(b"private-fixture")
    node = (await NativeMedia.find({}))[0]
    if tamper in {"user", "session", "conversation"}:
        object.__setattr__(interaction, tamper + "_id", "foreign")
    elif tamper == "agent":
        media.agent_id = "foreign"
    elif tamper == "registration":
        interaction.native_steps["runs"][0]["metadata"]["jv_user_id"] = "foreign"
    elif tamper == "payload":
        object.__setattr__(node, "payload", "ZGFtYWdl")
        await node.save()
    elif tamper == "count":
        object.__setattr__(node, "byte_count", 100)
        await node.save()
    elif tamper == "node_scope":
        object.__setattr__(node, "scope", {})
        await node.save()
    elif tamper == "missing":
        await node.delete()
    with pytest.raises((ValueError, FileNotFoundError)) as caught:
        await media.get(uri)
    assert "private-fixture" not in str(caught.value)


@pytest.mark.asyncio
async def test_write_failure_cannot_publish_a_dangling_snapshot(
    monkeypatch: Any,
) -> None:
    conversation, interaction, store = await host()
    monkeypatch.setattr(
        NativeMedia,
        "create_if_absent",
        AsyncMock(side_effect=OSError("private storage failure")),
    )
    snapshot = ContinuableSnapshot(
        run_id=interaction.id,
        step_index=1,
        messages=[
            ModelRequest(
                parts=[
                    UserPromptPart(
                        [BinaryContent(data=b"x" * 70000, media_type="image/png")]
                    )
                ]
            )
        ],
    )
    with pytest.raises(OSError):
        await store.save_snapshot(snapshot)
    assert not interaction.native_steps.get("snapshots")


@pytest.mark.asyncio
async def test_long_text_and_reserved_markers_roundtrip_through_public_sdk() -> None:
    from pydantic_ai.messages import TextContent, ToolReturnPart

    conversation, interaction, store = await host()
    literal = {"__harness_external_media__": True, "uri": media_uri_for(b"foreign")}
    snapshot = ContinuableSnapshot(
        run_id=interaction.id,
        step_index=1,
        messages=[
            ModelRequest(
                parts=[
                    UserPromptPart([TextContent("héllo " * 15000)]),
                    ToolReturnPart("lookup", literal, "call"),
                ]
            )
        ],
    )
    await store.save_snapshot(snapshot)
    assert len(str(interaction.native_steps["snapshots"][0])) < 3000
    restored = await store.latest_snapshot(run_id=interaction.id)
    assert restored.messages[0].parts[0].content[0].content == "héllo " * 15000
    assert restored.messages[0].parts[1].content == literal
    assert len(await NativeMedia.find({})) == 1


@pytest.mark.asyncio
async def test_missing_media_refuses_history_until_original_payload_is_restored() -> (
    None
):
    from pydantic_ai.messages import ModelResponse, TextPart

    from jvagent.harness.recovery import NativeHistoryError, history_for_new_turn

    conversation, interaction, store = await host()
    data = b"\x89PNG\r\n" + b"a" * 70000
    snapshot = ContinuableSnapshot(
        run_id=interaction.id,
        conversation_id="session",
        step_index=1,
        messages=[
            ModelRequest(
                parts=[
                    UserPromptPart([BinaryContent(data=data, media_type="image/png")])
                ]
            ),
            ModelResponse(parts=[TextPart("Read")]),
        ],
    )
    await store.save_snapshot(snapshot)
    interaction.native_run = {"run_id": interaction.id, "status": "delivered"}
    await interaction.save()
    original = interaction.native_steps.copy()
    await (await NativeMedia.find({}))[0].delete()
    with pytest.raises(NativeHistoryError, match="media history"):
        await history_for_new_turn(
            interaction,
            conversation_id=conversation.id,
            user_id="caller",
            session_id="session",
        )
    assert interaction.native_steps == original
    await GraphMediaStore(interaction, agent_id="agent").put(data)
    history = await history_for_new_turn(
        interaction,
        conversation_id=conversation.id,
        user_id="caller",
        session_id="session",
    )
    assert history.messages[0].parts[0].content[0].data == data
    assert interaction.native_steps == original


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, 0, 2, True, "1"])
async def test_unknown_snapshot_media_version_refuses(version: Any) -> None:
    conversation, interaction, store = await host()
    interaction.native_steps["snapshots"] = [
        {
            "run_id": interaction.id,
            "state": "complete",
            "jv_media_version": version,
            "messages": [],
        }
    ]
    with pytest.raises(ValueError, match="version"):
        await store.latest_snapshot(run_id=interaction.id)


@pytest.mark.asyncio
async def test_sdk_large_media_end_to_end(tmp_path: Path) -> None:
    import json

    from pydantic_ai.messages import ModelResponse, TextContent, TextPart
    from pydantic_ai.models.function import FunctionModel

    from jvagent.harness.native import build_native_agent, run_native_agent
    from jvagent.harness.recovery import history_for_new_turn

    conversation, interaction, store = await host(register=False)
    skill = tmp_path / "media"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: media\ndescription: Inspect uploads\n---\nRead supplied files.\n"
    )
    binary = b"\x89PNG\r\n" + b"a" * 70000
    text = "JUNIPER-28\n" + "context " * 10000

    def model(messages: Any, info: Any) -> ModelResponse:
        content = messages[0].parts[0].content
        assert content[0].data == binary and content[1].content == text
        return ModelResponse(parts=[TextPart("inspected")])

    async def allow(name: str) -> bool:
        return True

    agent = build_native_agent(
        FunctionModel(model),
        skill_directory=skill,
        tools=[],
        access_check=allow,
        instructions="Read files.",
        step_store=store,
    )
    result = await run_native_agent(
        agent,
        [BinaryContent(data=binary, media_type="image/png"), TextContent(text)],
        conversation_id="session",
        run_id=interaction.id,
    )
    assert result.output == "inspected"
    interaction.native_run = {"status": "delivered", "run_id": interaction.id}
    await interaction.save()
    assert len(await NativeMedia.find({})) == 2
    assert len(json.dumps(interaction.native_steps["snapshots"]).encode()) < 10000
    history = await history_for_new_turn(
        interaction,
        conversation_id=conversation.id,
        user_id="caller",
        session_id="session",
    )
    assert history.messages[0].parts[0].content[0].data == binary
    assert history.messages[0].parts[0].content[1].content == text


@pytest.mark.asyncio
async def test_warm_entity_cache_cannot_hide_physical_media_removal() -> None:
    conversation, interaction, store = await host()
    media = GraphMediaStore(interaction, agent_id="agent")
    uri = await media.put(b"cached-fixture")
    node = (await NativeMedia.find({}))[0]
    assert await NativeMedia.get(node.id) is not None
    from jvspatial.core.context import get_default_context

    await get_default_context().database.delete("node", node.id)
    with pytest.raises(FileNotFoundError):
        await media.get(uri)


@pytest.mark.asyncio
async def test_media_checkpoint_failure_precedes_any_model_request(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from unittest.mock import Mock

    from pydantic_ai.models.function import FunctionModel

    request = AsyncMock(side_effect=AssertionError("Model request must not start"))
    stream = Mock(side_effect=AssertionError("Model stream must not start"))
    monkeypatch.setattr(FunctionModel, "request", request)
    monkeypatch.setattr(FunctionModel, "request_stream", stream)
    monkeypatch.setattr(
        NativeMedia,
        "create_if_absent",
        AsyncMock(side_effect=OSError("media unavailable")),
    )
    with pytest.raises(OSError, match="media unavailable"):
        await test_sdk_large_media_end_to_end(tmp_path)
    request.assert_not_called()
    stream.assert_not_called()


@pytest.mark.parametrize("payload", [[], {}, None, 123, b"private-bytes"])
def test_fast_hydration_cannot_bypass_payload_type_check(payload: Any) -> None:
    from jvagent.harness.media_store import NativeMediaError

    node = NativeMedia.model_construct(
        id="n.NativeMedia.fixture",
        payload=payload,
        byte_count=0,
        uri="media+sha256://" + "0" * 64,
    )
    with pytest.raises(NativeMediaError, match="storage bounds"):
        GraphMediaStore._bytes(node)
