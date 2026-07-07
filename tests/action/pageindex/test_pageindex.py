"""Tests for PageIndex named collections and metadata."""

import pytest

pytest.importorskip("openai")
pytest.importorskip("tiktoken")
pytest.importorskip("litellm")
pytest.importorskip("PyPDF2")

from unittest.mock import AsyncMock, patch

from jvspatial.api.exceptions import ValidationError
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
from jvagent.action.pageindex.core.utils import list_to_tree
from jvagent.action.pageindex.document_walker import DocumentWalker
from jvagent.action.pageindex.documents import (
    _build_metadata_query,
    assimilate_document,
    enrich_structure_titles,
    export_documents,
    get_document_root,
    get_document_roots,
    list_document_chunks,
    list_documents,
)
from jvagent.action.pageindex.endpoints import (
    _do_assimilate,
    get_documents_queue_endpoint,
)
from jvagent.action.pageindex.jvforge_routing import resolve_effective_jvforge_base
from jvagent.action.pageindex.md_tree_enriched import (
    annotate_content_type_and_enabled,
    assign_hierarchy_breadcrumbs,
)
from jvagent.action.pageindex.models import DocumentNode, node_to_result
from jvagent.action.pageindex.pageindex_action import (
    PageIndexAction,
    ensure_ingestion_config_for_agent,
)
from jvagent.action.pageindex.retrieval import (
    _graph_to_tree,
    _parse_llm_json_object,
    _root_matches_metadata,
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
def sample_markdown():
    """Markdown body for assimilation (passed as content, not an external file path)."""
    return (
        "# Introduction\n\nThis is a test document.\n\n"
        "## Section One\n\nContent for section one.\n\n"
        "## Section Two\n\nContent for section two with finance topic.\n"
    )


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
    assert docs[0]["chunks"] > 0


@pytest.mark.asyncio
async def test_chunks_list_export_and_root(pageindex_temp_db, sample_markdown):
    """list_documents, export roots, and DocumentRootNode carry consistent chunk counts."""
    await assimilate_document(
        sample_markdown,
        doc_name="chunky_doc",
        if_add_node_summary="no",
        collection_name="col_chunks",
    )
    docs = await list_documents(collection_name="col_chunks")
    assert len(docs) == 1
    chunks_listed = docs[0]["chunks"]
    assert isinstance(chunks_listed, int) and chunks_listed > 0

    root = await get_document_root("chunky_doc", collection_name="col_chunks")
    assert root is not None
    assert root.chunks == chunks_listed

    exported = await export_documents(
        collection_name="col_chunks", doc_name="chunky_doc"
    )
    assert len(exported["roots"]) == 1
    assert exported["roots"][0]["chunks"] == chunks_listed
    assert len(exported["nodes"]) == chunks_listed


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
    from jvspatial.core.context import scoped_default_context_async

    async with scoped_default_context_async(ctx):
        tree_s = await _graph_to_tree(root, excerpt_source="summary")
        tree_t = await _graph_to_tree(root, excerpt_source="text")
        assert tree_s and tree_s[0]["summary"] == "LLM summary line"
        assert tree_t and tree_t[0]["summary"] == "Full section body"


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

    real_isinstance = isinstance

    def mock_isinstance(obj, cls):
        if cls == PageIndexAction and getattr(obj, "config", None) is not None:
            return True
        return real_isinstance(obj, cls)

    with patch(
        "jvagent.core.cache.get_cached_actions",
        new_callable=AsyncMock,
    ) as m:
        m.return_value = [mock_action]
        with patch("builtins.isinstance", mock_isinstance):
            await ensure_ingestion_config_for_agent("agent_123")
    assert get_pageindex_node_summary() is True


@pytest.mark.asyncio
async def test_do_assimilate_with_if_add_node_summary(
    pageindex_temp_db, sample_markdown
):
    """_do_assimilate passes if_add_node_summary to assimilate_document."""
    content = sample_markdown.encode("utf-8")
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
    from jvspatial.core.context import scoped_default_context_async

    async with scoped_default_context_async(ctx):
        tree = await _graph_to_tree(root, max_summary_chars=50)
        assert len(tree) >= 1
        max_len = _max_summary_len_in_tree(tree)
        assert max_len <= 51

        tree_long = await _graph_to_tree(root, max_summary_chars=1000)
        max_len_long = _max_summary_len_in_tree(tree_long)
        assert max_len_long <= 1001


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
    tree[1][
        "text"
    ] = "ISO 9001 and related standards are cited in this clause.\n\nSecond paragraph here."
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


def test_strip_page_markers_and_annotate_structure_pages():
    """Page-marker lines are stripped; structure nodes get PDF-like page fields."""
    from jvagent.action.pageindex.markdown_pages import (
        annotate_markdown_structure_pages,
        strip_page_markers_and_build_line_page_map,
    )

    raw = "# Intro\n\nLine\n\n--- [ Page 2 ] ---\n\n## Next\n\nTail\n"
    cleaned, line_map = strip_page_markers_and_build_line_page_map(raw)
    assert "--- [ Page" not in cleaned
    assert line_map
    num_lines = cleaned.count("\n") + (1 if cleaned else 0)
    structure = [
        {
            "title": "Intro",
            "line_num": 1,
            "nodes": [
                {
                    "title": "Next",
                    "line_num": 5,
                    "nodes": [],
                }
            ],
        }
    ]
    annotate_markdown_structure_pages(structure, line_map, num_lines)
    intro = structure[0]
    assert intro["physical_index"] == 1
    assert intro["start_index"] == 1
    assert intro["end_index"] == 1
    nxt = intro["nodes"][0]
    assert nxt["physical_index"] == 2
    assert nxt["start_index"] == 2
    assert nxt["end_index"] == 2


@pytest.mark.asyncio
async def test_assimilate_paged_markdown_sets_chunk_pages(pageindex_temp_db):
    """Markdown with ``--- [ Page N ] ---`` persists physical_index on chunks."""
    body = "# Intro\n\nHello.\n\n--- [ Page 2 ] ---\n\n## Section B\n\nMore.\n"
    await assimilate_document(
        body,
        doc_name="paged_assim",
        if_add_node_summary="no",
        collection_name="col_paged",
    )
    out = await list_document_chunks("paged_assim", collection_name="col_paged")
    by_title = {c["title"]: c for c in out["chunks"]}
    assert by_title["Intro"]["physical_index"] == 1
    assert by_title["Section B"]["physical_index"] == 2


@pytest.mark.asyncio
async def test_assimilate_markdown_no_atx_headings_still_indexes(pageindex_temp_db):
    """Docling-like export: page markers + tables + prose without # headings must persist."""
    body = (
        "\n--- [ Page 1 ] ---\n\n"
        "| Foreword | ...1 |\n"
        "|----------|------|\n\n"
        "--- [ Page 3 ] ---\n\n"
        "Foreword\n\n"
        "Body paragraph here.\n"
    )
    await assimilate_document(
        body,
        doc_name="no_headers_doc",
        if_add_node_summary="no",
        collection_name="col_nh",
    )
    out = await list_document_chunks("no_headers_doc", collection_name="col_nh")
    assert out["total"] >= 1
    titles = {c["title"] for c in out["chunks"]}
    assert "Document" in titles
    doc_chunk = next(c for c in out["chunks"] if c["title"] == "Document")
    assert doc_chunk.get("physical_index") == 1
    assert doc_chunk.get("end_index") == 3


def test_docling_convert_requires_installed_package(tmp_path):
    """Without a file, or missing docling, conversion fails predictably."""
    from jvagent.action.pageindex import docling_convert

    with pytest.raises(FileNotFoundError):
        docling_convert.convert_document_to_markdown_sync(tmp_path / "missing.pdf")

    try:
        import docling  # noqa: F401
    except ImportError:
        pytest.skip("docling not installed")
    # Minimal PDF bytes (empty single page) — may still fail in some envs; skip on error
    pdf_path = tmp_path / "one.pdf"
    try:
        from pypdf import PdfWriter

        w = PdfWriter()
        w.add_blank_page(width=72, height=72)
        with open(pdf_path, "wb") as f:
            w.write(f)
    except Exception:
        pytest.skip("could not write minimal PDF")

    try:
        md = docling_convert.convert_document_to_markdown_sync(pdf_path, ocr=False)
    except Exception as e:
        pytest.skip(f"docling convert failed in this environment: {e}")
    assert isinstance(md, str)
    assert len(md) >= 0


def test_resolve_effective_jvforge_base_tri_state():
    assert resolve_effective_jvforge_base("", use_jvforge=None) == ""
    assert resolve_effective_jvforge_base("https://f", use_jvforge=None) == "https://f"
    assert resolve_effective_jvforge_base("https://f", use_jvforge=False) == ""
    assert resolve_effective_jvforge_base("https://f", use_jvforge=True) == "https://f"


def test_resolve_effective_jvforge_base_requires_url_when_yes():
    with pytest.raises(ValidationError):
        resolve_effective_jvforge_base("", use_jvforge=True)


@pytest.mark.asyncio
async def test_get_documents_queue_empty_without_jvforge():
    """Native-only installs have no remote queue; endpoint returns empty lists."""
    with patch(
        "jvagent.action.pageindex.endpoints.get_jvagent_jvforge_base_url",
        return_value=None,
    ):
        out = await get_documents_queue_endpoint("any-agent-id")
    assert out == {"jobs": [], "total": 0}


@pytest.mark.asyncio
async def test_ensure_jvforge_llm_webhook_skipped_when_jvforge_url_unset():
    """Do not call get_webhook_url when JVAGENT_JVFORGE_BASE_URL is unset."""
    action = object.__new__(PageIndexAction)
    with patch.object(
        PageIndexAction, "get_webhook_url", new_callable=AsyncMock
    ) as mock_wh:
        with patch(
            "jvagent.action.pageindex.pageindex_action.pageindex_action.get_jvagent_jvforge_base_url",
            return_value=None,
        ):
            await PageIndexAction._ensure_jvforge_llm_webhook_if_configured(action)
        mock_wh.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_jvforge_llm_webhook_calls_when_jvforge_url_set():
    """Provision webhook when jvforge base URL is configured."""
    action = object.__new__(PageIndexAction)
    with patch.object(
        PageIndexAction, "get_webhook_url", new_callable=AsyncMock
    ) as mock_wh:
        with patch(
            "jvagent.action.pageindex.pageindex_action.pageindex_action.get_jvagent_jvforge_base_url",
            return_value="https://forge.example",
        ):
            await PageIndexAction._ensure_jvforge_llm_webhook_if_configured(action)
        mock_wh.assert_awaited_once()


@pytest.mark.asyncio
async def test_llm_bridge_pins_temperature_zero():
    """Bridged LLM calls must pass temperature=0 to preserve PageIndex determinism.

    Regression: prior bridge implementation called ``query_sync(prompt)`` with
    no temperature kwarg, falling through to LanguageModelAction.temperature
    (default 0.7). PageIndex's TOC detection / JSON tree-search depend on
    deterministic single-shot output.
    """
    from jvagent.action.pageindex import llm_bridge

    captured: dict = {}

    class _FakeResult:
        async def get_response(self) -> str:
            return "ok"

    class _FakeAction:
        async def query_sync(self, prompt, **kwargs):
            captured["prompt"] = prompt
            captured["kwargs"] = kwargs
            return _FakeResult()

    llm_bridge.set_pageindex_model_action(_FakeAction())
    try:
        out = await llm_bridge.llm_acompletion("gpt-4o-mini", "hello")
    finally:
        llm_bridge.set_pageindex_model_action(None)

    assert out == "ok"
    assert captured["kwargs"].get("temperature") == 0


def _snapshot_cv():
    from jvspatial.core import context as _ctx

    return _ctx._default_context_var.get()


def _restore_cv(saved):
    from jvspatial.core import context as _ctx

    if saved is None:
        _ctx.clear_default_context()
    else:
        _ctx._default_context_var.set(saved)


def test_clear_default_context_resets_per_task_slot():
    """``clear_default_context`` must zero the per-task ContextVar slot.

    Regression: previously the only restore path was ``set_default_context``
    which required a GraphContext, so callers that had captured ``prev=None``
    skipped restoration and leaked the swap. ``clear_default_context``
    closes that gap by accepting "no prior" cleanly.
    """
    from jvspatial.core.context import (
        GraphContext,
        _default_context_var,
        clear_default_context,
        set_default_context,
    )

    saved = _snapshot_cv()
    try:
        sentinel = GraphContext()
        set_default_context(sentinel)
        assert _default_context_var.get() is sentinel
        clear_default_context()
        assert _default_context_var.get() is None
    finally:
        _restore_cv(saved)


def test_set_default_context_isolated_per_task():
    """ContextVar refactor: concurrent tasks must not see each other's swaps."""
    import asyncio

    from jvspatial.core.context import (
        GraphContext,
        _default_context_var,
        set_default_context,
    )

    ctx_a = GraphContext()
    ctx_b = GraphContext()
    seen: dict = {}

    async def task_a():
        set_default_context(ctx_a)
        await asyncio.sleep(0.01)
        seen["a"] = _default_context_var.get()

    async def task_b():
        set_default_context(ctx_b)
        await asyncio.sleep(0.01)
        seen["b"] = _default_context_var.get()

    async def runner():
        await asyncio.gather(task_a(), task_b())

    saved = _snapshot_cv()
    try:
        asyncio.run(runner())
        assert seen["a"] is ctx_a
        assert seen["b"] is ctx_b
    finally:
        _restore_cv(saved)


def test_bm25_idf_uses_corpus_df_not_filtered_count():
    """Regression: passing ``term_df_map`` keeps IDF stable even when the
    caller filtered the posting list before scoring."""
    from jvagent.action.pageindex.ranking import bm25_score

    postings_unfiltered = [
        {"node_id": "n1", "doc_name": "d1", "tf": 1, "dl": 100},
        {"node_id": "n2", "doc_name": "d2", "tf": 1, "dl": 100},
        {"node_id": "n3", "doc_name": "d3", "tf": 1, "dl": 100},
    ]
    # Filter down to one posting (simulating allowed_doc_names).
    filtered = postings_unfiltered[:1]

    # Without term_df_map: IDF derives df from filtered (1) -> very high score.
    no_map = bm25_score(["foo"], {"foo": filtered}, total_nodes=10, avg_doc_len=100.0)
    # With term_df_map: IDF derives df from full corpus (3) -> lower score.
    with_map = bm25_score(
        ["foo"],
        {"foo": filtered},
        total_nodes=10,
        avg_doc_len=100.0,
        term_df_map={"foo": 3},
    )

    assert no_map and with_map
    assert no_map[0]["score"] > with_map[0]["score"]


def test_jvforge_response_missing_roots_raises():
    """Regression: jvforge response with no ``roots`` must NOT silently
    yield a no-op success."""
    import asyncio

    from jvspatial.api.exceptions import ValidationError

    from jvagent.action.pageindex.jvforge_assimilate import assimilate_via_jvforge

    class _FakeResp:
        status_code = 200
        text = ""

        def json(self):
            return {"meta": {"ok": True}}  # no 'roots' key

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, *a, **kw):
            return _FakeResp()

    with patch("httpx.AsyncClient", _FakeClient):
        with pytest.raises(ValidationError) as exc:
            asyncio.run(
                assimilate_via_jvforge(
                    base_url="https://forge.example",
                    agent_id="a1",
                    doc_name="missing.pdf",
                    model=None,
                    if_add_node_summary="no",
                    collection_name="c1",
                    metadata=None,
                    doc_description=None,
                    doc_url=None,
                    convert_to_markdown=False,
                    ocr=False,
                    docling_ocr_engine=None,
                    normalize_bold_headings=False,
                    llm_webhook_url="https://example/webhook",
                    filename="x.pdf",
                    content=b"%PDF-1.0",
                    file_url=None,
                )
            )
    assert "roots" in str(exc.value).lower()


