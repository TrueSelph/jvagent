#!/usr/bin/env python3
"""Check documentation anchors (file:line references and relative links).

Scans markdown files for:
1. Line-number references (path/to/file.py:123) in text and backticks
2. Relative markdown links [text](relative/path)

Exits with error if any reference is broken.
"""
import re
import sys
from pathlib import Path
from typing import List, Tuple


def find_markdown_files(root: Path) -> List[Path]:
    """Find all markdown files to scan (excluding .planning/archive/)."""
    patterns = [
        "CLAUDE.md",
        "AGENTS.md",
        "docs/**/*.md",
        ".planning/**/*.md",
    ]
    
    files = set()
    for pattern in patterns:
        files.update(root.glob(pattern))
    
    # Exclude archive directory
    archive_dir = root / ".planning" / "archive"
    files = {f for f in files if not f.is_relative_to(archive_dir) if archive_dir.exists()}
    
    return sorted(files)


def extract_line_refs(content: str) -> List[Tuple[str, int]]:
    """Extract file:line references from markdown content.
    
    Matches:
    - path/to/file.py:123
    - `file.py:123`
    - (file.py:123)
    - at `file.py:123`
    
    Returns list of (filepath, line_number) tuples.
    """
    # Pattern for file:line references
    # Supports: file.py:123, path/to/file.py:123, ../path/file.py:123
    pattern = r'(?:^|[\s(`])((?:\.\.?/)?[\w/.-]+\.py):(\d+)(?:[`)\s]|$)'
    
    refs = []
    for match in re.finditer(pattern, content, re.MULTILINE):
        filepath = match.group(1)
        line_num = int(match.group(2))
        refs.append((filepath, line_num))
    
    return refs


def extract_relative_links(content: str) -> List[str]:
    """Extract relative markdown links [text](path).
    
    Returns list of relative paths (excludes http/https URLs).
    """
    # Pattern for markdown links
    pattern = r'\[([^\]]+)\]\(([^)]+)\)'
    
    links = []
    for match in re.finditer(pattern, content):
        url = match.group(2)
        # Skip http/https URLs and anchors
        if not url.startswith(('http://', 'https://', '#')):
            links.append(url)
    
    return links


def check_line_ref(doc_path: Path, filepath: str, line_num: int, repo_root: Path) -> Tuple[bool, str]:
    """Check if file:line reference is valid.
    
    Returns (is_valid, error_message).
    """
    # Resolve relative paths from the document's directory
    doc_dir = doc_path.parent
    
    # Try multiple resolution strategies
    possible_paths = [
        doc_dir / filepath,  # Relative to document
        repo_root / filepath,  # Relative to repo root
    ]
    
    # If path starts with .planning/, also try from repo root
    if filepath.startswith('.planning/'):
        possible_paths.append(repo_root / filepath[1:])  # Strip leading dot
    
    for target in possible_paths:
        if target.exists() and target.is_file():
            # Check line count
            try:
                with open(target, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                    if line_num <= len(lines):
                        return (True, "")
                    else:
                        return (
                            False,
                            f"Line {line_num} exceeds file length ({len(lines)} lines): {target.relative_to(repo_root)}"
                        )
            except Exception as e:
                return (False, f"Error reading {target.relative_to(repo_root)}: {e}")
    
    return (False, f"File not found: {filepath}")


def check_relative_link(doc_path: Path, link: str, repo_root: Path) -> Tuple[bool, str]:
    """Check if relative markdown link target exists.
    
    Returns (is_valid, error_message).
    """
    # Strip fragment identifier
    if '#' in link:
        link = link.split('#')[0]
    
    # Skip empty links (pure anchors)
    if not link:
        return (True, "")
    
    doc_dir = doc_path.parent
    target = doc_dir / link
    
    if target.exists():
        return (True, "")
    
    return (False, f"Link target not found: {link}")


def main():
    repo_root = Path(__file__).parent.parent.resolve()
    markdown_files = find_markdown_files(repo_root)
    
    if not markdown_files:
        print("No markdown files found to check")
        return 0
    
    print(f"Checking {len(markdown_files)} markdown files...")
    
    errors = []
    
    for doc_path in markdown_files:
        rel_path = doc_path.relative_to(repo_root)
        
        try:
            content = doc_path.read_text(encoding='utf-8')
        except Exception as e:
            errors.append(f"{rel_path}: Error reading file: {e}")
            continue
        
        # Check line references
        line_refs = extract_line_refs(content)
        for filepath, line_num in line_refs:
            is_valid, error_msg = check_line_ref(doc_path, filepath, line_num, repo_root)
            if not is_valid:
                errors.append(f"{rel_path}: {error_msg} (ref: {filepath}:{line_num})")
        
        # Check relative links
        relative_links = extract_relative_links(content)
        for link in relative_links:
            is_valid, error_msg = check_relative_link(doc_path, link, repo_root)
            if not is_valid:
                errors.append(f"{rel_path}: {error_msg}")
    
    if errors:
        print("\n❌ Found broken references:\n")
        for error in errors:
            print(f"  {error}")
        print(f"\n{len(errors)} error(s) found")
        return 1
    else:
        print("✅ All documentation anchors are valid")
        return 0


if __name__ == "__main__":
    sys.exit(main())
