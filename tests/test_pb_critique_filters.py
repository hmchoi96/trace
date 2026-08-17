from main import (
    _pb_sign_off_body_acceptable,
    merge_pb_hard_fails_with_local_length,
    strip_pb_signoff_noise_hard_fails,
)


def test_acceptable_two_line_signoff():
    body = "Hi Pat,\n\nDo you relate?\n\nJamie\nbuilding Helix"
    assert _pb_sign_off_body_acceptable(body) is True


def test_strip_sign_off_only_hard_fail():
    body = "Hi\n\nQ?\n\nHyunmyung\nbuilding Helix"
    fails = ["sign-off mismatch", "wrong verbatim sign-off"]
    assert strip_pb_signoff_noise_hard_fails(body, fails) == []


def test_keep_non_signoff_hard_fail():
    body = "Hi\n\nQ?\n\nJamie\nbuilding Helix"
    fails = ["invents funding round not in FACTS"]
    assert strip_pb_signoff_noise_hard_fails(body, fails) == fails


def test_merge_drops_false_length_fail_when_local_under_limit():
    core = " ".join(["hello"] * 50)
    body = f"Hi Sam,\n\n{core}\n\nJamie\nbuilding Helix"
    out, meta = merge_pb_hard_fails_with_local_length(
        body,
        "Sam",
        ["Hard fail: body exceeds 75-word limit (reviewer count 76)"],
    )
    assert meta["body_word_count"] == 50
    assert out == []


def test_merge_adds_deterministic_length_fail_over_75():
    core = " ".join(["hello"] * 76)
    body = f"Hi Sam,\n\n{core}\n\nJamie\nbuilding Helix"
    out, meta = merge_pb_hard_fails_with_local_length(body, "Sam", [])
    assert meta["body_word_count"] == 76
    assert any("deterministic count" in x.lower() for x in out)


def test_merge_strategy_mode_allows_up_to_130():
    core = " ".join(["hello"] * 100)
    body = f"Hi Sam,\n\n{core}\n\nJamie\nbuilding Helix"
    out, meta = merge_pb_hard_fails_with_local_length(
        body, "Sam", [], warn_hi=130,
    )
    assert meta["body_word_count"] == 100
    assert out == []
