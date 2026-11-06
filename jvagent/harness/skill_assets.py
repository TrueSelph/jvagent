"""Strict JV SOP inheritance projected into the official Skills workspace.

This translates declarations and text only. Skills retains catalog validation,
capability loading and execution. No files are rewritten and no scripts run.
"""

import re
from pathlib import Path
from typing import Any, Mapping

import yaml

_MAX_DEPTH = 12
_MAX_DOCUMENT_BYTES = 256_000
_LIST_FIELDS = (
    "allowed-tools",
    "disabled-tools",
    "requires-tools",
    "requires-actions",
    "allowed-channels",
    "denied-channels",
)
_UNQUALIFIED = (
    "task-lock",
    "task_lock",
    "requires-tasks",
    "requires_tasks",
    "access-action",
    "allowed-groups",
    "denied-groups",
)


def resolve_skill_directory(configured: str) -> Path:
    """Resolve declarative paths against the app root, independent of process cwd.

    Existing explicitly absolute paths remain supported. Relative paths cannot
    escape the app root, including through symlinks.
    """
    if not configured.strip():
        raise ValueError("Skill directory must be explicit")
    if configured.startswith("library:"):
        return library_skill_directory(configured.removeprefix("library:"))
    directory = Path(configured)
    if directory.is_absolute():
        return directory.resolve(strict=True)
    from jvagent.core.app_context import get_app_root

    root = Path(get_app_root()).resolve(strict=True)
    directory = (root / directory).resolve(strict=True)
    if not directory.is_relative_to(root):
        raise ValueError("Relative skill directory escapes the app root")
    return directory


def library_skill_directory(name: str) -> Path:
    """Resolve a portable package asset reference, never an arbitrary path."""
    from importlib.resources import files

    if not name or Path(name).name != name or name in {".", ".."} or "\\" in name:
        raise ValueError("Library skill must name one installed directory")
    root = Path(str(files("jvagent.skills"))).resolve(strict=True)
    directory = (root / name).resolve(strict=True)
    asset = (directory / "SKILL.md").resolve(strict=True)
    if directory.parent != root or asset.parent != directory or not asset.is_file():
        raise ValueError("Library skill escapes or is missing from the package")
    return directory


def project_skill(
    directory: Path, *, action_bases: Mapping[str, Path] | None = None
) -> tuple[dict[str, Any], str]:
    """Compose a bounded chain; action bases must be explicitly supplied by host.

    Skill references are siblings under the configured skill root. Permission
    declarations are additive, disabled tools subtract, channel allowlists
    intersect, denials accumulate, and unsupported inherited policy cannot be
    erased by a child. Supporting inheritance does not qualify task execution.
    """
    directory = directory.resolve(strict=True)
    root = directory.parent
    bases = action_bases or {}

    def read(path: Path, stack: tuple[Path, ...]) -> tuple[dict[str, Any], str]:
        path = path.resolve(strict=True)
        if path in stack or len(stack) >= _MAX_DEPTH:
            raise ValueError("Skill inheritance cycle or depth limit")
        raw = (path / "SKILL.md").read_bytes()
        if len(raw) > _MAX_DOCUMENT_BYTES:
            raise ValueError("Skill document exceeds inheritance size limit")
        text = raw.decode("utf-8")
        sections = text.split("---", 2)
        if not text.startswith("---\n") or len(sections) != 3:
            raise ValueError("Skill requires YAML frontmatter")
        own = yaml.safe_load(sections[1])
        if not isinstance(own, dict) or not isinstance(own.get("name"), str):
            raise ValueError("Skill requires a name")
        if own.get("spec", "jv") != "jv":
            raise ValueError("Inherited skill specification requires an adapter")
        for key in _UNQUALIFIED:
            if own.get(key):
                raise ValueError(
                    f"Inherited policy requires an unqualified adapter: {key}"
                )
        for key in _LIST_FIELDS:
            value = own.get(key, [])
            if not isinstance(value, list) or any(
                not isinstance(item, str) or not item.strip() for item in value
            ):
                raise ValueError(f"{key} must be a list of nonempty names")
        extends = own.pop("extends", None)
        if extends is None:
            return own, sections[2].strip()
        if not isinstance(extends, str):
            raise ValueError("extends must name one skill or action base")
        kind, separator, target = extends.partition(":")
        if not separator:
            raise ValueError("extends requires a skill: or action: reference")
        if kind == "skill" and re.fullmatch(r"[A-Za-z0-9_-]+", target):
            parent = (root / target).resolve(strict=True)
            if parent.parent != root:
                raise ValueError("Skill base escapes the configured skill root")
        elif kind == "action" and target in bases:
            parent = bases[target]
        else:
            raise ValueError("Skill base is missing or not explicitly admitted")
        inherited, body = read(parent, (*stack, path))
        if kind == "skill" and inherited["name"] != target:
            raise ValueError("Skill base name must match its directory")
        contract = inherited.get("output-contract")
        if contract and own.get("output-contract", contract) != contract:
            raise ValueError("A child cannot replace an inherited output contract")
        merged = {**inherited, **own}
        for key in _LIST_FIELDS:
            left, right = inherited.get(key, []), own.get(key, [])
            if key == "allowed-channels" and left and right:
                value = [item for item in left if item in right]
                if not value:
                    raise ValueError("Inherited channel allowlists do not intersect")
            else:
                value = list(dict.fromkeys([*left, *right]))
            if value:
                merged[key] = value
        return merged, "\n\n".join(part for part in (body, sections[2].strip()) if part)

    metadata, body = read(directory, ())
    disabled = metadata.get("disabled-tools", [])
    if disabled:
        metadata["allowed-tools"] = [
            name for name in metadata.get("allowed-tools", []) if name not in disabled
        ]
    text = "---\n" + yaml.safe_dump(metadata, sort_keys=False) + "---\n" + body + "\n"
    return metadata, text


def projected_workspace(directory: Path, document: str) -> Any:
    """Read-only public workspace facade replacing only the selected SKILL.md."""
    from pydantic_ai.workspaces import (
        LocalWorkspaceBackend,
        ReadOnlyWorkspace,
        Workspace,
    )

    directory = directory.resolve(strict=True)
    selected = directory / "SKILL.md"

    class SkillWorkspace(ReadOnlyWorkspace):
        async def read_bytes(self, path: str) -> bytes:
            resolved = Path(await self.resolve(path))
            if resolved == selected:
                return document.encode("utf-8")
            return await super().read_bytes(path)

    return SkillWorkspace(Workspace(LocalWorkspaceBackend(directory.parent)))
