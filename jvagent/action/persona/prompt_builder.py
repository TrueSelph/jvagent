"""Structured prompt builder for PersonaAction.

This module provides PersonaPromptBuilder, a structured approach to building
prompts with named sections, priorities, and clear hierarchy. 
"""

from typing import List, Optional


class PromptSection:
    """Represents a named section in the prompt with priority and content."""

    def __init__(self, name: str, content: str, priority: int = 0):
        """Initialize a prompt section.

        Args:
            name: Section name/identifier
            content: Section content
            priority: Priority for ordering (lower = earlier in prompt)
        """
        self.name = name
        self.content = content
        self.priority = priority

    def __lt__(self, other: "PromptSection") -> bool:
        """Compare sections by priority for sorting."""
        return self.priority < other.priority

    def __repr__(self) -> str:
        return f"PromptSection(name={self.name!r}, priority={self.priority})"


class PersonaPromptBuilder:
    """Structured prompt builder for PersonaAction.

    Provides a clean API for building prompts with named sections, priorities,
    and automatic assembly. Sections are assembled in priority order (lower
    priority = earlier in prompt).

    Priority constants (for reference):
        PRIORITY_IDENTITY = 10      # Agent identity (name, role, description)
        PRIORITY_CONTEXT = 20       # Context awareness (date, time, user)
        PRIORITY_TASK = 30          # Task definition
        PRIORITY_DIRECTIVES = 40    # Directives (MANDATORY)
        PRIORITY_PARAMETERS = 50    # Behavioral parameters
        PRIORITY_HISTORY = 60       # Conversation history
        PRIORITY_PREVIOUS = 70     # Previous responses in same interaction
        PRIORITY_PRINCIPLES = 80    # General principles
        PRIORITY_QUALITY = 90       # Response quality requirements
    """

    # Priority constants for common sections
    PRIORITY_IDENTITY = 10
    PRIORITY_CONTEXT = 20
    PRIORITY_TASK = 30
    PRIORITY_DIRECTIVES = 40
    PRIORITY_PARAMETERS = 50
    PRIORITY_HISTORY = 60
    PRIORITY_PREVIOUS = 70
    PRIORITY_PRINCIPLES = 80
    PRIORITY_QUALITY = 90

    def __init__(self):
        """Initialize an empty prompt builder."""
        self._sections: List[PromptSection] = []

    def add_section(
        self,
        name: str,
        content: str,
        priority: Optional[int] = None,
        condition: bool = True,
    ) -> "PersonaPromptBuilder":
        """Add a named section to the prompt.

        Args:
            name: Section name/identifier (for debugging and organization)
            content: Section content (will be trimmed)
            priority: Priority for ordering (lower = earlier). If None, uses
                default priority based on section name or appends to end.
            condition: Only add section if condition is True (default: True)

        Returns:
            Self for method chaining
        """
        if not condition or not content:
            return self

        # Trim content
        content = content.strip()
        if not content:
            return self

        # Use provided priority or default based on name
        if priority is None:
            priority = self._get_default_priority(name)

        section = PromptSection(name=name, content=content, priority=priority)
        self._sections.append(section)
        return self

    def add_section_if(
        self,
        name: str,
        content: str,
        condition: bool,
        priority: Optional[int] = None,
    ) -> "PersonaPromptBuilder":
        """Add a section only if condition is True.

        Convenience method for conditional sections.

        Args:
            name: Section name
            content: Section content
            condition: Condition to check
            priority: Optional priority override

        Returns:
            Self for method chaining
        """
        return self.add_section(name, content, priority=priority, condition=condition)

    def _get_default_priority(self, name: str) -> int:
        """Get default priority based on section name.

        Args:
            name: Section name

        Returns:
            Default priority value
        """
        name_lower = name.lower()
        if "identity" in name_lower or "persona" in name_lower:
            return self.PRIORITY_IDENTITY
        elif "context" in name_lower:
            return self.PRIORITY_CONTEXT
        elif "task" in name_lower:
            return self.PRIORITY_TASK
        elif "directive" in name_lower:
            return self.PRIORITY_DIRECTIVES
        elif "parameter" in name_lower:
            return self.PRIORITY_PARAMETERS
        elif "history" in name_lower or "conversation" in name_lower:
            return self.PRIORITY_HISTORY
        elif "previous" in name_lower or "prior" in name_lower:
            return self.PRIORITY_PREVIOUS
        elif "principle" in name_lower or "general" in name_lower:
            return self.PRIORITY_PRINCIPLES
        elif "quality" in name_lower or "verification" in name_lower:
            return self.PRIORITY_QUALITY
        else:
            # Default: append to end
            return 100

    def build(self, separator: str = "\n\n") -> str:
        """Build the final prompt by assembling sections in priority order.

        Args:
            separator: Separator between sections (default: "\n\n")

        Returns:
            Assembled prompt string
        """
        if not self._sections:
            return ""

        # Sort sections by priority
        sorted_sections = sorted(self._sections)

        # Join sections with separator
        parts = [section.content for section in sorted_sections]
        return separator.join(parts)

    def clear(self) -> "PersonaPromptBuilder":
        """Clear all sections.

        Returns:
            Self for method chaining
        """
        self._sections.clear()
        return self

    def get_sections(self) -> List[PromptSection]:
        """Get all sections (for debugging/inspection).

        Returns:
            List of sections (unsorted)
        """
        return list(self._sections)

    def has_section(self, name: str) -> bool:
        """Check if a section with the given name exists.

        Args:
            name: Section name to check

        Returns:
            True if section exists, False otherwise
        """
        return any(section.name == name for section in self._sections)

    def get_section(self, name: str) -> Optional[PromptSection]:
        """Get a section by name.

        Args:
            name: Section name

        Returns:
            PromptSection if found, None otherwise
        """
        for section in self._sections:
            if section.name == name:
                return section
        return None

    def remove_section(self, name: str) -> bool:
        """Remove a section by name.

        Args:
            name: Section name to remove

        Returns:
            True if section was removed, False if not found
        """
        initial_count = len(self._sections)
        self._sections = [s for s in self._sections if s.name != name]
        return len(self._sections) < initial_count

    def __len__(self) -> int:
        """Get number of sections."""
        return len(self._sections)

    def __repr__(self) -> str:
        return f"PersonaPromptBuilder(sections={len(self._sections)})"
