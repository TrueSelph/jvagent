"""Fallback PDF generation: Markdown → HTML → PDF (WeasyPrint) when LaTeX is unavailable."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4

from jvagent.skills.fileinterface._core import copy_host_file_into_sandbox
from jvagent.skills.pdf_generation._document_args import parse_document_pdf_arguments
from jvagent.skills.pdf_generation._drive_upload import (
    upload_pdf_to_drive_if_configured,
)
from jvagent.skills.pdf_generation._sandbox_output import (
    resolve_sandbox_pdf_output_dir,
    sandbox_pdf_dest_relpath,
)


def _html_styles() -> str:
    return """
@page {
  size: A4;
  margin: 2.5cm 2.5cm 3cm 2.5cm;
  @bottom-center {
    content: counter(page) " / " counter(pages);
    font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
    font-size: 9pt;
    color: #666;
  }
}

body {
  font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
  font-size: 11pt;
  line-height: 1.6;
  color: #333;
}

.cover-page {
  page-break-after: always;
  text-align: center;
  padding-top: 30%;
}

.cover-page h1 {
  font-size: 28pt;
  color: #1a237e;
  margin-bottom: 1.5cm;
}

.cover-page .subtitle-line {
  font-size: 18pt;
  color: #333;
}

.cover-page .date {
  font-size: 12pt;
  color: #666;
  margin-top: 1cm;
}

.cover-page .author-line {
  font-size: 12pt;
  color: #333;
  margin-top: 0.75cm;
}

.cover-page .confidential {
  margin-top: 30%;
  font-size: 9pt;
  color: #999;
  text-transform: uppercase;
}

h1 {
  font-size: 20pt;
  color: #1a237e;
  border-bottom: 1px solid #1a237e;
  padding-bottom: 4pt;
  page-break-before: always;
}

h1:first-of-type {
  page-break-before: avoid;
}

h2 {
  font-size: 16pt;
  color: #0d47a1;
  margin-top: 1.2em;
}

h3 {
  font-size: 13pt;
  color: #333;
  margin-top: 1em;
}

table {
  width: 100%;
  border-collapse: collapse;
  margin: 1em 0;
}

table th {
  background-color: #1a237e;
  color: white;
  padding: 8px 12px;
  text-align: left;
}

table td {
  padding: 6px 12px;
  border-bottom: 1px solid #ddd;
}

table tr:nth-child(even) td {
  background-color: #f5f5f5;
}

blockquote {
  border-left: 3px solid #1a237e;
  margin: 1em 0;
  padding: 0.5em 1em;
  background: #f8f9ff;
  font-style: italic;
}

.footer-note {
  text-align: center;
  color: #999;
  font-size: 8pt;
  text-transform: uppercase;
  page-break-before: always;
  padding-top: 50%;
}

