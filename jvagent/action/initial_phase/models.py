"""Models for Initial Phase Action.

This module provides models for parameters, competencies, and workflows
used in the Initial Phase processing system.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from enum import Enum


class ExecutionRequirement(str, Enum):
    """When a parameter/competency should be executed."""

    ON_FIRST_INTERACTION = "on_first_interaction"
    ALWAYS_EXECUTE = "always_execute"
    CONDITIONAL = "conditional"


@dataclass
class Parameter:
    """Parameter for agent behavioral control.

    Parameters define conditions and responses for agent behavior,
    with optional action triggers and execution requirements.

    Attributes:
        id: Unique parameter identifier
        condition: When this parameter applies (semantic search target)
        response: Behavioral instruction or response template
        action: Optional action label to trigger
        workflow: Optional workflow identifier
        enabled: Whether parameter is active
        execution_requirement: When to execute (on_first_interaction, always_execute, conditional)
        metadata: Additional parameter metadata
        embedding: Vector embedding for semantic search (computed from condition)
    """

    id: str
    condition: str
    response: str
    action: Optional[str] = None
    workflow: Optional[str] = None
    enabled: bool = True
    execution_requirement: ExecutionRequirement = ExecutionRequirement.CONDITIONAL
    metadata: Dict[str, Any] = field(default_factory=dict)
    embedding: Optional[List[float]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "id": self.id,
            "condition": self.condition,
            "response": self.response,
            "action": self.action,
            "workflow": self.workflow,
            "enabled": self.enabled,
            "execution_requirement": self.execution_requirement.value,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Parameter":
        """Create Parameter from dictionary."""
        exec_req = data.get("execution_requirement", "conditional")
        if isinstance(exec_req, str):
            exec_req = ExecutionRequirement(exec_req)

        return cls(
            id=data.get("id", ""),
            condition=data.get("condition", ""),
            response=data.get("response", ""),
            action=data.get("action"),
            workflow=data.get("workflow"),
            enabled=data.get("enabled", True),
            execution_requirement=exec_req,
            metadata=data.get("metadata", {}),
        )

    def to_prompt_format(self) -> str:
        """Format for LLM prompt inclusion."""
        parts = [f"Condition: {self.condition}", f"Response: {self.response}"]
        if self.action:
            parts.append(f"Action: {self.action}")
        if self.workflow:
            parts.append(f"Workflow: {self.workflow}")
        return " | ".join(parts)


@dataclass
class Competency:
    """Competency for complex agent behaviors.

    Competencies represent multi-state flows, complex behavioral patterns,
    or multi-turn conversational capabilities.

    Attributes:
        id: Unique competency identifier
        label: Competency label
        name: Human-readable competency name
        description: Detailed description of what this competency does
        anchors: List of anchor phrases that trigger this competency
        states: List of state definitions for multi-turn flows
        actions: Actions required for this competency
        workflows: Workflows associated with this competency
        enabled: Whether competency is active
        execution_requirement: When to execute
        metadata: Additional competency metadata
        embedding: Vector embedding for semantic search
    """

    id: str
    label: str
    name: str
    description: str
    anchors: List[str] = field(default_factory=list)
    states: List[Dict[str, Any]] = field(default_factory=list)
    actions: List[str] = field(default_factory=list)
    workflows: List[str] = field(default_factory=list)
    enabled: bool = True
    execution_requirement: ExecutionRequirement = ExecutionRequirement.CONDITIONAL
    metadata: Dict[str, Any] = field(default_factory=dict)
    embedding: Optional[List[float]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "id": self.id,
            "label": self.label,
            "name": self.name,
            "description": self.description,
            "anchors": self.anchors,
            "states": self.states,
            "actions": self.actions,
            "workflows": self.workflows,
            "enabled": self.enabled,
            "execution_requirement": self.execution_requirement.value,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Competency":
        """Create Competency from dictionary."""
        exec_req = data.get("execution_requirement", "conditional")
        if isinstance(exec_req, str):
            exec_req = ExecutionRequirement(exec_req)

        return cls(
            id=data.get("id", ""),
            label=data.get("label", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            anchors=data.get("anchors", []),
            states=data.get("states", []),
            actions=data.get("actions", []),
            workflows=data.get("workflows", []),
            enabled=data.get("enabled", True),
            execution_requirement=exec_req,
            metadata=data.get("metadata", {}),
        )


@dataclass
class Workflow:
    """Workflow definition for orchestrating actions.

    Workflows define sequences of actions or processes that
    should be executed together.

    Attributes:
        id: Unique workflow identifier
        name: Human-readable workflow name
        description: Detailed workflow description
        steps: Ordered list of workflow steps
        enabled: Whether workflow is active
        metadata: Additional workflow metadata
    """

    id: str
    name: str
    description: str
    steps: List[Dict[str, Any]] = field(default_factory=list)
    enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "steps": self.steps,
            "enabled": self.enabled,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Workflow":
        """Create Workflow from dictionary."""
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            steps=data.get("steps", []),
            enabled=data.get("enabled", True),
            metadata=data.get("metadata", {}),
        )


@dataclass
class InitialPhaseInstructions:
    """Output instructions from Initial Phase processing.

    This is the structured JSON output that contains all the
    information needed for downstream processing.

    Attributes:
        simplified_intent: Simplified/categorized user request
        applicable_parameters: Parameters that apply to this interaction
        required_workflows: Workflows that should be executed
        required_actions: Actions that should be executed
        context: Additional context information
        metadata: Additional metadata
    """

    simplified_intent: str
    applicable_parameters: List[Dict[str, Any]] = field(default_factory=list)
    required_workflows: List[str] = field(default_factory=list)
    required_actions: List[str] = field(default_factory=list)
    directive: str = field(default_factory=str)
    context: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    example_message: str = field(default_factory=str)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "simplified_intent": self.simplified_intent,
            "applicable_parameters": self.applicable_parameters,
            "required_workflows": self.required_workflows,
            "required_actions": self.required_actions,
            "directive": self.directive,
            "context": self.context,
            "metadata": self.metadata,
            "example_message": self.example_message,
        }

    def to_json(self) -> str:
        """Convert to JSON string."""
        import json
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "InitialPhaseInstructions":
        """Create InitialPhaseInstructions from dictionary."""
        return cls(
            simplified_intent=data.get("simplified_intent", ""),
            applicable_parameters=data.get("applicable_parameters", []),
            required_workflows=data.get("required_workflows", []),
            required_actions=data.get("required_actions", []),
            directive=data.get("directive", ""),
            context=data.get("context", {}),
            metadata=data.get("metadata", {}),
            example_message=data.get("example_message", ""),
        )
