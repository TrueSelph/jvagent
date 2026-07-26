"""Bootstrap an app and print a hash of the prompt it would send.

Run as a subprocess so the caller controls ``PYTHONHASHSEED``. Set iteration
order is fixed per interpreter, so the only honest way to test that it never
reaches the model is to render the same turn in two interpreters seeded
differently and compare.

    python -m tests.wire._render_once <app_root>
"""

from __future__ import annotations

import asyncio
import hashlib
import sys


async def main(app_root: str) -> int:
    from jvagent.core.app_context import set_app_root

    from tests.wire._probe import WireProbe, load_orchestrator

    set_app_root(app_root)
    probe = WireProbe(await load_orchestrator(app_root))
    cap = await probe.capture(
        "what can you do for me today?",
        block_raw_tool_invocation=True,
        max_statement_length=600,
    )
    for label, text in (("SYSTEM", cap.system), ("USER", cap.user)):
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        print(f"{label} {digest} {len(text)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1])))
