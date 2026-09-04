"""Upstream signal discovery + qualification. Does not send mail or enrich before human approval."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from typing import Any, Callable

from grok_client import grok_research

_COST_LOG_LOCK = threading.Lock()

RECORD_TYPE = "signal_candidate"

RECOMMENDATIONS = (
    "PRIMARY_PROSPECT",
    "CHAMPION_CANDIDATE",
    "HIGH_VALUE_DISCOVERY",
    "ADJACENT_PRACTITIONER",
    "PAIN_SIGNAL_ONLY",
    "LIKELY_NOT_RELEVANT",
    "UNCLEAR",
    "LIKELY_PROSPECT",
    "LIKELY_NOT_PROSPECT",
)
ACTOR_TYPES = ("PRACTITIONER", "BUILDER_OR_VENDOR", "OTHER", "UNKNOWN")
HUMAN_STATUSES = ("PENDING", "APPROVED", "REJECTED")
REJECT_REASONS = ("vendor", "wrong_role", "not_real_pain", "wrong_company", "other")
RELEVANCE_KEEP = ("relevant", "highly_relevant")
CLASSIFICATION_AXES = (
    "persona_fit",
    "pain_evidence",
    "behavioral_evidence",
    "workaround_evidence",
    "outcome_feedback_evidence",
    "influence_or_champion_potential",
    "economic_buyer_likelihood",
    "end_user_likelihood",
)
AXIS_LEVELS = ("VERY_HIGH", "HIGH", "MEDIUM", "LOW", "UNKNOWN")
DEEPENING_BUDGET = 3
CACHE_TTL_DAYS = 14
_TRACKING_QUERY_KEYS = {
    "gt", "si", "ref", "source", "fbclid", "gclid", "mc_cid", "mc_eid",
}


def discovery_context_from_profile(profile: dict[str, Any]) -> dict[str, Any]:
    d = dict(profile.get("discovery") or {})
    if not d.get("product_name"):
        d["product_name"] = profile.get("product_name") or ""
    if not d.get("what_it_does"):
        d["what_it_does"] = (profile.get("product_context") or "").strip()
    for key in (
        "problems_it_solves",
        "examples_of_problem_signals",
        "obvious_non_targets_or_adjacent_vendors",
    ):
        val = d.get(key)
        if isinstance(val, str):
            d[key] = [val]
        elif not val:
            d[key] = []
    d.setdefault("target_users_or_buyers", "")
    d.setdefault("search_guidance", "")
    d.setdefault("qualification_question", "")
    d.setdefault("evidence_families", "")
    d.setdefault("prefer_web", False)
    channels = d.get("search_channels")
    if isinstance(channels, str) and channels.strip():
        d["search_channels"] = [channels.strip().lower()]
    elif isinstance(channels, list):
        d["search_channels"] = [str(c).strip().lower() for c in channels if str(c).strip()]
    else:
        d["search_channels"] = ["x", "web"]
    ratios = d.get("channel_limit_ratios")
    d["channel_limit_ratios"] = ratios if isinstance(ratios, dict) else {}
    return d


def load_custom_profile(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("product config must be a JSON object")
    discovery = data.get("discovery") or data
    ctx = discovery_context_from_profile({"discovery": discovery, **data})
    missing = [k for k in ("product_name", "what_it_does") if not str(ctx.get(k) or "").strip()]
    if missing:
        raise ValueError(f"product config missing: {', '.join(missing)}")
    profile = {
        "profile_kind": data.get("profile_kind") or "legacy",
        "product_name": ctx["product_name"],
        "product_context": data.get("product_context") or ctx["what_it_does"],
        "sign_off": data.get("sign_off") or "",
        "angles": data.get("angles") or {
            "senior": "ANGLE: Senior practitioner who may live with this workflow.",
            "early": "ANGLE: Practitioner who may live with this workflow.",
        },
        "email_mode": data.get("email_mode"),
        "sender_block": data.get("sender_block") or "",
        "discovery": ctx,
        "list_name": data.get("list_name") or "custom",
        "custom_config_path": path,
    }
    return profile


def _bullet(items: list[str]) -> str:
    return "\n".join(f"- {x}" for x in items if str(x).strip()) or "- (none)"


def format_discovery_brief(ctx: dict[str, Any]) -> str:
    extra = ""
    guidance = (ctx.get("search_guidance") or "").strip()
    if guidance:
        extra += f"Search intent:\n{guidance}\n"
    question = (ctx.get("qualification_question") or "").strip()
    if question:
        extra += f"Qualification question:\n{question}\n"
    return (
        f"Product: {ctx.get('product_name')}\n"
        f"What it does: {ctx.get('what_it_does')}\n"
        f"Who uses/buys it: {ctx.get('target_users_or_buyers')}\n"
        f"Problems it solves:\n{_bullet(ctx.get('problems_it_solves') or [])}\n"
        f"Example problem signals:\n{_bullet(ctx.get('examples_of_problem_signals') or [])}\n"
        f"Obvious non-targets / adjacent vendors:\n"
        f"{_bullet(ctx.get('obvious_non_targets_or_adjacent_vendors') or [])}\n"
        f"{extra}"
    )


def _strip_json_fences(raw: str) -> str:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return raw


def parse_json_payload(raw: str) -> Any:
    text = _strip_json_fences(raw)
    match = re.search(r"(\{.*\}|\[.*\])", text, flags=re.S)
    if match:
        text = match.group(1)
    return json.loads(text)


def candidate_id_for(source_url: str, signal_text: str) -> str:
    key = f"{canonical_url(source_url)}|{(signal_text or '').strip().lower()}"
    return "sig_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def entity_candidate_id(entity_key: str, profile_key: str = "") -> str:
    key = f"{profile_key}|{entity_key}"
    return "ent_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def infer_source(url: str, explicit: str = "") -> str:
    u = (url or "").lower()
    e = (explicit or "").strip().lower()
    if e in ("x", "twitter", "reddit", "web", "linkedin", "blog", "forum"):
        if e == "twitter":
            return "x"
        return e
    if "x.com" in u or "twitter.com" in u:
        return "x"
    if "reddit.com" in u:
        return "reddit"
    if "linkedin.com" in u:
        return "linkedin"
    return "web"


def normalize_signal(raw: dict[str, Any], citations: list[str] | None = None) -> dict[str, Any] | None:
    text = str(raw.get("signal_text") or raw.get("text") or "").strip()
    url = str(raw.get("source_url") or raw.get("url") or "").strip()
    if not text:
        return None
    if not url and citations:
        url = str(citations[0]).strip()
    relevance = str(raw.get("relevance") or "relevant").strip().lower()
    if relevance in ("highly relevant", "high"):
        relevance = "highly_relevant"
    if relevance in ("not_relevant", "irrelevant", "generic", "weak"):
        relevance = "generic" if relevance in ("generic", "weak") else "irrelevant"
    return {
        "source": infer_source(url, str(raw.get("source") or "")),
        "source_url": url,
        "source_key": canonical_url(url),
        "author_name": str(raw.get("author_name") or raw.get("author") or "").strip(),
        "author_handle": str(raw.get("author_handle") or raw.get("handle") or "").strip(),
        "published_at": str(raw.get("published_at") or raw.get("date") or "").strip(),
        "signal_text": text,
        "why_relevant": str(raw.get("why_relevant") or raw.get("why") or "").strip(),
        "latent_behavior": str(raw.get("latent_behavior") or "").strip(),
        "relevance": relevance or "relevant",
        "evidence_kind": _normalize_evidence_kind(str(raw.get("evidence_kind") or "")),
    }


def parse_signal_list(raw_text: str, citations: list[str] | None = None) -> list[dict[str, Any]]:
    payload = parse_json_payload(raw_text)
    if isinstance(payload, dict):
        items = payload.get("signals") or payload.get("results") or []
    else:
        items = payload
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        sig = normalize_signal(item, citations)
        if sig:
            out.append(sig)
    return out


def dedupe_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for sig in signals:
        url_key = canonical_url(sig.get("source_url") or "")
        text_key = (sig.get("signal_text") or "").strip().lower()
        cid = f"{url_key}|{text_key}" if url_key or text_key else candidate_id_for("", text_key)
        if cid in seen:
            continue
        seen.add(cid)
        out.append(sig)
    return out


def should_research_person(signal: dict[str, Any]) -> bool:
    if (signal.get("relevance") or "relevant") not in RELEVANCE_KEEP:
        return False
    return is_identifiable(signal)


_GENERIC_AUTHOR = re.compile(
    r"^(anonymous|user|throwaway|unknown|n/?a|redditor|"
    r"(senior\s+)?associate(\s+\d+)?|"
    r"(incoming\s+)?(pe\s+)?analyst|"
    r".*\bin pe\b.*)$",
    re.I,
)


def _normalize_evidence_kind(value: str) -> str:
    v = (value or "").strip().upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "PAIN": "EXPLICIT_PAIN",
        "EXPLICIT_PAIN": "EXPLICIT_PAIN",
        "FIRST_PERSON_PAIN": "EXPLICIT_PAIN",
        "STORAGE": "STORAGE",
        "REASONING_CAPTURE": "REASONING_CAPTURE",
        "REASONING": "REASONING_CAPTURE",
        "RETRIEVAL": "RETRIEVAL",
        "REUSE": "REUSE",
        "BEHAVIORAL": "BEHAVIORAL_REUSE",
        "BEHAVIORAL_REUSE": "BEHAVIORAL_REUSE",
        "OUTCOME_FEEDBACK": "OUTCOME_FEEDBACK",
        "CONTINUITY": "CONTINUITY",
        "INSTITUTIONAL_MEMORY": "INSTITUTIONAL_MEMORY",
        "WORKAROUND": "WORKAROUND",
        "PAIN_WORKAROUND": "WORKAROUND",
        "COMPANY_TRIGGER": "COMPANY_TRIGGER",
        "HIRING": "COMPANY_TRIGGER",
        "FUNDING": "COMPANY_TRIGGER",
        "GTM_STACK": "COMPANY_TRIGGER",
        "ADJACENT": "adjacent_reuse",
        "ADJACENT_REUSE": "adjacent_reuse",
        "ARTIFACT_REUSE": "adjacent_reuse",
        "TEMPLATE_REUSE": "adjacent_reuse",
        "OTHER": "other",
    }
    if v in aliases:
        return aliases[v]
    return (value or "").strip().lower().replace("-", "_").replace(" ", "_") or "other"


_AKASHIC_STORAGE_MARKERS = re.compile(
    r"\b(salesforce|hubspot|crm|dynamics\s*365|deal\s+history|knowledge\s+base|"
    r"institutional\s+memory|data\s+room|document\s+storage|everything\s+lives\s+in)\b",
    re.I,
)
_AKASHIC_REASONING_MARKERS = re.compile(
    r"\b(why\s+we\s+passed|why\s+we\s+(got\s+comfortable|declined|walked|said\s+no)|"
    r"record(ed|ing)?\s+why|revisit|look(ed)?\s+back|prior\s+(deal|investment|memo|underwriting)|"
    r"previous\s+(deal|investment|memo)|initial\s+(assum|thesis|underwriting)|"
    r"actual\s+vs|performance\s+vs|vs\s+expectations|ic\s+memo|investment\s+committee|"
    r"pattern\s+recognition|playbook|similar\s+(deal|add-?on)|seen\s+this\s+(movie|before)|"
    r"when\s+.*\s+left|context\s+(disappeared|walked\s+out|went\s+with)|"
    r"lessons?\s+learned|postmortem|were\s+we\s+right|old\s+ic)\b",
    re.I,
)


def _akashic_storage_only(text: str) -> bool:
    t = text or ""
    if not _AKASHIC_STORAGE_MARKERS.search(t):
        return False
    return not _AKASHIC_REASONING_MARKERS.search(t)


def _infer_akashic_evidence_kind(text: str, current: str) -> str:
    if current and current not in ("other", "WORKAROUND", "STORAGE", ""):
        return current
    t = text or ""
    if _akashic_storage_only(t):
        return "STORAGE"
    if re.search(r"when\s+.*\s+left|context\s+(disappeared|walked\s+out|went\s+with)", t, re.I):
        return "CONTINUITY"
    if re.search(r"actual\s+vs|performance\s+vs|vs\s+expectations|were\s+we\s+right|postmortem", t, re.I):
        return "OUTCOME_FEEDBACK"
    if re.search(r"playbook|pattern\s+recognition|learned\s+from|handled\s+a\s+similar", t, re.I):
        return "REUSE"
    if re.search(r"revisit|look(ed)?\s+back|old\s+ic|prior\s+memo|similar\s+deal|search\s+old", t, re.I):
        return "RETRIEVAL"
    if re.search(r"why\s+we\s+passed|record(ed|ing)?\s+why|got\s+comfortable|rationale", t, re.I):
        return "REASONING_CAPTURE"
    return current or "other"


def refine_akashic_signal(sig: dict[str, Any]) -> dict[str, Any]:
    """Demote CRM/storage-only mentions; infer reasoning ladder when Grok mislabels."""
    out = dict(sig)
    text = str(out.get("signal_text") or "")
    kind = _infer_akashic_evidence_kind(text, str(out.get("evidence_kind") or ""))
    out["evidence_kind"] = _normalize_evidence_kind(kind)
    if out["evidence_kind"] == "STORAGE" or _akashic_storage_only(text):
        out["relevance"] = "generic"
        if not out.get("why_relevant"):
            out["why_relevant"] = "Storage/CRM mention without reasoning reuse."
    return out


def is_identifiable(signal: dict[str, Any]) -> bool:
    name = (signal.get("author_name") or "").strip()
    handle = (signal.get("author_handle") or "").strip()
    if handle.startswith("u/") and (
        "throwaway" in handle.lower() or re.search(r"\d{2,}$", handle)
    ):
        return False
    if _GENERIC_AUTHOR.match(name or ""):
        return False
    if handle.startswith("@") and len(handle) > 2 and "throwaway" not in handle.lower():
        return True
    if name and name.lower() not in ("anonymous", "unknown", "n/a", "user"):
        return True
    return False


def parse_signal_date(value: str) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
        except ValueError:
            return None
    m = re.match(r"(\d{4})-(\d{2})$", raw)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), 1, tzinfo=timezone.utc)
        except ValueError:
            return None
    m = re.match(r"^(\d{4})$", raw)
    if m:
        return datetime(int(m.group(1)), 1, 1, tzinfo=timezone.utc)
    return None


def _recency_score(signal: dict[str, Any], now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    dt = parse_signal_date(str(signal.get("published_at") or ""))
    if dt is None:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    days = (now - dt).days
    if days <= 365:
        return 3
    if days <= 730:
        return 2
    return 1


def rank_signals(
    signals: list[dict[str, Any]],
    *,
    prefer_web: bool = False,
) -> list[dict[str, Any]]:
    """Prefer identifiable behavioral/pain evidence; recency is secondary, not a veto."""
    rel = {"highly_relevant": 3, "relevant": 2, "generic": 0, "irrelevant": 0}
    kind = {
        "REUSE": 5,
        "OUTCOME_FEEDBACK": 5,
        "RETRIEVAL": 4,
        "REASONING_CAPTURE": 4,
        "CONTINUITY": 4,
        "BEHAVIORAL_REUSE": 4,
        "EXPLICIT_PAIN": 3,
        "WORKAROUND": 3,
        "INSTITUTIONAL_MEMORY": 3,
        "COMPANY_TRIGGER": 2,
        "STORAGE": 0,
        "pain_workaround": 3,
        "other": 1,
        "adjacent_reuse": 0,
    }

    def source_bonus(sig: dict[str, Any]) -> int:
        src = sig.get("source") or ""
        if prefer_web:
            return 1 if src in ("web", "linkedin") else 0
        return 1 if src == "x" else 0

    def key(sig: dict[str, Any]) -> tuple:
        return (
            rel.get(sig.get("relevance") or "relevant", 1),
            1 if is_identifiable(sig) else 0,
            kind.get(sig.get("evidence_kind") or "other", 1),
            _recency_score(sig),
            source_bonus(sig),
        )

    return sorted(signals, key=key, reverse=True)


def normalize_actor(value: str) -> str:
    v = (value or "").strip().upper().replace(" ", "_")
    aliases = {
        "VENDOR": "BUILDER_OR_VENDOR",
        "BUILDER": "BUILDER_OR_VENDOR",
        "BUYER": "PRACTITIONER",
        "FOUNDER": "UNKNOWN",
        "COMMENTATOR": "OTHER",
    }
    v = aliases.get(v, v)
    return v if v in ACTOR_TYPES else "UNKNOWN"


def normalize_recommendation(value: str) -> str:
    v = (value or "").strip().upper().replace(" ", "_")
    aliases = {
        "PROSPECT": "LIKELY_PROSPECT",
        "YES": "LIKELY_PROSPECT",
        "NO": "LIKELY_NOT_PROSPECT",
        "NOT_PROSPECT": "LIKELY_NOT_PROSPECT",
        "LIKELY_NOT_RELEVANT": "LIKELY_NOT_RELEVANT",
    }
    v = aliases.get(v, v)
    return v if v in RECOMMENDATIONS else "UNCLEAR"


def _axis_level(value: str | None) -> int:
    v = (value or "UNKNOWN").strip().upper().replace(" ", "_")
    return {"UNKNOWN": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "VERY_HIGH": 4}.get(v, 0)


def extract_axes(payload: dict[str, Any] | None) -> dict[str, str]:
    payload = payload or {}
    out: dict[str, str] = {}
    champion = payload.get("influence_or_champion_potential") or payload.get("champion_potential")
    merged = dict(payload)
    if champion is not None:
        merged["influence_or_champion_potential"] = champion
    for key in CLASSIFICATION_AXES:
        raw = str(merged.get(key) or "").strip().upper().replace(" ", "_")
        out[key] = raw if raw in AXIS_LEVELS else "UNKNOWN"
    return out


def derive_recommendation(
    *,
    actor_type: str,
    raw_recommendation: str,
    axes: dict[str, str] | None = None,
) -> str:
    """Title/persona cannot veto strong behavioral or pain evidence."""
    actor = normalize_actor(actor_type)
    rec = normalize_recommendation(raw_recommendation)
    if actor == "BUILDER_OR_VENDOR":
        return "LIKELY_NOT_PROSPECT"
    axes = axes or {}
    evidence = max(
        _axis_level(axes.get("pain_evidence")),
        _axis_level(axes.get("behavioral_evidence")),
        _axis_level(axes.get("workaround_evidence")),
        _axis_level(axes.get("outcome_feedback_evidence")),
    )
    champion = _axis_level(axes.get("influence_or_champion_potential"))
    if rec in (
        "PRIMARY_PROSPECT",
        "CHAMPION_CANDIDATE",
        "HIGH_VALUE_DISCOVERY",
        "LIKELY_PROSPECT",
    ):
        return rec
    if evidence >= 3 and rec in (
        "LIKELY_NOT_PROSPECT",
        "LIKELY_NOT_RELEVANT",
        "UNCLEAR",
        "ADJACENT_PRACTITIONER",
        "PAIN_SIGNAL_ONLY",
    ):
        if champion >= 2:
            return "CHAMPION_CANDIDATE"
        return "HIGH_VALUE_DISCOVERY"
    return rec


def empty_person() -> dict[str, str]:
    return {
        "name": "",
        "title": "",
        "company": "",
        "company_type": "",
        "linkedin_url": "",
        "other_profile": "",
    }


def normalize_person(raw: dict[str, Any] | None, signal: dict[str, Any]) -> dict[str, str]:
    raw = raw or {}
    person = empty_person()
    person["name"] = str(raw.get("name") or signal.get("author_name") or "").strip()
    person["title"] = str(raw.get("title") or raw.get("current_title") or "").strip()
    person["company"] = str(raw.get("company") or raw.get("current_company") or "").strip()
    person["company_type"] = str(raw.get("company_type") or "").strip()
    person["linkedin_url"] = str(raw.get("linkedin_url") or "").strip()
    person["other_profile"] = str(raw.get("other_profile") or raw.get("profile_url") or "").strip()
    if (person["name"] or "").lower() in ("unknown", "anonymous", "n/a"):
        person["name"] = ""
    return person


def build_candidate(
    *,
    signal: dict[str, Any],
    person: dict[str, Any] | None,
    actor_type: str,
    recommendation: str,
    recommendation_reason: str,
    list_name: str,
    profile_key: str,
    product_name: str,
    product_snapshot: dict[str, Any] | None = None,
    researched: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sig = dict(signal)
    person_n = normalize_person(person, sig)
    rec = {
        "record_type": RECORD_TYPE,
        "candidate_id": candidate_id_for(sig.get("source_url") or "", sig.get("signal_text") or ""),
        "ts": datetime.now(timezone.utc).isoformat(),
        "list": list_name,
        "profile_key": profile_key,
        "product_name": product_name,
        "signal_source": sig.get("source") or "",
        "signal_url": sig.get("source_url") or "",
        "signal_text": sig.get("signal_text") or "",
        "signal_date": sig.get("published_at") or "",
        "author_name": sig.get("author_name") or person_n.get("name") or "",
        "author_handle": sig.get("author_handle") or "",
        "why_relevant": sig.get("why_relevant") or "",
        "signal_relevance": sig.get("relevance") or "",
        "name": person_n.get("name") or sig.get("author_name") or "",
        "title": person_n.get("title") or "",
        "company": person_n.get("company") or "",
        "company_type": person_n.get("company_type") or "",
        "linkedin_url": person_n.get("linkedin_url") or "",
        "other_profile": person_n.get("other_profile") or "",
        "actor_type": normalize_actor(actor_type),
        "recommendation": normalize_recommendation(recommendation),
        "recommendation_reason": (recommendation_reason or "").strip(),
        "human_status": "PENDING",
        "human_reject_reason": None,
        "human_decided_at": None,
        "enrichment_attempted": False,
        "email_found": False,
        "email": "",
        "passed_to_outbound": False,
        "outbound_sent": False,
        "person_researched": researched,
        "signal": sig,
        "person": person_n,
    }
    if extra:
        rec.update({k: v for k, v in extra.items() if v is not None})
    if extra and extra.get("entity_key"):
        rec["candidate_id"] = entity_candidate_id(
            str(extra.get("entity_key")),
            profile_key,
        )
    if product_snapshot:
        rec["product_snapshot"] = product_snapshot
    return rec


def load_candidates(path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path or not os.path.isfile(path):
        return rows
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("record_type") == RECORD_TYPE or rec.get("candidate_id"):
                rows.append(rec)
    return rows


def save_candidates(path: str, rows: list[dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for rec in rows:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def apply_human_decision(
    rows: list[dict[str, Any]],
    candidate_id: str,
    status: str,
    reject_reason: str | None = None,
) -> dict[str, Any]:
    status = (status or "").strip().upper()
    if status not in ("APPROVED", "REJECTED"):
        raise ValueError("human_status must be APPROVED or REJECTED")
    reason = (reject_reason or "").strip().lower() or None
    if reason and reason not in REJECT_REASONS:
        raise ValueError(f"reject_reason must be one of {', '.join(REJECT_REASONS)}")
    for rec in rows:
        if rec.get("candidate_id") == candidate_id:
            rec["human_status"] = status
            rec["human_decided_at"] = datetime.now(timezone.utc).isoformat()
            rec["human_reject_reason"] = reason if status == "REJECTED" else None
            return rec
    raise KeyError(f"candidate not found: {candidate_id}")


def should_enrich(rec: dict[str, Any]) -> bool:
    return rec.get("human_status") == "APPROVED"


def should_draft(rec: dict[str, Any]) -> bool:
    email = (rec.get("email") or "").strip()
    return should_enrich(rec) and bool(email)


def entity_key_for(name: str = "", company: str = "", handle: str = "") -> str:
    n = " ".join((name or "").lower().split())
    c = " ".join((company or "").lower().split())
    if n and c:
        return f"{n}|{c}"
    if n:
        return n
    return (handle or "").lower().lstrip("@")


def canonical_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    if netloc in ("twitter.com", "mobile.twitter.com"):
        netloc = "x.com"
    path = parts.path.rstrip("/") or "/"
    query = []
    for key, val in parse_qsl(parts.query, keep_blank_values=True):
        k = key.lower()
        if k.startswith("utm_") or k in _TRACKING_QUERY_KEYS:
            continue
        query.append((key, val))
    return urlunsplit((scheme, netloc, path, urlencode(query, doseq=True), ""))


def normalize_handle(handle: str = "", url: str = "") -> str:
    h = (handle or "").strip().lstrip("@").lower()
    if h.startswith("u/"):
        return h
    if h:
        return h
    m = re.search(r"(?:x\.com|twitter\.com)/([^/?#]+)/", (url or "").lower())
    if not m:
        return ""
    user = m.group(1)
    if user in ("i", "intent", "share", "search", "home", "explore"):
        return ""
    return user


def signal_group_key(signal: dict[str, Any]) -> str:
    handle = normalize_handle(signal.get("author_handle") or "", signal.get("source_url") or "")
    if handle:
        return "h:" + handle
    name = " ".join((signal.get("author_name") or "").lower().split())
    if name and " " in name and is_identifiable(signal):
        return "n:" + name
    url_key = canonical_url(signal.get("source_url") or "")
    if url_key:
        return "u:" + url_key
    return "t:" + candidate_id_for("", signal.get("signal_text") or "")


def aggregate_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for sig in signals:
        key = signal_group_key(sig)
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(sig)
    groups = []
    for key in order:
        ranked = rank_signals(buckets[key])
        groups.append({
            "entity_key": key,
            "signals": ranked,
            "primary": ranked[0],
        })
    return groups


def identity_resolved_enough(signal: dict[str, Any], qual: dict[str, Any]) -> bool:
    if qual.get("identity_resolved") is False:
        return False
    person = qual.get("person") or {}
    if (person.get("linkedin_url") or "").strip():
        return True
    name = (person.get("name") or signal.get("author_name") or "").strip()
    company = (person.get("company") or "").strip()
    return bool(company and name and " " in name)


def _cache_expired(entry: dict[str, Any], now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    raw = str(entry.get("last_researched_at") or "")
    dt = parse_signal_date(raw)
    if dt is None:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt) > timedelta(days=CACHE_TTL_DAYS)


def _cache_index_keys(entry: dict[str, Any]) -> list[str]:
    keys = list(entry.get("entity_keys") or [])
    person = entry.get("person") or {}
    handle = normalize_handle(
        entry.get("handle") or "",
        person.get("other_profile") or "",
    )
    if handle:
        keys.append("h:" + handle)
    name = person.get("name") or ""
    company = person.get("company") or ""
    ek = entity_key_for(name, company, handle)
    if ek:
        keys.append("n:" + ek if "|" in ek or " " in ek else "n:" + ek)
        keys.append(ek)
    li = canonical_url(person.get("linkedin_url") or "")
    if li:
        keys.append("li:" + li)
    for url in entry.get("source_urls") or []:
        cu = canonical_url(url)
        if cu:
            keys.append("u:" + cu)
    out: list[str] = []
    seen: set[str] = set()
    for key in keys:
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def lookup_research_cache(
    index: dict[tuple[str, str], dict[str, Any]],
    profile_key: str,
    signal: dict[str, Any],
    person: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    person = person or {}
    handle = normalize_handle(signal.get("author_handle") or "", signal.get("source_url") or "")
    candidates = [
        signal_group_key(signal),
        "h:" + handle if handle else "",
        "n:" + entity_key_for(person.get("name") or signal.get("author_name") or "", person.get("company") or "", handle),
        "li:" + canonical_url(person.get("linkedin_url") or ""),
        "u:" + canonical_url(signal.get("source_url") or ""),
    ]
    for key in candidates:
        if not key or key in ("n:", "h:", "li:", "u:"):
            continue
        hit = index.get((profile_key, key))
        if hit and not _cache_expired(hit):
            return hit
    return None


def load_research_cache(path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path or not os.path.isfile(path):
        return rows
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if isinstance(rec, dict) and rec.get("entity_keys"):
                rows.append(rec)
    return rows


def save_research_cache(path: str, rows: list[dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for rec in rows:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def build_cache_index(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in rows:
        profile_key = str(entry.get("profile_key") or "")
        for key in _cache_index_keys(entry):
            index[(profile_key, key)] = entry
    return index


def candidate_to_cache_entry(rec: dict[str, Any]) -> dict[str, Any]:
    person = rec.get("person") or {
        "name": rec.get("name") or "",
        "title": rec.get("title") or "",
        "company": rec.get("company") or "",
        "company_type": rec.get("company_type") or "",
        "linkedin_url": rec.get("linkedin_url") or "",
        "other_profile": rec.get("other_profile") or "",
    }
    urls = [rec.get("signal_url") or ""]
    for sig in rec.get("additional_signals") or rec.get("signals") or []:
        if isinstance(sig, dict):
            urls.append(sig.get("source_url") or "")
    evidence = []
    for ev in rec.get("supporting_evidence") or []:
        if not isinstance(ev, dict):
            continue
        evidence.append({
            "source_url": ev.get("source_url") or "",
            "source_key": canonical_url(ev.get("source_url") or ""),
            "source_date": ev.get("source_date") or "",
            "evidence_type": ev.get("evidence_type") or "",
            "quote_or_paraphrase": ev.get("quote_or_paraphrase") or "",
            "retrieved_at": rec.get("ts") or "",
        })
    primary = rec.get("signal") if isinstance(rec.get("signal"), dict) else {}
    if rec.get("signal_url") or rec.get("signal_text"):
        evidence.append({
            "source_url": rec.get("signal_url") or primary.get("source_url") or "",
            "source_key": canonical_url(rec.get("signal_url") or primary.get("source_url") or ""),
            "source_date": rec.get("signal_date") or primary.get("published_at") or "",
            "evidence_type": primary.get("evidence_kind") or "",
            "quote_or_paraphrase": rec.get("signal_text") or primary.get("signal_text") or "",
            "retrieved_at": rec.get("ts") or "",
        })
    source_keys = []
    seen_u: set[str] = set()
    for url in urls:
        cu = canonical_url(url)
        if cu and cu not in seen_u:
            seen_u.add(cu)
            source_keys.append(cu)
    identity = rec.get("identity_resolved")
    if identity is None:
        identity = identity_resolved_enough(primary or rec, {
            "person": person,
            "identity_resolved": rec.get("identity_resolved"),
        })
    entry = {
        "profile_key": rec.get("profile_key") or "",
        "entity_keys": [],
        "person": person,
        "handle": normalize_handle(rec.get("author_handle") or "", rec.get("signal_url") or ""),
        "identity_resolved": bool(identity),
        "actor_type": rec.get("actor_type") or "UNKNOWN",
        "axes": {k: rec.get(k) for k in CLASSIFICATION_AXES if rec.get(k)},
        "source_urls": source_keys,
        "evidence": evidence,
        "deepened": bool(rec.get("supporting_evidence") or rec.get("deepened")),
        "prior_recommendation": rec.get("recommendation") or "",
        "last_researched_at": rec.get("ts") or datetime.now(timezone.utc).isoformat(),
    }
    entry["entity_keys"] = _cache_index_keys(entry)
    return entry


def upsert_cache_entry(
    rows: list[dict[str, Any]],
    index: dict[tuple[str, str], dict[str, Any]],
    entry: dict[str, Any],
) -> None:
    existing = None
    profile_key = str(entry.get("profile_key") or "")
    for key in _cache_index_keys(entry):
        hit = index.get((profile_key, key))
        if hit:
            existing = hit
            break
    if existing is None:
        rows.append(entry)
        target = entry
    else:
        existing_urls = {canonical_url(u) for u in existing.get("source_urls") or []}
        for url in entry.get("source_urls") or []:
            cu = canonical_url(url)
            if cu and cu not in existing_urls:
                existing.setdefault("source_urls", []).append(cu)
                existing_urls.add(cu)
        seen_ev = {
            canonical_url(ev.get("source_url") or ev.get("source_key") or "")
            for ev in existing.get("evidence") or []
            if isinstance(ev, dict)
        }
        for ev in entry.get("evidence") or []:
            cu = canonical_url(ev.get("source_url") or ev.get("source_key") or "")
            if cu and cu not in seen_ev:
                existing.setdefault("evidence", []).append(ev)
                seen_ev.add(cu)
        if entry.get("identity_resolved"):
            existing["identity_resolved"] = True
            existing["person"] = entry.get("person") or existing.get("person")
        if entry.get("deepened"):
            existing["deepened"] = True
        existing["actor_type"] = entry.get("actor_type") or existing.get("actor_type")
        if entry.get("axes"):
            existing["axes"] = _max_axes(existing.get("axes") or {}, entry.get("axes") or {})
        existing["last_researched_at"] = entry.get("last_researched_at") or existing.get("last_researched_at")
        existing["prior_recommendation"] = entry.get("prior_recommendation") or existing.get("prior_recommendation")
        existing["entity_keys"] = _cache_index_keys(existing)
        target = existing
    for key in _cache_index_keys(target):
        index[(profile_key, key)] = target


def seed_cache_from_candidates(
    rows: list[dict[str, Any]],
    index: dict[tuple[str, str], dict[str, Any]],
    candidates: list[dict[str, Any]],
    profile_key: str,
) -> None:
    for rec in candidates:
        if (rec.get("profile_key") or profile_key) != profile_key:
            continue
        upsert_cache_entry(rows, index, candidate_to_cache_entry(rec))


def cache_has_source(entry: dict[str, Any], url: str) -> bool:
    key = canonical_url(url)
    if not key:
        return False
    known = {canonical_url(u) for u in entry.get("source_urls") or []}
    for ev in entry.get("evidence") or []:
        if isinstance(ev, dict):
            known.add(canonical_url(ev.get("source_url") or ev.get("source_key") or ""))
    return key in known


def qualification_from_cache(entry: dict[str, Any], signal: dict[str, Any]) -> dict[str, Any]:
    person = dict(entry.get("person") or empty_person())
    axes = extract_axes(entry.get("axes") or entry)
    actor = normalize_actor(str(entry.get("actor_type") or ""))
    rec = derive_recommendation(
        actor_type=actor,
        raw_recommendation="UNCLEAR",
        axes=axes,
    )
    if not entry.get("identity_resolved") and (signal.get("relevance") or "") in RELEVANCE_KEEP:
        rec = "HIGH_VALUE_DISCOVERY"
    return {
        "person": person,
        "actor_type": actor if entry.get("identity_resolved") else "UNKNOWN",
        "recommendation": rec,
        "recommendation_reason": (
            "Reused cached identity; classification recomputed from stored evidence plus the new signal."
        ),
        "researched": True,
        "identity_resolved": bool(entry.get("identity_resolved")),
        "cache_hit": True,
        "supporting_evidence": list(entry.get("evidence") or []),
        **axes,
    }


def append_research_cost(path: str, event: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    line = json.dumps(event, ensure_ascii=False) + "\n"
    with _COST_LOG_LOCK:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)


def load_research_costs(path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path or not os.path.isfile(path):
        return rows
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if isinstance(rec, dict):
                rows.append(rec)
    return rows


def print_research_cost_summary(path: str) -> None:
    rows = load_research_costs(path)
    if not rows:
        print("No research cost log.")
        return
    by_stage: dict[str, list[dict[str, Any]]] = {}
    for rec in rows:
        by_stage.setdefault(str(rec.get("stage") or "unknown"), []).append(rec)
    print("=== Research cost ===")
    total = 0.0
    priced = 0
    for stage in sorted(by_stage):
        chunk = by_stage[stage]
        costs = [float(r["cost_usd"]) for r in chunk if r.get("cost_usd") is not None]
        sub = sum(costs)
        total += sub
        priced += len(costs)
        print(f"  {stage:<16} {len(chunk):>3} call(s)  ${sub:.4f}")
        for rec in chunk:
            label = rec.get("person_name") or rec.get("entity_key") or ""
            cost = rec.get("cost_usd")
            cost_s = f"${cost:.4f}" if cost is not None else "$?.????"
            extra = f" {label}" if label else ""
            print(f"    {cost_s}{extra}")
    missing = len(rows) - priced
    miss = f"  ({missing} without cost_usd)" if missing else ""
    print(f"  {'TOTAL':<16} {len(rows):>3} call(s)  ${total:.4f}{miss}")
    print(f"  log: {path}")


def _guess_evidence_count(text: str) -> int | None:
    try:
        payload = parse_json_payload(text)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
    if isinstance(payload, dict):
        if isinstance(payload.get("signals"), list):
            return len(payload["signals"])
        if isinstance(payload.get("evidence"), list):
            return len(payload["evidence"])
        return 0
    if isinstance(payload, list):
        return len(payload)
    return None


def _invoke_researcher(
    fn: Callable[..., dict[str, Any]],
    prompt: str,
    *,
    tools: list[dict[str, Any]] | None = None,
    prompt_cache_key: str | None = None,
) -> dict[str, Any]:
    try:
        return fn(prompt, tools=tools, prompt_cache_key=prompt_cache_key)
    except TypeError:
        return fn(prompt, tools=tools)


def split_name(name: str) -> tuple[str, str]:
    parts = (name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def candidate_to_lead(rec: dict[str, Any]) -> dict[str, Any]:
    first, last = split_name(rec.get("name") or rec.get("author_name") or "")
    return {
        "first_name": first,
        "last_name": last,
        "name": (rec.get("name") or rec.get("author_name") or "").strip(),
        "email": (rec.get("email") or "").strip(),
        "title": rec.get("title") or "",
        "company": rec.get("company") or "Unknown",
        "industry": "",
        "keywords": "",
        "company_description": "",
        "employee_count": None,
        "employee_count_raw": "",
        "funding_stage": "",
        "technologies": "",
        "department": "",
        "departments": "",
        "sub_departments": "",
        "seniority": "",
        "location": "",
        "linkedin_url": rec.get("linkedin_url") or "",
        "website": "",
        "signal_source": rec.get("signal_source") or "",
        "signal_url": rec.get("signal_url") or "",
        "signal_text": rec.get("signal_text") or "",
        "signal_date": rec.get("signal_date") or "",
        "actor_type": rec.get("actor_type") or "",
        "recommendation": rec.get("recommendation") or "",
        "recommendation_reason": rec.get("recommendation_reason") or "",
        "human_status": rec.get("human_status") or "",
        "candidate_id": rec.get("candidate_id") or "",
    }


def signal_jsonl_fields(lead: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "signal_source",
        "signal_url",
        "signal_text",
        "signal_date",
        "actor_type",
        "recommendation",
        "recommendation_reason",
        "human_status",
        "candidate_id",
    )
    return {k: lead[k] for k in keys if lead.get(k) not in (None, "")}


def format_signal_evidence_block(lead: dict[str, Any]) -> str:
    text = (lead.get("signal_text") or "").strip()
    if not text:
        return ""
    excerpt = text if len(text) <= 400 else text[:397] + "…"
    return (
        "PUBLIC SIGNAL (verified public post/comment; treat as FACTS, not a buying request):\n"
        f"- Source: {lead.get('signal_source') or ''}\n"
        f"- URL: {lead.get('signal_url') or ''}\n"
        f"- Date: {lead.get('signal_date') or ''}\n"
        f"- What they said: {excerpt}\n"
        "- Rules: do not invent extra posts. Do not claim they want to buy or asked "
        "for a solution. Do not treat generic commentary as personal pain. "
        "Do not overquote; one short reference is enough if used at all."
    )


def format_review_card(rec: dict[str, Any]) -> str:
    lines = [
        "────────────────────────────────",
        f"{rec.get('name') or rec.get('author_name') or '(unresolved)'}  [{rec.get('candidate_id')}]",
        f"{rec.get('title') or ''} · {rec.get('company') or ''}".strip(" ·"),
        f"LinkedIn: {rec.get('linkedin_url') or '(not found)'}",
        "",
        f"Signal ({rec.get('signal_source') or ''} · {rec.get('signal_date') or ''})",
        f"  {(rec.get('signal_text') or '')[:500]}",
        f"  URL: {rec.get('signal_url') or ''}",
        f"Why surfaced: {rec.get('why_relevant') or ''}",
    ]
    extra_sigs = rec.get("additional_signals") or []
    if extra_sigs:
        lines.append(f"Additional signals ({len(extra_sigs)}):")
        for sig in extra_sigs[:4]:
            if not isinstance(sig, dict):
                continue
            lines.append(
                f"  - {sig.get('source') or ''} · {sig.get('published_at') or ''}: "
                f"{(sig.get('signal_text') or '')[:180]}"
            )
            if sig.get("source_url"):
                lines.append(f"    {sig.get('source_url')}")
    lines.extend([
        "",
        f"Actor: {rec.get('actor_type')}",
        f"AI recommendation: {rec.get('recommendation')}  (human: {rec.get('human_status')})",
        f"Reason: {rec.get('recommendation_reason') or ''}",
    ])
    axis_bits = [
        f"{k}={rec[k]}"
        for k in CLASSIFICATION_AXES
        if rec.get(k) and rec.get(k) != "UNKNOWN"
    ]
    if axis_bits:
        lines.append("Axes: " + "; ".join(axis_bits))
    extra_ev = rec.get("supporting_evidence") or []
    if extra_ev:
        lines.append(f"Additional evidence ({len(extra_ev)}):")
        for ev in extra_ev[:5]:
            if not isinstance(ev, dict):
                continue
            quote = str(ev.get("quote_or_paraphrase") or "")[:220]
            lines.append(f"  - {ev.get('evidence_type') or 'OTHER'}: {quote}")
            if ev.get("source_url"):
                lines.append(f"    {ev.get('source_url')}")
    if rec.get("human_reject_reason"):
        lines.append(f"Human reject reason: {rec['human_reject_reason']}")
    if rec.get("email"):
        lines.append(f"Email: {rec['email']}")
    return "\n".join(lines)


def export_approved_csv(rows: list[dict[str, Any]], path: str) -> int:
    approved = [r for r in rows if should_enrich(r)]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "candidate_id",
                "First Name",
                "Last Name",
                "Title",
                "Company Name",
                "Person Linkedin Url",
                "Email",
            ],
        )
        writer.writeheader()
        for rec in approved:
            first, last = split_name(rec.get("name") or rec.get("author_name") or "")
            writer.writerow({
                "candidate_id": rec.get("candidate_id") or "",
                "First Name": first,
                "Last Name": last,
                "Title": rec.get("title") or "",
                "Company Name": rec.get("company") or "",
                "Person Linkedin Url": rec.get("linkedin_url") or "",
                "Email": rec.get("email") or "",
            })
    return len(approved)


def _match_enriched_row(rec: dict[str, Any], lead: dict[str, Any]) -> bool:
    rec_li = (rec.get("linkedin_url") or "").strip().rstrip("/").lower()
    lead_li = (lead.get("linkedin_url") or "").strip().rstrip("/").lower()
    if rec_li and lead_li and rec_li == lead_li:
        return True
    rec_id = (rec.get("candidate_id") or "").strip()
    if rec_id and rec_id == (lead.get("candidate_id") or "").strip():
        return True
    rn = (rec.get("name") or "").strip().lower()
    ln = (lead.get("name") or "").strip().lower()
    rc = (rec.get("company") or "").strip().lower()
    lc = (lead.get("company") or "").strip().lower()
    return bool(rn and ln and rn == ln and rc and lc and rc == lc)


def import_enriched_leads(
    rows: list[dict[str, Any]],
    leads: list[dict[str, Any]],
) -> int:
    """Attach emails from an Apollo CSV onto APPROVED candidates only. Never invent email."""
    updated = 0
    for rec in rows:
        if not should_enrich(rec):
            continue
        rec["enrichment_attempted"] = True
        for lead in leads:
            email = (lead.get("email") or "").strip()
            if not email:
                continue
            if _match_enriched_row(rec, lead):
                rec["email"] = email
                rec["email_found"] = True
                if not rec.get("title"):
                    rec["title"] = lead.get("title") or rec.get("title") or ""
                if not rec.get("company") or rec.get("company") == "Unknown":
                    rec["company"] = lead.get("company") or rec.get("company") or ""
                if not rec.get("linkedin_url"):
                    rec["linkedin_url"] = lead.get("linkedin_url") or ""
                if lead.get("first_name") or lead.get("last_name"):
                    rec["name"] = lead.get("name") or rec.get("name")
                updated += 1
                break
    return updated


def candidate_to_apollo_details(rec: dict[str, Any]) -> dict[str, str]:
    from apollo_enrich import details_from_csv_row

    first, last = split_name(rec.get("name") or rec.get("author_name") or "")
    return details_from_csv_row({
        "First Name": first,
        "Last Name": last,
        "Person Linkedin Url": rec.get("linkedin_url") or "",
        "Company Name": rec.get("company") or "",
        "Email": rec.get("email") or "",
    })


def phone_from_csv_row(row: dict[str, Any]) -> str:
    for col in (
        "Mobile Phone",
        "Work Direct Phone",
        "Other Phone",
        "Corporate Phone",
        "Home Phone",
    ):
        value = str(row.get(col) or "").strip()
        if value:
            return value
    return ""


def csv_row_as_contact_lead(row: dict[str, Any]) -> dict[str, Any]:
    first = str(row.get("First Name") or "").strip()
    last = str(row.get("Last Name") or "").strip()
    return {
        "candidate_id": str(row.get("candidate_id") or "").strip(),
        "email": str(row.get("Email") or "").strip(),
        "phone": phone_from_csv_row(row),
        "name": f"{first} {last}".strip() or first or last,
        "title": str(row.get("Title") or "").strip(),
        "company": str(row.get("Company Name") or "").strip(),
        "linkedin_url": str(row.get("Person Linkedin Url") or "").strip(),
    }


def apply_contact_lead(rec: dict[str, Any], lead: dict[str, Any]) -> bool:
    """Attach email/phone from a matched lead onto a candidate record."""
    changed = False
    email = str(lead.get("email") or "").strip()
    phone = str(lead.get("phone") or "").strip()
    if email and not str(rec.get("email") or "").strip():
        rec["email"] = email
        rec["email_found"] = True
        rec["email_source"] = "Apollo"
        changed = True
    if phone and not str(rec.get("phone") or "").strip():
        rec["phone"] = phone
        rec["phone_found"] = True
        rec["phone_source"] = "Apollo"
        changed = True
    title = str(lead.get("title") or "").strip()
    if title and not rec.get("title"):
        rec["title"] = title
    company = str(lead.get("company") or "").strip()
    if company and (not rec.get("company") or rec.get("company") == "Unknown"):
        rec["company"] = company
    linkedin = str(lead.get("linkedin_url") or "").strip()
    if linkedin and not rec.get("linkedin_url"):
        rec["linkedin_url"] = linkedin
    if changed:
        rec["enrichment_attempted"] = True
    return changed


def sync_contact_from_csv_rows(rec: dict[str, Any], csv_rows: list[dict[str, Any]]) -> bool:
    for row in csv_rows:
        lead = csv_row_as_contact_lead(row)
        if not lead.get("email") and not lead.get("phone"):
            continue
        if _match_enriched_row(rec, lead):
            return apply_contact_lead(rec, lead)
    return False


def _apply_apollo_person(rec: dict[str, Any], person: dict[str, Any], email: str) -> None:
    from apollo_enrich import pick_phones

    rec["enrichment_attempted"] = True
    if email:
        rec["email"] = email
        rec["email_found"] = True
        rec["email_source"] = "Apollo"
    title = str(person.get("title") or "").strip()
    if title and not rec.get("title"):
        rec["title"] = title
    org = person.get("organization") if isinstance(person.get("organization"), dict) else {}
    company = str(org.get("name") or person.get("organization_name") or "").strip()
    if company and (not rec.get("company") or rec.get("company") == "Unknown"):
        rec["company"] = company
    linkedin = str(person.get("linkedin_url") or "").strip()
    if linkedin and not rec.get("linkedin_url"):
        rec["linkedin_url"] = linkedin
    if not str(rec.get("phone") or "").strip():
        phones = pick_phones(person.get("phone_numbers"))
        phone = phones.get("mobile") or phones.get("work_direct") or phones.get("other") or ""
        if phone:
            rec["phone"] = phone
            rec["phone_found"] = True
            rec["phone_source"] = "Apollo"


def enrich_approved_candidates(
    rows: list[dict[str, Any]],
    *,
    matcher: Callable[..., dict[str, Any]] | None = None,
    hunter_finder: Callable[..., dict[str, Any] | None] | None = None,
    progress: Any = print,
) -> int:
    """Apollo bulk_match emails onto APPROVED candidates, then Hunter.io per person."""
    from apollo_enrich import BULK_MATCH_LIMIT, bulk_match_people, emails_from_matches
    from hunter_enrich import apply_hunter_email, find_email, hunter_ready

    fn = matcher or bulk_match_people
    hunter_fn = hunter_finder or find_email
    need = [
        rec for rec in rows
        if should_enrich(rec) and not (rec.get("email") or "").strip()
    ]
    if not need:
        return 0
    updated = 0
    for i in range(0, len(need), BULK_MATCH_LIMIT):
        batch = need[i : i + BULK_MATCH_LIMIT]
        details = [candidate_to_apollo_details(rec) for rec in batch]
        if progress:
            progress(f"  Apollo email match {len(details)} approved people…")
        payload = fn(
            details,
            reveal_phone_number=False,
            reveal_personal_emails=True,
        )
        emails = emails_from_matches(payload if isinstance(payload, dict) else {})
        matches = payload.get("matches") if isinstance(payload, dict) else []
        if not isinstance(matches, list):
            matches = []
        for j, rec in enumerate(batch):
            person = matches[j] if j < len(matches) else None
            email = emails[j] if j < len(emails) else ""
            if isinstance(person, dict):
                _apply_apollo_person(rec, person, email)
            else:
                rec["enrichment_attempted"] = True
            if (rec.get("email") or "").strip():
                updated += 1

    if hunter_ready():
        for rec in need:
            if (rec.get("email") or "").strip():
                continue
            if progress:
                progress(f"  Hunter.io email lookup for {rec.get('name') or rec.get('candidate_id')}…")
            try:
                data = hunter_fn(rec)
            except Exception:
                rec["enrichment_attempted"] = True
                continue
            if data and apply_hunter_email(rec, data):
                updated += 1
    return updated


def discovery_channel_plan(ctx: dict[str, Any], limit: int) -> list[tuple[str, int]]:
    channels = ctx.get("search_channels") or ["x", "web"]
    ratios = ctx.get("channel_limit_ratios") or {}
    if not isinstance(ratios, dict):
        ratios = {}
    plan: list[tuple[str, int]] = []
    for ch in channels:
        if ch not in ("x", "web"):
            continue
        raw_ratio = ratios.get(ch, 1.0)
        try:
            ratio = float(raw_ratio)
        except (TypeError, ValueError):
            ratio = 1.0
        if ratio <= 0:
            continue
        n = max(1, int(round(limit * ratio)))
        plan.append((ch, n))
    return plan or [("x", limit), ("web", limit)]


_DEFAULT_EVIDENCE_FAMILIES = """\
A. EXPLICIT_PAIN — they state the friction directly (hard to find prior decisions,
knowledge in heads, scattered files, starting over).
B. WORKAROUND — manual process they already run to recover prior judgment (old memos,
spreadsheets, partner pinging, precedent libraries). CRM/storage alone is weak.
C. BEHAVIORAL_REUSE — past situation → judgment → lesson/outcome → reused next time.
Language like playbook, pattern recognition, lessons learned, precedent, seen this before,
repeatable approach, what worked / what didn't. Often the strongest signal.
D. OUTCOME_FEEDBACK — they later check whether a prior call was right (were we right to
pass, postmortem, actual vs original assumptions, reviewing misses).
E. INSTITUTIONAL_MEMORY — team/firm-level preservation (shared memory, onboarding off
prior work, internal playbooks, knowledge transfer). Look for the behavior, not a slogan."""


def discovery_prompt(ctx: dict[str, Any], limit: int, channel: str = "web") -> str:
    if channel == "x":
        channel_rules = (
            "Search ONLY X (Twitter) with x_search. Posts, replies, and threads. "
            "Do not use web_search. Every signal source must be x."
        )
    elif ctx.get("prefer_web"):
        channel_rules = (
            "Search ONLY the public web with web_search. Prefer company sites, "
            "hiring pages, funding/news, LinkedIn public posts and profiles "
            "(site:linkedin.com), blogs, and interviews. "
            "Do not use x_search. Signal source should be linkedin, web, or reddit."
        )
    else:
        channel_rules = (
            "Search ONLY the public web with web_search (Reddit, blogs, forums, interviews). "
            "Do not use x_search. Signal source should be reddit or web."
        )
    evidence = (ctx.get("evidence_families") or "").strip() or _DEFAULT_EVIDENCE_FAMILIES
    ontology = str(ctx.get("signal_ontology") or "").strip()
    if ontology == "decision_reasoning":
        kinds = (
            "STORAGE|REASONING_CAPTURE|RETRIEVAL|REUSE|OUTCOME_FEEDBACK|CONTINUITY|"
            "EXPLICIT_PAIN|WORKAROUND|BEHAVIORAL_REUSE|INSTITUTIONAL_MEMORY|"
            "COMPANY_TRIGGER|adjacent_reuse|other"
        )
        crm_rule = (
            "Do NOT treat CRM usage, document storage, knowledge-management tooling, or "
            "'institutional memory' slogans by themselves as evidence of the problem. "
            "Look for evidence that people preserve, retrieve, compare, revise, or evaluate "
            "the reasoning behind prior decisions.\n"
            "STORAGE-only signals (e.g. 'we use Salesforce', 'deal history in CRM') must be "
            "relevance=generic, not highly_relevant.\n"
            "Minimum Akashic bar: REASONING_CAPTURE plus RETRIEVAL or REUSE.\n"
        )
    elif ctx.get("prefer_web") or (ctx.get("evidence_families") or "").strip():
        kinds = (
            "EXPLICIT_PAIN|WORKAROUND|BEHAVIORAL_REUSE|OUTCOME_FEEDBACK|"
            "INSTITUTIONAL_MEMORY|COMPANY_TRIGGER|adjacent_reuse|other"
        )
        crm_rule = ""
    else:
        kinds = (
            "EXPLICIT_PAIN|WORKAROUND|BEHAVIORAL_REUSE|OUTCOME_FEEDBACK|"
            "INSTITUTIONAL_MEMORY|adjacent_reuse|other"
        )
        crm_rule = ""
    query_examples = ctx.get("search_query_examples") or []
    examples_block = ""
    if query_examples:
        examples_block = (
            "Example behavior-centered searches (adapt phrasing; do not copy blindly):\n"
            + _bullet([str(q) for q in query_examples])
            + "\n"
        )
    return f"""You are Trace's public-signal researcher. You find evidence, not sales leads.

