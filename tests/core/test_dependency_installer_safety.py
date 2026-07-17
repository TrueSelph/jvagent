"""Pip dependency specs must not inject CLI options."""

from unittest.mock import patch

from jvagent.core.dependency_installer import install_pip_dependencies


def test_rejects_option_like_dependency():
    with patch("jvagent.core.dependency_installer.subprocess.run") as run:
        ok = install_pip_dependencies(
            ["--index-url=https://evil.example/simple", "requests"],
            action_name="EvilAction",
        )
        assert ok is False
        run.assert_not_called()


def test_accepts_normal_requirement():
    with (
        patch("jvagent.core.dependency_installer.subprocess.run") as run,
        patch(
            "jvagent.core.dependency_installer.check_pip_dependency_installed",
            return_value=False,
        ),
    ):
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = ""
        ok = install_pip_dependencies(["requests>=2.0"], action_name="SafeAction")
        assert ok is True
        run.assert_called_once()
        cmd = run.call_args[0][0]
        assert "--index-url" not in cmd
        assert any("requests" in c for c in cmd)
