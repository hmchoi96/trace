"""Structured research: dedupe, empty defaults, and no prose-to-fact parsing."""

from trace_research import dedupe_research, parse_research, research_from_record


def test_duplicate_claims_keep_the_sourced_version():
    research = dedupe_research({
        "verified_facts": [
            {"claim": "The proxy is internal.", "source_url": "", "source_date": "", "quote_or_paraphrase": ""},
            {
                "claim": "The proxy is internal.",
                "source_url": "https://example.com/a",
                "source_date": "2026-05-01",
                "quote_or_paraphrase": "We run an internal proxy.",
            },
            {
                "claim": "A second system uses user tokens.",
                "source_url": "https://example.com/b",
                "source_date": "",
                "quote_or_paraphrase": "",
            },
        ],
        "inferences": [
            {"claim": "Authority may be fragmented.", "confidence": "high", "based_on": []},
            {
                "claim": "Authority may be fragmented.",
                "confidence": "medium",
                "based_on": ["https://example.com/a"],
            },
        ],
        "unknowns": ["Is there a central layer?", "is there a central layer"],
    })
    claims = [item["claim"] for item in research["verified_facts"]]
    assert claims.count("The proxy is internal.") == 1
    sourced = next(item for item in research["verified_facts"] if item["claim"] == "The proxy is internal.")
    assert sourced["source_url"] == "https://example.com/a"
    assert sourced["quote_or_paraphrase"] == "We run an internal proxy."
    assert "A second system uses user tokens." in claims
    inference = research["inferences"][0]
    assert inference["confidence"] == "medium"
    assert inference["based_on"] == ["https://example.com/a"]
    assert research["unknowns"] == ["Is there a central layer?"]


def test_higher_confidence_wins_only_with_evidence():
    research = dedupe_research({
        "inferences": [
            {"claim": "The gap may remain.", "confidence": "low", "based_on": ["https://example.com/a"]},
            {"claim": "The gap may remain.", "confidence": "high", "based_on": ["https://example.com/b"]},
        ]
    })
    assert research["inferences"][0]["confidence"] == "high"
    assert "https://example.com/a" in research["inferences"][0]["based_on"]


def test_old_prose_is_not_a_verified_fact():
    prose = "Do not outreach from the known post. The company already built a proxy."
    parsed = parse_research({"recommendation_reason": prose})
    assert parsed["verified_facts"] == []
    loaded = research_from_record({
        "recommendation_reason": prose,
        "email": "kept@example.com",
        "human_status": "APPROVED",
    })
    assert loaded["verified_facts"] == []
    assert loaded["gap_assessment"]["status"] == ""
