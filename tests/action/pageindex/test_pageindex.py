"""Tests for PageIndex named collections and metadata."""

import os

import pytest

pytest.importorskip("openai")
pytest.importorskip("tiktoken")
pytest.importorskip("litellm")
pytest.importorskip("PyPDF2")

from pathlib import Path
from unittest.mock import AsyncMock, patch

from jvspatial.core.context import get_default_context, set_default_context
from jvspatial.db import get_database_manager, unregister_database

from jvagent.action.pageindex.adapter import persist_structure
from jvagent.action.pageindex.config import (
    PAGEINDEX_DB_NAME,
    get_pageindex_config,
    get_pageindex_max_summary_chars,
    get_pageindex_node_summary,
    initialize_pageindex_database,
    set_pageindex_max_summary_chars,
    set_pageindex_node_summary,
    set_pageindex_retrieval_excerpt_source,
)
from jvagent.action.pageindex.document_walker import DocumentWalker
from jvagent.action.pageindex.core.utils import list_to_tree
from jvagent.action.pageindex.documents import (
    assimilate_document,
    delete_document,
    enrich_structure_titles,
    get_document_root,
    get_document_roots,
    list_document_chunks,
    list_documents,
)
from jvagent.action.pageindex.md_tree_enriched import (
    annotate_content_type_and_enabled,
    assign_hierarchy_breadcrumbs,
)
from jvagent.action.pageindex.endpoints import _do_assimilate
from jvagent.action.pageindex.pageindex_retrieval_interact_action import (
    PageIndexRetrievalInteractAction,
    ensure_ingestion_config_for_agent,
)
from jvagent.action.pageindex.models import DocumentNode, node_to_result
from jvagent.action.pageindex.retrieval import (
    _graph_to_tree,
    _parse_llm_json_object,
    search_documents,
)


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
async def test_assimilate_promotes_metadata_doc_url_to_root(
    pageindex_temp_db, sample_markdown
):
    """doc_url in metadata alone is stored on DocumentRootNode for citations."""
    with patch(
        "jvagent.action.pageindex.documents._get_app_id_from_node",
        new_callable=AsyncMock,
        return_value=None,
    ):
        await assimilate_document(
            sample_markdown,
            doc_name="meta_url_only",
            if_add_node_summary="no",
            collection_name="col_meta_url",
            metadata={"doc_url": "https://example.com/from-metadata"},
        )
    root = await get_document_root("meta_url_only", collection_name="col_meta_url")
    assert root is not None
    assert root.doc_url == "https://example.com/from-metadata"


@pytest.mark.asyncio
async def test_search_include_references_false_omits_doc_url(pageindex_temp_db):
    """When include_references is False, search results have no doc_url."""
    structure = [
        {
            "title": "Section",
            "text": "hello cite me",
            "node_id": "n1",
            "physical_index": 0,
            "start_index": 0,
            "end_index": 1,
            "structure": "1",
        },
    ]
    await persist_structure(
        doc_name="omit_url_doc",
        structure=structure,
        collection_name="col_ref_flag",
        doc_url="https://example.com/source.pdf",
    )
    with_refs = await search_documents(
        query="hello",
        strategy="direct",
        limit=10,
        collection_name="col_ref_flag",
        include_references=True,
    )
    assert any(r.get("doc_url") == "https://example.com/source.pdf" for r in with_refs)

    without_refs = await search_documents(
        query="hello",
        strategy="direct",
        limit=10,
        collection_name="col_ref_flag",
        include_references=False,
    )
    assert without_refs
    assert all("doc_url" not in r for r in without_refs)


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
    assert node.get("content") == "LLM-generated summary"

    set_pageindex_retrieval_excerpt_source("text")
    results_text = await search_documents(
        query="section",
        strategy="direct",
        limit=5,
        collection_name="col_summary",
    )
    node_text = next(
        (r for r in results_text if r.get("doc_name") == "summary_test"), None
    )
    assert node_text is not None
    assert node_text.get("content") == "Full section text content here"
    set_pageindex_retrieval_excerpt_source(None)


