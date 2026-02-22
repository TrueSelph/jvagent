"""Tests for dependency installation during action loading."""

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from jvagent.action.action_loader import ActionLoader
from jvagent.core.dependency_installer import (
    check_pip_dependency_installed,
    install_action_dependencies,
    install_pip_dependencies,
)

logger = logging.getLogger(__name__)


class TestDependencyInstallation:
    """Test dependency installation for actions."""

    def test_install_pip_dependencies_empty_list(self):
        """Test that empty dependency list returns True without calling pip."""
        result = install_pip_dependencies([], "test_action")
        assert result is True

    def test_install_pip_dependencies_filters_empty_strings(self):
        """Test that empty strings are filtered from dependencies."""
        with patch(
            "jvagent.core.dependency_installer.check_pip_dependency_installed"
        ) as mock_check:
            mock_check.return_value = False
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                result = install_pip_dependencies(
                    ["requests>=2.25.0", "", "  ", "numpy"],
                    "test_action",
                    skip_if_installed=False,
                )
            assert result is True
            # Should only install non-empty packages
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert "requests>=2.25.0" in args
            assert "numpy" in args
            assert "" not in args

    def test_install_pip_dependencies_success(self):
        """Test successful dependency installation."""
        with patch(
            "jvagent.core.dependency_installer.check_pip_dependency_installed"
        ) as mock_check:
            mock_check.return_value = False
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                result = install_pip_dependencies(
                    ["requests>=2.25.0"],
                    "test_action",
                    upgrade=False,
                    skip_if_installed=False,
                )
            assert result is True
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert sys.executable in args
            assert "pip" in args
            assert "install" in args
            assert "requests>=2.25.0" in args

    def test_install_pip_dependencies_failure(self):
        """Test failed dependency installation."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stderr="Error installing package"
            )
            result = install_pip_dependencies(["nonexistent-package"], "test_action")
            assert result is False

    def test_install_pip_dependencies_with_upgrade(self):
        """Test dependency installation with upgrade flag."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = install_pip_dependencies(
                ["requests>=2.25.0"], "test_action", upgrade=True
            )
            assert result is True
            args = mock_run.call_args[0][0]
            assert "--upgrade" in args

    def test_install_pip_dependencies_skip_if_installed(self):
        """Test that already installed dependencies are skipped."""
        with patch(
            "jvagent.core.dependency_installer.check_pip_dependency_installed"
        ) as mock_check:
            mock_check.return_value = True
            with patch("subprocess.run") as mock_run:
                result = install_pip_dependencies(
                    ["requests>=2.25.0"], "test_action", skip_if_installed=True
                )
                assert result is True
                # pip should not be called if package is already installed
                mock_run.assert_not_called()

    def test_install_action_dependencies_extracts_pip_deps(self):
        """Test that pip dependencies are extracted from metadata and installed."""
        metadata = {
            "package": {
                "name": "test/action",
                "dependencies": {"pip": ["requests>=2.25.0", "numpy"]},
            }
        }

        with patch(
            "jvagent.core.dependency_installer.install_pip_dependencies"
        ) as mock_install:
            mock_install.return_value = True
            result = install_action_dependencies(
                metadata, "test_action", Path("/tmp/test")
            )
            assert result is True
            mock_install.assert_called_once_with(
                ["requests>=2.25.0", "numpy"], "test_action", Path("/tmp/test")
            )

    def test_install_action_dependencies_no_pip_deps(self):
        """Test that actions without pip dependencies return True without calling pip."""
        metadata = {"package": {"name": "test/action", "dependencies": {}}}

        with patch(
            "jvagent.core.dependency_installer.install_pip_dependencies"
        ) as mock_install:
            result = install_action_dependencies(
                metadata, "test_action", Path("/tmp/test")
            )
            assert result is True
            mock_install.assert_not_called()

    def test_check_pip_dependency_installed_simple_name(self):
        """Test checking if a simple package is installed."""
        # This test uses a package we know is installed (logging)
        result = check_pip_dependency_installed("logging")
        assert isinstance(result, bool)

    def test_check_pip_dependency_installed_with_version(self):
        """Test checking if a package with version specifier is installed."""
        # This will check if the package exists, not the version
        result = check_pip_dependency_installed("requests>=2.25.0")
        assert isinstance(result, bool)


class TestActionLoaderDependencyInstallation:
    """Test that ActionLoader installs dependencies during pre-import."""

    def test_pre_import_core_action_packages_installs_dependencies(self, tmp_path):
        """Test that pre_import_core_action_packages installs dependencies for core actions."""
        # Create a mock action loader
        action_loader = ActionLoader(base_path=str(tmp_path))

        # Mock the core action cache to return a fake action with dependencies
        mock_cache = {
            "test_action": {
                "dir": tmp_path / "test_action",
                "module_file": "test_action",
                "class_name": "TestAction",
                "relative_path": "test_action",
                "data": {
                    "package": {
                        "name": "jvagent/test_action",
                        "dependencies": {"pip": ["httpx>=0.27.0"]},
                    }
                },
            }
        }

        with patch.object(
            action_loader, "_build_core_action_cache", return_value=mock_cache
        ):
            with patch.object(
                action_loader, "_get_core_action_path", return_value=tmp_path
            ):
                with patch.object(
                    action_loader, "_ensure_dependencies_installed"
                ) as mock_ensure_deps:
                    with patch("importlib.import_module"):
                        # Call the method
                        action_loader._pre_import_core_action_packages()

                        # Verify that _ensure_dependencies_installed was called
                        mock_ensure_deps.assert_called_once()
                        call_args = mock_ensure_deps.call_args[0]
                        assert call_args[0] == mock_cache["test_action"]["data"]
                        assert call_args[1] == "test_action"

    def test_ensure_dependencies_installed_calls_install_action_dependencies(
        self, tmp_path
    ):
        """Test that _ensure_dependencies_installed calls install_action_dependencies."""
        action_loader = ActionLoader(base_path=str(tmp_path))

        data = {
            "package": {
                "name": "jvagent/test_action",
                "dependencies": {"pip": ["requests>=2.25.0"]},
            }
        }

        with patch(
            "jvagent.action.action_loader.install_action_dependencies"
        ) as mock_install:
            mock_install.return_value = True
            action_loader._ensure_dependencies_installed(
                data, "test_action", tmp_path / "test_action"
            )

            mock_install.assert_called_once_with(
                data, "test_action", tmp_path / "test_action"
            )
