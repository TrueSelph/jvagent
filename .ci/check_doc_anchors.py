#!/usr/bin/env python3
"""Check documentation anchors (file:line references and relative links).

Scans active guides for:
1. Line-number references (path/to/file.py:123)
2. Relative markdown links [text](relative/path)

Skips historical review/spec dumps and external sibling-repo paths (jvspatial/).
Resolves bare package-relative paths under jvagent/.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Set, Tuple

# Hard-fail only on living agent/user guides. .planning/ ADRs and historical
# plans keep stale line anchors by design (ADRs are immutable once accepted).
HARD_GLOBS = [
    "CLAUDE.md",
    "AGENTS.md",
    "docs/**/*.md",
    "jvagent/**/CLAUDE.md",
    "jvagent/**/AGENTS.md",
    "jvagent/**/README.md",
    "tests/CLAUDE.md",
]

# Sibling / external repos referenced from docs — not resolvable in this checkout.
EXTERNAL_PREFIXES = ("jvspatial/",)


def find_markdown_files(root: Path) -> List[Path]:
    files: Set[Path] = set()
    for pattern in HARD_GLOBS:
        files.update(root.glob(pattern))
    return sorted(files)


def extract_line_refs(content: str) -> List[Tuple[str, int]]:
    pattern = r"(?:^|[\s(`\[])((?:\.\.?/)?[\w./-]+\.py):(\d+)(?:[`)\]\s,]|$)"
    refs = []
    for match in re.finditer(pattern, content, re.MULTILINE):
        filepath = match.group(1)
        line_num = int(match.group(2))
        refs.append((filepath, line_num))
    return refs


def extract_relative_links(content: str) -> List[str]:
    pattern = r"\[([^\]]+)\]\(([^)]+)\)"
    links = []
    for match in re.finditer(pattern, content):
        url = match.group(2).strip()
        if url.startswith(("http://", "https://", "mailto:", "#")):
            continue
        links.append(url)
    return links


def _candidate_paths(doc_path: Path, filepath: str, repo_root: Path) -> List[Path]:
    clean = filepath.lstrip("./")
    doc_dir = doc_path.parent
    jv = repo_root / "jvagent"
    candidates = [
        doc_dir / filepath,
        repo_root / filepath,
        repo_root / clean,
        jv / clean,
        jv / "action" / clean,
        jv / "memory" / clean,
        jv / "core" / clean,
        jv / "cli" / clean,
    ]
    # Bare basename — search under jvagent once.
    if "/" not in clean and clean.endswith(".py"):
        hits = list(jv.rglob(clean))
        candidates.extend(hits[:8])
    return candidates


def check_line_ref(
    doc_path: Path, filepath: str, line_num: int, repo_root: Path
) -> Tuple[bool, str]:
    if filepath.startswith(EXTERNAL_PREFIXES) or filepath.startswith("../"):
        # External or cross-repo — advisory only.
        return (True, "")

    for target in _candidate_paths(doc_path, filepath, repo_root):
        if not (target.exists() and target.is_file()):
            continue
        try:
            with open(target, "r", encoding="utf-8", errors="ignore") as f:
                n_lines = sum(1 for _ in f)
        except OSError as e:
            return (False, f"Error reading {filepath}: {e}")
        if line_num <= n_lines:
            return (True, "")
        try:
            rel = target.relative_to(repo_root)
        except ValueError:
            rel = target
        return (
            False,
            f"Line {line_num} exceeds file length ({n_lines} lines): {rel}",
        )

    return (False, f"File not found: {filepath}")


def check_relative_link(doc_path: Path, link: str, repo_root: Path) -> Tuple[bool, str]:
    if "#" in link:
        link = link.split("#", 1)[0]
    if not link:
        return (True, "")
    if link.startswith(("http://", "https://", "mailto:")):
        return (True, "")

    doc_dir = doc_path.parent
    candidates = [
        doc_dir / link,
        repo_root / link.lstrip("./"),
    ]
    for target in candidates:
        if target.exists():
            return (True, "")
    return (False, f"Link target not found: {link}")


def main() -> int:
    repo_root = Path(__file__).parent.parent.resolve()
    markdown_files = find_markdown_files(repo_root)

    if not markdown_files:
        print("No markdown files found to check")
        return 0

    print(f"Checking {len(markdown_files)} markdown files...")
    errors: List[str] = []

    for doc_path in markdown_files:
        rel_path = doc_path.relative_to(repo_root)
        try:
            content = doc_path.read_text(encoding="utf-8")
        except OSError as e:
            errors.append(f"{rel_path}: Error reading file: {e}")
            continue

        for filepath, line_num in extract_line_refs(content):
            ok, msg = check_line_ref(doc_path, filepath, line_num, repo_root)
            if not ok:
                errors.append(f"{rel_path}: {msg} (ref: {filepath}:{line_num})")

        for link in extract_relative_links(content):
            ok, msg = check_relative_link(doc_path, link, repo_root)
            if not ok:
                errors.append(f"{rel_path}: {msg}")

    if errors:
        print("\nFound broken references:\n")
        for error in errors:
            print(f"  {error}")
        print(f"\n{len(errors)} error(s) found")
        return 1

    print("All documentation anchors are valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
