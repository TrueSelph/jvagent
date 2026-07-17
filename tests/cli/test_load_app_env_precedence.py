"""load_app_env: real environment wins over .env (AUDIT-cli M24).

override=False — a variable already set in the environment must not be clobbered
by a (possibly stale) .env baked into an image."""

from __future__ import annotations

from jvagent.cli.server import load_app_env


def test_real_env_wins_over_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("JVAGENT_TEST_PRECEDENCE=from_dotenv\n")
    monkeypatch.setenv("JVAGENT_TEST_PRECEDENCE", "from_real_env")

    load_app_env(str(tmp_path))

    import os

    assert os.environ["JVAGENT_TEST_PRECEDENCE"] == "from_real_env"


def test_dotenv_fills_unset_var(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("JVAGENT_TEST_UNSET=from_dotenv\n")
    monkeypatch.delenv("JVAGENT_TEST_UNSET", raising=False)

    load_app_env(str(tmp_path))

    import os

    # .env still populates a variable that isn't already in the environment.
    assert os.environ.get("JVAGENT_TEST_UNSET") == "from_dotenv"
