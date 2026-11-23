"""Offline retirement of the reviewed default vision reflex into SDK input."""

from typing import Any

IMAGE_TYPES = ("image/gif", "image/jpeg", "image/png", "image/webp")
# Source review: only the retiring Orchestrator uploads module calls VisionAction.
# Unknown plugins may depend on its tool/API and must be reviewed before removal.
REVIEWED_CONSUMERS = frozenset(
    {
        "jvagent/orchestrator",
        "jvagent/reply",
        "jvagent/mcp",
        "jvagent/serper_web_search",
        "jvagent/web_search",
        "jvagent/web_fetch",
        "jvagent/pageindex_action",
        "jvagent/pageindex_read",
        "jvagent/agent_utils",
        "jvagent/code_execution",
        "jvagent/file_interface",
        "jvagent/skill_hub",
        "jvagent/intro_interact_action",
        "jvagent/interview",
        "jvagent/access_control",
        "jvagent/access_control_action",
        "jvagent/whatsapp_action",
        "jvagent/whatsapp_voice_action",
        "jvagent/elevenlabs_tts",
        "jvagent/task_monitor",
        "jvagent/openai_lm",
        "jvagent/ollama_lm",
        "jvagent/litellm_lm",
    }
)


def migrate_vision(
    context: dict[str, Any], actions: list[dict[str, Any]], report: dict[str, Any]
) -> bool:
    """Return whether the reviewed VisionAction is proposed for retirement.

    Native input stores original media in SDK history; it does not reproduce
    independent model calls, descriptions as artifacts, remote URL downloads or
    the interpret_images tool. This is an explicit configuration migration.
    """
    if "vision" not in context:
        return False
    enabled = context["vision"]
    if enabled is False:
        report.setdefault("retired_settings", {})[
            "vision"
        ] = "Retired disabled pre-loop vision control; no image input is enabled"
        report["mappings"]["vision"] = "retired inactive control"
        return False
    vision = [a for a in actions if a["action"] == "jvagent/vision"]
    configured = vision[0].get("context", {}) if len(vision) == 1 else None
    if (
        enabled is not True
        or len(vision) != 1
        or set(vision[0]) - {"action", "context"}
        or not isinstance(configured, dict)
        or set(configured) - {"enabled", "model_action_type", "model"}
        or configured.get("enabled", True) is not True
        or configured.get("model_action_type", "OpenAILanguageModelAction")
        != "OpenAILanguageModelAction"
        or configured.get("model", "gpt-4o") not in ("gpt-4o", "gpt-4.1")
        or context.get("model") not in ("gpt-4.1", "openai/gpt-4.1", "openai:gpt-4.1")
        or context.get("model_action_type", "OpenAILanguageModelAction")
        not in ("OpenAILanguageModelAction", "LiteLLMLanguageModelAction")
        or any(
            a["action"] not in REVIEWED_CONSUMERS | {"jvagent/vision"} for a in actions
        )
    ):
        report["blockers"].append(
            "Legacy vision policy, independent model settings or callers require explicit native migration"
        )
        return False
    report.setdefault("retired_actions", []).append("jvagent/vision")
    report.setdefault("retired_settings", {})[
        "vision"
    ] = "Retired independent interpreter and description-artifact reflex; native primary-model image input and SDK media history replace them"
    report["mappings"][
        "vision"
    ] = "input_media_types; independent vision execution retired"
    report["required_review"].append(
        "Review native image input with the primary model: original media replaces generated description artifacts; explicit image_interpretation:false refuses current images; remote URLs, interpret_images callers, other channel opt-outs and retained artifacts require separate migration"
    )
    return True
