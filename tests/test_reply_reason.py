"""Reply-reason gate, motion structures, and critique hard fails."""

import json

import pytest

from trace_drafting import format_drafting_context_package
from trace_first_touch import FIRST_TOUCH_WORD_MAX
from trace_reply_reason import (
    MOTION_STRUCTURES,
    DraftBlocked,
    assess_reply_reason,
    reply_reason_hard_fails,
    sender_assets,
)
from trace_style_prompts import DRAFTING_PLAIN_TEMPLATE, DRAFTING_SHORT_TEMPLATE


def _job():
    return {
        "outreach_motion": "direct_application",
        "outreach_role": "Practitioner",
        "role_open": True,
        "owns_or_influences": True,
        "relevant_proof": "built an outbound system and booked meetings",
        "signal_text": "Zip is hiring a BDR",
    }


def _akashic():
    from main import PRODUCT_PROFILES

    return PRODUCT_PROFILES["akashic"]


def _helix():
    from main import PRODUCT_PROFILES

    return PRODUCT_PROFILES["problem_validation"]


def test_akashic_bdr_hiring_does_not_send():
    out = assess_reply_reason(
        {
            "outreach_role": "Practitioner",
            "outreach_motion": "cold_product",
            "signal_text": "The PE firm is hiring a BDR.",
            "why_relevant": "Company is hiring.",
        },
        _akashic(),
    )
    assert out["motion"] == "cold_product"
    assert out["trigger_offer_alignment"] == "unrelated"
    assert out["draft_decision"] != "send_now"


def test_akashic_acquisition_alone_does_not_send():
    out = assess_reply_reason(
        {
            "outreach_role": "Practitioner",
            "outreach_motion": "cold_product",
            "signal_text": "The PE firm announced an add-on acquisition.",
        },
        _akashic(),
    )
    assert out["trigger_offer_alignment"] == "unrelated"
    assert out["draft_decision"] != "send_now"


def test_akashic_active_memo_comparison_needs_a_verified_asset():
    rec = {
        "outreach_role": "Practitioner",
        "outreach_motion": "cold_product",
        "signal_text": "The team currently compares past IC memos on every deal.",
        "workflow_ownership_evidence": ["compares past IC memos on every deal"],
    }
    held = assess_reply_reason(rec, _akashic())
    assert held["trigger_type"] == "behavior_trigger"
    assert held["trigger_offer_alignment"] == "direct"
    assert held["draft_decision"] == "strengthen_offer"
    sent = assess_reply_reason(
        rec,
        {
            **_akashic(),
            "sender_assets": [
                {
                    "id": "public_deal_replay",
                    "name": "One-page public deal replay",
                    "status": "verified",
                    "description": "one public deal, reconstructed",
                    "applicable_motions": ["cold_product"],
                }
            ],
        },
    )
    assert sent["draft_decision"] == "send_now"
    assert sent["sender_asset"] == "One-page public deal replay"


def test_akashic_search_for_decision_memory_can_send():
    out = assess_reply_reason(
        {
            "outreach_role": "Practitioner",
            "outreach_motion": "cold_product",
            "signal_text": (
                "The PE team is looking for a decision-memory solution for IC memos."
            ),
        },
        _akashic(),
    )
    assert out["trigger_type"] == "action_trigger"
    assert out["trigger_offer_alignment"] == "direct"
    assert out["draft_decision"] == "send_now"


def test_helix_bdr_hiring_is_adjacent_and_does_not_send():
    out = assess_reply_reason(
        {
            "outreach_role": "Practitioner",
            "outreach_motion": "cold_product",
            "signal_text": "The company is hiring a BDR as the sales team expands.",
        },
        _helix(),
    )
    assert out["trigger_offer_alignment"] == "adjacent"
    assert out["draft_decision"] == "research_more"


def test_helix_live_objection_problem_is_direct():
    out = assess_reply_reason(
        {
            "outreach_role": "Practitioner",
            "outreach_motion": "cold_product",
            "signal_text": (
                "The founder said new reps struggle to handle live objections on calls right now."
            ),
        },
        _helix(),
    )
    assert out["trigger_offer_alignment"] == "direct"
    assert out["trigger_type"] in ("action_trigger", "behavior_trigger")
    assert out["draft_decision"] == "send_now"


def test_sender_applying_is_a_direct_application():
    out = assess_reply_reason(
        {
            "outreach_role": "Practitioner",
            "signal_text": "Zip is hiring a BDR",
            "relevant_proof": "built an outbound system and booked meetings",
            "active_occasion": "I applied today",
        }
    )
    assert out["motion"] == "direct_application"
    assert out["draft_decision"] == "send_now"


