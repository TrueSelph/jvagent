"""Prompt templating system for model actions.

Provides Jinja2-based template rendering for prompts with support for
loading templates from action package directories.
"""

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

from jinja2 import Environment, FileSystemLoader, Template, TemplateNotFound

if TYPE_CHECKING:
    from jvagent.action.model.language.base import LanguageModelAction

logger = logging.getLogger(__name__)


class TemplateManager:
    """Manages prompt templates for model actions.

    Loads and renders Jinja2 templates from action package directories,
    providing a simple interface for template-based prompt generation.

    Templates are loaded from:
    1. Action package directory: {action_package_dir}/templates/
    2. Built-in templates (if package templates not found)

    Examples:
        >>> manager = TemplateManager(model_action)
        >>> prompt = await manager.render("user_query", query="Hello", context="...")
        >>> result = await model_action.query_sync(prompt)
    """

    def __init__(self, action: "LanguageModelAction"):
        """Initialize template manager for an action.

        Args:
            action: LanguageModelAction instance
        """
        self.action = action
        self._env: Optional[Environment] = None
        self._template_dir: Optional[Path] = None

    async def _get_template_directory(self) -> Optional[Path]:
        """Get the template directory for this action.

        Returns:
            Path to templates directory, or None if not found
        """
        if self._template_dir:
            return self._template_dir

        # Get action package path
        package_path = await self.action.get_package_path()
        if not package_path:
            logger.warning(f"Could not determine package path for action {self.action.label}")
            return None

        # Check for templates directory
        templates_dir = Path(package_path) / "templates"
        if templates_dir.exists() and templates_dir.is_dir():
            self._template_dir = templates_dir
            return templates_dir

        return None

    async def _get_environment(self) -> Environment:
        """Get or create the Jinja2 environment.

        Returns:
            Jinja2 Environment instance
        """
        if self._env:
            return self._env

        # Get template directory
        template_dir = await self._get_template_directory()

        if template_dir:
            # Use FileSystemLoader for templates directory
            loader = FileSystemLoader(str(template_dir))
            self._env = Environment(
                loader=loader,
                autoescape=False,  # Don't escape for text prompts
                trim_blocks=True,
                lstrip_blocks=True,
            )
            logger.debug(f"Template environment created with directory: {template_dir}")
        else:
            # No template directory, create environment without loader
            self._env = Environment(autoescape=False)
            logger.debug("Template environment created without loader (inline only)")

        return self._env

    async def render(self, template_name: str, **variables: Any) -> str:
        """Render a template with variables.

        Args:
            template_name: Name of the template (without .j2 extension)
            **variables: Template variables

        Returns:
            Rendered template string

        Raises:
            TemplateNotFound: If template not found
            Exception: If template rendering fails

        Examples:
            >>> prompt = await manager.render("user_query", query="Hello", context="AI")
        """
        env = await self._get_environment()

        # Add .j2 extension if not present
        if not template_name.endswith(".j2"):
            template_name = f"{template_name}.j2"

        try:
            # Load and render template
            template = env.get_template(template_name)
            rendered = template.render(**variables)

            logger.debug(f"Rendered template: {template_name}")
            return rendered

        except TemplateNotFound:
            logger.error(f"Template not found: {template_name}")
            raise
        except Exception as e:
            logger.error(f"Template rendering failed: {e}")
            raise

    async def render_string(self, template_string: str, **variables: Any) -> str:
        """Render a template from a string.

        Useful for inline templates that aren't stored in files.

        Args:
            template_string: Template string with Jinja2 syntax
            **variables: Template variables

        Returns:
            Rendered string

        Examples:
            >>> prompt = await manager.render_string(
            ...     "Hello {{ name }}! You are {{ role }}.",
            ...     name="User", role="admin"
            ... )
        """
        env = await self._get_environment()

        try:
            template = env.from_string(template_string)
            rendered = template.render(**variables)
            return rendered
        except Exception as e:
            logger.error(f"String template rendering failed: {e}")
            raise

    async def has_template(self, template_name: str) -> bool:
        """Check if a template exists.

        Args:
            template_name: Name of the template (with or without .j2)

        Returns:
            True if template exists, False otherwise
        """
        env = await self._get_environment()

        # Add .j2 extension if not present
        if not template_name.endswith(".j2"):
            template_name = f"{template_name}.j2"

        try:
            env.get_template(template_name)
            return True
        except TemplateNotFound:
            return False

    async def list_templates(self) -> list[str]:
        """List available templates.

        Returns:
            List of template names (without .j2 extension)
        """
        template_dir = await self._get_template_directory()
        if not template_dir or not template_dir.exists():
            return []

        templates = []
        for file in template_dir.glob("*.j2"):
            # Remove .j2 extension
            templates.append(file.stem)

        return sorted(templates)


# ============================================================================
# Built-in Template Examples
# ============================================================================

# Default system prompt template
DEFAULT_SYSTEM_PROMPT = """You are a helpful AI assistant.
You provide clear, accurate, and concise responses.
You are honest about the limits of your knowledge."""

# Contextual query template
CONTEXTUAL_QUERY_TEMPLATE = """{% if context %}
Context:
{{ context }}

{% endif %}Query: {{ query }}"""

# Function call template
FUNCTION_CALL_TEMPLATE = """You have access to the following functions:

{% for func in functions %}
- {{ func.name }}: {{ func.description }}
  Parameters: {{ func.parameters }}
{% endfor %}

When you need to call a function, use the following format:
Function: function_name
Arguments: {"arg1": "value1", "arg2": "value2"}

User query: {{ query }}"""


def create_default_templates(directory: Path) -> None:
    """Create default template files in a directory.

    Args:
        directory: Directory to create templates in
    """
    directory.mkdir(parents=True, exist_ok=True)

    # System prompt template
    (directory / "system_prompt.j2").write_text(DEFAULT_SYSTEM_PROMPT.strip())

    # User query template
    (directory / "user_query.j2").write_text("{{ query }}")

    # Contextual query template
    (directory / "contextual_query.j2").write_text(CONTEXTUAL_QUERY_TEMPLATE.strip())

    # Function call template
    (directory / "function_call.j2").write_text(FUNCTION_CALL_TEMPLATE.strip())

    logger.info(f"Created default templates in {directory}")
