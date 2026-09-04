"""Contracts for shared first-touch drafting philosophy (style ≠ product)."""

from __future__ import annotations

from main import (
    QUESTION_RULES,
    TRACE_STRATEGY_WORD_WARN_HI,
    _build_pb_system_prompt,
    _build_system_prompt,
    _profile_research_constraints_block,
)
from trace_first_touch import FIRST_TOUCH_WORD_MAX
from trace_strategy_prompt import (
    TRACE_STRATEGY_SYSTEM_PROMPT,
    build_trace_strategy_system_prompt,
)
from trace_style_prompts import (
    CRITIQUE_PLAIN_TEMPLATE,
    CRITIQUE_SHORT_TEMPLATE,
    DRAFTING_PLAIN_TEMPLATE,
    DRAFTING_SHORT_TEMPLATE,
)


def test_all_modes_share_75_word_ceiling():
    assert TRACE_STRATEGY_WORD_WARN_HI == FIRST_TOUCH_WORD_MAX == 75


def test_strategy_prompt_compresses_output_not_reasoning():
    text = TRACE_STRATEGY_SYSTEM_PROMPT
    assert "smallest message that can earn the reply" in text
    assert "Never exceed 75 words" in text
    assert "60 to 110" not in text
    assert "Generate 3 subject line options" in text

    built = build_trace_strategy_system_prompt(
        product_context="Product X",
        sign_off="Jamie\nbuilding Helix",
    )
    assert "FIRST-TOUCH PHILOSOPHY" in built
    assert "FIRST-TOUCH WRITING RULES" in built


def test_short_and_plain_templates_are_product_agnostic():
    assert "Helix" not in DRAFTING_SHORT_TEMPLATE
    assert "Helix" not in DRAFTING_PLAIN_TEMPLATE
    assert "Akashic" not in DRAFTING_SHORT_TEMPLATE
    assert "STYLE: SHORT DISCOVERY" in DRAFTING_SHORT_TEMPLATE
    assert "STYLE: CAUTIOUS HYPOTHESIS" in DRAFTING_PLAIN_TEMPLATE
    assert "{product_name}" in DRAFTING_SHORT_TEMPLATE
    assert "{product_name}" in DRAFTING_PLAIN_TEMPLATE


def test_short_plain_prompts_inject_profile_product_only():
    helix = {
        "product_name": "Helix",
        "product_context": "cold call scripts mid-call",
        "email_mode": "problem_validation_email",
        "sign_off": "Jamie\nbuilding Helix",
    }
    short = _build_pb_system_prompt(helix)
    assert "Helix" in short
    assert "STYLE: SHORT DISCOVERY" in short
    assert "30–55" in short

    akashic = {
        "product_name": "Akashic",
        "product_context": "decision memory",
        "email_mode": "problem_validation_email",
        "sign_off": "Jamie\nWiserbond",
    }
    ak_short = _build_pb_system_prompt(akashic)
    assert "Akashic" in ak_short
    assert "Helix" not in ak_short
    assert "STYLE: SHORT DISCOVERY" in ak_short

    plain = dict(akashic)
    plain["email_mode"] = "anti_ai_email"
    plain_prompt = _build_pb_system_prompt(plain)
    assert "STYLE: CAUTIOUS HYPOTHESIS" in plain_prompt
    assert "25–50" in plain_prompt
    assert "Helix" not in plain_prompt


def test_critique_templates_are_not_product_forks():
    short = CRITIQUE_SHORT_TEMPLATE.format(
        product_name="Akashic",
        sign_off="Jamie\nWiserbond",
    )
    plain = CRITIQUE_PLAIN_TEMPLATE.format(
        product_name="Helix",
        sign_off="Jamie\nbuilding Helix",
    )
    assert "Akashic" in short
    assert "Jamie\nWiserbond" in short
    assert "STYLE CHECKS (Short Discovery)" in short
    assert "STYLE CHECKS (Cautious Hypothesis)" in plain
    assert "building Helix" in plain  # only because it is this profile's sign_off
    assert "evidence_distance" in short
    assert "opening_relevance" in short
    assert "usable / sendable" in short


def test_legacy_relaxes_sentence_count_and_adds_barbara_guards():
    prompt = _build_system_prompt(
        "yesno",
        {
            "product_name": "Akashic",
            "product_context": "decision memory",
            "sign_off": "Jamie\nWiserbond",
        },
    )
    assert "exactly 2 sentences" not in prompt
    assert "30–50" in prompt
    assert "one level beyond the source" in prompt
    assert "yes, actually" not in QUESTION_RULES["yesno"]
    assert "materially improve" in QUESTION_RULES["open"]


def test_constraints_block_is_shared_not_helix_forked():
    blk = _profile_research_constraints_block(
        {
            "email": "a@b.com",
            "outreach_role": "Expert / Researcher",
            "recommended_ask": "validate_problem_interpretation",
        },
        {"product_name": "Akashic", "product_context": "c", "sign_off": "X\nY"},
        style="short",
    )
    assert "Akashic" in blk
    assert "Helix" not in blk
    assert "recommended_ask" in blk
    assert "cold calling myself" not in blk
