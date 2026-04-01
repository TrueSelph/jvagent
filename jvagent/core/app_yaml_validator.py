"""Validation helpers for app.yaml structure and expected keys."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Set

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppYamlWarning:
    """Single app.yaml validation warning."""

    path: str
    message: str
    hint: str = ""


_SEEN_WARNING_KEYS: Set[str] = set()

_ALLOWED_TOP_LEVEL = {
    "app",
    "version",
    "author",
    "jvagent",
    "context",
    "license",
    "homepage",
    "tags",
    "config",
    "agents",
}

_ALLOWED_CONFIG_SECTIONS = {
    "server",
    "auth",
    "interact",
    "cors",
    "performance",
}

_ALLOWED_CONTEXT_KEYS = {
    "name",
    "description",
    "timezone",
    "file_storage_enabled",
    "logging_enabled",
    "log_retention_days",
    "update_mode",
}

_ALLOWED_SERVER_KEYS = {"title", "description", "version", "docs_url", "redoc_url"}
_ALLOWED_AUTH_KEYS = {
    "enabled",
    "jwt_expire_minutes",
    "api_key_management_enabled",
    "api_key_enabled",
    "api_key_prefix",
    "api_key_header",
    "exempt_paths",
}
_ALLOWED_INTERACT_KEYS = {"rate_limit_per_minute", "max_utterance_length"}
_ALLOWED_CORS_KEYS = {"enabled", "origins"}
_ALLOWED_PERFORMANCE_KEYS = {
    "enable_profiling",
    "enable_agent_cache",
    "agent_cache_ttl",
    "enable_action_cache",
    "action_cache_ttl",
    "enable_deferred_saves",
    "cache_cleanup_probability",
    "enable_interact_router_cache",
    "interact_router_cache_ttl",
}


def _mk(path: str, message: str, hint: str = "") -> AppYamlWarning:
    return AppYamlWarning(path=path, message=message, hint=hint)


def _warn_once(warnings: Iterable[AppYamlWarning], source: str) -> None:
    for w in warnings:
        key = f"{source}|{w.path}|{w.message}|{w.hint}"
        if key in _SEEN_WARNING_KEYS:
            continue
        _SEEN_WARNING_KEYS.add(key)
        suffix = f" Hint: {w.hint}" if w.hint else ""
        logger.warning(
            "app.yaml validation warning [%s]: %s.%s", w.path, w.message, suffix
        )


def _expect_type(
    warnings: List[AppYamlWarning],
    path: str,
    value: Any,
    types: tuple[type, ...],
    hint: str = "",
) -> None:
    if value is None:
        return
    if not isinstance(value, types):
        expected = "/".join(t.__name__ for t in types)
        warnings.append(
            _mk(path, f"Expected {expected}, got {type(value).__name__}", hint=hint)
        )


def _expect_list_of_str(warnings: List[AppYamlWarning], path: str, value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        warnings.append(_mk(path, f"Expected list, got {type(value).__name__}"))
        return
    for idx, item in enumerate(value):
        if not isinstance(item, str):
            warnings.append(
                _mk(
                    f"{path}[{idx}]",
                    f"Expected string item, got {type(item).__name__}",
                )
            )


def _warn_unknown_keys(
    warnings: List[AppYamlWarning],
    base_path: str,
    payload: Dict[str, Any],
    allowed_keys: Set[str],
) -> None:
    for key in payload.keys():
        if key not in allowed_keys:
            full_path = f"{base_path}.{key}" if base_path else key
            warnings.append(_mk(full_path, "Unexpected key"))


def validate_app_yaml_descriptor(data: Dict[str, Any]) -> List[AppYamlWarning]:
    """Validate full app.yaml payload and return warning entries."""
    warnings: List[AppYamlWarning] = []
    if not isinstance(data, dict):
        return [_mk("app.yaml", f"Expected mapping, got {type(data).__name__}")]

    _warn_unknown_keys(warnings, "", data, _ALLOWED_TOP_LEVEL)

    _expect_type(warnings, "app", data.get("app"), (str,))
    _expect_type(warnings, "version", data.get("version"), (str,))
    _expect_type(warnings, "author", data.get("author"), (str,))
    _expect_type(warnings, "jvagent", data.get("jvagent"), (str,))
    _expect_type(warnings, "license", data.get("license"), (str,))
    _expect_type(warnings, "homepage", data.get("homepage"), (str,))
    _expect_list_of_str(warnings, "tags", data.get("tags"))
    _expect_list_of_str(warnings, "agents", data.get("agents"))

    context = data.get("context")
    _expect_type(warnings, "context", context, (dict,))
    if isinstance(context, dict):
        _warn_unknown_keys(warnings, "context", context, _ALLOWED_CONTEXT_KEYS)
        _expect_type(warnings, "context.name", context.get("name"), (str,))
        _expect_type(
            warnings, "context.description", context.get("description"), (str,)
        )
        _expect_type(warnings, "context.timezone", context.get("timezone"), (str,))
        _expect_type(
            warnings,
            "context.file_storage_enabled",
            context.get("file_storage_enabled"),
            (bool,),
        )
        _expect_type(
            warnings, "context.logging_enabled", context.get("logging_enabled"), (bool,)
        )
        _expect_type(
            warnings,
            "context.log_retention_days",
            context.get("log_retention_days"),
            (int,),
        )

    config = data.get("config")
    if config is not None and isinstance(config, dict):
        warnings.extend(validate_app_yaml_config(config))
    else:
        _expect_type(warnings, "config", config, (dict,))

    return warnings


def validate_app_yaml_config(config: Dict[str, Any]) -> List[AppYamlWarning]:
    """Validate `config:` block shape and key support."""
    warnings: List[AppYamlWarning] = []
    if not isinstance(config, dict):
        return [_mk("config", f"Expected mapping, got {type(config).__name__}")]

    _warn_unknown_keys(warnings, "config", config, _ALLOWED_CONFIG_SECTIONS)
    for section in _ALLOWED_CONFIG_SECTIONS:
        value = config.get(section)
        if value is None:
            continue
        if not isinstance(value, dict):
            warnings.append(
                _mk(
                    f"config.{section}",
                    f"Expected mapping section, got {type(value).__name__}",
                )
            )

    server = config.get("server")
    if isinstance(server, dict):
        _warn_unknown_keys(warnings, "config.server", server, _ALLOWED_SERVER_KEYS)
        _expect_type(warnings, "config.server.title", server.get("title"), (str,))
        _expect_type(
            warnings, "config.server.description", server.get("description"), (str,)
        )
        _expect_type(warnings, "config.server.version", server.get("version"), (str,))
        _expect_type(warnings, "config.server.docs_url", server.get("docs_url"), (str,))
        _expect_type(
            warnings, "config.server.redoc_url", server.get("redoc_url"), (str,)
        )

    auth = config.get("auth")
    if isinstance(auth, dict):
        _warn_unknown_keys(warnings, "config.auth", auth, _ALLOWED_AUTH_KEYS)
        _expect_type(warnings, "config.auth.enabled", auth.get("enabled"), (bool,))
        _expect_type(
            warnings,
            "config.auth.jwt_expire_minutes",
            auth.get("jwt_expire_minutes"),
            (int,),
        )
        _expect_list_of_str(
            warnings, "config.auth.exempt_paths", auth.get("exempt_paths")
        )

    interact = config.get("interact")
    if isinstance(interact, dict):
        _warn_unknown_keys(
            warnings, "config.interact", interact, _ALLOWED_INTERACT_KEYS
        )
        _expect_type(
            warnings,
            "config.interact.rate_limit_per_minute",
            interact.get("rate_limit_per_minute"),
            (int,),
        )
        _expect_type(
            warnings,
            "config.interact.max_utterance_length",
            interact.get("max_utterance_length"),
            (int, type(None)),
        )

    cors = config.get("cors")
    if isinstance(cors, dict):
        _warn_unknown_keys(warnings, "config.cors", cors, _ALLOWED_CORS_KEYS)
        _expect_type(warnings, "config.cors.enabled", cors.get("enabled"), (bool,))
        _expect_type(
            warnings,
            "config.cors.origins",
            cors.get("origins"),
            (str, list),
            hint="Use comma-separated string or string list.",
        )

    perf = config.get("performance")
    if isinstance(perf, dict):
        _warn_unknown_keys(
            warnings, "config.performance", perf, _ALLOWED_PERFORMANCE_KEYS
        )
        bool_keys = (
            "enable_profiling",
            "enable_agent_cache",
            "enable_action_cache",
            "enable_deferred_saves",
            "enable_interact_router_cache",
        )
        int_keys = ("agent_cache_ttl", "action_cache_ttl", "interact_router_cache_ttl")
        for k in bool_keys:
            _expect_type(warnings, f"config.performance.{k}", perf.get(k), (bool,))
        for k in int_keys:
            _expect_type(warnings, f"config.performance.{k}", perf.get(k), (int,))
        _expect_type(
            warnings,
            "config.performance.cache_cleanup_probability",
            perf.get("cache_cleanup_probability"),
            (int, float),
        )

    return warnings


def warn_app_yaml_descriptor(data: Dict[str, Any], source: str = "app.yaml") -> None:
    """Run descriptor validation and emit deduplicated warnings."""
    _warn_once(validate_app_yaml_descriptor(data), source=source)


def warn_app_yaml_config(config: Dict[str, Any], source: str = "app.yaml") -> None:
    """Run config-section validation and emit deduplicated warnings."""
    _warn_once(validate_app_yaml_config(config), source=source)


def _reset_warning_cache_for_tests() -> None:
    """Reset in-memory warning dedupe cache (tests only)."""
    _SEEN_WARNING_KEYS.clear()
