"""Unit tests for Apollo lead parsing and segment classification."""

from segmentation import (
    SEG_EARLY_TEAM,
    SEG_FOUNDER_ENGINEER,
    SEG_FOUNDER_LED,
    SEG_SALES_LEADER,
    classify_segment,
    normalize_csv_row,
    pb_body_length_analysis,
    subject_line_hint_for_segment,
)


def test_normalize_minimal_row():
    row = {
        "First Name": "Jane",
        "Last Name": "Doe",
        "Title": "VP Sales",
        "Company Name for Emails": "Acme",
        "Email": "j@acme.com",
        "Industry": "SaaS",
        "Keywords": "b2b",
        "# Employees": "55",
        "Latest Funding": "Series A",
        "Departments": "Sales",
        "Sub Departments": "Sales",
        "Seniority": "Vp",
        "City": "SF",
        "State": "CA",
        "Country": "USA",
        "Person Linkedin Url": "https://linkedin.com/in/j",
        "Website": "https://acme.com",
        "Technologies": "Python",
        "Qualify Contact": "",
    }
    lead = normalize_csv_row(row)
    assert lead["first_name"] == "Jane"
    assert lead["employee_count"] == 55
    assert "Sales" in lead["department"]
    assert "SF" in lead["location"]


def test_vp_sales_is_sales_leader():
    lead = normalize_csv_row(
        {
            "First Name": "A",
            "Last Name": "B",
            "Title": "VP of Sales",
            "Company Name for Emails": "Co",
            "Email": "a@co.com",
            "Qualify Contact": "",
        }
    )
    seg, reason = classify_segment(lead)
    assert seg == SEG_SALES_LEADER
    assert "R1" in reason


def test_founding_engineer_segment():
    lead = normalize_csv_row(
        {
            "First Name": "S",
            "Last Name": "F",
            "Title": "Founding Engineer",
            "Company Name for Emails": "X",
            "Email": "s@x.com",
            "Seniority": "Founder",
            "Qualify Contact": "",
        }
    )
    seg, _ = classify_segment(lead)
    assert seg == SEG_FOUNDER_ENGINEER


def test_seed_employee_early_team():
    lead = normalize_csv_row(
        {
            "First Name": "A",
            "Last Name": "B",
            "Title": "CEO",
            "Company Name for Emails": "Y",
            "Email": "a@y.com",
            "# Employees": "12",
            "Latest Funding": "Seed",
            "Seniority": "Founder",
            "Qualify Contact": "",
        }
    )
    seg, reason = classify_segment(lead)
    assert seg == SEG_EARLY_TEAM
    assert "R4" in reason


def test_subject_line_hint_by_segment():
    assert (
        subject_line_hint_for_segment(SEG_SALES_LEADER)
        == "script search during ramp"
    )
    assert (
        subject_line_hint_for_segment(SEG_FOUNDER_ENGINEER)
        == "finding the line mid-call"
    )


def test_pb_length_excludes_greeting_and_signoff():
    body = (
        "Hi Pat,\n\nOne two three four five.\n\n"
        "Question here?\n\n"
        "Jamie\nbuilding Helix"
    )
    m = pb_body_length_analysis(body, "Pat")
    assert m["body_word_count"] == 7
    assert m["length_status"] == "short"


def test_pb_length_warning_band():
    core = " ".join(["word"] * 66)
    body = f"Hi X,\n\n{core}\n\nJamie\nbuilding Helix"
    m = pb_body_length_analysis(body, "X")
    assert m["body_word_count"] == 66
    assert m["length_status"] == "warning"


def test_pb_length_hard_fail_over_75():
    core = " ".join(["word"] * 76)
    body = f"Hi X,\n\n{core}\n\nJamie\nbuilding Helix"
    m = pb_body_length_analysis(body, "X")
    assert m["body_word_count"] == 76
    assert m["length_status"] == "hard_fail"


def test_default_founder_led():
    lead = normalize_csv_row(
        {
            "First Name": "C",
            "Last Name": "D",
            "Title": "Co-Founder",
            "Company Name for Emails": "Z",
            "Email": "c@z.com",
            "Seniority": "Founder",
            "Qualify Contact": "",
        }
    )
    seg, reason = classify_segment(lead)
    assert seg == SEG_FOUNDER_LED
    assert "R5" in reason or "R6" in reason
