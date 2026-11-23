"""SDK-owned replies retain meaning without implicit legacy disclosure rules."""

import pytest

from jvagent.action.parameters import INTERNALS_DEFLECTION, reply_core_parameters
from jvagent.action.reply import ReplyAction
from jvagent.action.response.response_bus import ResponseBus
from jvagent.memory.interaction import Interaction

TEXT = "The tool mcp_records__misleading_write is not available."


def turn(*, native: bool = True) -> Interaction:
    interaction = Interaction(
        user_id="caller", session_id="session", conversation_id="conversation"
    )
    if native:
        interaction.native_run = {"run_id": "run", "status": "pending_delivery"}
        interaction.native_steps = {
            "runs": [
                {
                    "run_id": "run",
                    "registration_id": "registration",
                    "conversation_id": "session",
                    "metadata": {
                        "jv_agent_id": "agent",
                        "jv_interaction_id": interaction.id,
                        "jv_conversation_id": "conversation",
                        "jv_user_id": "caller",
                        "jv_session_id": "session",
                    },
                }
            ]
        }
    return interaction


@pytest.mark.asyncio
@pytest.mark.parametrize("with_bus", [False, True, "stream"])
async def test_native_output_survives_both_egress_routes(with_bus: bool | str) -> None:
    from types import SimpleNamespace

    interaction = turn()
    await interaction.save()
    if with_bus:
        message = await ResponseBus().publish(
            session_id="session",
            user_id="caller",
            interaction_id=interaction.id,
            interaction=interaction,
            channel="default",
            content=TEXT,
            stream=with_bus == "stream",
        )
        if with_bus == "stream":
            assert message.message_type == "final" and message.content == ""
        else:
            assert message.content == TEXT
    else:
        assert await ReplyAction().publish(
            TEXT, visitor=SimpleNamespace(interaction=interaction)
        )
    assert interaction.response == TEXT


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "legacy",
        "bare-marker",
        "foreign-user",
        "foreign-session",
        "foreign-interaction",
        "foreign-conversation",
        "unregistered",
        "foreign-sdk-session",
        "foreign-run",
        "missing-agent",
        "duplicate",
    ],
)
async def test_unbound_claim_does_not_disable_legacy_policy(failure: str) -> None:
    interaction = turn(native=failure != "legacy")
    if failure == "bare-marker":
        interaction.native_steps = {}
    elif failure == "unregistered":
        interaction.native_steps["runs"][0].pop("registration_id")
    elif failure == "foreign-sdk-session":
        interaction.native_steps["runs"][0]["conversation_id"] = "foreign"
    elif failure == "foreign-run":
        interaction.native_steps["runs"][0]["run_id"] = "foreign"
    elif failure == "missing-agent":
        interaction.native_steps["runs"][0]["metadata"].pop("jv_agent_id")
    elif failure == "duplicate":
        interaction.native_steps["runs"].append(
            dict(interaction.native_steps["runs"][0])
        )
    elif failure.startswith("foreign-"):
        key = {
            "foreign-user": "jv_user_id",
            "foreign-session": "jv_session_id",
            "foreign-interaction": "jv_interaction_id",
            "foreign-conversation": "jv_conversation_id",
        }[failure]
        interaction.native_steps["runs"][0]["metadata"][key] = "foreign"
    await interaction.save()
    message = await ResponseBus().publish(
        session_id="session",
        user_id="caller",
        interaction_id=interaction.id,
        interaction=interaction,
        channel="default",
        content=TEXT,
        metadata={"native_harness": True},
    )
    assert message.content == INTERNALS_DEFLECTION


@pytest.mark.asyncio
async def test_explicit_response_policy_still_applies_to_native_output() -> None:
    interaction = turn()
    interaction.parameters = reply_core_parameters()
    await interaction.save()
    message = await ResponseBus().publish(
        session_id="session",
        user_id="caller",
        interaction_id=interaction.id,
        interaction=interaction,
        channel="default",
        content=TEXT,
    )
    assert message.content == INTERNALS_DEFLECTION


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", [{"private": "value"}, ["private-value"]])
async def test_malformed_native_policy_cannot_silently_disable_rules(
    policy: object,
) -> None:
    interaction = turn()
    object.__setattr__(interaction, "parameters", policy)
    with pytest.raises(ValueError, match="Invalid native response policy") as caught:
        await ResponseBus().publish(
            session_id="session",
            user_id="caller",
            interaction_id=interaction.id,
            interaction=interaction,
            channel="default",
            content=TEXT,
        )
    assert "private" not in str(caught.value)
    assert not interaction.response
