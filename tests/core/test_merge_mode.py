"""Test merge mode for --update flag.

Verifies that when update_mode="merge" is used:
- DB-level configurations are always preserved for existing entities
- Source identity (version, app_id) is updated; context config preserved
- Metadata is always updated from source
- Actions removed from agent.yaml are deregistered
- New actions added to agent.yaml are created
- on_reload() is called for existing actions (not on_register())
- post_register() is called for merged actions
"""

import json
import os

import pytest

from jvagent.action.base import Action
from jvagent.core.agent import Agent
from jvagent.core.app import App
from jvagent.core.app_loader import AppLoader


class TestAppMergeMode:
    """Test app-level merge mode preserves DB values."""

    @pytest.mark.asyncio
    async def test_app_merge_preserves_db_values_not_in_context(
        self, temp_dir, test_db
    ):
        """DB values for all context fields are preserved; only version/app_id updated."""
        app_yaml = temp_dir / "app.yaml"
        app_yaml.write_text(
            """app: test_app
version: 1.0.0

context:
  name: Test App

agents:
  - test_namespace/test_agent
"""
        )

        agent_dir = temp_dir / "agents" / "test_namespace" / "test_agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent.yaml").write_text(
            """agent: test_namespace/test_agent
version: 1.0.0
"""
        )

        # Bootstrap with source to create initial state
        loader = AppLoader(str(temp_dir))
        await loader.bootstrap_application(update_mode="source")

        app = await App.get()
        assert app is not None
        # Modify DB directly (simulate admin UI change)
        app.description = "DB-modified-description"
        app.log_retention_days = 90
        await app.save()

        # Update app.yaml: add only version in context, NOT description
        app_yaml.write_text(
            """app: test_app
version: 2.0.0

context:
  name: Test App Updated

agents:
  - test_namespace/test_agent
"""
        )

        # Bootstrap with merge
        await loader.bootstrap_application(update_mode="merge")

        app = await App.get()
        assert app is not None
        # version and app_id are source identity - updated from YAML
        assert app.version == "2.0.0"
        # All context fields preserved from DB (name stays "Test App" from initial bootstrap)
        assert app.name == "Test App"
        assert app.description == "DB-modified-description"
        assert app.log_retention_days == 90


class TestAgentMergeMode:
    """Test agent-level merge mode preserves DB values."""

    @pytest.mark.asyncio
    async def test_agent_merge_preserves_db_values_not_in_context(
        self, temp_dir, test_db
    ):
        """DB values for all agent fields are preserved in merge mode."""
        app_yaml = temp_dir / "app.yaml"
        app_yaml.write_text(
            """app: test_app
version: 1.0.0
context:
  name: Test App
agents:
  - test_namespace/test_agent
"""
        )

        agent_dir = temp_dir / "agents" / "test_namespace" / "test_agent"
        agent_dir.mkdir(parents=True)
        agent_yaml = agent_dir / "agent.yaml"
        agent_yaml.write_text(
            """agent: test_namespace/test_agent
version: 1.0.0
context:
  enabled: true
  description: Initial description
"""
        )

        loader = AppLoader(str(temp_dir))
        await loader.bootstrap_application(update_mode="source")

        agent = await Agent.find_one(
            {"context.name": "test_agent", "context.namespace": "test_namespace"}
        )
        assert agent is not None
        # Modify DB directly
        agent.description = "DB-modified-agent-description"
        await agent.save()

        # Update agent.yaml: only enabled in context, NOT description
        agent_yaml.write_text(
            """agent: test_namespace/test_agent
version: 1.0.0
context:
  enabled: false
"""
        )

        await loader.bootstrap_application(update_mode="merge")

        agent = await Agent.find_one(
            {"context.name": "test_agent", "context.namespace": "test_namespace"}
        )
        assert agent is not None
        # All agent fields preserved from DB (enabled stays True, description stays modified)
        assert agent.enabled is True
        assert agent.description == "DB-modified-agent-description"

    @pytest.mark.asyncio
    async def test_agent_merge_preserves_db_values_even_when_in_yaml(
        self, temp_dir, test_db
    ):
        """DB values are preserved even when YAML has explicit values for same field."""
        app_yaml = temp_dir / "app.yaml"
        app_yaml.write_text(
            """app: test_app
version: 1.0.0
context:
  name: Test App
agents:
  - test_namespace/test_agent
"""
        )

        agent_dir = temp_dir / "agents" / "test_namespace" / "test_agent"
        agent_dir.mkdir(parents=True)
        agent_yaml = agent_dir / "agent.yaml"
        agent_yaml.write_text(
            """agent: test_namespace/test_agent
version: 1.0.0
context:
  enabled: true
  description: Original
"""
        )

        loader = AppLoader(str(temp_dir))
        await loader.bootstrap_application(update_mode="source")

        agent = await Agent.find_one(
            {"context.name": "test_agent", "context.namespace": "test_namespace"}
        )
        assert agent is not None
        agent.description = "DB-value"
        await agent.save()

        # Update agent.yaml with explicit description
        agent_yaml.write_text(
            """agent: test_namespace/test_agent
version: 1.0.0
context:
  enabled: true
  description: YAML-override
"""
        )

        await loader.bootstrap_application(update_mode="merge")

        agent = await Agent.find_one(
            {"context.name": "test_agent", "context.namespace": "test_namespace"}
        )
        assert agent is not None
        # DB value preserved, not overwritten by YAML
        assert agent.description == "DB-value"