{format_discovery_brief(ctx)}

Search for the behavior surrounding the problem, not just the vocabulary of the problem.
People rarely name the product category. They talk about what they complain about,
what workaround they already run, what they repeat from prior situations, what they
spend effort remembering, which prior decisions they revisit, and how previous
experience changes the next decision.

Convert the product into a latent operational concept, then search several practitioner
phrasings of that concept. Do not stop at one keyword family.

Do not search primarily for people asking for the product, a category, or a solution
(examples of solution language to avoid as primary queries: "decision memory",
"institutional memory", "CJR", "on-prem AI", "knowledge management tool").

{crm_rule}{examples_block}Evidence families — search each independently. Explicit pain wording is not required.

{evidence}

"We do this and it is annoying / we keep redoing it" is a strong signal.
"That became our playbook / it's pattern recognition / we learned from the last one"
is also a strong signal, even if they never say they have a problem.
Solution-aware tool-seeking is weaker than first-person workflow behavior.

{channel_rules}

Find up to {limit} distinct public signals.

Ranking / selection rules (apply on every product):
1. Prefer identifiable people with a current professional identity.
   Anonymous forum posts can be returned if they are strong evidence, but they are weaker
   than a named person.
2. Older evidence is not automatically weak. A 2022 description of the same behavior is
   still evidence. If you also find a recent corroboration from the same person or firm,
   that strengthens the signal; do not drop the older one.