def test_ssrf_guard_rejects_private_addresses():
    """Regression: _ssrf_guard_url must reject loopback/private IPs."""
    from jvspatial.api.exceptions import ValidationError

    from jvagent.action.pageindex.endpoints import _ssrf_guard_url

    for url in (
        "http://127.0.0.1/x",
        "http://localhost/x",
        "http://10.0.0.1/x",
        "http://192.168.1.5/x",
        "http://169.254.169.254/latest/meta-data/",
        "file:///etc/passwd",
        "ftp://example.com/x",
    ):
        with pytest.raises(ValidationError):
            _ssrf_guard_url(url)


def test_ssrf_guard_allows_public_https():
    """Public hostnames should pass; this must not break legitimate fetches."""
    from jvagent.action.pageindex.endpoints import _ssrf_guard_url

    # google.com / cloudflare.com etc. depend on DNS — use literal public IP.
    _ssrf_guard_url("https://1.1.1.1/")


# ---------------------------------------------------------------------------
# resolved_metadata_filter — AccessControlAction integration
# ---------------------------------------------------------------------------


def _make_visitor(user_id: str = "user-1", session_id: str = "sess-1"):
    """Stub InteractWalker exposing the attrs resolved_metadata_filter reads."""
    from types import SimpleNamespace

    return SimpleNamespace(user_id=user_id, session_id=session_id)


