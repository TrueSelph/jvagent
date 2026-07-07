"""LeadGenAction — conversational lead capture with auto-sync."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action

from . import engine
from .hooks import clear_module_cache, load_hook_function
from .spec import LeadGenRegistry, LeadGenSpec
from .tools import build_tools

logger = logging.getLogger(__name__)


class LeadGenAction(Action):
    """Opportunistic lead capture + MCP sync for orchestrator agents."""

    description: str = (
        "Lead generation action: capture lead fields conversationally and "
        "sync to external systems via MCP when configured."
    )
    binds_tools_to_visitor: bool = True

    default_fields: Dict[str, Dict[str, Any]] = attribute(
        default_factory=dict,
        description=(
            "Action-level field defaults when no skill spec is loaded. "
            "Each key is a field name; value may include required, guidance, aliases."
        ),
    )
    sync_destinations: List[Dict[str, Any]] = attribute(
        default_factory=list,
        description=(
            "Action-level MCP sync destinations (used when skill has none). "
            "Each entry: {server, mode, tool, arguments}."
        ),
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._registry = LeadGenRegistry()

    async def on_register(self):
        await super().on_register()
        await self._discover_specs()

    async def on_reload(self):
        await super().on_reload()
        clear_module_cache()
        skills_dirs = await self.resolve_skill_scan_dirs()
        if skills_dirs:
            self._registry.reload(skills_dirs)

    async def on_startup(self):
        await super().on_startup()
        if not self._registry.specs:
            await self._discover_specs()

    async def _discover_specs(self) -> None:
        skills_dirs = await self.resolve_skill_scan_dirs()
        if skills_dirs:
            self._registry.discover(skills_dirs)
            logger.info(
                "LeadGenAction discovered specs: %s", list(self._registry.specs.keys())
            )

    async def get_tools(self) -> List[Any]:
        if not self._registry.specs:
            await self._discover_specs()
        return build_tools(self)

    async def _handle_capture(self, **kwargs: Any) -> str:
        return await engine.handle_capture(self, **kwargs)

    async def _handle_retrieve(self, **kwargs: Any) -> str:
        return await engine.handle_retrieve(self, **kwargs)

    async def _handle_status(self, **kwargs: Any) -> str:
        return await engine.handle_status(self, **kwargs)

    async def _handle_sync(self, **kwargs: Any) -> str:
        return await engine.handle_sync(self, **kwargs)

    async def _handle_custom_tool(
        self, tdef: Any, spec: LeadGenSpec, **kwargs: Any
    ) -> str:
        from .hooks import HookExecutionContext, call_hook

        visitor = kwargs.pop("visitor", None)
        user, interaction = await engine.get_user_and_interaction(visitor)
        if not user:
            return json.dumps({"error": "no user found"})

        from .store import LeadRecord

        record = await LeadRecord.get_or_create_for_user(user)
        fn = load_hook_function(spec, tdef.function or tdef.name)
        if fn is None:
            return json.dumps({"error": f"hook {tdef.function} not found"})

        ctx = HookExecutionContext(
            spec=spec,
            record=record,
            profile_data=record.get_yaml() or {},
            fields=kwargs,
            visitor=visitor,
            user=user,
            args=kwargs,
        )
        import inspect

        if inspect.iscoroutinefunction(fn):
            result = await fn(ctx)
        else:
            result = fn(ctx)
        if isinstance(result, HookExecutionContext):
            return json.dumps(
                {"ok": True, "messages": result.messages, "extra": result.extra}
            )
        return json.dumps({"ok": True, "result": str(result)})
