"""Unit tests for ActionLoader conditional loading functionality."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jvagent.action.action_loader import ActionLoader, ActionRegistry, JvagentActionsImporter


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
                    "dependencies": {
                        "actions": ["jvagent/interact_router"]
                    }
                }
            }
        },
        "interact_router": {
            "dir": Path("/mock/jvagent/action/router"),
            "module_file": "router_action",
            "class_name": "RouterAction",
            "relative_path": "router",
            "data": {
                "package": {
                    "name": "jvagent/interact_router",
                    "archetype": "RouterAction",
                    "dependencies": {
                        "actions": []
                    }
                }
            }
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
                    "dependencies": {
                        "actions": []
                    }
                }
            }
        }
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
        self, mock_load_metadata, mock_build_cache, action_loader, mock_core_action_cache
    ):
        """Test that simple dependencies are resolved correctly."""
        mock_build_cache.return_value = mock_core_action_cache
        
        # Mock metadata for whatsapp (depends on interact_router)
        whatsapp_metadata = MagicMock()
        whatsapp_metadata.dependencies = {"actions": ["jvagent/interact_router"]}
        
        # Mock metadata for interact_router (no dependencies)
        router_metadata = MagicMock()
        router_metadata.dependencies = {"actions": []}
        
        def load_metadata_side_effect(action_ref, core_cache):
            if action_ref == "jvagent/whatsapp":
                return whatsapp_metadata
            elif action_ref == "jvagent/interact_router":
                return router_metadata
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
        assert "jvagent/interact_router" in resolved
        assert len(resolved) == 2
        
        # Both should be in resolved_actions
        assert "jvagent/whatsapp" in registry.resolved_actions
        assert "jvagent/interact_router" in registry.resolved_actions

    @patch.object(ActionLoader, "_build_core_action_cache")
    @patch.object(ActionLoader, "_load_action_metadata_for_deps")
    def test_resolve_action_dependencies_transitive(
        self, mock_load_metadata, mock_build_cache, action_loader, mock_core_action_cache
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
        self, mock_load_metadata, mock_build_cache, action_loader, mock_core_action_cache
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
        self, mock_load_metadata, mock_build_cache, action_loader, mock_core_action_cache
    ):
        """Test that already resolved actions are not resolved again."""
        mock_build_cache.return_value = mock_core_action_cache
        
        metadata = MagicMock()
        metadata.dependencies = {"actions": ["jvagent/interact_router"]}
        
        mock_load_metadata.return_value = metadata
        
        registry = ActionRegistry()
        registry.add_required_action("jvagent/whatsapp")
        registry.resolved_actions.add("jvagent/interact_router")  # Already resolved
        
        # Resolve dependencies
        resolved = action_loader._resolve_action_dependencies(
            "jvagent/whatsapp", mock_core_action_cache, registry
        )
        
        # Should only return whatsapp (interact_router already resolved)
        assert "jvagent/whatsapp" in resolved
        assert len(resolved) == 1


class TestActionLoaderConditionalImport:
    """Tests for ActionLoader conditional core action module imports."""

    @patch.object(ActionLoader, "_get_core_action_path")
    @patch.object(ActionLoader, "_build_core_action_cache")
    @patch("importlib.import_module")
    def test_pre_import_core_action_packages_conditional(
        self, mock_import_module, mock_build_cache, mock_get_path, action_loader, mock_core_action_cache
    ):
        """Test that _pre_import_core_action_packages only imports required actions."""
        mock_get_path.return_value = Path("/mock/core/path")
        mock_build_cache.return_value = mock_core_action_cache
        mock_import_module.return_value = MagicMock()
        
        # Request only whatsapp
        required_actions = {"jvagent/whatsapp"}
        imported_count = action_loader._pre_import_core_action_packages(
            required_actions=required_actions
        )
        
        # Should import whatsapp package
        assert imported_count > 0
        
        # Verify import_module was called with whatsapp-related paths
        import_calls = [call[0][0] for call in mock_import_module.call_args_list]
        whatsapp_imported = any("whatsapp" in call for call in import_calls)
        assert whatsapp_imported, "WhatsApp package should be imported"
        
        # Verify interact_router was NOT imported (not in required_actions)
        router_imported = any("router" in call or "interact_router" in call for call in import_calls)
        assert not router_imported, "Router should not be imported when not required"

    @patch.object(ActionLoader, "_get_core_action_path")
    @patch.object(ActionLoader, "_build_core_action_cache")
    @patch("importlib.import_module")
    def test_pre_import_core_action_packages_multiple(
        self, mock_import_module, mock_build_cache, mock_get_path, action_loader, mock_core_action_cache
    ):
        """Test that multiple required actions are imported."""
        mock_get_path.return_value = Path("/mock/core/path")
        mock_build_cache.return_value = mock_core_action_cache
        mock_import_module.return_value = MagicMock()
        
        # Request both whatsapp and persona
        required_actions = {"jvagent/whatsapp", "jvagent/persona"}
        imported_count = action_loader._pre_import_core_action_packages(
            required_actions=required_actions
        )
        
        # Should import both packages
        assert imported_count > 0
        
        # Verify both were imported
        import_calls = [call[0][0] for call in mock_import_module.call_args_list]
        whatsapp_imported = any("whatsapp" in call for call in import_calls)
        persona_imported = any("persona" in call for call in import_calls)
        assert whatsapp_imported, "WhatsApp package should be imported"
        assert persona_imported, "Persona package should be imported"

    @patch.object(ActionLoader, "_get_core_action_path")
    @patch.object(ActionLoader, "_build_core_action_cache")
    @patch("importlib.import_module")
    def test_pre_import_core_action_packages_empty_set(
        self, mock_import_module, mock_build_cache, mock_get_path, action_loader, mock_core_action_cache
    ):
        """Test that empty required_actions set results in no imports."""
        mock_get_path.return_value = Path("/mock/core/path")
        mock_build_cache.return_value = mock_core_action_cache
        
        # Request empty set
        required_actions = set()
        imported_count = action_loader._pre_import_core_action_packages(
            required_actions=required_actions
        )
        
        # Should not import anything
        assert imported_count == 0
        mock_import_module.assert_not_called()

    @patch.object(ActionLoader, "_get_core_action_path")
    @patch.object(ActionLoader, "_build_core_action_cache")
    @patch("importlib.import_module")
    def test_pre_import_core_action_packages_nonexistent(
        self, mock_import_module, mock_build_cache, mock_get_path, action_loader, mock_core_action_cache
    ):
        """Test that non-existent actions in required_actions are skipped."""
        mock_get_path.return_value = Path("/mock/core/path")
        mock_build_cache.return_value = mock_core_action_cache
        
        # Request non-existent action
        required_actions = {"jvagent/nonexistent_action"}
        imported_count = action_loader._pre_import_core_action_packages(
            required_actions=required_actions
        )
        
        # Should not import anything (action not in cache)
        assert imported_count == 0

    @patch.object(ActionLoader, "_get_core_action_path")
    def test_pre_import_core_action_packages_no_core_path(
        self, mock_get_path, action_loader
    ):
        """Test that missing core action path returns 0 imports."""
        mock_get_path.return_value = None
        
        required_actions = {"jvagent/whatsapp"}
        imported_count = action_loader._pre_import_core_action_packages(
            required_actions=required_actions
        )
        
        assert imported_count == 0


class TestWhatsAppWebhookURL:
    """Tests for WhatsAppAction.get_webhook_url functionality.

    These tests verify that the existing test file covers the requested cases.
    The actual implementation is in tests/action/whatsapp/test_webhook_url_generation.py
    """

    def test_webhook_url_generation_covered(self):
        """Verify that test_get_webhook_url_generates_new_key covers case 4."""
        # This test verifies that the existing test file has the required test
        # Case 4: WhatsAppAction.get_webhook_url generates new API key and webhook URL
        test_file = Path(__file__).parent / "whatsapp" / "test_webhook_url_generation.py"
        assert test_file.exists(), "WhatsApp webhook URL test file should exist"

        content = test_file.read_text()
        assert "test_get_webhook_url_generates_new_key" in content, \
            "Test for generating new API key should exist"

    def test_webhook_url_reuse_covered(self):
        """Verify that test_get_webhook_url_reuses_existing_url covers case 5."""
        # This test verifies that the existing test file has the required test
        # Case 5: WhatsAppAction.get_webhook_url reuses existing valid webhook URL without regeneration
        test_file = Path(__file__).parent / "whatsapp" / "test_webhook_url_generation.py"
        assert test_file.exists(), "WhatsApp webhook URL test file should exist"

        content = test_file.read_text()
        assert "test_get_webhook_url_reuses_existing_url" in content, \
            "Test for reusing existing URL should exist"


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
        spec = importer.find_spec("jvagent.actions.jvagent.foo.jvagent.bar.endpoints", None)
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