3. Prefer behavioral reuse, retrieval, reasoning capture, outcome feedback, and continuity
   over storage-only CRM mentions ("we use Salesforce" with no judgment reuse).
4. Prefer first-person operational evidence ("our team…", "we still manually…", "I usually…")
   over generic thought-leadership ("AI will transform…", "companies need…").

Comments, interviews, and replies matter as much as standalone posts.

Do NOT decide if someone is a prospect. Do NOT invent URLs, names, or quotes.
If identity is anonymous, still return the signal with whatever handle you have.
Finding one strong quote is not a reason to stop looking for other phrasings of the
same latent behavior.

Return JSON only:
{{
  "search_concepts": ["latent concept and practitioner phrasings you actually searched"],
  "signals": [
    {{
      "source": "x|reddit|web|linkedin",
      "source_url": "https://...",
      "author_name": "",
      "author_handle": "",
      "published_at": "",
      "signal_text": "verbatim or close paraphrase of what they said",
      "why_relevant": "one sentence",
      "latent_behavior": "underlying behavior, not the keyword",
      "relevance": "relevant|highly_relevant|generic|irrelevant",
      "evidence_kind": "{kinds}"
    }}
  ]
}}
"""


def person_research_prompt(
    ctx: dict[str, Any],
    signal: dict[str, Any],
    extra_signals: list[dict[str, Any]] | None = None,
) -> str:
    extra_block = ""
    if extra_signals:
        bits = []
        for sig in extra_signals:
            bits.append(
                f"- Source: {sig.get('source')} | {sig.get('source_url')} | "
                f"{sig.get('published_at')}\n  {(sig.get('signal_text') or '')[:400]}"
            )
        extra_block = "\nAdditional public signals from the same person:\n" + "\n".join(bits) + "\n"
    return f"""You are Trace's identity researcher. Qualification is relative to THIS product only.
