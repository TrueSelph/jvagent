"""Disposable native worker used by abrupt-exit storage tests."""

import asyncio
import os
import sys
from pathlib import Path

from jvspatial.core.context import GraphContext, set_default_context
from jvspatial.db.jsondb import JsonDB
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from pydantic_ai_harness import StepPersistence

from jvagent.harness.step_store import InteractionStepStore
from jvagent.memory.interaction import Interaction


async def main() -> None:
    """Persist library records and exit without Python shutdown cleanup."""
    root = Path(sys.argv[1])
    mode = sys.argv[2]
    os.environ["JVSPATIAL_ENABLE_DEFERRED_SAVES"] = "false"
    set_default_context(GraphContext(database=JsonDB(base_path=str(root / "graph"))))
    interaction = Interaction(utterance="worker test")
    await interaction.save()
    (root / "interaction-id").write_text(interaction.id)
    agent = Agent(
        TestModel(call_tools=["crash"] if mode == "tool" else []),
        capabilities=[StepPersistence(store=InteractionStepStore(interaction))],
    )

    @agent.tool_plain
    async def crash() -> str:
        """Simulate abrupt worker death while a tool is executing."""
        os._exit(17)

    await agent.run("Worker test", run_id="process-run", conversation_id="session")
    os._exit(17)


if __name__ == "__main__":
    asyncio.run(main())
