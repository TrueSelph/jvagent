"""CLI main() dispatch and _first_app_root_path behavior."""

import sys
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from jvagent.cli.main import DISPATCH, _first_app_root_path


def _main_patches(**extra):
    """Patch side-effectful setup inside main() without starting a server."""
    base = {
        "jvagent.cli.main.load_app_env": patch("jvagent.cli.main.load_app_env"),
        "set_app_root": patch("jvagent.core.app_context.set_app_root"),
        "reload_performance_config": patch(
            "jvagent.core.cache.reload_performance_config"
        ),
        "reload_profiling_config": patch(
            "jvagent.core.profiling.reload_profiling_config"
        ),
        "_set_db_env_from_config": patch("jvagent.cli.main._set_db_env_from_config"),
        "parse_stress_seed": patch(
            "jvagent.cli.main.parse_stress_seed_for_run", return_value=(None, [])
        ),
    }
    base.update(extra)
    return base


class TestFirstAppRootPath:
    def test_stress_seed_numeric_args_not_treated_as_app_root(self, tmp_path):
        app_dir = tmp_path / "my_app"
        app_dir.mkdir()
        args = [
            str(app_dir),
            "--stress-seed",
            "--user-memory-nodes",
            "700",
            "--interactions-per-user-memory-node",
            "20",
        ]
        subcommands = DISPATCH | frozenset({"run"})

        app_root, rest = _first_app_root_path(args, subcommands)

        assert app_root == str(app_dir.resolve())
        assert rest == [
            "--stress-seed",
            "--user-memory-nodes",
            "700",
            "--interactions-per-user-memory-node",
            "20",
        ]

    def test_directory_path_becomes_app_root(self, tmp_path):
        app_dir = tmp_path / "app_root"
        app_dir.mkdir()
        subcommands = DISPATCH | frozenset({"run"})

        app_root, rest = _first_app_root_path([str(app_dir), "status"], subcommands)

        assert app_root == str(app_dir.resolve())
        assert rest == ["status"]

    def test_nonexistent_path_stays_in_argv(self, tmp_path):
        subcommands = DISPATCH | frozenset({"run"})
        bogus = str(tmp_path / "not_a_dir")

        app_root, rest = _first_app_root_path([bogus, "validate"], subcommands)

        assert app_root is None
        assert rest == [bogus, "validate"]


class TestMainDispatch:
    def test_purge_blocked_when_env_unset(self, tmp_path, monkeypatch):
        # Guard requires JVSPATIAL_ENVIRONMENT=development to be set
        # EXPLICITLY; a misconfigured host with it unset must NOT purge.
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["jvagent", "--purge"])
        monkeypatch.delenv("JVSPATIAL_ENVIRONMENT", raising=False)

        patches = _main_patches(
            purge_app_data=patch("jvagent.cli.main.purge_app_data"),
        )
        with ExitStack() as stack:
            mocks = {k: stack.enter_context(v) for k, v in patches.items()}
            from jvagent.cli.main import main

            with pytest.raises(SystemExit) as exc:
                main()

            assert exc.value.code == 1
            mocks["purge_app_data"].assert_not_called()

    def test_purge_blocked_in_production(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["jvagent", "--purge"])
        monkeypatch.setenv("JVSPATIAL_ENVIRONMENT", "production")

        patches = _main_patches(
            purge_app_data=patch("jvagent.cli.main.purge_app_data"),
        )
        with ExitStack() as stack:
            mocks = {k: stack.enter_context(v) for k, v in patches.items()}
            from jvagent.cli.main import main

            with pytest.raises(SystemExit) as exc:
                main()

            assert exc.value.code == 1
            mocks["purge_app_data"].assert_not_called()

    def test_purge_allowed_in_development(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["jvagent", "--purge", "--yes", "validate"])
        monkeypatch.setenv("JVSPATIAL_ENVIRONMENT", "development")

        patches = _main_patches(
            purge_app_data=patch("jvagent.cli.main.purge_app_data"),
            run_validate=patch("jvagent.cli.main.run_validate", return_value=0),
        )
        with ExitStack() as stack:
            mocks = {k: stack.enter_context(v) for k, v in patches.items()}
            from jvagent.cli.main import main

            with pytest.raises(SystemExit) as exc:
                main()

            assert exc.value.code == 0
            mocks["purge_app_data"].assert_called_once()
            # --yes propagates as assume_yes=True
            assert mocks["purge_app_data"].call_args.kwargs.get("assume_yes") is True

    def test_source_without_update_exits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["jvagent", "--source"])

        patches = _main_patches(
            run_server=patch("jvagent.cli.main.run_server"),
        )
        with ExitStack() as stack:
            mocks = {k: stack.enter_context(v) for k, v in patches.items()}
            from jvagent.cli.main import main

            with pytest.raises(SystemExit) as exc:
                main()

            assert exc.value.code == 2
            mocks["run_server"].assert_not_called()

    def test_source_and_merge_mutually_exclusive(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["jvagent", "--update", "--source", "--merge"])

        patches = _main_patches(
            run_server=patch("jvagent.cli.main.run_server"),
        )
        with ExitStack() as stack:
            mocks = {k: stack.enter_context(v) for k, v in patches.items()}
            from jvagent.cli.main import main

            with pytest.raises(SystemExit) as exc:
                main()

            assert exc.value.code == 2
            mocks["run_server"].assert_not_called()

    def test_update_with_source_passes_source_mode(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["jvagent", "--update", "--source"])

        patches = _main_patches(
            run_server=patch("jvagent.cli.main.run_server"),
        )
        with ExitStack() as stack:
            mocks = {k: stack.enter_context(v) for k, v in patches.items()}
            from jvagent.cli.main import main

            main()

            mocks["run_server"].assert_called_once()
            assert mocks["run_server"].call_args.kwargs["update_mode"] == "source"
