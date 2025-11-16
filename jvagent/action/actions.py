"""Actions manager node for agent action registration and discovery."""

from typing import Any, Dict, Set, List, Callable
import asyncio

from jvspatial.core import Node
from jvspatial.core.annotations import attribute
from pydantic import Field


class Actions(Node):
    """Central node for managing agent actions.
    
    The Actions node manages the registration, discovery, and lifecycle of all
    actions for an agent. It provides action discovery, registration, dependency
    management, and lifecycle hooks.
    
    Attributes:
        _action_classes: Registry of action classes (private, not persisted)
        _action_modules: Registry of action modules (private, not persisted)
        _action_dependencies: Dependency mapping (private, not persisted)
        _action_hooks: Lifecycle hooks (private, not persisted)
        _lock: Async lock for thread-safe operations (private, not persisted)
    """
    
    # Internal registries (private - not persisted)
    # Use private=True for underscore-prefixed fields (Pydantic requirement)
    _action_classes: Dict[str, type] = attribute(private=True, default_factory=dict)
    _action_modules: Dict[str, Any] = attribute(private=True, default_factory=dict)
    _action_dependencies: Dict[str, Set[str]] = attribute(private=True, default_factory=dict)
    _action_hooks: Dict[str, List[Callable]] = attribute(private=True, default_factory=dict)
    _lock: asyncio.Lock = attribute(private=True, default_factory=asyncio.Lock)

