"""Unit tests for Integrity / Alignment / Copy evaluation architecture."""

from __future__ import annotations

from trace_eval import (
    EVIDENCE_CONTEXT,
    EVIDENCE_FRICTION,
    EVIDENCE_NONE,
    QUALITY_SENDABLE,
    annotate_critique,
    body_matches_sign_off,
    decide_verdict,
    evidence_drafting_guidance,
    evidence_level_for,
    has_draftable_signal,
    integrity_hard_fails,
    normalize_evidence_level,
    quality_band,
    strip_foreign_signoff_hard_fails,
)


def test_evidence_level_aliases():
    assert normalize_evidence_level("medium") == EVIDENCE_CONTEXT
    assert normalize_evidence_level("strong") == EVIDENCE_FRICTION
    assert normalize_evidence_level(None, signal_strength="none") == EVIDENCE_NONE
    assert evidence_level_for({"signal_strength": "medium"}) == EVIDENCE_CONTEXT


def test_context_guidance_forbids_pain_pitch():
    text = evidence_drafting_guidance(EVIDENCE_CONTEXT)
    assert "Do not describe a pain" in text


def test_no_signal_guard():
    assert has_draftable_signal({}) is False
    assert has_draftable_signal({"first_name": "Ali"}) is True
    assert has_draftable_signal({"signal_text": "IC memos matter"}) is True
    assert has_draftable_signal({"evidence_level": "context"}) is True


def test_sign_off_match_is_profile_based():
    body = "Hi Ali,\n\nOne question?\n\nJamie Choi\nWiserbond Technologies Inc."
    assert body_matches_sign_off(body, "Jamie Choi\nWiserbond Technologies Inc.")
    assert not body_matches_sign_off(body, "Jamie\nbuilding Helix")


def test_strip_helix_signoff_when_not_required():
    fails = [
        "No closing line indicating 'building Helix' or 'Helix by Wiserbond'",
        "Invents facts not supported by the FACTS block.",
    ]
    out = strip_foreign_signoff_hard_fails(
        fails, required_sign_off="Jamie Choi\nWiserbond Technologies Inc."
    )
    assert out == ["Invents facts not supported by the FACTS block."]


def test_integrity_flags_wrong_product_signoff():
    body = "Hi Pat,\n\nQ?\n\nJamie\nbuilding Helix"
    fails = integrity_hard_fails(
        body=body,
        subject="q",
        sign_off="Jamie Choi\nWiserbond Technologies Inc.",
        word_count=3,
    )
    assert any("different product" in f.lower() or "sign-off" in f.lower() for f in fails)


def test_unified_verdict_80_is_pass_not_review():
    c = annotate_critique(
        {"hard_fails": [], "soft_scores": {}, "total": 86, "issues": []},
        integrity_fails=[],
        alignment_fails=[],
    )
    assert quality_band(86) == "usable"
    assert decide_verdict(c, 0) == "pass"
    assert decide_verdict({"hard_fails": [], "total": 92}, 0) == "pass"
    assert decide_verdict({"hard_fails": [], "total": 75}, 0) == "revise"
    assert decide_verdict({"hard_fails": [], "total": 60}, 0) == "block"


def test_hard_fail_blocks_even_at_high_score():
    c = annotate_critique(
        {"hard_fails": [], "total": 95, "soft_scores": {}, "issues": []},
        integrity_fails=["Sign-off uses a different product line than the active profile."],
        alignment_fails=[],
    )
    assert decide_verdict(c, 0) == "revise"
    assert decide_verdict(c, 1) == "block"


def test_quality_sendable_constant():
    assert QUALITY_SENDABLE == 80
