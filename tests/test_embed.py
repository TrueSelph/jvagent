"""Tests for the public :mod:`jvagent.embed` surface.

These tests assert the contract hosts depend on:

* ``jvagent.embed`` is importable and exports the documented names.
* ``embed.bootstrap()`` runs against an externally-provided jvspatial
  context (the autouse fixture's JsonDB) without requiring jvagent's
  CLI server.
* ``embed.bootstrap()`` is idempotent — calling twice does not raise.
* ``embed.shutdown()`` is safe to call when nothing was started.
"""

from __future__ import annotations

import pytest

import jvagent
from jvagent import embed


def test_embed_module_is_importable() -> None:
    """The embed surface is reachable as ``jvagent.embed``."""
    assert hasattr(jvagent, "embed")
    assert jvagent.embed is embed


def test_embed_public_api_surface() -> None:
    """The documented names are exported with __all__."""
    expected = {"bootstrap", "shutdown", "run_index_migration", "run_app_startup"}
    assert expected.issubset(set(embed.__all__))
    for name in expected:
        assert callable(getattr(embed, name))


@pytest.mark.asyncio
async def test_bootstrap_runs_against_host_context(tmp_path) -> None:
    """``bootstrap()`` runs end-to-end against the host's jvspatial DB.

    The autouse ``_ensure_default_graph_context`` fixture provides the
    jvspatial default context — bootstrap must not construct its own.
    """
    await embed.bootstrap(app_root=tmp_path, ensure_admin=False)

    # App node should be present after manual bootstrap (no app.yaml in tmp_path).
    from jvagent.core.app import App

    app = await App.get()
    assert app is not None


@pytest.mark.asyncio
async def test_bootstrap_is_idempotent(tmp_path) -> None:
    """Calling ``bootstrap()`` twice does not raise."""
    await embed.bootstrap(app_root=tmp_path, ensure_admin=False)
    await embed.bootstrap(app_root=tmp_path, ensure_admin=False)


@pytest.mark.asyncio
async def test_shutdown_is_safe_when_nothing_started() -> None:
    """``shutdown()`` is a no-op when no scheduler is running."""
    await embed.shutdown()


@pytest.mark.asyncio
async def test_run_index_migration_callable_standalone(tmp_path) -> None:
    """Hosts can run only the schema step without full bootstrap."""
    await embed.run_index_migration()


@pytest.mark.asyncio
async def test_interact_rejects_empty_utterance() -> None:
    """``interact()`` validates utterance before touching the runtime."""
    from jvspatial.api.exceptions import ValidationError

    with pytest.raises(ValidationError):
        await embed.interact(agent_id="any", utterance="   ")


@pytest.mark.asyncio
async def test_interact_rejects_missing_agent(tmp_path) -> None:
    """``interact()`` raises ResourceNotFoundError for unknown agent ids."""
    from jvspatial.api.exceptions import ResourceNotFoundError

    await embed.bootstrap(app_root=tmp_path, ensure_admin=False)

    with pytest.raises(ResourceNotFoundError):
        await embed.interact(
            agent_id="nope_does_not_exist",
            utterance="hello",
        )


@pytest.mark.asyncio
async def test_list_agents_returns_empty_before_install(tmp_path) -> None:
    """Fresh DB with no agents installed yields an empty list."""
    await embed.bootstrap(app_root=tmp_path, ensure_admin=False)
    agents = await embed.list_agents()
    assert isinstance(agents, list)
    # Manual bootstrap (no app.yaml) installs no agents.
    assert agents == []


@pytest.mark.asyncio
async def test_get_agent_id_by_name_returns_none_when_absent(tmp_path) -> None:
    """``get_agent_id_by_name`` returns None for an unknown name."""
    await embed.bootstrap(app_root=tmp_path, ensure_admin=False)
    assert await embed.get_agent_id_by_name("nope/missing") is None
    assert await embed.get_agent_id_by_name("missing", namespace="nope") is None


@pytest.mark.asyncio
async def test_get_agent_id_by_name_parses_dotted_form(tmp_path) -> None:
    """Dotted ``namespace/name`` parses without an explicit namespace kwarg."""
    # No agents installed — we just want to confirm the parse path doesn't
    # raise and the underlying query runs cleanly.
    await embed.bootstrap(app_root=tmp_path, ensure_admin=False)
    result = await embed.get_agent_id_by_name("ns/name")
    assert result is None  # No matching agent installed; None is the contract.


@pytest.mark.asyncio
async def test_interact_stream_rejects_empty_utterance() -> None:
    """``interact_stream()`` validates utterance before opening the generator."""
    from jvspatial.api.exceptions import ValidationError

    gen = embed.interact_stream(agent_id="any", utterance="   ")
    with pytest.raises(ValidationError):
        await gen.__anext__()


@pytest.mark.asyncio
async def test_interact_stream_emits_error_for_missing_agent(tmp_path) -> None:
    """Unknown agent yields a single terminal ``error`` envelope."""
    await embed.bootstrap(app_root=tmp_path, ensure_admin=False)
    envelopes = []
    async for env in embed.interact_stream(
        agent_id="nope_does_not_exist",
        utterance="hello",
    ):
        envelopes.append(env)
    assert len(envelopes) == 1
    assert envelopes[0]["type"] == "error"
    assert envelopes[0]["code"] == "agent_not_found"


# ---------------------------------------------------------------------------
# Identity & conversation lifecycle surface
# ---------------------------------------------------------------------------
#
# Construction of fully-formed Agent + Memory nodes in unit-test isolation
# is awkward (Agent has required pydantic fields the host normally supplies
# via app.yaml), so the happy-path tests here are minimal: they exercise
# the contract that missing-resource paths return clean negatives without
# raising. End-to-end multi-user isolation is covered by integral's smoke
# test against a fully bootstrapped agent.


@pytest.mark.asyncio
async def test_ensure_user_rejects_missing_agent(tmp_path) -> None:
    """``ensure_user`` raises ResourceNotFoundError for unknown agent ids."""
    from jvspatial.api.exceptions import ResourceNotFoundError

    await embed.bootstrap(app_root=tmp_path, ensure_admin=False)
    with pytest.raises(ResourceNotFoundError):
        await embed.ensure_user(agent_id="nope", user_id="any")


@pytest.mark.asyncio
async def test_list_user_conversations_rejects_missing_agent(tmp_path) -> None:
    """``list_user_conversations`` raises ResourceNotFoundError for unknown agent ids."""
    from jvspatial.api.exceptions import ResourceNotFoundError

    await embed.bootstrap(app_root=tmp_path, ensure_admin=False)
    with pytest.raises(ResourceNotFoundError):
        await embed.list_user_conversations(agent_id="nope", user_id="any")


@pytest.mark.asyncio
async def test_delete_conversation_rejects_missing_agent(tmp_path) -> None:
    """``delete_conversation`` raises ResourceNotFoundError for unknown agent ids."""
    from jvspatial.api.exceptions import ResourceNotFoundError

    await embed.bootstrap(app_root=tmp_path, ensure_admin=False)
    with pytest.raises(ResourceNotFoundError):
        await embed.delete_conversation(
            agent_id="nope", session_id="sess_does_not_exist"
        )


@pytest.mark.asyncio
async def test_purge_user_rejects_missing_agent(tmp_path) -> None:
    """``purge_user`` raises ResourceNotFoundError for unknown agent ids."""
    from jvspatial.api.exceptions import ResourceNotFoundError

    await embed.bootstrap(app_root=tmp_path, ensure_admin=False)
    with pytest.raises(ResourceNotFoundError):
        await embed.purge_user(agent_id="nope", user_id="any")
