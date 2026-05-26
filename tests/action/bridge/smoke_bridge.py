"""Real-LM smoke harness for ``bridge_agent`` (BRIDGE-ROADMAP §C-7).

Mirrors ``tests/action/cockpit/smoke_real_lm.py`` but targets
``examples/jvagent_app/agents/jvagent/bridge_agent`` so the Bridge +
ReasoningHelm composition can be compared against the cockpit baseline at
commit ``7d95904`` for parity validation.

Default behaviour: run the 6-utterance suite, print a per-utterance
metrics table, archive the JSON dump under
``tests/action/bridge/baselines/<short_sha>.json``, and exit with status:

- ``0`` on green parity (every metric within 5% drift vs baseline).
- ``1`` on drift past the parity tolerance.
- ``2`` on bootstrap or environment failure.

This is **NOT** a pytest test (no ``test_`` prefix). Invoke directly::

    .venv/bin/python tests/action/bridge/smoke_bridge.py [APP_ROOT] [options]

Required env vars (loaded from ``<APP_ROOT>/.env``):
    OPENAI_API_KEY, SERPER_API_KEY, OLLAMA_API_KEY (optional),
    JVAGENT_ADMIN_PASSWORD

Defaults APP_ROOT to ``examples/jvagent_app`` relative to repo root.

Useful flags:
    --utterance "..."   Single utterance, skips parity comparison.
    --json              Dump per-utterance JSON to stdout + archive file.
    --no-parity         Skip parity gating (print table only, exit 0).
    --tolerance 0.05    Override parity tolerance (default 0.05 = 5%).
    --verbose           jvagent INFO logging.
    --debug             ReasoningHelm DEBUG logging.
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

# ---------------------------------------------------------------------------
# Default utterance suite — mirrors cockpit smoke_real_lm.py 1:1 so the
# parity gate has like-for-like inputs.
# ---------------------------------------------------------------------------

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
    {
        "label": "thanks_followup",
        "utterance": "Thanks!",
    },
]


# ---------------------------------------------------------------------------
# Cockpit baseline at commit 7d95904 (from BRIDGE-ROADMAP §Baseline).
#
# Keyed by ``label`` so per-utterance comparison is unambiguous even if the
# suite reorders. Field semantics match the smoke harness output below.
# ---------------------------------------------------------------------------

BASELINE_7D95904: Dict[str, Dict[str, float]] = {
    "greeting": {
        "duration_s": 2.93,
        "model_calls": 2,
        "prompt_tokens": 2014,
        "response_chars": 34,
    },
    "informational_simple": {
        "duration_s": 2.79,
        "model_calls": 2,
        "prompt_tokens": 4956,
        "response_chars": 5,
    },
    "directive_web_search": {
        "duration_s": 5.89,
        "model_calls": 3,
        "prompt_tokens": 8342,
        "response_chars": 167,
    },
    "directive_remember_pref": {
        "duration_s": 9.29,
        "model_calls": 3,
        "prompt_tokens": 8260,
        "response_chars": 139,
    },
    "informational_recall": {
        "duration_s": 8.70,
        "model_calls": 3,
        "prompt_tokens": 8342,
        "response_chars": 183,
    },
    "thanks_followup": {
        "duration_s": 3.55,
        "model_calls": 2,
        "prompt_tokens": 2180,
        "response_chars": 79,
    },
}

# Parity tolerance: each metric within ±N% of the cockpit baseline.
DEFAULT_TOLERANCE: float = 0.05  # 5%

# Metrics that participate in the parity check. ``model_calls`` is an
# integer ledger field — drift of 1 may be acceptable depending on
# router cache state; treat it as informational-only and require exact
# match for tighter comparison.
PARITY_METRICS: List[str] = [
    "duration_s",
    "model_calls",
    "prompt_tokens",
    "response_chars",
]


# ---------------------------------------------------------------------------
# Bootstrap helpers (lifted from cockpit smoke; no source-level coupling —
# just shape parity for ease of maintenance).
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


async def _bootstrap_app(app_root: Path) -> Any:
    """Bootstrap the app graph and return the bridge_agent node."""
    from jvagent.cli.server_config import _set_db_env_from_config

    _set_db_env_from_config(str(app_root))

    from jvagent.cli.bootstrap import bootstrap_application_graph
    from jvagent.core.index_bootstrap import run_index_migration

    await run_index_migration()
    await bootstrap_application_graph(update_mode=None, app_root=str(app_root))

    return await _resolve_bridge_agent()


async def _resolve_bridge_agent() -> Any:
    from jvagent.core.agent import Agent

    agents = await Agent.find({"context.name": "bridge_agent"})
    if not agents:
        raise SystemExit("bridge_agent not found in graph — did bootstrap run?")
    return agents[0]


# ---------------------------------------------------------------------------
# Per-utterance run (same shape as cockpit smoke for ease of comparison)
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
    tasks_payload: List[Dict[str, Any]] = []
    actions_executed: List[str] = []

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
            tasks_payload = list(getattr(interaction, "tasks", []) or [])
        except Exception:
            tasks_payload = []

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
    callers = [_data(m).get("called_by") or "?" for m in metrics]

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
        "callers": callers,
        "response_chars": len(response),
        "response_preview": response[:160],
        "tasks_count": len(tasks_payload),
        "error": err,
    }


# ---------------------------------------------------------------------------
# Parity comparison
# ---------------------------------------------------------------------------


def _drift_pct(observed: float, baseline: float) -> Optional[float]:
    """Return signed drift fraction (observed - baseline) / baseline.

    Returns ``None`` when baseline is zero (no meaningful percentage).
    """
    if baseline == 0:
        return None
    return (observed - baseline) / baseline


def _format_drift(d: Optional[float]) -> str:
    if d is None:
        return "n/a"
    pct = d * 100
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


def _evaluate_parity(
    rows: List[Dict[str, Any]],
    *,
    tolerance: float,
) -> Dict[str, Any]:
    """Compare smoke results against ``BASELINE_7D95904``.

    Returns a structured report:

    {
        "label": {metric: {observed, baseline, drift, within_tol}, ...},
        ...,
        "_summary": {"green": bool, "breaches": [(label, metric, drift), ...]},
    }
    """
    report: Dict[str, Any] = {}
    breaches: List[Dict[str, Any]] = []

    for row in rows:
        label = row["label"]
        baseline = BASELINE_7D95904.get(label)
        if baseline is None:
            continue
        per_metric: Dict[str, Any] = {}
        for metric in PARITY_METRICS:
            observed = float(row.get(metric, 0))
            base = float(baseline.get(metric, 0))
            drift = _drift_pct(observed, base)
            within = True if drift is None else abs(drift) <= tolerance
            per_metric[metric] = {
                "observed": observed,
                "baseline": base,
                "drift": drift,
                "within_tol": within,
            }
            if drift is not None and not within:
                breaches.append(
                    {
                        "label": label,
                        "metric": metric,
                        "observed": observed,
                        "baseline": base,
                        "drift": drift,
                    }
                )
        report[label] = per_metric

    report["_summary"] = {
        "tolerance": tolerance,
        "green": not breaches,
        "breaches": breaches,
        "baseline_commit": "7d95904",
    }
    return report


# ---------------------------------------------------------------------------
# Pretty printing
# ---------------------------------------------------------------------------


def _print_summary(rows: List[Dict[str, Any]]) -> None:
    print()
    print("=" * 100)
    print("BRIDGE SMOKE — per-utterance metrics (bridge_agent)")
    print("=" * 100)
    header = (
        f"{'label':<26} {'dur(s)':>7} {'calls':>5} {'p_tok':>6} {'c_tok':>6} "
        f"{'tot_tok':>7} {'resp_ch':>7}  preview"
    )
    print(header)
    print("-" * 120)
    for r in rows:
        preview = r["response_preview"].replace("\n", " ⏎ ")
        if r["error"]:
            preview = f"[ERR] {r['error']}"
        print(
            f"{r['label']:<26} {r['duration_s']:>7.3f} {r['model_calls']:>5} "
            f"{r['prompt_tokens']:>6} {r['completion_tokens']:>6} "
            f"{r['total_tokens']:>7} {r['response_chars']:>7}  {preview}"
        )
    print("-" * 120)

    total_dur = sum(r["duration_s"] for r in rows)
    total_calls = sum(r["model_calls"] for r in rows)
    total_p_tok = sum(r["prompt_tokens"] for r in rows)
    total_c_tok = sum(r["completion_tokens"] for r in rows)
    total_tok = sum(r["total_tokens"] for r in rows)
    print(
        f"TOTALS  dur={total_dur:.3f}s  model_calls={total_calls}  "
        f"prompt={total_p_tok}  completion={total_c_tok}  total_tokens={total_tok}"
    )
    print()


def _print_parity(report: Dict[str, Any]) -> None:
    summary = report["_summary"]
    print("=" * 100)
    print(
        f"PARITY vs cockpit baseline {summary['baseline_commit']} "
        f"(tolerance ±{summary['tolerance']*100:.1f}%)"
    )
    print("=" * 100)
    header = (
        f"{'label':<26} {'metric':<14} {'observed':>10} {'baseline':>10} "
        f"{'drift':>10}  status"
    )
    print(header)
    print("-" * 100)
    for label, per_metric in report.items():
        if label.startswith("_"):
            continue
        for metric, entry in per_metric.items():
            status = "OK" if entry["within_tol"] else "BREACH"
            print(
                f"{label:<26} {metric:<14} "
                f"{entry['observed']:>10.2f} {entry['baseline']:>10.2f} "
                f"{_format_drift(entry['drift']):>10}  {status}"
            )
    print("-" * 100)
    if summary["green"]:
        print("PARITY: GREEN — all metrics within tolerance.")
    else:
        print(
            f"PARITY: RED — {len(summary['breaches'])} breach(es). "
            "See rows marked BREACH above."
        )
    print()


# ---------------------------------------------------------------------------
# Baselines directory
# ---------------------------------------------------------------------------


def _short_sha() -> str:
    """Best-effort current commit short SHA. Returns 'nogit' on failure."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parents[3],
            stderr=subprocess.DEVNULL,
        )
        return out.decode().strip() or "nogit"
    except Exception:
        return "nogit"