def _make_pageindex_action(metadata_filter=None, access_control=False):
    action = object.__new__(PageIndexAction)
    object.__setattr__(action, "metadata_filter", metadata_filter)
    object.__setattr__(action, "access_control", access_control)
    return action


class _StubACA:
    """Minimal stand-in for AccessControlAction used by resolved_metadata_filter."""

    def __init__(self, user_groups):
        self.user_groups = user_groups

    def _resolve_user_groups(self, action_label):
        if action_label in self.user_groups:
            groups = self.user_groups[action_label]
            default_groups = self.user_groups.get("default", {})
            merged = dict(default_groups)
            merged.update(groups)
            return merged
        return dict(self.user_groups.get("default", {}))


@pytest.mark.asyncio
async def test_resolved_metadata_filter_access_control_false_no_filter():
    """access_control=False with no metadata filter → returns None (no filtering)."""
    action = _make_pageindex_action(metadata_filter=None, access_control=False)
    aca = _StubACA(user_groups={"PageIndexAction": {"private": ["other-user"]}})

    with patch.object(
        PageIndexAction, "get_action", new_callable=AsyncMock, return_value=aca
    ):
        result = await PageIndexAction.resolved_metadata_filter(
            action, _make_visitor("user-1"), None, access_control=False
        )

    assert result is None


