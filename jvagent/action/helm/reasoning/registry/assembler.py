"""Tool registry assembly for ``ReasoningHelm``.

**C-2 STUB.** Returns an empty :class:`ToolRegistry`. At C-3 this module is
replaced with a duplicate of ``jvagent/action/cockpit/registry/assembler.py``
that wires harness service tools (memory, response, task, conversation,
skill, artifact, search, clock) via ``_build_*`` helpers under
``jvagent/action/helm/reasoning/tools/``. C-5 adds skill loading. C-6 adds
delivery filtering.

The function name :func:`assemble_cockpit_tools` is preserved so the
duplicated engine's import statement diffs cleanly against its cockpit
ancestor.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from jvagent.tooling.tool_registry import ToolRegistry

if TYPE_CHECKING:
    from jvagent.action.helm.reasoning.context import CockpitContext

logger = logging.getLogger(__name__)


async def assemble_cockpit_tools(ctx: "CockpitContext") -> ToolRegistry:
    """C-2 stub: return an empty registry.

    At C-3 this is replaced with the full assembler. Until then the engine
    runs LM calls with zero tools available — useful for bare-loop tests
    and for the C-2 commit being self-contained.
    """
    logger.debug(
        "reasoning.assembler: C-2 stub invoked; returning empty ToolRegistry "
        "(C-3 will populate harness + action + skill tools)"
    )
    return ToolRegistry()
