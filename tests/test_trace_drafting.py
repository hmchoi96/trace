"""Tests for Trace drafting context package and outreach role classification."""

from trace_drafting import (
    classify_outreach,
    classify_outreach_role,
    enrich_lead_from_candidate,
    format_drafting_context_package,
    format_trace_critique_context,
    legacy_outreach_angle,
    outreach_ask_guidance,
)
from trace_strategy_prompt import build_trace_strategy_sender_block


def _barbara_rec() -> dict:
    return {
        "name": "Barbara McEvilley",
        "title": "Director, Research",
        "company": "First American",
        "linkedin_url": "https://linkedin.com/in/example",
        "signal_text": "investment firms keep and revisit investment memos",
        "signal_source": "research interview",
        "signal_date": "2024-06",
        "why_relevant": (
            "research directly documents the behavior Akashic is built around"
        ),
        "recommendation": "CHAMPION_CANDIDATE",
        "recommendation_reason": (
            "co-author on industry research; commentator, not an economic buyer; "
            "not IC workflow owner; describes the workflow without owning it"
        ),
        "actor_type": "OTHER",
        "economic_buyer_likelihood": "LOW",
        "end_user_likelihood": "LOW",
        "influence_or_champion_potential": "HIGH",
        "supporting_evidence": [
            {
                "evidence_type": "DEEPENED",
                "quote_or_paraphrase": (
                    "54 firms interviewed; some revisit original concerns against outcomes; "
                    "most lack structured ex-post process"
                ),
                "source_date": "2024",
            }
        ],
    }


def _barbara_db_rec() -> dict:
    base = _barbara_rec()
    base["recommendation_reason"] = (
        "The paper documents teams keeping IC memos and reopening them on similar deals. "
        "The authors are Addepar/Stanford researchers commenting on interviews, "
        "who describe the workflow without owning it. "
        "Useful as an industry explainer or possible intro via the Monk/Addepar network, "
        "not as an economic buyer or IC user."
    )
    return base


def test_barbara_db_rec_stays_expert_with_connector_secondary():
    outreach = classify_outreach(_barbara_db_rec())
    assert outreach["outreach_role"] == "Expert / Researcher"
    assert "Connector" in outreach["secondary_roles"]
    assert outreach["recommended_ask"] == "validate_problem_interpretation"


def test_barbara_classified_as_expert_researcher():
    role = classify_outreach_role(_barbara_rec())
    assert role == "Expert / Researcher"


def test_barbara_context_includes_trace_interpretation():
    pkg = format_drafting_context_package(_barbara_rec())
    assert "TRACE RESEARCH POLICY" in pkg
    assert "Why surfaced:" in pkg
    assert "research directly documents" in pkg
    assert "Expert / Researcher" in pkg
    assert "54 firms interviewed" in pkg
    assert "Do NOT ask whether they personally struggle" in pkg


def test_barbara_legacy_angle_prioritizes_actor_over_senior():
    profile = {
        "angles": {
            "senior": "SENIOR NUANCE TEXT",
            "early": "EARLY NUANCE TEXT",
        }
    }
    angle = legacy_outreach_angle(_barbara_rec(), profile, is_senior=True)
    assert "Expert / Researcher" in angle
    assert angle.index("Expert / Researcher") < angle.index("SENIOR NUANCE TEXT")
    assert "Do NOT ask whether they personally struggle" in angle


def test_enrich_lead_copies_trace_fields():
    lead = enrich_lead_from_candidate(_barbara_rec())
    assert lead["outreach_role"] == "Expert / Researcher"
    assert lead["recommended_ask"] == "validate_problem_interpretation"
    assert lead["why_relevant"].startswith("research directly")
    assert lead["recommendation_reason"].startswith("co-author")


def test_critique_context_includes_logic_checks():
    ctx = format_trace_critique_context(_barbara_rec())
    assert "TRACE EVALUATION LAYERS" in ctx
    assert "Evidence distance" in ctx or "evidence_level" in ctx
    assert "Expert / Researcher" in ctx
    assert "validate_problem_interpretation" in ctx


def test_practitioner_role_guidance():
    rec = {
        "actor_type": "PRACTITIONER",
        "recommendation": "PRIMARY_PROSPECT",
        "end_user_likelihood": "HIGH",
        "recommendation_reason": "owns IC workflow",
    }
    assert classify_outreach_role(rec) == "Practitioner"
    assert "day-to-day work" in outreach_ask_guidance("Practitioner")


def test_strategy_sender_uses_profile_sender_block():
    block = build_trace_strategy_sender_block(
        "=== SENDER ===\n- Product: Akashic\n=== end sender ==="
    )
    assert "Akashic" in block
    assert "Helix" not in block


def test_strategy_sender_builds_from_profile_without_helix_fallback():
    block = build_trace_strategy_sender_block(
        profile={
            "product_name": "Myzel Organics",
            "sign_off": "Alex\nMyzel",
            "product_context": "Functional mushroom products for wellness.",
        }
    )
    assert "Myzel Organics" in block
    assert "building Helix" not in block
    assert "mid-call script" not in block


def test_strategy_sender_raises_without_profile_or_block():
    try:
        build_trace_strategy_sender_block()
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_template_labels_in_profiles():
    from trace_app.profiles import TEMPLATES

    assert TEMPLATES["legacy"]["label"] == "Research-Led Discovery"
    assert TEMPLATES["strategy"]["label"] == "Value-First Outreach"
    assert TEMPLATES["short"]["label"] == "Short Discovery"
    assert TEMPLATES["plain"]["label"] == "Cautious Hypothesis"