@pytest.mark.asyncio
async def test_resolved_metadata_filter_access_control_false_preserves_filter():
    """access_control=False with metadata_filter passed → returns filter unchanged."""
    action = _make_pageindex_action(
        metadata_filter={"topic": "finance"}, access_control=False
    )
    aca = _StubACA(user_groups={"PageIndexAction": {"private": ["other-user"]}})

    with patch.object(
        PageIndexAction, "get_action", new_callable=AsyncMock, return_value=aca
    ):
        result = await PageIndexAction.resolved_metadata_filter(
            action, _make_visitor("user-1"), {"topic": "finance"}, access_control=False
        )

    assert result == {"topic": "finance"}


@pytest.mark.asyncio
async def test_resolved_metadata_filter_access_control_true_ac_absent(caplog):
    """access_control=True with AccessControlAction not registered → access=["public"]."""
    import logging

    action = _make_pageindex_action(metadata_filter=None, access_control=True)

    with patch.object(
        PageIndexAction, "get_action", new_callable=AsyncMock, return_value=None
    ):
        with caplog.at_level(
            logging.DEBUG,
            logger="jvagent.action.pageindex.pageindex_action.pageindex_action",
        ):
            result = await PageIndexAction.resolved_metadata_filter(
                action, _make_visitor(), None, access_control=True
            )

    assert result == {"access": ["public"]}


