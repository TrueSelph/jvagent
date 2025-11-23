"""Test the plugin system implementation.

This module tests the complete plugin-based subsystem including:
- ActionLoader: Action discovery and loading
- AgentLoader: Agent installation from descriptors
- AppLoader: Application bootstrap from app.yaml
- Actions Manager: Action lifecycle management
"""

import pytest

from jvagent.action.action_loader import ActionLoader, ActionMetadata
from jvagent.action.actions import Actions
from jvagent.core.agent import Agent
from jvagent.core.agent_loader import AgentDescriptor, AgentLoader
from jvagent.core.agents import Agents
from jvagent.core.app import App
from jvagent.core.app_loader import AppDescriptor, AppLoader


class TestActionLoader:
    """Test ActionLoader functionality."""

    @pytest.mark.asyncio
    async def test_action_discovery(self, temp_dir, test_db):
        """Test action discovery."""
        # Create action directory structure
        agent_dir = temp_dir / "agents" / "test_namespace" / "test_agent"
        action_dir = agent_dir / "actions" / "test_namespace" / "test_action"
        action_dir.mkdir(parents=True)

        # Create action implementation
        action_py = action_dir / "test_action.py"
        action_py.write_text(
            """from jvagent.action.action import Action

class TestAction(Action):
    pass
"""
        )

        # Create info.yaml
        info_yaml = action_dir / "info.yaml"
        info_yaml.write_text(
            """name: test_action
title: Test Action
version: 1.0.0
description: Test action
enabled: true

archetype: TestAction
module: test_action
"""
        )

        loader = ActionLoader(str(temp_dir))
        actions = loader.discover_actions("test_namespace", "test_agent")

        assert len(actions) > 0, "No actions found"
        assert any(a.name == "test_action" for a in actions), "test_action not found"

    @pytest.mark.asyncio
    async def test_action_class_loading(self, temp_dir, test_db):
        """Test action class loading."""
        # Create action directory structure
        agent_dir = temp_dir / "agents" / "test_namespace" / "test_agent"
        action_dir = agent_dir / "actions" / "test_namespace" / "test_action"
        action_dir.mkdir(parents=True)

        # Create action implementation
        action_py = action_dir / "test_action.py"
        action_py.write_text(
            """from jvagent.action.action import Action

class TestAction(Action):
    pass
"""
        )

        # Create info.yaml
        info_yaml = action_dir / "info.yaml"
        info_yaml.write_text(
            """package:
  name: test_namespace/test_action
  archetype: TestAction
  version: 1.0.0
  meta:
    title: Test Action
    description: Test action
"""
        )

        loader = ActionLoader(str(temp_dir))
        actions = loader.discover_actions("test_namespace", "test_agent")

        assert len(actions) > 0, "No actions found"

        metadata = actions[0]
        action_class = loader.load_action_class(metadata)

        assert action_class is not None, "Failed to load class"
        assert action_class.__name__ == "TestAction", "Wrong class loaded"

    @pytest.mark.asyncio
    async def test_action_instance_creation(self, temp_dir, test_db):
        """Test action instance creation."""
        # Create action directory structure
        agent_dir = temp_dir / "agents" / "test_namespace" / "test_agent"
        action_dir = agent_dir / "actions" / "test_namespace" / "test_action"
        action_dir.mkdir(parents=True)

        # Create action implementation
        action_py = action_dir / "test_action.py"
        action_py.write_text(
            """from jvagent.action.action import Action

class TestAction(Action):
    pass
"""
        )

        # Create info.yaml
        info_yaml = action_dir / "info.yaml"
        info_yaml.write_text(
            """package:
  name: test_namespace/test_action
  archetype: TestAction
  version: 1.0.0
  meta:
    title: Test Action
    description: Test action
"""
        )

        loader = ActionLoader(str(temp_dir))
        actions = loader.discover_actions("test_namespace", "test_agent")

        assert len(actions) > 0, "No actions found"

        metadata = actions[0]
        action_class = loader.load_action_class(metadata)

        assert action_class is not None, "Failed to load class"

        action_instance = loader.create_action_instance(
            metadata, agent_id="test_agent_id", agent_name="test_agent", action_class=action_class
        )

        assert action_instance is not None, "Failed to create instance"
        assert action_instance.label == "test_action", "Label not set correctly"


