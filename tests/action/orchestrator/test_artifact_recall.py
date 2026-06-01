"""Deterministic artifact recall seed (ADR-0021 S3): when vision is on, the turn
carries no new image, the utterance is a back-reference, and the conversation
holds image artifacts, the most-recent interpretation(s) are seeded into the loop
so a weak model recalls without choosing a tool. Plus the prompt affordance."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jvagent.action.orchestrator.orchestrator_interact_action import (
    _RECALL_MAX_ARTIFACTS,
    _RECALL_MAX_CHARS,
    OrchestratorInteractAction,
)
from jvagent.action.orchestrator.prompts import ARTIFACT_RECALL_PROMPT

pytestmark = pytest.mark.asyncio


def _art(data, source="vision", tags=("image",)):
    return SimpleNamespace(data=data, source=source, tags=list(tags))


def _conv(by_source=None, by_tags=None):
    async def get_artifacts(*, name=None, source=None, tags=None):
        if source is not None:
            return list((by_source or {}).get(source, []))
        if tags is not None:
            return list((by_tags or {}).get(tuple(tags), []))
        return []

    return SimpleNamespace(get_artifacts=get_artifacts)


def _visitor(*, utterance="which house is more luxurious?", data=None, conv=None):
    return SimpleNamespace(utterance=utterance, data=data or {}, conversation=conv)


def _ex(vision=True):
    ex = OrchestratorInteractAction()
    ex.vision = vision
    return ex


async def test_seed_off_when_vision_disabled():
    ex = _ex(vision=False)
    conv = _conv(by_source={"vision": [_art("a villa")]})
    assert await ex._artifact_recall_seed(_visitor(conv=conv)) == ""


async def test_seed_skipped_when_new_image_present():
    # a new image this turn → the vision reflex handles it, not recall
    ex = _ex()
    conv = _conv(by_source={"vision": [_art("a villa")]})
    out = await ex._artifact_recall_seed(
        _visitor(data={"image_urls": ["u"]}, conv=conv)
    )
    assert out == ""


async def test_seed_skipped_without_backreference_cue():
    ex = _ex()
    conv = _conv(by_source={"vision": [_art("a villa")]})
    out = await ex._artifact_recall_seed(
        _visitor(utterance="what is the capital of France", conv=conv)
    )
    assert out == ""


async def test_seed_skipped_when_no_artifacts():
    ex = _ex()
    assert await ex._artifact_recall_seed(_visitor(conv=_conv())) == ""


async def test_seed_recalls_vision_artifact_on_backreference():
    ex = _ex()
    conv = _conv(
        by_source={"vision": [_art("House A: cottage"), _art("House B: villa")]}
    )
    out = await ex._artifact_recall_seed(_visitor(conv=conv))
    assert "House A: cottage" in out and "House B: villa" in out


async def test_seed_falls_back_to_image_tag():
    ex = _ex()
    conv = _conv(by_source={"vision": []}, by_tags={("image",): [_art("tagged shot")]})
    out = await ex._artifact_recall_seed(
        _visitor(utterance="describe the photo", conv=conv)
    )
    assert "tagged shot" in out


async def test_seed_caps_count_and_length():
    ex = _ex()
    many = [_art(f"art{i} " + "x" * 5000) for i in range(5)]
    conv = _conv(by_source={"vision": many})
    out = await ex._artifact_recall_seed(_visitor(conv=conv))
    # only the most-recent N, each truncated
    assert out.count("---") == _RECALL_MAX_ARTIFACTS - 1  # joined with one sep
    assert "art3" in out and "art4" in out and "art0" not in out
    for chunk in out.split("\n\n---\n\n"):
        assert len(chunk) <= _RECALL_MAX_CHARS


async def test_prompt_affordance_default_and_gating():
    ex = _ex()
    assert ex.artifact_recall_prompt == ARTIFACT_RECALL_PROMPT
    assert "list_artifacts" in ARTIFACT_RECALL_PROMPT
    # The affordance is NOT baked into the base template; _run_model appends it
    # only when vision is on (this asserts the source of truth + gating intent).
    base = ex._compose_system_prompt(
        identity_section="", tools_section="", skills_section=""
    )
    assert ARTIFACT_RECALL_PROMPT not in base
    composed = f"{base}\n\n{ex.artifact_recall_prompt}" if ex.vision else base
    assert ARTIFACT_RECALL_PROMPT in composed
