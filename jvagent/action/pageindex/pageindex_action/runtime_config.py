"""Shared ingestion/retrieval config push helpers for PageIndex actions."""

from typing import Any, Dict

from jvagent.action.pageindex.config import (
    set_pageindex_candidate_k,
    set_pageindex_doc_description,
    set_pageindex_enable_lexical_index,
    set_pageindex_max_docs_for_tree_search,
    set_pageindex_max_summary_chars,
    set_pageindex_max_token_num_each_node,
    set_pageindex_max_tree_prompt_tokens,
    set_pageindex_node_summary,
    set_pageindex_node_text,
    set_pageindex_retrieval_excerpt_source,
    set_pageindex_summary_token_threshold,
)


def bool_from_config(value: Any, default: bool) -> bool:
    """Convert config value to bool. None -> default; yes/true/1 -> True; else False."""
    if value is None:
        return default
    v = str(value).lower().strip()
    return v in ("yes", "true", "1")


def push_ingestion_config(ingestion: Dict[str, Any]) -> None:
    """Push ingestion config values to config module."""
    set_pageindex_node_summary(ingestion.get("node_summary", False))
    set_pageindex_node_text(ingestion.get("node_text", True))
    set_pageindex_doc_description(ingestion.get("doc_description", False))
    set_pageindex_max_token_num_each_node(ingestion.get("max_token_num_each_node"))
    set_pageindex_summary_token_threshold(ingestion.get("summary_token_threshold"))


def push_retrieval_config(retrieval: Dict[str, Any]) -> None:
    """Push retrieval config values to config module."""
    if "max_summary_chars" in retrieval and retrieval["max_summary_chars"] is not None:
        set_pageindex_max_summary_chars(retrieval["max_summary_chars"])
    if (
        "max_tree_prompt_tokens" in retrieval
        and retrieval["max_tree_prompt_tokens"] is not None
    ):
        set_pageindex_max_tree_prompt_tokens(retrieval["max_tree_prompt_tokens"])
    if "enable_lexical_index" in retrieval:
        set_pageindex_enable_lexical_index(retrieval["enable_lexical_index"])
    if "candidate_k" in retrieval and retrieval["candidate_k"] is not None:
        set_pageindex_candidate_k(retrieval["candidate_k"])
    if (
        "max_docs_for_tree_search" in retrieval
        and retrieval["max_docs_for_tree_search"] is not None
    ):
        set_pageindex_max_docs_for_tree_search(retrieval["max_docs_for_tree_search"])
    if (
        "retrieval_excerpt_source" in retrieval
        and retrieval["retrieval_excerpt_source"] is not None
    ):
        set_pageindex_retrieval_excerpt_source(
            str(retrieval["retrieval_excerpt_source"])
        )


def get_ingestion_config(config: Dict[str, Any], node_summary_attr: bool) -> Dict[str, Any]:
    """Resolve ingestion config from action config (with attribute fallback for node_summary)."""
    cfg = config or {}
    node_summary = (
        bool_from_config(cfg["node_summary"], False)
        if "node_summary" in cfg
        else node_summary_attr
    )
    return {
        "node_summary": node_summary,
        "node_text": bool_from_config(cfg.get("node_text"), True),
        "doc_description": bool_from_config(cfg.get("doc_description"), False),
        "max_token_num_each_node": cfg.get("max_token_num_each_node"),
        "summary_token_threshold": cfg.get("summary_token_threshold")
        or cfg.get("max_node_tokens"),
    }


def normalize_retrieval_excerpt_source(value: Any, fallback: str) -> str:
    """Return 'text' or 'summary' for tree prompt and directive excerpts."""
    if value is None:
        v = str(fallback).lower().strip()
    else:
        v = str(value).lower().strip()
    return "text" if v == "text" else "summary"


def format_page_range(r: Dict[str, Any]) -> str:
    """Format page range from result dict, e.g. 'pp. 5-8' or 'p. 5'."""
    start = r.get("start_index")
    end = r.get("end_index")
    if start is not None and end is not None and start != end:
        return f"pp. {start}-{end}"
    if start is not None:
        return f"p. {start}"
    return ""


async def ensure_ingestion_config_for_agent(agent_id: str) -> None:
    """Push ingestion config from agent's PageIndex action to config module.

    Used when REST ingest does not receive if_add_node_summary in the form.
    Resolves config from cached actions; falls back to text-first ingestion when
    cache miss or no PageIndex action (agent-scoped routes assume PageIndex).
    The first cached ``PageIndexAction`` (including subclasses) wins.
    """
    from jvagent.core.cache import get_cached_actions

    from jvagent.action.pageindex.pageindex_action.pageindex_action import PageIndexAction

    actions = await get_cached_actions(agent_id, enabled_only=True)
    for action in actions or []:
        if isinstance(action, PageIndexAction):
            config = getattr(action, "config", None) or {}
            node_summary_attr = getattr(action, "node_summary", False)
            ingestion = get_ingestion_config(config, node_summary_attr)
            push_ingestion_config(ingestion)
            return
    push_ingestion_config(
        {
            "node_summary": False,
            "node_text": True,
            "doc_description": False,
            "max_token_num_each_node": None,
            "summary_token_threshold": None,
        }
    )
