"""Leadgen SOP composition helpers."""

from __future__ import annotations

from jvagent.scaffold.sop_extend import compose_skill_body, load_action_base_sop_body

_LEADGEN_ACTION_REF = "jvagent/leadgen"


def get_standard_leadgen_procedure() -> str:
    return load_action_base_sop_body(_LEADGEN_ACTION_REF)


def compose_leadgen_skill_body(custom_body: str = "") -> str:
    return compose_skill_body(get_standard_leadgen_procedure(), custom_body)


__all__ = [
    "compose_leadgen_skill_body",
    "get_standard_leadgen_procedure",
]
