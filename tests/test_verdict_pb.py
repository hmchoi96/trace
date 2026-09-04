from main import (
    PRODUCT_PROFILES,
    REVIEW_THRESHOLD_PB_MIN,
    _decide_verdict,
)

_PROFILE_PV = PRODUCT_PROFILES["problem_validation"]


def test_pass_at_80_unified():
    c = {"hard_fails": [], "total": 80, "integrity_fails": [], "alignment_fails": []}
    assert _decide_verdict(c, 0, _PROFILE_PV) == "pass"


def test_pass_at_90():
    c = {"hard_fails": [], "total": 90, "integrity_fails": [], "alignment_fails": []}
    assert _decide_verdict(c, 0, _PROFILE_PV) == "pass"


def test_pass_86_no_longer_review():
    c = {"hard_fails": [], "total": 86, "integrity_fails": [], "alignment_fails": []}
    assert _decide_verdict(c, 0, _PROFILE_PV) == "pass"


def test_revise_70_to_79():
    c = {"hard_fails": [], "total": 75, "integrity_fails": [], "alignment_fails": []}
    assert _decide_verdict(c, 0, _PROFILE_PV) == "revise"


def test_block_below_70():
    c = {"hard_fails": [], "total": 60, "integrity_fails": [], "alignment_fails": []}
    assert _decide_verdict(c, 0, _PROFILE_PV) == "block"


def test_revise_when_hard_fail():
    c = {
        "hard_fails": ["invents facts"],
        "total": 95,
        "integrity_fails": ["invents facts"],
        "alignment_fails": [],
    }
    assert _decide_verdict(c, 0, _PROFILE_PV) == "revise"


def test_block_after_max_attempts_with_hard_fail():
    c = {
        "hard_fails": ["still bad"],
        "total": 95,
        "integrity_fails": ["still bad"],
        "alignment_fails": [],
    }
    assert _decide_verdict(c, 1, _PROFILE_PV) == "block"


def test_review_threshold_alias_is_sendable_floor():
    assert REVIEW_THRESHOLD_PB_MIN == 80


def test_akashic_product_name_is_not_wiserbond():
    assert PRODUCT_PROFILES["akashic"]["product_name"] == "Akashic Record"
    assert "Wiserbond" not in PRODUCT_PROFILES["akashic"]["product_name"]
