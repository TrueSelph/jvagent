"""Resolve declarative skill selections for offline native migration."""

import asyncio
from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml


def _inventory(root: Path) -> dict[str, tuple[Path, dict[str, Any]]]:
    """Retain authored-name precedence without legacy best-effort fallbacks."""
    if not root.exists():
        return {}
    root = root.resolve(strict=True)
    entries = list(root.iterdir())
    if len(entries) > 256:
        raise ValueError("Skill inventory exceeds migration bounds")
    result: dict[str, tuple[Path, dict[str, Any]]] = {}
    for entry in sorted(entries):
        asset = entry / "SKILL.md"
        if not asset.is_file():
            continue
        directory = entry.resolve(strict=True)
        if directory.parent != root or not asset.resolve(strict=True).is_relative_to(
            root
        ):
            raise ValueError("Skill inventory escapes its declared source")
        with asset.open("rb") as stream:
            raw = stream.read(256_001)
        if len(raw) > 256_000:
            raise ValueError("Skill inventory document exceeds migration bounds")
        text = raw.decode("utf-8-sig")
        sections = text.split("---", 2)
        if not text.startswith("---\n") or len(sections) != 3:
            raise ValueError("Skill inventory needs explicit frontmatter migration")
        metadata = yaml.safe_load(sections[1])
        if not isinstance(metadata, dict) or not isinstance(metadata.get("name"), str):
            raise ValueError("Skill inventory requires explicit names")
        name = metadata["name"]
        if name in result:
            raise ValueError("Skill inventory has ambiguous authored names")
        result[name] = (directory, metadata)
    return result


def migrate_skill_selection(
    root: Path,
    agent_directory: Path,
    context: dict[str, Any],
    selection: str | Sequence[str] | None,
    report: dict[str, Any],
) -> dict[str, Any]:
    """Return native declarations; all loader checks are offline and model-free.

    The public Skills loader owns format validation. This does not qualify Action
    prerequisites, scripts, policy inheritance or stored task/state migration.
    """
    blockers = report["blockers"]
    mode = context.get("skill_runtime", "legacy")
    declared = context.get("skills") if mode == "legacy" else None
    chosen = (
        selection
        if selection is not None
        else (
            context.get("pilot_skill", "research")
            if mode == "capability_pilot"
            else declared
        )
    )
    chosen = [chosen] if isinstance(chosen, str) else chosen
    if (
        not isinstance(chosen, Sequence)
        or not 1 <= len(chosen) <= 32
        or any(
            not isinstance(name, str)
            or not name
            or name in {".", "..", "-all"}
            or Path(name).name != name
            or "\\" in name
            or any(c in name for c in "*?[]")
            for name in chosen
        )
    ):
        blockers.append("Select a finite catalog of 1 to 32 installed skills")
        return {}
    if len(set(chosen)) != len(chosen):
        blockers.append("Duplicate selected skills require explicit migration")
        return {}
    if declared is not None:
        declared = [declared] if isinstance(declared, str) else declared
        if (
            not isinstance(declared, list)
            or any(not isinstance(n, str) for n in declared)
            or set(chosen) != set(declared)
        ):
            blockers.append("Selection must preserve the configured finite skill set")
            return {}
    if mode == "capability_pilot" and list(chosen) != [
        context.get("pilot_skill", "research")
    ]:
        blockers.append("Selected skill differs from the configured pilot skill")
        return {}
    source = context.get(
        "skills_source",
        "both" if declared is not None or mode == "capability_pilot" else "app",
    )
    if source not in ("app", "library", "both"):
        blockers.append("Skill source requires an explicit app/library/both mapping")
        return {}
    try:
        # App declarations override library declarations by authored name, as
        # before; folder aliases cannot silently expose a different base skill.
        app_root = agent_directory / "skills"
        if app_root.exists() and not app_root.resolve().is_relative_to(agent_directory):
            raise ValueError("App skill root escapes the agent directory")
        app = _inventory(app_root) if source in ("app", "both") else {}
        library = (
            _inventory(Path(str(files("jvagent.skills"))))
            if source in ("library", "both")
            else {}
        )
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise ValueError("Offline planning requires the synchronous CLI context")
        from pydantic_ai_harness import Skills

        from jvagent.harness.skill_assets import project_skill, projected_workspace

        async def validate(directory: Path) -> None:
            _, document = project_skill(directory)
            catalog = await Skills(
                ".",
                include=[directory.name],
                workspace=projected_workspace(directory, document),
            ).load()
            if len(catalog.skills) != 1 or not catalog.skills[0].model_invocable:
                raise ValueError("Selected skill requires official format migration")

        refs = []
        for index, name in enumerate(chosen, 1):
            member = app.get(name) or library.get(name)
            if member is None:
                blockers.append(
                    f"Selected skill {index} needs asset or Action-skill migration"
                )
                continue
            directory, metadata = member
            try:
                if name != directory.name or metadata.get("spec", "jv") not in (
                    "jv",
                    "claude",
                ):
                    raise ValueError("Selected skill needs name/spec migration")
                if metadata.get("spec") == "claude" and (
                    not isinstance(metadata.get("requires-actions"), list)
                    or not isinstance(metadata.get("requires-tools"), list)
                    or "CodeExecutionAction" not in metadata.get("requires-actions", [])
                    or "code_execution__bash" not in metadata.get("requires-tools", [])
                ):
                    raise ValueError(
                        "Script skill needs explicit execution prerequisites"
                    )
                # Native JV projection also requires canonical, non-BOM frontmatter.
                with (directory / "SKILL.md").open("rb") as stream:
                    if stream.read(4) != b"---\n":
                        raise ValueError("Selected skill needs frontmatter migration")
                asyncio.run(validate(directory))
            except (ValueError, RuntimeError):
                blockers.append(
                    f"Selected skill {index} needs native format/name/spec migration"
                )
                continue
            refs.append(
                str(directory.relative_to(root))
                if name in app
                else "library:" + directory.name
            )
    except (
        OSError,
        ValueError,
        ImportError,
        yaml.YAMLError,
        RecursionError,
        UnicodeError,
    ):
        blockers.append(
            "Skill inventory or native loader requires explicit migration and native-harness dependencies"
        )
        return {}
    report["selected_skill_count"] = len(refs)
    report["skill_format_preflight"] = (
        "JV policy projection and official Skills loader; runtime tools/Action prerequisites still require qualification"
    )
    if not refs or len(refs) != len(chosen):
        return {}
    catalog_mode = declared is not None or len(refs) > 1
    field = "skill_directories" if catalog_mode else "skill_directory"
    mapped_keys = (
        ("skills", "skills_source")
        if mode == "legacy"
        else ("skills_source", "pilot_skill")
    )
    for key in mapped_keys:
        if key in context:
            report["mappings"][key] = field
    return {field: refs if catalog_mode else refs[0]}
