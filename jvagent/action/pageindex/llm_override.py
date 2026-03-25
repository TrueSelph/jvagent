"""Override module for core.utils: injects jvagent LLM bridge for observability.

Loaded via sys.modules injection. Patches ``llm_completion`` / ``llm_acompletion``
on the executed _real module so internal helpers resolve bridge + cancellation
at call time.
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

_orig_llm_acompletion = _real.llm_acompletion
_orig_llm_completion = _real.llm_completion


async def _llm_acompletion(model, prompt):
    return await llm_bridge.llm_acompletion(
        model, prompt, _real_impl=_orig_llm_acompletion
    )


def _llm_completion(model, prompt, chat_history=None, return_finish_reason=False):
    return llm_bridge.llm_completion(
        model,
        prompt,
        chat_history,
        return_finish_reason,
        _real_impl=_orig_llm_completion,
    )


_real.llm_acompletion = _llm_acompletion
_real.llm_completion = _llm_completion

_override = types.ModuleType("jvagent.action.pageindex.core.utils")
_override.__file__ = str(_real_utils_path)
_override.__package__ = "jvagent.action.pageindex.core"

for _name in dir(_real):
    if not _name.startswith("_"):
        setattr(_override, _name, getattr(_real, _name))

override_module = _override
