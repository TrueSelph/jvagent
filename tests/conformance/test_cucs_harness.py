"""HP-10 CUCS evals against HarnessRuntime doubles (not a live model)."""

from __future__ import annotations

from pathlib import Path

import pytest

from jvagent.harness.contracts import IdempotencyClass, NativeCaller, TurnRunState
from jvagent.harness.runtime import (
    HarnessRuntime,
    HarnessStore,
    SkillIsolationRefused,
    SkillManifest,
)
from jvagent.testing.use_case_loader import discover_use_cases, load_use_case

pytestmark = pytest.mark.harness_conformance

CUCS_ROOT = Path(__file__).resolve().parent / "cucs"


def _run_scenario(data: dict) -> dict:
    rt = HarnessRuntime(HarnessStore(), worker_id="cucs")
    caller = NativeCaller("ag", "cucs-user", "cucs-sess")
    rt.put_host_tools(caller.session_id, ["host_lookup"])
    snap = rt.admit_snapshot(caller)
    corr = rt.new_correlation()
    rt.start_turn(corr, caller, snap)
    rt.register_manifest(
        SkillManifest(
            skill_key="host_skill",
            source="cucs",
            digest="digest-host",
            declared_tools=(),
            capabilities=(),
            trust_tier="trusted",
        )
    )
    rt.register_manifest(
        SkillManifest(
            skill_key="scripty",
            source="cucs",
            digest="digest-untrusted",
            declared_tools=(),
            capabilities=(),
            trust_tier="untrusted",
            spec="claude",
        )
    )
    called: list[str] = []
    isolation_refused = False
    recovery_required = False
    cached_replay = False
    for turn in data["turns"]:
        for dec in (turn.get("harness") or {}).get("decisions") or []:
            if dec.get("action") != "tool":
                if dec.get("action") == "final":
                    rt.append_event(
                        session_id=caller.session_id,
                        kind="final",
                        message_id=turn["id"],
                        correlation_id=corr,
                        snapshot_id=snap.snapshot_id,
                    )
                continue
            name = str(dec["tool"])
            args = dict(dec.get("args") or {})
            called.append(name)
            if name == "activate_skill":
                try:
                    rt.activate_skill(
                        caller,
                        snap.snapshot_id,
                        str(args.get("digest") or ""),
                        trust_tier=str(args.get("trust_tier") or "trusted"),
                    )
                except SkillIsolationRefused:
                    isolation_refused = True
                continue
            klass = None
            if name == "send_mail":
                klass = IdempotencyClass.NON_RETRYABLE
            elif name == "echo":
                klass = IdempotencyClass.IDEMPOTENT
            rec, cached = rt.begin_invocation(
                correlation_id=corr,
                snapshot_id=snap.snapshot_id,
                tool_name=name,
                payload=args,
                idempotency_class=klass,
            )
            if cached is not None:
                cached_replay = True
                continue
            ok = name != "send_mail"
            rt.finish_invocation(
                correlation_id=corr,
                record=rec,
                result="ok" if ok else "failed",
                ok=ok,
            )
            run = rt.get_run(corr)
            if run is not None and run.state is TurnRunState.RECOVERY_REQUIRED:
                recovery_required = True
    finals = [e for e in rt.replay_from(caller.session_id) if e.kind == "final"]
    return {
        "tools_called": called,
        "isolation_refused": isolation_refused,
        "recovery_required": recovery_required,
        "cached_replay": cached_replay,
        "unique_final": len({e.message_id for e in finals}) == len(finals),
        "host_tools": list(snap.host_tool_names),
    }


@pytest.mark.parametrize("path", discover_use_cases(CUCS_ROOT), ids=lambda p: p.stem)
def test_cucs_harness_eval(path: Path):
    data = load_use_case(path)
    observed = _run_scenario(data)
    for turn in data["turns"]:
        then = turn.get("then") or {}
        loop = then.get("loop") or {}
        expected_tools = loop.get("tools_called")
        if expected_tools:
            assert observed["tools_called"] == expected_tools
        surface = then.get("tools_surface") or {}
        for name in surface.get("includes") or []:
            assert name in observed["host_tools"]
        ctx = then.get("context") or {}
        for key in (
            "isolation_refused",
            "recovery_required",
            "cached_replay",
            "unique_final",
        ):
            if key in ctx:
                assert observed[key] is ctx[key]