def test_node_to_result_excerpt_source_explicit():
    """node_to_result honors excerpt_source without context var."""
    n = DocumentNode()
    n.title = "T"
    n.text = "full body"
    n.summary = "short sum"
    assert node_to_result(n, excerpt_source="summary")["content"] == "short sum"
    assert node_to_result(n, excerpt_source="text")["content"] == "full body"


def test_parse_llm_json_object_ignores_trailing_text():
    raw = '{"thinking":"x","node_list":["0001"]}\n\nExtra thanks.'
    out = _parse_llm_json_object(raw)
    assert out["node_list"] == ["0001"]


@pytest.mark.asyncio
async def test_graph_to_tree_summary_vs_text_excerpt(pageindex_temp_db):
    """_graph_to_tree uses summary-first or text-first per excerpt_source."""
    structure = [
        {
            "title": "Section",
            "text": "Full section body",
            "summary": "LLM summary line",
            "node_id": "n1",
            "physical_index": 0,
            "start_index": 0,
            "end_index": 1,
            "structure": "1",
        },
    ]
    await persist_structure(
        doc_name="excerpt_mode_test",
        structure=structure,
        collection_name="col_excerpt",
    )
    root = await get_document_root("excerpt_mode_test", collection_name="col_excerpt")
    assert root is not None
    db = get_database_manager().get_database(PAGEINDEX_DB_NAME)
    from jvspatial.core.context import GraphContext

    ctx = GraphContext(database=db)
    prev = get_default_context()
    try:
        set_default_context(ctx)
        tree_s = await _graph_to_tree(root, excerpt_source="summary")
        tree_t = await _graph_to_tree(root, excerpt_source="text")
        assert tree_s and tree_s[0]["summary"] == "LLM summary line"
        assert tree_t and tree_t[0]["summary"] == "Full section body"
    finally:
        set_default_context(prev)


@pytest.mark.asyncio
async def test_ensure_ingestion_config_for_agent_fallback():
    """When cache miss, ensure_ingestion_config_for_agent uses text-first defaults."""
    set_pageindex_node_summary(True)
    with patch(
        "jvagent.core.cache.get_cached_actions",
        new_callable=AsyncMock,
    ) as m:
        m.return_value = None
        await ensure_ingestion_config_for_agent("agent_123")
    assert get_pageindex_node_summary() is False


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


def _max_summary_len_in_tree(tree):
    """Recursively find max summary/prefix_summary length in tree."""
    max_len = 0
    for node in tree:
        for key in ("summary", "prefix_summary"):
            val = node.get(key)
            if val:
                max_len = max(max_len, len(val))
        if node.get("nodes"):
            max_len = max(max_len, _max_summary_len_in_tree(node["nodes"]))
    return max_len


@pytest.mark.asyncio
async def test_graph_to_tree_truncates_summaries(pageindex_temp_db):
    """_graph_to_tree truncates summaries to max_summary_chars."""
    long_summary = "A" * 500
    structure = [
        {
            "title": "Section",
            "text": "Content",
            "summary": long_summary,
            "node_id": "n1",
            "physical_index": 0,
            "start_index": 0,
            "end_index": 1,
            "structure": "1",
        },
    ]
    await persist_structure(
        doc_name="trunc_test",
        structure=structure,
        collection_name="col_trunc",
    )
    root = await get_document_root("trunc_test", collection_name="col_trunc")
    assert root is not None

    db = get_database_manager().get_database(PAGEINDEX_DB_NAME)
    from jvspatial.core.context import GraphContext

    ctx = GraphContext(database=db)
    prev = get_default_context()
    try:
        set_default_context(ctx)
        tree = await _graph_to_tree(root, max_summary_chars=50)
        assert len(tree) >= 1
        max_len = _max_summary_len_in_tree(tree)
        assert max_len <= 51

        tree_long = await _graph_to_tree(root, max_summary_chars=1000)
        max_len_long = _max_summary_len_in_tree(tree_long)
        assert max_len_long <= 1001
    finally:
        set_default_context(prev)


