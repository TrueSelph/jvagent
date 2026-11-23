"""Qualified provider profiles through public SDK constructors."""

import re
from typing import Any


def qualified_model(model: Any) -> Any:
    """Keep GPT-4.1 skill tools in ordinary Responses tool declarations.

    Core 2.66 enables first-party additional_tools for every OpenAI model.
    Ordinary multi-skill GPT-4.1 browser requests repeatedly load capabilities
    without invoking the revealed tools. Use the SDK's public fallback message
    projection and tool declarations. Remove this override once the same
    success/denial/restart cohort passes with the upstream default profile.

    Explicit model instances retain their caller-established profiles/clients.
    No provider protocol, message transformation or execution is implemented here.
    """
    if not isinstance(model, str):
        return model
    provider, separator, name = model.partition(":")
    if (
        not separator
        or provider not in {"openai", "openai-responses"}
        or re.fullmatch(r"gpt-4\.1(?:-\d{4}-\d{2}-\d{2})?", name) is None
    ):
        return model
    from pydantic_ai.models.openai import OpenAIResponsesModel
    from pydantic_ai.profiles.openai import OpenAIModelProfile

    return OpenAIResponsesModel(
        name, profile=OpenAIModelProfile(tool_addition_mode=None)
    )
