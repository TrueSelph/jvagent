"""HC-08: native / embedded / remote share invocation + revocation."""

from __future__ import annotations

import pytest

from jvagent.harness.contracts import SnapshotSelector
from tests.conformance.fixtures.fake_host import (
    FAKE_HOST_ID,
    fake_caller,
    fake_host_runtime,
    provider_for,
)

pytestmark = pytest.mark.harness_conformance

PROVIDERS = ("native", "embedded", "remote")


@pytest.mark.parametrize("transport", PROVIDERS)
def test_fake_host_fixture_is_not_a_product_host(transport):
    assert FAKE_HOST_ID == "fake-host"
    assert transport in PROVIDERS


@pytest.mark.parametrize("transport", PROVIDERS)
@pytest.mark.asyncio
async def test_provider_revocation_takes_effect_on_next_snapshot(transport):
    rt = fake_host_runtime()
    caller = fake_caller()
    provider = provider_for(transport, rt)
    first = await provider.resolve_snapshot(caller)
    assert "host_lookup" in first.host_tool_names
    invoked = await provider.invoke(
        first.snapshot_id, "inv-1", "host_lookup", {"q": "x"}
    )
    assert invoked.ok
    rt.revoke_host_tool(caller.session_id, "host_lookup")
    await provider.invalidate(SnapshotSelector(snapshot_id=first.snapshot_id))
    second = await provider.resolve_snapshot(caller)
    assert "host_lookup" not in second.host_tool_names
    assert first.snapshot_id != second.snapshot_id
    skill = await provider.load_skill(second.snapshot_id, "host_skill")
    assert skill.skill_key == "host_skill"
    assert "host_lookup" in skill.body
    encode = getattr(provider, "encode_caller", None)
    if callable(encode):
        blob = encode(caller)
        assert "workspace_id" not in blob
