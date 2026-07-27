#!/usr/bin/env python3
"""Measure what one Orchestrator turn actually costs, on a real agent graph.

Token spend and turn latency are the two things about the executive loop that
are easy to regress and impossible to eyeball: the system prompt is assembled
from a dozen sources, and every tick resends it plus the whole observation log.
This boots an app, drives a scripted turn against the real ``execute`` path, and
reports per-tick input tokens, prompt-cache prefix stability, and where the
non-model wall-clock went.

No API key or network is needed — the model call is intercepted, the built
prompts are measured, and a canned decision is returned in place of a response.

    python scripts/bench_orchestrator.py examples/jvagent_app
    python scripts/bench_orchestrator.py examples/jvagent_app --agent leadgen
    python scripts/bench_orchestrator.py examples/jvagent_app --dump /tmp/prompts

Token counts use tiktoken's ``cl100k_base`` when installed and a ~4-chars-per-
token estimate otherwise; either way the numbers are comparable run to run,
which is what a regression check needs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import defaultdict
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

# A turn that exercises the paths that actually cost something: a core tool, a
# skill activation (which swaps the skills section mid-turn), a catalog lookup,
# and a reply.
DEFAULT_SCRIPT: List[Dict[str, Any]] = [
    {"action": "tool", "tool": "get_current_datetime", "args": {}},
    {"action": "tool", "tool": "find_tool", "args": {"query": "write a file"}},
    {"action": "tool", "tool": "reply", "args": {"text": "Here you go."}},
    {"action": "final", "answer": ""},
]

try:  # pragma: no cover - measurement helper
    import tiktoken

    _ENC = tiktoken.get_encoding("cl100k_base")

    def count_tokens(text: str) -> int:
        return len(_ENC.encode(text or ""))

except Exception:  # pragma: no cover - tiktoken is optional

    def count_tokens(text: str) -> int:
        return len(text or "") // 4


def synthetic_history(turns: int, with_events: bool = False) -> List[Dict[str, str]]:
    """Prior conversation in the shape ``get_interaction_history(formatted=True)``
    returns it: alternating user/assistant messages.

    Loop history never includes ``[EVENT]`` lines (ADR-0041); ``with_events`` is
    retained only for local what-if measurements of token cost.

    Benchmarking against an empty history flatters the result -- history rides
    in every tick's request, between the system prompt and the user turn, and a
    real agent runs history_limit at 4-10 turns.
    """
    out: List[Dict[str, str]] = []
    for i in range(turns):
        out.append(
            {
                "role": "user",
                "content": (
                    f"Question {i} about the current project status, roughly the "
                    "length of a real chat message rather than one word."
                ),
            }
        )
        if with_events:
            out.append(
                {
                    "role": "system",
                    "content": f"[EVENT] tool_call web_search__search #{i}",
                }
            )
        out.append(
            {
                "role": "assistant",
                "content": (
                    f"Answer {i}. A couple of sentences of substantive reply, "
                    "with enough detail to resemble what the agent actually "
                    "sends back to a user on a normal turn."
                ),
            }
        )
    return out


def make_visitor(
    utterance: str, channel: str, history: Optional[List[Dict[str, str]]] = None
) -> Any:
    """A walker stand-in shaped like the one the interact pipeline builds.

    Mirrors ``tests/action/orchestrator/conftest.py``: enough of the interaction
    and conversation surface for the real loop to run end to end.
    """
    directives: List[Dict[str, Any]] = []
    interaction = MagicMock()
    interaction.id = "bench_interaction"
    interaction.utterance = utterance
    interaction.response = ""
    interaction.directives = directives
    interaction.parameters = []
    interaction.has_emitted = lambda: bool((interaction.response or "").strip())

    def add_directive(content: str, action_name: str = "ReplyAction") -> bool:
        directives.append(
            {"action_name": action_name, "content": content, "executed": False}
        )
        return True

    interaction.add_directive = add_directive
    interaction.get_unexecuted_directives = lambda: [
        d for d in directives if not d.get("executed")
    ]
    interaction.get_unexecuted_parameters = lambda: []
    interaction.set_to_executed = lambda directives=None, parameters=None: None

    conversation = MagicMock()
    conversation.context = {}
    conversation.tasks = []
    conversation.save = AsyncMock()
    conversation.get_interaction_history = AsyncMock(return_value=list(history or []))

    visitor = MagicMock()
    visitor.user_id = "bench_user"
    visitor.channel = channel
    visitor.utterance = utterance
    visitor.interaction = interaction
    visitor.conversation = conversation
    visitor.data = {}
    visitor.add_directives = AsyncMock()
    visitor.visit = AsyncMock()
    return visitor


def common_prefix_len(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


async def resolve_orchestrator(app_root: str, agent_hint: str, update_mode: str) -> Any:
    from jvagent.cli.bootstrap import bootstrap_application_graph
    from jvagent.core.agents import Agents

    await bootstrap_application_graph(update_mode=update_mode, app_root=app_root)
    agents = await (await Agents.get()).get_connected_agents()
    if not agents:
        raise SystemExit("no agents installed in this app")
    agent = next(
        (a for a in agents if agent_hint.lower() in (a.name or "").lower()),
        agents[0],
    )
    manager = await agent.get_actions_manager()
    actions = await manager.get_all_actions(enabled_only=True)
    orchestrator = next(
        (a for a in actions if type(a).__name__ == "OrchestratorInteractAction"),
        None,
    )
    if orchestrator is None:
        raise SystemExit(f"agent {agent.name!r} has no enabled orchestrator action")
    print(f"agent: {agent.name}  ({len(actions)} enabled actions)")
    return orchestrator


def instrument(orchestrator_cls: Any, script: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Wrap the model call and the per-turn phases; return the capture sink."""
    sink: Dict[str, Any] = {"ticks": [], "phases": defaultdict(lambda: [0, 0.0])}
    decisions = list(script)
    original_run_model = orchestrator_cls._run_model

    async def run_model(
        self,
        visitor,
        utterance,
        history,
        tools,
        observations,
        flow_note="",
        skills_section="",
        **kwargs,
    ):
        kwargs.update(flow_note=flow_note, skills_section=skills_section)
        captured: Dict[str, Any] = {}

        class _Result:
            response = json.dumps(
                decisions.pop(0) if decisions else {"action": "final", "answer": ""}
            )
            thinking_content = None

        async def query_messages(**payload):
            captured.update(payload)
            return _Result()

        async def gear_model(_self, _gear):
            return (
                SimpleNamespace(query_messages=query_messages),
                "bench",
                0.2,
                4096,
                False,
            )

        previous = orchestrator_cls._gear_model
        orchestrator_cls._gear_model = gear_model
        started = time.perf_counter()
        try:
            decision = await original_run_model(
                self, visitor, utterance, history, tools, observations, **kwargs
            )
        finally:
            orchestrator_cls._gear_model = previous

        history_text = "\n".join(
            str(m.get("content", "")) for m in (captured.get("history") or [])
        )
        # Token totals come from the WIRE payload (`messages`), not from the
        # observability fields. Those fields carry the system prompt, the history
        # and the user turn -- but a caller may put content in additional
        # messages (the trailing tool-listing variant does exactly that), and
        # summing the observability fields would silently undercount it and
        # report a saving that is really just content the bench stopped looking
        # at.
        wire_text = "\n".join(
            str(m.get("content", "")) for m in (captured.get("messages") or [])
        )
        sink["ticks"].append(
            {
                "gear": kwargs.get("gear", "heavy"),
                "tools": len(tools),
                "observations": len(observations),
                "system": captured.get("system", ""),
                "user": captured.get("prompt_for_observability", ""),
                "system_tokens": count_tokens(captured.get("system", "")),
                "user_tokens": count_tokens(
                    captured.get("prompt_for_observability", "")
                ),
                "history_tokens": count_tokens(history_text),
                "wire_tokens": count_tokens(wire_text),
                # What the provider actually caches is the request PREFIX, and
                # the request is [system, *history, user] -- so history is part
                # of the cacheable span, not a separate bucket. Measuring the
                # system prompt alone understates what a volatile section costs:
                # anything downstream of the first changed byte is re-priced,
                # history included.
                "prefix": captured.get("system", "") + "\n" + history_text,
                "build_ms": (time.perf_counter() - started) * 1000,
            }
        )
        return decision

    orchestrator_cls._run_model = run_model

    async def publish(self, *, visitor, content, **kwargs):
        interaction = getattr(visitor, "interaction", None)
        if interaction is not None:
            interaction.response = (interaction.response or "") + content

    orchestrator_cls.publish = publish

    # Compose egress must not hit a real model provider — the bench has no API
    # key, and a failed respond() retry (~10s) poisons wall-clock measurements.
    async def get_responder(self):
        async def gather(visitor):
            interaction = getattr(visitor, "interaction", None)
            if interaction is None:
                return False
            for d in list(
                getattr(interaction, "get_unexecuted_directives", lambda: [])()
            ):
                content = (d.get("content") or "").split("\u2063", 1)[0].strip()
                if content.lower().startswith("tell the user"):
                    content = content.split(":", 1)[-1].strip()
                if content:
                    interaction.response = (interaction.response or "") + content
                d["executed"] = True
            return bool((interaction.response or "").strip())

        async def respond(interaction, visitor=None, **kwargs):
            return await gather(visitor or SimpleNamespace(interaction=interaction))

        return SimpleNamespace(
            gather=gather,
            respond=respond,
            apply_channel_format=False,
            get_channel_format=lambda _ch: None,
        )

    orchestrator_cls.get_responder = get_responder

    for name in (
        "_assemble_tools",
        "_collect_capabilities",
        "_history",
        "_accumulate_parameters",
        "_ingest_uploads",
        "_drain_runnable_tasks",
        "_routable_flow_tool_names",
        "_enabled_actions",
        "_enforce_required_actions",
        "_record_orchestrator_activation",
    ):
        original = getattr(orchestrator_cls, name, None)
        if original is None:
            continue

        def wrap(fn, label):
            async def timed(*args, **kwargs):
                started = time.perf_counter()
                try:
                    return await fn(*args, **kwargs)
                finally:
                    bucket = sink["phases"][label]
                    bucket[0] += 1
                    bucket[1] += (time.perf_counter() - started) * 1000

            return timed

        setattr(orchestrator_cls, name, wrap(original, name))

    return sink


