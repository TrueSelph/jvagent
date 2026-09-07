#!/usr/bin/env python
"""Run the live provider smoke (real model calls — costs money).

    python scripts/live_smoke.py --provider openai [--model gpt-4o-mini] [--transport litellm]

Needs the provider's API key in the environment (see
``jvagent.testing.live_smoke.PROVIDER_ACTIONS``). Prints a JSON summary and exits
non-zero when any scenario fails. Used by the nightly ``live-providers`` workflow;
never part of the normal test suite.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys


def main() -> int:
    from jvagent.testing.live_smoke import (
        PROVIDER_ACTIONS,
        required_key_env,
        run_smoke,
        summarise,
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True, choices=sorted(PROVIDER_ACTIONS))
    parser.add_argument("--model", default=None)
    parser.add_argument("--transport", default=None, choices=["httpx", "litellm"])
    parser.add_argument(
        "--tool-protocol", default="auto", choices=["auto", "native", "json"]
    )
    args = parser.parse_args()

    key_env = required_key_env(args.provider, args.model)
    if key_env and not os.environ.get(key_env):
        print(json.dumps({"skipped": f"{key_env} not set"}))
        return 0

    results = asyncio.run(
        run_smoke(
            args.provider,
            args.model,
            transport=args.transport,
            tool_protocol=args.tool_protocol,
        )
    )
    summary = summarise(results)
    summary["provider"] = args.provider
    summary["model"] = args.model or PROVIDER_ACTIONS[args.provider][3]
    summary["transport"] = args.transport or "httpx"
    print(json.dumps(summary, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
