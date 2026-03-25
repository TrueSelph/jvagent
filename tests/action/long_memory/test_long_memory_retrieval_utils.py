"""Pure long-memory retrieval helpers (no PageIndex stack)."""

from jvagent.memory.long_memory_retrieval_utils import resolve_long_memory_collection


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
