"""Override module for core.utils: injects jvagent LLM bridge for observability.

Loaded via sys.modules injection so core imports see this instead of real utils.
Re-exports everything from real utils but overrides ChatGPT_API_async,
ChatGPT_API, ChatGPT_API_with_finish_reason to use jvagent model when in context.
"""

import importlib.util
import types
from pathlib import Path

from . import llm_bridge

_real_utils_path = Path(__file__).parent / "core" / "utils.py"
if not _real_utils_path.exists():
    raise FileNotFoundError(
        f"PageIndex core/utils.py not found at {_real_utils_path}. "
        "Ensure the core directory is present."
    )

_spec = importlib.util.spec_from_file_location(
    "_pageindex_utils_real",
    _real_utils_path,
)
_real = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_real)

_override = types.ModuleType("jvagent.action.pageindex.core.utils")
_override.__file__ = str(_real_utils_path)
_override.__package__ = "jvagent.action.pageindex.core"

for _name in dir(_real):
    if not _name.startswith("_"):
        setattr(_override, _name, getattr(_real, _name))


# Override the three LLM functions with bridge wrappers
async def _ChatGPT_API_async(model, prompt, api_key=None):
    return await llm_bridge.ChatGPT_API_async(
        model, prompt, api_key, _real_impl=_real.ChatGPT_API_async
    )


def _ChatGPT_API(model, prompt, api_key=None, chat_history=None):
    return llm_bridge.ChatGPT_API(
        model,
        prompt,
        api_key,
        chat_history,
        _real_impl=lambda m, p, k, h=None: _real.ChatGPT_API(m, p, k, h),
    )


def _ChatGPT_API_with_finish_reason(model, prompt, api_key=None, chat_history=None):
    return llm_bridge.ChatGPT_API_with_finish_reason(
        model,
        prompt,
        api_key,
        chat_history,
        _real_impl=lambda m, p, k, h=None: _real.ChatGPT_API_with_finish_reason(
            m, p, k, h
        ),
    )


setattr(_override, "ChatGPT_API_async", _ChatGPT_API_async)
setattr(_override, "ChatGPT_API", _ChatGPT_API)
setattr(_override, "ChatGPT_API_with_finish_reason", _ChatGPT_API_with_finish_reason)

# Export the override module (used by __init__.py for sys.modules injection)
override_module = _override