.review-marker {
  background: #fff3cd;
  border: 1px solid #ffc107;
  padding: 4px 8px;
  border-radius: 3px;
  font-size: 9pt;
  color: #856404;
}
"""


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "pdf_generation__pandoc_fallback",
        "description": (
            "Render a PDF from Markdown using HTML→PDF (WeasyPrint). Use when no LaTeX "
            "engine is available or as a faster path for simpler layouts. Optional Google "
            "Drive upload when drive_output_folder_id is set."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Document title (cover and metadata).",
                },
                "content": {
                    "type": "string",
                    "description": "Main body in Markdown. Legacy: same as `body` key.",
                },
                "subtitle": {
                    "type": "string",
                    "description": "Optional cover line (e.g. audience). Legacy: `client_name`.",
                },
                "author": {
                    "type": "string",
                    "description": "Optional organization or author. Legacy: `company_name`.",
                },
                "date": {
                    "type": "string",
                    "description": "Date string; defaults to today if omitted.",
                },
                "prepared_for_label": {
                    "type": "string",
                    "description": "Text before subtitle on the cover (default: 'Prepared for').",
                },
                "presented_by_label": {
                    "type": "string",
                    "description": "Label before author on the cover (default: 'Presented by').",
                },
                "mark_confidential": {
                    "type": "boolean",
                    "description": "Show a confidential mark on the cover (default: true).",
                },
                "output_basename": {
                    "type": "string",
                    "description": "Optional base filename for Drive upload (.pdf added if needed).",
                },
                "body": {
                    "type": "string",
                    "description": "Alias for `content` (deprecated).",
                },
                "client_name": {
                    "type": "string",
                    "description": "Alias for `subtitle` (deprecated).",
                },
                "company_name": {
                    "type": "string",
                    "description": "Alias for `author` (deprecated).",
                },
                "output_dir": {
                    "type": "string",
                    "description": (
                        "Sandbox-relative directory for the final PDF (e.g. output). "
                        "Defaults to the hosting action's output_dir or output. "
                        "pdf_path is always this sandbox path."
                    ),
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
    """Generate a PDF via WeasyPrint HTML→PDF conversion."""
    if not (arguments.get("content") or arguments.get("body")):
        return {
            "success": False,
            "error": "Missing `content` (or legacy `body`): provide the document body text.",
        }

    try:
        import weasyprint
    except ImportError:
        return {
            "success": False,
            "error": "WeasyPrint is not installed",
            "suggestion": (
                "Declare weasyprint under package.dependencies.pip on your app's "
                "SkillInteractAction (e.g. proposal_skill_interact_action), or "
                "pip install weasyprint. For best quality, install a system LaTeX "
                "and use pdf_generation__latex_compile."
            ),
        }

    params = parse_document_pdf_arguments(arguments)
    content = params.content

    cover_parts: list = [
        '<div class="cover-page">',
        f"    <h1>{_html_escape(params.title)}</h1>",
    ]
    if params.subtitle:
        text = f"{params.prepared_for_label} {params.subtitle}"
        cover_parts.append(f'    <div class="subtitle-line">{_html_escape(text)}</div>')
    cover_parts.append(f'    <div class="date">{_html_escape(params.date)}</div>')
    if params.author:
        by_line = f"{params.presented_by_label} {params.author}"
        cover_parts.append(
            f'    <div class="author-line">{_html_escape(by_line)}</div>'
        )
    if params.mark_confidential:
        cover_parts.append('    <div class="confidential">Confidential</div>')
    cover_parts.append("</div>")
    cover_html = "\n".join(cover_parts)

    body_html = _markdown_to_html(content)

    full_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{_html_escape(params.title)}</title>
    <style>{_html_styles()}</style>
</head>
<body>
    {cover_html}
    {body_html}
    <div class="footer-note">End of document</div>
</body>
</html>"""

    work_dir = Path(tempfile.gettempdir()) / f"jvagent_pdf_{uuid4().hex[:8]}"
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        pdf_path = work_dir / "document.pdf"
        try:
            weasyprint.HTML(string=full_html).write_pdf(str(pdf_path))
        except Exception as e:
            return {"success": False, "error": f"PDF generation failed: {e}"}

        if not pdf_path.exists():
            return {"success": False, "error": "PDF was not generated"}

        drive_result = await upload_pdf_to_drive_if_configured(
            visitor, pdf_path, params
        )

        try:
            sandbox_dir = resolve_sandbox_pdf_output_dir(arguments, visitor)
        except ValueError as e:
            return {
                "success": False,
                "error": str(e),
                "method": "weasyprint",
                "drive_upload": drive_result,
            }

        dest_relpath = sandbox_pdf_dest_relpath(sandbox_dir, params)
        try:
            await copy_host_file_into_sandbox(visitor, str(pdf_path), dest_relpath)
        except Exception as e:
            return {
                "success": False,
                "error": f"PDF was generated but could not be written to the user sandbox: {e}",
                "method": "weasyprint",
                "drive_upload": drive_result,
            }

        return {
            "success": True,
            "pdf_path": dest_relpath,
            "method": "weasyprint",
            "drive_upload": drive_result,
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _markdown_to_html(md: str) -> str:
    """Convert basic Markdown to HTML for PDF rendering.

    Supports: headings, bold, italic, lists, tables, paragraphs, blockquotes, code.
    """
    import re

    lines = md.split("\n")
    html_parts: list = []
    in_list = False
    in_table = False

    for line in lines:
        stripped = line.strip()

        if stripped == "---" and not in_table:
            html_parts.append("")
            continue

        if stripped.startswith("##### "):
            html_parts.append(f"<h5>{_html_escape(stripped[6:])}</h5>")
        elif stripped.startswith("#### "):
            html_parts.append(f"<h4>{_html_escape(stripped[5:])}</h4>")
        elif stripped.startswith("### "):
            html_parts.append(f"<h3>{_html_escape(stripped[4:])}</h3>")
        elif stripped.startswith("## "):
            html_parts.append(f"<h2>{_html_escape(stripped[3:])}</h2>")
        elif stripped.startswith("# "):
            html_parts.append(f"<h1>{_html_escape(stripped[2:])}</h1>")

        elif stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            html_parts.append(f"<li>{_inline_md_to_html(stripped[2:])}</li>")

        elif "|" in stripped and stripped.startswith("|"):
            cells = [c.strip() for c in stripped.split("|") if c.strip()]
            if not in_table:
                html_parts.append("<table><thead><tr>")
                for cell in cells:
                    html_parts.append(f"<th>{_html_escape(cell)}</th>")
                html_parts.append("</tr></thead><tbody>")
                in_table = True
            else:
                if all("-" in cell for cell in cells):
                    continue
                html_parts.append("<tr>")
                for cell in cells:
                    html_parts.append(f"<td>{_html_escape(cell)}</td>")
                html_parts.append("</tr>")

        elif in_table and not stripped.startswith("|"):
            html_parts.append("</tbody></table>")
            in_table = False

        elif stripped.startswith("> "):
            html_parts.append(
                f"<blockquote>{_inline_md_to_html(stripped[2:])}</blockquote>"
            )

        elif stripped in ("---", "***", "___"):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append("<hr>")

        elif not stripped:
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append("<br>")

        else:
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append(f"<p>{_inline_md_to_html(stripped)}</p>")

    if in_list:
        html_parts.append("</ul>")
    if in_table:
        html_parts.append("</tbody></table>")

    return "\n".join(html_parts)


def _inline_md_to_html(text: str) -> str:
    """Convert inline Markdown (bold, italic, code, links) to HTML."""
    import re

    escaped = _html_escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"__(.+?)__", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*(.+?)\*", r"<em>\1</em>", escaped)
    escaped = re.sub(r"_(.+?)_", r"<em>\1</em>", escaped)
    escaped = re.sub(r"`(.+?)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', escaped)
    return escaped