@pytest.mark.asyncio
async def test_resolved_metadata_filter_access_control_true_ac_empty_user_groups():
    """access_control=True with empty user_groups → access=["public"] only (no metadata_filter merge)."""
    action = _make_pageindex_action(
        metadata_filter={"topic": "faq"}, access_control=True
    )
    aca = _StubACA(user_groups={})

    with patch.object(
        PageIndexAction, "get_action", new_callable=AsyncMock, return_value=aca
    ):
        result = await PageIndexAction.resolved_metadata_filter(
            action, _make_visitor(), None, access_control=True
        )

    assert result == {"access": ["public"]}


@pytest.mark.asyncio
async def test_resolved_metadata_filter_access_control_true_no_pageindex_scope():
    """access_control=True with no PageIndexAction groups → access=["public"] only (no metadata_filter merge)."""
    action = _make_pageindex_action(
        metadata_filter={"topic": "finance"}, access_control=True
    )
    aca = _StubACA(user_groups={"SomeOtherAction": {"admins": ["user-1"]}})

    with patch.object(
        PageIndexAction, "get_action", new_callable=AsyncMock, return_value=aca
    ):
        result = await PageIndexAction.resolved_metadata_filter(
            action, _make_visitor(), None, access_control=True
        )

    assert result == {"access": ["public"]}


@pytest.mark.asyncio
async def test_resolved_metadata_filter_access_control_true_visitor_matches():
    """access_control=True, visitor matches group → access includes public + matched group (no metadata_filter merge)."""
    action = _make_pageindex_action(
        metadata_filter={"topic": "faq"}, access_control=True
    )
    aca = _StubACA(
        user_groups={
            "PageIndexAction": {
                "admins": ["user-1"],
                "guests": ["other-user"],
            }
        }
    )

    with patch.object(
        PageIndexAction, "get_action", new_callable=AsyncMock, return_value=aca
    ):
        result = await PageIndexAction.resolved_metadata_filter(
            action, _make_visitor("user-1"), None, access_control=True
        )

    assert result == {"access": ["public", "admins"]}


@pytest.mark.asyncio
async def test_resolved_metadata_filter_access_control_true_visitor_unmatched():
    """access_control=True, visitor matches no group → access=["public"] only (no metadata_filter merge)."""
    action = _make_pageindex_action(
        metadata_filter={"topic": "faq"}, access_control=True
    )
    aca = _StubACA(
        user_groups={
            "PageIndexAction": {
                "admins": ["other-user"],
            }
        }
    )

    with patch.object(
        PageIndexAction, "get_action", new_callable=AsyncMock, return_value=aca
    ):
        result = await PageIndexAction.resolved_metadata_filter(
            action, _make_visitor("user-1"), None, access_control=True
        )

    assert result == {"access": ["public"]}