class TestActionMergeMode:
    """Test action-level merge mode: sync, metadata, lifecycle hooks."""

    @pytest.mark.asyncio
    async def test_merge_removes_actions_not_in_agent_yaml(self, temp_dir, test_db):
        """Actions removed from agent.yaml are deregistered in merge mode."""
        app_yaml = temp_dir / "app.yaml"
        app_yaml.write_text(
            """app: test_app
version: 1.0.0
context:
  name: Test App
agents:
  - test_namespace/test_agent
"""
        )

        agent_dir = temp_dir / "agents" / "test_namespace" / "test_agent"
        action_a_dir = agent_dir / "actions" / "test_namespace" / "action_a"
        action_b_dir = agent_dir / "actions" / "test_namespace" / "action_b"
        action_a_dir.mkdir(parents=True)
        action_b_dir.mkdir(parents=True)

        for name, dir_path in [("action_a", action_a_dir), ("action_b", action_b_dir)]:
            (dir_path / f"{name}.py").write_text(
                f"""from jvagent.action.base import Action
class {name.title().replace("_", "")}(Action):
    pass
"""
            )
            (dir_path / "info.yaml").write_text(
                f"""package:
  name: test_namespace/{name}
  archetype: {name.title().replace("_", "")}
  version: 1.0.0
  meta:
    title: {name}
"""
            )

        agent_yaml = agent_dir / "agent.yaml"
        agent_yaml.write_text(
            """agent: test_namespace/test_agent
version: 1.0.0
context:
  enabled: true
actions:
  - action: test_namespace/action_a
  - action: test_namespace/action_b
"""
        )

        loader = AppLoader(str(temp_dir))
        await loader.bootstrap_application(update_mode="source")

        agent = await Agent.find_one(
            {"context.name": "test_agent", "context.namespace": "test_namespace"}
        )
        assert agent is not None
        actions_mgr = await agent.node(node="Actions")
        assert actions_mgr is not None
        actions_before = await actions_mgr.nodes(node=Action)
        assert len(actions_before) == 2

        # Remove action_b from agent.yaml
        agent_yaml.write_text(
            """agent: test_namespace/test_agent
version: 1.0.0
context:
  enabled: true
actions:
  - action: test_namespace/action_a
"""
        )

        await loader.bootstrap_application(update_mode="merge")

        actions_after = await actions_mgr.nodes(node=Action)
        assert len(actions_after) == 1
        assert actions_after[0].label == "action_a"

        # DB-level verification: removed action must not exist in database
        db_actions = await Action.find({"context.agent_id": agent.id})
        assert len(db_actions) == 1
        assert db_actions[0].label == "action_a"

    @pytest.mark.asyncio
    async def test_merge_removes_all_actions_when_agent_yaml_has_empty_actions(
        self, temp_dir, test_db
    ):
        """When agent.yaml has empty actions list, all actions are removed in merge mode."""
        app_yaml = temp_dir / "app.yaml"
        app_yaml.write_text(
            """app: test_app
version: 1.0.0
context:
  name: Test App
agents:
  - test_namespace/test_agent
"""
        )

        agent_dir = temp_dir / "agents" / "test_namespace" / "test_agent"
        action_dir = agent_dir / "actions" / "test_namespace" / "orphan_action"
        action_dir.mkdir(parents=True)

        (action_dir / "orphan_action.py").write_text(
            """from jvagent.action.base import Action
class OrphanAction(Action):
    pass
"""
        )
        (action_dir / "info.yaml").write_text(
            """package:
  name: test_namespace/orphan_action
  archetype: OrphanAction
  version: 1.0.0
  meta:
    title: Orphan Action
"""
        )

        agent_yaml = agent_dir / "agent.yaml"
        agent_yaml.write_text(
            """agent: test_namespace/test_agent
version: 1.0.0
context:
  enabled: true
actions:
  - action: test_namespace/orphan_action
"""
        )

        loader = AppLoader(str(temp_dir))
        await loader.bootstrap_application(update_mode="source")

        agent = await Agent.find_one(
            {"context.name": "test_agent", "context.namespace": "test_namespace"}
        )
        actions_mgr = await agent.node(node="Actions")
        actions_before = await actions_mgr.nodes(node=Action)
        assert len(actions_before) == 1

        # Remove all actions from agent.yaml (empty actions list)
        agent_yaml.write_text(
            """agent: test_namespace/test_agent
version: 1.0.0
context:
  enabled: true
actions: []
"""
        )

        await loader.bootstrap_application(update_mode="merge")

        actions_after = await actions_mgr.nodes(node=Action)
        assert len(actions_after) == 0

        # DB-level verification: all actions must be removed from database
        db_actions = await Action.find({"context.agent_id": agent.id})
        assert len(db_actions) == 0

    @pytest.mark.asyncio
    async def test_merge_sweeps_ghost_action_nodes_with_unimported_classes(
        self, temp_dir, test_db
    ):
        """Action nodes whose class is not imported are swept during merge.

        When an action is removed from agent.yaml its module is no longer
        pre-imported, making its entity type invisible to Action.find() and
        nodes(node=Action).  The raw-DB sweep should still detect and remove
        these "ghost" nodes.
        """
        from jvspatial.core.context import get_default_context
        from jvspatial.core.entities.node import Node

        app_yaml = temp_dir / "app.yaml"
        app_yaml.write_text(
            """app: test_app
version: 1.0.0
context:
  name: Test App
agents:
  - test_namespace/test_agent
"""
        )

        agent_dir = temp_dir / "agents" / "test_namespace" / "test_agent"
        action_a_dir = agent_dir / "actions" / "test_namespace" / "action_a"
        action_a_dir.mkdir(parents=True)

        (action_a_dir / "action_a.py").write_text(
            """from jvagent.action.base import Action
class ActionA(Action):
    pass
"""
        )
        (action_a_dir / "info.yaml").write_text(
            """package:
  name: test_namespace/action_a
  archetype: ActionA
  version: 1.0.0
  meta:
    title: Action A
"""
        )

        agent_yaml = agent_dir / "agent.yaml"
        agent_yaml.write_text(
            """agent: test_namespace/test_agent
version: 1.0.0
context:
  enabled: true
actions:
  - action: test_namespace/action_a
"""
        )

        loader = AppLoader(str(temp_dir))
        await loader.bootstrap_application(update_mode="source")

        agent = await Agent.find_one(
            {"context.name": "test_agent", "context.namespace": "test_namespace"}
        )
        assert agent is not None
        actions_mgr = await agent.node(node="Actions")
        assert actions_mgr is not None

        # Inject a ghost node directly into the DB with an entity type that is
        # not an imported subclass.  This simulates a previously-registered
        # action whose module is no longer loaded.
        context = get_default_context()
        type_code = context._get_entity_type_code(Node)
        collection = context._get_collection_name(type_code)

        ghost_id = "ghost-action-node-id"
        ghost_record = {
            "id": ghost_id,
            "entity": "UnimportedGhostAction",
            "context": {
                "agent_id": agent.id,
                "namespace": "test_namespace",
                "label": "ghost_action",
                "enabled": True,
                "description": "ghost",
                "metadata": {},
            },
            "edges": [],
        }
        await context.database.save(collection, ghost_record)

        # Also create an edge from the Actions manager to the ghost
        edge_id = "ghost-edge-id"
        edge_record = {
            "id": edge_id,
            "entity": "Edge",
            "source": actions_mgr.id,
            "target": ghost_id,
            "context": {},
        }
        await context.database.save("edge", edge_record)

        # Verify the ghost exists via raw DB
        raw_before = await context.database.find(
            collection, {"context.agent_id": agent.id}
        )
        ghost_before = [r for r in raw_before if r["id"] == ghost_id]
        assert len(ghost_before) == 1

        # Verify Action.find does NOT see the ghost (entity type unknown)
        visible = await Action.find({"context.agent_id": agent.id})
        visible_ids = {a.id for a in visible}
        assert ghost_id not in visible_ids

        # Re-bootstrap with merge — should sweep the ghost
        await loader.bootstrap_application(update_mode="merge")

        # Ghost must be gone from raw DB
        raw_after = await context.database.find(
            collection, {"context.agent_id": agent.id}
        )
        ghost_after = [r for r in raw_after if r["id"] == ghost_id]
        assert (
            len(ghost_after) == 0
        ), "Ghost action node with unimported entity type was not removed"

        # The edge pointing to the ghost must also be gone (no remnant edges)
        edges_after = await context.database.find("edge", {"target": ghost_id})
        assert (
            len(edges_after) == 0
        ), "Remnant edge pointing to removed ghost action node was not cleaned up"

        # action_a must still exist
        remaining = [
            r for r in raw_after if r.get("context", {}).get("label") == "action_a"
        ]
        assert len(remaining) == 1

    @pytest.mark.asyncio
    async def test_merge_adds_new_actions_from_agent_yaml(self, temp_dir, test_db):
        """New actions added to agent.yaml are created in merge mode."""
        app_yaml = temp_dir / "app.yaml"
        app_yaml.write_text(
            """app: test_app
version: 1.0.0
context:
  name: Test App
agents:
  - test_namespace/test_agent
"""
        )

        agent_dir = temp_dir / "agents" / "test_namespace" / "test_agent"
        action_a_dir = agent_dir / "actions" / "test_namespace" / "action_a"
        action_b_dir = agent_dir / "actions" / "test_namespace" / "action_b"
        action_a_dir.mkdir(parents=True)
        action_b_dir.mkdir(parents=True)

        for name, dir_path in [("action_a", action_a_dir), ("action_b", action_b_dir)]:
            (dir_path / f"{name}.py").write_text(
                f"""from jvagent.action.base import Action
class {name.title().replace("_", "")}(Action):
    pass
"""
            )
            (dir_path / "info.yaml").write_text(
                f"""package:
  name: test_namespace/{name}
  archetype: {name.title().replace("_", "")}
  version: 1.0.0
  meta:
    title: {name}
"""
            )

        agent_yaml = agent_dir / "agent.yaml"
        agent_yaml.write_text(
            """agent: test_namespace/test_agent
version: 1.0.0
context:
  enabled: true
actions:
  - action: test_namespace/action_a
"""
        )

        loader = AppLoader(str(temp_dir))
        await loader.bootstrap_application(update_mode="source")

        agent = await Agent.find_one(
            {"context.name": "test_agent", "context.namespace": "test_namespace"}
        )
        actions_mgr = await agent.node(node="Actions")
        actions_before = await actions_mgr.nodes(node=Action)
        assert len(actions_before) == 1

        # Add action_b to agent.yaml
        agent_yaml.write_text(
            """agent: test_namespace/test_agent
version: 1.0.0
context:
  enabled: true
actions:
  - action: test_namespace/action_a
  - action: test_namespace/action_b
"""
        )

        await loader.bootstrap_application(update_mode="merge")

        actions_after = await actions_mgr.nodes(node=Action)
        assert len(actions_after) == 2
        labels = {a.label for a in actions_after}
        assert labels == {"action_a", "action_b"}

    @pytest.mark.asyncio
    async def test_merge_updates_metadata_for_existing_actions(self, temp_dir, test_db):
        """Metadata is always updated for existing actions in merge mode."""
        app_yaml = temp_dir / "app.yaml"
        app_yaml.write_text(
            """app: test_app
version: 1.0.0
context:
  name: Test App
agents:
  - test_namespace/test_agent
"""
        )

        agent_dir = temp_dir / "agents" / "test_namespace" / "test_agent"
        action_dir = agent_dir / "actions" / "test_namespace" / "merge_action"
        action_dir.mkdir(parents=True)

        action_py = action_dir / "merge_action.py"
        info_yaml = action_dir / "info.yaml"

        action_py.write_text(
            """from jvagent.action.base import Action
class MergeAction(Action):
    pass
"""
        )
        info_yaml.write_text(
            """package:
  name: test_namespace/merge_action
  archetype: MergeAction
  version: 1.0.0
  meta:
    title: Merge Action v1
"""
        )

        agent_yaml = agent_dir / "agent.yaml"
        agent_yaml.write_text(
            """agent: test_namespace/test_agent
version: 1.0.0
context:
  enabled: true
actions:
  - action: test_namespace/merge_action
"""
        )

        loader = AppLoader(str(temp_dir))
        await loader.bootstrap_application(update_mode="source")

        agent = await Agent.find_one(
            {"context.name": "test_agent", "context.namespace": "test_namespace"}
        )
        actions_mgr = await agent.node(node="Actions")
        actions = await actions_mgr.nodes(node=Action)
        action = next(a for a in actions if a.label == "merge_action")
        assert action.metadata.get("version") == "1.0.0"

        # Update info.yaml version
        info_yaml.write_text(
            """package:
  name: test_namespace/merge_action
  archetype: MergeAction
  version: 2.0.0
  meta:
    title: Merge Action v2
"""
        )

        await loader.bootstrap_application(update_mode="merge")

        actions = await actions_mgr.nodes(node=Action)
        action = next(a for a in actions if a.label == "merge_action")
        # Metadata should reflect new version from source
        assert action.metadata.get("version") == "2.0.0"
        assert "v2" in action.metadata.get("title", "")

    @pytest.mark.asyncio
    async def test_merge_calls_on_reload_and_post_register_for_existing_actions(
        self, temp_dir, test_db
    ):
        """on_reload and post_register are called for merged existing actions."""
        hook_log = temp_dir / "hook_log.txt"
        hook_log.write_text("")
        os.environ["JVAGENT_TEST_HOOK_LOG"] = str(hook_log)

        try:
            app_yaml = temp_dir / "app.yaml"
            app_yaml.write_text(
                """app: test_app
version: 1.0.0
context:
  name: Test App
agents:
  - test_namespace/test_agent
"""
            )

            agent_dir = temp_dir / "agents" / "test_namespace" / "test_agent"
            action_dir = agent_dir / "actions" / "test_namespace" / "hook_action"
            action_dir.mkdir(parents=True)

            action_py = action_dir / "hook_action.py"
            action_py.write_text(
                """from jvagent.action.base import Action
import os
import json

def _log(hook, label):
    path = os.environ.get("JVAGENT_TEST_HOOK_LOG")
    if path:
        with open(path, "a") as f:
            f.write(json.dumps({"hook": hook, "label": label}) + "\\n")

class HookAction(Action):
    async def on_register(self):
        _log("on_register", self.label)
    async def on_reload(self):
        _log("on_reload", self.label)
    async def post_register(self):
        _log("post_register", self.label)
"""
            )
            (action_dir / "info.yaml").write_text(
                """package:
  name: test_namespace/hook_action
  archetype: HookAction
  version: 1.0.0
  meta:
    title: Hook Action
"""
            )

            agent_yaml = agent_dir / "agent.yaml"
            agent_yaml.write_text(
                """agent: test_namespace/test_agent
version: 1.0.0
context:
  enabled: true
actions:
  - action: test_namespace/hook_action
"""
            )

            loader = AppLoader(str(temp_dir))
            await loader.bootstrap_application(update_mode="source")

            # Clear log and run merge
            hook_log.write_text("")
            await loader.bootstrap_application(update_mode="merge")

            log_content = hook_log.read_text()
            entries = [
                json.loads(line) for line in log_content.strip().split("\n") if line
            ]

            hooks_called = [
                e["hook"] for e in entries if e.get("label") == "hook_action"
            ]
            # For merge of existing action: on_reload should be called, NOT on_register
            assert "on_reload" in hooks_called
            assert "on_register" not in hooks_called
            # post_register should be called for merged actions
            assert "post_register" in hooks_called
        finally:
            os.environ.pop("JVAGENT_TEST_HOOK_LOG", None)

    @pytest.mark.asyncio
    async def test_merge_preserves_action_db_properties(self, temp_dir, test_db):
        """Action DB properties (e.g. enabled) are preserved when agent.yaml has different values."""
        app_yaml = temp_dir / "app.yaml"
        app_yaml.write_text(
            """app: test_app
version: 1.0.0
context:
  name: Test App
agents:
  - test_namespace/test_agent
"""
        )

        agent_dir = temp_dir / "agents" / "test_namespace" / "test_agent"
        action_dir = agent_dir / "actions" / "test_namespace" / "preserve_action"
        action_dir.mkdir(parents=True)

        (action_dir / "preserve_action.py").write_text(
            """from jvagent.action.base import Action
class PreserveAction(Action):
    pass
"""
        )
        (action_dir / "info.yaml").write_text(
            """package:
  name: test_namespace/preserve_action
  archetype: PreserveAction
  version: 1.0.0
  meta:
    title: Preserve Action
"""
        )

        agent_yaml = agent_dir / "agent.yaml"
        agent_yaml.write_text(
            """agent: test_namespace/test_agent
version: 1.0.0
context:
  enabled: true
actions:
  - action: test_namespace/preserve_action
    context:
      enabled: true
"""
        )

        loader = AppLoader(str(temp_dir))
        await loader.bootstrap_application(update_mode="source")

        agent = await Agent.find_one(
            {"context.name": "test_agent", "context.namespace": "test_namespace"}
        )
        assert agent is not None
        actions_mgr = await agent.node(node="Actions")
        actions = await actions_mgr.nodes(node=Action)
        action = next(a for a in actions if a.label == "preserve_action")
        assert action.enabled is True

        # Modify action in DB (simulate admin UI change)
        action.enabled = False
        await action.save()

        # Update agent.yaml with enabled: true for this action
        agent_yaml.write_text(
            """agent: test_namespace/test_agent
version: 1.0.0
context:
  enabled: true
actions:
  - action: test_namespace/preserve_action
    context:
      enabled: true
"""
        )

        await loader.bootstrap_application(update_mode="merge")

        actions = await actions_mgr.nodes(node=Action)
        action = next(a for a in actions if a.label == "preserve_action")
        # DB value preserved, not overwritten by agent.yaml context
        assert action.enabled is False
