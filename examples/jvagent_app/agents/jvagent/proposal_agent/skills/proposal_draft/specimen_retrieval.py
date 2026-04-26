"""Retrieve specimen proposals, template, and guide from the corpus."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional



def _read_file(path: Path) -> Optional[str]:
    """Safely read a text file, returning None if it doesn't exist."""
    if path.exists() and path.is_file():
        return path.read_text(encoding="utf-8")
    return None


def _discover_specimens(corpus_dir: Path) -> List[Dict[str, Any]]:
    """Discover specimen proposal files in the corpus directory."""
    specimens = []
    if not corpus_dir.exists():
        return specimens

    # Walk all .md files excluding template.md and guide.md
    for fpath in corpus_dir.rglob("*.md"):
        rel = fpath.relative_to(corpus_dir)
        if rel.name in ("template.md", "guide.md", "README.md"):
            continue
        specimens.append(
            {
                "path": str(fpath),
                "filename": fpath.name,
                "relative_path": str(rel),
                "parent_dir": str(rel.parent) if rel.parent != "." else "",
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

    # If the corpus has a README index, prefer it for selection context
    # The LLM will use the index + tags to select relevant specimens
    # For simplicity, return the discovered specimens and let the LLM
    # select the most relevant ones based on the index and filenames
    return {
        "template": template,
        "guide": guide,
        "corpus_index": corpus_index,
        "specimens": all_specimens[:max_specimens]
        if len(all_specimens) <= max_specimens
        else all_specimens,
        "available_count": len(all_specimens),
        "specimens_path": str(corpus_dir),
        "instruction": (
            "Review the template, guide, and corpus index above. "
            "Select the most relevant specimens by filename based on client tags. "
            "If the corpus has more specimens than returned, note which ones "
            "you'd like to load by calling this tool again with specific tags."
        ),
    }
