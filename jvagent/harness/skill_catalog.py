"""JV declarations and policy around the official Skills catalog, never a router."""

import contextlib
import contextvars
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from jvagent.harness.skill_admission import admit_skill
from jvagent.harness.skill_assets import project_skill, projected_workspace
from jvagent.skills.output_policies import SUPPORTED_OUTPUT_CONTRACTS, output_policy


class NativeSkillCatalog:
    """Keep host permission facts separate from library-owned skill activation.

    A primary skill preserves the explicitly selected single-skill contract.
    Otherwise the official deferred catalog selects skills. Loading instructions
    never replaces host access checks. Research remains required for this run
    once invoked in this run, even if compaction later drops its activation.
    Library activation survives across turns; output obligations do not.
    """

    def __init__(
        self,
        *,
        primary: Path | None,
        directories: Sequence[Path],
        action_bases: Mapping[str, Path] | None,
        available_actions: Sequence[str],
        available_tools: Sequence[str],
        channel: str,
    ) -> None:
        from pydantic_ai_harness import Skills

        if (primary is not None) == bool(directories):
            raise ValueError("Configure one primary skill or an explicit skill catalog")
        paths = [primary] if primary is not None else list(directories)
        if len(paths) > 32:
            raise ValueError("At most 32 skills may be configured")
        self.directories: list[Path] = []
        self.metadata: dict[str, dict[str, Any]] = {}
        self.libraries: list[Any] = []
        self.tool_owners: dict[str, set[str]] = {}
        self.contract_owners: set[str] = set()
        self.primary: str | None = None
        for path in paths:
            assert path is not None
            directory = path.resolve(strict=True)
            metadata, document = project_skill(directory, action_bases=action_bases)
            metadata = admit_skill(
                directory,
                metadata=metadata,
                available_actions=available_actions,
                available_tools=available_tools,
                channel=channel,
                supported_output_contracts=SUPPORTED_OUTPUT_CONTRACTS,
            )
            name = metadata["name"]
            if name != directory.name:
                raise ValueError(
                    "Skill name must match its directory for official Skills loading"
                )
            if name in self.metadata:
                raise ValueError("Duplicate configured skill names")
            self.directories.append(directory)
            self.metadata[name] = metadata
            self.libraries.append(
                Skills(
                    ".",
                    include=[name],
                    workspace=projected_workspace(directory, document),
                )
            )
            for tool in metadata.get("allowed-tools", []):
                self.tool_owners.setdefault(tool, set()).add(name)
            if metadata.get("output-contract"):
                self.contract_owners.add(name)
            if primary is not None:
                self.primary = name
        self.allowed_names = frozenset(self.tool_owners)
        # evidence_required is currently the only admitted output contract.
        self.policy = (
            output_policy("evidence_required") if self.contract_owners else None
        )
        self._required: contextvars.ContextVar[dict[str, bool] | None] = (
            contextvars.ContextVar(
                "native_skill_output_contract_required", default=None
            )
        )

    async def preflight(self) -> None:
        """Validate every configured member with the official loader before spend."""
        for library in self.libraries:
            catalog = await library.load()
            if len(catalog.skills) != 1 or not catalog.skills[0].model_invocable:
                raise ValueError(
                    "Configured skill is not model-invocable: "
                    + "; ".join(item.message for item in catalog.skipped)
                )

    def allows_tool(self, ctx: Any, name: str) -> bool:
        return bool(self.tool_owners.get(name, set()) & ctx.active_capability_ids)

    def requires_output_contract(self) -> bool:
        if self.policy is None:
            return False
        state = self._required.get()
        if state is None:
            raise RuntimeError("Skill output policy requires an active run")
        return state["required"]

    def note_tool(self, ctx: Any, name: str, arguments: dict[str, Any]) -> None:
        """Bind evidence obligations to this run's invocation, not old history.

        The SDK intentionally retains loaded capabilities between runs and has
        no unload operation. A new research load or a call to an active research
        tool engages its output contract; a later greeting does not. This policy
        observes public hook inputs and never rewrites the SDK's loading state.
        """
        if self.policy is None:
            return
        loading = (
            name == "load_capability" and arguments.get("id") in self.contract_owners
        )
        using = (
            self.tool_owners.get(name, set())
            & self.contract_owners
            & ctx.active_capability_ids
        )
        if loading or using:
            state = self._required.get()
            if state is None:
                raise RuntimeError("Skill output policy requires an active run")
            state["required"] = True

    @contextlib.contextmanager
    def run_scope(self) -> Iterator[None]:
        token = self._required.set({"required": self.primary in self.contract_owners})
        try:
            with contextlib.ExitStack() as stack:
                if self.policy is not None:
                    stack.enter_context(self.policy.run_scope())
                yield
        finally:
            self._required.reset(token)
