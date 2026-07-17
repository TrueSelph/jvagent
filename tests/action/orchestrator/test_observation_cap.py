"""Observation replay into the model prompt is capped."""

from jvagent.action.orchestrator.tools import (
    MAX_OBSERVATIONS_IN_PROMPT,
    render_observations_section,
)


def test_render_observations_caps_oldest():
    obs = [
        {"tool": f"t{i}", "args": {}, "observation": f"r{i}"}
        for i in range(MAX_OBSERVATIONS_IN_PROMPT + 5)
    ]
    text = render_observations_section(obs)
    assert "earlier tool results omitted" in text
    assert "TOOL t0(" not in text
    assert f"TOOL t{MAX_OBSERVATIONS_IN_PROMPT + 4}(" in text


def test_render_observations_empty():
    assert render_observations_section([]) == "(none yet)"
