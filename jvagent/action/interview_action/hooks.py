"""Hook loading and dispatch — custom_tools.py functions and validators.

Skills implement validators, pre/post processors, handlers, and skill tools as
functions in ``scripts/custom_tools.py``. The frontmatter references them by
name; this module loads and invokes them with signature-filtered kwargs.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import logging
import os
from typing import Any, Callable, Dict, Optional

from .session import InterviewSession
from .spec import FieldDef, InterviewSpec
from .validators import get_validator

logger = logging.getLogger(__name__)

_module_cache: Dict[str, Any] = {}


def load_hook_function(spec: InterviewSpec, function_name: str) -> Optional[Callable]:
    """Load a named function from the skill's scripts/custom_tools.py."""
    key = f"{spec.name}:{spec.source_dir}"
    module = _module_cache.get(key)
    if module is None:
        custom_tools_path = os.path.join(spec.source_dir, "scripts", "custom_tools.py")
        if not os.path.isfile(custom_tools_path):
            return None
        try:
            loader_spec = importlib.util.spec_from_file_location(
                f"interview_custom_tools_{spec.name}", custom_tools_path
            )
            if not loader_spec or not loader_spec.loader:
                return None
            module = importlib.util.module_from_spec(loader_spec)
            module.__dict__["InterviewSession"] = InterviewSession
            loader_spec.loader.exec_module(module)
            _module_cache[key] = module
        except Exception as e:
            logger.error(
                "Failed to load custom_tools from %s: %s", custom_tools_path, e
            )
            return None

    func = getattr(module, function_name, None)
    return func if callable(func) else None


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


def coerce_hook_result(result: Any) -> Dict[str, Any]:
    """Normalize a hook return value to a dict (str-JSON parsed, else empty)."""
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


def _parse_validation_result(
    result: Any, original_value: str, validator_name: str
) -> Dict[str, Any]:
    """Normalize a validator return value to a {valid, ...} dict."""
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict):
                result = parsed
        except (json.JSONDecodeError, TypeError):
            pass
    if not isinstance(result, dict) or "valid" not in result:
        return {
            "valid": False,
            "error": f"Validator must return dict with 'valid' key, got {type(result)}",
            "validator": validator_name,
        }
    if result.get("valid") is True:
        out: Dict[str, Any] = {
            "valid": True,
            "value": result.get("value", original_value),
            "validator": validator_name,
        }
        for key in (
            "interview_complete",
            "response_directive",
            "retain_context_keys",
        ):
            if key in result:
                out[key] = result[key]
        return out
    out = {
        "valid": False,
        "error": result.get("error", f"Validation failed for {validator_name}"),
        "validator": validator_name,
    }
    if "response_directive" in result:
        out["response_directive"] = result["response_directive"]
    return out


async def run_validator(
    action: Any,
    spec: InterviewSpec,
    field: FieldDef,
    value: str,
    session: Optional[InterviewSession] = None,
    visitor: Any = None,
) -> Dict[str, Any]:
    """Run the field's configured validator. Returns a {valid, ...} dict.

    A field without a validator accepts any non-empty value. Built-in
    validators (by name) are tried first, then ``custom_tools.py`` functions.
    """
    cleaned = (value or "").strip()
    if not field.validator:
        return {"valid": True, "value": cleaned, "validator": None}
    if not cleaned:
        return {
            "valid": False,
            "error": f"No value provided for field '{field.key}'",
            "validator": field.validator,
        }

    builtin = get_validator(field.validator)
    if builtin:
        try:
            result = builtin(cleaned, **dict(field.validator_args))
        except Exception as e:
            return {
                "valid": False,
                "error": f"Validator error: {e}",
                "validator": field.validator,
            }
        return _parse_validation_result(result, cleaned, field.validator)

    func = load_hook_function(spec, field.validator)
    if not func:
        return {
            "valid": False,
            "error": f"No validator found for '{field.validator}' in {spec.name}",
            "validator": field.validator,
        }
    try:
        result = await call_hook(
            func,
            session=session,
            spec=spec,
            visitor=visitor,
            interview_action=action,
            value=cleaned,
            kwargs=dict(field.validator_args),
        )
    except Exception as e:
        return {
            "valid": False,
            "error": f"Validator error: {e}",
            "validator": field.validator,
        }
    return _parse_validation_result(result, cleaned, field.validator)