def test_live_role_with_proof_sends():
    out = assess_reply_reason(_job())
    assert out["trigger_type"] == "action_trigger"
    assert out["draft_decision"] == "send_now"
    assert out["motion"] == "direct_application"


def test_named_warm_intro_sends():
    out = assess_reply_reason(
        {
            "outreach_motion": "warm_intro",
            "relationship_path": "Jason Koulouras",
            "overlap": "founder-led GTM",
        }
    )
    assert out["trigger_type"] == "relationship_trigger"
    assert out["draft_decision"] == "send_now"


def test_existing_relationship_allows_follow_up():
    out = assess_reply_reason(
        {
            "outreach_motion": "existing_relationship",
            "trigger_type": "relationship_trigger",
            "relationship_path": "prior substantive conversation",
        }
    )
    assert out["trigger_type"] == "relationship_trigger"
    assert out["draft_decision"] == "send_now"


def test_linkedin_connection_is_not_a_warm_path():
    out = assess_reply_reason(
        {
            "outreach_motion": "warm_intro",
            "signal_text": "linkedin connection only",
        }
    )
    assert out["draft_decision"] != "send_now"


def test_public_memo_post_does_not_send():
    out = assess_reply_reason(
        {
            "outreach_motion": "cold_product",
            "outreach_role": "Practitioner",
            "signal_text": "wrote a public post about investment memos",
            "recommendation_reason": "no workflow ownership",
        }
    )
    assert out["trigger_type"] == "topic_trigger"
    assert out["draft_decision"] in ("research_more", "no_draft")


def test_acquisition_announcement_does_not_send():
    out = assess_reply_reason(
        {
            "outreach_motion": "cold_product",
            "signal_text": "The company completed an acquisition",
        }
    )
    assert out["trigger_type"] == "topic_trigger"
    assert out["draft_decision"] != "send_now"


def test_verified_workflow_and_asset_can_draft():
    out = assess_reply_reason(
        {
            "outreach_motion": "cold_product",
            "outreach_role": "Practitioner",
            "workflow_ownership_evidence": ["reopens prior memos before every IC"],
            "signal_text": "reopens prior memos before every IC",
            "trigger_offer_alignment": "direct",
        },
        {
            "sender_assets": [
                {
                    "id": "public_deal_replay",
                    "name": "One-page public deal replay",
                    "status": "verified",
                    "applicable_motions": ["cold_product"],
                    "description": "one public deal, reconstructed",
                }
            ]
        },
    )
    assert out["trigger_type"] == "behavior_trigger"
    assert out["draft_decision"] == "send_now"
    assert out["sender_asset"] == "One-page public deal replay"


def test_planned_asset_strengthens_offer_with_no_body():
    from trace_app import drafting

    out = drafting.build_draft(
        {
            "sender_assets": [
                {
                    "id": "replay",
                    "name": "Replay",
                    "status": "planned",
                    "applicable_motions": ["cold_product"],
                }
            ]
        },
        {
            "first_name": "A",
            "outreach_motion": "cold_product",
            "outreach_role": "Practitioner",
            "workflow_ownership_evidence": ["reopens memos every deal"],
            "signal_text": "reopens memos every deal",
            "trigger_offer_alignment": "direct",
        },
    )
    assert out["verdict"] == "strengthen_offer"
    assert out["body"] == ""
    assert out["subject"] == ""


def test_expert_with_firsthand_narrow_question_can_draft():
    out = assess_reply_reason(
        {
            "outreach_motion": "expert_research",
            "outreach_role": "Expert / Researcher",
            "firsthand_evidence": True,
            "novel_question": True,
            "signal_text": "In the source you observed teams reopening memos",
        }
    )
    assert out["motion"] == "expert_research"
    assert out["draft_decision"] == "send_now"


def test_broad_expert_article_uses_another_channel():
    out = assess_reply_reason(
        {
            "outreach_motion": "expert_research",
            "signal_text": "wrote a broad article about institutional memory",
            "firsthand_evidence": False,
        }
    )
    assert out["draft_decision"] in ("use_different_channel", "research_more")


def test_model_is_blocked_after_no_draft():
    import main as engine

    with pytest.raises(DraftBlocked):
        engine.claude_draft_email(
            {
                "email_mode": "problem_validation_email",
                "product_name": "Example",
                "product_context": "example",
                "sign_off": "A\nB",
            },
            {
                "outreach_motion": "cold_product",
                "outreach_role": "Practitioner",
                "signal_text": "wrote about investment memos",
            },
        )


