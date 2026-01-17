"""Configuration objects for interview action.

Type-safe configuration classes to replace the many individual attributes
on InterviewInteractAction.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .prompts import (
    UPDATE_PROMPT_FOR_VALUE_TEMPLATE,
    REVIEW_SUMMARY_HEADER_TEMPLATE,
    REVIEW_SUMMARY_ITEM_TEMPLATE,
    REVIEW_DIRECTIVE_TEMPLATE,
    REVIEW_CONFIRMATION_CONTENT,
    REVIEW_CONFIRMATION_DEFAULT_INSTRUCTIONS,
    REVIEW_CONFIRMATION_DEFAULT_PROMPT,
    REVIEW_UNCLEAR_EDIT_CONTENT,
    REVIEW_UNCLEAR_GENERAL_CONTENT,
    COMPLETION_MESSAGE_TEMPLATE,
    CANCELLATION_MESSAGE_TEMPLATE,
    ACTIVE_EVENT_MESSAGE_TEMPLATE,
    REVIEW_EVENT_MESSAGE_TEMPLATE,
    COMPLETION_EVENT_MESSAGE_TEMPLATE,
    CANCELLATION_EVENT_MESSAGE_TEMPLATE,
    QUESTION_DIRECTIVE_TEMPLATE,
    INTERVIEW_PROMPT_TEMPLATE,
    INTERVIEW_CLASSIFICATION_SIGNATURE,
    REQUIRED_FIELD_DECLINE_TEMPLATE,
)


@dataclass
class ModelConfig:
    """Configuration for language model settings."""
    
    action_type: str = "OpenAILanguageModelAction"
    model: str = "gpt-4o"
    temperature: float = 0.1
    max_tokens: int = 4096
    use_history: bool = True
    max_statement_length: int = 400
    history_limit: int = 5
    
    def __post_init__(self):
        """Validate model configuration."""
        if self.temperature < 0 or self.temperature > 2:
            raise ValueError(f"Temperature must be between 0 and 2, got {self.temperature}")
        if self.max_tokens < 1:
            raise ValueError(f"max_tokens must be positive, got {self.max_tokens}")
        if self.history_limit < 0:
            raise ValueError(f"history_limit must be non-negative, got {self.history_limit}")


@dataclass
class TemplateConfig:
    """Configuration for prompt templates."""
    
    # Summary formatting
    summary_header: str = REVIEW_SUMMARY_HEADER_TEMPLATE
    summary_item: str = REVIEW_SUMMARY_ITEM_TEMPLATE
    
    # Review directive
    review_directive: str = REVIEW_DIRECTIVE_TEMPLATE
    confirmation_content: str = REVIEW_CONFIRMATION_CONTENT
    confirmation_instructions: str = REVIEW_CONFIRMATION_DEFAULT_INSTRUCTIONS
    confirmation_prompt: str = REVIEW_CONFIRMATION_DEFAULT_PROMPT
    unclear_edit_content: str = REVIEW_UNCLEAR_EDIT_CONTENT
    unclear_general_content: str = REVIEW_UNCLEAR_GENERAL_CONTENT
    
    # Update prompt
    update_prompt_for_value: str = UPDATE_PROMPT_FOR_VALUE_TEMPLATE
    
    # Completion and cancellation
    completion_message: str = COMPLETION_MESSAGE_TEMPLATE
    cancellation_message: str = CANCELLATION_MESSAGE_TEMPLATE
    
    # Event messages
    active_event_message: str = ACTIVE_EVENT_MESSAGE_TEMPLATE
    review_event_message: str = REVIEW_EVENT_MESSAGE_TEMPLATE
    completion_event_message: str = COMPLETION_EVENT_MESSAGE_TEMPLATE
    cancellation_event_message: str = CANCELLATION_EVENT_MESSAGE_TEMPLATE
    
    # Question directive
    question_directive: str = QUESTION_DIRECTIVE_TEMPLATE
    
    # Required field decline
    required_field_decline: str = REQUIRED_FIELD_DECLINE_TEMPLATE
    
    # Interview prompt
    interview_prompt: str = INTERVIEW_PROMPT_TEMPLATE
    interview_classification_signature: str = INTERVIEW_CLASSIFICATION_SIGNATURE
    
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
            "confirmation_content": ["summary", "instructions", "prompt"],
            "unclear_edit_content": ["summary", "field_list"],
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


@dataclass
class InterviewConfig:
    """Complete configuration for interview actions.
    
    Consolidates all configuration into type-safe objects.
    """
    
    model: ModelConfig = field(default_factory=ModelConfig)
    templates: TemplateConfig = field(default_factory=TemplateConfig)
    use_dspy: bool = False
    
    @classmethod
    def from_dict(cls, config_dict: Dict[str, Any]) -> "InterviewConfig":
        """Create config from dictionary (e.g., from agent.yaml).
        
        Args:
            config_dict: Dictionary with configuration values
            
        Returns:
            InterviewConfig instance
        """
        # Extract model config
        model_config = ModelConfig()
        if "model_action_type" in config_dict:
            model_config.action_type = config_dict["model_action_type"]
        if "model" in config_dict:
            model_config.model = config_dict["model"]
        if "model_temperature" in config_dict:
            model_config.temperature = config_dict["model_temperature"]
        if "model_max_tokens" in config_dict:
            model_config.max_tokens = config_dict["model_max_tokens"]
        if "use_history" in config_dict:
            model_config.use_history = config_dict["use_history"]
        if "max_statement_length" in config_dict:
            model_config.max_statement_length = config_dict["max_statement_length"]
        if "history_limit" in config_dict:
            model_config.history_limit = config_dict["history_limit"]
        
        # Extract template config (can be overridden)
        template_config = TemplateConfig()
        template_attrs = [
            "summary_header_template", "summary_item_template",
            "review_directive_template", "confirmation_content_template",
            "confirmation_instructions", "confirmation_prompt",
            "unclear_edit_content_template", "unclear_general_content_template",
            "update_prompt_for_value_template", "completion_message_template",
            "cancellation_message_template", "active_event_message_template",
            "review_event_message_template", "completion_event_message_template",
            "cancellation_event_message_template", "question_directive_template",
            "required_field_decline_template", "interview_prompt",
            "interview_classification_signature"
        ]
        
        for attr in template_attrs:
            # Map attribute names (remove _template suffix for some)
            config_key = attr.replace("_template", "")
            if config_key in config_dict:
                setattr(template_config, attr.replace("_template", ""), config_dict[config_key])
        
        return cls(
            model=model_config,
            templates=template_config,
            use_dspy=config_dict.get("use_dspy", False)
        )
