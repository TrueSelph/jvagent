"""Native-library composition for one explicitly selected JV skill.

Development entrypoint only: callers establish identity and provide an access
checker. This module does not import the legacy Orchestrator or pilot engine.
"""

from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

import yaml

from jvagent.harness.contracts import reject_model_authority_fields
from jvagent.tooling.tool import Tool as JVTool

AccessCheck = Callable[[str], Awaitable[bool]]


def build_native_agent(
    model: Any,
    *,
    skill_directory: Path,
    tools: Sequence[JVTool],
    access_check: AccessCheck,
    instructions: str,
    step_store: Any = None,
) -> Any:
    """Compose official Skills and native tools without a custom execution loop.

    Only reviewed read tools are admitted in this first increment. Writes, flows
    and visitor-bound tools require separate compatibility qualification.
    """
    from pydantic_ai import Agent, Tool
    from pydantic_ai.capabilities import LocalWorkspace
    from pydantic_ai.exceptions import ModelRetry
    from pydantic_ai_harness import Skills

    directory = skill_directory.resolve(strict=True)
    text = (directory / "SKILL.md").read_text()
    sections = text.split("---", 2)
    if not text.startswith("---\n") or len(sections) != 3:
        raise ValueError("Skill requires YAML frontmatter")
    metadata = yaml.safe_load(sections[1])
    if not isinstance(metadata, dict) or not isinstance(metadata.get("name"), str):
        raise ValueError("Skill requires a name")
    if metadata.get("task_lock") or metadata.get("requires_tasks"):
        raise ValueError("Flow skills are not yet qualified")
    allowed = metadata.get("allowed-tools", [])
    if not isinstance(allowed, list) or any(not isinstance(v, str) for v in allowed):
        raise ValueError("allowed-tools must be a list of exact tool names")
    if any("*" in name or "?" in name for name in allowed):
        raise ValueError("Wildcard tool declarations require migration")
    if len({tool.name for tool in tools}) != len(tools):
        raise ValueError("Duplicate Action tool names")

    def adapt(original: JVTool) -> Any:
        async def invoke(**arguments: Any) -> str:
            reject_model_authority_fields(arguments)
            # Bind the checked name in the closure, never in model arguments.
            if not await access_check(original.access_label or original.name):
                raise ModelRetry("Action access denied")
            result = await original.call(**arguments)
            return str(result.content)

        return Tool.from_schema(
            invoke,
            name=original.name,
            description=original.description,
            json_schema=original.parameters_schema,
        )

    admitted = []
    for tool in tools:
        if tool.name not in allowed:
            continue
        if tool.effect_class != "read" or tool.binds_visitor:
            raise ValueError(
                f"Action is not qualified for native execution: {tool.name}"
            )
        admitted.append(adapt(tool))

    capabilities = [
        LocalWorkspace(directory.parent, read_only=True),
        Skills(".", include=[directory.name]),
    ]
    if step_store is not None:
        from pydantic_ai_harness import StepPersistence

        capabilities.append(StepPersistence(store=step_store))
    return Agent(
        model,
        instructions=instructions,
        tools=admitted,
        capabilities=capabilities,
        name="jvagent-native",
    )


async def run_native_agent(
    agent: Any,
    question: str,
    *,
    conversation_id: str,
    run_id: str,
    message_history: Sequence[Any] = (),
    event_stream_handler: Any = None,
    max_requests: int = 20,
    max_tool_calls: int = 40,
    max_tokens: int = 100_000,
    timeout_seconds: float = 180.0,
) -> Any:
    """Run through native lifecycle, limits and events, returning the full result.

    The host must persist typed messages and usage from this result and reconcile
    delivery separately. No partial text is substituted for a failed native run.
    """
    import math

    from pydantic_ai.usage import UsageLimits

    if not conversation_id.strip() or not run_id.strip():
        raise ValueError("Host-established conversation and run IDs are required")
    for limit in (max_requests, max_tool_calls, max_tokens):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("Native run limits must be positive integers")
    if (
        isinstance(timeout_seconds, bool)
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise ValueError("Native timeout must be finite and positive")
    return await agent.run(
        question,
        conversation_id=conversation_id,
        run_id=run_id,
        message_history=message_history,
        event_stream_handler=event_stream_handler,
        usage_limits=UsageLimits(
            request_limit=max_requests,
            tool_calls_limit=max_tool_calls,
            total_tokens_limit=max_tokens,
        ),
        timeout=timeout_seconds,
    )
