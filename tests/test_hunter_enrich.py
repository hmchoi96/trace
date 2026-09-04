"""Hunter.io email finder helpers. No live Hunter calls."""

from __future__ import annotations

import hunter_enrich


def test_split_name_from_full_name():
    first, last = hunter_enrich.split_name({"name": "Ben Cole"})
    assert first == "Ben"
    assert last == "Cole"


def test_linkedin_handle_from_url():
    handle = hunter_enrich.linkedin_handle(
        {"linkedin_url": "https://uk.linkedin.com/in/ben-cole-62439664"}
    )
    assert handle == "ben-cole-62439664"


def test_candidate_to_hunter_params_prefers_company_and_name():
    params = hunter_enrich.candidate_to_hunter_params(
        {
            "name": "Ben Cole",
            "company": "FPE Capital",
            "linkedin_url": "https://uk.linkedin.com/in/ben-cole-62439664",
        }
    )
    assert params["first_name"] == "Ben"
    assert params["last_name"] == "Cole"
    assert params["company"] == "FPE Capital"
    assert params["linkedin_handle"] == "ben-cole-62439664"


def test_apply_hunter_email_sets_source():
    rec = {"email": ""}
    changed = hunter_enrich.apply_hunter_email(
        rec,
        {"email": "ben.cole@fpecapital.com", "position": "Director"},
    )
    assert changed is True
    assert rec["email"] == "ben.cole@fpecapital.com"
    assert rec["email_source"] == "Hunter.io"


def test_find_email_uses_api(monkeypatch):
    class FakeResp:
        status_code = 200

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"data": {"email": "dana@fund.com", "score": 91}}

    monkeypatch.setenv("HUNTER_API_KEY", "test-key")
    monkeypatch.setattr(
        "hunter_enrich.requests.get",
        lambda *args, **kwargs: FakeResp(),
    )
    data = hunter_enrich.find_email({"name": "Dana Reed", "company": "Northline"})
    assert data["email"] == "dana@fund.com"
