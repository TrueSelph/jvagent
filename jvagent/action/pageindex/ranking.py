"""BM25-based ranking for PageIndex lexical retrieval.

Scores candidate nodes using Okapi BM25 with field-aware term frequencies
derived from the inverted index.  No embeddings or external services required.
"""

import math
from typing import Any, Dict, List


def bm25_score(
    query_terms: List[str],
    postings_map: Dict[str, List[Dict[str, Any]]],
    total_nodes: int,
    avg_doc_len: float,
    k1: float = 1.2,
    b: float = 0.75,
    term_df_map: Dict[str, int] = None,
) -> List[Dict[str, Any]]:
    """Score nodes using Okapi BM25.

    Each posting dict must contain ``node_id``, ``doc_name``, ``tf``, ``dl``
    (document length in tokens).

    Args:
        term_df_map: Optional precomputed document-frequency per term, taken
            from the FULL corpus before any allowed-doc filter. When the
            caller filters postings by metadata/doc_name before scoring,
            ``len(postings)`` no longer reflects the corpus df and IDF would
            become filter-dependent. Pass the unfiltered df via this map to
            keep IDF stable across queries.

    Returns a list of ``{node_id, doc_name, score}`` dicts sorted by score
    descending.
    """
    if not query_terms or total_nodes == 0:
        return []

    scores: Dict[str, float] = {}
    doc_names: Dict[str, str] = {}

    safe_avg = avg_doc_len if avg_doc_len > 0 else 1.0

    for term in query_terms:
        postings = postings_map.get(term)
        if not postings:
            continue

        df = (
            term_df_map.get(term, len(postings))
            if term_df_map is not None
            else len(postings)
        )
        idf = math.log((total_nodes - df + 0.5) / (df + 0.5) + 1.0)

        for p in postings:
            nid = p["node_id"]
            tf = p["tf"]
            dl = p.get("dl", safe_avg)

            numerator = tf * (k1 + 1)
            denominator = tf + k1 * (1 - b + b * (dl / safe_avg))

            scores[nid] = scores.get(nid, 0.0) + idf * (numerator / denominator)
            doc_names[nid] = p["doc_name"]

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [
        {"node_id": nid, "doc_name": doc_names[nid], "score": sc} for nid, sc in ranked
    ]