@pytest.mark.asyncio
async def test_resolved_metadata_filter_access_control_true_no_filter():
    """access_control=True with no metadata_filter → access=["public"]."""
    action = _make_pageindex_action(metadata_filter=None, access_control=True)
    aca = _StubACA(user_groups={"PageIndexAction": {"private": ["other-user"]}})

    with patch.object(
        PageIndexAction, "get_action", new_callable=AsyncMock, return_value=aca
    ):
        result = await PageIndexAction.resolved_metadata_filter(
            action, _make_visitor("user-1"), None, access_control=True
        )

    assert result == {"access": ["public"]}


@pytest.mark.asyncio
async def test_resolved_metadata_filter_access_control_true_session_id_match():
    """access_control=True, visitor matches via session_id → access includes public + group."""
    action = _make_pageindex_action(metadata_filter=None, access_control=True)
    aca = _StubACA(
        user_groups={
            "PageIndexAction": {
                "private": ["sess-special"],
            }
        }
    )

    with patch.object(
        PageIndexAction, "get_action", new_callable=AsyncMock, return_value=aca
    ):
        result = await PageIndexAction.resolved_metadata_filter(
            action,
            _make_visitor(user_id="user-1", session_id="sess-special"),
            None,
            access_control=True,
        )

    assert result == {"access": ["public", "private"]}


@pytest.mark.asyncio
async def test_resolved_metadata_filter_access_control_true_multiple_groups():
    """access_control=True, visitor in multiple groups → access includes public + all matched."""
    action = _make_pageindex_action(metadata_filter=None, access_control=True)
    aca = _StubACA(
        user_groups={
            "PageIndexAction": {
                "admins": ["user-1"],
                "editors": ["user-1"],
                "viewers": ["other-user"],
            }
        }
    )

    with patch.object(
        PageIndexAction, "get_action", new_callable=AsyncMock, return_value=aca
    ):
        result = await PageIndexAction.resolved_metadata_filter(
            action, _make_visitor("user-1"), None, access_control=True
        )

    assert result["access"] == ["public", "admins", "editors"]


@pytest.mark.asyncio
async def test_resolved_metadata_filter_access_control_true_preserves_existing_access():
    """access_control=True with existing access key in metadata_filter → overwritten with group-based access (no merge)."""
    action = _make_pageindex_action(
        metadata_filter={"topic": "faq", "access": "public"}, access_control=True
    )
    aca = _StubACA(
        user_groups={
            "PageIndexAction": {
                "private": ["user-1"],
            }
        }
    )

    with patch.object(
        PageIndexAction, "get_action", new_callable=AsyncMock, return_value=aca
    ):
        result = await PageIndexAction.resolved_metadata_filter(
            action, _make_visitor("user-1"), None, access_control=True
        )

    assert result == {"access": ["public", "private"]}


@pytest.mark.asyncio
async def test_resolved_metadata_filter_access_control_true_visitor_none():
    """access_control=True with visitor=None → returns access=['public'] (no bypass)."""
    action = _make_pageindex_action(metadata_filter=None, access_control=True)
    aca = _StubACA(user_groups={"PageIndexAction": {"private": ["user-1"]}})

    with patch.object(
        PageIndexAction, "get_action", new_callable=AsyncMock, return_value=aca
    ):
        result = await PageIndexAction.resolved_metadata_filter(
            action, None, None, access_control=True
        )

    assert result == {"access": ["public"]}


@pytest.mark.asyncio
async def test_resolved_metadata_filter_access_control_true_visitor_no_identity():
    """access_control=True with visitor having None user_id and session_id → access=['public']."""
    action = _make_pageindex_action(metadata_filter=None, access_control=True)
    aca = _StubACA(user_groups={"PageIndexAction": {"private": ["user-1"]}})

    with patch.object(
        PageIndexAction, "get_action", new_callable=AsyncMock, return_value=aca
    ):
        result = await PageIndexAction.resolved_metadata_filter(
            action,
            _make_visitor(user_id=None, session_id=None),
            None,
            access_control=True,
        )

    assert result == {"access": ["public"]}


