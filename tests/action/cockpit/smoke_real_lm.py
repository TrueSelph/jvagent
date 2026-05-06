"""Real-LM smoke harness for cockpit_agent.

Boots the example jvagent app graph (``examples/jvagent_app``), sends a
curated list of utterances through ``cockpit_agent`` via ``InteractWalker``,
and prints a metrics table per utterance. Designed for iterative use during
cockpit development — run it before/after a change and diff the output.

This is **NOT** a pytest test (no ``test_`` prefix). Invoke directly::

    .venv/bin/python tests/action/cockpit/smoke_real_lm.py [APP_ROOT] [--utterance "..."]

Required env vars (loaded from ``<APP_ROOT>/.env``):
    OPENAI_API_KEY, SERPER_API_KEY, OLLAMA_API_KEY (optional), JVAGENT_ADMIN_PASSWORD

Defaults APP_ROOT to ``examples/jvagent_app`` relative to repo root.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Default utterance suite — covers the routing/dispatch surface
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


async def _bootstrap_app(app_root: Path) -> Any:
    """Bootstrap the app graph and return the cockpit_agent node."""
    # Ensure DB env vars are set the same way the CLI does.
    from jvagent.cli.server_config import _set_db_env_from_config

    _set_db_env_from_config(str(app_root))

    from jvagent.cli.bootstrap import bootstrap_application_graph
    from jvagent.core.index_bootstrap import run_index_migration

    await run_index_migration()
    await bootstrap_application_graph(update_mode=None, app_root=str(app_root))

    # ensure_admin_user / run_app_startup require a Server context; skip for smoke.
    return await _resolve_cockpit_agent()


async def _resolve_cockpit_agent() -> Any:
    from jvagent.core.agent import Agent

    agents = await Agent.find({"context.name": "cockpit_agent"})
    if not agents:
        raise SystemExit("cockpit_agent not found in graph — did bootstrap run?")
    return agents[0]


# ---------------------------------------------------------------------------
# Per-utterance run
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
        tasks_payload = []
        try:
            tasks_payload = list(getattr(interaction, "tasks", []) or [])
        except Exception:
            pass

    # Metric shape: {event_type, data: {usage, called_by, ...}, timestamp}
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
# Pretty printing
# ---------------------------------------------------------------------------


def _print_summary(rows: List[Dict[str, Any]]) -> None:
    print()
    print("=" * 100)
    print("COCKPIT SMOKE — per-utterance metrics")
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
        logging.getLogger("jvagent.action.cockpit").setLevel(logging.DEBUG)

    print(f"App root: {app_root}")
    print("Bootstrapping...")
    t_boot = time.monotonic()
    agent = await _bootstrap_app(app_root)
    boot_dur = time.monotonic() - t_boot
    print(f"Bootstrap OK in {boot_dur:.2f}s — cockpit_agent id={agent.id}")

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

    if args.json:
        out_file = app_root / "cockpit_smoke_last_run.json"
        out_file.write_text(json.dumps(rows, indent=2, default=str))
        print(f"\nFull JSON trace: {out_file}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Real-LM smoke harness for cockpit_agent."
    )
    parser.add_argument("app_root", nargs="?", default=None, help="App root path")
    parser.add_argument(
        "--utterance",
        default=None,
        help="Single utterance to send (overrides default suite)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Dump full per-utterance metrics as JSON to stdout + a file",
    )
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Cockpit DEBUG logging (prompt size telemetry)",
    )
    args = parser.parse_args()

    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