Do not reject someone only because they are a founder.
Do not reject someone only because their current title is not the core workflow owner.

{format_discovery_brief(ctx)}

Public signal:
- Source: {signal.get('source')}
- URL: {signal.get('source_url')}
- Author: {signal.get('author_name')} ({signal.get('author_handle')})
- Date: {signal.get('published_at')}
- Text: {signal.get('signal_text')}
- Why surfaced: {signal.get('why_relevant')}
- Latent behavior: {signal.get('latent_behavior') or ''}
- Evidence kind: {signal.get('evidence_kind') or ''}
{extra_block}
Score what they are actually describing or doing BEFORE you score their title.

Do not treat CRM usage, document storage, or knowledge-management tooling by itself as
evidence of the problem. Look for preserve / retrieve / compare / revise / evaluate
behavior around prior decision reasoning. Storage-only quotes are weak.

Use web_search (and x_search if helpful) to find who this author is professionally.
If you cannot resolve a real identity (anonymous Reddit, throwaway, etc.), say so.
Never fabricate a name, company, or LinkedIn URL.

actor_type:
- PRACTITIONER: does this work themselves (or is a founder/operator in the ICP doing the work).
- BUILDER_OR_VENDOR: building a product/service around this problem, or selling into it.
- OTHER: commentator or unrelated.
- UNKNOWN: cannot tell.

