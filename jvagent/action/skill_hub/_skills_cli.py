"""Async helpers for running ``npx skills`` CLI commands.

Provides subprocess wrappers for ``npx skills find`` (search) and
``npx skills add`` (download/install), with output parsing and
error handling. Node.js / npx must be available on the host.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_FIND_TIMEOUT = 30
_ADD_TIMEOUT = 60
_NPX_CMD = "npx"


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences and Unicode box-drawing noise."""
    # Strip common ANSI codes
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    # Strip cursor-control sequences (e.g. [?25l, [?25h, [999D, [J)
    text = re.sub(r"\x1b\[[?0-9A-Da-d]*[A-Za-z]", "", text)
    # Collapse weird Unicode whitespace artifacts
    text = re.sub(r"[\u2800-\u28ff]", "", text)  # Braille patterns
    # Collapse runs of spaces/tabs within lines, preserving leading indent
    lines = text.splitlines()
    processed = []
    for line in lines:
        stripped = line.lstrip()
        if stripped:
            indent = line[: len(line) - len(stripped)]
            # Collapse non-leading whitespace only
            stripped = re.sub(r"[^\S]+", " ", stripped)
            processed.append(indent + stripped)
        else:
            processed.append("")
    return "\n".join(processed).strip()


def parse_find_output(raw: str) -> List[Dict[str, Any]]:
    """Parse ``npx skills find`` output into structured skill metadata.

    Expected format (after ANSI stripping):
        owner/repo@skill-name  <install-count> installs
        └ https://skills.sh/owner/repo/skill-name

    Returns a list of dicts with keys: name, source, install_count, url.
    """
    cleaned = _strip_ansi(raw)
    results: List[Dict[str, Any]] = []

    # Match lines like: owner/repo@skill-name  123.4K installs
    pattern = re.compile(
        r"^([\w\-./]+@[\w\-]+)\s+"  # owner/repo@skill
        r"([\d.]+[KkMm]?)\s+installs",  # install count
        re.MULTILINE,
    )
    # Match URL lines: └ https://skills.sh/...
    url_pattern = re.compile(r"└\s+(https://skills\.sh/\S+)", re.MULTILINE)

    names = pattern.findall(cleaned)
    urls = url_pattern.findall(cleaned)

    for i, (ref, installs) in enumerate(names):
        # Split source@skill into parts
        if "@" in ref:
            source, skill_name = ref.rsplit("@", 1)
        else:
            source, skill_name = ref, ref

        entry: Dict[str, Any] = {
            "name": skill_name,
            "source": source,
            "install_count": installs,
            "has_tools": None,  # Not available from find output
        }
        if i < len(urls):
            entry["url"] = urls[i]
        results.append(entry)

    return results


def parse_add_list_output(raw: str) -> List[Dict[str, Any]]:
    """Parse ``npx skills add <source> -l`` output into skill metadata.

    Expected format (after ANSI stripping):
        Available Skills
          skill-name
            Description text...
          another-skill
            Another description...

    Returns a list of dicts with keys: name, description, has_tools.
    """
    cleaned = _strip_ansi(raw)
    results: List[Dict[str, Any]] = []

    # Find the "Available Skills" section
    section_match = re.search(r"Available Skills\s*\n(.+)", cleaned, re.DOTALL)
    if not section_match:
        return results

    section = section_match.group(1)

    # Parse: skill names are lines with ~2 spaces of indentation,
    # descriptions are lines with ~4+ spaces.
    # We detect name lines by checking if they have less indentation than
    # the following description lines.
    current_name = None
    current_desc_lines: List[str] = []

    for line in section.splitlines():
        if not line.strip():
            continue
        # Count leading spaces
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        if (
            indent <= 3
            and stripped
            and not stripped.startswith(
                (
                    "─",
                    "│",
                    "├",
                    "└",
                    "Tip",
                    "Source",
                    "Fetching",
                    "Found",
                    "Use --skill",
                )
            )
        ):
            # This is a skill name line
            if current_name is not None:
                results.append(
                    {
                        "name": current_name,
                        "description": " ".join(current_desc_lines).strip(),
                        "has_tools": None,
                    }
                )
            current_name = stripped
            current_desc_lines = []
        elif indent >= 4 and current_name is not None:
            # This is a description line
            current_desc_lines.append(stripped)

    # Don't forget the last skill
    if current_name is not None:
        results.append(
            {
                "name": current_name,
                "description": " ".join(current_desc_lines).strip(),
                "has_tools": None,
            }
        )

    return results


