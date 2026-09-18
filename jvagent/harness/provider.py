"""HostCapabilityProvider adapters (HP-08). Native / embedded / remote share types."""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Optional

from jvagent.harness.contracts import (
    HarnessContractError,
    NativeCaller,
    SkillMaterialization,
    SnapshotSelector,
    ToolResult,
    ToolSurfaceSnapshot,
    native_caller_from_mapping,
    reject_model_authority_fields,
)
from jvagent.harness.runtime import HarnessRuntime, get_runtime


class LocalHostProvider:
    """In-process reference provider. Host tools/skills are per session_id."""

    def __init__(self, runtime: Optional[HarnessRuntime] = None) -> None:
        self.runtime = runtime or get_runtime()

    async def resolve_snapshot(self, caller: NativeCaller) -> ToolSurfaceSnapshot:
        return self.runtime.admit_snapshot(caller, force_new=True)

    async def invoke(
        self,
        snapshot_id: str,
        invocation_id: str,
        tool_name: str,
        payload: Mapping[str, Any],
    ) -> ToolResult:
        reject_model_authority_fields(payload)
        snap = self.runtime.require_usable(snapshot_id)
        if tool_name in snap.host_tool_names:
            runner = self.runtime.host_runner(snap.caller.session_id, tool_name)
            if runner is None:
                raise HarnessContractError(
                    f"no runner registered for host tool {tool_name!r}"
                )
            result = runner(dict(payload))
            if hasattr(result, "__await__"):
                result = await result  # type: ignore[misc]
            return ToolResult(
                invocation_id=invocation_id,
                ok=True,
                payload=(
                    dict(result) if isinstance(result, Mapping) else {"result": result}
                ),
            )
        if (
            tool_name not in snap.native_tool_names
            and tool_name not in snap.host_tool_names
        ):
            raise HarnessContractError(
                f"tool {tool_name!r} not on snapshot {snapshot_id}"
            )
        raise HarnessContractError(
            f"native tool {tool_name!r} is dispatched by wrap_action_tool, not HostCapabilityProvider"
        )

    async def load_skill(
        self, snapshot_id: str, skill_key: str
    ) -> SkillMaterialization:
        snap = self.runtime.require_usable(snapshot_id)
        if (
            skill_key not in snap.host_skill_keys
            and skill_key not in snap.native_skill_keys
        ):
            raise HarnessContractError(
                f"skill {skill_key!r} not on snapshot {snapshot_id}"
            )
        if skill_key in snap.host_skill_keys:
            materialization = self.runtime.host_skill_materialization(
                snap.caller.session_id, skill_key
            )
            if materialization is None:
                raise HarnessContractError(
                    f"host skill {skill_key!r} has no registered materialization"
                )
            return materialization
        digest = f"digest-{skill_key}"
        return SkillMaterialization(
            skill_key=skill_key,
            digest=digest,
            spec="jv",
            body=f"# {skill_key}\n",
        )

    async def invalidate(self, selector: SnapshotSelector) -> None:
        self.runtime.invalidate(selector)


class EmbeddedHostAdapter(LocalHostProvider):
    """Embedded transport: identical types, in-process."""


class RemoteHostAdapter:
    """Remote transport: JSON wire of the same types. No host-domain fields."""

    def __init__(self, inner: Optional[LocalHostProvider] = None) -> None:
        self.inner = inner or LocalHostProvider()

    @staticmethod
    def encode_caller(caller: NativeCaller) -> str:
        blob = json.dumps(caller.to_mapping(), sort_keys=True)
        parsed = json.loads(blob)
        native_caller_from_mapping(parsed)
        return blob

    @staticmethod
    def decode_caller(blob: str) -> NativeCaller:
        return native_caller_from_mapping(json.loads(blob))

    async def resolve_snapshot(self, caller: NativeCaller) -> ToolSurfaceSnapshot:
        wire = self.encode_caller(caller)
        return await self.inner.resolve_snapshot(self.decode_caller(wire))

    async def invoke(
        self,
        snapshot_id: str,
        invocation_id: str,
        tool_name: str,
        payload: Mapping[str, Any],
    ) -> ToolResult:
        encoded = json.dumps(dict(payload), sort_keys=True)
        decoded: Dict[str, Any] = json.loads(encoded)
        return await self.inner.invoke(snapshot_id, invocation_id, tool_name, decoded)

    async def load_skill(
        self, snapshot_id: str, skill_key: str
    ) -> SkillMaterialization:
        return await self.inner.load_skill(snapshot_id, skill_key)

    async def invalidate(self, selector: SnapshotSelector) -> None:
        await self.inner.invalidate(selector)


def provider_for(transport: str, runtime: Optional[HarnessRuntime] = None) -> Any:
    rt = runtime or get_runtime()
    local = LocalHostProvider(rt)
    if transport == "native":
        return local
    if transport == "embedded":
        return EmbeddedHostAdapter(rt)
    if transport == "remote":
        return RemoteHostAdapter(local)
    raise HarnessContractError(f"unknown provider transport {transport!r}")


__all__ = [
    "EmbeddedHostAdapter",
    "LocalHostProvider",
    "RemoteHostAdapter",
    "provider_for",
]
