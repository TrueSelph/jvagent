"""Observation replay is size-bounded, not just count-bounded.

Every tick re-sends this turn's prior tool results, so without size caps the
per-turn input cost is quadratic in tick count — an 8-tick research turn over
8 KB page fetches billed ~70k input tokens before these caps existed.
"""

from __future__ import annotations

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)
from jvagent.action.orchestrator.tools import (
    elide_middle,
    render_observations_section,
)


def test_elide_middle_keeps_head_and_tail_and_marks_the_cut():
    text = "H" * 500 + "T" * 500
    out = elide_middle(text, 300)
    assert len(out) <= 300
    assert out.startswith("H")
    assert out.endswith("T")
    assert "elided" in out


def test_elide_middle_is_a_noop_under_the_limit_or_when_disabled():
    assert elide_middle("short", 300) == "short"
    assert elide_middle("x" * 5000, 0) == "x" * 5000


def test_recent_results_keep_more_than_stale_ones():
    obs = [
        {"tool": f"t{i}", "args": {}, "observation": "payload " * 2000}
        for i in range(6)
    ]
    out = render_observations_section(
        obs, max_chars=4000, stale_max_chars=200, full_recent=2
    )
    lines = [ln for ln in out.splitlines() if ln.startswith("TOOL ")]
    assert len(lines) == 6
    # The two most recent get the generous budget; the older four are cut hard.
    assert all(len(ln) < 400 for ln in lines[:4])
    assert all(len(ln) > 3000 for ln in lines[-2:])


def test_arguments_are_elided_too():
    """A write-file call carries its whole payload in ``args``; unbounded, it
    would be replayed verbatim on every remaining tick of the turn."""
    obs = [
        {"tool": "write_file", "args": {"content": "x" * 50000}, "observation": "ok"}
    ]
    out = render_observations_section(obs, args_max_chars=300)
    assert len(out) < 1000
    assert "elided" in out


def test_caps_can_be_disabled_entirely():
    payload = "y" * 20000
    obs = [{"tool": "t", "args": {}, "observation": payload}]
    out = render_observations_section(
        obs, max_chars=0, stale_max_chars=0, args_max_chars=0
    )
    assert payload in out


def test_quadratic_growth_is_bounded_across_a_long_turn():
    """The point of the caps: total replay across a turn must not grow with the
    square of the tick count."""
    observations: list = []
    uncapped = capped = 0
    for i in range(20):
        uncapped += len(
            render_observations_section(
                observations, max_chars=0, stale_max_chars=0, args_max_chars=0
            )
        )
        capped += len(render_observations_section(observations))
        observations.append(
            {"tool": f"t{i}", "args": {}, "observation": "word " * 1600}
        )
    assert capped < uncapped / 4


def test_defaults_are_exposed_as_configuration():
    ex = OrchestratorInteractAction()
    assert ex.observation_max_chars > ex.stale_observation_max_chars > 0
    assert ex.observation_full_recent >= 1
    assert ex.observation_args_max_chars > 0