Score these axes separately. Do not collapse them into one number.
Each is VERY_HIGH|HIGH|MEDIUM|LOW|UNKNOWN:
- persona_fit
- pain_evidence
- behavioral_evidence
- workaround_evidence
- outcome_feedback_evidence
- influence_or_champion_potential
- economic_buyer_likelihood
- end_user_likelihood

recommendation — "not the underwriting/workflow owner" is NOT enough for LIKELY_NOT_PROSPECT:
- PRIMARY_PROSPECT / LIKELY_PROSPECT: likely user or buyer of the workflow.
- CHAMPION_CANDIDATE: may not own the core workflow, but describes the behavior strongly
  and could influence adoption (process designers, sourcing/ops sitting on historical
  pipeline, people who connect teams).
- HIGH_VALUE_DISCOVERY: unusually strong public evidence; worth a human look even if persona is imperfect.
- ADJACENT_PRACTITIONER: nearby work, weaker fit.
- PAIN_SIGNAL_ONLY: useful quote, person is not a prospect.
- LIKELY_NOT_PROSPECT / LIKELY_NOT_RELEVANT: vendor/builder, or truly unrelated.
- UNCLEAR: not enough public identity.

A founder can be PRACTITIONER if they personally do the work (e.g. still making the cold calls).
A founder is BUILDER_OR_VENDOR if they are selling a solution for this problem.
Do not treat "we should build a system for this" as a prospect signal.
Do not use the shortcut: current title → not core user → reject.

