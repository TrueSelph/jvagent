"""Pattern matrix smoke harness (BRIDGE-ROADMAP §J).

Runs the canonical 6-utterance suite against each enabled agent in the
example app and emits a side-by-side comparison table. Designed to
populate the performance ledger in
``.planning/PATTERNS.md``.

Configurations the matrix supports today:

- ``cockpit_agent``                     — the cockpit baseline pattern.
- ``bridge_agent`` (default)            — bridge with whatever helm composition
                                          the YAML declares. Toggle Reflex /
                                          Reasoning / Persona in
                                          ``examples/jvagent_app/agents/jvagent/
                                          bridge_agent/agent.yaml`` and re-run
                                          for each cell of the matrix.

The four canonical matrix cells from BRIDGE-ROADMAP §J:

1. Cockpit (control)                    — ``cockpit_agent``
2. Bridge + Reasoning                   — bridge_agent, ``helms: [ReasoningHelm]``
3. Bridge + Reflex + Reasoning          — bridge_agent, ``helms: [ReflexHelm, ReasoningHelm]``
4. Bridge + Reflex + Reasoning + Specialist
                                        — bridge_agent with an Interview /
                                          Specialist IA in the chain

(The "Bridge + Reflex + Reasoning + Persona" cell was retired when
PersonaHelm was scrapped — persona stylisation now happens directly
inside Bridge via ``deliver_via_persona`` and isn't a separate
matrix axis.)

Workflow:

    1. Enable both ``bridge_agent`` and ``cockpit_agent`` in
       ``examples/jvagent_app/app.yaml``.
    2. Edit ``bridge_agent.yaml`` to the matrix cell you want to measure.
    3. Run::

           .venv/bin/python tests/action/bridge/smoke_pattern_matrix.py \\
               --agents bridge_agent cockpit_agent \\
               --label "bridge_reflex_reasoning_vs_cockpit" \\
               --json

    4. JSON output archived under ``tests/action/bridge/baselines/
       matrix_<label>_<short_sha>.json``.
    5. Copy the comparison row into ``.planning/PATTERNS.md`` ledger.

Exit codes:
    0 — all configurations completed without errors.
    1 — at least one configuration raised on the suite.
    2 — bootstrap / env failure.

This is NOT a pytest test (no ``test_`` prefix). Invoke directly.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# The 6-utterance suite (mirrors ``smoke_bridge.py`` + ``smoke_real_lm.py``).
DEFAULT_UTTERANCES: List[Dict[str, str]] = [
    {"label": "greeting", "utterance": "Hi"},
    {
        "label": "informational_simple",
        "utterance": "What is 2 + 2? Answer in one word.",
    },
    {
        "label": "directive_web_search",
        "utterance": "Search the web for the most recent stable release of Python.",
    },
    {
        "label": "directive_remember_pref",
        "utterance": "Remember that I prefer Python over JavaScript for new projects.",
    },
    {
        "label": "informational_recall",
        "utterance": "What do you remember about my language preferences?",
    },
    {"label": "thanks_followup", "utterance": "Thanks!"},
]


# ---------------------------------------------------------------------------
# Bootstrap helpers
# ---------------------------------------------------------------------------


def _resolve_app_root(arg: Optional[str]) -> Path:
    if arg:
        p = Path(arg).expanduser().resolve()
    else:
        repo_root = Path(__file__).resolve().parents[3]
        p = (repo_root / "examples" / "jvagent_app").resolve()
    if not p.is_dir():
        raise SystemExit(f"App root not found: {p}")
    if not (p / "app.yaml").is_file():
        raise SystemExit(f"app.yaml missing at {p}")
    return p


def _load_dotenv(app_root: Path) -> None:
    env_file = app_root / ".env"
    if not env_file.is_file():
        return
    from dotenv import load_dotenv

    load_dotenv(env_file, override=True)


async def _bootstrap_app(app_root: Path) -> None:
    from jvagent.cli.server_config import _set_db_env_from_config

    _set_db_env_from_config(str(app_root))

    from jvagent.cli.bootstrap import bootstrap_application_graph
    from jvagent.core.index_bootstrap import run_index_migration

    await run_index_migration()
    await bootstrap_application_graph(update_mode=None, app_root=str(app_root))


async def _resolve_agent(name: str) -> Any:
    from jvagent.core.agent import Agent

    agents = await Agent.find({"context.name": name})
    if not agents:
        raise SystemExit(
            f"agent {name!r} not found in graph — enable it in app.yaml + "
            f"run `jvagent --update --source` first"
        )
    return agents[0]


# ---------------------------------------------------------------------------
# Per-utterance run (reuses the same shape as smoke_bridge / smoke_real_lm)
# ---------------------------------------------------------------------------


async def _run_one(
    agent: Any,
    *,
    label: str,
    utterance: str,
    session_id: Optional[str],
    user_id: Optional[str],
) -> Dict[str, Any]:
    from jvspatial import flush_deferred_entities

    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.action.model.context import set_interaction

    walker = InteractWalker(
        agent_id=agent.id,
        utterance=utterance,
        channel="default",
        data={},
        session_id=session_id,
        user_id=user_id,
        stream=False,
    )

    t0 = time.monotonic()
    err: Optional[str] = None
    try:
        await walker.spawn(agent)
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
    duration = time.monotonic() - t0

    interaction = walker.interaction
    response = ""
    metrics: List[Dict[str, Any]] = []
    actions_executed: List[str] = []
    bridge_observability: Dict[str, Any] = {}

    if interaction is not None:
        try:
            interaction.streamed = False
            await interaction.close_interaction()
            await flush_deferred_entities(
                interaction, walker.conversation, strict=False
            )
        except Exception:
            pass
        set_interaction(None)
        response = (interaction.response or "").strip()
        metrics = list(getattr(interaction, "observability_metrics", []) or [])
        actions_executed = list(getattr(interaction, "actions", []) or [])
        try:
            params = getattr(interaction, "parameters", None) or {}
            if isinstance(params, dict):
                bridge_observability = params.get("bridge_observability", {}) or {}
        except Exception:
            bridge_observability = {}

    def _data(m: Dict[str, Any]) -> Dict[str, Any]:
        return m.get("data", m) or {}

    total_tokens = sum(
        int(_data(m).get("usage", {}).get("total_tokens") or 0) for m in metrics
    )
    prompt_tokens = sum(
        int(_data(m).get("usage", {}).get("prompt_tokens") or 0) for m in metrics
    )
    completion_tokens = sum(
        int(_data(m).get("usage", {}).get("completion_tokens") or 0) for m in metrics
    )
    model_calls = len([m for m in metrics if "usage" in _data(m)])
    helm_shift_events = [m for m in metrics if m.get("event_type") == "helm_shift"]

    return {
        "label": label,
        "utterance": utterance,
        "duration_s": round(duration, 3),
        "session_id": walker.session_id,
        "user_id": walker.user_id,
        "actions_executed": actions_executed,
        "model_calls": model_calls,
        "total_tokens": total_tokens,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "response_chars": len(response),
        "response_preview": response[:160],
        "helm_shift_count": len(helm_shift_events),
        "bridge_observability": bridge_observability,
        "error": err,
    }


async def _run_suite(agent: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    for spec in DEFAULT_UTTERANCES:
        row = await _run_one(
            agent,
            label=spec["label"],
            utterance=spec["utterance"],
            session_id=session_id,
            user_id=user_id,
        )
        session_id = row.get("session_id") or session_id
        user_id = row.get("user_id") or user_id
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Comparison + reporting
# ---------------------------------------------------------------------------


def _summarise(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    """Aggregate the 6-utterance suite into headline metrics."""
    total_dur = sum(r["duration_s"] for r in rows)
    total_calls = sum(r["model_calls"] for r in rows)
    total_prompt = sum(r["prompt_tokens"] for r in rows)
    total_completion = sum(r["completion_tokens"] for r in rows)
    total_tokens = sum(r["total_tokens"] for r in rows)
    trivial_durations = [
        r["duration_s"]
        for r in rows
        if r["label"] in {"greeting", "informational_simple", "thanks_followup"}
    ]
    median_trivial = (
        sorted(trivial_durations)[len(trivial_durations) // 2]
        if trivial_durations
        else 0.0
    )
    p99_dur = max((r["duration_s"] for r in rows), default=0.0)
    return {
        "total_dur_s": round(total_dur, 3),
        "total_calls": total_calls,
        "total_prompt_tokens": total_prompt,
        "total_completion_tokens": total_completion,
        "total_tokens": total_tokens,
        "median_trivial_dur_s": round(median_trivial, 3),
        "p99_dur_s": round(p99_dur, 3),
    }


def _print_matrix_table(results: Dict[str, Dict[str, Any]]) -> None:
    print()
    print("=" * 120)
    print("PATTERN MATRIX — totals per config")
    print("=" * 120)
    header = (
        f"{'agent':<28} {'tot_dur(s)':>10} {'calls':>6} {'p_tok':>8} "
        f"{'c_tok':>8} {'tot_tok':>8} {'trivial_p50':>12} {'p99_dur':>10}"
    )
    print(header)
    print("-" * 120)
    for agent_name, payload in results.items():
        if payload.get("error"):
            print(f"{agent_name:<28} ERROR — {payload['error']}")
            continue
        s = payload["summary"]
        print(
            f"{agent_name:<28} {s['total_dur_s']:>10.3f} {s['total_calls']:>6} "
            f"{s['total_prompt_tokens']:>8} {s['total_completion_tokens']:>8} "
            f"{s['total_tokens']:>8} {s['median_trivial_dur_s']:>12.3f} "
            f"{s['p99_dur_s']:>10.3f}"
        )
    print("-" * 120)


def _print_per_utterance(results: Dict[str, Dict[str, Any]]) -> None:
    """Side-by-side per-utterance latency + call counts across configs."""
    print()
    print("=" * 120)
    print("PER-UTTERANCE — duration / calls / prompt_tokens")
    print("=" * 120)
    labels = [u["label"] for u in DEFAULT_UTTERANCES]
    agents = list(results.keys())
    col_w = 26
    header = f"{'label':<26}" + "".join(f"{a:<{col_w}}" for a in agents)
    print(header)
    print("-" * 120)
    for lbl in labels:
        cells: List[str] = []
        for a in agents:
            payload = results[a]
            if payload.get("error"):
                cells.append(f"{'ERROR':<{col_w}}")
                continue
            row = next((r for r in payload["rows"] if r["label"] == lbl), None)
            if row is None:
                cells.append(f"{'-':<{col_w}}")
                continue
            cell = (
                f"{row['duration_s']:>5.2f}s "
                f"calls={row['model_calls']} "
                f"p_tok={row['prompt_tokens']:>5}"
            )
            cells.append(f"{cell:<{col_w}}")
        print(f"{lbl:<26}" + "".join(cells))
    print("-" * 120)


def _short_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parents[3],
            stderr=subprocess.DEVNULL,
        )
        return out.decode().strip() or "nogit"
    except Exception:
        return "nogit"


def _archive(label: str, results: Dict[str, Dict[str, Any]]) -> Path:
    baselines_dir = Path(__file__).resolve().parent / "baselines"
    baselines_dir.mkdir(parents=True, exist_ok=True)
    sha = _short_sha()
    out_file = baselines_dir / f"matrix_{label}_{sha}.json"
    payload = {
        "label": label,
        "commit": sha,
        "wall_clock_unix": time.time(),
        "results": results,
    }
    out_file.write_text(json.dumps(payload, indent=2, default=str))
    return out_file


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def _main_async(args: argparse.Namespace) -> int:
    app_root = _resolve_app_root(args.app_root)
    _load_dotenv(app_root)

    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s %(name)s | %(message)s",
    )
    if args.verbose:
        logging.getLogger("jvagent").setLevel(logging.INFO)

    print(f"App root: {app_root}")
    print("Bootstrapping...")
    try:
        await _bootstrap_app(app_root)
    except Exception as exc:
        print(f"Bootstrap failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    results: Dict[str, Dict[str, Any]] = {}
    any_error = False
    for name in args.agents:
        print(f"\n--- agent: {name} ---")
        try:
            agent = await _resolve_agent(name)
        except SystemExit as exc:
            print(f"  resolve failed: {exc}", file=sys.stderr)
            results[name] = {"error": str(exc)}
            any_error = True
            continue
        try:
            rows = await _run_suite(agent)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            print(f"  suite raised: {err}", file=sys.stderr)
            results[name] = {"error": err}
            any_error = True
            continue
        results[name] = {
            "rows": rows,
            "summary": _summarise(rows),
        }

    _print_matrix_table(results)
    _print_per_utterance(results)

    archive = _archive(args.label or "matrix", results)
    print(f"\nArchived: {archive}")

    if args.json:
        print(json.dumps(results, indent=2, default=str))

    return 1 if any_error else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pattern matrix smoke harness for Bridge."
    )
    parser.add_argument("app_root", nargs="?", default=None, help="App root path")
    parser.add_argument(
        "--agents",
        nargs="+",
        default=["bridge_agent", "cockpit_agent"],
        help=(
            "Agent names to include in the matrix (default: bridge_agent + "
            "cockpit_agent). The agent must be enabled in app.yaml AND "
            "boot-strapped in the graph."
        ),
    )
    parser.add_argument(
        "--label",
        default="default",
        help=(
            "Short label baked into the archive filename "
            "(``matrix_<label>_<short_sha>.json``)"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Dump full per-config results as JSON to stdout in addition to archiving",
    )
    parser.add_argument("--verbose", action="store_true", help="jvagent INFO logging")
    args = parser.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
