"""Configuration objects for interview action.

Type-safe configuration classes to replace the many individual attributes
on InterviewInteractAction.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .prompts import (
    CANCELLATION_MESSAGE,
    COMPLETION_MESSAGE,
    INTERVIEW_PROMPT,
    QUESTION_DIRECTIVE,
    REQUIRED_FIELD_DECLINE,
    REVIEW_CONFIRMATION_DEFAULT_INSTRUCTIONS,
    REVIEW_CONFIRMATION_DEFAULT_PROMPT,
    REVIEW_CONFIRMATION_DIRECTIVE,
    REVIEW_SUMMARY_HEADER,
    REVIEW_SUMMARY_ITEM,
    REVIEW_UNCLEAR_EDIT_DIRECTIVE,
    REVIEW_UNCLEAR_GENERAL_DIRECTIVE,
    UPDATE_PROMPT_FOR_VALUE,
    get_state_event_message,
)


@dataclass
class ClassificationConfig:
    """Configuration for classification and context data formatting."""

    # Context data formatting thresholds
    context_list_compact_threshold: int = 5  # Max list length to display items inline
    context_options_text: str = "options available"  # Text to show for long lists

    # Decline value for required fields
    decline_value: str = "n/a"

    # Structured reasoning controls (new prompt architecture)
    require_structured_reasoning: bool = (
        True  # Require structured reasoning object in LLM response
    )
    include_few_shot_examples: bool = (
        True  # Include few-shot examples in classification prompt
    )
    max_examples: int = (
        5  # Maximum number of examples to include (when include_few_shot_examples=True)
    )
    enable_reference_resolution: bool = (
        True  # Enable reference resolution section in prompt
    )
    enable_composition: bool = (
        True  # Enable multi-turn value composition section in prompt
    )

    def __post_init__(self):
        """Validate classification configuration."""
        if self.context_list_compact_threshold < 1:
            raise ValueError(
                f"context_list_compact_threshold must be positive, got {self.context_list_compact_threshold}"
            )
        if self.max_examples < 0:
            raise ValueError(
                f"max_examples must be non-negative, got {self.max_examples}"
            )


@dataclass
class ModelConfig:
    """Configuration for language model settings."""

    model_action_type: str = "OpenAILanguageModelAction"
    model: str = "gpt-4o"
    model_temperature: float = 0.1
    model_max_tokens: int = 8192
    use_history: bool = True
    max_statement_length: int = 500
    history_limit: int = 3

    def __post_init__(self) -> None:
        """Validate model configuration."""
        if self.model_temperature < 0 or self.model_temperature > 2:
            raise ValueError(
                f"model_temperature must be between 0 and 2, got {self.model_temperature}"
            )
        if self.model_max_tokens < 1:
            raise ValueError(
                f"model_max_tokens must be positive, got {self.model_max_tokens}"
            )
        if self.history_limit < 0:
            raise ValueError(
                f"history_limit must be non-negative, got {self.history_limit}"
            )


@dataclass
class TemplateConfig:
    """Configuration for prompt templates."""

    # Summary formatting
    summary_header: str = REVIEW_SUMMARY_HEADER
    summary_item: str = REVIEW_SUMMARY_ITEM

    # Review directives
    review_confirmation: str = REVIEW_CONFIRMATION_DIRECTIVE
    confirmation_instructions: str = REVIEW_CONFIRMATION_DEFAULT_INSTRUCTIONS
    confirmation_prompt: str = REVIEW_CONFIRMATION_DEFAULT_PROMPT
    review_unclear_edit: str = REVIEW_UNCLEAR_EDIT_DIRECTIVE
    review_unclear_general: str = REVIEW_UNCLEAR_GENERAL_DIRECTIVE

    # Update prompt
    update_prompt_for_value: str = UPDATE_PROMPT_FOR_VALUE

    # Completion and cancellation
    completion_message: str = COMPLETION_MESSAGE
    cancellation_message: str = CANCELLATION_MESSAGE

    # Question directive
    question_directive: str = QUESTION_DIRECTIVE

    # Required field decline
    required_field_decline: str = REQUIRED_FIELD_DECLINE

    # Interview prompt
    interview_prompt: str = INTERVIEW_PROMPT

    def __post_init__(self):
        """Validate template placeholders."""
        self._validate_placeholders()

    def _validate_placeholders(self) -> None:
        """Validate that required placeholders are present in templates.

        This is a basic check - full validation would require knowing
        which placeholders are actually used at runtime.
        """
        # Check key templates for common placeholders
        templates_to_check = {
            "summary_item": ["display_name", "value"],
            "review_confirmation": ["summary", "instructions", "prompt"],
            "review_unclear_edit": ["summary", "field_list"],
            "update_prompt_for_value": ["field_display", "current_value"],
            "required_field_decline": ["field_display", "question"],
        }

        for template_name, expected_placeholders in templates_to_check.items():
            template_value = getattr(self, template_name, "")
            if template_value:
                for placeholder in expected_placeholders:
                    if f"{{{placeholder}}}" not in template_value:
                        # Warning only - some templates might not use all placeholders
                        import logging

                        logger = logging.getLogger(__name__)
                        logger.debug(
                            f"Template '{template_name}' may be missing placeholder '{{{placeholder}}}'"
                        )

    def get_state_event_message(self, state: str, class_name: str) -> str:
        """Get formatted state event message for terminal states.

        Args:
            state: Interview state (COMPLETED, CANCELLED; ACTIVE/REVIEW use active tasks)
            class_name: Interview action class name

        Returns:
            Formatted event message string, or empty string if state has no message
        """
        return get_state_event_message(state, class_name)


@dataclass
class InterviewConfig:
    """Complete configuration for interview actions.

    Consolidates all configuration into type-safe objects.
    """

    model: ModelConfig = field(default_factory=ModelConfig)
    templates: TemplateConfig = field(default_factory=TemplateConfig)
    classification: ClassificationConfig = field(default_factory=ClassificationConfig)
    auto_confirm: bool = False  # Skip confirmation prompt in REVIEW state

    @classmethod
    def from_dict(cls, config_dict: Dict[str, Any]) -> "InterviewConfig":
        """Create config from dictionary (e.g., from agent.yaml).

        Args:
            config_dict: Dictionary with configuration values

        Returns:
            InterviewConfig instance
        """
        # Model config - YAML keys match ModelConfig attribute names
        model_config = ModelConfig()
        for key in model_config.__dataclass_fields__.keys():
            if key in config_dict:
                setattr(model_config, key, config_dict[key])

        # Template config - YAML keys match TemplateConfig attribute names
        template_config = TemplateConfig()
        for key in template_config.__dataclass_fields__.keys():
            if key in config_dict:
                setattr(template_config, key, config_dict[key])

        # Classification config - support both nested and top-level keys
        classification_config = ClassificationConfig()
        classification_dict = config_dict.get("classification", {})
        if isinstance(classification_dict, dict):
            for key in classification_config.__dataclass_fields__.keys():
                if key in classification_dict:
                    setattr(classification_config, key, classification_dict[key])
        # Top-level keys override nested ones
        for key in classification_config.__dataclass_fields__.keys():
            if key in config_dict:
                setattr(classification_config, key, config_dict[key])

        # Auto-confirm config - top-level key
        auto_confirm = config_dict.get("auto_confirm", False)

        return cls(
            model=model_config,
            templates=template_config,
            classification=classification_config,
            auto_confirm=auto_confirm,
        )