class TestAgentLoader:
    """Test AgentLoader functionality."""

    @pytest.mark.asyncio
    async def test_agent_discovery(self, temp_dir, test_db):
        """Test agent discovery."""
        # Create agent directory structure
        agent_dir = temp_dir / "agents" / "test_namespace" / "test_agent"
        agent_dir.mkdir(parents=True)

        # Create agent.yaml
        agent_yaml = agent_dir / "agent.yaml"
        agent_yaml.write_text(
            """agent: test_namespace/test_agent
version: 1.0.0
"""
        )

        loader = AgentLoader(str(temp_dir))
        agents = loader.discover_agents()

        assert len(agents) > 0, "No agents found"
        assert any(
            ns == "test_namespace" and name == "test_agent" for ns, name in agents
        ), "test_agent not found"

    @pytest.mark.asyncio
    async def test_agent_descriptor_loading(self, temp_dir, test_db):
        """Test agent descriptor loading."""
        # Create agent directory structure
        agent_dir = temp_dir / "agents" / "test_namespace" / "test_agent"
        agent_dir.mkdir(parents=True)

        # Create agent.yaml
        agent_yaml = agent_dir / "agent.yaml"
        agent_yaml.write_text(
            """agent: test_namespace/test_agent
version: 1.0.0
author: Test Author

context:
  alias: Test Agent
  description: Test agent description
"""
        )

        loader = AgentLoader(str(temp_dir))
        agents = loader.discover_agents()

        assert len(agents) > 0, "No agents found"

        namespace, agent_name = agents[0]
        descriptor = loader.load_agent_descriptor(namespace, agent_name)

        assert descriptor is not None, "Failed to load descriptor"
        assert descriptor.name == "test_agent", "Name not set correctly"
        assert descriptor.namespace == "test_namespace", "Namespace not set correctly"


class TestAppLoader:
    """Test AppLoader functionality."""

    @pytest.mark.asyncio
    async def test_app_descriptor_loading(self, temp_dir, test_db):
        """Test app descriptor loading."""
        # Create app.yaml
        app_yaml = temp_dir / "app.yaml"
        app_yaml.write_text(
            """app: test_app
version: 1.0.0
author: Test Author

context:
  name: Test App
  description: Test application

agents:
  - test_namespace/test_agent
"""
        )

        loader = AppLoader(str(temp_dir))
        descriptor = loader.load_app_descriptor()

        assert descriptor is not None, "Failed to load descriptor"
        assert descriptor.app_id == "test_app", "App ID not set correctly"
        assert descriptor.name == "Test App", "Name not set correctly"
        assert len(descriptor.agents) > 0, "No agents in descriptor"


class TestFullBootstrap:
    """Test complete bootstrap process."""

    @pytest.mark.asyncio
    async def test_full_bootstrap(self, temp_dir, test_db):
        """Test complete bootstrap process."""
        # Create app.yaml
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

        # Create agent directory structure
        agent_dir = temp_dir / "agents" / "test_namespace" / "test_agent"
        agent_dir.mkdir(parents=True)

        # Create agent.yaml
        agent_yaml = agent_dir / "agent.yaml"
        agent_yaml.write_text(
            """agent: test_namespace/test_agent
version: 1.0.0

context:
  enabled: true
"""
        )

        # Bootstrap application
        loader = AppLoader(str(temp_dir))
        app = await loader.bootstrap_application(update_if_exists=True)

        assert app is not None, "Bootstrap failed"
        assert app.name == "Test App", "App name not set correctly"

        # Verify Agents node
        app_nodes = await app.nodes()
        agents_manager = None

        for node in app_nodes:
            if isinstance(node, Agents):
                agents_manager = node
                break

        assert agents_manager is not None, "Agents manager not found"

        # Verify agent installation
        agents = await Agent.find({})

        assert len(agents) > 0, "No agents found"

        for agent in agents:
            assert agent.name is not None, "Agent name not set"

            # Check for Actions manager
            agent_nodes = await agent.nodes()
            actions_manager = None

            for node in agent_nodes:
                if isinstance(node, Actions):
                    actions_manager = node
                    break

            # Actions manager is created even if no actions are defined
            assert actions_manager is not None, "Actions manager not found"
