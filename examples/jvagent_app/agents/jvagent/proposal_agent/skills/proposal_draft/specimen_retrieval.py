"""Retrieve specimen proposals, template, and guide from the corpus."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _read_file(path: Path) -> Optional[str]:
    """Safely read a text file, returning None if it doesn't exist."""
    if path.exists() and path.is_file():
        return path.read_text(encoding="utf-8")
    return None


def _discover_specimens(corpus_dir: Path) -> List[Dict[str, Any]]:
    """Discover specimen proposal files in the corpus directory."""
    specimens: List[Dict[str, Any]] = []
    if not corpus_dir.exists():
        return specimens

    for fpath in corpus_dir.rglob("*.md"):
        rel = fpath.relative_to(corpus_dir)
        if rel.name in ("template.md", "guide.md", "README.md"):
            continue
        text = _read_file(fpath) or ""
        specimens.append(
            {
                "path": str(fpath),
                "filename": fpath.name,
                "relative_path": str(rel),
                "parent_dir": str(rel.parent) if rel.parent != "." else "",
                "content": text,
                "char_count": len(text),
            }
        )
    return specimens


def _read_corpus_index(corpus_dir: Path) -> str:
    """Read the corpus README.md index."""
    readme = corpus_dir / "README.md"
    content = _read_file(readme)
    if content:
        return content
    return "# Specimen Corpus\n\n(No README.md index found. Discovered files are listed below.)"


def _rank_specimens(
    specimens: List[Dict[str, Any]],
    client_tags: List[str],
    max_specimens: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    tags = [t.lower().strip() for t in client_tags if str(t).strip()]

    scored: List[Tuple[int, Dict[str, Any]]] = []
    for sp in specimens:
        haystack = " ".join(
            [
                str(sp.get("filename", "")).lower(),
                str(sp.get("relative_path", "")).lower(),
                str(sp.get("parent_dir", "")).lower(),
            ]
        )
        score = 0
        for tag in tags:
            if tag in haystack:
                score += 2
            if tag in str(sp.get("content", "")).lower():
                score += 1
        scored.append((score, sp))

    scored.sort(key=lambda item: (item[0], item[1].get("char_count", 0)), reverse=True)
    selected = [sp for _, sp in scored[:max_specimens]]
    remainder = [sp for _, sp in scored[max_specimens:]]
    return selected, remainder


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "proposal_draft__retrieve_specimens",
        "description": (
            "Load the proposal template (template.md), writing guide (guide.md), "
            "and select relevant past proposal specimens from the corpus. "
            "Call this first to gather all reference materials before generating a draft."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "client_tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Tags describing the client context for specimen matching "
                        "(e.g., ['retail', 'mobile', 'e-commerce']). "
                        "The LLM should derive these from the transcript."
                    ),
                },
                "max_specimens": {
                    "type": "integer",
                    "description": "Maximum number of specimen proposals to return (default 3)",
                },
            },
            "required": ["client_tags"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    """Load template, guide, and select relevant specimens from the corpus."""
    corpus_dir = None
    client_tags: List[str] = arguments.get("client_tags", [])
    max_specimens: int = arguments.get("max_specimens", 3)

    # Try to resolve specimens path from the action config
    try:
        action = getattr(visitor, "_current_action", None)
        if action and hasattr(action, "specimens_path") and action.specimens_path:
            corpus_dir = Path(action.specimens_path)
    except Exception:
        pass

    # Fallback: check common locations
    if not corpus_dir or not corpus_dir.exists():
        candidates = [
            Path("specimens"),
            Path(os.getcwd()) / "specimens",
            Path(os.path.dirname(__file__)) / "../../../specimens",
        ]
        for c in candidates:
            if c.exists() and c.is_dir():
                corpus_dir = c
                break

    if not corpus_dir or not corpus_dir.exists():
        return {
            "template": None,
            "guide": None,
            "specimens": [],
            "corpus_index": None,
            "note": "No specimen corpus directory found. Generate the draft using built-in defaults.",
        }

    # Load template and guide
    template = _read_file(corpus_dir / "template.md")
    guide = _read_file(corpus_dir / "guide.md")
    corpus_index = _read_corpus_index(corpus_dir)

    # Discover specimens (all .md files except template, guide, README)
    all_specimens = _discover_specimens(corpus_dir)
    selected, remaining = _rank_specimens(all_specimens, client_tags, max_specimens)

    return {
        "template": template,
        "guide": guide,
        "corpus_index": corpus_index,
        "specimens": selected,
        "selected_specimen_contents": [sp.get("content", "") for sp in selected],
        "remaining_specimens": [
            {
                "filename": sp.get("filename"),
                "relative_path": sp.get("relative_path"),
            }
            for sp in remaining
        ],
        "available_count": len(all_specimens),
        "selected_count": len(selected),
        "selection_tags": client_tags,
        "specimens_path": str(corpus_dir),
        "instruction": (
            "Use selected_specimen_contents as direct writing references. "
            "Treat specimens as style guidance only; ground facts in the user transcript."
        ),
    }
