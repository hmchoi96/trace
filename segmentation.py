"""
Deterministic segment classification and derived campaign fields for Apollo leads.
"""

from __future__ import annotations

import re
from typing import Any

# ─── Five recommended segments (single label per lead) ───────────────────────

SEG_SALES_LEADER = "Sales Leader / Revenue Leader"
SEG_FOUNDER_ENGINEER = "Founder-Engineer / Technical Founder"
SEG_COMPLEX = "Complex Product / Trust-Heavy Industry"
SEG_EARLY_TEAM = "Early Team / Seed Stage"
SEG_FOUNDER_LED = "Founder-led Sales"

# Trust-heavy signals (industry or keywords substrings), lowercase match
_TRUST_HEAVY = (
    "medical",
    "healthcare",
    "pharma",
    "biotech",
    "hipaa",
    "fintech",
    "bank",
    "banking",
    "insurance",
    "compliance",
    "security",
    "fraud",
    "defense",
    "government",
    "cryptocurrency",
    "semiconductor",
    "aerospace",
    "aerospace & defense",
)

# Sales leadership title signals
_SALES_LEADER_TITLE = (
    "cro",
    "chief revenue",
    "vp of sales",
    "vice president of sales",
    "vp sales",
    "svp sales",
    "evp sales",
    "head of sales",
    "head of revenue",
    "director of sales",
    "sales director",
    "revenue leader",
    "head of business development",
    "vp business development",
)


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _parse_employee_count(raw: str) -> int | None:
    if not raw or not str(raw).strip():
        return None
    m = re.search(r"(\d+)", str(raw))
    if not m:
        return None
    return int(m.group(1))


def normalize_csv_row(row: dict[str, Any]) -> dict[str, Any]:
    """Map Apollo CSV DictReader row to internal whitelist keys."""
    first = (row.get("First Name") or "").strip()
    last = (row.get("Last Name") or "").strip()
    company = (
        (row.get("Company Name for Emails") or row.get("Company Name") or "")
        .strip()
    )
    departments = (row.get("Departments") or "").strip()
    sub_departments = (row.get("Sub Departments") or "").strip()
    dept_merged = ", ".join(
        x for x in (departments, sub_departments) if x
    )

    city = (row.get("City") or "").strip()
    state = (row.get("State") or "").strip()
    country = (row.get("Country") or "").strip()
    location_parts = [p for p in (city, state, country) if p]
    location = ", ".join(location_parts)

    funding_latest = (row.get("Latest Funding") or "").strip()
    funding_total = (row.get("Total Funding") or "").strip()
    if funding_latest and funding_total and funding_latest != funding_total:
        funding_stage = f"{funding_latest} (total funding field: {funding_total})"
    else:
        funding_stage = funding_latest or funding_total

    return {
        "first_name": first,
        "last_name": last,
        "name": f"{first} {last}".strip(),
        "email": (row.get("Email") or "").strip(),
        "title": (row.get("Title") or "").strip(),
        "company": company or "Unknown",
        "industry": (row.get("Industry") or "").strip(),
        "keywords": (row.get("Keywords") or "").strip(),
        "company_description": (
            (row.get("Company Description") or "").strip()
            or (row.get("Company Description Keywords") or "").strip()
        ),
        "employee_count": _parse_employee_count(row.get("# Employees") or ""),
        "employee_count_raw": (row.get("# Employees") or "").strip(),
        "funding_stage": funding_stage,
        "technologies": (row.get("Technologies") or "").strip(),
        "department": dept_merged,
        "departments": departments,
        "sub_departments": sub_departments,
        "seniority": (row.get("Seniority") or "").strip(),
        "location": location,
        "linkedin_url": (row.get("Person Linkedin Url") or "").strip(),
        "website": (row.get("Website") or "").strip(),
    }


def _founderish(title: str, seniority: str) -> bool:
    t = _norm(title)
    s = _norm(seniority)
    return (
        "founder" in t
        or "co-founder" in t
        or "cofounder" in t
        or "founding engineer" in t
        or "ceo" in t.split()
        or ("chief executive" in t)
        or s == "founder"
    )


def _technical_founder_signals(title: str, sub_dept: str, departments: str) -> bool:
    t = _norm(title)
    sd = _norm(sub_dept)
    d = _norm(departments)
    if "engineer" in t or "engineering" in t:
        return True
    if "founding engineer" in t:
        return True
    if "cto" in t and ("founder" in t or "co-founder" in t):
        return True
    if "engineering & technical" in sd or "engineering & technical" in d:
        return True
    if "engineer" in sd and "founder" in t:
        return True
    return False


def _sales_leader_match(title: str, departments: str) -> bool:
    t = _norm(title)
    d = _norm(departments)
    if any(x in t for x in _SALES_LEADER_TITLE):
        return True
    if ("vp" in t or "vice president" in t) and (
        "sales" in t or "revenue" in t or "business development" in t
    ):
        return True
    if "head " in t and ("sales" in t or "revenue" in t):
        return True
    if "director" in t and "sales" in t:
        return True
    if d and "sales" in d and ("vp" in t or "director" in t or "head" in t):
        return True
    return False


