"""Pure long-memory retrieval helpers (no PageIndex stack)."""

from jvagent.memory.long_memory_retrieval_utils import (
    resolve_long_memory_collection,
    utterance_overlaps_category_keywords,
)


class _Cat:
    def __init__(self, category: str, keywords: list, empty: bool = False):
        self.category = category
        self.title = category.title()
        self.keywords = keywords
        self._empty = empty

    def is_empty(self) -> bool:
        return self._empty


def test_resolve_collection_prefers_config_over_attribute() -> None:
    assert (
        resolve_long_memory_collection(
            "n.Agent.demo",
            "LongTermMemory",
            {"collection": "FromYaml"},
        )
        == "n.Agent.demo_FromYaml"
    )


def test_resolve_collection_falls_back_to_attribute() -> None:
    assert (
        resolve_long_memory_collection(
            "n.Agent.demo",
            "LongTermMemory",
            {},
        )
        == "n.Agent.demo_LongTermMemory"
    )


def test_resolve_collection_collection_name_alias() -> None:
    assert (
        resolve_long_memory_collection(
            "n.Agent.demo",
            "Ignored",
            {"collection_name": "Alt"},
        )
        == "n.Agent.demo_Alt"
    )


def test_resolve_collection_no_agent_id() -> None:
    assert resolve_long_memory_collection("", "LongTermMemory", {}) == "LongTermMemory"


def test_utterance_overlaps_category_keywords() -> None:
    cats = [_Cat("i", ["Netflix", "streaming"])]
    assert (
        utterance_overlaps_category_keywords(cats, "I watch netflix daily", "") is True
    )
    assert (
        utterance_overlaps_category_keywords(cats, "nothing relevant", "still nothing")
        is False
    )
