#!/usr/bin/env python3
"""A/B two orchestrator prompt variants against a live model.

The prompt consolidation in `perf(orchestrator): cut per-turn token and latency
cost` merged several overlapping rule blocks. Every distinct instruction
survived the rewrite, but "the instructions are all still there" and "a
gpt-4.1-class model still obeys them" are different claims, and only the second
one matters. This measures the second.

Each arm runs the same CUCS scenarios (`jvagent/testing/live_runner.py`) against
the same agent, differing only in the prompt constants. Arm `before` restores
the pre-consolidation text from git; arm `after` uses whatever is in the working
tree. Scenarios are run N times per arm because the loop runs at temperature
0.2, which is not deterministic -- a single run tells you almost nothing.

    # see the cost before committing to it
    python scripts/ab_prompt_variants.py --dry-run

    # then, with spend approved
    python scripts/ab_prompt_variants.py --runs 5 --model gpt-4.1

THIS SPENDS MONEY. Every scenario turn is one or more real model calls. Use
--dry-run first; it prints the request/token estimate and calls nothing.

Reading the result: a large gap between arms is a real signal. A small gap at
low N is *not* evidence of equivalence -- the honest conclusion there is "no
detectable difference at this sample size", and the fix is more runs, not a
softer claim.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import subprocess
import sys
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

# Commit that introduced the consolidated prompts; its parent holds the originals.
CONSOLIDATION_COMMIT = "4beccab7"

# Prompt attributes the consolidation touched, mapped to the module constant
# that supplies each default. Restoring these reproduces the "before" arm
# without checking out the old tree.
VARIANT_ATTRS: Tuple[Tuple[str, str], ...] = (
    ("system_prompt", "ORCHESTRATOR_STABLE_SYSTEM_PROMPT"),
    ("planning_prompt", "PLANNING_PROMPT"),
)

# The other two blocks stopped being attributes in ADR-0037 — their text is now
# owned by the parameter that states the rule, so an arm swaps the parameter's
# `response` rather than an attribute. Same text on the wire, same comparison;
# only the surface it is set through changed.
VARIANT_PARAMS: Tuple[Tuple[str, str], ...] = (
    ("memory.search_first", "MEMORY_PROMPT"),
    ("tools.selection", "TOOL_USE_POLICY"),
)

ALL_VARIANT_CONSTANTS: Tuple[str, ...] = tuple(
    name for _, name in VARIANT_ATTRS + VARIANT_PARAMS
)


def _param_responses(orchestrator: Any) -> Dict[str, str]:
    """Current `response` text of each parameter an arm can swap."""
    out: Dict[str, str] = {}
    for param in orchestrator.parameters or []:
        if isinstance(param, dict) and param.get("key") in dict(VARIANT_PARAMS):
            out[param["key"]] = param.get("response", "")
    return out


def _set_param_responses(orchestrator: Any, values: Dict[str, str]) -> None:
    """Rewrite those parameters in place, preserving every other field."""
    if not values:
        return
    updated = []
    for param in orchestrator.parameters or []:
        if isinstance(param, dict) and param.get("key") in values:
            param = {**param, "response": values[param["key"]]}
        updated.append(param)
    orchestrator.parameters = updated


def load_prior_prompt_constants(commit: str) -> Dict[str, str]:
    """Exec the pre-consolidation prompts.py from git and return its constants.

    Reading the old text out of history keeps the two arms honest: the `before`
    arm is the prompt that actually shipped, not a reconstruction.
    """
    source = subprocess.run(
        ["git", "show", f"{commit}^:jvagent/action/orchestrator/prompts.py"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    namespace: Dict[str, Any] = {"__name__": "prompts_before"}
    exec(compile(source, "<prompts@before>", "exec"), namespace)  # noqa: S102
    return {n: namespace[n] for n in ALL_VARIANT_CONSTANTS if n in namespace}


# Arms beyond the prompt-consolidation pair. Each is a plain attribute override
# applied on top of the working-tree defaults, so any config-shaped hypothesis
# can be measured with the same corpus rather than argued about.
ATTRIBUTE_ARMS: Dict[str, Dict[str, Any]] = {
    "listing-system": {"tool_listing_position": "system"},
    "listing-trailing": {"tool_listing_position": "trailing"},
}


def _register_reminder_arms() -> None:
    """Arms for the user-turn safeguards reminder (injection resistance)."""
    from jvagent.action.orchestrator import prompts

    ATTRIBUTE_ARMS["reminder-basic"] = {
        "safeguards_reminder": prompts.SAFEGUARDS_REMINDER_BASIC
    }
    ATTRIBUTE_ARMS["reminder-hardened"] = {
        "safeguards_reminder": prompts.SAFEGUARDS_REMINDER
    }


def apply_variant(orchestrator: Any, constants: Optional[Dict[str, str]]) -> None:
    """Point the orchestrator's prompt attributes at *constants*, or leave the
    working-tree defaults in place when None."""
    if constants is None:
        return
    for attr, const_name in VARIANT_ATTRS:
        if const_name in constants:
            setattr(orchestrator, attr, constants[const_name])
    _set_param_responses(
        orchestrator,
        {
            key: constants[const_name]
            for key, const_name in VARIANT_PARAMS
            if const_name in constants
        },
    )


async def resolve_orchestrator(app_root: str, agent_hint: str) -> Any:
    from jvagent.cli.bootstrap import bootstrap_application_graph
    from jvagent.cli.server import load_app_env
    from jvagent.core.agents import Agents

    # The CLI loads the app-root .env before anything touches a provider; a
    # script that bootstraps the graph directly skips that and every model call
    # goes out with an empty bearer token. Reuse the CLI's loader rather than
    # reimplementing its precedence (operator env wins over .env).
    load_app_env(app_root)
    await bootstrap_application_graph(update_mode="source", app_root=app_root)
    agents = await (await Agents.get()).get_connected_agents()
    agent = next(
        (a for a in agents if agent_hint.lower() in (a.name or "").lower()), agents[0]
    )
    actions = await (await agent.get_actions_manager()).get_all_actions(
        enabled_only=True
    )
    orchestrator = next(
        (a for a in actions if type(a).__name__ == "OrchestratorInteractAction"), None
    )
    if orchestrator is None:
        raise SystemExit(f"agent {agent.name!r} has no enabled orchestrator")
    return orchestrator


def summarize(results: Dict[str, Dict[str, List[bool]]], runs: int) -> None:
    """Per-scenario pass rate for each arm, worst regressions first."""
    scenarios = sorted({s for arm in results.values() for s in arm})
    arms = list(results)
    width = max((len(s) for s in scenarios), default=10)

    rows: List[Tuple[float, str, str]] = []
    for scenario in scenarios:
        rates = {
            arm: 100.0 * sum(results[arm].get(scenario, [])) / max(1, runs)
            for arm in arms
        }
        delta = rates.get("after", 0.0) - rates.get("before", 0.0)
        cells = "  ".join(f"{arm}={rates[arm]:5.1f}%" for arm in arms)
        rows.append(
            (delta, scenario, f"  {scenario:<{width}}  {cells}  Δ={delta:+.1f}pp")
        )

    print(f"\n=== per-scenario pass rate over {runs} run(s) ===")
    for _, _, line in sorted(rows):
        print(line)

    print("\n=== arm totals ===")
    for arm in arms:
        flat = [ok for s in results[arm].values() for ok in s]
        rate = 100.0 * sum(flat) / max(1, len(flat))
        print(
            f"  {arm:<7} {rate:5.1f}%  ({sum(flat)}/{len(flat)} scenario runs passed)"
        )

    if len(arms) == 2 and runs > 1:
        deltas = [d for d, _, _ in rows]
        print(
            f"\n  mean Δ = {statistics.mean(deltas):+.1f}pp, "
            f"worst scenario Δ = {min(deltas):+.1f}pp"
        )
        print(
            "  A small Δ at this sample size means 'no detectable difference', "
            "not 'equivalent'."
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app_root", nargs="?", default="examples/jvagent_app")
    parser.add_argument("--agent", default="orchestrator")
    parser.add_argument(
        "--use-cases",
        default="use-cases/orchestrator-rules",
        help="relative to app_root",
    )
    parser.add_argument("--runs", type=int, default=3, help="runs per arm per scenario")
    parser.add_argument("--model", default="", help="override the heavy model id")
    parser.add_argument("--light-model", default="", help="override the light model id")
    parser.add_argument(
        "--arms",
        default="before,after",
        help="comma-separated arms: before | after | listing-system | listing-trailing",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Estimate the work and exit without calling any model.",
    )
    args = parser.parse_args()

    app_root = os.path.abspath(args.app_root)
    os.chdir(app_root)
    sys.path.insert(0, app_root)

    from jvagent.testing.use_case_loader import discover_use_cases, load_use_case

    _register_reminder_arms()

    paths = discover_use_cases(args.use_cases)
    scenarios = [load_use_case(p) for p in paths]
    if not scenarios:
        raise SystemExit(f"no scenarios under {args.use_cases}")
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    turns = sum(len(s.get("turns") or []) for s in scenarios)

    print(
        f"scenarios: {len(scenarios)}  turns: {turns}  arms: {arms}  runs: {args.runs}"
    )
    total_turns = turns * len(arms) * args.runs
    print(
        f"total live turns: {total_turns}  "
        f"(each is 1+ model calls; a 3-6 tick turn is ~3k input tokens per tick)"
    )
    if args.dry_run:
        print("\ndry run — nothing was called and nothing was billed.")
        for s in scenarios:
            print(f"  - {s['id']}: {s['title']}")
        return

    from jvagent.testing.live_runner import LiveScenarioRunner

    orchestrator = await resolve_orchestrator(app_root, args.agent)
    model_action = await orchestrator.get_model_action(required=False)
    if model_action is not None and not model_action.api_key_from_context(
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY"
    ):
        raise SystemExit(
            "no API key resolved for the orchestrator's model action — check "
            f"{os.path.join(app_root, '.env')}. Refusing to run: every turn "
            "would fail with an empty bearer token."
        )
    if args.model:
        orchestrator.model = args.model
    if args.light_model:
        orchestrator.light_model = args.light_model

    baseline = {attr: getattr(orchestrator, attr) for attr, _ in VARIANT_ATTRS}
    param_baseline = _param_responses(orchestrator)
    attribute_baseline = {
        attr: getattr(orchestrator, attr, None)
        for overrides in ATTRIBUTE_ARMS.values()
        for attr in overrides
    }
    unknown = [
        a for a in arms if a not in ("before", "after") and a not in ATTRIBUTE_ARMS
    ]
    if unknown:
        raise SystemExit(
            f"unknown arm(s) {unknown}; known: before, after, "
            f"{', '.join(sorted(ATTRIBUTE_ARMS))}"
        )
    before_constants = load_prior_prompt_constants(CONSOLIDATION_COMMIT)
    missing = [n for n in ALL_VARIANT_CONSTANTS if n not in before_constants]
    absent = [k for k, _ in VARIANT_PARAMS if k not in param_baseline]
    if absent:
        raise SystemExit(
            f"parameters {absent} are not on the orchestrator; the 'before' arm "
            "cannot restore text it has nowhere to put. Re-bootstrap the app "
            "with --update --source so the graph matches the current code."
        )
    if missing:
        print(f"  note: no pre-consolidation text for {missing} — arm uses current")

    results: Dict[str, Dict[str, List[bool]]] = {a: defaultdict(list) for a in arms}
    runner = LiveScenarioRunner(orchestrator)

    for arm in arms:
        # Always start each arm from the working-tree defaults so arms cannot
        # contaminate each other, then apply exactly that arm's override.
        for attr, value in baseline.items():
            setattr(orchestrator, attr, value)
        _set_param_responses(orchestrator, param_baseline)
        for attr, value in attribute_baseline.items():
            setattr(orchestrator, attr, value)
        if arm == "before":
            apply_variant(orchestrator, before_constants)
        for attr, value in ATTRIBUTE_ARMS.get(arm, {}).items():
            setattr(orchestrator, attr, value)
        print(f"\n--- arm: {arm} ---")
        for run in range(args.runs):
            for scenario in scenarios:
                result = await runner.run(scenario)
                results[arm][scenario["id"]].append(result.passed)
                mark = "ok " if result.passed else "FAIL"
                print(f"  [{arm} run{run}] {mark} {scenario['id']}")
                for failure in result.failures:
                    print(f"        {failure}")

    for attr, value in baseline.items():
        setattr(orchestrator, attr, value)
    for attr, value in attribute_baseline.items():
        setattr(orchestrator, attr, value)
    summarize({a: dict(results[a]) for a in arms}, args.runs)


if __name__ == "__main__":
    asyncio.run(main())
