"""Tests for bundle dockerfile generation helpers."""

from jvagent.bundle.dockerfile_generator import generate_dockerfile_run_commands


def test_generate_dockerfile_run_commands_uses_single_run_layer():
    deps = {
        "a/action1": ["openai>=1.0.0", "httpx>=0.24.0"],
        "b/action2": ["httpx>=0.27.0", "pydantic>=2.0.0"],
    }
    output = generate_dockerfile_run_commands(deps)

    assert output.count("RUN /opt/venv/bin/pip install --no-cache-dir") == 1
    assert "openai>=1.0.0" in output
    assert "httpx>=0.24.0" in output
    assert "pydantic>=2.0.0" in output
