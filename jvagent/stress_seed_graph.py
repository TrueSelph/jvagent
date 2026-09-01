"""Seed the app graph with synthetic users, long-memory category nodes, and interactions.

Two entry points:
- ``async execute_stress_seed_graph`` — graph write only; use after the process has already
created the server and run ``pre_startup_bootstrap`` (e.g. ``jvagent --stress-seed``).
- ``main`` / ``async _run`` — standalone CLI: creates server, optional graph bootstrap, then
seeds (does not start the HTTP server unless combined with a separate ``jvagent`` run).
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from jvagent.core.agent import Agent
from jvagent.core.agents import Agents
from jvagent.core.app import App

logger = logging.getLogger(__name__)

# argv tokens that are never app-root path segments in shared CLI path parsing
STRESS_FLAG_NAMES = (
    "--stress-seed",
    "--user-memory-nodes",
    "--interactions-per-user-memory-node",
    "--user-id-prefix",
    "--agent",
    "--no-bootstrap",
    "--pre-startup",
    "--progress-every",
)

# For ``jvagent`` / ``run`` only: optional env defaults when using ``--stress-seed`` without
# explicit N/M.
ENV_STRESS_NODES = "JVAGENT_STRESS_SEED_USER_MEMORY_NODES"
ENV_STRESS_I_PER = "JVAGENT_STRESS_SEED_INTERACTIONS_PER_USER_MEMORY_NODE"


@dataclass(frozen=True)
class StressSeedConfig:
    """Parameters for one stress-seed run (used by the server and the standalone subcommand)."""

    user_memory_nodes: int
    interactions_per_user_memory_node: int
    agent: Optional[str] = None
    user_id_prefix: str = "graph_stress"
    progress_every: int = 100
    # Standalone / advanced only; ignored when run from ``run_server`` (bootstrap already done)
    no_bootstrap: bool = False
    pre_startup: bool = False


def _parse_agent_arg(raw: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Return (namespace, name) for ``ns/name``; else (None, raw)."""
    if not raw or not str(raw).strip():
        return (None, None)
    s = str(raw).strip()
    if "/" in s:
        parts = s.split("/", 1)
        if len(parts) == 2 and parts[0] and parts[1]:
            return (parts[0].strip(), parts[1].strip())
    return (None, s)


async def _all_agent_candidates() -> List[Agent]:
    app = await App.get()
    if not app:
        return []
    agents_node = await app.node(node=Agents)
    if agents_node:
        connected = await agents_node.get_connected_agents()
        if connected:
            return connected
    return list(await Agent.find({}))


def _format_agent_choices(agents: List[Agent]) -> str:
    return ", ".join(
        f"{a.namespace}/{a.name}"
        for a in sorted(agents, key=lambda x: (x.namespace, x.name))
    )


def _match_agent(
    agents: List[Agent], namespace: Optional[str], name: Optional[str]
) -> Agent:
    if not name:
        raise SystemExit("Internal error: empty agent name after parsing.")

    if namespace:
        for a in agents:
            if a.namespace == namespace and a.name == name:
                return a
        for a in agents:
            if a.namespace == namespace and a.name.lower() == name.lower():
                return a
    else:
        for a in agents:
            if a.name == name:
                return a
        for a in agents:
            if a.name.lower() == name.lower():
                return a

    all_names = [a.name for a in agents]
    close = difflib.get_close_matches(name, all_names, n=1, cutoff=0.72)
    hint = f" Did you mean {close[0]!r}?" if close else ""

    raise SystemExit(
        f"No agent named {name!r}"
        f"{f' in namespace {namespace!r}' if namespace else ''}. "
        f"Known: {_format_agent_choices(agents)}.{hint} "
        f"Use the machine name (e.g. skills_agent) or jvagent/skills_agent."
    )


def _select_default_agent(agents: List[Agent]) -> Agent:
    if not agents:
        raise SystemExit(
            "No agents available; install at least one before stress-seeding."
        )
    enabled = [a for a in agents if a.enabled]
    if enabled:
        return enabled[0]
    return agents[0]


async def _resolve_agent(raw: Optional[str]) -> Agent:
    if not await App.get():
        raise SystemExit(
            "No App node: run `jvagent bootstrap` from the app root first."
        )
    agents = await _all_agent_candidates()
    if not agents:
        raise SystemExit(
            "No agents installed. Add agents in app.yaml and run `jvagent bootstrap --update`."
        )

    if not raw or not str(raw).strip():
        return _select_default_agent(agents)

    ns, name = _parse_agent_arg(raw)
    assert name is not None
    return _match_agent(agents, ns, name)


