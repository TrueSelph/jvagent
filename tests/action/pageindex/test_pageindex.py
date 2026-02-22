"""Tests for PageIndex named collections and metadata."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from jvspatial.db import unregister_database

from jvagent.action.pageindex.adapter import persist_structure
from jvagent.action.pageindex.config import (
    PAGEINDEX_DB_NAME,
    get_pageindex_node_summary,
    initialize_pageindex_database,
    set_pageindex_node_summary,
)
from jvagent.action.pageindex.documents import (
    assimilate_document,
    delete_document,
    get_document_root,
    get_document_roots,
    list_documents,
)
from jvagent.action.pageindex.endpoints import _do_assimilate
from jvagent.action.pageindex.pageindex_retrieval_interact_action import (
    PageIndexRetrievalInteractAction,
    ensure_ingestion_config_for_agent,
)
from jvagent.action.pageindex.retrieval import search_documents


@pytest.fixture
def pageindex_temp_db(temp_dir):
    """Initialize PageIndex with a temp database, unregistering any existing one."""
    db_path = temp_dir / "pageindex_test"
    db_path.mkdir()
    config = {"db_type": "json", "db_path": str(db_path)}

    try:
        unregister_database(PAGEINDEX_DB_NAME)
    except Exception:
        pass

    initialize_pageindex_database(config)
    yield db_path

    try:
        unregister_database(PAGEINDEX_DB_NAME)
    except Exception:
        pass


@pytest.fixture
def sample_markdown(temp_dir):
    """Create a minimal markdown file for assimilation (no LLM needed with if_add_node_summary=no)."""
    path = temp_dir / "sample.md"
    path.write_text(
        "# Introduction\n\nThis is a test document.\n\n"
        "## Section One\n\nContent for section one.\n\n"
        "## Section Two\n\nContent for section two with finance topic.\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.asyncio
async def test_assimilate_document_with_metadata(pageindex_temp_db, sample_markdown):
    """Ingest with metadata; verify DocumentRootNode has metadata."""
    result = await assimilate_document(
        sample_markdown,
        doc_name="test_doc",
        if_add_node_summary="no",
        collection_name="col_a",
        metadata={"topic": "finance", "year": 2024},
    )
    assert result.get("doc_name") == "test_doc"
    assert "_root_id" in result

    root = await get_document_root("test_doc", collection_name="col_a")
    assert root is not None
    assert root.metadata == {"topic": "finance", "year": 2024}
    assert root.collection_name == "col_a"


@pytest.mark.asyncio
async def test_assimilate_document_without_metadata(pageindex_temp_db, sample_markdown):
    """Ingest without metadata; verify metadata is None."""
    result = await assimilate_document(
        sample_markdown,
        doc_name="test_doc_no_meta",
        if_add_node_summary="no",
        collection_name="default",
    )
    assert result.get("doc_name") == "test_doc_no_meta"

    root = await get_document_root("test_doc_no_meta", collection_name="default")
    assert root is not None
    assert root.metadata is None


@pytest.mark.asyncio
async def test_search_with_metadata_filter(pageindex_temp_db, sample_markdown):
    """Ingest docs with different metadata; search with filter; verify only matching docs returned."""
    await assimilate_document(
        sample_markdown,
        doc_name="doc_finance",
        if_add_node_summary="no",
        collection_name="col_x",
        metadata={"topic": "finance"},
    )
    await assimilate_document(
        sample_markdown,
        doc_name="doc_legal",
        if_add_node_summary="no",
        collection_name="col_x",
        metadata={"topic": "legal"},
    )

    results = await search_documents(
        query="content",
        strategy="direct",
        limit=20,
        collection_name="col_x",
        metadata_filter={"topic": "finance"},
    )
    doc_names = {r.get("doc_name") for r in results}
    assert doc_names == {"doc_finance"}


@pytest.mark.asyncio
async def test_search_without_metadata_filter(pageindex_temp_db, sample_markdown):
    """Search without filter; verify all docs in collection returned."""
    await assimilate_document(
        sample_markdown,
        doc_name="doc_a",
        if_add_node_summary="no",
        collection_name="col_y",
    )
    await assimilate_document(
        sample_markdown,
        doc_name="doc_b",
        if_add_node_summary="no",
        collection_name="col_y",
    )

    results = await search_documents(
        query="content",
        strategy="direct",
        limit=20,
        collection_name="col_y",
    )
    doc_names = {r.get("doc_name") for r in results}
    assert doc_names == {"doc_a", "doc_b"}


@pytest.mark.asyncio
async def test_list_documents_with_metadata_filter(pageindex_temp_db, sample_markdown):
    """List with filter; verify correct subset."""
    await assimilate_document(
        sample_markdown,
        doc_name="doc_internal",
        if_add_node_summary="no",
        collection_name="col_z",
        metadata={"access": "internal"},
    )
    await assimilate_document(
        sample_markdown,
        doc_name="doc_public",
        if_add_node_summary="no",
        collection_name="col_z",
        metadata={"access": "public"},
    )

    docs = await list_documents(
        collection_name="col_z",
        metadata_filter={"access": "internal"},
    )
    assert len(docs) == 1
    assert docs[0]["doc_name"] == "doc_internal"


@pytest.mark.asyncio
async def test_metadata_filter_multiple_keys(pageindex_temp_db, sample_markdown):
    """Filter by multiple keys; verify AND semantics."""
    await assimilate_document(
        sample_markdown,
        doc_name="doc_both",
        if_add_node_summary="no",
        collection_name="col_m",
        metadata={"topic": "finance", "year": 2024},
    )
    await assimilate_document(
        sample_markdown,
        doc_name="doc_topic_only",
        if_add_node_summary="no",
        collection_name="col_m",
        metadata={"topic": "finance"},
    )

    roots = await get_document_roots(
        collection_name="col_m",
        metadata_filter={"topic": "finance", "year": 2024},
    )
    assert len(roots) == 1
    assert roots[0].doc_name == "doc_both"


@pytest.mark.asyncio
async def test_metadata_serialization(pageindex_temp_db, sample_markdown):
    """Verify str, int, bool, list values round-trip."""
    metadata = {
        "s": "text",
        "i": 42,
        "b": True,
        "lst": ["a", "b"],
    }
    await assimilate_document(
        sample_markdown,
        doc_name="doc_serial",
        if_add_node_summary="no",
        collection_name="col_s",
        metadata=metadata,
    )

    root = await get_document_root("doc_serial", collection_name="col_s")
    assert root is not None
    assert root.metadata["s"] == "text"
    assert root.metadata["i"] == 42
    assert root.metadata["b"] is True
    assert root.metadata["lst"] == ["a", "b"]


@pytest.mark.asyncio
async def test_collection_isolation(pageindex_temp_db, sample_markdown):
    """Documents in different collections are isolated."""
    await assimilate_document(
        sample_markdown,
        doc_name="same_name",
        if_add_node_summary="no",
        collection_name="col_1",
    )
    await assimilate_document(
        sample_markdown,
        doc_name="same_name",
        if_add_node_summary="no",
        collection_name="col_2",
    )

    root1 = await get_document_root("same_name", collection_name="col_1")
    root2 = await get_document_root("same_name", collection_name="col_2")
    assert root1 is not None and root2 is not None
    assert root1.id != root2.id

    docs_col1 = await list_documents(collection_name="col_1")
    docs_col2 = await list_documents(collection_name="col_2")
    assert len(docs_col1) == 1 and len(docs_col2) == 1


@pytest.mark.asyncio
async def test_adapter_persists_distinct_summary_vs_text(pageindex_temp_db):
    """When structure has summary != text, adapter persists both correctly."""
    structure = [
        {
            "title": "Section",
            "text": "Full section text content here",
            "summary": "LLM-generated summary",
            "node_id": "n1",
            "physical_index": 0,
            "start_index": 0,
            "end_index": 25,
            "structure": "section",
        },
    ]
    await persist_structure(
        doc_name="summary_test",
        structure=structure,
        collection_name="col_summary",
    )
    results = await search_documents(
        query="section",
        strategy="direct",
        limit=5,
        collection_name="col_summary",
    )
    assert len(results) >= 1
    node = next((r for r in results if r.get("doc_name") == "summary_test"), None)
    assert node is not None
    assert node.get("text") == "Full section text content here"
    assert node.get("summary") == "LLM-generated summary"
    assert node["summary"] != node["text"]


@pytest.mark.asyncio
async def test_ensure_ingestion_config_for_agent_fallback():
    """When cache miss, ensure_ingestion_config_for_agent defaults to node_summary=True."""
    set_pageindex_node_summary(False)
    with patch(
        "jvagent.core.cache.get_cached_actions",
        new_callable=AsyncMock,
    ) as m:
        m.return_value = None
        await ensure_ingestion_config_for_agent("agent_123")
    assert get_pageindex_node_summary() is True


@pytest.mark.asyncio
async def test_ensure_ingestion_config_for_agent_from_action():
    """When cache has PageIndex action, ensure_ingestion_config_for_agent uses its config."""
    mock_action = type(
        "MockAction", (), {"config": {"node_summary": "yes"}, "node_summary": True}
    )()

    def mock_isinstance(obj, cls):
        if (
            cls == PageIndexRetrievalInteractAction
            and getattr(obj, "config", None) is not None
        ):
            return True
        return isinstance(obj, cls)

    with patch(
        "jvagent.core.cache.get_cached_actions",
        new_callable=AsyncMock,
    ) as m:
        m.return_value = [mock_action]
        with patch(
            "jvagent.action.pageindex.pageindex_retrieval_interact_action.isinstance",
            mock_isinstance,
        ):
            await ensure_ingestion_config_for_agent("agent_123")
    assert get_pageindex_node_summary() is True


@pytest.mark.asyncio
async def test_do_assimilate_with_if_add_node_summary(
    pageindex_temp_db, sample_markdown
):
    """_do_assimilate passes if_add_node_summary to assimilate_document."""
    content = sample_markdown.read_bytes()
    result = await _do_assimilate(
        content,
        ".md",
        doc_name="form_test",
        if_add_node_summary="no",
        collection_name="col_form",
    )
    assert result.get("doc_name") == "form_test"
    assert "_root_id" in result