def _trust_heavy_match(industry: str, keywords: str) -> bool:
    blob = f"{_norm(industry)} {_norm(keywords)}"
    return any(term in blob for term in _TRUST_HEAVY)


def _early_team_match(emp: int | None, funding: str) -> bool:
    f = _norm(funding)
    if "seed" in f or "pre-seed" in f or "preseed" in f:
        return True
    if emp is not None and 1 <= emp <= 40:
        return True
    return False


def classify_segment(lead: dict[str, Any]) -> tuple[str, str]:
    """
    Return (segment_label, segment_reason) deterministically.
    Tie-break order: Sales Leader → Founder-Engineer → Complex industry
    → Early Team → Founder-led Sales (default).
    """
    title = lead.get("title") or ""
    industry = lead.get("industry") or ""
    keywords = lead.get("keywords") or ""
    departments = lead.get("departments") or ""
    sub_dept = lead.get("sub_departments") or ""
    seniority = lead.get("seniority") or ""
    funding = lead.get("funding_stage") or ""
    emp = lead.get("employee_count")

    if _sales_leader_match(title, departments):
        return (
            SEG_SALES_LEADER,
            f'Rule R1: sales leadership pattern matched on Title="{title}" '
            f"and/or Departments/role keywords.",
        )

    if _technical_founder_signals(title, sub_dept, departments) and _founderish(
        title, seniority
    ):
        return (
            SEG_FOUNDER_ENGINEER,
            f'Rule R2: Founder/C-Suite + engineering signals in Title="{title}" '
            f'or Sub Departments="{sub_dept}".',
        )

    if _trust_heavy_match(industry, keywords):
        return (
            SEG_COMPLEX,
            f'Rule R3: Industry/Keywords matched trust-heavy heuristics '
            f'(Industry="{industry[:80]}…").',
        )

    if _early_team_match(emp, funding):
        return (
            SEG_EARLY_TEAM,
            f"Rule R4: Seed-stage or small team (Employees={lead.get('employee_count_raw')}, "
            f'Latest/Total funding="{funding[:60]}").',
        )

    if _founderish(title, seniority):
        return (
            SEG_FOUNDER_LED,
            f'Rule R5: Founder-led / exec selling role Title="{title}", Seniority="{seniority}".',
        )

    return (
        SEG_FOUNDER_LED,
        f"Rule R6 default: no higher-priority rule matched (Title={title!r}).",
    )


def _email_angle_for_segment(segment: str) -> str:
    if segment == SEG_SALES_LEADER:
        return "sales_leader_hypothesis"
    if segment == SEG_FOUNDER_ENGINEER:
        return "technical_founder"
    if segment == SEG_COMPLEX:
        return "trust_heavy_context"
    if segment == SEG_EARLY_TEAM:
        return "founder_peer"
    return "founder_peer"


def _buyer_angle_line(segment: str, lead: dict[str, Any]) -> str:
    """One line for model context: facts from Apollo only, plus segment label."""
    ind = (lead.get("industry") or "")[:120]
    base = (
        f"SEGMENT={segment}. Title={lead.get('title')!r}, "
        f"Company={lead.get('company')!r}, Industry={ind!r}."
    )
    return base


def _research_basis(lead: dict[str, Any], segment: str, segment_reason: str) -> str:
    parts = [
        f"segment={segment}",
        segment_reason,
        f"fields_used: title, industry, keywords, departments, sub_departments, "
        f"seniority, # Employees, funding, technologies, location, website, linkedin",
    ]
    return " | ".join(parts)


def _likely_objection_context(segment: str, lead: dict[str, Any]) -> str:
    """Internal hypothesis only — not asserted as fact to the prospect."""
    ind = lead.get("industry") or ""
    if segment == SEG_SALES_LEADER:
        return (
            "Hypothesis: new reps may burn attention scrolling a long script doc mid-call "
            "instead of listening (ramp / coaching angle; internal planning only)."
        )
    if segment == SEG_FOUNDER_ENGINEER:
        return (
            "Hypothesis: may juggle technical depth with live sales while hunting for the "
            "right script line mid-call (internal planning only)."
        )
    if segment == SEG_COMPLEX:
        return (
            f"Hypothesis: industry {ind[:60]} may mean denser scripts / more objection "
            "branches, so mid-call search cost is higher (internal planning only)."
        )
    if segment == SEG_EARLY_TEAM:
        return (
            "Hypothesis: small team, founder-led outbound, scripts in one long doc "
            "(internal only)."
        )
    return (
        "Hypothesis: founder- or IC-led outbound with mid-call script search friction "
        "(internal only)."
    )


