"""Hunter.io email finder. Second pass after Apollo."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

load_dotenv()

HUNTER_API_URL = "https://api.hunter.io/v2"


def hunter_ready() -> bool:
    return bool((os.getenv("HUNTER_API_KEY") or "").strip())


def _api_key() -> str:
    key = os.getenv("HUNTER_API_KEY") or ""
    if not key:
        raise EnvironmentError("HUNTER_API_KEY is missing from .env")
    return key


def split_name(rec: dict[str, Any]) -> tuple[str, str]:
    first = str(rec.get("first_name") or "").strip()
    last = str(rec.get("last_name") or "").strip()
    if first or last:
        return first, last
    full = str(rec.get("name") or rec.get("author_name") or "").strip()
    if not full:
        return "", ""
    parts = full.split(None, 1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def linkedin_handle(rec: dict[str, Any]) -> str:
    url = str(rec.get("linkedin_url") or rec.get("linkedin") or "").strip()
    if not url:
        return ""
    path = urlparse(url).path.strip("/")
    if path.startswith("in/"):
        return path[3:].strip("/").split("/")[0]
    return path.split("/")[0] if path else ""


def candidate_to_hunter_params(rec: dict[str, Any]) -> dict[str, str]:
    """Build Hunter Email Finder query params from a Trace candidate."""
    params: dict[str, str] = {}
    first, last = split_name(rec)
    if first:
        params["first_name"] = first
    if last:
        params["last_name"] = last
    company = str(rec.get("company") or "").strip()
    if company and company.lower() != "unknown":
        params["company"] = company
    handle = linkedin_handle(rec)
    if handle:
        params["linkedin_handle"] = handle
    domain = str(rec.get("domain") or rec.get("company_domain") or "").strip()
    if domain:
        params["domain"] = domain.replace("https://", "").replace("http://", "").split("/")[0]
    return params


def can_lookup(rec: dict[str, Any]) -> bool:
    if str(rec.get("email") or "").strip():
        return False
    params = candidate_to_hunter_params(rec)
    if not (params.get("domain") or params.get("company") or params.get("linkedin_handle")):
        return False
    if params.get("linkedin_handle"):
        return True
    return bool(params.get("first_name") and params.get("last_name"))


def find_email(rec: dict[str, Any], *, session: Any | None = None) -> dict[str, Any] | None:
    """Return Hunter data payload or None when no email is found."""
    if not hunter_ready() or not can_lookup(rec):
        return None
    params = candidate_to_hunter_params(rec)
    req = session or requests
    resp = req.get(
        f"{HUNTER_API_URL}/email-finder",
        params={**params, "api_key": _api_key()},
        timeout=30,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    body = resp.json()
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        return None
    email = str(data.get("email") or "").strip()
    if not email:
        return None
    return data


def apply_hunter_email(rec: dict[str, Any], data: dict[str, Any]) -> bool:
    email = str(data.get("email") or "").strip()
    if not email or str(rec.get("email") or "").strip():
        return False
    rec["email"] = email
    rec["email_found"] = True
    rec["email_source"] = "Hunter.io"
    rec["enrichment_attempted"] = True
    position = str(data.get("position") or "").strip()
    if position and not rec.get("title"):
        rec["title"] = position
    return True
