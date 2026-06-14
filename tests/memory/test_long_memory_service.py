"""LongMemoryService.resolve_collection signature fix (AUDIT-memory CRIT-02).

The previous implementation called ``resolve_long_memory_collection(
agent_id=..., suffix=...)``, but the helper signature is
``(agent_id, collection_attr, config)`` — no ``suffix`` kwarg. Any caller
hit ``TypeError`` immediately.
"""

from jvagent.memory.services.long_memory_service import LongMemoryService


def test_resolve_collection_uses_suffix_as_collection_attr():
    svc = LongMemoryService()
    result = svc.resolve_collection(agent_id="agent_a", suffix="Resumes")
    assert result == "agent_a_Resumes"


def test_resolve_collection_default_suffix_when_blank():
    svc = LongMemoryService()
    # Blank suffix falls back to "LongTermMemory" inside the helper.
    result = svc.resolve_collection(agent_id="agent_b", suffix="")
    assert result == "agent_b_LongTermMemory"


def test_resolve_collection_no_agent_id():
    svc = LongMemoryService()
    result = svc.resolve_collection(agent_id="", suffix="Notes")
    assert result == "Notes"