def _priority_score(lead: dict[str, Any], segment: str) -> int:
    score = 50
    if lead.get("email"):
        score += 10
    if lead.get("title"):
        score += 8
    if lead.get("industry"):
        score += 7
    if lead.get("keywords"):
        score += 5
    if lead.get("technologies"):
        score += 5
    return min(100, score)


def subject_line_hint_for_segment(segment: str) -> str:
    """Short lowercase subject line template per segment (vary across leads)."""
    return {
        SEG_SALES_LEADER: "script search during ramp",
        SEG_FOUNDER_ENGINEER: "finding the line mid-call",
        SEG_COMPLEX: "long scripts on live calls",
        SEG_EARLY_TEAM: "cold call script friction",
        SEG_FOUNDER_LED: "scrolling the script mid-call",
    }.get(segment, "scrolling the script mid-call")


def derive_campaign_fields(
    lead: dict[str, Any],
    test_batch: str,
) -> dict[str, Any]:
    segment, segment_reason = classify_segment(lead)
    email_angle = _email_angle_for_segment(segment)
    return {
        "segment": segment,
        "segment_reason": segment_reason,
        "buyer_angle": _buyer_angle_line(segment, lead),
        "likely_objection_context": _likely_objection_context(segment, lead),
        "research_basis": _research_basis(lead, segment, segment_reason),
        "email_angle": email_angle,
        "subject_line_hint": subject_line_hint_for_segment(segment),
        "priority": _priority_score(lead, segment),
        "test_batch": test_batch,
    }


def format_apollo_context_block(lead: dict[str, Any]) -> str:
    """Narrative block for the model: only whitelist facts."""
    lines = [
        "FACTS (Apollo export only — do not invent beyond these):",
        f"- First name: {lead.get('first_name', '')}",
        f"- Title: {lead.get('title', '')}",
        f"- Company: {lead.get('company', '')}",
        f"- Industry: {lead.get('industry', '')}",
        f"- Keywords: {lead.get('keywords', '')}",
        f"- Company description: {lead.get('company_description', '')}",
        f"- Employee count (raw): {lead.get('employee_count_raw', '')}",
        f"- Funding / stage fields: {lead.get('funding_stage', '')}",
        f"- Technologies: {lead.get('technologies', '')[:500]}",
        f"- Departments: {lead.get('department', '')}",
        f"- Seniority: {lead.get('seniority', '')}",
        f"- Location: {lead.get('location', '')}",
        f"- LinkedIn (contact): {lead.get('linkedin_url', '')}",
        f"- Website: {lead.get('website', '')}",
    ]
    return "\n".join(lines)


# Problem-validation email length bands (deterministic; source of truth for gating)
PB_WORD_TARGET_LO = 45
PB_WORD_TARGET_HI = 65
PB_WORD_WARN_HI = 75


def extract_pb_counted_prose(body: str, first_name: str) -> str:
    """Prose counted for length rules: after Hi {{first}}, before signature; no subject."""
    if not (body or "").strip():
        return ""
    lines = body.replace("\r\n", "\n").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    if len(lines) >= 2:
        l2 = lines[-1].strip().lower()
        if l2 in (
            "building helix",
            "helix by wiserbond",
            "helix (by wiserbond)",
        ):
            lines = lines[:-2]
            while lines and not lines[-1].strip():
                lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and first_name:
        fl = lines[0].strip()
        if re.match(r"^Hi\s+" + re.escape(first_name.strip()) + r"\s*,?\s*$", fl, re.I):
            lines = lines[1:]
    while lines and not lines[0].strip():
        lines.pop(0)
    return " ".join(ln.strip() for ln in lines if ln.strip())


def pb_body_length_analysis(
    body: str,
    first_name: str,
    *,
    warn_hi: int = PB_WORD_WARN_HI,
    target_lo: int = PB_WORD_TARGET_LO,
) -> dict[str, Any]:
    """
    Deterministic word count and band. Does not use Claude.
    Default bands: target (>=target_lo), warning (66–75 when warn_hi=75),
    hard_fail (>warn_hi), short (<target_lo).
    Soft aims differ by template; warn_hi is the hard ceiling (normally 75).
    """
    counted = extract_pb_counted_prose(body, first_name)
    n = len(re.findall(r"\S+", counted)) if counted else 0
    if n > warn_hi:
        status = "hard_fail"
    elif warn_hi <= 75 and n >= 66:
        status = "warning"
    elif warn_hi > 75 and n > int(warn_hi * 0.85):
        status = "warning"
    elif n >= target_lo:
        status = "target"
    else:
        status = "short"
    return {
        "body_word_count": n,
        "counted_text": counted,
        "length_status": status,
    }


def word_count_body_minus_signoff(body: str, sign_off: str) -> int:
    """Deprecated path: prefer pb_body_length_analysis with first_name."""
    if not body:
        return 0
    b = body.strip()
    s = sign_off.strip()
    if s and b.endswith(s):
        b = b[: -len(s)].rstrip()
    return len(re.findall(r"\S+", b))
