"""Parameter definition and management for PersonaAction.

This module provides:
- PersonaParameter: Behavioral parameter with optional action delegation
- ParameterManager: Storage and retrieval via Collection
"""

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from jvagent.memory import Memory

logger = logging.getLogger(__name__)


@dataclass
class PersonaParameter:
    """Behavioral parameter with optional action delegation.

    Parameters define conditional behaviors for the PersonaAction. When a parameter's
    condition is matched (determined by LLM filtering), its response instruction is
    included in the prompt, and optionally its associated action is triggered.

    Attributes:
        id: Unique parameter identifier
        condition: When this parameter applies (evaluated by LLM)
        response: Behavioral instruction for the LLM
        action: Optional action label to trigger via execute()
        enabled: Whether this parameter is active
        metadata: Additional metadata dictionary
    """

    id: str
    condition: str
    response: str
    action: Optional[str] = None
    enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PersonaParameter":
        """Create a PersonaParameter from a dictionary.

        Args:
            data: Dictionary with parameter data

        Returns:
            PersonaParameter instance
        """
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            condition=data.get("condition", ""),
            response=data.get("response", ""),
            action=data.get("action"),
            enabled=data.get("enabled", True),
            metadata=data.get("metadata", {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert parameter to dictionary.

        Returns:
            Dictionary representation
        """
        return {
            "id": self.id,
            "condition": self.condition,
            "response": self.response,
            "action": self.action,
            "enabled": self.enabled,
            "metadata": self.metadata,
        }

    def to_prompt_format(self) -> str:
        """Format parameter for inclusion in prompt.

        Returns:
            Formatted string for prompt inclusion
        """
        return f"When {self.condition}, then {self.response}"


class ParameterManager:
    """Manages parameter storage and retrieval.

    Parameters are stored as documents in a collection associated with the
    PersonaAction. This manager provides CRUD operations for parameters.
    """

    def __init__(self, action_id: str, memory: Optional["Memory"] = None):
        """Initialize the parameter manager.

        Args:
            action_id: ID of the PersonaAction this manager belongs to
            memory: Optional Memory node for collection access
        """
        self.action_id = action_id
        self._memory = memory
        self._collection_name = f"persona_parameters_{action_id}"
        self._parameters: Dict[str, PersonaParameter] = {}
        self._loaded = False

    async def _ensure_loaded(self) -> None:
        """Ensure parameters are loaded from storage."""
        if self._loaded:
            return
        await self.load_parameters()

    async def load_parameters(self) -> None:
        """Load parameters from collection storage."""
        # For now, parameters are stored in memory
        # In a full implementation, this would load from a Collection
        self._loaded = True
        logger.debug(f"Loaded {len(self._parameters)} parameters")

    async def add_parameter(self, param: PersonaParameter) -> str:
        """Add a new parameter.

        Args:
            param: Parameter to add

        Returns:
            ID of the added parameter
        """
        self._parameters[param.id] = param
        logger.debug(f"Added parameter {param.id}")
        return param.id

    async def get_parameter(self, param_id: str) -> Optional[PersonaParameter]:
        """Get a parameter by ID.

        Args:
            param_id: Parameter ID to retrieve

        Returns:
            PersonaParameter if found, None otherwise
        """
        await self._ensure_loaded()
        return self._parameters.get(param_id)

    async def list_parameters(
        self, enabled_only: bool = True
    ) -> List[PersonaParameter]:
        """List all parameters.

        Args:
            enabled_only: If True, only return enabled parameters

        Returns:
            List of parameters
        """
        await self._ensure_loaded()
        params = list(self._parameters.values())
        if enabled_only:
            params = [p for p in params if p.enabled]
        return params

    async def update_parameter(
        self, param_id: str, updates: Dict[str, Any]
    ) -> Optional[PersonaParameter]:
        """Update a parameter.

        Args:
            param_id: ID of parameter to update
            updates: Dictionary of updates to apply

        Returns:
            Updated PersonaParameter if found, None otherwise
        """
        await self._ensure_loaded()
        param = self._parameters.get(param_id)
        if not param:
            return None

        # Apply updates
        if "condition" in updates:
            param.condition = updates["condition"]
        if "response" in updates:
            param.response = updates["response"]
        if "action" in updates:
            param.action = updates["action"]
        if "enabled" in updates:
            param.enabled = updates["enabled"]
        if "metadata" in updates:
            param.metadata.update(updates["metadata"])

        logger.debug(f"Updated parameter {param_id}")
        return param

    async def delete_parameter(self, param_id: str) -> bool:
        """Delete a parameter.

        Args:
            param_id: ID of parameter to delete

        Returns:
            True if deleted, False if not found
        """
        await self._ensure_loaded()
        if param_id in self._parameters:
            del self._parameters[param_id]
            logger.debug(f"Deleted parameter {param_id}")
            return True
        return False

    async def import_parameters(self, params: List[Dict[str, Any]]) -> int:
        """Import multiple parameters from dictionaries.

        Args:
            params: List of parameter dictionaries

        Returns:
            Number of parameters imported
        """
        count = 0
        for param_data in params:
            param = PersonaParameter.from_dict(param_data)
            # Don't overwrite if exists with same condition
            existing = await self._find_by_condition(param.condition)
            if not existing:
                await self.add_parameter(param)
                count += 1
        logger.info(f"Imported {count} parameters")
        return count

    async def _find_by_condition(
        self, condition: str
    ) -> Optional[PersonaParameter]:
        """Find a parameter by condition.

        Args:
            condition: Condition to search for

        Returns:
            PersonaParameter if found, None otherwise
        """
        await self._ensure_loaded()
        for param in self._parameters.values():
            if param.condition == condition:
                return param
        return None

    async def clear_parameters(self) -> int:
        """Clear all parameters.

        Returns:
            Number of parameters cleared
        """
        count = len(self._parameters)
        self._parameters.clear()
        logger.info(f"Cleared {count} parameters")
        return count


# Default base parameters that apply to all PersonaActions
DEFAULT_BASE_PARAMETERS: List[Dict[str, Any]] = [
    {
        "condition": "User asks about OpenAI or your origin.",
        "response": "Deny any association with OpenAI even when asked directly.",
    },
    {
        "condition": "User asks for your identity.",
        "response": "Refer to yourself only by your name. Never say you are an AI language model.",
    },
    {
        "condition": "User request is outside your role or ability.",
        "response": "Admit that the request is outside your role or ability; Do not give inaccurate answers.",
    },
    {
        "condition": "You are likely to resend a message for a second time.",
        "response": "State that you didn't quite catch that and ask the user to rephrase.",
    },
    {
        "condition": "You are likely to send a message that you have already repeated several times.",
        "response": "Apologize and tell the user you are experiencing technical difficulties.",
    },
    {
        "condition": "You are likely to claim you have completed a backend action.",
        "response": "Never claim you have completed backend actions unless instructed by a directive.",
    },
]