@pytest.mark.asyncio
async def test_document_walker_respects_limit(pageindex_temp_db):
    """DocumentWalker stops when report reaches limit."""
    structure = [
        {"title": "A", "text": "content", "node_id": "n1", "structure": "1"},
        {"title": "B", "text": "content", "node_id": "n2", "structure": "2"},
        {"title": "C", "text": "content", "node_id": "n3", "structure": "3"},
    ]
    for s in structure:
        s.update({"physical_index": 0, "start_index": 0, "end_index": 1})
    await persist_structure(
        doc_name="walker_limit_test",
        structure=structure,
        collection_name="col_walker",
    )
    root = await get_document_root("walker_limit_test", collection_name="col_walker")
    assert root is not None

    walker = DocumentWalker(query="content", limit=2)
    await walker.spawn(root)
    report = await walker.get_report()
    assert len(report) <= 2


@pytest.mark.asyncio
async def test_tree_search_falls_back_when_over_token_budget(pageindex_temp_db):
    """When tree exceeds max_tree_prompt_tokens, fall back to direct search."""
    structure = [
        {
            "title": "Section",
            "text": "content to match",
            "summary": "X" * 1000,
            "node_id": "n1",
            "physical_index": 0,
            "start_index": 0,
            "end_index": 1,
            "structure": "1",
        },
    ]
    await persist_structure(
        doc_name="token_budget_test",
        structure=structure,
        collection_name="col_budget",
    )

    results = await search_documents(
        query="content",
        strategy="tree_search",
        limit=5,
        collection_name="col_budget",
        max_tree_prompt_tokens=100,
    )
    assert len(results) >= 1
    assert any(r.get("doc_name") == "token_budget_test" for r in results)


@pytest.mark.asyncio
async def test_config_max_summary_chars():
    """set_pageindex_max_summary_chars and get_pageindex_max_summary_chars work."""
    set_pageindex_max_summary_chars(200)
    assert get_pageindex_max_summary_chars() == 200
    set_pageindex_max_summary_chars(None)
    assert get_pageindex_max_summary_chars() == 300


def test_get_pageindex_config_db_name_derivation():
    """get_pageindex_config derives db name from app_id when JVAGENT_PAGEINDEX_DB_NAME unset."""
    with patch.dict("os.environ", {"JVAGENT_PAGEINDEX_DB_NAME": ""}, clear=False):
        config = get_pageindex_config(app_id="jvagent_demo_app")
    assert config["db_type"] == "json"
    assert "jvagent_demo_app_pageindex_db" in config["db_path"]


def test_get_pageindex_config_explicit_db_name():
    """JVAGENT_PAGEINDEX_DB_NAME overrides derivation."""
    with patch.dict("os.environ", {"JVAGENT_PAGEINDEX_DB_NAME": "custom_pageindex_db"}):
        config = get_pageindex_config(app_id="jvagent_demo_app")
    assert config["db_type"] == "json"
    assert "custom_pageindex_db" in config["db_path"]


def test_get_pageindex_config_mongodb_uri_falls_back_to_jvspatial():
    """When DB_TYPE is mongodb and PAGEINDEX URI unset, use JVSPATIAL_MONGODB_URI."""
    env = {
        "JVAGENT_PAGEINDEX_DB_TYPE": "mongodb",
        "JVAGENT_PAGEINDEX_DB_URI": "",
        "JVSPATIAL_MONGODB_URI": "mongodb://cluster.example:27017",
    }
    with patch.dict("os.environ", env, clear=False):
        config = get_pageindex_config(app_id="jvagent_demo_app")
    assert config["db_type"] == "mongodb"
    assert config["db_uri"] == "mongodb://cluster.example:27017"


