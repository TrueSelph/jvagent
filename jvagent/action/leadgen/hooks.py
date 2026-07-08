"""Hook loading and dispatch for LeadGenAction skill extensions."""

from __future__ import annotations

import importlib.util
import inspect
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .spec import FieldDef, LeadGenSpec

logger = logging.getLogger(__name__)

_module_cache: Dict[str, Any] = {}


@dataclass
class HookExecutionContext:
    spec: LeadGenSpec
    record: Any
    profile_data: Dict[str, Any]
    fields: Dict[str, Any]
    visitor: Any = None
    user: Any = None
    field_def: Optional[FieldDef] = None
    args: Dict[str, Any] = field(default_factory=dict)
    messages: List[str] = field(default_factory=list)
    blocked: bool = False
    block_reason: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def say(self, message: str) -> None:
        if message and message.strip():
            self.messages.append(message.strip())

    def block_sync(self, reason: str = "") -> None:
        self.blocked = True
        self.block_reason = reason


def clear_module_cache() -> None:
    _module_cache.clear()


def _load_module(spec: LeadGenSpec) -> Optional[Any]:
    if not spec.source_dir:
        return None
    cache_key = spec.source_dir
    if cache_key in _module_cache:
        return _module_cache[cache_key]

    module_path = os.path.join(spec.source_dir, "scripts", "custom_tools.py")
    if not os.path.isfile(module_path):
        return None

    mod_name = f"leadgen_hooks_{spec.name}"
    spec_obj = importlib.util.spec_from_file_location(mod_name, module_path)
    if spec_obj is None or spec_obj.loader is None:
        return None
    module = importlib.util.module_from_spec(spec_obj)
    spec_obj.loader.exec_module(module)
    _module_cache[cache_key] = module
    return module


def load_hook_function(spec: LeadGenSpec, name: str) -> Optional[Callable]:
    if not name:
        return None
    module = _load_module(spec)
    if module is None:
        return None
    fn = getattr(module, name, None)
    if fn is None or not callable(fn):
        return None
    return fn


async def call_hook(
    spec: LeadGenSpec,
    hook_name: str,
    ctx: HookExecutionContext,
) -> HookExecutionContext:
    fn = load_hook_function(spec, hook_name)
    if fn is None:
        return ctx
    try:
        if inspect.iscoroutinefunction(fn):
            result = await fn(ctx)
        else:
            result = fn(ctx)
        if isinstance(result, HookExecutionContext):
            return result
    except Exception as exc:
        logger.warning("leadgen hook %s failed: %s", hook_name, exc)
    return ctx
