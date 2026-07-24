#!/usr/bin/env python3
"""Build the jvmessenger embeddable chat and stage it into the jvagent package.

Runs the jvmessenger Vite builds (the loader IIFE + the React iframe app) and
copies the output into ``jvagent/messenger/dist/`` so it ships as wheel
package-data and is served by ``jvagent messenger``. Run before ``python -m build``
(and in the publish workflow, after the jvchat build step).

    python scripts/build_jvmessenger.py [--no-install]

Requires Node.js + npm. The staged ``jvagent/messenger/dist/`` is git-ignored.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
JVMESSENGER = REPO / "jvmessenger"
SRC_DIST = JVMESSENGER / "dist"
DEST_DIST = REPO / "jvagent" / "messenger" / "dist"


def _run(cmd: list[str]) -> None:
    print(f"$ {' '.join(cmd)} (in {JVMESSENGER})")
    subprocess.run(cmd, cwd=JVMESSENGER, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build + stage the jvmessenger UI.")
    parser.add_argument(
        "--no-install",
        action="store_true",
        help="Skip `npm ci` (assume node_modules is present).",
    )
    args = parser.parse_args()

    if not JVMESSENGER.is_dir():
        print(f"jvmessenger source not found at {JVMESSENGER}", file=sys.stderr)
        return 1
    if shutil.which("npm") is None:
        print(
            "npm not found on PATH — Node.js is required to build the messenger.",
            file=sys.stderr,
        )
        return 1

    if not args.no_install:
        _run(["npm", "ci"])
    _run(["npm", "run", "build"])

    missing = [
        rel for rel in ("loader.js", "app.html") if not (SRC_DIST / rel).is_file()
    ]
    if missing:
        print(
            f"Build did not produce {', '.join(str(SRC_DIST / m) for m in missing)}",
            file=sys.stderr,
        )
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
