#!/usr/bin/env python3
"""Render a Markdown (or plain-text) file to PDF — self-contained, no jvagent deps.

Runs inside the code-execution sandbox via the ``code_execution__bash`` tool,
with the cwd being the user's own workspace. Reads an input file and writes a
PDF into the workspace, then prints the output path.

Engine cascade (first available wins):
  1. pandoc + a LaTeX engine (xelatex/pdflatex/lualatex/tectonic) — best quality
  2. pandoc + wkhtmltopdf or weasyprint (HTML route)
  3. weasyprint (Python; Markdown -> HTML -> PDF)
If none is available it prints an actionable error and exits non-zero.

Usage:
  python render_pdf.py --input doc.md --output output/report.pdf [--title "Title"]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys


def _which(name: str) -> bool:
    return shutil.which(name) is not None


def _first_latex_engine() -> str:
    for eng in ("xelatex", "pdflatex", "lualatex", "tectonic"):
        if _which(eng):
            return eng
    return ""


def _run(cmd: list) -> tuple:
    proc = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    return proc.returncode, proc.stdout, proc.stderr


def _render_pandoc_latex(inp: str, out: str, title: str) -> bool:
    engine = _first_latex_engine()
    if not (_which("pandoc") and engine):
        return False
    cmd = ["pandoc", inp, "-o", out, f"--pdf-engine={engine}", "--standalone"]
    if title:
        cmd += ["--metadata", f"title={title}"]
    code, _, err = _run(cmd)
    if code != 0:
        sys.stderr.write(err)
    return code == 0 and os.path.exists(out)


def _render_pandoc_html(inp: str, out: str, title: str) -> bool:
    html_engine = "wkhtmltopdf" if _which("wkhtmltopdf") else ""
    if not html_engine and _which("weasyprint"):
        html_engine = "weasyprint"
    if not (_which("pandoc") and html_engine):
        return False
    cmd = ["pandoc", inp, "-o", out, f"--pdf-engine={html_engine}", "--standalone"]
    if title:
        cmd += ["--metadata", f"title={title}"]
    code, _, err = _run(cmd)
    if code != 0:
        sys.stderr.write(err)
    return code == 0 and os.path.exists(out)


def _render_weasyprint(inp: str, out: str, title: str) -> bool:
    try:
        import markdown  # type: ignore
        from weasyprint import HTML  # type: ignore
    except Exception:
        return False
    with open(inp, "r", encoding="utf-8") as fh:
        body = markdown.markdown(fh.read(), extensions=["tables", "fenced_code"])
    head = f"<title>{title}</title>" if title else ""
    html = f"<!doctype html><html><head><meta charset='utf-8'>{head}</head><body>{body}</body></html>"
    HTML(string=html).write_pdf(out)
    return os.path.exists(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Render Markdown/text to PDF.")
    ap.add_argument("--input", required=True, help="Path to the Markdown/text file.")
    ap.add_argument("--output", default="output/document.pdf", help="Output PDF path.")
    ap.add_argument("--title", default="", help="Optional document title.")
    args = ap.parse_args()

    if not os.path.isfile(args.input):
        sys.stderr.write(f"input not found: {args.input}\n")
        return 2

    out_dir = os.path.dirname(os.path.abspath(args.output))
    os.makedirs(out_dir, exist_ok=True)

    for render in (_render_pandoc_latex, _render_pandoc_html, _render_weasyprint):
        try:
            if render(args.input, args.output, args.title):
                print(args.output)
                return 0
        except Exception as exc:  # try the next engine
            sys.stderr.write(f"{render.__name__} failed: {exc}\n")

    sys.stderr.write(
        "No PDF engine available. Install one of: pandoc + a LaTeX engine "
        "(xelatex/pdflatex/tectonic), pandoc + wkhtmltopdf, or "
        "`pip install weasyprint markdown`.\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
