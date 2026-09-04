"""Apollo people match + phone reveal. Polls webhook_result; no inbound webhook."""

from __future__ import annotations

import csv
import os
import time
from typing import Any
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

load_dotenv()

APOLLO_API_URL = "https://api.apollo.io/api/v1"
BULK_MATCH_LIMIT = 10
DEFAULT_PHONE_WEBHOOK = "https://example.com/apollo-phone-webhook"
PHONE_POLL_SECONDS = 180

_MOBILE_TYPES = {"mobile", "mobile_phone", "cell", "cellphone"}
_DIRECT_TYPES = {"work_direct", "work", "direct", "direct_dial", "office"}


def _api_key() -> str:
    key = os.getenv("APOLLO_API_KEY") or ""
    if not key:
        raise EnvironmentError("APOLLO_API_KEY is missing from .env")
    return key


def _headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Cache-Control": "no-cache",
        "X-Api-Key": _api_key(),
    }


def details_from_csv_row(row: dict[str, str]) -> dict[str, str]:
    """Identifiers Apollo can match. Prefer person id, then LinkedIn/email/name."""
    details: dict[str, str] = {}
    person_id = (row.get("Apollo Contact Id") or "").strip()
    if person_id:
        details["id"] = person_id
    linkedin = (row.get("Person Linkedin Url") or "").strip()
    if linkedin:
        details["linkedin_url"] = linkedin
    email = (row.get("Email") or "").strip()
    if email:
        details["email"] = email
    first = (row.get("First Name") or "").strip()
    last = (row.get("Last Name") or "").strip()
    if first:
        details["first_name"] = first
    if last:
        details["last_name"] = last
    org = (row.get("Company Name") or row.get("Company Name for Emails") or "").strip()
    if org:
        details["organization_name"] = org
    website = (row.get("Website") or "").strip()
    if website:
        details["domain"] = website.replace("https://", "").replace("http://", "").split("/")[0]
    return details


def _phone_type(entry: dict[str, Any]) -> str:
    raw = str(entry.get("type_cd") or entry.get("type") or "").strip().lower()
    return raw.replace(" ", "_")


def _phone_number(entry: dict[str, Any]) -> str:
    return str(
        entry.get("sanitized_number") or entry.get("raw_number") or ""
    ).strip()


def pick_phones(entries: list[Any] | None) -> dict[str, str]:
    """Split Apollo phone_numbers into mobile / work_direct / other."""
    mobile = ""
    direct = ""
    other: list[str] = []
    seen: set[str] = set()
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        number = _phone_number(entry)
        if not number or number in seen:
            continue
        status = str(entry.get("status_cd") or entry.get("status") or "").lower()
        if status in ("invalid_number", "invalid"):
            continue
        seen.add(number)
        kind = _phone_type(entry)
        if kind in _MOBILE_TYPES and not mobile:
            mobile = number
        elif kind in _DIRECT_TYPES and not direct:
            direct = number
        else:
            other.append(number)
    leftover = [n for n in other if n not in (mobile, direct)]
    if not mobile and leftover:
        mobile = leftover.pop(0)
    if not direct and leftover:
        direct = leftover.pop(0)
    return {
        "mobile": mobile,
        "work_direct": direct,
        "other": leftover[0] if leftover else "",
    }


