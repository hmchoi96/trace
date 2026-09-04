from main import (
    _pb_sign_off_body_acceptable,
    merge_pb_hard_fails_with_local_length,
    strip_cross_product_signoff_hard_fails,
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


def test_merge_strategy_mode_also_caps_at_75():
    core = " ".join(["hello"] * 100)
    body = f"Hi Sam,\n\n{core}\n\nJamie\nbuilding Helix"
    out, meta = merge_pb_hard_fails_with_local_length(
        body, "Sam", [], warn_hi=75,
    )
    assert meta["body_word_count"] == 100
    assert any("deterministic count" in x.lower() for x in out)


def test_merge_strategy_mode_allows_under_75():
    core = " ".join(["hello"] * 70)
    body = f"Hi Sam,\n\n{core}\n\nJamie\nbuilding Helix"
    out, meta = merge_pb_hard_fails_with_local_length(
        body, "Sam", [], warn_hi=75,
    )
    assert meta["body_word_count"] == 70
    assert out == []


def test_non_helix_drops_building_helix_signoff_hard_fail():
    fails = [
        "No closing line indicating 'building Helix' or 'Helix by Wiserbond' — "
        "signature shows only name and company.",
        "Invents facts not supported by the FACTS block.",
    ]
    out = strip_cross_product_signoff_hard_fails(
        fails,
        helix=False,
        required_sign_off="Jamie Choi\nWiserbond Technologies Inc.",
    )
    assert out == ["Invents facts not supported by the FACTS block."]


def test_helix_keeps_building_helix_signoff_hard_fail():
    fails = [
        "Missing required second line 'building Helix'",
    ]
    out = strip_cross_product_signoff_hard_fails(
        fails,
        helix=True,
        required_sign_off="Jamie\nbuilding Helix",
    )
    assert out == fails


def test_merge_strips_helix_signoff_fail_for_akashic_body():
    body = (
        "Hi Sam,\n\nShort note about decision memory.\n\n"
        "Jamie Choi\nWiserbond Technologies Inc."
    )
    out, _meta = merge_pb_hard_fails_with_local_length(
        body,
        "Sam",
        [
            "No closing line 'building Helix' or 'Helix by Wiserbond' present.",
        ],
        helix=False,
    )
    assert out == []
