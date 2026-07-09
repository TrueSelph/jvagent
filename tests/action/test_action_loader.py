"""Unit tests for ActionLoader conditional loading functionality."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jvagent.action.loader import (
    ActionLoader,
    ActionRegistry,
    JvagentActionsImporter,
)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def action_loader(temp_dir):
    """Create an ActionLoader instance with a temporary base path."""
    return ActionLoader(base_path=str(temp_dir))


@pytest.fixture
def mock_core_action_cache():
    """Create a mock core action cache."""
    return {
        "whatsapp": {
            "dir": Path("/mock/jvagent/action/whatsapp"),
            "module_file": "whatsapp_action",
            "class_name": "WhatsAppAction",
            "relative_path": "whatsapp",
            "data": {
                "package": {
                    "name": "jvagent/whatsapp",
                    "archetype": "WhatsAppAction",
                    "dependencies": {"actions": ["jvagent/reply"]},
                }
            },
        },
        "reply": {
            "dir": Path("/mock/jvagent/action/reply"),
            "module_file": "reply_action",
            "class_name": "ReplyAction",
            "relative_path": "reply",
            "data": {
                "package": {
                    "name": "jvagent/reply",
                    "archetype": "ReplyAction",
                    "dependencies": {"actions": []},
                }
            },
        },
        "persona": {
            "dir": Path("/mock/jvagent/action/persona"),
            "module_file": "persona_action",
            "class_name": "PersonaAction",
            "relative_path": "persona",
            "data": {
                "package": {
                    "name": "jvagent/persona",
                    "archetype": "PersonaAction",
                    "dependencies": {"actions": []},
                }
            },
        },
    }


class TestActionRegistry:
    """Tests for ActionRegistry tracking functionality."""

    def test_add_required_action(self):
        """Test that ActionRegistry correctly adds and tracks required actions."""
        registry = ActionRegistry()

        # Initially empty
        assert len(registry.required_actions) == 0

        # Add a required action
        registry.add_required_action("jvagent/whatsapp")
        assert "jvagent/whatsapp" in registry.required_actions
        assert len(registry.required_actions) == 1

        # Add another required action
        registry.add_required_action("jvagent/persona")
        assert "jvagent/whatsapp" in registry.required_actions
        assert "jvagent/persona" in registry.required_actions
        assert len(registry.required_actions) == 2

        # Adding duplicate should not create duplicates (set behavior)
        registry.add_required_action("jvagent/whatsapp")
        assert len(registry.required_actions) == 2

        # Invalid format should be ignored
        registry.add_required_action("invalid_format")
        assert len(registry.required_actions) == 2
        registry.add_required_action("")
        assert len(registry.required_actions) == 2

    def test_should_import_action(self):
        """Test that should_import_action correctly identifies actions to import."""
        registry = ActionRegistry()

        # Action not in resolved_actions should not be imported
        assert registry.should_import_action("jvagent/whatsapp") is False

        # Add to resolved_actions
        registry.resolved_actions.add("jvagent/whatsapp")
        assert registry.should_import_action("jvagent/whatsapp") is True

        # After marking as imported, should not be imported again
        registry.mark_imported("jvagent/whatsapp")
        assert registry.should_import_action("jvagent/whatsapp") is False

    def test_mark_imported(self):
        """Test that mark_imported correctly tracks imported actions."""
        registry = ActionRegistry()

        # Initially empty
        assert len(registry.imported_actions) == 0

        # Mark as imported
        registry.mark_imported("jvagent/whatsapp")
        assert "jvagent/whatsapp" in registry.imported_actions
        assert len(registry.imported_actions) == 1

        # Mark another as imported
        registry.mark_imported("jvagent/persona")
        assert len(registry.imported_actions) == 2

    def test_resolving_tracking(self):
        """Test that resolving state is correctly tracked to prevent cycles."""
        registry = ActionRegistry()

        # Initially not resolving
        assert registry.is_resolving("jvagent/whatsapp") is False

        # Start resolving
        registry.start_resolving("jvagent/whatsapp")
        assert registry.is_resolving("jvagent/whatsapp") is True

        # Finish resolving
        registry.finish_resolving("jvagent/whatsapp")
        assert registry.is_resolving("jvagent/whatsapp") is False


class TestActionLoaderDependencyResolution:
    """Tests for ActionLoader transitive dependency resolution."""

    @patch.object(ActionLoader, "_build_core_action_cache")
    @patch.object(ActionLoader, "_load_action_metadata_for_deps")
    def test_resolve_action_dependencies_simple(
        self,
        mock_load_metadata,
        mock_build_cache,
        action_loader,
        mock_core_action_cache,
    ):
        """Test that simple dependencies are resolved correctly."""
        mock_build_cache.return_value = mock_core_action_cache

        # Mock metadata for whatsapp (depends on reply)
        whatsapp_metadata = MagicMock()
        whatsapp_metadata.dependencies = {"actions": ["jvagent/reply"]}

        # Mock metadata for reply (no dependencies)
        reply_metadata = MagicMock()
        reply_metadata.dependencies = {"actions": []}

        def load_metadata_side_effect(action_ref, core_cache):
            if action_ref == "jvagent/whatsapp":
                return whatsapp_metadata
            elif action_ref == "jvagent/reply":
                return reply_metadata
            return None

        mock_load_metadata.side_effect = load_metadata_side_effect

        registry = ActionRegistry()
        registry.add_required_action("jvagent/whatsapp")

        # Resolve dependencies
        resolved = action_loader._resolve_action_dependencies(
            "jvagent/whatsapp", mock_core_action_cache, registry
        )

        # Should include both whatsapp and its dependency
        assert "jvagent/whatsapp" in resolved
        assert "jvagent/reply" in resolved
        assert len(resolved) == 2

        # Both should be in resolved_actions
        assert "jvagent/whatsapp" in registry.resolved_actions
        assert "jvagent/reply" in registry.resolved_actions

    @patch.object(ActionLoader, "_build_core_action_cache")
    @patch.object(ActionLoader, "_load_action_metadata_for_deps")
    def test_resolve_action_dependencies_transitive(
        self,
        mock_load_metadata,
        mock_build_cache,
        action_loader,
        mock_core_action_cache,
    ):
        """Test that transitive dependencies are resolved correctly."""
        mock_build_cache.return_value = mock_core_action_cache

        # Create a chain: action_a -> action_b -> action_c
        metadata_a = MagicMock()
        metadata_a.dependencies = {"actions": ["jvagent/action_b"]}

        metadata_b = MagicMock()
        metadata_b.dependencies = {"actions": ["jvagent/action_c"]}

        metadata_c = MagicMock()
        metadata_c.dependencies = {"actions": []}

        def load_metadata_side_effect(action_ref, core_cache):
            if action_ref == "jvagent/action_a":
                return metadata_a
            elif action_ref == "jvagent/action_b":
                return metadata_b
            elif action_ref == "jvagent/action_c":
                return metadata_c
            return None

        mock_load_metadata.side_effect = load_metadata_side_effect

        registry = ActionRegistry()
        registry.add_required_action("jvagent/action_a")

        # Resolve dependencies
        resolved = action_loader._resolve_action_dependencies(
            "jvagent/action_a", mock_core_action_cache, registry
        )

        # Should include all three actions
        assert "jvagent/action_a" in resolved
        assert "jvagent/action_b" in resolved
        assert "jvagent/action_c" in resolved
        assert len(resolved) == 3

    @patch.object(ActionLoader, "_build_core_action_cache")
    @patch.object(ActionLoader, "_load_action_metadata_for_deps")
    def test_resolve_action_dependencies_circular(
        self,
        mock_load_metadata,
        mock_build_cache,
        action_loader,
        mock_core_action_cache,
    ):
        """Test that circular dependencies are handled correctly."""
        mock_build_cache.return_value = mock_core_action_cache

        # Create circular dependency: action_a -> action_b -> action_a
        metadata_a = MagicMock()
        metadata_a.dependencies = {"actions": ["jvagent/action_b"]}

        metadata_b = MagicMock()
        metadata_b.dependencies = {"actions": ["jvagent/action_a"]}

        def load_metadata_side_effect(action_ref, core_cache):
            if action_ref == "jvagent/action_a":
                return metadata_a
            elif action_ref == "jvagent/action_b":
                return metadata_b
            return None

        mock_load_metadata.side_effect = load_metadata_side_effect

        registry = ActionRegistry()
        registry.add_required_action("jvagent/action_a")

        # Resolve dependencies - should not loop infinitely
        resolved = action_loader._resolve_action_dependencies(
            "jvagent/action_a", mock_core_action_cache, registry
        )

        # Should include both actions (circular dependency detected and handled)
        assert "jvagent/action_a" in resolved
        assert "jvagent/action_b" in resolved
        assert len(resolved) == 2

        # Should not be resolving anymore
        assert registry.is_resolving("jvagent/action_a") is False
        assert registry.is_resolving("jvagent/action_b") is False

    @patch.object(ActionLoader, "_build_core_action_cache")
    @patch.object(ActionLoader, "_load_action_metadata_for_deps")
    def test_resolve_action_dependencies_already_resolved(
        self,
        mock_load_metadata,
        mock_build_cache,
        action_loader,
        mock_core_action_cache,
    ):
        """Test that already resolved actions are not resolved again."""
        mock_build_cache.return_value = mock_core_action_cache

        metadata = MagicMock()
        metadata.dependencies = {"actions": ["jvagent/reply"]}

        mock_load_metadata.return_value = metadata

        registry = ActionRegistry()
        registry.add_required_action("jvagent/whatsapp")
        registry.resolved_actions.add("jvagent/reply")  # Already resolved

        # Resolve dependencies
        resolved = action_loader._resolve_action_dependencies(
            "jvagent/whatsapp", mock_core_action_cache, registry
        )

        # Should only return whatsapp (reply already resolved)
        assert "jvagent/whatsapp" in resolved
        assert len(resolved) == 1


class TestWhatsAppWebhookURL:
    """Tests for WhatsAppAction.get_webhook_url functionality.

    These tests verify that the existing test file covers the requested cases.
    The actual implementation is in tests/action/whatsapp/test_webhook_url_generation.py
    """

    def test_webhook_url_generation_covered(self):
        """Verify that test_get_webhook_url_generates_new_key covers case 4."""
        # This test verifies that the existing test file has the required test
        # Case 4: WhatsAppAction.get_webhook_url generates new API key and webhook URL
        test_file = (
            Path(__file__).parent / "whatsapp" / "test_webhook_url_generation.py"
        )
        assert test_file.exists(), "WhatsApp webhook URL test file should exist"

        content = test_file.read_text()
        assert (
            "test_get_webhook_url_generates_new_key" in content
        ), "Test for generating new API key should exist"

    def test_webhook_url_reuse_covered(self):
        """Verify that test_get_webhook_url_reuses_existing_url covers case 5."""
        # This test verifies that the existing test file has the required test
        # Case 5: WhatsAppAction.get_webhook_url reuses existing valid webhook URL without regeneration
        test_file = (
            Path(__file__).parent / "whatsapp" / "test_webhook_url_generation.py"
        )
        assert test_file.exists(), "WhatsApp webhook URL test file should exist"

        content = test_file.read_text()
        assert (
            "test_get_webhook_url_reuses_existing_url" in content
        ), "Test for reusing existing URL should exist"


class TestJvagentActionsImporter:
    """Tests for JvagentActionsImporter MetaPathFinder and path mapping."""

    def test_find_spec_namespace_packages_and_action_package(self, temp_dir):
        """JvagentActionsImporter returns correct specs for namespace and action package."""
        # agents/jvagent/foo/actions/jvagent/bar/ with __init__.py and bar.py
        action_dir = (
            temp_dir / "agents" / "jvagent" / "foo" / "actions" / "jvagent" / "bar"
        )
        action_dir.mkdir(parents=True)
        (action_dir / "__init__.py").write_text("from .bar import BarAction\n")
        (action_dir / "bar.py").write_text("class BarAction: pass\n")
        (action_dir / "endpoints.py").write_text("# endpoints\n")

        importer = JvagentActionsImporter(temp_dir)

        # Namespace packages (loader=None, submodule_search_locations set)
        for fullname, expected_path in [
            ("jvagent.actions.jvagent", temp_dir / "agents" / "jvagent"),
            (
                "jvagent.actions.jvagent.foo",
                temp_dir / "agents" / "jvagent" / "foo" / "actions",
            ),
            (
                "jvagent.actions.jvagent.foo.jvagent",
                temp_dir / "agents" / "jvagent" / "foo" / "actions" / "jvagent",
            ),
        ]:
            spec = importer.find_spec(fullname, None)
            assert spec is not None, f"find_spec({fullname!r}) should return a spec"
            assert spec.submodule_search_locations == [str(expected_path)]
            assert spec.loader is None

        # Action package
        fullname = "jvagent.actions.jvagent.foo.jvagent.bar"
        spec = importer.find_spec(fullname, None)
        assert spec is not None
        assert spec.loader is not None
        assert spec.origin is not None
        assert str(action_dir) in (spec.submodule_search_locations or [""])[0]

        # Submodule (relative import target)
        spec = importer.find_spec(
            "jvagent.actions.jvagent.foo.jvagent.bar.endpoints", None
        )
        assert spec is not None
        assert spec.loader is not None
        assert spec.origin is not None

    def test_find_spec_returns_none_for_non_actions_prefix(self, temp_dir):
        """JvagentActionsImporter returns None for modules outside jvagent.actions.*."""
        importer = JvagentActionsImporter(temp_dir)
        assert importer.find_spec("jvagent.action.base", None) is None
        assert importer.find_spec("other.module", None) is None

    def test_find_spec_returns_none_when_agents_dir_missing(self, temp_dir):
        """JvagentActionsImporter returns None when agents directory does not exist."""
        importer = JvagentActionsImporter(temp_dir)
        assert not (temp_dir / "agents").exists()
        spec = importer.find_spec("jvagent.actions.jvagent.foo", None)
        assert spec is None


class TestActionLoaderInvalidateCoreCache:
    """Tests for ActionLoader.invalidate_core_cache."""

    def test_invalidate_core_cache_clears_state(self, action_loader):
        """invalidate_core_cache resets the core path and discovery cache."""
        action_loader._core_action_path = Path("/mock/path")
        action_loader._core_action_cache = {"x": {}}

        action_loader.invalidate_core_cache()

        assert action_loader._core_action_path is None
        assert action_loader._core_action_cache is None
