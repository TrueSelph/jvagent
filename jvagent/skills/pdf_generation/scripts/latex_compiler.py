"""Generate a PDF from Markdown-like plain text using LaTeX or Tectonic."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from jvagent.skills.fileinterface.scripts._core import copy_host_file_into_sandbox
from jvagent.skills.pdf_generation.scripts._document_args import (
    parse_document_pdf_arguments,
)
from jvagent.skills.pdf_generation.scripts._drive_upload import (
    upload_pdf_to_drive_if_configured,
)
from jvagent.skills.pdf_generation.scripts._sandbox_output import (
    resolve_sandbox_pdf_output_dir,
    sandbox_pdf_dest_relpath,
)

logger = logging.getLogger(__name__)

# Log slice limits: failures need enough context (Tectonic errors are often at end of stdout).
_LOG_TAIL_OK = 2000
_LOG_TAIL_FAIL = 20000

# Hex color validation: only allow 6-digit hex (e.g. "1a2b3c") after stripping #
_HEX_COLOR_RE = re.compile(r"^[0-9a-fA-F]{6}$")


def _safe_hex_color(raw: str) -> str:
    """Return the 6-digit hex portion of *raw* if it looks like a hex color, else empty string."""
    if not raw:
        return ""
    stripped = raw.strip().lstrip("#")
    return stripped if _HEX_COLOR_RE.match(stripped) else ""


def _output_tails(
    stdout: Optional[str], stderr: Optional[str], *, failure: bool
) -> Dict[str, str]:
    lim = _LOG_TAIL_FAIL if failure else _LOG_TAIL_OK

    def clip(text: Optional[str]) -> str:
        if not text:
            return ""
        t = text
        if len(t) <= lim:
            return t
        return t[-lim:]

    return {"stdout_tail": clip(stdout), "stderr_tail": clip(stderr)}


def _find_tex_engine() -> Optional[str]:
    """Return the first usable TeX engine on PATH: xelatex, pdflatex, lualatex, then tectonic."""
    for cmd in ("xelatex", "pdflatex", "lualatex"):
        try:
            result = subprocess.run(
                [cmd, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return cmd
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    tectonic = shutil.which("tectonic")
    if not tectonic:
        return None
    try:
        result = subprocess.run(
            [tectonic, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return tectonic
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _engine_is_tectonic(latex_cmd: str) -> bool:
    return Path(latex_cmd).name == "tectonic"


def _render_jinja_template(template_path: Path, variables: Dict[str, Any]) -> str:
    """Render a Jinja2 template with the given variables.

    Falls back to simple string replacement if Jinja2 is not installed.
    """
    try:
        from jinja2 import Environment, FileSystemLoader

        template_dir = template_path.parent
        env = Environment(loader=FileSystemLoader(str(template_dir)))
        template = env.get_template(template_path.name)
        return template.render(**variables)
    except ImportError:
        # Fallback: simple string replacement
        content = template_path.read_text(encoding="utf-8")
        for key, value in variables.items():
            placeholder = "{{ " + key + " }}"
            content = content.replace(placeholder, str(value))
        return content


def _tex_escape(text: str) -> str:
    """Escape special LaTeX characters in text."""
    replacements = {
        "&": "\\&",
        "%": "\\%",
        "$": "\\$",
        "#": "\\#",
        "_": "\\_",
        "{": "\\{",
        "}": "\\}",
        "~": "\\textasciitilde{}",
        "^": "\\textasciicircum{}",
    }
    for char, escaped in replacements.items():
        text = text.replace(char, escaped)
    return text


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "pdf_generation__latex_compile",
        "description": (
            "Render a PDF from Markdown-like plain text using LaTeX (prefers xelatex) "
            "or Tectonic if no traditional engine is on PATH. "
            "Produces structured typography with cover page, table of contents, and "
            "headers or footers. Use pdf_generation__pandoc_fallback if no TeX engine "
            "is installed. Optional Google Drive upload when drive_output_folder_id is set."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Document title (cover page and metadata).",
                },
                "content": {
                    "type": "string",
                    "description": (
                        "Main document body. Markdown-like: #/## headings, - lists, "
                        "paragraphs. Legacy key: `body` (same meaning)."
                    ),
                },
                "subtitle": {
                    "type": "string",
                    "description": (
                        "Optional second line (e.g. audience, client, or report code). "
                        "Shown on the cover. Legacy key: `client_name`."
                    ),
                },
                "author": {
                    "type": "string",
                    "description": (
                        "Optional organization or author for header and cover. "
                        "Legacy key: `company_name`."
                    ),
                },
                "date": {
                    "type": "string",
                    "description": "Date string; defaults to today in locale format if omitted.",
                },
                "prepared_for_label": {
                    "type": "string",
                    "description": "Cover line before subtitle (default: 'Prepared for').",
                },
                "presented_by_label": {
                    "type": "string",
                    "description": "Label before author on the cover (default: 'Presented by').",
                },
                "mark_confidential": {
                    "type": "boolean",
                    "description": "Include CONFIDENTIAL in header, footer, and cover (default: true).",
                },
                "output_basename": {
                    "type": "string",
                    "description": (
                        "Optional base filename for Drive upload (e.g. 'Q1_Report' → "
                        "Q1_Report.pdf). If omitted, a name is derived from title or subtitle."
                    ),
                },
                "brand_primary_color": {
                    "type": "string",
                    "description": "Primary brand hex color for headings and table accents.",
                },
                "brand_accent_color": {
                    "type": "string",
                    "description": "Accent brand hex color for subsection titles and links.",
                },
                "brand_logo_path": {
                    "type": "string",
                    "description": "Optional logo image path on host for cover/header branding.",
                },
                "company_letterhead": {
                    "type": "string",
                    "description": "Optional letterhead text shown on the cover.",
                },
                "output_dir": {
                    "type": "string",
                    "description": (
                        "Sandbox-relative directory for the final PDF (e.g. output). "
                        "Defaults to the hosting action's output_dir or output. "
                        "pdf_path in the response is always this sandbox path, not a temp file."
                    ),
                },
                "body": {
                    "type": "string",
                    "description": "Alias for `content` (deprecated; prefer `content`).",
                },
                "client_name": {
                    "type": "string",
                    "description": "Alias for `subtitle` (deprecated; prefer `subtitle`).",
                },
                "company_name": {
                    "type": "string",
                    "description": "Alias for `author` (deprecated; prefer `author`).",
                },
                "drive_output_folder_id": {
                    "type": "string",
                    "description": "Optional Google Drive folder ID to upload the PDF to.",
                },
            },
            "required": ["title"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    """Compile a PDF from document content via LaTeX, with optional Drive upload."""
    # Allow legacy: required fields historically used body
    if not (arguments.get("content") or arguments.get("body")):
        return {
            "success": False,
            "error": "Missing `content` (or legacy `body`): provide the document body text.",
        }

    resolved_args = dict(arguments)
    action = getattr(visitor, "_current_action", None)
    for key in (
        "brand_primary_color",
        "brand_accent_color",
        "brand_logo_path",
        "company_letterhead",
    ):
        if not resolved_args.get(key) and action is not None:
            value = getattr(action, key, None)
            if value:
                resolved_args[key] = value

    params = parse_document_pdf_arguments(resolved_args)

    latex_cmd = _find_tex_engine()
    if latex_cmd is None:
        return {
            "success": False,
            "error": "No TeX engine found (xelatex/pdflatex/lualatex/tectonic) on this system",
            "suggestion": (
                "Install TeX Live, MacTeX, or MiKTeX (xelatex et al.), or the standalone "
                "`tectonic` CLI (e.g. brew install tectonic; apt install tectonic where available). "
                "Alternatively use pdf_generation__pandoc_fallback (WeasyPrint)."
            ),
        }

    body_text = params.content
    latex_body = _convert_to_latex(body_text)

    template_dir = Path(__file__).resolve().parent.parent / "templates"
    template_path = template_dir / "document.tex.j2"

    if not template_path.exists():
        return {
            "success": False,
            "error": f"LaTeX template not found at {template_path}",
        }

    logo_abs_path = ""
    if params.brand_logo_path:
        try:
            logo_path = Path(params.brand_logo_path).expanduser()
            if logo_path.exists():
                logo_abs_path = str(logo_path.resolve())
        except Exception:
            logo_abs_path = ""

    variables: Dict[str, Any] = {
        "title": _tex_escape(params.title),
        "subtitle": _tex_escape(params.subtitle) if params.subtitle else "",
        "date": _tex_escape(params.date),
        "author": _tex_escape(params.author) if params.author else "",
        "prepared_for_label": _tex_escape(params.prepared_for_label),
        "presented_by_label": _tex_escape(params.presented_by_label),
        "brand_primary_color": _safe_hex_color(params.brand_primary_color),
        "brand_accent_color": _safe_hex_color(params.brand_accent_color),
        "brand_logo_path": _tex_escape(logo_abs_path) if logo_abs_path else "",
        "company_letterhead": (
            _tex_escape(params.company_letterhead) if params.company_letterhead else ""
        ),
        "mark_confidential": params.mark_confidential,
        "body": latex_body,
    }

    try:
        tex_content = _render_jinja_template(template_path, variables)
    except Exception as e:
        return {"success": False, "error": f"Template rendering failed: {e}"}

    work_dir = Path(tempfile.gettempdir()) / f"jvagent_pdf_{uuid4().hex[:8]}"
    work_dir.mkdir(parents=True, exist_ok=True)
    compile_logs: List[Dict[str, Any]] = []
    try:
        tex_path = work_dir / "document.tex"
        tex_path.write_text(tex_content, encoding="utf-8")

        if _engine_is_tectonic(latex_cmd):
            # Tectonic writes its download cache under the user cache dir by default. In containers,
            # sandboxes, or hardened hosts that path may be read-only; use a per-build cache in work_dir.
            tectonic_cache = work_dir / ".tectonic_cache"
            tectonic_cache.mkdir(parents=True, exist_ok=True)
            tectonic_env = os.environ.copy()
            tectonic_env["TECTONIC_CACHE_DIR"] = str(tectonic_cache)
            # Tectonic bundles TeX and may fetch packages on first run; allow a longer timeout.
            try:
                result = subprocess.run(
                    [latex_cmd, str(tex_path)],
                    cwd=str(work_dir),
                    capture_output=True,
                    text=True,
                    timeout=300,
                    env=tectonic_env,
                )
                failed = result.returncode != 0
                compile_logs.append(
                    {
                        "pass": 1,
                        "return_code": result.returncode,
                        **_output_tails(result.stdout, result.stderr, failure=failed),
                    }
                )
            except subprocess.TimeoutExpired:
                compile_logs.append({"pass": 1, "error": "Timeout (300s)"})
            except FileNotFoundError:
                return {
                    "success": False,
                    "error": f"TeX command '{latex_cmd}' not found",
                }
        else:
            max_passes = 3
            for pass_num in range(max_passes):
                try:
                    result = subprocess.run(
                        [
                            latex_cmd,
                            "-interaction=nonstopmode",
                            "-halt-on-error",
                            "-no-shell-escape",
                            f"-output-directory={work_dir}",
                            str(tex_path),
                        ],
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )
                    failed = result.returncode != 0
                    compile_logs.append(
                        {
                            "pass": pass_num + 1,
                            "return_code": result.returncode,
                            **_output_tails(
                                result.stdout, result.stderr, failure=failed
                            ),
                        }
                    )

                    if result.returncode != 0:
                        stderr = result.stderr or ""
                        stdout = result.stdout or ""
                        combined = stderr + stdout
                        if "Fatal error" in combined or "! Emergency stop" in combined:
                            break
                except subprocess.TimeoutExpired:
                    compile_logs.append(
                        {"pass": pass_num + 1, "error": "Timeout (120s)"}
                    )
                    break
                except FileNotFoundError:
                    return {
                        "success": False,
                        "error": f"LaTeX command '{latex_cmd}' not found",
                    }

        pdf_path = work_dir / "document.pdf"

        if not pdf_path.exists():
            return {
                "success": False,
                "error": "PDF was not generated after LaTeX compilation",
                "compile_logs": compile_logs,
                "tex_content": tex_content,
                "suggestion": "Check the compile logs. Try pdf_generation__pandoc_fallback if needed.",
            }

        drive_result = await upload_pdf_to_drive_if_configured(
            visitor, pdf_path, params
        )

        try:
            sandbox_dir = resolve_sandbox_pdf_output_dir(arguments, visitor)
        except ValueError as e:
            return {
                "success": False,
                "error": str(e),
                "compile_logs": compile_logs,
                "latex_command": latex_cmd,
                "drive_upload": drive_result,
            }

        dest_relpath = sandbox_pdf_dest_relpath(sandbox_dir, params)
        try:
            await copy_host_file_into_sandbox(visitor, str(pdf_path), dest_relpath)
        except Exception as e:
            logger.warning("LaTeX PDF sandbox copy failed: %s", e)
            return {
                "success": False,
                "error": f"PDF was compiled but could not be written to the user sandbox: {e}",
                "compile_logs": compile_logs,
                "latex_command": latex_cmd,
                "drive_upload": drive_result,
            }

        logger.info("PDF written to sandbox: %s", dest_relpath)
        return {
            "success": True,
            "pdf_path": dest_relpath,
            "compile_logs": compile_logs,
            "latex_command": latex_cmd,
            "drive_upload": drive_result,
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _convert_to_latex(text: str) -> str:
    """Convert plain/Markdown text sections to LaTeX."""
    lines = text.split("\n")
    latex_lines: List[str] = []
    in_itemize = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("## "):
            if in_itemize:
                latex_lines.append("\\end{itemize}")
                in_itemize = False
            latex_lines.append("")
            latex_lines.append(f"\\subsection{{{_tex_escape(stripped[3:])}}}")
            latex_lines.append("")

        elif stripped.startswith("# "):
            if in_itemize:
                latex_lines.append("\\end{itemize}")
                in_itemize = False
            latex_lines.append("")
            latex_lines.append(f"\\section{{{_tex_escape(stripped[2:])}}}")
            latex_lines.append("")

        elif stripped.startswith("- ") or stripped.startswith("* "):
            if not in_itemize:
                latex_lines.append("\\begin{itemize}")
                in_itemize = True
            latex_lines.append(f"\\item {_tex_escape(stripped[2:])}")

        elif stripped and stripped[0].isdigit() and ". " in stripped[:4]:
            if in_itemize:
                latex_lines.append("\\end{itemize}")
                in_itemize = False
            latex_lines.append(f"\\item {_tex_escape(stripped.split('. ', 1)[1])}")

        elif not stripped:
            if in_itemize:
                latex_lines.append("\\end{itemize}")
                in_itemize = False
            latex_lines.append("")

        else:
            if in_itemize:
                latex_lines.append("\\end{itemize}")
                in_itemize = False
            latex_lines.append(_tex_escape(stripped))

    if in_itemize:
        latex_lines.append("\\end{itemize}")

    return "\n".join(latex_lines)