async def execute_stress_seed_graph(config: StressSeedConfig) -> dict:
    """Write stress users and Interaction chains into the current graph.

    Call this only after the process has set up :class:`jvspatial` default context
    (e.g. after ``create_server_from_config`` and ``pre_startup_bootstrap``). Does not
    start or stop the HTTP server.

    Returns:
        Summary dict with keys ``agent`` (str), ``user_memory_nodes``, ``total_interactions``,
        ``elapsed_s``, ``ok`` (True).
    """
    if config.user_memory_nodes < 1:
        raise ValueError("user_memory_nodes must be at least 1")
    if config.interactions_per_user_memory_node < 1:
        raise ValueError("interactions_per_user_memory_node must be at least 1")

    os.environ.setdefault("JVSPATIAL_ENABLE_DEFERRED_SAVES", "false")

    if not await App.get():
        raise SystemExit("No App: bootstrap failed or graph context is not set.")

    agent = await _resolve_agent(config.agent)
    memory = await agent.get_memory()
    if not memory:
        raise SystemExit(
            f"Agent {agent.namespace!r}/{agent.name!r} has no Memory node."
        )

    total_interactions = 0
    t0 = time.perf_counter()
    n = config.user_memory_nodes
    m = config.interactions_per_user_memory_node
    prefix = config.user_id_prefix
    pe = config.progress_every

    for i in range(n):
        ext_user_id = f"{prefix}_{i:08d}"
        user = await memory.get_user(ext_user_id, create_if_missing=True)
        if user is None:
            raise SystemExit(f"Could not get or create user {ext_user_id!r}")

        user.memory.setdefault("stress_seed", {})["node_index"] = i
        await user.save()

        session_id = f"stress_sess_{prefix}_{i:08d}"
        conv = await user.create_conversation(
            session_id=session_id,
            channel="default",
            interaction_limit=0,
        )

        for j in range(m):
            await conv.add_interaction(
                utterance=f"[stress] user_memory_node={i} interaction={j}",
                session_id=conv.session_id,
            )
            total_interactions += 1

        if pe > 0 and (i + 1) % pe == 0:
            logger.info(
                "Stress-seed progress: %d / %d user memory nodes (%.1f s)",
                i + 1,
                n,
                time.perf_counter() - t0,
            )

    await memory.refresh_memory_counters_from_graph()

    elapsed = time.perf_counter() - t0
    summary = {
        "ok": True,
        "agent": f"{agent.namespace}/{agent.name}",
        "user_memory_nodes": n,
        "interactions_per_user_memory_node": m,
        "total_interactions": total_interactions,
        "elapsed_s": round(elapsed, 2),
    }
    print(
        f"Stress seed done: agent={summary['agent']!r} "
        f"user_memory_nodes={n} interactions_per_node={m} "
        f"total_interactions={total_interactions} elapsed_s={summary['elapsed_s']}"
    )
    return summary


def parse_stress_seed_for_run(
    args: List[str], *, allow_env_defaults: bool = True
) -> Tuple[Optional[StressSeedConfig], List[str]]:
    """If ``--stress-seed`` is present, build :class:`StressSeedConfig` and return argv without
    those tokens. Otherwise return ``(None, args)`` unchanged.

    ``allow_env_defaults`` supplies N and M from the environment when ``--stress-seed`` is
    set but counts are omitted (only for the ``jvagent`` / default run path).
    """
    if "--stress-seed" not in args:
        return None, list(args)

    out: List[str] = []
    has_seed = False
    n: Optional[int] = None
    m: Optional[int] = None
    agent: Optional[str] = None
    user_id_prefix = "graph_stress"
    pe = 100
    no_bootstrap = False
    pre_start = False

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--stress-seed":
            has_seed = True
            i += 1
        elif a == "--user-memory-nodes" and i + 1 < len(args):
            n = int(args[i + 1])
            i += 2
        elif a == "--interactions-per-user-memory-node" and i + 1 < len(args):
            m = int(args[i + 1])
            i += 2
        elif a == "--agent" and i + 1 < len(args):
            agent = args[i + 1]
            i += 2
        elif a == "--user-id-prefix" and i + 1 < len(args):
            user_id_prefix = args[i + 1]
            i += 2
        elif a == "--progress-every" and i + 1 < len(args):
            pe = int(args[i + 1])
            i += 2
        elif a == "--no-bootstrap":
            no_bootstrap = True
            i += 1
        elif a == "--pre-startup":
            pre_start = True
            i += 1
        else:
            out.append(a)
            i += 1

    if not has_seed:
        return None, list(args)

    if n is None and allow_env_defaults:
        raw = os.environ.get(ENV_STRESS_NODES, "").strip()
        if raw.isdigit():
            n = int(raw)
    if m is None and allow_env_defaults:
        raw = os.environ.get(ENV_STRESS_I_PER, "").strip()
        if raw.isdigit():
            m = int(raw)

    if n is None or m is None:
        sys.exit(
            f"With --stress-seed, set --user-memory-nodes N and "
            f"--interactions-per-user-memory-node M (or {ENV_STRESS_NODES} and {ENV_STRESS_I_PER} in the environment)."
        )

    return (
        StressSeedConfig(
            user_memory_nodes=n,
            interactions_per_user_memory_node=m,
            agent=agent,
            user_id_prefix=user_id_prefix,
            progress_every=pe,
            no_bootstrap=no_bootstrap,
            pre_startup=pre_start,
        ),
        out,
    )


