"""Override module for core.utils: injects jvagent LLM bridge for observability.

Loaded via sys.modules injection. Patches ``llm_completion`` / ``llm_acompletion``
on the executed _real module so internal helpers resolve bridge + cancellation
at call time. Also patches ``list_to_tree`` to preserve TOC fields that upstream
drops (``structure``, ``physical_index``, etc.).
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


def list_to_tree(data):
    """Build a tree from flat TOC items, preserving all input fields.

    Upstream only keeps title/start_index/end_index; JV needs ``structure`` and
    ``physical_index`` for title enrichment and graph metadata.
    """

    def get_parent_structure(structure):
        if not structure:
            return None
        parts = str(structure).split(".")
        return ".".join(parts[:-1]) if len(parts) > 1 else None

    nodes = {}
    root_nodes = []

    for item in data:
        structure = item.get("structure")
        node = {**item, "nodes": []}
        nodes[structure] = node
        parent_structure = get_parent_structure(structure)
        if parent_structure:
            if parent_structure in nodes:
                nodes[parent_structure]["nodes"].append(node)
            else:
                root_nodes.append(node)
        else:
            root_nodes.append(node)

    def clean_node(node):
        if not node["nodes"]:
            del node["nodes"]
        else:
            for child in node["nodes"]:
                clean_node(child)
        return node

    return [clean_node(node) for node in root_nodes]


_real.llm_acompletion = _llm_acompletion
_real.llm_completion = _llm_completion
_real.list_to_tree = list_to_tree

_override = types.ModuleType("jvagent.action.pageindex.core.utils")
_override.__file__ = str(_real_utils_path)
_override.__package__ = "jvagent.action.pageindex.core"

# Expose private helpers (e.g. ``_is_openai_model``) that other core modules import.
for _name in dir(_real):
    if _name.startswith("__") and _name.endswith("__"):
        continue
    setattr(_override, _name, getattr(_real, _name))

# Re-bind after the copy so callers get the bridged entry points.
_override.llm_acompletion = _llm_acompletion
_override.llm_completion = _llm_completion
_override.list_to_tree = list_to_tree

override_module = _override