def _chunk(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def usable_email(value: str) -> str:
    email = (value or "").strip()
    if not email or "@" not in email:
        return ""
    if email.lower() in ("unavailable", "not_found", "null", "none"):
        return ""
    return email


def emails_from_matches(payload: dict[str, Any]) -> list[str]:
    """Emails aligned with bulk_match `matches` order. Blank if unmatched."""
    matches = payload.get("matches") if isinstance(payload.get("matches"), list) else []
    out: list[str] = []
    for person in matches:
        if not isinstance(person, dict):
            out.append("")
            continue
        email = usable_email(str(person.get("email") or ""))
        if not email:
            extras = person.get("emails") or person.get("contact_emails") or []
            if isinstance(extras, list):
                for item in extras:
                    if isinstance(item, str):
                        email = usable_email(item)
                    elif isinstance(item, dict):
                        email = usable_email(str(item.get("email") or item.get("address") or ""))
                    if email:
                        break
        out.append(email)
    return out


def bulk_match_people(
    details: list[dict[str, str]],
    *,
    reveal_phone_number: bool = True,
    reveal_personal_emails: bool = False,
    webhook_url: str | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    if not details:
        return {"matches": [], "request_id": None}
    if len(details) > BULK_MATCH_LIMIT:
        raise ValueError(f"bulk_match accepts at most {BULK_MATCH_LIMIT} people")
    params: dict[str, str] = {
        "reveal_personal_emails": "true" if reveal_personal_emails else "false",
        "reveal_phone_number": "true" if reveal_phone_number else "false",
    }
    if reveal_phone_number:
        params["webhook_url"] = (
            webhook_url
            or os.getenv("APOLLO_PHONE_WEBHOOK_URL")
            or DEFAULT_PHONE_WEBHOOK
        )
    url = f"{APOLLO_API_URL}/people/bulk_match?{urlencode(params)}"
    response = requests.post(
        url,
        headers=_headers(),
        json={"details": details},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Apollo bulk_match returned a non-object")
    return payload


def poll_webhook_result(
    request_id: str | int,
    *,
    timeout_sec: int = PHONE_POLL_SECONDS,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """GET /webhook_result/{request_id} until ready or timeout."""
    rid = str(request_id)
    url = f"{APOLLO_API_URL}/webhook_result/{rid}"
    http = session or requests
    deadline = time.time() + timeout_sec
    last: dict[str, Any] = {}
    while time.time() < deadline:
        response = http.get(url, headers=_headers(), timeout=30)
        try:
            last = response.json() if response.content else {}
        except ValueError:
            last = {"raw": response.text, "status_code": response.status_code}
        if not isinstance(last, dict):
            last = {"payload": last, "status_code": response.status_code}
        if response.status_code == 200:
            return last
        err = str(last.get("error_code") or "")
        if response.status_code in (400, 410) or err in (
            "request_id_unknown",
            "request_id_expired",
            "invalid_request_id",
        ):
            raise RuntimeError(f"Apollo webhook poll failed: {last}")
        wait = last.get("retry_after_seconds")
        try:
            sleep_for = max(1, int(wait)) if wait is not None else 10
        except (TypeError, ValueError):
            sleep_for = 10
        sleep_for = min(sleep_for, max(1, int(deadline - time.time())))
        time.sleep(sleep_for)
    raise TimeoutError(f"Apollo phone webhook still pending after {timeout_sec}s: {rid}")


def phones_from_webhook(payload: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Map Apollo person id -> picked phones from a poll payload."""
    inner = payload.get("webhook_result")
    if not isinstance(inner, dict):
        inner = payload
    people = inner.get("people") if isinstance(inner, dict) else None
    out: dict[str, dict[str, str]] = {}
    if not isinstance(people, list):
        return out
    for person in people:
        if not isinstance(person, dict):
            continue
        pid = str(person.get("id") or "").strip()
        if not pid:
            continue
        out[pid] = pick_phones(person.get("phone_numbers"))
    return out


def _canonical_linkedin(url: str) -> str:
    return (url or "").strip().rstrip("/").lower().split("?")[0]


def index_match_people(payload: dict[str, Any]) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    """Return (phones_by_id, alias_to_id) from a bulk_match payload."""
    matches = payload.get("matches")
    phones_by_id: dict[str, dict[str, str]] = {}
    alias_to_id: dict[str, str] = {}
    if not isinstance(matches, list):
        return phones_by_id, alias_to_id
    for person in matches:
        if not isinstance(person, dict):
            continue
        pid = str(person.get("id") or "").strip()
        if not pid:
            continue
        phones_by_id[pid] = pick_phones(person.get("phone_numbers"))
        email = str(person.get("email") or "").strip().lower()
        if email:
            alias_to_id[f"email:{email}"] = pid
        linkedin = _canonical_linkedin(str(person.get("linkedin_url") or ""))
        if linkedin:
            alias_to_id[f"li:{linkedin}"] = pid
    return phones_by_id, alias_to_id


def lookup_row_phones(
    row: dict[str, str],
    phones_by_id: dict[str, dict[str, str]],
    alias_to_id: dict[str, str],
) -> dict[str, str]:
    pid = (row.get("Apollo Contact Id") or "").strip()
    if pid and pid in phones_by_id:
        return phones_by_id[pid]
    mapped = alias_to_id.get(f"contact:{pid}") if pid else ""
    if mapped and mapped in phones_by_id:
        return phones_by_id[mapped]
    email = (row.get("Email") or "").strip().lower()
    mapped = alias_to_id.get(f"email:{email}") if email else ""
    if mapped and mapped in phones_by_id:
        return phones_by_id[mapped]
    linkedin = _canonical_linkedin(row.get("Person Linkedin Url") or "")
    mapped = alias_to_id.get(f"li:{linkedin}") if linkedin else ""
    if mapped and mapped in phones_by_id:
        return phones_by_id[mapped]
    return {}


def apply_phones_to_row(row: dict[str, str], phones: dict[str, str]) -> dict[str, str]:
    updated = dict(row)
    if phones.get("mobile") and not (updated.get("Mobile Phone") or "").strip():
        updated["Mobile Phone"] = phones["mobile"]
    if phones.get("work_direct") and not (updated.get("Work Direct Phone") or "").strip():
        updated["Work Direct Phone"] = phones["work_direct"]
    if phones.get("other") and not (updated.get("Other Phone") or "").strip():
        updated["Other Phone"] = phones["other"]
    return updated


def enrich_csv_phones(
    csv_path: str,
    out_path: str,
    *,
    limit: int | None = None,
    start: int = 1,
    reveal_phone_number: bool = True,
    poll_timeout_sec: int = PHONE_POLL_SECONDS,
    progress: Any | None = print,
) -> dict[str, int]:
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if not fieldnames:
        raise ValueError(f"no header in {csv_path}")
    eligible = rows[max(0, start - 1) :]
    if limit is not None:
        eligible = eligible[:limit]
    batches = _chunk(eligible, BULK_MATCH_LIMIT)
    phones_by_id: dict[str, dict[str, str]] = {}
    alias_to_id: dict[str, str] = {}
    stats = {
        "requested": len(eligible),
        "matched": 0,
        "mobile": 0,
        "work_direct": 0,
        "other": 0,
        "batches": len(batches),
    }
    for i, batch in enumerate(batches, 1):
        details = [details_from_csv_row(row) for row in batch]
        if progress:
            progress(f"[{i}/{len(batches)}] Apollo bulk_match {len(details)} people")
        payload = bulk_match_people(details, reveal_phone_number=reveal_phone_number)
        batch_phones, batch_alias = index_match_people(payload)
        phones_by_id.update(batch_phones)
        alias_to_id.update(batch_alias)
        matches = payload.get("matches") if isinstance(payload.get("matches"), list) else []
        for row, person in zip(batch, matches):
            if not isinstance(person, dict):
                continue
            person_id = str(person.get("id") or "").strip()
            csv_id = (row.get("Apollo Contact Id") or "").strip()
            if person_id and csv_id:
                alias_to_id[f"contact:{csv_id}"] = person_id
            email = (row.get("Email") or "").strip().lower()
            if person_id and email:
                alias_to_id[f"email:{email}"] = person_id
            linkedin = _canonical_linkedin(row.get("Person Linkedin Url") or "")
            if person_id and linkedin:
                alias_to_id[f"li:{linkedin}"] = person_id
        request_id = payload.get("request_id")
        if reveal_phone_number and request_id not in (None, ""):
            if progress:
                progress(f"    polling webhook_result {request_id}")
            webhook = poll_webhook_result(request_id, timeout_sec=poll_timeout_sec)
            phones_by_id.update(phones_from_webhook(webhook))
        elif reveal_phone_number and progress:
            progress("    no request_id; using sync match phones only")
    requested_ids = {(row.get("Apollo Contact Id") or "").strip() for row in eligible}
    updated_rows: list[dict[str, str]] = []
    for row in rows:
        phones = lookup_row_phones(row, phones_by_id, alias_to_id)
        new_row = apply_phones_to_row(row, phones)
        in_request = (row.get("Apollo Contact Id") or "").strip() in requested_ids
        if in_request and any(phones.values()):
            stats["matched"] += 1
        if in_request:
            if (new_row.get("Mobile Phone") or "").strip() and not (row.get("Mobile Phone") or "").strip():
                stats["mobile"] += 1
            if (new_row.get("Work Direct Phone") or "").strip() and not (row.get("Work Direct Phone") or "").strip():
                stats["work_direct"] += 1
            if (new_row.get("Other Phone") or "").strip() and not (row.get("Other Phone") or "").strip():
                stats["other"] += 1
        updated_rows.append(new_row)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(updated_rows)
    return stats