def _archive_run(rows: List[Dict[str, Any]], report: Dict[str, Any]) -> Path:
    baselines_dir = Path(__file__).resolve().parent / "baselines"
    baselines_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "commit": _short_sha(),
        "agent": "bridge_agent",
        "rows": rows,
        "parity": report,
        "wall_clock_unix": time.time(),
    }
    out_file = baselines_dir / f"{payload['commit']}.json"
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
    if args.debug:
        logging.getLogger("jvagent.action.helm.reasoning").setLevel(logging.DEBUG)

    print(f"App root: {app_root}")
    print("Bootstrapping...")
    t_boot = time.monotonic()
    try:
        agent = await _bootstrap_app(app_root)
    except SystemExit:
        return 2
    except Exception as exc:
        print(f"Bootstrap failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    boot_dur = time.monotonic() - t_boot
    print(f"Bootstrap OK in {boot_dur:.2f}s — bridge_agent id={agent.id}")

    if args.utterance:
        utterances = [{"label": "custom", "utterance": args.utterance}]
    else:
        utterances = DEFAULT_UTTERANCES

    rows: List[Dict[str, Any]] = []
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    for spec in utterances:
        print(f"\n>>> [{spec['label']}] {spec['utterance']}")
        row = await _run_one(
            agent,
            label=spec["label"],
            utterance=spec["utterance"],
            session_id=session_id,
            user_id=user_id,
        )
        # Re-use the session/user across the suite so memory recall makes sense.
        session_id = row.get("session_id") or session_id
        user_id = row.get("user_id") or user_id
        rows.append(row)
        if args.json:
            print(json.dumps(row, indent=2, default=str))

    _print_summary(rows)

    # Parity gate (skipped for custom-utterance / no-parity runs).
    is_custom = bool(args.utterance)
    if is_custom or args.no_parity:
        if args.json:
            archive = _archive_run(rows, {"_summary": {"green": True, "breaches": []}})
            print(f"\nArchived: {archive}")
        return 0

    report = _evaluate_parity(rows, tolerance=args.tolerance)
    _print_parity(report)
    archive = _archive_run(rows, report)
    print(f"Archived: {archive}")

    return 0 if report["_summary"]["green"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Real-LM smoke harness for bridge_agent."
    )
    parser.add_argument("app_root", nargs="?", default=None, help="App root path")
    parser.add_argument(
        "--utterance",
        default=None,
        help="Single utterance to send (overrides default suite; skips parity)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Dump per-utterance metrics as JSON to stdout + archive",
    )
    parser.add_argument(
        "--no-parity",
        action="store_true",
        help="Skip parity gating (print table only)",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=DEFAULT_TOLERANCE,
        help=f"Parity tolerance as a fraction (default {DEFAULT_TOLERANCE})",
    )
    parser.add_argument("--verbose", action="store_true", help="jvagent INFO logging")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="ReasoningHelm DEBUG logging (engine prompt-size telemetry)",
    )
    args = parser.parse_args()

    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