async def _run(
    app_root: str,
    user_memory_nodes: int,
    interactions_per_user_memory_node: int,
    agent_name: Optional[str],
    user_id_prefix: str,
    no_bootstrap: bool,
    pre_startup: bool,
    progress_every: int,
) -> None:
    from jvagent.cli.bootstrap import bootstrap_application_graph
    from jvagent.cli.server_config import (
        create_server_from_config,
        pre_startup_bootstrap,
    )
    from jvagent.core.bootstrap_update_mode import resolve_bootstrap_update_mode

    if user_memory_nodes < 1:
        raise SystemExit("--user-memory-nodes must be at least 1")
    if interactions_per_user_memory_node < 1:
        raise SystemExit("--interactions-per-user-memory-node must be at least 1")

    os.environ.setdefault("JVSPATIAL_ENABLE_DEFERRED_SAVES", "false")

    server = create_server_from_config(debug=False, app_root=app_root)
    try:
        logger.info(
            "Starting graph stress-seed (standalone: exits when done; does not start the HTTP server)."
        )
        App.clear_cache()
        if no_bootstrap:
            logger.info(
                "--no-bootstrap: skipping graph sync (the app must already exist in this database)."
            )
        elif pre_startup:
            await pre_startup_bootstrap(server, update_mode=None, app_root=app_root)
        else:
            effective = await resolve_bootstrap_update_mode(None)
            await bootstrap_application_graph(update_mode=effective, app_root=app_root)

        cfg = StressSeedConfig(
            user_memory_nodes=user_memory_nodes,
            interactions_per_user_memory_node=interactions_per_user_memory_node,
            agent=agent_name,
            user_id_prefix=user_id_prefix,
            progress_every=progress_every,
        )
        await execute_stress_seed_graph(cfg)
    finally:
        App.clear_cache()


def main(argv: Optional[Sequence[str]] = None, *, app_root: str) -> None:
    p = argparse.ArgumentParser(
        prog="jvagent stress-seed",
        description=(
            "Prepopulate long-memory category nodes and conversation interactions. "
            "This subcommand only writes data and does not start the web server. "
            "To seed when starting the app, use: "
            "jvagent --stress-seed --user-memory-nodes N --interactions-per-user-memory-node M"
        ),
    )
    p.add_argument(
        "--user-memory-nodes",
        type=int,
        required=True,
        metavar="N",
        help="Number of user long-memory category nodes to create (one stress user per node)",
    )
    p.add_argument(
        "--interactions-per-user-memory-node",
        type=int,
        required=True,
        metavar="M",
        help="Interactions per user memory node",
    )
    p.add_argument(
        "--agent",
        default=None,
        metavar="REF",
        help="Agent: machine name or namespace/name (e.g. jvagent/skills_agent)",
    )
    p.add_argument(
        "--user-id-prefix",
        default="graph_stress",
        help="Prefix for external user_id values (default: graph_stress)",
    )
    b = p.add_mutually_exclusive_group()
    b.add_argument(
        "--no-bootstrap",
        action="store_true",
        help="Do not run graph bootstrap (app must already exist)",
    )
    b.add_argument(
        "--pre-startup",
        action="store_true",
        help="Run full pre-startup (graph + action init + admin)",
    )
    p.add_argument(
        "--progress-every",
        type=int,
        default=100,
        metavar="K",
        help="Log every K user memory nodes (0 to disable; default: 100)",
    )

    ns = p.parse_args(list(argv or ()))

    asyncio.run(
        _run(
            app_root,
            user_memory_nodes=ns.user_memory_nodes,
            interactions_per_user_memory_node=ns.interactions_per_user_memory_node,
            agent_name=ns.agent,
            user_id_prefix=ns.user_id_prefix,
            no_bootstrap=ns.no_bootstrap,
            pre_startup=ns.pre_startup,
            progress_every=ns.progress_every,
        )
    )


if __name__ == "__main__":
    from jvagent.cli.commands import load_app_env
    from jvagent.cli.server_config import _set_db_env_from_config
    from jvagent.core.app_context import set_app_root

    r = os.getcwd()
    load_app_env(app_root=r)
    set_app_root(r)
    _set_db_env_from_config(r)
    main(sys.argv[1:], app_root=r)
