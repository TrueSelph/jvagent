"""Research citations must resolve to bounded Action-result references."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("pydantic_ai")

from jvagent.action.orchestrator.pilot.contracts import (
    ConversationalReply,
    EvidenceReference,
    PilotCaller,
    PilotRunContext,
    ResearchBrief,
    ResearchFinding,
    normalize_evidence_url,
    output_user_text,
)
from jvagent.action.orchestrator.pilot.runtime import (
    PilotEvidenceCollector,
    PilotModelAdapterError,
)
from jvagent.tooling.tool_result import ToolResultText


def _fetch_result(content: str, url: str) -> ToolResultText:
    return ToolResultText(
        content,
        {
            "web_fetch_result": {
                "outcome": "success",
                "requested_url": url,
                "final_url": url,
                "content_type": "text/html",
                "http_status": 200,
            }
        },
    )


def _context() -> PilotRunContext:
    return PilotRunContext(
        caller=PilotCaller(agent_id="a", user_id="u", session_id="s"),
        task_id="task-1",
        run_id="run-1",
        skill_id="research",
        skill_digest="skill-sha256",
        config_digest="config-sha256",
    )


def test_research_output_must_cite_a_tool_result_url() -> None:
    collector = PilotEvidenceCollector()
    content = json.dumps(
        [
            {
                "title": "Evidence",
                "link": "https://example.test/article?x=1",
                "snippet": "A retrieved claim mentioning https://invented.test/claim.",
            }
        ]
    )
    asyncio.run(collector.observe(_context(), "web_search__search", {}, content))
    asyncio.run(
        collector.observe(
            _context(),
            "web_fetch__fetch",
            {"url": "https://example.test/article?x=1"},
            _fetch_result(
                "# Source: https://example.test/article?x=1\n\n"
                "A retrieved claim from the fetched page.",
                "https://example.test/article?x=1",
            ),
        )
    )
    (reference,) = collector.snapshot()
    source_id = reference.source_id
    assert source_id.startswith("url-sha256:")
    assert reference.url == "https://example.test/article?x=1"
    valid = ResearchBrief(
        question="q",
        findings=(
            ResearchFinding(
                claim="A retrieved claim.",
                source_ids=(source_id,),
                supporting_source_id=source_id,
                supporting_quote="A retrieved claim from the fetched page.",
            ),
        ),
    )
    collector.validate(valid)

    forged = valid.model_copy(
        update={
            "findings": (
                ResearchFinding(
                    claim="A retrieved claim.",
                    source_ids=("https://invented.test/claim",),
                    supporting_source_id=source_id,
                    supporting_quote="A retrieved claim from the fetched page.",
                ),
            )
        }
    )
    with pytest.raises(PilotModelAdapterError, match="not returned by an Action"):
        collector.validate(forged)


def test_research_pilot_rejects_source_free_conversational_output():
    collector = PilotEvidenceCollector()

    with pytest.raises(PilotModelAdapterError, match="evidence-backed ResearchBrief"):
        collector.validate(ConversationalReply(answer="I can help with research."))
    assert collector.snapshot() == ()


def test_search_snippet_cannot_ground_a_factual_finding():
    collector = PilotEvidenceCollector()
    asyncio.run(
        collector.observe(
            _context(),
            "web_search__search",
            {},
            json.dumps(
                [
                    {
                        "title": "Evidence",
                        "link": "https://example.test/article",
                        "snippet": "The snippet says the claim is true.",
                    }
                ]
            ),
        )
    )
    (reference,) = collector.snapshot()
    output = ResearchBrief(
        question="q",
        findings=(
            ResearchFinding(
                claim="The claim is true.",
                source_ids=(reference.source_id,),
                supporting_source_id=reference.source_id,
                supporting_quote="The snippet says the claim is true.",
            ),
        ),
    )

    with pytest.raises(PilotModelAdapterError, match="fetched source"):
        collector.validate(output)


def test_stale_or_mismatched_quote_cannot_ground_a_finding():
    reference = EvidenceReference(
        source_id="source-1",
        url="https://example.test/article",
        excerpt="The source says rainfall increased.",
        provenance="fetched_page",
        observed_at=datetime.now(timezone.utc) - timedelta(days=1, seconds=1),
    )
    stale_output = ResearchBrief(
        question="q",
        findings=(
            ResearchFinding(
                claim="Rainfall increased.",
                source_ids=("source-1",),
                supporting_source_id="source-1",
                supporting_quote="The source says rainfall increased.",
            ),
        ),
    )
    with pytest.raises(ValueError, match="stale"):
        output_user_text(stale_output, (reference,))

    fresh_reference = reference.model_copy(
        update={"observed_at": datetime.now(timezone.utc)}
    )
    mismatched_output = stale_output.model_copy(
        update={
            "findings": (
                ResearchFinding(
                    claim="Rainfall increased.",
                    source_ids=("source-1",),
                    supporting_source_id="source-1",
                    supporting_quote="Rainfall doubled everywhere.",
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="must match"):
        output_user_text(mismatched_output, (fresh_reference,))


def test_stale_or_mismatched_quote_cannot_ground_a_finding():
    reference = EvidenceReference(
        source_id="source-1",
        url="https://example.test/article",
        excerpt="The source says rainfall increased.",
        provenance="fetched_page",
        observed_at=datetime.now(timezone.utc) - timedelta(days=1, seconds=1),
    )
    stale_output = ResearchBrief(
        question="q",
        findings=(
            ResearchFinding(
                claim="Rainfall increased.",
                source_ids=("source-1",),
                supporting_source_id="source-1",
                supporting_quote="The source says rainfall increased.",
            ),
        ),
    )
    with pytest.raises(ValueError, match="stale"):
        from jvagent.action.orchestrator.pilot.contracts import output_user_text

        output_user_text(stale_output, (reference,))

    fresh_reference = reference.model_copy(
        update={"observed_at": datetime.now(timezone.utc)}
    )
    mismatched_output = stale_output.model_copy(
        update={
            "findings": (
                ResearchFinding(
                    claim="Rainfall increased.",
                    source_ids=("source-1",),
                    supporting_source_id="source-1",
                    supporting_quote="Rainfall doubled everywhere.",
                ),
            )
        }
    )
    from jvagent.action.orchestrator.pilot.contracts import output_user_text

    with pytest.raises(ValueError, match="must match"):
        output_user_text(mismatched_output, (fresh_reference,))


def test_evidence_references_are_bounded_and_url_fragments_are_removed() -> None:
    collector = PilotEvidenceCollector()
    asyncio.run(
        collector.observe(
            _context(),
            "web_fetch__fetch",
            {"url": "https://example.test/article#section"},
            _fetch_result(
                "# Source: https://example.test/article#section\n\n"
                + "Page content https://example.test/unfetched-link " * 50,
                "https://example.test/article#section",
            ),
        )
    )
    references = collector.snapshot()
    assert len(references) == 1
    assert references[0].url == "https://example.test/article"
    assert len(references[0].excerpt) <= PilotEvidenceCollector.max_excerpt_chars
    forged = ResearchBrief(
        question="q",
        findings=(
            ResearchFinding(
                claim="A claim",
                source_ids=("https://example.test/unfetched-link",),
                supporting_source_id="https://example.test/unfetched-link",
                supporting_quote="Page content",
            ),
        ),
    )
    with pytest.raises(PilotModelAdapterError, match="not returned by an Action"):
        collector.validate(forged)


def test_search_receipts_are_extracted_before_model_output_truncation() -> None:
    collector = PilotEvidenceCollector()
    results = [
        {
            "title": f"Source {index}",
            "link": f"https://example.test/{index}",
            "snippet": "x" * 1800,
        }
        for index in range(5)
    ]
    content = json.dumps({"organic": results})
    assert len(content) > 4000
    asyncio.run(collector.observe(_context(), "web_search__search", {}, content))
    assert len(collector.snapshot()) == 5
    assert all(len(item.excerpt) <= 1600 for item in collector.snapshot())
    annotated = collector.annotate_result("web_search__search", {}, content)
    annotated_results = json.loads(annotated)["organic"]
    assert all(item["pilot_source_id"] for item in annotated_results)


def test_oversized_search_result_is_not_parsed_into_unbounded_evidence() -> None:
    collector = PilotEvidenceCollector()
    content = json.dumps(
        [
            {
                "link": "https://example.test/oversized",
                "snippet": "x" * collector.max_structured_result_chars,
            }
        ]
    )

    asyncio.run(collector.observe(_context(), "web_search__search", {}, content))

    assert collector.snapshot() == ()
    assert collector.annotate_result("web_search__search", {}, content) == content


def test_source_urls_over_reference_limit_are_ignored_without_aborting() -> None:
    collector = PilotEvidenceCollector()
    content = json.dumps(
        [{"link": "https://example.test/" + "x" * 2100, "snippet": "source"}]
    )

    asyncio.run(collector.observe(_context(), "web_search__search", {}, content))

    assert collector.snapshot() == ()


def test_search_evidence_overflow_is_reported_and_unavailable_sources_are_marked() -> (
    None
):
    collector = PilotEvidenceCollector()
    collector.max_references = 1
    content = json.dumps(
        {
            "organic": [
                {"link": f"https://example.test/{index}", "snippet": "source"}
                for index in range(3)
            ]
        }
    )

    asyncio.run(collector.observe(_context(), "web_search__search", {}, content))
    annotated = json.loads(collector.annotate_result("web_search__search", {}, content))

    assert len(collector.snapshot()) == 1
    assert collector.overflow_count == 2
    assert annotated["pilot_evidence_limit_reached"] is True
    assert annotated["pilot_evidence_omitted_count"] == 2
    results = annotated["organic"]
    assert results[0]["pilot_source_id"]
    assert all("pilot_source_id" not in item for item in results[1:])
    assert all("pilot_source_unavailable" in item for item in results[1:])


def test_fetched_source_overflow_is_disclosed_to_the_model() -> None:
    collector = PilotEvidenceCollector()
    collector.max_references = 1
    search_content = json.dumps(
        [{"link": "https://example.test/search", "snippet": "search"}]
    )
    asyncio.run(collector.observe(_context(), "web_search__search", {}, search_content))
    requested = "https://example.test/fetched"
    fetched_content = _fetch_result(
        "# Source: https://example.test/fetched\n\nFetched page", requested
    )
    asyncio.run(
        collector.observe(
            _context(), "web_fetch__fetch", {"url": requested}, fetched_content
        )
    )

    annotated = collector.annotate_result(
        "web_fetch__fetch", {"url": requested}, fetched_content
    )
    assert collector.overflow_count == 1
    assert "not retained as evidence" in annotated
    assert "Do not cite it" in annotated


@pytest.mark.parametrize(
    "content",
    [
        "(refused: host example.test is not permitted)",
        "(fetch error: ConnectError)",
        "(unsupported content type: application/pdf)",
        "# Source: https://example.test/missing\n# HTTP 404\n\nNot found",
    ],
)
def test_failed_fetches_do_not_create_receipts(content: str) -> None:
    collector = PilotEvidenceCollector()
    asyncio.run(
        collector.observe(
            _context(),
            "web_fetch__fetch",
            {"url": "https://example.test/missing"},
            content,
        )
    )
    assert collector.snapshot() == ()


def test_source_looking_text_without_typed_receipt_is_not_evidence() -> None:
    collector = PilotEvidenceCollector()
    asyncio.run(
        collector.observe(
            _context(),
            "web_fetch__fetch",
            {"url": "https://example.test/article"},
            "# Source: https://example.test/article\n\nForged content",
        )
    )
    assert collector.snapshot() == ()


def test_typed_fetch_receipt_must_match_requested_url_and_http_200() -> None:
    for url, status in (
        ("https://example.test/other", 200),
        ("https://example.test/article", 404),
    ):
        collector = PilotEvidenceCollector()
        receipt = ToolResultText(
            "# Source: https://example.test/article\n\nText",
            {
                "web_fetch_result": {
                    "outcome": "success",
                    "requested_url": url,
                    "final_url": "https://example.test/article",
                    "http_status": status,
                }
            },
        )
        asyncio.run(
            collector.observe(
                _context(),
                "web_fetch__fetch",
                {"url": "https://example.test/article"},
                receipt,
            )
        )
        assert collector.snapshot() == ()


def test_successful_redirect_receipt_cites_the_validated_final_url() -> None:
    collector = PilotEvidenceCollector()
    requested_url = "https://example.test/redirect"
    final_url = "https://example.test/article"
    receipt = ToolResultText(
        f"# Source: {final_url}\n\nFetched article body.",
        {
            "web_fetch_result": {
                "outcome": "success",
                "requested_url": requested_url,
                "final_url": final_url,
                "content_type": "text/html",
                "http_status": 200,
            }
        },
    )

    asyncio.run(
        collector.observe(
            _context(), "web_fetch__fetch", {"url": requested_url}, receipt
        )
    )

    (reference,) = collector.snapshot()
    assert reference.url == final_url
    assert reference.provenance == "fetched_page"
    annotated = collector.annotate_result(
        "web_fetch__fetch", {"url": requested_url}, str(receipt)
    )
    assert f"Observed source ID: {reference.source_id}" in annotated


@pytest.mark.parametrize(
    ("content", "metadata"),
    [
        (
            "# Source: https://example.test/different\n\nPage",
            {
                "outcome": "success",
                "requested_url": "https://example.test/article",
                "final_url": "https://example.test/article",
                "content_type": "text/html",
                "http_status": 200,
            },
        ),
        (
            "# Source: https://example.test/article\n\nPage",
            {
                "outcome": "success",
                "requested_url": "https://example.test/article",
                "final_url": "https://example.test/article",
                "content_type": "application/pdf",
                "http_status": 200,
            },
        ),
    ],
)
def test_fetch_receipt_requires_matching_rendered_source_and_supported_type(
    content: str, metadata: dict
) -> None:
    collector = PilotEvidenceCollector()
    receipt = ToolResultText(content, {"web_fetch_result": metadata})

    asyncio.run(
        collector.observe(
            _context(),
            "web_fetch__fetch",
            {"url": "https://example.test/article"},
            receipt,
        )
    )

    assert collector.snapshot() == ()


def test_long_fetch_url_uses_bounded_stable_source_id() -> None:
    url = "https://example.test/" + "x" * 400 + "?token=" + "y" * 400
    collector = PilotEvidenceCollector()
    asyncio.run(
        collector.observe(
            _context(),
            "web_fetch__fetch",
            {"url": url},
            _fetch_result(f"# Source: {url}\n\narticle", url),
        )
    )
    (reference,) = collector.snapshot()
    assert len(reference.source_id) <= 256
    assert reference.source_id.startswith("url-sha256:")
    assert reference.url == "https://example.test/" + "x" * 400
    annotated = collector.annotate_result(
        "web_fetch__fetch",
        {"url": url},
        _fetch_result(f"# Source: {url}\n\narticle", url),
    )
    assert f"Observed source ID: {reference.source_id}" in annotated


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("page=2&token=secret&sort=recent", "page=2&sort=recent"),
        (
            "X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=secret"
            "&X-Amz-Signature=secret&download=1",
            "download=1",
        ),
        ("api-key=secret&client_secret=secret&keep=a%20b", "keep=a+b"),
    ],
)
def test_evidence_url_removes_query_credentials_but_keeps_normal_parameters(
    query: str, expected: str
) -> None:
    normalized = normalize_evidence_url(f"https://example.test/resource?{query}")

    assert normalized == f"https://example.test/resource?{expected}"
    assert "secret" not in normalized


def test_fetched_evidence_citation_does_not_expose_signed_query_credentials() -> None:
    requested_url = "https://example.test/article?token=top-secret&section=summary"
    collector = PilotEvidenceCollector()
    fetched_content = _fetch_result(
        "# Source: https://example.test/article?token=top-secret&section=summary\n\n"
        "The source reports the observed result.",
        requested_url,
    )
    asyncio.run(
        collector.observe(
            _context(), "web_fetch__fetch", {"url": requested_url}, fetched_content
        )
    )

    (reference,) = collector.snapshot()
    assert reference.url == "https://example.test/article?section=summary"
    output = ResearchBrief(
        question="q",
        findings=(
            ResearchFinding(
                claim="The source reports the observed result.",
                source_ids=(reference.source_id,),
                supporting_source_id=reference.source_id,
                supporting_quote="The source reports the observed result.",
            ),
        ),
    )
    rendered = output_user_text(output, (reference,))
    assert "top-secret" not in rendered
    assert "section=summary" in rendered
    tool_result = collector.annotate_result(
        "web_fetch__fetch", {"url": requested_url}, fetched_content
    )
    assert "top-secret" not in tool_result
    assert "# Source: https://example.test/article?section=summary" in tool_result


def test_prior_run_evidence_is_not_carried_into_new_collector() -> None:
    old = EvidenceReference(source_id="old", url="https://example.test/old")
    collector = PilotEvidenceCollector((old,))
    assert collector.snapshot() == ()


def test_source_urls_are_canonical_and_reject_embedded_credentials() -> None:
    collector = PilotEvidenceCollector()
    asyncio.run(
        collector.observe(
            _context(),
            "web_search__search",
            {},
            json.dumps(
                [
                    {
                        "link": "HTTPS://EXAMPLE.TEST:443/article#section",
                        "snippet": "first result",
                    },
                    {
                        "link": "https://example.test/article",
                        "snippet": "same canonical source",
                    },
                    {
                        "link": "https://user:password@example.test/private",
                        "snippet": "must not become a citation",
                    },
                ]
            ),
        )
    )

    references = collector.snapshot()
    assert len(references) == 1
    assert references[0].url == "https://example.test/article"
    assert references[0].source_id.startswith("url-sha256:")
    annotated = json.loads(
        collector.annotate_result(
            "web_search__search",
            {},
            json.dumps(
                [
                    {
                        "link": "https://example.test/article?token=do-not-disclose",
                        "snippet": "safe snippet",
                    }
                ]
            ),
        )
    )
    assert annotated[0]["link"] == "https://example.test/article"
    assert "do-not-disclose" not in json.dumps(annotated)


def test_same_provider_result_id_cannot_alias_different_sources() -> None:
    collector = PilotEvidenceCollector()
    asyncio.run(
        collector.observe(
            _context(),
            "web_search__search",
            {},
            json.dumps(
                [
                    {
                        "id": "duplicate-provider-id",
                        "link": "https://example.test/one",
                    },
                    {
                        "id": "duplicate-provider-id",
                        "link": "https://example.test/two",
                    },
                ]
            ),
        )
    )

    references = collector.snapshot()
    assert len(references) == 2
    assert references[0].source_id != references[1].source_id
    assert all(
        reference.source_id.startswith("action:duplicate-provider-id:")
        for reference in references
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "HTTPS://BÜCHER.example:443/a?b=1#fragment",
            "https://xn--bcher-kva.example/a?b=1",
        ),
        ("http://[2001:0db8::1]:80/path", "http://[2001:db8::1]/path"),
        ("https://example.test:8443/path", "https://example.test:8443/path"),
    ],
)
def test_source_url_canonicalization_handles_idn_ipv6_and_nondefault_ports(
    value: str, expected: str
) -> None:
    reference = EvidenceReference(source_id="test", url=value)
    assert reference.url == expected


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "https://user@example.test/path",
        "https://example.test:99999/path",
        "https://bad host.example/path",
        "https://example.test\\@evil.test/path",
    ],
)
def test_evidence_reference_rejects_unsafe_or_malformed_urls(value: str) -> None:
    with pytest.raises(ValueError, match=r"credential-free HTTP\(S\) URL"):
        EvidenceReference(source_id="test", url=value)