def report(sink: Dict[str, Any], wall_ms: float) -> Dict[str, float]:
    """Print the measurement and return the headline figures for assertions."""
    ticks = sink["ticks"]
    print(
        f"\nturn wall-clock (model excluded): {wall_ms:.0f} ms over {len(ticks)} ticks"
    )

    print("\nper-turn phases (non-model):")
    for name, (calls, ms) in sorted(sink["phases"].items(), key=lambda kv: -kv[1][1]):
        if ms >= 1.0:
            print(f"  {ms:8.1f} ms  x{calls:<3} {name}")

    print("\nper-tick input tokens:")
    total = 0
    for index, tick in enumerate(ticks):
        tick_total = tick["wire_tokens"]
        total += tick_total
        print(
            f"  tick{index} gear={tick['gear']:<5} tools={tick['tools']:<3} "
            f"obs={tick['observations']:<3} system={tick['system_tokens']:<6} "
            f"user={tick['user_tokens']:<6} history={tick['history_tokens']:<5} "
            f"= {tick_total}"
        )
    print(f"  TOTAL INPUT TOKENS: {total:,}")

    worst_reuse_pct = 100.0
    if len(ticks) > 1:
        # The provider caches the request PREFIX. The request is normally
        # [system, *history, user], so this measures system+history rather than
        # the system prompt alone: anything downstream of the first changed byte
        # is re-priced, history included.
        print("\nprompt-cache prefix stability (system + history vs tick0):")
        base = ticks[0]["prefix"]
        base_tokens = max(1, count_tokens(base))
        for index, tick in enumerate(ticks[1:], 1):
            shared = count_tokens(base[: common_prefix_len(base, tick["prefix"])])
            pct = 100 * shared / base_tokens
            worst_reuse_pct = min(worst_reuse_pct, pct)
            print(f"  tick{index}: {shared}/{base_tokens} tokens ({pct:.0f}% reusable)")

    return {"total_tokens": float(total), "worst_reuse_pct": worst_reuse_pct}


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app_root", nargs="?", default="examples/jvagent_app")
    parser.add_argument(
        "--agent", default="orchestrator", help="substring of agent name"
    )
    parser.add_argument("--utterance", default="research the topic and save a report")
    parser.add_argument("--channel", default="web")
    parser.add_argument(
        "--history",
        type=int,
        default=0,
        help="Seed N prior conversation turns. History rides in every tick's "
        "request, so benchmarking at 0 (the default, for a first turn) "
        "understates a mid-conversation turn. Try the agent's history_limit.",
    )
    parser.add_argument(
        "--assert-max-tokens",
        type=int,
        default=0,
        help="Exit non-zero if the turn's total input tokens exceed N. Token "
        "counts here are fully deterministic (no model call), so this is a "
        "regression gate rather than a flaky threshold.",
    )
    parser.add_argument(
        "--assert-min-cache-pct",
        type=float,
        default=0.0,
        help="Exit non-zero if any tick reuses less than this percentage of the "
        "tick-0 request prefix. Guards the prompt section order, which is easy "
        "to undo by moving a volatile section back above the invariant ones.",
    )
    parser.add_argument(
        "--dump", default="", help="directory to write each tick's prompts"
    )
    parser.add_argument(
        "--update-mode",
        default="source",
        choices=("merge", "source", "none"),
        help="Graph sync before measuring. Prompt templates are persisted "
        "``attribute`` defaults, so an app graph bootstrapped against an older "
        "jvagent keeps that release's prompts under 'merge' — measure with "
        "'source' (the default here) to benchmark the code in this checkout.",
    )
    parser.add_argument(
        "--tool-listing",
        default="",
        choices=("", "system", "trailing"),
        help="Override tool_listing_position. 'trailing' moves the tool/skill "
        "listings into their own message after the history, which pulls the "
        "conversation into the cacheable request prefix.",
    )
    parser.add_argument(
        "--script",
        default="",
        help="JSON list of model decisions to drive the turn (default: a "
        "tool + discovery + reply turn)",
    )
    args = parser.parse_args()

    app_root = os.path.abspath(args.app_root)
    os.chdir(app_root)
    sys.path.insert(0, app_root)

    from jvagent.action.orchestrator.orchestrator_interact_action import (
        OrchestratorInteractAction,
    )

    orchestrator = await resolve_orchestrator(
        app_root, args.agent, None if args.update_mode == "none" else args.update_mode
    )
    if args.tool_listing:
        orchestrator.tool_listing_position = args.tool_listing
    script = json.loads(args.script) if args.script else DEFAULT_SCRIPT
    sink = instrument(OrchestratorInteractAction, script)

    visitor = make_visitor(
        args.utterance,
        args.channel,
        synthetic_history(args.history),
    )
    started = time.perf_counter()
    await orchestrator.execute(visitor)
    headline = report(sink, (time.perf_counter() - started) * 1000)

    if args.dump:
        os.makedirs(args.dump, exist_ok=True)
        for index, tick in enumerate(sink["ticks"]):
            with open(f"{args.dump}/tick{index}_system.txt", "w") as handle:
                handle.write(tick["system"])
            with open(f"{args.dump}/tick{index}_user.txt", "w") as handle:
                handle.write(tick["user"])
        print(f"\nprompts written to {args.dump}")

    failures: List[str] = []
    if args.assert_max_tokens and headline["total_tokens"] > args.assert_max_tokens:
        failures.append(
            f"total input tokens {headline['total_tokens']:,.0f} exceeds budget "
            f"{args.assert_max_tokens:,}"
        )
    if (
        args.assert_min_cache_pct
        and headline["worst_reuse_pct"] < args.assert_min_cache_pct
    ):
        failures.append(
            f"worst prefix reuse {headline['worst_reuse_pct']:.0f}% is below "
            f"{args.assert_min_cache_pct:.0f}%"
        )
    if failures:
        for line in failures:
            print(f"\nBUDGET FAILED: {line}")
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