Return JSON only:
{{
  "person": {{
    "name": "",
    "title": "",
    "company": "",
    "company_type": "",
    "linkedin_url": "",
    "other_profile": ""
  }},
  "identity_resolved": true,
  "actor_type": "PRACTITIONER|BUILDER_OR_VENDOR|OTHER|UNKNOWN",
  "persona_fit": "UNKNOWN",
  "pain_evidence": "UNKNOWN",
  "behavioral_evidence": "UNKNOWN",
  "workaround_evidence": "UNKNOWN",
  "outcome_feedback_evidence": "UNKNOWN",
  "influence_or_champion_potential": "UNKNOWN",
  "economic_buyer_likelihood": "UNKNOWN",
  "end_user_likelihood": "UNKNOWN",
  "recommendation": "PRIMARY_PROSPECT|CHAMPION_CANDIDATE|HIGH_VALUE_DISCOVERY|ADJACENT_PRACTITIONER|PAIN_SIGNAL_ONLY|LIKELY_PROSPECT|LIKELY_NOT_PROSPECT|LIKELY_NOT_RELEVANT|UNCLEAR",
  "recommendation_reason": "one or two sentences, product-relative; lead with the behavior, then the role"
}}
"""


def entity_search_fallbacks(name: str, firm: str, title: str = "") -> list[str]:
    """Name/firm variants so a page typo or first-name-only mention still surfaces."""
    first, last = split_name(name)
    out: list[str] = []
    seen: set[str] = set()

    def add(q: str) -> None:
        q = " ".join(q.split())
        if q and q not in seen:
            seen.add(q)
            out.append(q)

    if name:
        add(f'"{name}" interview')
        add(f'"{name}" podcast')
        add(f'"{name}"')
    if first and firm:
        add(f'"{firm}" "{first}"')
    if last and firm:
        add(f'"{firm}" "{last}"')
    if firm and title:
        add(f'"{firm}" "{title}"')
    if firm:
        add(f'"{firm}" interview')
        add(f'"{firm}" lessons learned')
        add(f'"{firm}" playbook')
    return out


def should_deepen(signal: dict[str, Any], qual: dict[str, Any]) -> bool:
    if not is_identifiable(signal):
        return False
    if (signal.get("relevance") or "relevant") not in RELEVANCE_KEEP:
        return False
    if normalize_actor(str(qual.get("actor_type") or "")) == "BUILDER_OR_VENDOR":
        return False
    if not identity_resolved_enough(signal, qual):
        return False
    person = qual.get("person") or {}
    if not (person.get("name") or signal.get("author_name")):
        return False
    return bool(qual.get("researched"))


def deepening_prompt(ctx: dict[str, Any], signal: dict[str, Any], qual: dict[str, Any]) -> str:
    person = qual.get("person") or {}
    name = person.get("name") or signal.get("author_name") or ""
    firm = person.get("company") or ""
    title = person.get("title") or ""
    queries = entity_search_fallbacks(name, firm, title)
    query_block = "\n".join(f"- {q}" for q in queries)
    return f"""You are Trace's person-deepening researcher. A relevant public signal was already found.
