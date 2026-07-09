"""Guard: removed Rails-era actions must not reappear in jvagent source."""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_JVAGENT = _REPO / "jvagent"

_REMOVED_REFS = (
    "jvagent/interact_router",
    "jvagent/converse_interact_action",
    "jvagent/retrieval_interact_action",
    "jvagent/web_search_retrieval_interact_action",
    "jvagent/long_memory_retrieval_interact_action",
    "jvagent/pageindex_retrieval_interact_action",
    "jvagent/long_memory_interact_action",
    "jvagent/long_memory_store_interact_action",
)

_PATTERN = re.compile("|".join(re.escape(ref) for ref in _REMOVED_REFS))

_EXCLUDE = {
    "jvagent/core/agent_yaml_validator.py",
}


def test_no_removed_action_refs_in_jvagent_source() -> None:
    offenders: list[str] = []
    for path in _JVAGENT.rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".yaml", ".yml"}:
            continue
        rel = str(path.relative_to(_REPO))
        if rel in _EXCLUDE:
            continue
        text = path.read_text(encoding="utf-8")
        if _PATTERN.search(text):
            offenders.append(rel)
    assert not offenders, "Removed action refs remain:\n" + "\n".join(offenders)
