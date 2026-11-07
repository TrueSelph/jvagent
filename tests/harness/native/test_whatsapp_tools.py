"""Real SDK dispatch around existing WhatsApp domain tools; no network calls."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from jvagent.action.whatsapp.whatsapp_action import WhatsAppAction
from jvagent.harness.mutation_tools import MutationOutcomeUnknown
from jvagent.harness.native import build_native_agent
from jvagent.harness.recovery import NativeHistoryError, history_for_new_turn
from jvagent.harness.step_store import InteractionStepStore
from jvagent.memory.conversation import Conversation
from jvagent.memory.interaction import Interaction
from jvagent.tooling.channel_identity import bind_verified_channel_sender
from jvagent.tooling.context import bind_dispatch_context


class Visitor(SimpleNamespace):
    """Weak-referenceable host fixture."""


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["template", "flow"])
@pytest.mark.parametrize(
    "failure",
    [
        "",
        "missing",
        "malformed",
        "refused",
        "timeout",
        "cancelled",
        "duplicate",
        "batch",
        "batch_unknown",
        "permission",
        "sender",
        "selector",
    ],
)
async def test_native_send_has_authority_receipt_and_no_blind_replay(
    tmp_path: Path, monkeypatch: Any, caplog: Any, kind: str, failure: str
) -> None:
    conversation = Conversation(user_id="15555550100", session_id="session")
    await conversation.save()
    interaction = Interaction(
        conversation_id=conversation.id,
        user_id=conversation.user_id,
        session_id="session",
        utterance="Send the approved item",
    )
    await interaction.save()
    interaction.native_run = {"run_id": "send-run", "status": "running"}
    host = Visitor(
        agent_id="agent",
        _agent=SimpleNamespace(id="agent"),
        user_id=conversation.user_id,
        session_id="session",
        channel="whatsapp",
        conversation=conversation,
        interaction=interaction,
        _native_execution=True,
    )
    if failure != "sender":
        bind_verified_channel_sender(
            host, agent_id="agent", sender=host.user_id, channel="whatsapp"
        )
    action = WhatsAppAction(
        provider="meta", template_allowlist=["signup"], flow_allowlist=["signin_signup"]
    )
    tools = await action.get_native_tools()
    name = f"whatsapp__send_{kind}"
    calls = []

    async def send(to: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(to)
        marked = [
            e
            for e in interaction.native_steps["effects"]
            if e.get("effect_summary") == "jv:mutation:whatsapp-send:v1"
        ]
        assert len(marked) == 1 and marked[0]["status"] == "started"
        assert marked[0]["idempotency_key"].startswith(
            f"jv:whatsapp-send:{interaction.id}:"
        )
        if failure == "cancelled":
            raise asyncio.CancelledError()
        if failure == "timeout":
            raise TimeoutError("private-provider-detail")
        if failure in {"missing", "batch_unknown"}:
            return {"ok": True}
        if failure == "malformed":
            return {
                "ok": True,
                "messages": [{"id": {"unsafe": "private-provider-detail"}}],
            }
        if failure == "refused":
            return {"ok": False, "error": "private-provider-detail"}
        return {"ok": True, "messages": [{"id": "wamid.synthetic.accepted"}]}

    async def api(_: Any) -> Any:
        return SimpleNamespace(send_template_message=send, send_flow_message=send)

    monkeypatch.setattr(WhatsAppAction, "api", api)
    monkeypatch.setattr(WhatsAppAction, "is_configured", lambda _: True)

    async def access(_: str) -> bool:
        return True

    async def tool_access(_: Any) -> bool:
        return failure != "permission"

    args = (
        {"template_name": "signup"}
        if kind == "template"
        else {"flow_name": "signin_signup"}
    )
    if failure == "selector":
        args = (
            {"template_name": "forbidden"}
            if kind == "template"
            else {"flow_name": "signin_signup", "flow_id": "forbidden"}
        )
    step = 0

    def respond(messages: Any, info: Any) -> ModelResponse:
        nonlocal step
        step += 1
        if step == 1 or (failure == "duplicate" and step == 2):
            effective = dict(args)
            if step == 2 and kind == "template":
                effective.update(
                    template_name=" signup ", language=" en_US ", components=[]
                )
            elif step == 2:
                effective.update(
                    flow_name=" signin_signup ",
                    flow_cta=" Open ",
                    body=" Please complete this form. ",
                    screen_data={},
                    flow_action="navigate",
                )
            parts = [ToolCallPart(name, effective, tool_call_id=f"call-{step}")]
            if failure in {"batch", "batch_unknown"}:
                other = dict(args)
                if failure == "batch_unknown":
                    if kind == "flow":
                        other["body"] = "different request"
                    else:
                        other["components"] = [
                            {
                                "type": "body",
                                "parameters": [
                                    {"type": "text", "text": "different request"}
                                ],
                            }
                        ]
                parts.append(ToolCallPart(name, other, tool_call_id="call-batch"))
            return ModelResponse(parts=parts)
        return ModelResponse(
            parts=[TextPart("Accepted by provider, not delivery confirmation.")]
        )

    skill = tmp_path / "whatsapp-send"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: whatsapp-send\ndescription: Send an approved item.\n"
        f"allowed-tools: [{name}]\n---\nUse the authorized send tool.\n"
    )
    store = InteractionStepStore(interaction)
    agent = build_native_agent(
        FunctionModel(respond),
        skill_directory=skill,
        tools=tools,
        access_check=access,
        tool_access_check=tool_access,
        instructions="Send",
        available_actions=["WhatsAppAction"],
        channel="whatsapp",
        step_store=store,
        conversation_id="session",
    )
    with bind_dispatch_context(host):
        if failure:
            expected = (
                PermissionError
                if failure in {"permission", "sender", "selector"}
                else (
                    asyncio.CancelledError
                    if failure == "cancelled"
                    else MutationOutcomeUnknown
                )
            )
            with pytest.raises(expected):
                await agent.run("Send", run_id="send-run", conversation_id="session")
        else:
            result = await agent.run(
                "Send", run_id="send-run", conversation_id="session"
            )
            assert "not delivery" in result.output
    assert calls == (
        [] if failure in {"permission", "sender", "selector"} else [host.user_id]
    )
    assert "private-provider-detail" not in caplog.text
    effect = await store.get_tool_effect(run_id="send-run", tool_call_id="call-1")
    if failure not in {"permission", "sender", "selector"}:
        assert effect.effect_summary == "jv:mutation:whatsapp-send:v1"
        assert effect.status == (
            "completed"
            if failure in {"", "duplicate", "batch"}
            else "started" if failure == "cancelled" else "failed"
        )
        if failure:
            interaction.native_run = {"run_id": "send-run", "status": "failed"}
            with pytest.raises(
                NativeHistoryError, match="mutation requires reconciliation"
            ):
                await history_for_new_turn(
                    interaction,
                    conversation_id=conversation.id,
                    user_id=host.user_id,
                    session_id="session",
                )


@pytest.mark.asyncio
async def test_native_tool_names_schemas_and_effect_metadata_are_explicit() -> None:
    action = WhatsAppAction()
    originals = {t.name: t for t in await action.get_tools()}
    native = await action.get_native_tools()
    assert {t.name for t in native} == originals.keys()
    for tool in native:
        assert tool.parameters_schema == originals[tool.name].parameters_schema
        assert tool.binds_visitor and tool.requires_tool_permission
        assert tool.effect_class == ("write" if "__send_" in tool.name else "read")


@pytest.mark.parametrize("value", [None, "", " ", "x\ny", {}, 3, "x" * 513])
def test_missing_or_malformed_provider_ids_are_not_coerced(value: Any) -> None:
    from jvagent.action.whatsapp.utils.message_receipts import acknowledged_message_id

    with pytest.raises(ValueError):
        acknowledged_message_id({"messages": [{"id": value}]})