That is a reason to search MORE about this person, not to stop.

{format_discovery_brief(ctx)}

Known person:
- Name: {name}
- Title: {title}
- Firm: {firm}
- Already-found URL: {signal.get('source_url')}
- Already-found quote: {signal.get('signal_text')}
- Date: {signal.get('published_at')}

Run web_search using several of these query families. Do not rely on exact full-name match only.
Public pages misspell surnames, use first name only, old titles, or firm + first name.

Query families:
{query_block}

Also combine the firm name with practitioner language from THIS product brief
(playbook, pattern recognition, lessons learned, prior situations, what worked / didn't,
repeatable approach — whatever matches the latent behavior, not a fixed industry glossary).

Look for interviews, podcasts, conference talks, firm commentary, and older pieces.
Return additional evidence of:
- EXPLICIT_PAIN, WORKAROUND, BEHAVIORAL_REUSE, OUTCOME_FEEDBACK, INSTITUTIONAL_MEMORY

If an older source and a newer source describe the same behavior, that corroboration
STRENGTHENS the signal. Do not drop old evidence.

Do not invent quotes or URLs. If nothing else exists, return an empty evidence list.

Return JSON only:
{{
  "evidence": [
    {{
      "source_url": "",
      "source_date": "",
      "evidence_type": "EXPLICIT_PAIN|WORKAROUND|BEHAVIORAL_REUSE|OUTCOME_FEEDBACK|INSTITUTIONAL_MEMORY|OTHER",
      "quote_or_paraphrase": "",
      "latent_behavior": "",
      "why_relevant": ""
    }}
  ],
  "persona_fit": "UNKNOWN",
  "pain_evidence": "UNKNOWN",
  "behavioral_evidence": "UNKNOWN",
  "workaround_evidence": "UNKNOWN",
  "outcome_feedback_evidence": "UNKNOWN",
  "influence_or_champion_potential": "UNKNOWN",
  "economic_buyer_likelihood": "UNKNOWN",
  "end_user_likelihood": "UNKNOWN",
  "recommendation": "",
  "recommendation_reason": ""
}}
"""


def parse_deepening(raw_text: str) -> dict[str, Any]:
    try:
        payload = parse_json_payload(raw_text)
    except (json.JSONDecodeError, ValueError, TypeError):
        return {"evidence": [], "axes": extract_axes({})}
    if not isinstance(payload, dict):
        return {"evidence": [], "axes": extract_axes({})}
    rows = payload.get("evidence") or payload.get("signals") or []
    evidence: list[dict[str, Any]] = []
    if isinstance(rows, list):
        for item in rows:
            if not isinstance(item, dict):
                continue
            quote = str(item.get("quote_or_paraphrase") or item.get("signal_text") or "").strip()
            url = str(item.get("source_url") or item.get("url") or "").strip()
            if not quote and not url:
                continue
            evidence.append({
                "source_url": url,
                "source_date": str(item.get("source_date") or item.get("published_at") or "").strip(),
                "evidence_type": str(item.get("evidence_type") or item.get("evidence_kind") or "OTHER").strip(),
                "quote_or_paraphrase": quote,
                "latent_behavior": str(item.get("latent_behavior") or "").strip(),
                "why_relevant": str(item.get("why_relevant") or "").strip(),
            })
    return {
        "evidence": evidence,
        "axes": extract_axes(payload),
        "recommendation": str(payload.get("recommendation") or "").strip(),
        "recommendation_reason": str(payload.get("recommendation_reason") or "").strip(),
    }


def _max_axes(left: dict[str, str], right: dict[str, str]) -> dict[str, str]:
    out = dict(left)
    for key in CLASSIFICATION_AXES:
        if _axis_level(right.get(key)) > _axis_level(out.get(key)):
            out[key] = right[key]
    return out


def apply_deepening(
    qual: dict[str, Any],
    extra: dict[str, Any],
    signal: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(qual)
    evidence = list(extra.get("evidence") or [])
    merged["supporting_evidence"] = evidence
    axes = _max_axes(extract_axes(qual), extra.get("axes") or {})
    merged.update(axes)
    if extra.get("recommendation"):
        merged["recommendation"] = extra["recommendation"]
    if extra.get("recommendation_reason"):
        reason = extra["recommendation_reason"]
        if merged.get("recommendation_reason") and reason not in merged["recommendation_reason"]:
            merged["recommendation_reason"] = f"{merged['recommendation_reason']} {reason}".strip()
        else:
            merged["recommendation_reason"] = reason or merged.get("recommendation_reason") or ""
    dates = {
        str(signal.get("published_at") or "").strip(),
        *(str(ev.get("source_date") or "").strip() for ev in evidence),
    }
    dates.discard("")
    if len(dates) >= 2:
        note = (
            " Same behavior appears across multiple dates ("
            + ", ".join(sorted(dates))
            + ")."
        )
        merged["recommendation_reason"] = (merged.get("recommendation_reason") or "") + note
    merged["recommendation"] = derive_recommendation(
        actor_type=str(merged.get("actor_type") or ""),
        raw_recommendation=str(merged.get("recommendation") or ""),
        axes=extract_axes(merged),
    )
    return merged


def deepen_person(
    ctx: dict[str, Any],
    signal: dict[str, Any],
    qual: dict[str, Any],
    researcher: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    person = qual.get("person") or {}
    result = researcher(
        deepening_prompt(ctx, signal, qual),
        tools=[{"type": "web_search"}],
        stage="deepening",
        person_name=person.get("name") or signal.get("author_name") or "",
        entity_key=entity_key_for(
            person.get("name") or signal.get("author_name") or "",
            person.get("company") or "",
            signal.get("author_handle") or "",
        ),
        signal_url=signal.get("source_url") or "",
    )
    return parse_deepening(result.get("text") or "")


def _unresolved_qualification(signal: dict[str, Any]) -> dict[str, Any]:
    generic = (signal.get("relevance") or "") not in RELEVANCE_KEEP
    return {
        "person": empty_person(),
        "actor_type": "UNKNOWN" if not generic else "OTHER",
        "recommendation": "LIKELY_NOT_PROSPECT" if generic else "UNCLEAR",
        "recommendation_reason": (
            "Generic or weakly relevant commentary; skipped person research."
            if generic
            else "Relevant discussion found, but current role and relationship to the workflow could not be verified."
        ),
        "researched": False,
        **extract_axes({}),
    }


def qualify_signal(
    ctx: dict[str, Any],
    signal: dict[str, Any],
    *,
    researcher: Callable[..., dict[str, Any]],
    extra_signals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not should_research_person(signal):
        return _unresolved_qualification(signal)
    result = researcher(
        person_research_prompt(ctx, signal, extra_signals),
        tools=[{"type": "web_search"}, {"type": "x_search"}],
        stage="qualification",
        person_name=signal.get("author_name") or "",
        entity_key=entity_key_for(
            signal.get("author_name") or "",
            "",
            signal.get("author_handle") or "",
        ),
        signal_url=signal.get("source_url") or "",
    )
    try:
        payload = parse_json_payload(result.get("text") or "")
    except (json.JSONDecodeError, ValueError):
        return _unresolved_qualification(signal)
    if not isinstance(payload, dict):
        return _unresolved_qualification(signal)
    person = normalize_person(payload.get("person") if isinstance(payload.get("person"), dict) else payload, signal)
    identity = payload.get("identity_resolved")
    axes = extract_axes(payload)
    unresolved = identity is False or (
        not person.get("name")
        and not person.get("linkedin_url")
        and not (signal.get("author_name") or "").strip()
    )
    if unresolved or not identity_resolved_enough(signal, {"person": person, "identity_resolved": identity}):
        rec = (
            "HIGH_VALUE_DISCOVERY"
            if (signal.get("relevance") or "") in RELEVANCE_KEEP
            else "UNCLEAR"
        )
        return {
            "person": person,
            "actor_type": "UNKNOWN",
            "recommendation": rec,
            "recommendation_reason": str(
                payload.get("recommendation_reason")
                or "Strong public signal kept as market evidence; current role could not be verified."
            ),
            "researched": True,
            "identity_resolved": False,
            **axes,
        }
    actor = normalize_actor(str(payload.get("actor_type") or ""))
    rec = derive_recommendation(
        actor_type=actor,
        raw_recommendation=str(payload.get("recommendation") or ""),
        axes=axes,
    )
    return {
        "person": person,
        "actor_type": actor,
        "recommendation": rec,
        "recommendation_reason": str(payload.get("recommendation_reason") or "").strip(),
        "researched": True,
        "identity_resolved": True,
        **axes,
    }


def _search_channel(
    fn: Callable[..., dict[str, Any]],
    ctx: dict[str, Any],
    limit: int,
    channel: str,
) -> dict[str, Any]:
    tools = [{"type": "x_search"}] if channel == "x" else [{"type": "web_search"}]
    print(f"  [{channel}] searching…")
    out = fn(
        discovery_prompt(ctx, limit, channel),
        tools=tools,
        stage=f"discovery_{channel}",
    )
    n = len(parse_signal_list(out.get("text") or "", out.get("citations") or []))
    print(f"  [{channel}] {n} signals")
    return out


def run_discovery(
    profile: dict[str, Any],
    *,
    list_name: str,
    profile_key: str,
    limit: int = 8,
    researcher: Callable[..., dict[str, Any]] | None = None,
    cost_log_path: str | None = None,
    run_id: str | None = None,
    cache_path: str | None = None,
    seed_candidate_paths: list[str] | None = None,
    on_stage: Callable[[str, str], None] | None = None,
) -> list[dict[str, Any]]:
    ctx = discovery_context_from_profile(profile)
    inner = researcher or grok_research
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    def fn(prompt, tools=None, **kwargs):
        stage = str(kwargs.pop("stage", "") or "unknown")
        person_name = str(kwargs.pop("person_name", "") or "")
        entity_key = str(kwargs.pop("entity_key", "") or "")
        signal_url = str(kwargs.pop("signal_url", "") or "")
        if on_stage:
            on_stage(stage, person_name)
        started = time.monotonic()
        result = _invoke_researcher(
            inner,
            prompt,
            tools=tools,
            prompt_cache_key=f"trace:{profile_key}:{stage}",
        )
        elapsed = time.monotonic() - started
        if not isinstance(result, dict):
            result = {"text": str(result or ""), "citations": [], "usage": {}}
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        event = {
            "run_id": run_id,
            "stage": stage,
            "request_id": usage.get("request_id"),
            "person_name": person_name,
            "entity_key": entity_key,
            "signal_url": signal_url,
            "prompt_tokens": usage.get("prompt_tokens"),
            "cached_prompt_tokens": usage.get("cached_prompt_tokens"),
            "cache_ratio": usage.get("cache_ratio"),
            "reasoning_tokens": usage.get("reasoning_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "web_calls_attempted": usage.get("web_calls_attempted"),
            "web_calls_billable": usage.get("web_calls_billable"),
            "x_calls_attempted": usage.get("x_calls_attempted"),
            "x_calls_billable": usage.get("x_calls_billable"),
            "cost_usd": usage.get("cost_usd"),
            "elapsed_sec": round(elapsed, 3),
            "evidence_count": _guess_evidence_count(result.get("text") or ""),
            "citation_count": len(result.get("citations") or []),
        }
        if cost_log_path:
            append_research_cost(cost_log_path, event)
            cost = event.get("cost_usd")
            cost_s = f"${cost:.4f}" if cost is not None else "n/a"
            who = f" {person_name}" if person_name else ""
            web_b, web_a = event["web_calls_billable"], event["web_calls_attempted"]
            x_b, x_a = event["x_calls_billable"], event["x_calls_attempted"]
            print(
                f"  [{stage}]{who}  {cost_s}  {event['elapsed_sec']:.1f}s  "
                f"web={web_b}/{web_a}  x={x_b}/{x_a}"
            )
        return result
    plan = discovery_channel_plan(ctx, limit)
    if on_stage:
        on_stage("search", "")
    print(
        "Searching "
        + " + ".join(f"{ch} (up to {n})" for ch, n in plan)
        + "…"
    )
    found: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(2, max(1, len(plan)))) as pool:
        futs = {
            pool.submit(_search_channel, fn, ctx, n, ch): ch
            for ch, n in plan
        }
        for fut in as_completed(futs):
            found[futs[fut]] = fut.result()
    signals: list[dict[str, Any]] = []
    for ch, _n in plan:
        out = found.get(ch) or {}
        items = parse_signal_list(out.get("text") or "", out.get("citations") or [])
        if ch == "x":
            for sig in items:
                if not sig.get("source") or sig.get("source") == "web":
                    sig["source"] = infer_source(sig.get("source_url") or "", "x")
        signals.extend(items)
    if profile_key == "akashic":
        signals = [refine_akashic_signal(sig) for sig in signals]
    signals = rank_signals(
        dedupe_signals(signals),
        prefer_web=bool(ctx.get("prefer_web")),
    )
    snapshot = None
    if profile_key not in ("akashic", "problem_validation"):
        snapshot = {
            "profile_kind": profile.get("profile_kind"),
            "product_name": profile.get("product_name"),
            "product_context": profile.get("product_context"),
            "sign_off": profile.get("sign_off"),
            "angles": profile.get("angles"),
            "email_mode": profile.get("email_mode"),
            "sender_block": profile.get("sender_block"),
            "discovery": ctx,
        }
    cache_rows = load_research_cache(cache_path) if cache_path else []
    cache_index = build_cache_index(cache_rows)
    for seed_path in seed_candidate_paths or []:
        seed_cache_from_candidates(
            cache_rows,
            cache_index,
            load_candidates(seed_path),
            profile_key,
        )
    groups = aggregate_signals(signals)
    to_research = [g for g in groups if should_research_person(g["primary"])]
    print(
        f"Qualifying {len(to_research)} people "
        f"({len(groups)} signal groups from {len(signals)} signals)…"
    )
    quals: dict[int, dict[str, Any]] = {}
    live_research: list[tuple[int, dict[str, Any]]] = []
    for i, group in enumerate(groups):
        primary = group["primary"]
        if not should_research_person(primary):
            continue
        extras = group["signals"][1:]
        cached = lookup_research_cache(cache_index, profile_key, primary)
        if cached:
            new_urls = [
                s.get("source_url") or ""
                for s in group["signals"]
                if not cache_has_source(cached, s.get("source_url") or "")
            ]
            quals[i] = qualification_from_cache(cached, primary)
            quals[i]["skip_deepen"] = bool(cached.get("deepened") or not cached.get("identity_resolved"))
            who = (cached.get("person") or {}).get("name") or primary.get("author_name") or group["entity_key"]
            print(
                f"  cache hit {who}"
                + (f" (+{len(new_urls)} new signal)" if new_urls else "")
                + " — skip qualification"
            )
            continue
        live_research.append((i, group))
    if live_research:
        with ThreadPoolExecutor(max_workers=min(4, max(1, len(live_research)))) as pool:
            futs = {
                pool.submit(
                    qualify_signal,
                    ctx,
                    group["primary"],
                    researcher=fn,
                    extra_signals=group["signals"][1:],
                ): i
                for i, group in live_research
            }
            done = 0
            for fut in as_completed(futs):
                quals[futs[fut]] = fut.result()
                done += 1
                print(f"  qualified {done}/{len(live_research)}")
    deepen_idxs = [
        i for i, group in enumerate(groups)
        if i in quals
        and not quals[i].get("skip_deepen")
        and should_deepen(group["primary"], quals[i])
    ][:DEEPENING_BUDGET]
    if deepen_idxs:
        print(
            f"Deepening {len(deepen_idxs)} resolved people "
            "(unresolved identity is kept as signal, not a dossier)…"
        )
        with ThreadPoolExecutor(max_workers=min(4, max(1, len(deepen_idxs)))) as pool:
            deep_futs = {
                pool.submit(deepen_person, ctx, groups[i]["primary"], quals[i], fn): i
                for i in deepen_idxs
            }
            done = 0
            for fut in as_completed(deep_futs):
                i = deep_futs[fut]
                quals[i] = apply_deepening(quals[i], fut.result(), groups[i]["primary"])
                quals[i]["deepened"] = True
                done += 1
                print(f"  deepened {done}/{len(deepen_idxs)}")
    candidates: list[dict[str, Any]] = []
    for i, group in enumerate(groups):
        primary = group["primary"]
        if i in quals:
            qual = quals[i]
        else:
            qual = qualify_signal(ctx, primary, researcher=fn)
        extra = {k: qual[k] for k in CLASSIFICATION_AXES if k in qual}
        if qual.get("supporting_evidence"):
            extra["supporting_evidence"] = qual["supporting_evidence"]
        if primary.get("latent_behavior"):
            extra["latent_behavior"] = primary["latent_behavior"]
        extra["entity_key"] = group["entity_key"]
        extra["identity_resolved"] = qual.get("identity_resolved")
        extra["cache_hit"] = bool(qual.get("cache_hit"))
        extra["deepened"] = bool(qual.get("deepened"))
        extra["signals"] = group["signals"]
        if len(group["signals"]) > 1:
            extra["additional_signals"] = group["signals"][1:]
        rec = build_candidate(
            signal=primary,
            person=qual.get("person"),
            actor_type=qual.get("actor_type") or "UNKNOWN",
            recommendation=qual.get("recommendation") or "UNCLEAR",
            recommendation_reason=qual.get("recommendation_reason") or "",
            list_name=list_name,
            profile_key=profile_key,
            product_name=ctx.get("product_name") or profile.get("product_name") or "",
            product_snapshot=snapshot,
            researched=bool(qual.get("researched")),
            extra=extra or None,
        )
        candidates.append(rec)
        if cache_path:
            upsert_cache_entry(cache_rows, cache_index, candidate_to_cache_entry(rec))
    if cache_path:
        save_research_cache(cache_path, cache_rows)
        print(f"Research cache: {len(cache_rows)} people → {cache_path}")
    return candidates
