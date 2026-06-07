"""Load and invoke custom_tools.py hook functions."""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import logging
import os
from typing import Any, Callable, Dict, Optional

from ..core.interview_loader import InterviewSpec
from ..core.session import InterviewSession
from ..core.validators import ExtractionStatus

logger = logging.getLogger(__name__)

_module_cache: Dict[str, Any] = {}


def _cache_key(spec: InterviewSpec) -> str:
    return f"{spec.name}:{spec.source_dir}"


def load_hook_function(spec: InterviewSpec, function_name: str) -> Optional[Callable]:
    """Load a named function from the skill's custom_tools.py."""
    key = _cache_key(spec)
    module = _module_cache.get(key)
    if module is None:
        custom_tools_path = os.path.join(spec.source_dir, "scripts", "custom_tools.py")
        if not os.path.isfile(custom_tools_path):
            custom_tools_path = os.path.join(spec.source_dir, "custom_tools.py")
        if not os.path.isfile(custom_tools_path):
            return None
        try:
            from ..core.decorators import interview_tool as _it

            mod_name = f"interview_custom_tools_{spec.name}"
            loader_spec = importlib.util.spec_from_file_location(
                mod_name, custom_tools_path
            )
            if not loader_spec or not loader_spec.loader:
                return None
            module = importlib.util.module_from_spec(loader_spec)
            module.__dict__["interview_tool"] = _it
            module.__dict__["ExtractionStatus"] = ExtractionStatus
            module.__dict__["InterviewSession"] = InterviewSession
            loader_spec.loader.exec_module(module)
            _module_cache[key] = module
        except Exception as e:
            logger.error(
                "Failed to load custom_tools from %s: %s", custom_tools_path, e
            )
            return None

    func = getattr(module, function_name, None)
    if func and callable(func):
        return func
    return None


def clear_module_cache() -> None:
    _module_cache.clear()


async def call_hook(
    func: Callable,
    *,
    session: Optional[InterviewSession] = None,
    spec: Optional[InterviewSpec] = None,
    visitor: Any = None,
    interview_action: Any = None,
    value: Optional[str] = None,
    kwargs: Optional[dict] = None,
) -> Any:
    """Invoke a hook with signature-filtered kwargs."""
    call_kwargs: Dict[str, Any] = {
        "session": session,
        "visitor": visitor,
        "interview_action": interview_action,
        "config": spec,
        "extracted_values": session.get_collected_summary() if session else {},
    }
    if value is not None:
        call_kwargs["value"] = value
    if kwargs and isinstance(kwargs, dict):
        call_kwargs.update(kwargs)

    try:
        sig_params = set(inspect.signature(func).parameters.keys())
        if sig_params:
            call_kwargs = {k: v for k, v in call_kwargs.items() if k in sig_params}
    except (ValueError, TypeError):
        pass

    result = func(**call_kwargs)
    if asyncio.iscoroutine(result):
        result = await result
    return result
