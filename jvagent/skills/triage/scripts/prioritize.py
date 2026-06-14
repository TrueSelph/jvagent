#!/usr/bin/env python3
"""Sort triage findings by severity (descending) — self-contained, no deps.

Reads a JSON array of findings from --input (or stdin) and writes the sorted
array to --output (or stdout). Each finding should have a numeric ``severity``;
ties keep input order. Deterministic code beats asking the model to sort.

Usage:
  python prioritize.py --input findings.json --output ranked.json
  cat findings.json | python prioritize.py
"""

from __future__ import annotations

import argparse
import json
import sys


def _severity(item: dict) -> int:
    try:
        return int(item.get("severity", 0))
    except (TypeError, ValueError):
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Rank findings by severity (desc).")
    ap.add_argument("--input", default="", help="JSON file (default: stdin).")
    ap.add_argument("--output", default="", help="JSON file (default: stdout).")
    args = ap.parse_args()

    if args.input:
        with open(args.input, encoding="utf-8") as fh:
            raw = fh.read()
    else:
        raw = sys.stdin.read()
    try:
        findings = json.loads(raw or "[]")
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"invalid JSON: {exc}\n")
        return 2
    if not isinstance(findings, list):
        sys.stderr.write("expected a JSON array of findings\n")
        return 2

    findings.sort(key=_severity, reverse=True)
    out = json.dumps(findings, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(out)
        print(args.output)
    else:
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
