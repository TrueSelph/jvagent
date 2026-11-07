"""Host-owned file binding for the official SDK workspace, not a file store."""

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jvagent.core.sandbox import local_user_storage, resolve_user_sandbox_relpath
from jvagent.memory.lock_manager import get_user_lock_manager

_MARKER = "_jv_workspace_binding.txt"
_MAX_FILESYSTEM_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class NativeScriptFiles:
    """Reuse the existing caller's local files on a host-bounded filesystem.

    The mount comes only from authenticated graph scope and the file service.
    Containers see this user's directory, never the application or parent root.
    Filesystem quota/durability belong to the storage deployment, not a second
    storage or synchronization engine in the harness.
    """

    root: Path
    facts: dict[str, Any]
    marker: bytes

    @classmethod
    async def admit(
        cls, fi: Any, *, agent_id: str, user_id: str, image: str
    ) -> "NativeScriptFiles":
        if os.name != "posix" or os.getuid() == 0:
            raise ValueError("Native file workspaces require a non-root POSIX host")
        if resolve_user_sandbox_relpath(agent_id, user_id) != f"{agent_id}/{user_id}":
            raise ValueError(
                "Script file identities require unambiguous storage segments"
            )
        scoped = local_user_storage(fi, agent_id, user_id, "", create_root=True)
        root = scoped.root_dir
        if any(char in str(root) for char in (",", "\n", "\r", '"')):
            raise ValueError("Storage path cannot be represented as a Docker mount")
        stat = root.stat()
        capacity = os.statvfs(root)
        total = capacity.f_blocks * capacity.f_frsize
        if total <= 0 or total > _MAX_FILESYSTEM_BYTES:
            raise ValueError(
                "Script files require a host-bounded filesystem of at most 64 MiB"
            )
        if stat.st_uid != os.getuid():
            raise ValueError("Script files must belong to the non-root server account")
        facts = {
            "kind": "local-user-bind-v1",
            "root": str(root),
            "device": stat.st_dev,
            "inode": stat.st_ino,
            "uid": os.getuid(),
            "gid": os.getgid(),
            "filesystem_bytes": total,
        }
        marker = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    {
                        "storage": facts,
                        "agent_id": agent_id,
                        "user_id": user_id,
                        "image": image,
                    },
                    sort_keys=True,
                ).encode()
            ).hexdigest()
        ).encode()
        lock = await get_user_lock_manager().acquire("native-script-files:" + str(root))
        async with lock:
            existing = await scoped.read_file(_MARKER)
            if existing is None:
                await scoped.save_file(_MARKER, marker)
            elif existing != marker:
                raise ValueError(
                    "Workspace binding changed; explicit storage migration is required"
                )
        return cls(root, facts, marker)

    @property
    def mount_argument(self) -> str:
        return f"--mount=type=bind,src={self.root},dst=/workspace"

    async def stage(self, workspace: Any, assets: dict[str, bytes]) -> None:
        lock = await get_user_lock_manager().acquire(
            "native-script-files:" + str(self.root)
        )
        async with lock:
            current = self.root.stat()
            if self.root.resolve() != self.root or (current.st_dev, current.st_ino) != (
                self.facts["device"],
                self.facts["inode"],
            ):
                raise ValueError("The admitted user file root changed")
            for name in (_MARKER, *assets):
                target = self.root / name
                if not target.is_relative_to(self.root) or target.resolve() != target:
                    raise ValueError(
                        "Workspace binding and staged scripts cannot be symbolic links"
                    )
            marker_stat = await workspace.stat(_MARKER)
            if (
                marker_stat.size != len(self.marker)
                or await workspace.read_bytes(_MARKER) != self.marker
            ):
                raise ValueError(
                    "SDK workspace is not bound to the admitted user's files"
                )
            for name, content in assets.items():
                try:
                    info = await workspace.stat(name)
                    if info.size != len(content):
                        raise ValueError(
                            "Staged script changed; explicit asset migration is required"
                        )
                    existing = await workspace.read_bytes(name)
                except FileNotFoundError:
                    await workspace.write_bytes(name, content)
                else:
                    if existing != content:
                        raise ValueError(
                            "Staged script changed; explicit asset migration is required"
                        )