@pytest.mark.asyncio
async def test_resolved_metadata_filter_access_control_true_default_groups_merged():
    """access_control=True merges default groups with PageIndexAction groups."""
    action = _make_pageindex_action(metadata_filter=None, access_control=True)
    aca = _StubACA(
        user_groups={
            "default": {"public": [], "private": []},
            "PageIndexAction": {"private": ["user-1"]},
        }
    )

    with patch.object(
        PageIndexAction, "get_action", new_callable=AsyncMock, return_value=aca
    ):
        result = await PageIndexAction.resolved_metadata_filter(
            action, _make_visitor("user-1"), None, access_control=True
        )

    assert result == {"access": ["public", "private"]}


@pytest.mark.asyncio
async def test_resolved_metadata_filter_access_control_false_no_merge_with_self():
    """access_control=False does not fall back to self.metadata_filter."""
    action = _make_pageindex_action(
        metadata_filter={"topic": "finance"}, access_control=False
    )
    aca = _StubACA(user_groups={"PageIndexAction": {"private": ["user-1"]}})

    with patch.object(
        PageIndexAction, "get_action", new_callable=AsyncMock, return_value=aca
    ):
        # Pass metadata_filter=None explicitly; self.metadata_filter should NOT be used
        result = await PageIndexAction.resolved_metadata_filter(
            action, _make_visitor("user-1"), None, access_control=False
        )

    assert result is None


@pytest.mark.asyncio
async def test_resolved_metadata_filter_access_control_true_no_aca_uses_enabled_only_false():
    """access_control=True calls get_action with enabled_only=False."""
    action = _make_pageindex_action(metadata_filter=None, access_control=True)

    with patch.object(
        PageIndexAction, "get_action", new_callable=AsyncMock, return_value=None
    ) as mock_get:
        result = await PageIndexAction.resolved_metadata_filter(
            action, _make_visitor("user-1"), None, access_control=True
        )

    assert result == {"access": ["public"]}
    mock_get.assert_awaited_once_with("AccessControlAction", enabled_only=False)


def test_root_matches_metadata_access_public_or_member():
    """Untagged docs are public; tagged docs require group intersection."""
    from types import SimpleNamespace

    public = SimpleNamespace(metadata=None)
    public_empty = SimpleNamespace(metadata={"access": []})
    admins = SimpleNamespace(metadata={"access": ["admins"]})
    guests = SimpleNamespace(metadata={"access": ["guests"]})

    # Unmatched visitor (access=[]) sees public docs only.
    assert _root_matches_metadata(public, {"access": []}) is True
    assert _root_matches_metadata(public_empty, {"access": []}) is True
    assert _root_matches_metadata(admins, {"access": []}) is False

    # admins member sees public + admins-tagged, not guests-tagged.
    assert _root_matches_metadata(public, {"access": ["admins"]}) is True
    assert _root_matches_metadata(admins, {"access": ["admins"]}) is True
    assert _root_matches_metadata(guests, {"access": ["admins"]}) is False

    # Access control does not relax other metadata constraints.
    finance_admins = SimpleNamespace(
        metadata={"topic": "finance", "access": ["admins"]}
    )
    assert (
        _root_matches_metadata(
            finance_admins, {"topic": "finance", "access": ["admins"]}
        )
        is True
    )
    assert (
        _root_matches_metadata(finance_admins, {"topic": "legal", "access": ["admins"]})
        is False
    )


def test_build_metadata_query_access_public_or_member():
    """access filter expands to (public OR member); empty groups = public only."""
    field = "context.metadata.access"

    q = _build_metadata_query({"access": ["admins"]})
    assert {field: {"$exists": False}} in q["$or"]
    assert {field: None} in q["$or"]
    assert {field: []} in q["$or"]
    assert {field: {"$in": ["admins"]}} in q["$or"]

    # Empty allowed groups → public only, no membership clause.
    q_empty = _build_metadata_query({"access": []})
    assert {field: {"$exists": False}} in q_empty["$or"]
    assert all(
        not (isinstance(clause.get(field), dict) and "$in" in clause[field])
        for clause in q_empty["$or"]
    )

    # Combined with a non-access key → wrapped in $and.
    q_combined = _build_metadata_query({"topic": "finance", "access": ["admins"]})
    assert "$and" in q_combined
    assert {"context.metadata.topic": "finance"} in q_combined["$and"]