def parse_add_output(raw: str) -> List[str]:
    """Parse ``npx skills add`` output to extract installed file paths.

    Looks for lines indicating skill files were copied/symlinked.
    Returns a list of file paths (relative to the install dir).
    """
    cleaned = _strip_ansi(raw)
    paths: List[str] = []

    # Match patterns like: "Installed to .claude/skills/skill-name/SKILL.md"
    for match in re.finditer(r"Installed to\s+(\S+)", cleaned):
        paths.append(match.group(1))
    # Also match: "Copied <file> to <path>"
    for match in re.finditer(r"Copied\s+\S+\s+to\s+(\S+)", cleaned):
        paths.append(match.group(1))
    # Also match: "Created <path>"
    for match in re.finditer(r"Created\s+(\S+)", cleaned):
        paths.append(match.group(1))

    return paths


async def _run_npx(
    args: List[str], cwd: Optional[str] = None, timeout: int = 30
) -> Dict[str, Any]:
    """Run an npx command and return stdout or an error dict."""
    try:
        proc = await asyncio.create_subprocess_exec(
            _NPX_CMD,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        if proc.returncode != 0:
            return {"error": f"npx exited with code {proc.returncode}: {stderr[:300]}"}
        return {"output": stdout}
    except FileNotFoundError:
        return {"error": "npx is not available. Install Node.js to use skill_hub."}
    except asyncio.TimeoutError:
        return {"error": f"npx command timed out after {timeout}s"}
    except Exception as exc:
        logger.warning("npx skills failed: %s", exc, exc_info=True)
        return {"error": f"npx command failed: {exc}"}


async def run_skills_find(query: str, top_k: int = 5) -> Dict[str, Any]:
    """Run ``npx skills find <query>`` and return parsed results.

    Returns ``{"skills": [...]}`` on success or ``{"error": "..."}`` on failure.
    """
    result = await _run_npx(["skills", "find", query], timeout=_FIND_TIMEOUT)
    if "error" in result:
        return result
    skills = parse_find_output(result["output"])
    return {"skills": skills[:top_k]}


async def run_skills_list(source: str) -> Dict[str, Any]:
    """Run ``npx skills add <source> -l -y`` to list available skills in a repo.

    Returns ``{"skills": [...]}`` on success or ``{"error": "..."}`` on failure.
    """
    result = await _run_npx(["skills", "add", source, "-l", "-y"], timeout=_ADD_TIMEOUT)
    if "error" in result:
        return result
    skills = parse_add_list_output(result["output"])
    return {"skills": skills}


async def run_skills_add(
    source: str,
    skill: str,
    cwd: str,
) -> Dict[str, Any]:
    """Run ``npx skills add <source> -s <skill> --copy -y`` in a target directory.

    The ``cwd`` should be a directory with a ``.claude/`` subdirectory so
    the CLI has a recognized agent path to install into.

    Returns ``{"files": [...], "skill_name": ...}`` on success
    or ``{"error": "..."}`` on failure.
    """
    result = await _run_npx(
        ["skills", "add", source, "-s", skill, "--copy", "-y"],
        cwd=cwd,
        timeout=_ADD_TIMEOUT,
    )
    if "error" in result:
        return result

    files = parse_add_output(result["output"])
    return {"files": files, "skill_name": skill}