def test_templates_stay_product_agnostic_and_short():
    assert "Helix" not in DRAFTING_SHORT_TEMPLATE
    assert "Akashic" not in DRAFTING_SHORT_TEMPLATE
    assert "Helix" not in DRAFTING_PLAIN_TEMPLATE
    assert "Akashic" not in DRAFTING_PLAIN_TEMPLATE
    assert FIRST_TOUCH_WORD_MAX == 75
    assert "Never exceed 75 words" in DRAFTING_SHORT_TEMPLATE
    assert "em dash" in DRAFTING_SHORT_TEMPLATE
    blob = "\n".join(MOTION_STRUCTURES.values())
    assert "Helix" not in blob and "Akashic" not in blob
    assert "live opportunity" in MOTION_STRUCTURES["direct_application"]
    assert "proof" in MOTION_STRUCTURES["direct_application"]
    assert "Never state that an asset exists" in MOTION_STRUCTURES["cold_product"]
    assert "personally" in MOTION_STRUCTURES["expert_research"]


def test_context_package_includes_reply_reason():
    pkg = format_drafting_context_package(_job())
    assert "Reply-Reason Assessment" in pkg
    assert "send_now" in pkg
    assert "direct_application" in pkg
    assert "why_surfaced" in pkg or "Why surfaced" in pkg or "Outreach role" in pkg


def test_question_without_reply_reason_fails():
    assessment = assess_reply_reason(
        {
            "outreach_motion": "cold_product",
            "signal_text": "wrote about investment memos",
        }
    )
    fails = reply_reason_hard_fails("Does your team feel this too?", assessment)
    assert "question_without_reply_reason" in fails


def test_unverified_asset_claim_fails():
    assessment = assess_reply_reason(
        {
            "outreach_motion": "cold_product",
            "workflow_ownership_evidence": ["reopens memos every deal"],
            "sender_asset_status": "planned",
            "trigger_offer_alignment": "direct",
        }
    )
    fails = reply_reason_hard_fails("I can send the one-page replay first.", assessment)
    assert "sender_asset_not_verified" in fails
    assert assessment["draft_decision"] == "strengthen_offer"


def test_verified_asset_claim_is_allowed():
    assessment = {
        "trigger_type": "behavior_trigger",
        "reply_reason_level": "credible",
        "motion": "cold_product",
        "draft_decision": "send_now",
        "sender_asset": "One-page public deal replay",
        "sender_asset_status": "verified",
        "workflow_ownership_evidence": ["reopens memos"],
    }
    fails = reply_reason_hard_fails("I can send the one-page replay first.", assessment)
    assert "sender_asset_not_verified" not in fails


def test_meeting_before_value_and_topic_laundering():
    assessment = {
        "trigger_type": "topic_trigger",
        "reply_reason_level": "topic_only",
        "motion": "cold_product",
        "draft_decision": "no_draft",
        "sender_asset": "",
        "workflow_ownership_evidence": [],
    }
    fails = reply_reason_hard_fails(
        "You probably struggle with this. Could we do 15 minutes?",
        assessment,
    )
    assert "topic_signal_used_as_action_trigger" in fails
    assert "meeting_before_value" in fails
    assert "draft_generated_despite_no_draft" in fails
    assert "no_verified_workflow_owner" in fails


def test_expert_personal_pain_is_a_structure_mismatch():
    fails = reply_reason_hard_fails(
        "You struggle with this every week.",
        {
            "motion": "expert_research",
            "trigger_type": "topic_trigger",
            "reply_reason_level": "topic_only",
            "draft_decision": "send_now",
            "sender_asset": "",
            "workflow_ownership_evidence": [],
        },
    )
    assert "motion_structure_mismatch" in fails


def test_old_record_and_profile_keep_working():
    rec = json.loads('{"name":"Old","signal_text":"a post about memos"}\n')
    before = dict(rec)
    out = assess_reply_reason(rec, None)
    assert rec == before
    assert out["draft_decision"] != "send_now"
    assert sender_assets(None) == []
    assert sender_assets({"product_name": "Example"}) == []


def test_profile_form_keeps_sender_assets():
    from trace_app.profiles import _form_profile_json

    out = _form_profile_json(
        {
            "name": "Example",
            "whatItDoes": "does a thing",
            "senderName": "A",
            "senderCompany": "B",
        },
        {"sender_assets": [{"id": "replay", "name": "Replay", "status": "verified"}]},
    )
    assert out["sender_assets"][0]["id"] == "replay"
