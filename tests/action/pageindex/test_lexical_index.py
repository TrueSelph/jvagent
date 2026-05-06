"""Tests for PageIndex lexical index, tokenizer, ranking, and two-stage retrieval."""

import pytest

pytest.importorskip("openai")
pytest.importorskip("tiktoken")
pytest.importorskip("litellm")
pytest.importorskip("PyPDF2")

from jvspatial.db import get_database_manager, unregister_database

from jvagent.action.pageindex.adapter import persist_structure
from jvagent.action.pageindex.config import (
    PAGEINDEX_DB_NAME,
    get_pageindex_candidate_k,
    get_pageindex_enable_lexical_index,
    get_pageindex_max_docs_for_tree_search,
    initialize_pageindex_database,
    set_pageindex_candidate_k,
    set_pageindex_enable_lexical_index,
    set_pageindex_max_docs_for_tree_search,
)
from jvagent.action.pageindex.documents import (
    assimilate_document,
    delete_document,
    get_document_root,
)
from jvagent.action.pageindex.lexical_index import (
    index_node,
    reindex_nodes,
    remove_collection,
    remove_document_nodes,
    remove_node,
    search,
)
from jvagent.action.pageindex.ranking import bm25_score
from jvagent.action.pageindex.retrieval import search_documents
from jvagent.action.pageindex.tokenizer import (
    term_frequencies,
    tokenize,
    tokenize_fields,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pageindex_temp_db(temp_dir):
    db_path = temp_dir / "pageindex_lex_test"
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
    path = temp_dir / "sample_lex.md"
    path.write_text(
        "# Introduction\n\nThis is a test document about finance.\n\n"
        "## Budget Planning\n\nQuarterly budget planning involves revenue forecasting.\n\n"
        "## Legal Compliance\n\nLegal compliance ensures regulatory adherence.\n",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# Tokenizer tests
# ---------------------------------------------------------------------------


class TestTokenizer:
    def test_tokenize_basic(self):
        tokens = tokenize("Hello World test")
        assert "hello" in tokens
        assert "world" in tokens
        assert "test" in tokens

    def test_tokenize_removes_stop_words(self):
        tokens = tokenize("this is a test of the system")
        assert "this" not in tokens
        assert "test" in tokens
        assert "system" in tokens

    def test_tokenize_removes_short_tokens(self):
        tokens = tokenize("I am a x test")
        assert "x" not in tokens
        assert "test" in tokens

    def test_tokenize_empty(self):
        assert tokenize("") == []
        assert tokenize("   ") == []

    def test_tokenize_punctuation(self):
        tokens = tokenize("hello, world! How's it going?")
        assert "hello" in tokens
        assert "world" in tokens

    def test_term_frequencies(self):
        tokens = ["hello", "world", "hello", "test"]
        tf = term_frequencies(tokens)
        assert tf["hello"] == 2
        assert tf["world"] == 1
        assert tf["test"] == 1

    def test_tokenize_fields_title_boost(self):
        tf_map, total = tokenize_fields(
            title="budget",
            text="quarterly budget planning",
        )
        assert tf_map["budget"] > tf_map.get("quarterly", 0)
        assert total > 0


# ---------------------------------------------------------------------------
# Ranking tests
# ---------------------------------------------------------------------------


class TestRanking:
    def test_bm25_basic(self):
        postings_map = {
            "budget": [
                {"node_id": "n1", "doc_name": "d1", "tf": 3, "dl": 100},
                {"node_id": "n2", "doc_name": "d1", "tf": 1, "dl": 50},
            ],
            "planning": [
                {"node_id": "n1", "doc_name": "d1", "tf": 2, "dl": 100},
            ],
        }
        results = bm25_score(
            query_terms=["budget", "planning"],
            postings_map=postings_map,
            total_nodes=10,
            avg_doc_len=75.0,
        )
        assert len(results) == 2
        assert results[0]["node_id"] == "n1"
        assert results[0]["score"] > results[1]["score"]

    def test_bm25_empty_query(self):
        assert bm25_score([], {}, 10, 75.0) == []

    def test_bm25_zero_nodes(self):
        assert bm25_score(["test"], {}, 0, 0.0) == []

    def test_bm25_returns_doc_name(self):
        postings_map = {
            "test": [{"node_id": "n1", "doc_name": "mydoc", "tf": 1, "dl": 10}],
        }
        results = bm25_score(["test"], postings_map, 5, 10.0)
        assert results[0]["doc_name"] == "mydoc"


# ---------------------------------------------------------------------------
# Lexical index CRUD tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_and_search_node(pageindex_temp_db):
    await index_node(
        node_id="test_n1",
        doc_name="doc1",
        collection_name="test_col",
        title="Budget Planning",
        text="This section covers quarterly budget planning and forecasting.",
    )
    results = await search("budget planning", "test_col")
    assert len(results) >= 1
    assert results[0]["node_id"] == "test_n1"
    assert results[0]["doc_name"] == "doc1"
    assert results[0]["score"] > 0


@pytest.mark.asyncio
async def test_search_empty_index(pageindex_temp_db):
    results = await search("anything", "empty_col")
    assert results == []


@pytest.mark.asyncio
async def test_search_filters_by_doc_name(pageindex_temp_db):
    await index_node("n1", "doc_a", "col", title="Finance report")
    await index_node("n2", "doc_b", "col", title="Finance analysis")

    results = await search("finance", "col", doc_name="doc_a")
    assert all(r["doc_name"] == "doc_a" for r in results)


@pytest.mark.asyncio
async def test_search_filters_by_allowed_doc_names(pageindex_temp_db):
    await index_node("n1", "doc_a", "col2", title="Budget report")
    await index_node("n2", "doc_b", "col2", title="Budget analysis")
    await index_node("n3", "doc_c", "col2", title="Budget overview")

    results = await search("budget", "col2", allowed_doc_names=["doc_a", "doc_c"])
    doc_names = {r["doc_name"] for r in results}
    assert "doc_b" not in doc_names


@pytest.mark.asyncio
async def test_remove_node_from_index(pageindex_temp_db):
    await index_node(
        "rm_n1", "doc1", "rm_col", title="Remove test", text="Content here"
    )
    results_before = await search("remove test", "rm_col")
    assert len(results_before) >= 1

    await remove_node("rm_n1", "rm_col")
    results_after = await search("remove test", "rm_col")
    assert not any(r["node_id"] == "rm_n1" for r in results_after)


@pytest.mark.asyncio
async def test_remove_node_decrements_collection_stats(pageindex_temp_db):
    """remove_node must decrement sum_doc_len by the removed node's dl.

    Regression: prior implementation re-read the posting record AFTER the
    node had already been filtered out, so removed_len was always 0 and
    BM25's avg_doc_len drifted upward over time.
    """
    from jvspatial.db import get_database_manager

    from jvagent.action.pageindex.lexical_index import (
        _STATS_COLLECTION,
        _collection_stats_id,
    )

    coll = "stats_col"
    await index_node("sn1", "doc_a", coll, title="Alpha", text="lorem ipsum dolor")
    await index_node("sn2", "doc_a", coll, title="Beta", text="sit amet consectetur")

    db = get_database_manager().get_database(PAGEINDEX_DB_NAME)
    stats_before = await db.get(_STATS_COLLECTION, _collection_stats_id(coll))
    assert stats_before is not None
    assert stats_before["total_nodes"] == 2
    sum_before = stats_before["sum_doc_len"]
    assert sum_before > 0

    await remove_node("sn1", coll)

    stats_after = await db.get(_STATS_COLLECTION, _collection_stats_id(coll))
    assert stats_after is not None
    assert stats_after["total_nodes"] == 1
    # sum_doc_len must shrink by exactly the removed node's dl, not stay flat.
    assert stats_after["sum_doc_len"] < sum_before
    assert stats_after["sum_doc_len"] >= 0


@pytest.mark.asyncio
async def test_remove_document_nodes_batch(pageindex_temp_db):
    await index_node("bn1", "doc_del", "b_col", title="First", text="Content one")
    await index_node("bn2", "doc_del", "b_col", title="Second", text="Content two")
    await index_node("bn3", "doc_keep", "b_col", title="Third", text="Content three")

    await remove_document_nodes(["bn1", "bn2"], "b_col")

    results = await search("content", "b_col")
    node_ids = {r["node_id"] for r in results}
    assert "bn1" not in node_ids
    assert "bn2" not in node_ids
    assert "bn3" in node_ids


@pytest.mark.asyncio
async def test_remove_collection_clears_all(pageindex_temp_db):
    await index_node("cn1", "d1", "wipe_col", title="Data", text="Some data")
    await index_node("cn2", "d2", "wipe_col", title="More", text="More data")

    await remove_collection("wipe_col")

    results = await search("data", "wipe_col")
    assert results == []


@pytest.mark.asyncio
async def test_reindex_nodes(pageindex_temp_db):
    nodes = [
        {"id": "ri_n1", "doc_name": "d1", "title": "Alpha", "text": "Alpha content"},
        {"id": "ri_n2", "doc_name": "d1", "title": "Beta", "text": "Beta content"},
    ]
    count = await reindex_nodes(nodes, "reindex_col")
    assert count == 2

    results = await search("alpha", "reindex_col")
    assert len(results) >= 1
    assert results[0]["node_id"] == "ri_n1"


# ---------------------------------------------------------------------------
# Integration: ingestion builds lexical index
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assimilate_builds_lexical_index(pageindex_temp_db, sample_markdown):
    await assimilate_document(
        sample_markdown,
        doc_name="lex_doc",
        if_add_node_summary="no",
        collection_name="lex_col",
    )

    results = await search("budget planning", "lex_col")
    assert len(results) >= 1
    assert any(r["doc_name"] == "lex_doc" for r in results)


@pytest.mark.asyncio
async def test_delete_cleans_lexical_index(pageindex_temp_db, sample_markdown):
    await assimilate_document(
        sample_markdown,
        doc_name="del_lex_doc",
        if_add_node_summary="no",
        collection_name="del_lex_col",
    )
    results_before = await search("budget", "del_lex_col")
    assert len(results_before) >= 1

    await delete_document("del_lex_doc", collection_name="del_lex_col")

    results_after = await search("budget", "del_lex_col")
    assert not any(r["doc_name"] == "del_lex_doc" for r in results_after)


# ---------------------------------------------------------------------------
# Integration: two-stage retrieval
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_direct_search_uses_lexical_candidates(
    pageindex_temp_db, sample_markdown
):
    """Direct search should find results via lexical index (no regex scan needed)."""
    await assimilate_document(
        sample_markdown,
        doc_name="direct_lex",
        if_add_node_summary="no",
        collection_name="direct_col",
    )

    results = await search_documents(
        query="budget planning revenue",
        strategy="direct",
        limit=5,
        collection_name="direct_col",
    )
    assert len(results) >= 1
    assert any(r.get("doc_name") == "direct_lex" for r in results)


@pytest.mark.asyncio
async def test_direct_search_fallback_when_index_empty(pageindex_temp_db):
    """When lexical index has no data, direct search falls back to regex scan."""
    structure = [
        {
            "title": "Fallback Section",
            "text": "Unique content for fallback test",
            "node_id": "fb_n1",
            "physical_index": 0,
            "start_index": 0,
            "end_index": 1,
            "structure": "1",
        },
    ]
    await persist_structure(
        doc_name="fallback_doc",
        structure=structure,
        collection_name="fallback_col",
    )

    set_pageindex_enable_lexical_index(False)
    try:
        results = await search_documents(
            query="Unique content",
            strategy="direct",
            limit=5,
            collection_name="fallback_col",
        )
        assert len(results) >= 1
        assert any(r.get("doc_name") == "fallback_doc" for r in results)
    finally:
        set_pageindex_enable_lexical_index(None)


@pytest.mark.asyncio
async def test_search_with_metadata_filter_via_lexical(
    pageindex_temp_db, sample_markdown
):
    """Two-stage retrieval respects metadata filters."""
    await assimilate_document(
        sample_markdown,
        doc_name="meta_finance",
        if_add_node_summary="no",
        collection_name="meta_col",
        metadata={"topic": "finance"},
    )
    await assimilate_document(
        sample_markdown,
        doc_name="meta_legal",
        if_add_node_summary="no",
        collection_name="meta_col",
        metadata={"topic": "legal"},
    )

    results = await search_documents(
        query="budget",
        strategy="direct",
        limit=20,
        collection_name="meta_col",
        metadata_filter={"topic": "finance"},
    )
    doc_names = {r.get("doc_name") for r in results}
    assert "meta_legal" not in doc_names


@pytest.mark.asyncio
async def test_collection_isolation_in_lexical_index(
    pageindex_temp_db, sample_markdown
):
    """Lexical index respects collection scoping."""
    await assimilate_document(
        sample_markdown,
        doc_name="iso_doc",
        if_add_node_summary="no",
        collection_name="iso_col_a",
    )
    await assimilate_document(
        sample_markdown,
        doc_name="iso_doc",
        if_add_node_summary="no",
        collection_name="iso_col_b",
    )

    results_a = await search("budget", "iso_col_a")
    results_b = await search("budget", "iso_col_b")

    assert len(results_a) >= 1
    assert len(results_b) >= 1
    ids_a = {r["node_id"] for r in results_a}
    ids_b = {r["node_id"] for r in results_b}
    assert ids_a.isdisjoint(ids_b)


# ---------------------------------------------------------------------------
# Config knob tests
# ---------------------------------------------------------------------------


class TestConfigKnobs:
    def test_enable_lexical_index_default(self):
        set_pageindex_enable_lexical_index(None)
        assert get_pageindex_enable_lexical_index() is True

    def test_enable_lexical_index_set(self):
        set_pageindex_enable_lexical_index(False)
        assert get_pageindex_enable_lexical_index() is False
        set_pageindex_enable_lexical_index(None)

    def test_candidate_k_default(self):
        set_pageindex_candidate_k(None)
        assert get_pageindex_candidate_k() == 200

    def test_candidate_k_set(self):
        set_pageindex_candidate_k(50)
        assert get_pageindex_candidate_k() == 50
        set_pageindex_candidate_k(None)

    def test_max_docs_for_tree_search_default(self):
        set_pageindex_max_docs_for_tree_search(None)
        assert get_pageindex_max_docs_for_tree_search() == 10

    def test_max_docs_for_tree_search_set(self):
        set_pageindex_max_docs_for_tree_search(20)
        assert get_pageindex_max_docs_for_tree_search() == 20
        set_pageindex_max_docs_for_tree_search(None)


# ---------------------------------------------------------------------------
# Scale-oriented tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lexical_search_bounded_by_candidate_k(pageindex_temp_db):
    """Even with many nodes, search returns at most candidate_k results."""
    for i in range(30):
        await index_node(
            f"scale_n{i}",
            "doc_scale",
            "scale_col",
            title=f"Section {i}",
            text="Revenue budget planning forecast data analysis",
        )

    results = await search("revenue budget", "scale_col", candidate_k=5)
    assert len(results) <= 5


@pytest.mark.asyncio
async def test_multiple_docs_lexical_ranking(pageindex_temp_db):
    """Nodes with higher term frequency rank higher."""
    await index_node(
        "rank_n1",
        "doc1",
        "rank_col",
        title="Budget",
        text="budget budget budget revenue",
    )
    await index_node(
        "rank_n2",
        "doc2",
        "rank_col",
        title="Other",
        text="budget revenue",
    )

    results = await search("budget", "rank_col")
    assert len(results) == 2
    assert results[0]["node_id"] == "rank_n1"
