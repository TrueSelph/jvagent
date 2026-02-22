"""Tests for PersonaPromptBuilder and prompt composition."""

import pytest

from jvagent.action.persona.prompt_builder import PersonaPromptBuilder


class TestPersonaPromptBuilder:
    """Test PersonaPromptBuilder functionality."""

    def test_builder_initialization(self):
        """Test that builder initializes correctly."""
        builder = PersonaPromptBuilder()
        assert len(builder) == 0
        assert builder.get_sections() == []

    def test_add_section(self):
        """Test adding sections to the builder."""
        builder = PersonaPromptBuilder()
        builder.add_section("test_section", "Test content", priority=10)

        assert len(builder) == 1
        assert builder.has_section("test_section")
        assert not builder.has_section("nonexistent")

    def test_section_priority_ordering(self):
        """Test that sections are ordered by priority."""
        builder = PersonaPromptBuilder()
        builder.add_section("high_priority", "High", priority=10)
        builder.add_section("low_priority", "Low", priority=50)
        builder.add_section("medium_priority", "Medium", priority=30)

        result = builder.build()

        # Check that high priority comes before medium, which comes before low
        assert result.index("High") < result.index("Medium")
        assert result.index("Medium") < result.index("Low")

    def test_conditional_section(self):
        """Test conditional section addition."""
        builder = PersonaPromptBuilder()
        builder.add_section("conditional", "Content", condition=False)
        assert len(builder) == 0

        builder.add_section("conditional", "Content", condition=True)
        assert len(builder) == 1

    def test_add_section_if(self):
        """Test add_section_if convenience method."""
        builder = PersonaPromptBuilder()
        builder.add_section_if("test", "Content", condition=False)
        assert len(builder) == 0

        builder.add_section_if("test", "Content", condition=True)
        assert len(builder) == 1

    def test_default_priority_detection(self):
        """Test that default priorities are assigned based on section names."""
        builder = PersonaPromptBuilder()
        builder.add_section("agent_identity", "Identity content")
        builder.add_section("directives_section", "Directives content")
        builder.add_section("parameters_section", "Parameters content")

        sections = builder.get_sections()
        priorities = [s.priority for s in sections]

        # Identity should have lower priority (earlier) than directives
        identity_priority = next(
            s.priority for s in sections if s.name == "agent_identity"
        )
        directives_priority = next(
            s.priority for s in sections if s.name == "directives_section"
        )
        assert identity_priority < directives_priority

    def test_build_empty(self):
        """Test building an empty prompt."""
        builder = PersonaPromptBuilder()
        result = builder.build()
        assert result == ""

    def test_build_with_sections(self):
        """Test building a prompt with multiple sections."""
        builder = PersonaPromptBuilder()
        builder.add_section("section1", "Content 1", priority=10)
        builder.add_section("section2", "Content 2", priority=20)

        result = builder.build()
        assert "Content 1" in result
        assert "Content 2" in result
        assert result.index("Content 1") < result.index("Content 2")

    def test_clear(self):
        """Test clearing all sections."""
        builder = PersonaPromptBuilder()
        builder.add_section("test", "Content")
        assert len(builder) == 1

        builder.clear()
        assert len(builder) == 0

    def test_get_section(self):
        """Test retrieving a section by name."""
        builder = PersonaPromptBuilder()
        builder.add_section("test_section", "Test content", priority=10)

        section = builder.get_section("test_section")
        assert section is not None
        assert section.name == "test_section"
        assert section.content == "Test content"
        assert section.priority == 10

        assert builder.get_section("nonexistent") is None

    def test_remove_section(self):
        """Test removing a section by name."""
        builder = PersonaPromptBuilder()
        builder.add_section("test", "Content")
        assert builder.has_section("test")

        removed = builder.remove_section("test")
        assert removed is True
        assert not builder.has_section("test")

        # Removing non-existent section returns False
        assert builder.remove_section("nonexistent") is False

    def test_priority_constants(self):
        """Test that priority constants are accessible."""
        assert PersonaPromptBuilder.PRIORITY_IDENTITY == 10
        assert PersonaPromptBuilder.PRIORITY_DIRECTIVES == 40
        assert PersonaPromptBuilder.PRIORITY_PARAMETERS == 50
