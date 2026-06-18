#!/usr/bin/env python3
"""Build the jvchat UI and stage it into the jvagent package.

Runs the jvchat Vite build and copies the output into
``jvagent/webui/dist/`` so it ships as wheel package-data and is served by
``jvagent chat``. Run before ``python -m build`` (and in the publish workflow).

    python scripts/build_jvchat.py [--no-install]

Requires Node.js + npm. The staged ``jvagent/webui/dist/`` is git-ignored.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
JVCHAT = REPO / "jvchat"
SRC_DIST = JVCHAT / "dist"
DEST_DIST = REPO / "jvagent" / "webui" / "dist"


def _run(cmd: list[str]) -> None:
    print(f"$ {' '.join(cmd)} (in {JVCHAT})")
    subprocess.run(cmd, cwd=JVCHAT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build + stage the jvchat UI.")
    parser.add_argument(
        "--no-install",
        action="store_true",
        help="Skip `npm ci` (assume node_modules is present).",
    )
    args = parser.parse_args()

    if not JVCHAT.is_dir():
        print(f"jvchat source not found at {JVCHAT}", file=sys.stderr)
        return 1
    if shutil.which("npm") is None:
        print(
            "npm not found on PATH — Node.js is required to build the UI.",
            file=sys.stderr,
        )
        return 1

    if not args.no_install:
        _run(["npm", "ci"])
    _run(["npm", "run", "build"])

    if not (SRC_DIST / "index.html").is_file():
        print(f"Build did not produce {SRC_DIST}/index.html", file=sys.stderr)
        return 1

    if DEST_DIST.exists():
        shutil.rmtree(DEST_DIST)
    DEST_DIST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SRC_DIST, DEST_DIST)
    count = sum(1 for _ in DEST_DIST.rglob("*") if _.is_file())
    print(f"Staged {count} files into {DEST_DIST.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
