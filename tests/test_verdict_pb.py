from main import (
    PRODUCT_PROFILES,
    REVIEW_THRESHOLD_PB_MIN,
    _decide_verdict,
)

_PROFILE_PV = PRODUCT_PROFILES["problem_validation"]


def test_pass_at_90():
    c = {"hard_fails": [], "total": 90}
    assert _decide_verdict(c, 0, _PROFILE_PV) == "pass"


def test_review_80_to_89():
    c = {"hard_fails": [], "total": 86}
    assert _decide_verdict(c, 0, _PROFILE_PV) == "review"


def test_block_below_80():
    c = {"hard_fails": [], "total": 72}
    assert _decide_verdict(c, 0, _PROFILE_PV) == "block"


def test_revise_when_hard_fail():
    c = {"hard_fails": ["invents facts"], "total": 95}
    assert _decide_verdict(c, 0, _PROFILE_PV) == "revise"


def test_block_after_max_attempts_with_hard_fail():
    c = {"hard_fails": ["still bad"], "total": 95}
    assert _decide_verdict(c, 1, _PROFILE_PV) == "block"


def test_review_boundary_80():
    c = {"hard_fails": [], "total": REVIEW_THRESHOLD_PB_MIN}
    assert _decide_verdict(c, 0, _PROFILE_PV) == "review"
