"""Minimal smoke: vectorstore action import and instantiate."""

from jvagent.action.vectorstore.typesense.typesense import TypesenseVectorStore


def test_vectorstore_action_instantiates() -> None:
    action = TypesenseVectorStore()
    assert action.__class__.__name__ == "TypesenseVectorStore"