@pytest.mark.asyncio
async def test_search_access_public_visible_to_unmatched(
    pageindex_temp_db, sample_markdown
):
    """End-to-end: access=[] returns untagged (public) docs, excludes tagged ones."""
    await assimilate_document(
        sample_markdown,
        doc_name="doc_public",
        if_add_node_summary="no",
        collection_name="col_acl",
    )
    await assimilate_document(
        sample_markdown,
        doc_name="doc_restricted",
        if_add_node_summary="no",
        collection_name="col_acl",
        metadata={"access": "admins"},
    )

    results = await search_documents(
        query="content",
        strategy="direct",
        limit=20,
        collection_name="col_acl",
        metadata_filter={"access": []},
    )
    assert {r.get("doc_name") for r in results} == {"doc_public"}


@pytest.mark.asyncio
async def test_search_access_member_sees_public_and_own(
    pageindex_temp_db, sample_markdown
):
    """End-to-end: a member sees public docs plus docs tagged with their group."""
    await assimilate_document(
        sample_markdown,
        doc_name="doc_public",
        if_add_node_summary="no",
        collection_name="col_acl2",
    )
    await assimilate_document(
        sample_markdown,
        doc_name="doc_admins",
        if_add_node_summary="no",
        collection_name="col_acl2",
        metadata={"access": "admins"},
    )
    await assimilate_document(
        sample_markdown,
        doc_name="doc_guests",
        if_add_node_summary="no",
        collection_name="col_acl2",
        metadata={"access": "guests"},
    )

    results = await search_documents(
        query="content",
        strategy="direct",
        limit=20,
        collection_name="col_acl2",
        metadata_filter={"access": ["admins"]},
    )
    assert {r.get("doc_name") for r in results} == {"doc_public", "doc_admins"}


@pytest.mark.asyncio
async def test_search_access_unmatched_public_baseline(
    pageindex_temp_db, sample_markdown
):
    """End-to-end: public baseline returns untagged + public-tagged, excludes private."""
    await assimilate_document(
        sample_markdown,
        doc_name="doc_untagged",
        if_add_node_summary="no",
        collection_name="col_acl3",
    )
    await assimilate_document(
        sample_markdown,
        doc_name="doc_public_tagged",
        if_add_node_summary="no",
        collection_name="col_acl3",
        metadata={"access": "public"},
    )
    await assimilate_document(
        sample_markdown,
        doc_name="doc_private",
        if_add_node_summary="no",
        collection_name="col_acl3",
        metadata={"access": "private"},
    )

    results = await search_documents(
        query="content",
        strategy="direct",
        limit=20,
        collection_name="col_acl3",
        metadata_filter={"access": "public"},
    )
    assert {r.get("doc_name") for r in results} == {
        "doc_untagged",
        "doc_public_tagged",
    }


@pytest.mark.asyncio
async def test_search_access_public_list_includes_public_and_untagged(
    pageindex_temp_db, sample_markdown
):
    """End-to-end: access=['public'] returns untagged + public-tagged, excludes private."""
    await assimilate_document(
        sample_markdown,
        doc_name="doc_untagged",
        if_add_node_summary="no",
        collection_name="col_acl4",
    )
    await assimilate_document(
        sample_markdown,
        doc_name="doc_public_tagged",
        if_add_node_summary="no",
        collection_name="col_acl4",
        metadata={"access": "public"},
    )
    await assimilate_document(
        sample_markdown,
        doc_name="doc_private",
        if_add_node_summary="no",
        collection_name="col_acl4",
        metadata={"access": "private"},
    )

    results = await search_documents(
        query="content",
        strategy="direct",
        limit=20,
        collection_name="col_acl4",
        metadata_filter={"access": ["public"]},
    )
    assert {r.get("doc_name") for r in results} == {
        "doc_untagged",
        "doc_public_tagged",
    }


@pytest.mark.asyncio
async def test_search_access_public_plus_private_includes_both(
    pageindex_temp_db, sample_markdown
):
    """End-to-end: access=['public','private'] returns all docs including private-tagged."""
    await assimilate_document(
        sample_markdown,
        doc_name="doc_untagged",
        if_add_node_summary="no",
        collection_name="col_acl5",
    )
    await assimilate_document(
        sample_markdown,
        doc_name="doc_public_tagged",
        if_add_node_summary="no",
        collection_name="col_acl5",
        metadata={"access": "public"},
    )
    await assimilate_document(
        sample_markdown,
        doc_name="doc_private",
        if_add_node_summary="no",
        collection_name="col_acl5",
        metadata={"access": "private"},
    )

    results = await search_documents(
        query="content",
        strategy="direct",
        limit=20,
        collection_name="col_acl5",
        metadata_filter={"access": ["public", "private"]},
    )
    assert {r.get("doc_name") for r in results} == {
        "doc_untagged",
        "doc_public_tagged",
        "doc_private",
    }