def test_get_pageindex_config_mongodb_uri_explicit_wins_over_jvspatial():
    """JVAGENT_PAGEINDEX_DB_URI takes precedence over JVSPATIAL_MONGODB_URI."""
    env = {
        "JVAGENT_PAGEINDEX_DB_TYPE": "mongodb",
        "JVAGENT_PAGEINDEX_DB_URI": "mongodb://pageindex-only:27017",
        "JVSPATIAL_MONGODB_URI": "mongodb://cluster.example:27017",
    }
    with patch.dict("os.environ", env, clear=False):
        config = get_pageindex_config(app_id="jvagent_demo_app")
    assert config["db_uri"] == "mongodb://pageindex-only:27017"


def test_get_pageindex_config_mongodb_uri_localhost_when_both_unset():
    """Whitespace-only URIs fall through to localhost default."""
    env = {
        "JVAGENT_PAGEINDEX_DB_TYPE": "mongodb",
        "JVAGENT_PAGEINDEX_DB_URI": "   ",
        "JVSPATIAL_MONGODB_URI": "",
    }
    with patch.dict("os.environ", env, clear=False):
        config = get_pageindex_config(app_id="jvagent_demo_app")
    assert config["db_uri"] == "mongodb://localhost:27017"


def test_enrich_structure_titles_adds_section_prefix():
    structure = [
        {"structure": "1", "title": "Intro", "nodes": []},
        {
            "structure": "1.2",
            "title": "Details",
            "nodes": [{"structure": "1.2.1", "title": "Sub", "nodes": []}],
        },
    ]
    out = enrich_structure_titles(structure)
    assert out[0]["title"] == "1 Intro"
    assert out[1]["title"] == "1.2 Details"
    assert out[1]["nodes"][0]["title"] == "1.2.1 Sub"


def test_enrich_structure_titles_skips_zero_and_existing_prefix():
    structure = [
        {"structure": "0", "title": "Preface", "nodes": []},
        {"structure": "2", "title": "2 Already numbered", "nodes": []},
    ]
    out = enrich_structure_titles(structure)
    assert out[0]["title"] == "Preface"
    assert out[1]["title"] == "2 Already numbered"


def test_merge_running_header_absorbs_iso_page_break_heading():
    """Running-header ## at page break is merged into the prior definition section."""
    from jvagent.action.pageindex.md_tree_enriched import (
        extract_node_text_content,
        extract_nodes_from_markdown,
        merge_adjacent_clause_headings,
        merge_running_header_blocks,
    )

    md = """## 3.21 reasonably foreseeable misuse

Note 3 body.

## ISO 22367 ISO/DIS 22367:2019(E)

Note 4 body.

## 4 Next section

Other.
"""
    node_list, lines = extract_nodes_from_markdown(md)
    nodes = extract_node_text_content(node_list, lines)
    nodes = merge_adjacent_clause_headings(nodes, lines)
    out = merge_running_header_blocks(nodes, lines)
    assert len(out) == 2
    assert out[0]["title"] == "3.21 reasonably foreseeable misuse"
    assert "Note 3" in out[0]["text"]
    assert "Note 4" in out[0]["text"]
    assert "ISO 22367" in out[0]["text"]
    assert out[1]["title"] == "4 Next section"
    assert "Other." in out[1]["text"]


def test_merge_running_header_noop_without_boilerplate_headings():
    from jvagent.action.pageindex.md_tree_enriched import (
        extract_node_text_content,
        extract_nodes_from_markdown,
        merge_adjacent_clause_headings,
        merge_running_header_blocks,
    )

    md = """## Section One

Alpha.

## Section Two

Beta.
"""
    node_list, lines = extract_nodes_from_markdown(md)
    nodes = extract_node_text_content(node_list, lines)
    merged_clause = merge_adjacent_clause_headings(nodes, lines)
    out = merge_running_header_blocks(merged_clause, lines)
    assert len(out) == len(merged_clause) == 2


