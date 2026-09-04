"""Apollo phone enrich helpers. No live Apollo calls."""

from __future__ import annotations

import csv
from pathlib import Path

from apollo_enrich import (
    apply_phones_to_row,
    details_from_csv_row,
    emails_from_matches,
    index_match_people,
    lookup_row_phones,
    phones_from_webhook,
    pick_phones,
    usable_email,
)


def test_pick_phones_splits_mobile_and_direct():
    phones = pick_phones([
        {"sanitized_number": "+14155550111", "type_cd": "mobile", "status_cd": "valid_number"},
        {"sanitized_number": "+14155550112", "type_cd": "work_direct"},
        {"raw_number": "+1 415-555-0113", "type": "other"},
    ])
    assert phones["mobile"] == "+14155550111"
    assert phones["work_direct"] == "+14155550112"
    assert phones["other"] == "+1 415-555-0113"


def test_pick_phones_skips_invalid_and_uses_leftover():
    phones = pick_phones([
        {"sanitized_number": "+1000", "status_cd": "invalid_number", "type_cd": "mobile"},
        {"sanitized_number": "+14155550999", "type_cd": "unknown"},
    ])
    assert phones["mobile"] == "+14155550999"
    assert phones["work_direct"] == ""


def test_details_from_csv_row_uses_apollo_id():
    details = details_from_csv_row({
        "Apollo Contact Id": "abc123",
        "First Name": "Jane",
        "Last Name": "Doe",
        "Email": "jane@acme.com",
        "Person Linkedin Url": "http://www.linkedin.com/in/jane",
        "Company Name": "Acme",
        "Website": "https://acme.com/about",
    })
    assert details["id"] == "abc123"
    assert details["email"] == "jane@acme.com"
    assert details["domain"] == "acme.com"


def test_lookup_uses_contact_alias_after_webhook():
    payload = {
        "matches": [{
            "id": "person-1",
            "email": "jane@acme.com",
            "linkedin_url": "https://www.linkedin.com/in/jane",
            "phone_numbers": [],
        }]
    }
    phones_by_id, alias_to_id = index_match_people(payload)
    alias_to_id["contact:csv-id"] = "person-1"
    phones_by_id.update(phones_from_webhook({
        "webhook_result": {
            "people": [{
                "id": "person-1",
                "phone_numbers": [
                    {"sanitized_number": "+14155550100", "type_cd": "mobile"},
                ],
            }]
        }
    }))
    row = {
        "Apollo Contact Id": "csv-id",
        "Email": "jane@acme.com",
        "Person Linkedin Url": "https://www.linkedin.com/in/jane/",
        "Mobile Phone": "",
    }
    phones = lookup_row_phones(row, phones_by_id, alias_to_id)
    filled = apply_phones_to_row(row, phones)
    assert filled["Mobile Phone"] == "+14155550100"


def test_apply_phones_does_not_overwrite_existing():
    row = {"Mobile Phone": "+1 already", "Work Direct Phone": ""}
    filled = apply_phones_to_row(row, {"mobile": "+1 new", "work_direct": "+1 direct", "other": ""})
    assert filled["Mobile Phone"] == "+1 already"
    assert filled["Work Direct Phone"] == "+1 direct"


def test_enrich_csv_phones_writes_output(tmp_path: Path, monkeypatch):
    from apollo_enrich import enrich_csv_phones

    src = tmp_path / "leads.csv"
    with src.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=[
            "First Name", "Last Name", "Email", "Apollo Contact Id",
            "Mobile Phone", "Work Direct Phone", "Other Phone",
            "Person Linkedin Url",
        ])
        writer.writeheader()
        writer.writerow({
            "First Name": "Jane",
            "Last Name": "Doe",
            "Email": "jane@acme.com",
            "Apollo Contact Id": "csv-id",
            "Mobile Phone": "",
            "Work Direct Phone": "",
            "Other Phone": "",
            "Person Linkedin Url": "https://www.linkedin.com/in/jane",
        })

    def fake_bulk(details, **kwargs):
        assert details[0]["id"] == "csv-id"
        return {
            "request_id": "111",
            "matches": [{
                "id": "person-1",
                "email": "jane@acme.com",
                "linkedin_url": "https://www.linkedin.com/in/jane",
                "phone_numbers": [],
            }],
        }

    def fake_poll(request_id, **kwargs):
        assert str(request_id) == "111"
        return {
            "webhook_result": {
                "people": [{
                    "id": "person-1",
                    "phone_numbers": [
                        {"sanitized_number": "+14155550888", "type_cd": "mobile"},
                    ],
                }]
            }
        }

    monkeypatch.setattr("apollo_enrich.bulk_match_people", fake_bulk)
    monkeypatch.setattr("apollo_enrich.poll_webhook_result", fake_poll)
    out = tmp_path / "leads.phones.csv"
    stats = enrich_csv_phones(str(src), str(out), progress=None)
    assert stats["requested"] == 1
    assert stats["mobile"] == 1
    with out.open(encoding="utf-8") as fh:
        row = next(csv.DictReader(fh))
    assert row["Mobile Phone"] == "+14155550888"


def test_usable_email_rejects_placeholders():
    assert usable_email("jane@acme.com") == "jane@acme.com"
    assert usable_email("unavailable") == ""
    assert usable_email("not_found") == ""
    assert usable_email("") == ""


def test_emails_from_matches_aligns_with_nulls():
    emails = emails_from_matches({
        "matches": [
            {"email": "a@co.com"},
            None,
            {"email": "unavailable"},
            {"emails": [{"email": "b@co.com"}]},
        ]
    })
    assert emails == ["a@co.com", "", "", "b@co.com"]

