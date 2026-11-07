"""Reviewed script assets and policy around official Docker/Shell capabilities."""

import asyncio
import hashlib
import json
import math
from itertools import islice
from pathlib import Path
from typing import Any, Sequence

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

SCRIPT_TOOL = "code_execution__bash"


class NativeScriptSandbox(BaseModel):
    """Host configuration, never a model-selected image or host filesystem mount.

    The SDK owns container creation, workspace references, command execution and
    shell output. JV copies bounded authored scripts and preserves the existing
    Action tool name. No legacy executor or staging runtime is used.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    image: str = Field(pattern=r"^(?:[^\s]+@)?sha256:[0-9a-f]{64}$")
    timeout: float = Field(default=60, gt=0, le=120, allow_inf_nan=False)
    max_output_chars: int = Field(default=64_000, ge=256, le=64_000, strict=True)
    _asset_snapshot: dict[str, bytes] | None = PrivateAttr(default=None)
    _asset_directories: tuple[Path, ...] | None = PrivateAttr(default=None)
    _files: Any = PrivateAttr(default=None)
    _session_id: str | None = PrivateAttr(default=None)

    @classmethod
    def from_action_context(cls, context: dict[str, Any]) -> "NativeScriptSandbox":
        """One admission rule shared by runtime and offline configuration review."""
        if context.get("network", False) is not False or context.get(
            "sandbox_root", ""
        ):
            raise ValueError("Native scripts require isolated, network-free storage")
        defaults = {
            "memory_mb": 2048,
            "cpu_seconds": 30,
            "max_procs": 0,
            "max_file_mb": 256,
        }
        if any(context.get(key, value) != value for key, value in defaults.items()):
            raise ValueError(
                "Custom legacy resource settings require explicit native migration"
            )
        return cls(
            image=context.get("native_image", ""),
            timeout=context.get("timeout", 60),
            max_output_chars=context.get("max_output_bytes", 64_000),
        )

    def check_arguments(self, arguments: dict[str, Any]) -> None:
        """The SDK accepts a timeout override; host policy supplies its ceiling."""
        value = arguments.get("timeout_seconds")
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 < value <= self.timeout
        ):
            raise ValueError("Script timeout exceeds host allowance")

    def assets(self, directories: Sequence[Path]) -> dict[str, bytes]:
        """Snapshot reviewed scripts; refuse symlinks, special files and excess."""
        roots = tuple(path.resolve(strict=True) for path in directories)
        if len(roots) > 32:
            raise ValueError("Script skill count exceeds host allowance")
        if self._asset_snapshot is not None:
            if roots != self._asset_directories:
                raise ValueError("Script asset scope changed")
            return dict(self._asset_snapshot)
        assets: dict[str, bytes] = {}
        total = 0
        for directory in roots:
            scripts = directory / "scripts"
            if scripts.is_symlink():
                raise ValueError("Script assets cannot contain symlinks")
            if not scripts.exists():
                continue
            if not scripts.is_dir():
                raise ValueError("Script directory must be an ordinary directory")
            paths = list(islice(scripts.rglob("*"), 257))
            if len(paths) > 256:
                raise ValueError("Script directory entries exceed host allowance")
            for path in sorted(paths):
                if path.is_symlink():
                    raise ValueError("Script assets cannot contain symlinks")
                if path.is_dir():
                    continue
                if not path.is_file() or path.stat().st_size > 256_000:
                    raise ValueError("Script asset is not a bounded ordinary file")
                content = path.read_bytes()
                total += len(content)
                if len(content) > 256_000 or total > 4_000_000 or len(assets) >= 128:
                    raise ValueError("Script assets exceed host allowance")
                relative = path.relative_to(directory).as_posix()
                assets[f"staged_skills/{directory.name}/{relative}"] = content
        self._asset_snapshot = assets
        self._asset_directories = roots
        return dict(assets)

    def capabilities(self, assets: dict[str, bytes]) -> list[Any]:
        if self._files is None:
            raise ValueError("Scripts require an admitted user file storage binding")
        from pydantic_ai.capabilities import Hooks
        from pydantic_ai_harness import DockerSandbox, Shell

        class CompatibleShell(Shell[Any]):
            def get_toolset(self) -> Any:
                return super().get_toolset().renamed({SCRIPT_TOOL: "run_command"})

        stage_lock = asyncio.Lock()
        staged_runs: set[str] = set()

        async def stage(ctx: Any, *, call: Any, tool_def: Any, args: Any) -> Any:
            if call.tool_name != SCRIPT_TOOL:
                return args
            async with stage_lock:
                if ctx.run_id not in staged_runs:
                    if ctx.conversation_id != self._session_id:
                        raise ValueError("Script workspace belongs to another session")
                    if ctx.workspace is None:
                        raise ValueError(
                            "Scripts require the official Docker workspace"
                        )
                    # Verify the provider before copying any bytes. Stage once
                    # before SDK-dispatched parallel commands can read scripts.
                    await ctx.workspace.resolve(".")
                    ref = ctx.workspace.ref
                    if ref is None or ref.provider != "docker":
                        raise ValueError("Scripts cannot execute in a host workspace")
                    await self._files.stage(ctx.workspace, assets)
                    staged_runs.add(ctx.run_id)
            return args

        return [
            DockerSandbox(
                image=self.image,
                allow_internet_access=False,
                env={"HOME": "/tmp"},
                docker_args=(
                    "--read-only",
                    "--cap-drop=ALL",
                    "--security-opt=no-new-privileges",
                    "--memory=512m",
                    "--cpus=1",
                    "--pids-limit=128",
                    # Explicit mount mode survives Docker restart; the SDK's
                    # shorthand tmpfs otherwise resets its PID directory to 0755.
                    "--mount=type=tmpfs,dst=/run/pydantic-ai,tmpfs-mode=1777",
                    f"--user={self._files.facts['uid']}:{self._files.facts['gid']}",
                    self._files.mount_argument,
                    "--tmpfs=/tmp:rw,nosuid,nodev,size=16m,mode=1777",
                ),
            ),
            CompatibleShell(
                tools=["run_command"],
                default_timeout=self.timeout,
                max_output_chars=self.max_output_chars,
                max_file_bytes=8_000_000,
                env={},
                persist_cwd=False,
            ),
            Hooks(before_tool_execute=stage),
        ]

    async def bind_policy(
        self,
        conversation: Any,
        *,
        agent_id: str,
        user_id: str,
        session_id: str,
        lock: Any,
        assets: dict[str, bytes],
        file_interface: Any = None,
    ) -> None:
        """An existing SDK workspace cannot silently adopt changed host policy."""
        from jvspatial import flush_deferred_entities

        from jvagent.memory.conversation import Conversation

        policy = {
            "version": "sdk-docker-user-files-v2",
            "agent_id": agent_id,
            "user_id": user_id,
            "session_id": session_id,
            "configuration": self.model_dump(mode="json"),
            "assets": {
                name: "sha256:" + hashlib.sha256(content).hexdigest()
                for name, content in assets.items()
            },
        }
        # Check canonical serialization before graph mutation.
        json.dumps(policy, allow_nan=False)
        async with lock:
            rows = await Conversation.find({"id": conversation.id})
            if (
                len(rows) != 1
                or rows[0].user_id != user_id
                or rows[0].session_id != session_id
            ):
                raise ValueError("Script workspace belongs to another caller")
            owner = await rows[0].get_agent()
            if owner is None or owner.id != agent_id:
                raise ValueError("Script workspace belongs to another agent")
            prior = rows[0].native_script_policy
            if not isinstance(prior, dict) or (
                prior
                and (
                    set(prior) != set(policy) | {"storage"}
                    or {k: v for k, v in prior.items() if k != "storage"} != policy
                )
            ):
                raise ValueError(
                    "Script workspace policy changed; explicit migration or a new conversation is required"
                )
            if file_interface is None:
                from jvagent.core.app import App

                app = await App.get()
                if app is None or not app.file_storage_enabled:
                    raise ValueError("Scripts require enabled application file storage")
                file_interface = await app.get_file_interface()
            from jvagent.harness.script_files import NativeScriptFiles

            files = await NativeScriptFiles.admit(
                file_interface, agent_id=agent_id, user_id=user_id, image=self.image
            )
            policy["storage"] = files.facts
            if prior and prior != policy:
                raise ValueError(
                    "Script storage identity changed; explicit migration is required"
                )
            object.__setattr__(conversation, "native_script_policy", policy)
            await conversation.save()
            await flush_deferred_entities(conversation, strict=True)
            self._files = files
            self._session_id = session_id