def test_list_to_tree_preserves_structure_and_physical_index():
    flat = [
        {
            "structure": "1",
            "title": "One",
            "physical_index": 1,
            "start_index": 1,
            "end_index": 5,
        },
        {
            "structure": "1.1",
            "title": "One-one",
            "physical_index": 2,
            "start_index": 2,
            "end_index": 5,
        },
    ]
    tree = list_to_tree(flat)
    assert len(tree) == 1
    root = tree[0]
    assert root["structure"] == "1"
    assert root["physical_index"] == 1
    child = root["nodes"][0]
    assert child["structure"] == "1.1"
    assert child["physical_index"] == 2


def test_finalize_pdf_shaped_tree_hierarchy_and_content_type():
    """list_to_tree output + shared finalization yields hierarchy and content_type."""
    flat = [
        {
            "structure": "1",
            "title": "Introduction",
            "physical_index": 1,
            "start_index": 1,
            "end_index": 2,
        },
        {
            "structure": "1.1",
            "title": "Scope",
            "physical_index": 2,
            "start_index": 2,
            "end_index": 3,
        },
        {
            "structure": "2",
            "title": "Normative references",
            "physical_index": 4,
            "start_index": 4,
            "end_index": 5,
        },
    ]
    tree = list_to_tree(flat)
    # Synthetic flat items have no body text until PDF add_node_text; fill for shape inference.
    tree[0]["nodes"][0]["text"] = (
        "This section defines the scope of the document.\n\n"
        "Additional paragraph with enough characters for substantive classification."
    )
    tree[1]["text"] = (
        "ISO 9001 and related standards are cited in this clause.\n\nSecond paragraph here."
    )
    assign_hierarchy_breadcrumbs(tree)
    annotate_content_type_and_enabled(tree)

    intro = tree[0]
    assert intro["hierarchy"] == ["Introduction"]
    assert intro["content_type"] == "introduction"
    assert intro["enabled"] is True
    assert intro.get("structure") == "1"

    scope = intro["nodes"][0]
    assert scope["hierarchy"] == ["Introduction", "Scope"]
    assert scope["content_type"] == "substantive"
    assert scope["enabled"] is True
    assert scope.get("structure") == "1.1"

    norms = tree[1]
    assert norms["title"] == "Normative references"
    assert norms["hierarchy"] == ["Normative references"]
    assert norms["content_type"] == "substantive"
    assert norms["enabled"] is True
    assert norms.get("structure") == "2"


def test_enrich_structure_titles_then_breadcrumbs_uses_prefixed_titles():
    """After TOC prefixing, breadcrumbs use the enriched titles (assimilate order)."""
    flat = [
        {
            "structure": "1",
            "title": "Scope",
            "physical_index": 1,
            "start_index": 1,
            "end_index": 3,
        },
    ]
    tree = list_to_tree(flat)
    tree = enrich_structure_titles(tree)
    assign_hierarchy_breadcrumbs(tree)
    assert tree[0]["title"] == "1 Scope"
    assert tree[0]["hierarchy"] == ["1 Scope"]


@pytest.mark.asyncio
async def test_assimilate_markdown_chunks_have_hierarchy_and_content_type(
    pageindex_temp_db, sample_markdown
):
    """Markdown clause-style hierarchy is preserved; content_type comes from assimilate finalization."""
    await assimilate_document(
        sample_markdown,
        doc_name="md_hier_test",
        if_add_node_summary="no",
        collection_name="col_md",
    )
    out = await list_document_chunks("md_hier_test", collection_name="col_md")
    chunks = {c["title"]: c for c in out["chunks"]}

    intro = chunks["Introduction"]
    assert intro["hierarchy"] == ["Introduction"]
    assert intro["content_type"] == "introduction"
    assert intro["enabled"] is True

    sec2 = chunks["Section Two"]
    assert sec2["hierarchy"] == ["Introduction", "Section Two"]
    assert sec2["content_type"] == "substantive"
    assert sec2["enabled"] is True

