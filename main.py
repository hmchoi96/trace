"""
Trace

Pipeline: Claude draft → Claude self-critique (rubric) → optional revise →
light humanize (problem_validation) → final safety critique → optional Graph send.
Problem-validation profile: Apollo whitelist fields, deterministic segments,
JSONL audit log on every lead (including blocked).
"""

# 터미널(이 디렉터리): python3 main.py --list helix --limit 10          # 실제 발송은 끝에 --send

from __future__ import annotations

import argparse
import copy
import csv
import glob
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

import anthropic
import requests
from dotenv import load_dotenv

from light_humanize import (
    build_greeting_line,
    claude_light_humanize,
    deterministic_post_humanize_violations,
    run_humanize_jsonl_batch,
)
from segmentation import (
    PB_WORD_WARN_HI,
    derive_campaign_fields,
    format_apollo_context_block,
    normalize_csv_row,
    pb_body_length_analysis,
    SEG_COMPLEX,
    SEG_EARLY_TEAM,
    SEG_FOUNDER_ENGINEER,
    SEG_FOUNDER_LED,
    SEG_SALES_LEADER,
)
from signal_discovery import (
    apply_human_decision,
    candidate_to_lead,
    discovery_context_from_profile,
    enrich_approved_candidates,
    export_approved_csv,
    format_review_card,
    format_signal_evidence_block,
    import_enriched_leads,
    load_candidates,
    load_custom_profile,
    print_research_cost_summary,
    run_discovery,
    save_candidates,
    should_draft,
    should_enrich,
    signal_jsonl_fields,
)
from trace_drafting import (
    enrich_lead_from_candidate,
    format_drafting_context_package,
    format_trace_critique_context,
    legacy_outreach_angle,
    outreach_ask_guidance,
)
from trace_strategy_prompt import (
    build_trace_strategy_sender_block,
    build_trace_strategy_system_prompt,
    draft_strategy_jsonl_fields,
    normalize_trace_strategy_draft,
)
from trace_first_touch import (
    FIRST_TOUCH_WORD_MAX,
    with_first_touch_critique_checks,
    with_first_touch_rules,
)
from trace_style_prompts import (
    CRITIQUE_PLAIN_TEMPLATE,
    CRITIQUE_SHORT_TEMPLATE,
    DRAFTING_PLAIN_TEMPLATE,
    DRAFTING_SHORT_TEMPLATE,
)

# Strategy-mode counted-body hard limit (greeting + sign-off excluded)
TRACE_STRATEGY_WORD_WARN_HI = FIRST_TOUCH_WORD_MAX

load_dotenv()

# ─── Environment ────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID")
AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID")
AZURE_CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET")
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
APOLLO_API_KEY = os.getenv("APOLLO_API_KEY")
SENDER_FIRST_NAME = os.getenv("SENDER_FIRST_NAME", "Jamie")
SENDER_FULL_NAME = os.getenv("SENDER_FULL_NAME", "Hyunmyung Choi")
SENDER_COMPANY = os.getenv("SENDER_COMPANY", "Wiserbond Technologies Inc.")


def build_wiserbond_sign_off(profile: dict | None = None) -> str:
    """Two-line sign-off: full name, then company. No em dash."""
    if profile and str(profile.get("sign_off") or "").strip():
        raw = str(profile["sign_off"]).strip().lstrip("—–-").strip()
        if "\n" in raw:
            return raw
        if "," in raw:
            parts = [p.strip() for p in raw.split(",", 1)]
            if len(parts) == 2:
                return f"{parts[0]}\n{parts[1]}"
        return raw
    return f"{SENDER_FULL_NAME}\n{SENDER_COMPANY}"


def build_pb_sign_off(profile: dict | None = None) -> str:
    if profile and str(profile.get("sign_off") or "").strip():
        return str(profile["sign_off"]).strip()
    if profile and _is_helix_profile(profile):
        return f"{SENDER_FIRST_NAME}\nbuilding Helix"
    return build_wiserbond_sign_off(profile)


def _effective_email_mode(profile: dict | None) -> str:
    if not profile:
        return "legacy_email"
    mode = str(profile.get("email_mode") or "").strip()
    if mode:
        return mode
    if profile.get("profile_kind") == "legacy":
        return "legacy_email"
    return "problem_validation_email"


def _uses_template_draft_path(profile: dict | None) -> bool:
    return _effective_email_mode(profile) != "legacy_email"


def _is_helix_profile(profile: dict | None) -> bool:
    if not profile:
        return False
    return str(profile.get("product_name") or "").strip().lower() == "helix"


def _pb_sign_off_body_acceptable(body: str, sign_off: str | None = None) -> bool:
    """Last lines match the active profile sign-off when provided; else Helix legacy forms."""
    from trace_eval import body_matches_sign_off, sign_off_lines

    if sign_off and sign_off_lines(sign_off):
        return body_matches_sign_off(body, sign_off)
    lines = [ln.strip() for ln in body.replace("\r\n", "\n").split("\n") if ln.strip()]
    if len(lines) < 2:
        return False
    line2 = lines[-1].lower().replace("—", "-").strip()
    line1 = lines[-2].strip()
    second_ok = line2 in (
        "building helix",
        "helix by wiserbond",
        "helix (by wiserbond)",
    )
    first_ok = 1 <= len(line1) <= 40 and not line1.lower().startswith("http")
    return bool(second_ok and first_ok)


def _sign_off_lines(sign_off: str) -> list[str]:
    return [
        ln.strip()
        for ln in sign_off.replace("\r\n", "\n").strip().split("\n")
        if ln.strip()
    ]


def _body_has_sign_off(body: str, sign_off: str) -> bool:
    body_lines = [
        ln.strip()
        for ln in body.replace("\r\n", "\n").strip().split("\n")
        if ln.strip()
    ]
    sign_lines = _sign_off_lines(sign_off)
    if not sign_lines or len(body_lines) < len(sign_lines):
        return False
    return body_lines[-len(sign_lines):] == sign_lines


def _draft_sign_off(profile: dict | None) -> str:
    if profile and _uses_template_draft_path(profile):
        return build_pb_sign_off(profile)
    return build_wiserbond_sign_off(profile)


def ensure_draft_sign_off(email: dict, profile: dict | None) -> dict:
    """Append the profile sign-off when the model omitted it."""
    sign_off = _draft_sign_off(profile)
    body = str(email.get("body") or "").rstrip()
    if not body or _body_has_sign_off(body, sign_off):
        return email
    out = dict(email)
    out["body"] = body + "\n\n" + sign_off.strip()
    return out


def _is_claude_length_hard_fail(msg: str) -> bool:
    """True if this hard_fail looks like a body word-count / 75-word limit (not subject-line rule)."""
    t = str(msg).lower()
    if "subject" in t and ("7" in t or "seven" in t or "word" in t):
        return False
    if "subject line" in t:
        return False
    return (
        ("75" in t or "word" in t or "words" in t or "length" in t)
        and (
            "exceed" in t
            or "over" in t
            or "above" in t
            or "long" in t
            or "limit" in t
            or "maximum" in t
        )
    )


def _is_deterministic_length_hard_fail(msg: str) -> bool:
    return "deterministic count" in str(msg).lower()


def _pb_word_warn_hi(profile: dict | None = None) -> int:
    if profile and profile.get("email_mode") == "trace_strategy_email":
        return TRACE_STRATEGY_WORD_WARN_HI
    return PB_WORD_WARN_HI


def merge_pb_hard_fails_with_local_length(
    body: str,
    first_name: str,
    hard_fails: list[str],
    *,
    warn_hi: int | None = None,
    helix: bool = False,
    required_sign_off: str = "",
) -> tuple[list[str], dict]:
    """
    Sign-off noise strip, then align length hard fails with deterministic count.
    If local count > warn_hi, append exactly one pipeline length hard fail.
    """
    limit = PB_WORD_WARN_HI if warn_hi is None else warn_hi
    meta = pb_body_length_analysis(body, first_name or "", warn_hi=limit)
    n = meta["body_word_count"]
    out = strip_pb_signoff_noise_hard_fails(
        body,
        [str(x) for x in hard_fails if x],
        sign_off=required_sign_off or None,
    )
    out = strip_cross_product_signoff_hard_fails(
        out, helix=helix, required_sign_off=required_sign_off
    )
    out = [h for h in out if not (n <= limit and _is_claude_length_hard_fail(h))]
    out = [h for h in out if not _is_deterministic_length_hard_fail(h)]
    if n > limit:
        length_msg = (
            f"Body exceeds {limit} words (deterministic count: {n})"
        )
        if length_msg not in out:
            out.append(length_msg)
    return out, meta


def _length_json_fields(profile: dict, lead: dict, body: str | None) -> dict:
    """Deterministic length fields for JSONL (template draft modes only)."""
    if not _uses_template_draft_path(profile) or not body:
        return {}
    m = pb_body_length_analysis(
        body,
        lead.get("first_name") or "",
        warn_hi=_pb_word_warn_hi(profile),
    )
    return {
        "body_word_count": m["body_word_count"],
        "counted_text": m["counted_text"],
        "length_status": m["length_status"],
    }


def strip_pb_signoff_noise_hard_fails(
    body: str,
    hard_fails: list[str],
    *,
    sign_off: str | None = None,
) -> list[str]:
    """Do not burn revise attempts on brittle sign-off whitespace mismatches."""
    if not _pb_sign_off_body_acceptable(body, sign_off=sign_off):
        return [str(x) for x in hard_fails if x]
    out: list[str] = []
    for h in hard_fails:
        hl = str(h).lower()
        if "sign-off" in hl or "sign off" in hl or "signoff" in hl:
            continue
        if "sign" in hl and ("mismatch" in hl or "verbatim" in hl or "wrong sign" in hl):
            continue
        out.append(str(h))
    return out


def strip_cross_product_signoff_hard_fails(
    hard_fails: list[str],
    *,
    helix: bool = False,
    required_sign_off: str = "",
) -> list[str]:
    """Drop sign-off complaints that belong to a different product than this profile."""
    from trace_eval import strip_foreign_signoff_hard_fails

    if required_sign_off:
        return strip_foreign_signoff_hard_fails(
            hard_fails, required_sign_off=required_sign_off
        )
    # Legacy Helix flag path for older callers/tests.
    if helix:
        return [str(x) for x in hard_fails if x]
    return strip_foreign_signoff_hard_fails(
        hard_fails, required_sign_off="Jamie\nWiserbond Technologies Inc."
    )


# ─── Lead Sourcing ──────────────────────────────────────────────────────────


def apollo_get_leads(
    job_titles: list,
    industries: list,
    employee_range: tuple,
    num_leads: int = 3,
) -> list[dict]:
    """Search Apollo for leads (minimal fields). Extended fields left empty."""
    if not APOLLO_API_KEY:
        raise EnvironmentError("APOLLO_API_KEY is missing from .env")

    min_emp, max_emp = employee_range
    range_str = f"{min_emp},{max_emp}"

    response = requests.post(
        "https://api.apollo.io/api/v1/mixed_people/search",
        headers={"Content-Type": "application/json", "X-Api-Key": APOLLO_API_KEY},
        json={
            "person_titles": job_titles,
            "q_organization_keyword_tags": industries,
            "organization_num_employees_ranges": [range_str],
            "per_page": num_leads * 3,
            "page": 1,
        },
        timeout=30,
    )
    response.raise_for_status()

    people = response.json().get("people", [])

    leads = []
    for person in people:
        email = person.get("email")
        if not email:
            continue
        first = (person.get("first_name") or "").strip()
        last = (person.get("last_name") or "").strip()
        org = person.get("organization") or {}
        leads.append({
            "first_name": first,
            "last_name": last,
            "name": f"{first} {last}".strip(),
            "email": email,
            "company": org.get("name", "Unknown"),
            "title": person.get("title", ""),
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
            "linkedin_url": "",
            "website": "",
        })
        if len(leads) >= num_leads:
            break

    return leads


def load_leads_from_csv(filepath: str) -> list[dict]:
    """Load Apollo CSV rows as normalized whitelist lead dicts."""
    leads = []
    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (row.get("Qualify Contact") or "").strip().lower() == "disqualified":
                continue
            email = (row.get("Email") or "").strip()
            if not email:
                continue
            leads.append(normalize_csv_row(row))
    return leads


# ─── Product Profiles ───────────────────────────────────────────────────────


PRODUCT_PROFILES = {
    "akashic": {
        "profile_kind": "legacy",
        "product_name": "Akashic Record",
        "product_context": (
            "Akashic Record (by Wiserbond Technologies Inc.) helps investment "
            "teams reuse their own underwriting and decision history when a "
            "new deal comes up: how similar risks were handled, why the team "
            "got comfortable or passed, what assumptions were made, and what "
            "is different this time. Local first, on-prem deployable. Not a "
            "search tool or chatbot."
        ),
        "sign_off": "Hyunmyung Choi\nWiserbond Technologies Inc.",
        "sender_block": (
            "=== SENDER (verified for this campaign) ===\n"
            "- Name: Hyunmyung Choi\n"
            "- Company: Wiserbond Technologies Inc.\n"
            "- Product: Akashic Record\n"
            "- Current work: structuring past investment judgments, reasoning, and "
            "decision context into reusable institutional memory for PE and "
            "private-market teams\n"
            "- Desired outcome: validate whether the decision-memory / IC memo reuse "
            "problem is real and whether Trace's read of the signal matches what "
            "practitioners and researchers observe\n"
            "- Recipient value: a thoughtful question grounded in their public research "
            "or workflow, not a product pitch\n"
            "- Constraints: no fabricated customers or metrics; Trace research package "
            "only; do not treat researchers as IC workflow owners\n"
            "=== end sender ==="
        ),
        "discovery": {
            "product_name": "Akashic Record",
            "what_it_does": (
                "Helps investment teams use their own underwriting and decision "
                "history when evaluating a new deal, so they can see how similar "
                "risks were handled before, why the team got comfortable or passed, "
                "what assumptions were made, and what is different this time."
            ),
            "target_users_or_buyers": (
                "PE and private-market investment professionals involved in "
                "underwriting, diligence, and investment decisions, especially "
                "teams doing repeated or similar deals/add-ons."
            ),
            "problems_it_solves": [
                "Going back through old IC memos, underwriting models, or diligence files",
                "Trying to remember why the team passed or got comfortable with a similar deal",
                "Comparing a current deal with previous deals",
                "Rebuilding analysis or judgment the team has already done before",
                "Difficulty finding prior assumptions, risks, valuation logic, or deal rationale",
                "Important deal context living in people's heads",
                "Knowledge disappearing when team members leave",
                "Wanting to reuse historical underwriting instead of starting from scratch",
            ],
            "examples_of_problem_signals": [
                "When we look at a new add-on we still end up going through old IC decks",
                "Every new deal starts with someone digging through old folders",
                "Can't find why we passed or got comfortable last time",
                "The person who remembered that deal left and the context went with them",
                "We're doing the same analysis again from scratch",
                "I don't know where the old underwriting assumptions live",
                "We record why we passed and revisit that when a similar deal shows up",
                "We look back at prior IC memos and compare assumptions to what actually happened",
                "That experience became a playbook for later similar situations",
                "A lot of it is pattern recognition from prior investments",
                "We went back to how we handled a similar deal",
                "We've seen this movie before",
            ],
            "obvious_non_targets_or_adjacent_vendors": [
                "Founders or vendors building software to solve this problem",
                "Consultants or commentators describing the problem without owning the workflow",
                "Generic 'AI will transform PE' commentary",
                "People asking for 'institutional memory', 'decision memory', 'CJR', or 'on-prem AI' as a product category",
                "Generic CRM usage with no sign they preserve, retrieve, or reuse prior decision reasoning",
            ],
            "qualification_question": (
                "Does this person show that past investment judgments, assumptions, or "
                "reasoning are preserved, retrieved, compared, revised, or evaluated "
                "when a similar decision comes up again?"
            ),
            "search_guidance": (
                "Find the behavior around prior decision reasoning — not CRM as an object.\n"
                "Do NOT treat CRM usage, document storage, knowledge bases, or "
                "'institutional memory' slogans by themselves as evidence.\n"
                "CRM mentions are weak unless they include why a past judgment was made, "
                "how it is retrieved for a similar deal, or how outcomes were checked later.\n\n"
                "Search behavior-first. Prefer first-person operational language:\n"
                "- why we passed / why we got comfortable\n"
                "- revisit past deals / look back at old deals\n"
                "- previous investment memo / IC memo / underwriting assumptions\n"
                "- performance vs original assumptions / actual vs expectations\n"
                "- similar add-on / we've seen this movie before / pattern recognition\n"
                "- when the partner left, context disappeared\n"
                "- that became our playbook / lessons learned from the last one\n\n"
                "Avoid making these primary search queries (they surface HubSpot/Salesforce noise):\n"
                "CRM, deal history, knowledge base, institutional memory, Dynamo, Salesforce.\n"
                "Use them only if paired with reasoning behavior in the same query."
            ),
            "search_query_examples": [
                '"why we passed" investment deal private equity',
                '"revisit past deals" private equity',
                '"previous investment memo" similar opportunity',
                '"original underwriting assumptions" actual performance',
                '"investment committee" why we got comfortable',
                '"look back at old deals" growth equity',
                '"what we thought at underwriting" performance',
                '"past investment decisions" lessons learned',
                '"when the partner left" deal context disappeared',
                '"pattern recognition" prior investments',
            ],
            "signal_ontology": "decision_reasoning",
            "evidence_families": (
                "Akashic signal ladder — rank by reasoning depth, not storage:\n"
                "1. STORAGE (weak alone) — where notes/files/CRM records live; "
                "'we use Salesforce', 'deal history in CRM', 'knowledge base'. "
                "Do NOT promote unless paired with reasoning below.\n"
                "2. REASONING_CAPTURE — they record why a judgment was made: "
                "'why we passed', 'why IC got comfortable', 'log the rationale'.\n"
                "3. RETRIEVAL — they find prior judgments for a similar situation: "
                "'look back at old deals', 'open the prior memo', 'search old IC decks'.\n"
                "4. REUSE — prior reasoning changes the next decision: "
                "'playbook from last time', 'pattern recognition', 'we handled a similar deal by…'.\n"
                "5. OUTCOME_FEEDBACK — they check whether a prior call was right: "
                "'actual vs original assumptions', 'were we right to pass', postmortem.\n"
                "6. CONTINUITY — reasoning survives people leaving: "
                "'when the partner left the context went with them', onboarding off prior work.\n\n"
                "Minimum bar for Akashic: at least REASONING_CAPTURE plus RETRIEVAL or REUSE. "
                "STORAGE-only signals are generic, not highly_relevant."
            ),
            "search_channels": ["web", "x"],
            "channel_limit_ratios": {"web": 1.0, "x": 0.35},
            "prefer_web": True,
        },
        "angles": {
            "senior": (
                "ANGLE: This person is senior. Their likely pain: when "
                "experienced people leave, the reasoning behind past "
                "decisions disappears. Audits and reviews demand "
                "traceability of why decisions were made, but that "
                "context lives in scattered docs and departed employees' "
                "heads."
            ),
            "early": (
                "ANGLE: This person is early career. Their likely pain: "
                "when they have questions about why something is done a "
                "certain way, the only option is asking a manager who may "
                "be busy or unavailable. Existing tools teach steps to "
                "follow, not the reasoning behind decisions."
            ),
        },
    },
    "problem_validation": {
        "profile_kind": "problem_validation",
        "email_mode": "trace_strategy_email",
        "product_name": "Helix",
        "product_context": (
            "Helix pain + UVP (internal; do not dump the whole brief into the email).\n"
            "\n"
            "SURFACE PAIN: During cold calls, opening / objection / closing scripts live in "
            "one long Google Doc. Finding the right line means scrolling or searching mid-call, "
            "which costs a few seconds each time.\n"
            "\n"
            "ROOT PAIN (cognitive load): Those seconds are not just UX friction. Searching "
            "'which line is it' steals attention from live prospect signals (tone shifts, "
            "hesitation, real interest vs polite noise). Conversation gets stiff, objection "
            "response slows, and new SDRs on ramp get hit hardest because the script is not "
            "muscle memory yet.\n"
            "\n"
            "EXISTING ALTERNATIVES: Gong/Chorus (post-call analysis) and Balto (heavy real-time "
            "coaching) need recording, compliance, dialer integration. They do not remove the "
            "immediate 'which line do I need right now' friction for an individual rep.\n"
            "\n"
            "UVP: Reallocate thinking time during the call — from searching for the line, to "
            "listening to the prospect. One tap surfaces the line for the current moment "
            "(opening stage or objection type). Search cost near zero so attention stays on "
            "the buyer.\n"
            "\n"
            "HONEST SCOPE (do not overclaim): Helix does not magically raise call skill "
            "(tone, timing, ad-lib). It reduces execution friction in early ramp — especially "
            "for new SDRs whose brain is still stuck in the script. Team coaching analytics "
            "from button usage may come later; that is secondary, not the first-touch UVP.\n"
            "\n"
            "EMAIL GOAL: design a credible value exchange first (competency, clear ask, "
            "transparent self-interest, evidence of effort). Prefer founder-to-founder or "
            "peer validation. For sales leaders: ramp / new-rep script friction and "
            "consistency under live call pressure. Never invent customers or metrics. If you "
            "mention the product, always call it Helix (never only 'a lightweight tool')."
        ),
        # sign_off filled at draft time via build_pb_sign_off()
        "sign_off": "",
        "sender_block": (
            "=== SENDER (verified for this campaign) ===\n"
            "- Name: use sign-off first line\n"
            "- Company: Wiserbond\n"
            "- Product: Helix\n"
            "- Current work: building Helix while doing founder-led outbound\n"
            "- Relevant system built: outbound workflow using Apollo list data, Claude "
            "draft + critique, and Outlook send\n"
            "- UVP: one tap surfaces the right script line mid-call so reps stop "
            "scrolling a long Google Doc and can listen to the prospect\n"
            "- Honest scope: reduces early-ramp execution friction; does not claim to "
            "magically improve tone, timing, or ad-lib skill\n"
            "- Desired outcome: a low-friction reply validating whether mid-call script "
            "search / cognitive load is a real pain\n"
            "- Constraints: no fabricated customers or metrics; Trace research package "
            "only; do not overclaim vs Gong/Chorus/Balto\n"
            "=== end sender ==="
        ),
        "discovery": {
            "product_name": "Helix",
            "what_it_does": (
                "Lightweight in-call helper: one tap surfaces the right script line "
                "(opening / objection / close) so reps stop scrolling a long Google Doc "
                "mid-call and can listen to the prospect."
            ),
            "target_users_or_buyers": (
                "People who actually run or live with live outbound calls: founders "
                "doing their own cold calling, SDRs / AEs on ramp, sales leaders "
                "responsible for ramp and in-call execution. Founder is not an automatic reject."
            ),
            "problems_it_solves": [
                "Finding the right script line mid-call by scrolling or searching a long doc",
                "Cognitive load: searching steals attention from the live prospect",
                "New reps whose script is not muscle memory yet",
                "No lightweight way to practice or retrieve objection lines before/during dials",
            ],
            "examples_of_problem_signals": [
                "I still do most of our cold calling myself and have no good way to practice objections",
                "Reps lose the thread hunting for the right line mid-call",
                "Script lives in a giant Google Doc and we scroll during the call",
                "Managers don't have time to run objection practice with new reps",
                "We keep going back to the opener that worked last week and hunting for it in the doc",
                "New reps don't have a repeatable way to reuse what worked on prior calls",
            ],
            "obvious_non_targets_or_adjacent_vendors": [
                "Founders selling AI sales-roleplay, conversation intelligence, or call-coaching platforms",
                "Vendors pitching Gong / Chorus / Balto-class products as the solution",
                "Generic 'sales teams need scalable AI roleplay' thought leadership",
            ],
        },
    },
    "myzel": {
        "profile_kind": "problem_validation",
        "email_mode": "trace_strategy_email",
        "product_name": "Myzel Organics",
        "product_context": (
            "Myzel Organics (internal; do not dump the whole brief into the email).\n"
            "\n"
            "WHAT WE SUPPLY: Canadian-grown organic functional mushroom powders "
            "(Lion's Mane, Reishi, Cordyceps, and others) that brands add to food, "
            "beverage, or supplement formulations.\n"
            "\n"
            "WHO TO WRITE: R&D / product development / innovation / formulation / "
            "ingredient sourcing at North American SMB-to-midsize protein, functional "
            "beverage, coffee/RTD, supplement, or healthy snack/bar brands. Prefer a "
            "handful of companies Markus would actually sell. Skip Nestlé-scale CPG.\n"
            "\n"
            "TRIGGER TO INFER: they are exploring a new functional product or "
            "differentiating a current line (protein + functional positioning + new "
            "formulation). Do not wait for them to ask for mushrooms. Do not write as "
            "if they already chose a mushroom ingredient.\n"
            "\n"
            "EMAIL SHAPE: one observed expansion/concept (from FACTS only) → we grow "
            "organic functional mushrooms in Ontario and supply powders for "
            "formulations → one low-friction ask for samples/specs. Sound like a "
            "person, not like software found them.\n"
            "\n"
            "Never invent customers, launches, or metrics. If you name the company, "
            "call it Myzel Organics."
        ),
        "sign_off": "Markus\nMyzel Organics",
        "sender_block": (
            "=== SENDER (verified for this campaign) ===\n"
            "- Name: Markus\n"
            "- Company: Myzel Organics\n"
            "- Current work: Ontario-grown organic functional mushroom powders for "
            "food, beverage, and supplement formulations\n"
            "- Ingredients: Lion's Mane, Reishi, Cordyceps, and other mushroom powders\n"
            "- Desired outcome: a samples/specs conversation with R&D or product development\n"
            "- Recipient value: an ingredient option for a functional product they are "
            "already exploring, not a pitch that they need mushrooms\n"
            "- Constraints: no fabricated customers or metrics; Apollo FACTS only; "
            "do not claim they already chose mushrooms; do not write like AI found them\n"
            "=== end sender ==="
        ),
        "discovery": {
            "product_name": "Myzel Organics",
            "what_it_does": (
                "Supplies Canadian-grown organic functional mushroom powders that brands "
                "can add to food, beverage, or supplement products to create differentiated "
                "functional or wellness SKUs."
            ),
            "target_users_or_buyers": (
                "R&D, product development, innovation, formulation, and ingredient sourcing "
                "people at North American SMB-to-midsize protein/nutrition, functional "
                "beverage, coffee/RTD, supplement, and healthy snack/bar brands. "
                "A few real-fit companies beat a long list of Nestlé-scale names."
            ),
            "problems_it_solves": [
                "Want to launch something new in functional nutrition but still exploring concepts",
                "Looking for new ingredients for a protein powder or RTD",
                "Trying to differentiate a current product line",
                "Exploring adaptogens, cognitive wellness, energy, immunity, or gut-health claims",
                "Testing new product concepts with creators/influencers",
            ],
            "examples_of_problem_signals": [
                "We want to launch something new in functional nutrition, but we're still exploring concepts",
                "Looking for new ingredients for a protein powder / RTD",
                "Trying to differentiate our current product line",
                "Exploring adaptogens, cognitive wellness, energy, immunity, gut health, or other functional claims",
                "Testing new product concepts with creators/influencers",
            ],
            "obvious_non_targets_or_adjacent_vendors": [
                "People already shopping for Lion's Mane / Reishi / mushroom powders as the ingredient",
                "Mushroom, adaptogen, or functional-ingredient vendors selling into the same buyers",
                "Nestlé / Pepsi / Coke-scale CPG where Myzel is not a realistic first customer",
                "Generic wellness thought leadership with no live product-development work",
            ],
            "search_guidance": (
                "Do not hunt for people asking for mushrooms. Hunt for people who have not "
                "locked an ingredient yet and are building a new functional food, beverage, "
                "protein, or supplement product. Protein + functional positioning + "
                "new formulation is enough to infer Myzel could fit. Prefer named R&D / "
                "product-development people at North American SMB-to-midsize brands. "
                "Pet nutrition is a separate profile; do not spend this run on pet."
            ),
        },
    },
    "myzel_pet": {
        "profile_kind": "problem_validation",
        "email_mode": "trace_strategy_email",
        "product_name": "Myzel Organics",
        "product_context": (
            "Myzel Organics pet run (internal; do not dump the whole brief into the email).\n"
            "\n"
            "WHAT WE SUPPLY: Canadian-grown organic functional mushroom powders "
            "for pet food, treats, toppers, chews, and pet supplements.\n"
            "\n"
            "WHO TO WRITE: (1) R&D / product development / innovation / formulation "
            "at North American SMB-to-midsize pet food or pet supplement brands. "
            "(2) Marketing or creator-commerce shops that run short influencer "
            "group-buys / drops to test demand for a pet SKU, then sell a batch. "
            "(3) Formulation houses building those SKUs. Skip Purina / Mars-scale.\n"
            "\n"
            "TRIGGER TO INFER: a new calming chew, senior-dog cognition line, "
            "functional topper, gut/immunity/healthy-aging treat is in development "
            "and the functional ingredient is not locked. Do not wait for them to "
            "ask for mushrooms.\n"
            "\n"
            "EMAIL SHAPE: one observed pet-product concept (from FACTS only) → "
            "Ontario-grown organic mushroom powders for pet formulations → "
            "samples/specs. Sound like a person, not like software found them.\n"
            "\n"
            "Never invent customers, launches, or metrics. Call the company "
            "Myzel Organics."
        ),
        "sign_off": "Markus\nMyzel Organics",
        "sender_block": (
            "=== SENDER (verified for this campaign) ===\n"
            "- Name: Markus\n"
            "- Company: Myzel Organics\n"
            "- Current work: Ontario-grown organic functional mushroom powders; "
            "pet food is currently the largest share of sales\n"
            "- Ingredients: Lion's Mane, Reishi, Cordyceps, and other mushroom powders "
            "for pet chews, toppers, treats, and supplements\n"
            "- Desired outcome: a samples/specs conversation with pet R&D, a "
            "formulator, or a creator-commerce shop testing a pet SKU\n"
            "- Recipient value: a functional ingredient option for a pet product "
            "they are already exploring or demand-testing\n"
            "- Constraints: no fabricated customers or metrics; Apollo FACTS only; "
            "do not claim they already chose mushrooms; do not write like AI found them\n"
            "=== end sender ==="
        ),
        "discovery": {
            "product_name": "Myzel Organics",
            "what_it_does": (
                "Supplies Canadian-grown organic functional mushroom powders that pet "
                "food and pet supplement brands add to chews, toppers, treats, powders, "
                "and other functional pet SKUs."
            ),
            "target_users_or_buyers": (
                "R&D, product development, innovation, and formulation people at North "
                "American SMB-to-midsize pet food / pet supplement brands. Also marketing "
                "or influencer-commerce shops that gather demand with short creator "
                "group-buys or live drops, then sell a limited pet SKU. Formulation "
                "houses building those products count. A few real-fit companies beat "
                "Purina-scale names."
            ),
            "problems_it_solves": [
                "Developing a new dog chew, topper, powder, or treat and still choosing the functional ingredient",
                "Adding cognitive support to a senior-dog line",
                "Launching a calming, gut-health, immunity, or healthy-aging pet SKU without a locked formula",
                "Wanting a functional topper but the formula is not finalized",
                "Marketing or creator shops testing a pet product concept with a short influencer group-buy or drop before committing to a full formula",
            ],
            "examples_of_problem_signals": [
                "We're developing a new calming chew and testing different functional ingredients",
                "Looking at ways to add cognitive support to our senior dog line",
                "We want to launch a functional topper but haven't finalized the formula",
                "Testing a new pet treat concept with creators / running a group buy to see if demand is real",
                "Formulating a pet supplement for gut health / immunity / healthy aging and still comparing actives",
            ],
            "obvious_non_targets_or_adjacent_vendors": [
                "People already shopping for Lion's Mane / Reishi pet powders as the locked ingredient",
                "Mushroom or pet-ingredient vendors selling into the same buyers",
                "Purina / Mars / Nestlé Purina-scale pet CPG",
                "Generic pet-wellness commentary with no live product or demand-test work",
                "Human food/supplement R&D with no pet SKU (that is the other Myzel profile)",
            ],
            "search_guidance": (
                "This run is pet only. Do not hunt for people asking for mushrooms. "
                "Hunt for people at a real pet nutrition company, or at a marketing/"
                "creator-commerce shop, who are making or demand-testing a new pet "
                "product and have not locked the functional ingredient. Need all three: "
                "pet company or pet-SKU tester + new product in motion + ingredient "
                "unset. Group-buy / influencer drop / 'we tested demand with creators' "
                "is in-scope if the SKU is pet. Prefer named North American SMB-to-midsize "
                "people. Skip this run's human food and RTD work."
            ),
        },
    },
    "oneaway": {
        "profile_kind": "problem_validation",
        "email_mode": "trace_strategy_email",
        "product_name": "OneAway",
        "product_context": (
            "OneAway (internal; do not dump the whole brief into the email).\n"
            "\n"
            "WHAT IT IS: A B2B outbound agency. Cold email, LinkedIn DMs, "
            "appointment setting, and GTM engineering / workflow automation "
            "for companies that need pipeline and do not yet have a mature "
            "in-house outbound engine.\n"
            "\n"
            "WHO TO WRITE: Founder, CEO, Head/VP of Sales, Head of Growth, "
            "CRO, RevOps or GTM lead at a B2B SaaS / tech company. Prefer a "
            "company that is hiring SDRs, just raised, entering a new market, "
            "still founder-led on outbound, or talking about pipeline / Clay / "
            "Apollo / HubSpot as an unfinished GTM stack. Skip other agencies, "
            "GTM consultants, sales coaches, and companies that already run a "
            "large SDR org with a mature outbound engine.\n"
            "\n"
            "TRIGGER TO INFER: they need outsourced outbound or GTM engineering "
            "now. They do not need to have asked for an agency.\n"
            "\n"
            "EMAIL SHAPE: one observed company trigger (from FACTS only) → "
            "OneAway runs outbound + GTM workflow for teams in that spot → "
            "one low-friction ask. Sound like a person, not like software "
            "found them.\n"
            "\n"
            "Never invent customers, pipeline numbers, or retainers. If you "
            "name the company, call it OneAway."
        ),
        "sign_off": "Hyunmyung Choi\nWiserbond Technologies Inc.",
        "sender_block": (
            "=== SENDER (verified for this campaign) ===\n"
            "- Name: Hyunmyung Choi\n"
            "- Company: Wiserbond Technologies Inc.\n"
            "- Current work: B2B outbound agency — cold email, LinkedIn DM, "
            "appointment setting, GTM engineering / workflow automation\n"
            "- Desired outcome: a conversation about whether outsourced outbound "
            "or GTM engineering would help their pipeline this quarter\n"
            "- Recipient value: pipeline without standing up a full SDR org yet\n"
            "- Constraints: no fabricated customers or metrics; public FACTS only; "
            "do not claim they asked for an agency; do not write like AI found them\n"
            "=== end sender ==="
        ),
        "discovery": {
            "product_name": "OneAway",
            "what_it_does": (
                "B2B outbound agency: cold email, LinkedIn DMs, appointment setting, "
                "and GTM engineering / workflow automation for companies that need "
                "pipeline and do not have a mature in-house outbound engine."
            ),
            "target_users_or_buyers": (
                "Founder, CEO, Head/VP of Sales, Head of Growth, CRO, RevOps, or GTM "
                "lead at a B2B SaaS / tech company with a small or new outbound motion. "
                "Buyers of outsourced outbound or GTM engineering, not people selling it."
            ),
            "problems_it_solves": [
                "Need outbound pipeline but the SDR team is small or not built yet",
                "Founder-led sales is becoming the bottleneck",
                "Trying to scale outbound into a new market without a repeatable engine",
                "Pipeline is weak or inconsistent and GTM tooling is unfinished",
                "Hiring SDRs / AEs and still designing the outbound workflow",
            ],
            "examples_of_problem_signals": [
                "We're hiring our first SDR / first AE",
                "Just raised and need to turn that into pipeline",
                "Launching in a new market and outbound is still founder-led",
                "Pipeline has been inconsistent this quarter",
                "Trying to get Clay / Apollo / HubSpot to actually run outbound",
                "We need to get off founder-led sales",
            ],
            "obvious_non_targets_or_adjacent_vendors": [
                "Other lead-gen or cold-email agencies",
                "GTM consultants and vendors selling outbound as a product",
                "Individual sales coaches",
                "Companies with a large SDR org and a mature outbound engine",
                "People asking to buy or sell 'a cold email agency' as a category",
            ],
            "qualification_question": (
                "Does this company have a reason to buy outsourced outbound or "
                "GTM engineering right now?"
            ),
            "search_guidance": (
                "Do not hunt for people saying they need a cold email agency. "
                "Hunt for B2B SaaS/tech companies whose public trail shows a "
                "reason to buy outbound help now: SDR/AE hiring, recent funding, "
                "new-market launch, founder still doing outbound, weak pipeline, "
                "or GTM stack (Clay, Apollo, HubSpot) still being built. "
                "Prefer named buyers (founder, CEO, sales/growth/CRO/RevOps). "
                "Weight company pages, hiring posts, funding news, and LinkedIn "
                "over Twitter chatter. Skip other agencies and mature SDR machines."
            ),
            "evidence_families": (
                "A. HIRING — SDR, BDR, AE, or outbound roles opening; first sales hire.\n"
                "B. FUNDING / EXPANSION — recent raise, new market, new ICP, new geo.\n"
                "C. PIPELINE PAIN — inconsistent pipeline, founder still selling, "
                "trying to leave founder-led sales.\n"
                "D. GTM STACK — Clay, Apollo, HubSpot, or outbound workflow still "
                "being assembled; GTM engineering mentions.\n"
                "E. COMPANY_TRIGGER — any of the above on a company page, job post, "
                "funding article, or LinkedIn. They do not need to ask for an agency."
            ),
            "search_channels": ["web", "x"],
            "channel_limit_ratios": {"web": 1.0, "x": 0.4},
            "prefer_web": True,
        },
    },
}


LIST_TO_PROFILE = {
    "akashic": "akashic",
    "problem_validation": "problem_validation",
    "helix": "problem_validation",
    "myzel": "myzel",
    "myzel_pet": "myzel_pet",
    "oneaway": "oneaway",
}

LEAD_LISTS = {
    "akashic": "akashic_record_list.csv",
    "problem_validation": "helix_list.csv",
    "helix": "helix_list.csv",
    "myzel": "myzel_list.csv",
    "myzel_pet": "myzel_pet_list.csv",
    "oneaway": "oneaway_list.csv",
}


# ─── Email Draft (legacy Akashic) ──────────────────────────────────────────

_DRAFTING_SYSTEM_TEMPLATE = """\
You write first-touch cold emails for B2B discovery outreach.

# PRODUCT CONTEXT (internal only, NEVER output any of this)
{product_context}

# STYLE
1. Write as if speaking out loud to a peer, pragmatic and direct.
2. Never start a sentence with a verb (e.g. "Noticed…", "Saw…").
3. Never use em dashes (—) anywhere in the subject or body, including the sign-off.
4. Subject lines may naturally use lowercase. Full grammatical sentences should normally
   use standard capitalization. Short fragments may occasionally begin lowercase when
   natural. Do not force capitalization variation mechanically.
5. Never use hollow words: "impressed", "inspiring", "admire", "fascinating",
   "excited", "thrilled", "remarkable", "incredible", "love", "noticed".
6. Keep the body very short.
   Usually 2–4 short sentences total. End with one focused question.
   Aim for about 30–50 words. Never exceed 75 words (excluding the sign-off).
   Do not add or remove a sentence merely to hit a fixed sentence count.
7. Subject line: under 6 words, lowercase friendly when natural.
8. Never label or reference the prospect's seniority level, career stage,
   or experience. Words like "early career", "junior", "new to", "as a
   young professional" are banned. Write as equal to equal.

# PERSONALIZATION
1. The email is about the PROSPECT and the Trace research signal, not about us.
2. The Trace research package in the user message is the sole basis for
   personalization. Use verified facts, research evidence, outreach role, and
   recommendation together. Do not invent posts, news, achievements, or specifics
   beyond that package.
3. Do not turn observational evidence into a claim that the recipient personally
   experiences the problem unless Trace classification supports it.
4. If the cited research already states a finding directly, do not ask the recipient
   to simply confirm that same finding. Ask one level beyond the source: clarify which
   problem mattered more, what caused it, what changed, or how the observed behavior
   worked in practice.
5. Do not turn Trace's interpretation into a claim about the source.
   Avoid phrases such as "the core finding was…", "the main problem was…",
   "this proves…" unless the source explicitly supports that characterization.
6. Do not just reference their title or company name as personalization.

# ASK PRIORITY
Choose the closing question in this order:
1. recommended_ask
2. outreach_role
3. Trace evidence
4. question style preference below

Examples of ask fit:
- validate_problem_interpretation → open, discriminating question
- confirm_workflow_pain → yes/no or short open question
- find_workflow_owner_or_intro → direct role/owner question
- low_friction_next_step → concrete CTA

# QUESTION
{question_rules}

# WHAT NEVER APPEARS IN THE EMAIL
1. Do NOT name {product_name} in the body. Do NOT describe what
   {product_name} does or how it works. Zero product mentions in the body.
2. Do NOT request a meeting, call, or demo.
3. Do NOT pitch, sell, or explain what any company does.
4. Do NOT repeat the prospect's name after the greeting.
5. End the body with exactly these two sign-off lines (no em dash):
{sign_off}

Before outputting, ask yourself: would a busy professional reply to this?
If not, rewrite it shorter and sharper.

OUTPUT FORMAT — return raw JSON only, no markdown fences:
{{"subject": "...", "body": "..."}}
"""


def _profile_research_constraints_block(
    lead: dict,
    profile: dict,
    *,
    style: str,
) -> str:
    """Product-agnostic constraints from Profile + Trace research (not style forks)."""
    product = profile.get("product_name") or "the product"
    role = lead.get("outreach_role") or "Practitioner"
    ask = lead.get("recommended_ask") or ""
    lines = [
        "=== PROFILE + RESEARCH CONSTRAINTS ===",
        "",
        f"Product (mention at most once if needed): {product}",
        f"Outreach role: {role}",
        f"Recommended ask: {ask}",
        "",
        outreach_ask_guidance(role),
        "",
        "RULES:",
        "- Use only Profile context and the Trace research package.",
        "- Do not invent facts, pain, posts, hiring, funding, or metrics.",
        f"- If you name a product, name only {product}.",
        "- Match the closing question to outreach_role and recommended_ask.",
        "- Do not assume cold-call / script-scroll pain unless Profile or research supports it.",
        "",
    ]
    if style == "plain":
        lines.extend([
            "PLAIN VOICE:",
            "- Mild uncertainty when evidence is incomplete.",
            "- Do not force a fixed opener phrase across leads.",
            "- Do not use generic artifact-send closings unless that is literally the ask.",
            "",
        ])
    else:
        lines.extend([
            "SHORT VOICE:",
            "- One observation, one question; optional one product line only if needed.",
            "",
        ])
    lines.append("=== end profile + research constraints ===")
    return "\n".join(lines)


# Problem-validation / founder-led discovery (Helix cold-call campaigns)

_PB_REVISION_EXTRAS = """\

# REVISION RULES (only when rewriting after critique)
- Fix ONLY the critique issues and hard-fail causes. Do **not** rewrite the whole
  email into a generic template.
- A revision should normally stay the same length or become shorter.
  Do not add explanation unless the critique specifically identifies missing meaning.
- Keep the research angle, outreach_role, recommended_ask, and voice of the first draft
  unless a cited issue requires a change.
- If the issue is evidence safety, remove the unsupported line; do not replace it with
  over-generic filler.
"""


QUESTION_RULES = {
    "yesno": (
        "1. If a yes/no question best fits the recommended ask, make it easy to answer "
        "in one sentence.\n"
        "2. Keep it under 15 words.\n"
        "3. NEVER use 'How do you currently...' as a question opener.\n"
        "4. Do NOT ask generic questions like 'what\\'s your biggest challenge'."
    ),
    "open": (
        "1. Ask one specific question whose answer would materially improve the "
        "sender's understanding.\n"
        "2. The question must be under 15 words.\n"
        "3. Prefer a discriminating question over a yes/no when recommended_ask "
        "calls for interpretation or diagnosis.\n"
        "4. NEVER use 'How do you currently...' as a question opener.\n"
        "5. Do NOT ask generic questions like 'what\\'s your biggest challenge'."
    ),
}


# Sales-pitch mode placeholder (not used in initial campaign; kept for separation)
_DRAFTING_SALES_PITCH_TEMPLATE = """\
You write a stronger product-led cold email. (Campaign default should NOT use this.)
{product_context}
Sign off:
{sign_off}
Return JSON: {{"subject":"...","body":"..."}}
"""


def _build_system_prompt(question_style: str, profile: dict) -> str:
    rules = QUESTION_RULES.get(question_style, QUESTION_RULES["yesno"])
    sign_off = build_wiserbond_sign_off(profile)
    return with_first_touch_rules(
        _DRAFTING_SYSTEM_TEMPLATE.format(
            question_rules=rules,
            product_context=profile["product_context"],
            product_name=profile["product_name"],
            sign_off=sign_off,
        )
    )


def _build_pb_system_prompt(profile: dict) -> str:
    mode = profile.get("email_mode", "problem_validation_email")
    sign_off = build_pb_sign_off(profile)
    product_name = profile.get("product_name") or ""
    product_context = profile.get("product_context") or ""
    if mode == "sales_pitch_email":
        return with_first_touch_rules(
            _DRAFTING_SALES_PITCH_TEMPLATE.format(
                product_context=product_context,
                sign_off=sign_off,
            )
        )
    if mode == "trace_strategy_email":
        return build_trace_strategy_system_prompt(
            product_context=product_context,
            sign_off=sign_off,
        )
    if mode == "anti_ai_email":
        return with_first_touch_rules(
            DRAFTING_PLAIN_TEMPLATE.format(
                product_context=product_context,
                product_name=product_name,
                sign_off=sign_off,
            )
        )
    return with_first_touch_rules(
        DRAFTING_SHORT_TEMPLATE.format(
            product_context=product_context,
            product_name=product_name,
            sign_off=sign_off,
        )
    )


def _is_senior_title(title: str) -> bool:
    senior_keywords = [
        "head", "director", "vp", "vice president", "chief",
        "cio", "cro", "cco", "cfo", "partner", "managing",
        "principal", "senior vice", "president",
    ]
    lower = title.lower()
    return any(kw in lower for kw in senior_keywords)


def _strip_json_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return raw


def claude_draft_email(
    profile: dict,
    lead: dict,
    question_style: str = "yesno",
    revision: dict | None = None,
    *,
    derived: dict | None = None,
    test_batch: str = "",
) -> dict:
    if not ANTHROPIC_API_KEY:
        raise EnvironmentError("ANTHROPIC_API_KEY is missing from .env")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    mode = _effective_email_mode(profile)
    uses_template = _uses_template_draft_path(profile)

    if uses_template:
        system_prompt = _build_pb_system_prompt(profile)
        anti_ai_mode = mode == "anti_ai_email"
        strategy_mode = mode == "trace_strategy_email"

        draft_ctx = format_drafting_context_package(lead)
        angle_note = (
            "OUTREACH (Trace classification — follow this):\n"
            f"- outreach_role: {lead.get('outreach_role') or 'Practitioner'}\n"
            f"- recommended_ask: {lead.get('recommended_ask') or ''}\n"
            "- Ground the observation in the Trace research package only.\n"
            "- Match the closing question to outreach_role and recommended_ask.\n"
            "- Ask intent comes from research; style comes from the selected template.\n"
        )
        if derived:
            angle_note += (
                "\nOPTIONAL PROFILE CAMPAIGN HINTS (tone only; do not assert as fact):\n"
                f"- segment: {derived.get('segment')}\n"
                f"- subject_line_hint: {derived.get('subject_line_hint')}\n"
                f"- email_angle: {derived.get('email_angle')}\n"
            )
        if strategy_mode:
            sig = build_pb_sign_off(profile)
            copy_blk = (
                build_trace_strategy_sender_block(
                    profile.get("sender_block"), profile=profile
                )
                + "\n\n"
                + "=== CAMPAIGN NOTES ===\n"
                + f"- email_mode: {mode}\n"
                + f"- test_batch: {test_batch or '(none)'}\n"
                + f"- counted-body hard limit: {TRACE_STRATEGY_WORD_WARN_HI} words "
                + "(greeting + two-line sign-off excluded; soft aim ~40–65, shorter OK)\n"
                + "- required sign-off (exact two lines at end of email.body):\n"
                + sig.replace("\n", "\n  ")
                + "\n"
                + "=== end campaign notes ===\n"
            )
        else:
            copy_blk = _profile_research_constraints_block(
                lead,
                profile,
                style="plain" if anti_ai_mode else "short",
            )
        base_message = f"{draft_ctx}\n\n{angle_note}\n{copy_blk}\n"
        if revision:
            previous = revision.get("previous") or {}
            critique = revision.get("critique") or {}
            hard_fails = critique.get("hard_fails") or []
            issues = critique.get("issues") or []
            feedback_lines: list[str] = []
            if hard_fails:
                feedback_lines.append("Hard rule violations to fix:")
                for h in hard_fails:
                    feedback_lines.append(f"  - {h}")
            if issues:
                feedback_lines.append("Other reviewer feedback:")
                for i in issues:
                    feedback_lines.append(f"  - {i}")
            feedback = "\n".join(feedback_lines) or "Improve overall quality."
            if strategy_mode:
                user_message = (
                    f"{base_message}\nPREVIOUS DRAFT:\nSubject: {previous.get('subject', '')}\n"
                    f"Body:\n{previous.get('body', '')}\n\nREVIEWER FEEDBACK:\n{feedback}\n"
                    f"Rewrite using the full Trace strategy JSON schema. Fix only the "
                    f"cited issues. Keep factual safety. Output raw JSON only."
                )
            else:
                user_message = (
                    f"{base_message}\nPREVIOUS DRAFT:\nSubject: {previous.get('subject', '')}\n"
                    f"Body:\n{previous.get('body', '')}\n\nREVIEWER FEEDBACK:\n{feedback}\n"
                    f"{_PB_REVISION_EXTRAS}\n"
                    f"Rewrite from scratch (minimal change: fix issues only). Output raw JSON only."
                )
        else:
            user_message = base_message
    else:
        title = lead.get("title", "")
        draft_ctx = format_drafting_context_package(lead)
        outreach = legacy_outreach_angle(
            lead, profile, is_senior=_is_senior_title(title)
        )
        base_message = f"{draft_ctx}\n\n{outreach}\n"
        if revision:
            previous = revision.get("previous") or {}
            critique = revision.get("critique") or {}
            hard_fails = critique.get("hard_fails") or []
            issues = critique.get("issues") or []
            feedback_lines = []
            if hard_fails:
                feedback_lines.append("Hard rule violations to fix:")
                for h in hard_fails:
                    feedback_lines.append(f"  - {h}")
            if issues:
                feedback_lines.append("Other reviewer feedback:")
                for i in issues:
                    feedback_lines.append(f"  - {i}")
            feedback = "\n".join(feedback_lines) or "Improve overall quality."
            user_message = (
                f"{base_message}\n\n"
                f"PREVIOUS DRAFT:\n"
                f"Subject: {previous.get('subject', '')}\n"
                f"Body:\n{previous.get('body', '')}\n\n"
                f"REVIEWER FEEDBACK:\n{feedback}\n\n"
                f"Rewrite the email from scratch. Fix every hard rule violation "
                f"and address the other feedback. Output raw JSON only."
            )
        else:
            user_message = base_message
        system_prompt = _build_system_prompt(question_style, profile)

    max_tokens = 4096 if (
        uses_template and mode == "trace_strategy_email"
    ) else 768

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    raw = _strip_json_fences(response.content[0].text)
    email = json.loads(raw)

    if uses_template and mode == "trace_strategy_email":
        email = normalize_trace_strategy_draft(email)
    elif "subject" not in email or "body" not in email:
        raise ValueError("Claude response missing 'subject' or 'body' key")

    return ensure_draft_sign_off(email, profile)


# ─── Email Critique (legacy) ───────────────────────────────────────────────

_CRITIQUE_SYSTEM_TEMPLATE = """\
You are a brutal cold-email reviewer for first-touch B2B discovery outreach.
Apply the rubric strictly. Default to lower scores. If unsure, deduct.

This email is being sent to promote {product_name}. The body must NOT
mention {product_name} or describe what it does. The sign-off is the ONLY
place the company name may appear. Never use em dashes anywhere in the
subject or body, including the sign-off.

# HARD FAILS (each one independently is a hard fail)
- Banned hollow words appear in subject or body: "impressed", "inspiring",
  "admire", "fascinating", "excited", "thrilled", "remarkable",
  "incredible", "love", "noticed".
- Em dashes (—) anywhere in the subject or body, including the sign-off.
- Any sentence in the body starts with a verb (e.g. "Noticed", "Saw",
  "Hope", "Wanted").
- Body mentions {product_name} or describes what it does or how it works.
- Body requests a meeting, call, demo, time on calendar, or "15 minutes".
- Body has no focused closing question.
- Body word count exceeds 75 words (counted excluding greeting and sign-off).
  Do NOT fail merely because the email is short when complete.
- The closing question opens with "How do you currently".
- The sign-off line is missing or altered from "{sign_off}".
- Subject line exceeds 6 words.
- Prospect's first name appears more than once in subject + body
  combined (the greeting is the only place it should appear).
- The email invents specific facts about the prospect or their company
  that could not be derived from the Trace research package in the user
  message (fake quotes, fake news, fake achievements, fake metrics).
- Email asks the prospect about personal workflow pain when outreach_role
  is Expert / Researcher or recommended_ask is validate_problem_interpretation.
- Email contradicts why_surfaced, Trace recommendation, or outreach_role.
- Email asks the recipient only to reconfirm a finding their cited research
  already states, instead of asking one level beyond the source.
- Email labels Trace interpretation as "the core finding" / "the main problem"
  / "this proves" without the source supporting that characterization.

# SOFT RUBRIC (each 0-25, total 0-100)
- personalization: how naturally the email speaks to this specific
  prospect's likely reality vs. swap-the-name boilerplate. 25 = clearly
  written for them. 0 = generic.
- question_quality: is the closing question sharp, specific, and aligned
  to recommended_ask / outreach_role? Generic questions like "what's your
  biggest challenge" score near 0.
- voice: peer-to-peer, pragmatic, direct, not salesy or hollow. Marketer
  energy or hype scores low.
- hook: would a busy professional pause on the first sentence rather
  than archive? 25 = sharp open. 0 = generic filler.

# OUTPUT FORMAT — return raw JSON only, no markdown fences.
{{
  "hard_fails": ["short reason", "short reason"],
  "soft_scores": {{
    "personalization": 0,
    "question_quality": 0,
    "voice": 0,
    "hook": 0
  }},
  "total": 0,
  "issues": ["actionable feedback the writer can use to revise"]
}}

"total" must equal the sum of soft_scores. "issues" should always have
1-4 short, specific items even when the email passes, in case revision
is later needed.
"""


_CRITIQUE_TRACE_STRATEGY_TEMPLATE = """\
You review a Trace *strategy* cold email (value-exchange first, not generic AI outbound).

Prospect facts available to the sender are ONLY what appears in the FACTS block.
Derived segment context is for tone only — not proven internal problems.

# PRODUCT (this campaign)
Product name: {product_name}
Do not require Helix, Akashic, or any other product branding unless it matches
the product name or the required sign-off below.

# WHAT GOOD LOOKS LIKE
- Clear why this recipient, why now, why this sender
- One concrete recipient benefit
- One clear low-friction ask
- Competency shown via execution evidence, not adjectives
- Transparent sender motive without making the email about the sender
- No fabricated research, product usage, customers, or metrics

# SIGN-OFF — NEVER a hard fail for whitespace or exact name spelling alone
Body should end with these two lines (minor whitespace / benign name variants OK):
{sign_off}

Missing a closing entirely is a fail.
**Do NOT hard-fail for missing "building Helix" or "Helix by Wiserbond"
unless those exact lines are the required sign-off above.**
Do not invent a different product line for the signature.

# HARD FAILS (any one = hard fail)
- Invents facts not supported by the FACTS block.
- States a weak assumption as a confirmed internal problem.
- Fake personalization (over-specific claims from thin air).
- Vague CTA ("let me know your thoughts", "would love to connect", "pick your brain").
- No clear ask at all.
- Buzzword-heavy value prop with no concrete outcome.
- Message is primarily about the sender's need with little recipient value.
- No signature / no closing lines at the end of the body.
**Do NOT put body length limits in hard_fails.** Length is enforced by deterministic code.
A clear meeting / walkthrough / focused-question ask is ALLOWED in this mode.

# SCORE CALIBRATION (same for every style)
- **90–100**: strong copy.
- **80–89**: usable / sendable once Integrity + Research alignment pass.
- **70–79**: revise.
- **Below 70**: rewrite.
Do not treat 90 as the sendability cutoff. Sendability is Integrity + Alignment + score ≥ 80.

# SOFT SCORES (integers; sum = total 0–100) — Copy quality layer only
- opening_relevance (0–15): first sentence gives a reason to keep reading from the signal (not praise words)
- evidence_distance (0–20): how far the email moves past what the research package supports (20 = tight)
- question_quality (0–20): one clear ask aligned to outreach_role / recommended_ask
- subject_fit (0–10): specific, natural, connected to the observation — not marketing, not a white-paper label
- clarity (0–15)
- brevity (0–10): complete without padding; under the word limit
- naturalness (0–10): peer voice, no hollow hook words

"total" MUST equal the sum of these seven.

# OUTPUT FORMAT — raw JSON only.
{{
  "hard_fails": [],
  "soft_scores": {{
    "opening_relevance": 0,
    "evidence_distance": 0,
    "question_quality": 0,
    "subject_fit": 0,
    "clarity": 0,
    "brevity": 0,
    "naturalness": 0
  }},
  "total": 0,
  "issues": []
}}
"""


def claude_critique_email(
    email: dict,
    profile: dict,
    lead: dict,
    *,
    derived: dict | None = None,
) -> dict:
    if not ANTHROPIC_API_KEY:
        raise EnvironmentError("ANTHROPIC_API_KEY is missing from .env")

    from trace_eval import (
        COPY_SOFT_KEYS,
        alignment_hard_fails,
        annotate_critique,
        evidence_level_for,
        integrity_hard_fails,
        strip_foreign_signoff_hard_fails,
    )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    mode = _effective_email_mode(profile)
    uses_template = _uses_template_draft_path(profile)
    helix = _is_helix_profile(profile)
    sign_off = build_pb_sign_off(profile)
    product_name = profile.get("product_name") or "(campaign product)"

    if uses_template:
        if mode == "trace_strategy_email":
            system_prompt = with_first_touch_critique_checks(
                _CRITIQUE_TRACE_STRATEGY_TEMPLATE.format(
                    product_name=product_name,
                    sign_off=sign_off,
                )
            )
        elif mode == "anti_ai_email":
            system_prompt = with_first_touch_critique_checks(
                CRITIQUE_PLAIN_TEMPLATE.format(
                    product_name=product_name,
                    sign_off=sign_off,
                )
            )
        else:
            system_prompt = with_first_touch_critique_checks(
                CRITIQUE_SHORT_TEMPLATE.format(
                    product_name=product_name,
                    sign_off=sign_off,
                )
            )
    else:
        system_prompt = with_first_touch_critique_checks(
            _CRITIQUE_SYSTEM_TEMPLATE.format(
                product_name=product_name,
                sign_off=sign_off.replace("\n", " / "),
            )
        )

    from trace_drafting import format_trace_critique_context

    user_message = (
        f"{format_trace_critique_context(lead)}\n\n"
        f"SUBJECT:\n{email.get('subject') or ''}\n\n"
        f"BODY:\n{email.get('body') or ''}\n"
    )
    if derived:
        user_message += f"\nDERIVED (tone only):\n{json.dumps(derived, ensure_ascii=False)}\n"

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=768,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    raw = _strip_json_fences(response.content[0].text)
    try:
        critique = json.loads(raw)
    except json.JSONDecodeError:
        # One deterministic repair pass: take the first {...} object.
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise
        critique = json.loads(raw[start : end + 1])

    hard_fails = critique.get("hard_fails") or []
    soft = critique.get("soft_scores") or {}
    issues = critique.get("issues") or []

    total = critique.get("total")
    if not isinstance(total, int):
        keys = COPY_SOFT_KEYS
        # Backward-compatible sum if model returns an older schema.
        if not any(k in soft for k in keys):
            if uses_template:
                keys = (
                    "problem_relevance",
                    "evidence_safety",
                    "non_aggressive_tone",
                    "segment_fit",
                    "reply_likelihood",
                    "clarity",
                    "feature_density",
                )
            else:
                keys = ("personalization", "question_quality", "voice", "hook")
        total = sum(int(soft.get(k, 0) or 0) for k in keys)

    out = {
        "hard_fails": [str(x) for x in hard_fails if x],
        "soft_scores": soft,
        "total": int(total),
        "issues": [str(x) for x in issues if x],
    }
    body = email.get("body") or ""
    subject = email.get("subject") or ""
    word_count = None
    if uses_template:
        merged, meta = merge_pb_hard_fails_with_local_length(
            body,
            lead.get("first_name") or "",
            out["hard_fails"],
            warn_hi=_pb_word_warn_hi(profile),
            helix=helix,
            required_sign_off=sign_off,
        )
        out["hard_fails"] = merged
        out["length_analysis"] = meta
        word_count = meta.get("body_word_count")
    else:
        out["hard_fails"] = strip_foreign_signoff_hard_fails(
            out["hard_fails"], required_sign_off=sign_off
        )
        try:
            word_count = pb_body_length_analysis(
                body, lead.get("first_name") or "", warn_hi=75
            ).get("body_word_count")
        except Exception:
            word_count = None

    # Layered gates (Integrity → Alignment). Copy score stays in soft_scores/total.
    llm_fails = list(out["hard_fails"])
    integ = integrity_hard_fails(
        body=body,
        subject=subject,
        sign_off=sign_off,
        word_count=word_count,
        word_limit=_pb_word_warn_hi(profile) if uses_template else 75,
        llm_hard_fails=llm_fails,
    )
    align = alignment_hard_fails(
        body=body,
        evidence_level=evidence_level_for(lead),
        llm_hard_fails=llm_fails,
    )
    return annotate_critique(out, integrity_fails=integ, alignment_fails=align)


PASS_THRESHOLD = 80
# Kept as aliases for older tests/docs; all styles now share QUALITY_SENDABLE.
PASS_THRESHOLD_PB = 80
REVIEW_THRESHOLD_PB_MIN = 80
REVISE_THRESHOLD = 70
MAX_REVISE_ATTEMPTS = 1


def _decide_verdict(
    critique: dict,
    attempts_used: int,
    profile: dict,
) -> str:
    from trace_eval import decide_verdict

    return decide_verdict(
        critique,
        attempts_used,
        integrity_fails=critique.get("integrity_fails"),
        alignment_fails=critique.get("alignment_fails"),
    )


def _final_length_fields(
    lead: dict,
    body: str | None,
    *,
    warn_hi: int | None = None,
) -> dict[str, Any]:
    if not body:
        return {
            "final_word_count": None,
            "length_status": None,
            "counted_text": None,
        }
    m = pb_body_length_analysis(
        body,
        lead.get("first_name") or "",
        warn_hi=PB_WORD_WARN_HI if warn_hi is None else warn_hi,
    )
    return {
        "final_word_count": m["body_word_count"],
        "length_status": m["length_status"],
        "counted_text": m["counted_text"],
    }


def _finalize_pb_with_humanize(
    email: dict[str, str],
    critique: dict[str, Any],
    verdict: str,
    lead: dict[str, Any],
    derived: dict[str, Any] | None,
    profile: dict[str, Any],
    *,
    no_humanize: bool,
) -> tuple[dict[str, str], dict[str, Any], str, dict[str, Any]]:
    """Light humanize + final safety critique. On any risk, keep original draft."""
    orig_subj = (email.get("subject") or "").strip()
    meta: dict[str, Any] = {
        "original_subject": email.get("subject"),
        "original_body": email.get("body"),
        "humanize_applied": False,
        "humanized_body": None,
        "humanize_reason": None,
        "final_subject": orig_subj,
        "final_body": email.get("body"),
        "final_verdict": verdict,
        "verdict_after_critique": verdict,
        "critique_pre_humanize": copy.deepcopy(critique),
    }
    meta.update(_final_length_fields(lead, email.get("body"), warn_hi=_pb_word_warn_hi(profile)))

    if (
        no_humanize
        or not _uses_template_draft_path(profile)
        or verdict not in ("pass", "review")
    ):
        return email, critique, verdict, meta

    sig = build_pb_sign_off(profile)
    raw_body = email.get("body") or ""
    lines = raw_body.replace("\r\n", "\n").split("\n")
    greeting_line = (
        lines[0].strip()
        if lines
        else build_greeting_line(lead.get("first_name") or "")
    )
    length_limit = _pb_word_warn_hi(profile)

    try:
        hb, summ = claude_light_humanize(
            email,
            greeting_line=greeting_line,
            sign_off=sig,
            api_key=ANTHROPIC_API_KEY,
        )
    except Exception:
        meta["humanize_reason"] = "fallback_to_original"
        return email, critique, verdict, meta

    if not (hb or "").strip():
        meta["humanize_reason"] = "fallback_to_original"
        return email, critique, verdict, meta

    meta["humanized_body"] = hb

    viol = deterministic_post_humanize_violations(
        hb,
        lead.get("first_name") or "",
        greeting_line=greeting_line,
        sign_off=sig,
        warn_hi=length_limit,
    )
    if viol:
        meta["humanize_reason"] = "fallback_to_original"
        return email, critique, verdict, meta

    hum = {"subject": orig_subj, "body": hb}
    for key in (
        "strategy",
        "factuality",
        "subject_lines",
        "short_version",
        "quality_score",
        "send_decision",
        "cta",
        "email_mode",
    ):
        if key in email:
            hum[key] = email[key]
    crit_f = claude_critique_email(hum, profile, lead, derived=derived)
    pre_es = int((critique.get("soft_scores") or {}).get("evidence_safety", 0))
    post_es = int((crit_f.get("soft_scores") or {}).get("evidence_safety", 0))

    if crit_f.get("hard_fails"):
        meta["humanize_reason"] = "fallback_to_original"
        return email, critique, verdict, meta
    if post_es < pre_es - 3:
        meta["humanize_reason"] = "fallback_to_original"
        return email, critique, verdict, meta

    meta["humanize_applied"] = True
    meta["humanize_reason"] = (summ or "").strip() or "applied"
    meta["final_subject"] = orig_subj
    meta["final_body"] = hb
    fv = _decide_verdict(crit_f, 0, profile)
    meta["final_verdict"] = fv
    meta.update(_final_length_fields(lead, hb, warn_hi=length_limit))
    return hum, crit_f, fv, meta


PB_SOFT_KEYS = (
    "opening_relevance",
    "evidence_distance",
    "question_quality",
    "subject_fit",
    "clarity",
    "brevity",
    "naturalness",
)

LEGACY_SOFT_KEYS = (
    "opening_relevance",
    "evidence_distance",
    "question_quality",
    "subject_fit",
    "clarity",
    "brevity",
    "naturalness",
)


def _format_critique_log(critique: dict, profile: dict) -> list[str]:
    from trace_eval import COPY_SOFT_KEYS

    soft = critique.get("soft_scores") or {}
    keys = COPY_SOFT_KEYS if any(k in soft for k in COPY_SOFT_KEYS) else (
        PB_SOFT_KEYS if _uses_template_draft_path(profile) else LEGACY_SOFT_KEYS
    )
    # Fall back to whatever keys the model returned.
    if not any(k in soft for k in keys):
        keys = tuple(soft.keys()) or keys
    soft_str = ", ".join(f"{k}={soft.get(k, 0)}" for k in keys)
    band = critique.get("quality_band") or ""
    layers = critique.get("layers") or {}
    lines = [f"  total={critique.get('total', 0)}/100  ({soft_str})"]
    if band or layers:
        lines.append(
            f"  layers: integrity={layers.get('integrity', '?')} "
            f"alignment={layers.get('alignment', '?')} copy={band or layers.get('copy', '?')}"
        )
    if _uses_template_draft_path(profile):
        la = critique.get("length_analysis") or {}
        if la:
            lines.append(
                f"  deterministic words (no greeting/sign-off): "
                f"{la.get('body_word_count')} ({la.get('length_status')})"
            )
    for h in critique.get("integrity_fails") or []:
        lines.append(f"  INTEGRITY FAIL: {h}")
    for h in critique.get("alignment_fails") or []:
        lines.append(f"  ALIGNMENT FAIL: {h}")
    for h in critique.get("hard_fails") or []:
        if h in (critique.get("integrity_fails") or []):
            continue
        if h in (critique.get("alignment_fails") or []):
            continue
        lines.append(f"  HARD FAIL: {h}")
    for issue in critique.get("issues") or []:
        lines.append(f"  - {issue}")
    return lines


# ─── Send via Outlook ───────────────────────────────────────────────────────


def _get_graph_token() -> str:
    url = f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/oauth2/v2.0/token"

    response = requests.post(
        url,
        data={
            "client_id": AZURE_CLIENT_ID,
            "client_secret": AZURE_CLIENT_SECRET,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout=15,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def _graph_env_ok() -> bool:
    for var, label in [
        (AZURE_TENANT_ID, "AZURE_TENANT_ID"),
        (AZURE_CLIENT_ID, "AZURE_CLIENT_ID"),
        (AZURE_CLIENT_SECRET, "AZURE_CLIENT_SECRET"),
        (SENDER_EMAIL, "SENDER_EMAIL"),
    ]:
        if not var:
            print(f"[SEND] Missing env var: {label}")
            return False
    return True


def outlook_send_legacy(to_email: str, subject: str, body: str) -> bool:
    """sendMail only (no message IDs returned). Fallback."""
    if not _graph_env_ok():
        return False
    token = _get_graph_token()
    url = f"https://graph.microsoft.com/v1.0/users/{SENDER_EMAIL}/sendMail"
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [
                {"emailAddress": {"address": to_email}}
            ],
        },
        "saveToSentItems": "true",
    }
    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=15,
    )
    if response.status_code == 202:
        return True
    print(f"[SEND] Graph API returned {response.status_code}: {response.text}")
    return False


def outlook_send_with_meta(to_email: str, subject: str, body: str) -> tuple[bool, dict[str, Any]]:
    """
    Create draft → send → fetch ids for reply tracking. Falls back to sendMail on failure.
    """
    meta: dict[str, Any] = {
        "graph_sent_message_id": None,
        "internet_message_id": None,
        "conversation_id": None,
    }
    if not _graph_env_ok():
        return False, meta

    token = _get_graph_token()
    create_url = f"https://graph.microsoft.com/v1.0/users/{SENDER_EMAIL}/messages"
    payload = {
        "subject": subject,
        "body": {"contentType": "Text", "content": body},
        "toRecipients": [{"emailAddress": {"address": to_email}}],
    }
    cr = requests.post(
        create_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    if cr.status_code not in (200, 201):
        print(f"[SEND] create message {cr.status_code}: {cr.text[:200]} — falling back to sendMail")
        ok = outlook_send_legacy(to_email, subject, body)
        return ok, meta

    mid = (cr.json() or {}).get("id")
    if not mid:
        ok = outlook_send_legacy(to_email, subject, body)
        return ok, meta

    send_url = (
        f"https://graph.microsoft.com/v1.0/users/{SENDER_EMAIL}/messages/{mid}/send"
    )
    sr = requests.post(
        send_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if sr.status_code not in (200, 202):
        print(f"[SEND] send {sr.status_code}: {sr.text[:200]} — falling back to sendMail")
        ok = outlook_send_legacy(to_email, subject, body)
        return ok, meta

    gr = requests.get(
        f"https://graph.microsoft.com/v1.0/users/{SENDER_EMAIL}/messages/{mid}"
        f"?$select=id,conversationId,internetMessageId",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if gr.status_code == 200:
        gd = gr.json()
        meta["graph_sent_message_id"] = gd.get("id")
        meta["conversation_id"] = gd.get("conversationId")
        meta["internet_message_id"] = gd.get("internetMessageId")
    return True, meta


def outlook_send(to_email: str, subject: str, body: str) -> bool:
    ok, _meta = outlook_send_with_meta(to_email, subject, body)
    return ok


def _parse_args():
    parser = argparse.ArgumentParser(description="Trace")
    parser.add_argument(
        "--list",
        choices=list(LEAD_LISTS.keys()),
        default="akashic",
        dest="list_name",
        help="Lead list: akashic | problem_validation | helix | myzel | myzel_pet | oneaway.",
    )
    parser.add_argument(
        "--start", type=int, default=1,
        help="Lead number to start from (1-indexed).",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Process at most N leads (after --start filtering).",
    )
    parser.add_argument(
        "--question-style", choices=["yesno", "open"], default="yesno",
        dest="question_style",
        help="Question style (legacy akashic list).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicit dry-run label (default already skips send unless --send).",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Actually send via Microsoft Graph when the email passes the rubric.",
    )
    parser.add_argument(
        "--test-batch", default="", dest="test_batch",
        help="Label stored in each JSONL record.",
    )
    parser.add_argument(
        "--output-dir", default="runs", dest="output_dir",
        help="Directory for JSONL run logs (created if missing).",
    )
    parser.add_argument(
        "--no-humanize",
        action="store_true",
        help="Skip light humanize + final safety critique (problem_validation profile only).",
    )
    parser.add_argument(
        "--humanize-only",
        action="store_true",
        help="Read an existing JSONL run and apply light humanize only (no draft/critique).",
    )
    parser.add_argument(
        "--humanize-input",
        default=None,
        dest="humanize_input",
        help="Input JSONL path for --humanize-only.",
    )
    parser.add_argument(
        "--humanize-output",
        default=None,
        dest="humanize_output",
        help="Output JSONL for --humanize-only (default: input basename + .humanized.jsonl).",
    )
    parser.add_argument(
        "--track-replies",
        action="store_true",
        dest="track_replies",
        help="Scan Inbox via Graph and merge reply fields into a JSONL copy.",
    )
    parser.add_argument(
        "--replies-input",
        default=None,
        dest="replies_input",
        help="Input JSONL for --track-replies (same run log that contains sent rows).",
    )
    parser.add_argument(
        "--replies-output",
        default=None,
        dest="replies_output",
        help="Output JSONL for --track-replies (default: input + .with_replies.jsonl).",
    )
    parser.add_argument(
        "--since-days",
        type=int,
        default=14,
        dest="since_days",
        help="For --track-replies: fetch Inbox messages received in the last N days.",
    )
    parser.add_argument(
        "--discover-signals",
        "--research-only",
        action="store_true",
        dest="discover_signals",
        help=(
            "Find people (Grok). In a real terminal, you then approve/reject each "
            "person with a/r/s. Apollo + drafts run after you say yes. Alias: --research-only."
        ),
    )
    parser.add_argument(
        "--product-config",
        default=None,
        dest="product_config",
        help="Optional JSON product/discovery profile for a custom product.",
    )
    parser.add_argument(
        "--candidates-file",
        default=None,
        dest="candidates_file",
        help="JSONL from --discover-signals (review / approve / export / process).",
    )
    parser.add_argument(
        "--review-candidates",
        action="store_true",
        dest="review_candidates",
        help="Print the candidate review queue from --candidates-file.",
    )
    parser.add_argument(
        "--interactive-review",
        action="store_true",
        dest="interactive_review",
        help=(
            "In the terminal, approve/reject each PENDING person (a/r/s). "
            "Works alone with --candidates-file, or after --discover-signals. "
            "Then asks whether to fetch Apollo emails and write drafts."
        ),
    )
    parser.add_argument(
        "--no-interactive-review",
        action="store_true",
        dest="no_interactive_review",
        help="After --discover-signals, do not prompt a/r/s (scripts / non-TTY).",
    )
    parser.add_argument(
        "--candidate-id",
        default=None,
        dest="candidate_id",
        help="Candidate id for --set-human-status.",
    )
    parser.add_argument(
        "--set-human-status",
        choices=["APPROVED", "REJECTED"],
        default=None,
        dest="set_human_status",
        help="Set human_status on --candidate-id. Does not overwrite the AI recommendation.",
    )
    parser.add_argument(
        "--reject-reason",
        choices=["vendor", "wrong_role", "not_real_pain", "wrong_company", "other"],
        default=None,
        dest="reject_reason",
        help="Optional reason when --set-human-status REJECTED.",
    )
    parser.add_argument(
        "--export-approved",
        action="store_true",
        dest="export_approved",
        help="Write APPROVED candidates to CSV for manual Apollo lookup (no API spend).",
    )
    parser.add_argument(
        "--export-csv",
        default=None,
        dest="export_csv",
        help="Output path for --export-approved (default: candidates file + .approved.csv).",
    )
    parser.add_argument(
        "--import-enriched",
        default=None,
        dest="import_enriched",
        help="Apollo CSV with emails; attach only to APPROVED candidates in --candidates-file.",
    )
    parser.add_argument(
        "--process-approved",
        action="store_true",
        dest="process_approved",
        help=(
            "After you APPROVED people: fetch emails from Apollo, then draft "
            "(steps 8-9). Add --send for step 10. Manual CSV import is optional."
        ),
    )
    parser.add_argument(
        "--enrich-phones",
        action="store_true",
        dest="enrich_phones",
        help=(
            "Call Apollo people/bulk_match with reveal_phone_number for --list CSV "
            "(default akashic). Writes a sibling .phones.csv; does not send email."
        ),
    )
    return parser.parse_args()


def _append_jsonl(path: str, record: dict) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _process_lead(
    idx: int,
    total_leads: int,
    lead: dict,
    profile: dict,
    question_style: str,
    *,
    send_ok: bool,
    test_batch: str,
    jsonl_path: str,
    list_name: str,
    no_humanize: bool = False,
) -> str:
    """Returns status: sent | blocked | failed | not_sent | review."""
    name = lead.get("name", "")
    company = lead.get("company", "")
    to_email = lead.get("email", "")
    title = lead.get("title", "")

    derived = None
    if _uses_template_draft_path(profile) and _is_helix_profile(profile):
        derived = derive_campaign_fields(lead, test_batch)

    print(f"\n{'─' * 50}")
    print(f"Lead {idx}/{total_leads}: {name} ({title}) @ {company}")
    if derived:
        print(f"  segment: {derived['segment']}")
        sr = derived["segment_reason"]
        print(f"  reason: {sr}" if len(sr) <= 200 else f"  reason: {sr[:197]}…")
    print(f"{'─' * 50}")

    email: dict | None = None
    critique: dict | None = None
    verdict = "failed"
    err: str | None = None

    print("\n[1/3] Drafting email …")
    try:
        email = claude_draft_email(
            profile,
            lead,
            question_style,
            revision=None,
            derived=derived,
            test_batch=test_batch,
        )
    except Exception as exc:
        err = f"draft: {exc}"
        print(f"[FAIL] Draft failed: {exc}")
        _append_jsonl(jsonl_path, {
            "ts": datetime.now(timezone.utc).isoformat(),
            "list": list_name,
            "lead_index": idx,
            "test_batch": test_batch,
            "to_email": to_email,
            "error": err,
            "verdict": "failed",
            "segment": derived.get("segment") if derived else None,
            "segment_reason": derived.get("segment_reason") if derived else None,
            "derived": derived,
            **signal_jsonl_fields(lead),
        })
        return "failed"

    assert email is not None
    print(f"  Subject: {email['subject']}")
    print(f"  Body:\n{email['body']}\n")

    if _uses_template_draft_path(profile):
        _la = pb_body_length_analysis(
            email["body"],
            lead.get("first_name") or "",
            warn_hi=_pb_word_warn_hi(profile),
        )
        print(
            f"  (deterministic body words, greeting+sign-off excluded: "
            f"{_la['body_word_count']} — {_la['length_status']})"
        )

    attempts_used = 0
    while True:
        print(f"[2/3] Critique (round {attempts_used + 1}) …")
        try:
            critique = claude_critique_email(
                email, profile, lead, derived=derived,
            )
        except Exception as exc:
            err = f"critique: {exc}"
            print(f"[FAIL] Critique failed: {exc}")
            rec = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "list": list_name,
                "lead_index": idx,
                "test_batch": test_batch,
                "to_email": to_email,
                "subject": email.get("subject"),
                "body": email.get("body"),
                "error": err,
                "verdict": "failed",
                "derived": derived,
            }
            rec.update(_length_json_fields(profile, lead, email.get("body")))
            _append_jsonl(jsonl_path, rec)
            return "failed"

        assert critique is not None
        for line in _format_critique_log(critique, profile):
            print(line)

        verdict = _decide_verdict(critique, attempts_used, profile)
        total_s = critique.get("total", 0)

        if verdict == "pass":
            band = critique.get("quality_band") or ""
            print(
                f"  → PASS (Integrity+Alignment ok, score {total_s} ≥ {PASS_THRESHOLD}"
                f"{f', {band}' if band else ''} — auto-send only with --send)."
            )
            break
        if verdict == "review":
            print(
                f"  → REVIEW (legacy path; score {total_s}). "
                f"Manual approval; not auto-sent even with --send."
            )
            break
        if verdict == "block":
            hf = critique.get("hard_fails") or []
            integ = critique.get("integrity_fails") or []
            align = critique.get("alignment_fails") or []
            if attempts_used >= MAX_REVISE_ATTEMPTS and (hf or integ or align):
                reason = "hard fail unresolved after revision"
            elif total_s < REVISE_THRESHOLD:
                reason = f"score {total_s} below {REVISE_THRESHOLD} (rewrite)"
            elif total_s < PASS_THRESHOLD:
                reason = f"score {total_s} below {PASS_THRESHOLD} after revise"
            else:
                reason = "blocked"
            print(f"  → BLOCK ({reason}).")
            break

        attempts_used += 1
        print(f"  → REVISE (attempt {attempts_used}/{MAX_REVISE_ATTEMPTS}) …")
        try:
            email = claude_draft_email(
                profile,
                lead,
                question_style,
                revision={"previous": email, "critique": critique},
                derived=derived,
                test_batch=test_batch,
            )
        except Exception as exc:
            err = f"revision: {exc}"
            print(f"[FAIL] Revision failed: {exc}")
            rec = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "list": list_name,
                "lead_index": idx,
                "test_batch": test_batch,
                "to_email": to_email,
                "error": err,
                "verdict": "failed",
                "derived": derived,
                "subject": email.get("subject"),
                "body": email.get("body"),
            }
            rec.update(_length_json_fields(profile, lead, email.get("body")))
            _append_jsonl(jsonl_path, rec)
            return "failed"

        print(f"  Revised Subject: {email['subject']}")
        print(f"  Revised Body:\n{email['body']}\n")

    hum_meta: dict[str, Any] = {}
    if (
        _uses_template_draft_path(profile)
        and verdict in ("pass", "review")
    ):
        if no_humanize:
            print("\n[2b/3] Humanize skipped (--no-humanize).")
        else:
            print("\n[2b/3] Light humanize + final safety critique …")
        email, critique, verdict, hum_meta = _finalize_pb_with_humanize(
            email,
            critique,
            verdict,
            lead,
            derived,
            profile,
            no_humanize=no_humanize,
        )
        assert critique is not None
        if hum_meta.get("humanize_applied"):
            print(f"  → Humanize applied. Verdict: {verdict}")
            print(f"  Subject: {email['subject']}")
            print(f"  Body:\n{email['body']}\n")
        elif hum_meta.get("humanize_reason") == "fallback_to_original":
            print("  → Humanize skipped output; kept original draft (fallback_to_original).")

    sent_flag: bool | None = None
    status_out = verdict

    row_common = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "list": list_name,
        "lead_index": idx,
        "test_batch": test_batch,
        "to_email": to_email,
        "subject": email.get("subject"),
        "body": email.get("body"),
        "critique": critique,
        "derived": derived,
        "segment": derived.get("segment") if derived else None,
        "segment_reason": derived.get("segment_reason") if derived else None,
    }
    row_common.update(_length_json_fields(profile, lead, email.get("body")))
    row_common.update(draft_strategy_jsonl_fields(email))
    row_common.update(signal_jsonl_fields(lead))
    if hum_meta:
        row_common.update(hum_meta)
    row_common.setdefault("final_verdict", verdict)
    row_common.setdefault("final_body", email.get("body"))
    row_common.setdefault("humanize_reason", None)

    if verdict == "review":
        _append_jsonl(jsonl_path, {
            **row_common,
            "verdict": verdict,
            "requires_manual_review": True,
            "sent": False,
        })
        print(
            "\n[3/3] REVIEW queue — JSONL saved. "
            "Not auto-sent (--send only applies to PASS ≥ 90)."
        )
        return "review"

    if verdict == "block":
        _append_jsonl(jsonl_path, {
            **row_common,
            "verdict": verdict,
            "sent": False,
            "blocked_reason": critique.get("hard_fails") or critique.get("issues"),
        })
        return "blocked"

    # Only pass reaches here (review/block returned above). Send only with --send.
    if not send_ok:
        print(
            "\n[3/3] Not sending (--send not set). "
            "PASS — output saved; use --send to auto-send passes only."
        )
        sent_flag = False
        status_out = "not_sent"
    else:
        print(f"\n[3/3] Auto-sending to {to_email} (--send, final_verdict=pass) …")
        try:
            sent, graph_meta = outlook_send_with_meta(
                to_email, email["subject"], email["body"],
            )
        except Exception as exc:
            err = f"send: {exc}"
            print(f"[FAIL] Send failed: {exc}")
            rec = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "list": list_name,
                "lead_index": idx,
                "test_batch": test_batch,
                "to_email": to_email,
                "subject": email.get("subject"),
                "body": email.get("body"),
                "critique": critique,
                "verdict": verdict,
                "sent": False,
                "error": err,
                "derived": derived,
            }
            rec.update(_length_json_fields(profile, lead, email.get("body")))
            if hum_meta:
                rec.update(hum_meta)
            _append_jsonl(jsonl_path, rec)
            return "failed"

        if not sent:
            print(f"[FAIL] Send failed for {name}.")
            rec = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "list": list_name,
                "lead_index": idx,
                "test_batch": test_batch,
                "to_email": to_email,
                "subject": email.get("subject"),
                "body": email.get("body"),
                "critique": critique,
                "verdict": verdict,
                "sent": False,
                "error": "graph_api_failed",
                "derived": derived,
            }
            rec.update(_length_json_fields(profile, lead, email.get("body")))
            if hum_meta:
                rec.update(hum_meta)
            _append_jsonl(jsonl_path, rec)
            return "failed"

        print("  Sent.")
        sent_flag = True
        status_out = "sent"
        sent_at = datetime.now(timezone.utc).isoformat()
        safe_batch = (test_batch or "default").replace("/", "-")[:48]
        row_common.update(
            {
                "outreach_id": f"{list_name}_{safe_batch}_{idx:04d}",
                "sent_at": sent_at,
                **graph_meta,
                "reply_status": "none",
                "reply_count": 0,
                "first_reply_at": None,
                "last_reply_at": None,
                "reply_from": None,
                "reply_subject": None,
                "reply_preview": None,
                "reply_type": None,
                "matched_by": None,
            }
        )

    _append_jsonl(jsonl_path, {
        **row_common,
        "verdict": verdict,
        "sent": sent_flag,
    })
    return status_out


def _require_candidates_file(path: str | None) -> str:
    if not path or not os.path.isfile(path):
        print("[FAIL] --candidates-file must point to an existing discovery JSONL.")
        sys.exit(2)
    return path


def _profile_for_candidate(rec: dict) -> tuple[str, dict]:
    list_name = rec.get("list") or "akashic"
    key = rec.get("profile_key") or LIST_TO_PROFILE.get(list_name, list_name)
    if key in PRODUCT_PROFILES:
        return list_name, PRODUCT_PROFILES[key]
    snap = rec.get("product_snapshot")
    if isinstance(snap, dict) and snap.get("product_name"):
        return list_name, snap
    print(f"[FAIL] No product profile for candidate {rec.get('candidate_id')}.")
    sys.exit(2)


def _load_enriched_csv(filepath: str) -> list[dict]:
    leads = []
    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            email = (row.get("Email") or "").strip()
            if not email:
                continue
            lead = normalize_csv_row(row)
            lead["candidate_id"] = (row.get("candidate_id") or "").strip()
            leads.append(lead)
    return leads


def _print_next_discovery_steps(path: str) -> None:
    print("\n--- In a terminal, approve people yourself (a/r/s) ---")
    print(
        f"  python main.py --interactive-review --candidates-file {path}"
    )
    print("  Then Trace asks whether to fetch Apollo emails and write drafts.")
    print("  Sending still needs --send, or a yes at the send prompt.")
    print("\n--- Or approve one id at a time ---")
    print(
        f"     python main.py --set-human-status APPROVED "
        f"--candidate-id sig_... --candidates-file {path}"
    )
    print(f"     python main.py --process-approved --candidates-file {path}")
    print(f"     python main.py --process-approved --candidates-file {path} --send")


def _stdin_is_tty() -> bool:
    return sys.stdin.isatty()


def _prompt_yes_no(prompt: str, *, default: bool) -> bool:
    suffix = " [Y/n] " if default else " [y/N] "
    try:
        raw = input(prompt + suffix).strip().lower()
    except EOFError:
        return default
    if not raw:
        return default
    return raw in ("y", "yes")


def _run_interactive_review(path: str) -> int:
    """Prompt a/r/s for each PENDING person. Returns how many are APPROVED."""
    rows = load_candidates(path)
    pending = [r for r in rows if r.get("human_status") == "PENDING"]
    if not pending:
        print("No PENDING candidates.")
        return sum(1 for r in rows if r.get("human_status") == "APPROVED")
    print(
        f"\n--- Your turn: {len(pending)} people. "
        "Open LinkedIn from the card, then a=approve  r=reject  s=skip ---"
    )
    for i, rec in enumerate(pending, 1):
        print(f"\n[{i}/{len(pending)}]")
        print(format_review_card(rec))
        try:
            raw = input("Approve / Reject / Skip [a/r/s]: ").strip().lower()
        except EOFError:
            print("No more input. Remaining people left PENDING.")
            break
        if raw in ("a", "approve", "approved"):
            apply_human_decision(rows, rec["candidate_id"], "APPROVED")
            print("  → APPROVED")
        elif raw in ("r", "reject", "rejected"):
            try:
                reason = input(
                    "Optional reason (vendor/wrong_role/not_real_pain/"
                    "wrong_company/other/blank): "
                ).strip().lower() or None
            except EOFError:
                reason = None
            apply_human_decision(rows, rec["candidate_id"], "REJECTED", reason)
            print("  → REJECTED")
        else:
            print("  → skipped (still PENDING)")
    save_candidates(path, rows)
    approved = sum(1 for r in rows if r.get("human_status") == "APPROVED")
    rejected = sum(1 for r in rows if r.get("human_status") == "REJECTED")
    still = sum(1 for r in rows if r.get("human_status") == "PENDING")
    print(f"\nSaved → {path}")
    print(f"APPROVED {approved}  REJECTED {rejected}  PENDING {still}")
    return approved


def _maybe_continue_after_review(path: str, args: argparse.Namespace) -> None:
    rows = load_candidates(path)
    approved = sum(1 for r in rows if r.get("human_status") == "APPROVED")
    if approved == 0:
        print("No one approved. Stopping before Apollo / drafts.")
        return
    if _stdin_is_tty():
        if not _prompt_yes_no(
            f"{approved} approved. Fetch emails from Apollo and write drafts?",
            default=True,
        ):
            print(
                f"Stopped. Later: python main.py --process-approved "
                f"--candidates-file {path}"
            )
            return
        send_ok = bool(args.send)
        if send_ok:
            print("`--send` is on: PASS ≥90 will go out.")
        else:
            send_ok = _prompt_yes_no(
                "Send drafts that score 90+ now?",
                default=False,
            )
    elif not args.process_approved:
        print(
            f"{approved} approved. Non-interactive: run "
            f"python main.py --process-approved --candidates-file {path}"
        )
        return
    else:
        send_ok = bool(args.send)
    _run_process_approved(path, args, send_ok=send_ok)


def _run_process_approved(
    path: str,
    args: argparse.Namespace,
    *,
    send_ok: bool,
) -> None:
    rows = load_candidates(path)
    need_email = [r for r in rows if should_enrich(r) and not should_draft(r)]
    if need_email:
        print("=== Apollo email enrich (approved, no address yet) ===")
        try:
            n = enrich_approved_candidates(rows)
        except EnvironmentError as exc:
            print(f"[FAIL] {exc}")
            sys.exit(1)
        except Exception as exc:
            print(f"[FAIL] Apollo email enrich failed: {exc}")
            sys.exit(1)
        save_candidates(path, rows)
        print(f"Attached {n} emails. {len(need_email) - n} still missing.")
    ready = [r for r in rows if should_draft(r)]
    skipped_no_email = [
        r for r in rows if should_enrich(r) and not should_draft(r)
    ]
    blocked = [r for r in rows if r.get("human_status") != "APPROVED"]
    if skipped_no_email:
        print(
            f"{len(skipped_no_email)} APPROVED without email — kept, not drafted."
        )
    if not ready:
        print("No APPROVED candidates with email. Nothing sent to the outbound engine.")
        return
    os.makedirs(args.output_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_batch = (args.test_batch or "signals-approved").replace("/", "-")[:64]
    jsonl_path = os.path.join(
        args.output_dir, f"trace_{stamp}_{safe_batch}.jsonl",
    )
    print("=== Trace outbound (approved signals) ===")
    print("    Gate 1 already passed (human_status=APPROVED).")
    print(
        "    Gate 2: "
        + ("`--send` — PASS ≥90 will go out." if send_ok else "no --send, drafts only.")
    )
    print(f"    JSONL: {jsonl_path}\n")
    counts: dict[str, int] = {}
    for i, rec in enumerate(ready, 1):
        list_name, profile = _profile_for_candidate(rec)
        lead = enrich_lead_from_candidate(rec)
        result = _process_lead(
            i,
            len(ready),
            lead,
            profile,
            args.question_style,
            send_ok=send_ok,
            test_batch=args.test_batch or "signals-approved",
            jsonl_path=jsonl_path,
            list_name=list_name,
            no_humanize=args.no_humanize,
        )
        rec["passed_to_outbound"] = True
        rec["outbound_sent"] = result == "sent"
        rec["outbound_result"] = result
        counts[result] = counts.get(result, 0) + 1
    save_candidates(path, rows)
    print(f"\nPipeline complete: {counts}  |  not-approved ignored: {len(blocked)}")
    print(f"JSONL: {jsonl_path}")
    print(f"Candidates: {path}")


def main():
    args = _parse_args()
    if args.track_replies:
        if not args.replies_input or not os.path.isfile(args.replies_input):
            print("[FAIL] --track-replies requires an existing --replies-input JSONL path.")
            sys.exit(2)
        from reply_tracker import run_cli

        run_cli(args.replies_input, args.replies_output, int(args.since_days))
        return

    if args.enrich_phones:
        csv_filename = LEAD_LISTS[args.list_name]
        csv_path = os.path.join(os.path.dirname(__file__) or ".", csv_filename)
        if not os.path.isfile(csv_path):
            print(f"[FAIL] lead CSV not found: {csv_path}")
            sys.exit(2)
        out = args.export_csv
        if not out:
            base, ext = os.path.splitext(csv_path)
            out = f"{base}.phones{ext or '.csv'}"
        print("=== Trace Apollo phone enrich ===")
        print(f"    List: {args.list_name}  |  Source: {csv_path}")
        print("    Endpoint: people/bulk_match  reveal_phone_number=true")
        print("    Mobile reveal bills extra Apollo credits if a number is found.")
        print(f"    Output: {out}\n")
        try:
            from apollo_enrich import enrich_csv_phones

            stats = enrich_csv_phones(
                csv_path,
                out,
                limit=args.limit,
                start=args.start,
            )
        except EnvironmentError as exc:
            print(f"[FAIL] {exc}")
            sys.exit(1)
        except Exception as exc:
            print(f"[FAIL] Apollo phone enrich failed: {exc}")
            sys.exit(1)
        print(
            f"Requested {stats['requested']}  |  with a number {stats['matched']}  |  "
            f"mobile {stats['mobile']}  direct {stats['work_direct']}  other {stats['other']}"
        )
        print(f"Wrote → {out}")
        return

    if args.humanize_only:
        if not args.humanize_input or not os.path.isfile(args.humanize_input):
            print("[FAIL] --humanize-only requires an existing --humanize-input JSONL path.")
            sys.exit(2)
        base, ext = os.path.splitext(args.humanize_input)
        out_path = args.humanize_output
        if not out_path:
            out_path = f"{base}.humanized{ext or '.jsonl'}"
        try:
            n = run_humanize_jsonl_batch(
                args.humanize_input, out_path, ANTHROPIC_API_KEY,
            )
        except EnvironmentError as exc:
            print(f"[FAIL] {exc}")
            sys.exit(1)
        print(f"Humanize-only: {n} lines written → {out_path}")
        return

    custom_profile = None
    if args.product_config:
        try:
            custom_profile = load_custom_profile(args.product_config)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"[FAIL] --product-config: {exc}")
            sys.exit(2)

    if args.discover_signals:
        list_name = (
            custom_profile.get("list_name")
            if custom_profile
            else args.list_name
        )
        profile_key = (
            "custom"
            if custom_profile
            else LIST_TO_PROFILE[args.list_name]
        )
        profile = custom_profile or PRODUCT_PROFILES[profile_key]
        ctx = discovery_context_from_profile(profile)
        os.makedirs(args.output_dir, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_batch = (args.test_batch or "default").replace("/", "-")[:64]
        jsonl_path = args.candidates_file or os.path.join(
            args.output_dir,
            f"signals_{stamp}_{list_name}_{safe_batch}.jsonl",
        )
        limit = args.limit if args.limit is not None else 8
        print("=== Trace research only (steps 1-6) ===")
        print(f"    List: {list_name}  |  Product: {ctx.get('product_name')}")
        print("    Grok: X + web in parallel, then merge/rank")
        print("    Prefer recent, identifiable, first-person pain. Cap per channel:", limit)
        print("    In a terminal you then approve/reject each person (a/r/s).")
        print("    Apollo + drafts run after you say yes. Send still needs --send or a yes.")
        print(f"    Candidates: {jsonl_path}\n")
        cost_path = os.path.join(
            args.output_dir,
            f"research_cost_{stamp}_{list_name}_{safe_batch}.jsonl",
        )
        print(f"    Cost log: {cost_path}")
        cache_path = os.path.join(args.output_dir, f"research_cache_{list_name}.jsonl")
        seed_paths = sorted(
            p for p in glob.glob(os.path.join(args.output_dir, f"signals_*_{list_name}_*.jsonl"))
            if os.path.abspath(p) != os.path.abspath(jsonl_path)
        )
        print(f"    Cache: {cache_path}")
        if seed_paths:
            print(f"    Seed from {len(seed_paths)} prior candidate file(s)\n")
        else:
            print()
        try:
            rows = run_discovery(
                profile,
                list_name=list_name,
                profile_key=profile_key,
                limit=limit,
                cost_log_path=cost_path,
                run_id=stamp,
                cache_path=cache_path,
                seed_candidate_paths=seed_paths,
            )
        except EnvironmentError as exc:
            print(f"[FAIL] {exc}")
            if os.path.isfile(cost_path):
                print_research_cost_summary(cost_path)
            sys.exit(1)
        except Exception as exc:
            print(f"[FAIL] Discovery failed: {exc}")
            if os.path.isfile(cost_path):
                print_research_cost_summary(cost_path)
            sys.exit(1)
        save_candidates(jsonl_path, rows)
        print_research_cost_summary(cost_path)
        if not rows:
            print("No signals returned. JSONL written anyway.")
        for rec in rows:
            print(format_review_card(rec))
            print()
        print(f"Wrote {len(rows)} candidates → {jsonl_path}")
        if rows and not args.no_interactive_review and _stdin_is_tty():
            _run_interactive_review(jsonl_path)
            _maybe_continue_after_review(jsonl_path, args)
        else:
            _print_next_discovery_steps(jsonl_path)
        return

    if args.set_human_status:
        path = _require_candidates_file(args.candidates_file)
        if not args.candidate_id:
            print("[FAIL] --set-human-status requires --candidate-id.")
            sys.exit(2)
        rows = load_candidates(path)
        try:
            rec = apply_human_decision(
                rows, args.candidate_id, args.set_human_status, args.reject_reason,
            )
        except (KeyError, ValueError) as exc:
            print(f"[FAIL] {exc}")
            sys.exit(2)
        save_candidates(path, rows)
        print(
            f"{rec['candidate_id']}: recommendation={rec.get('recommendation')} "
            f"human_status={rec.get('human_status')}"
        )
        if rec.get("human_status") == "APPROVED":
            if args.process_approved:
                print("Approved. Continuing to Apollo email match + drafts.")
            else:
                print("Approved. Next: --process-approved (Apollo emails + drafts).")
        else:
            print("Rejected. No enrichment or outbound.")
        if not args.process_approved:
            return

    if args.interactive_review:
        path = _require_candidates_file(args.candidates_file)
        _run_interactive_review(path)
        _maybe_continue_after_review(path, args)
        return

    if args.review_candidates:
        path = _require_candidates_file(args.candidates_file)
        rows = load_candidates(path)
        if not rows:
            print("No candidates in file.")
            return
        for rec in rows:
            print(format_review_card(rec))
            print()
        pending = sum(1 for r in rows if r.get("human_status") == "PENDING")
        approved = sum(1 for r in rows if r.get("human_status") == "APPROVED")
        rejected = sum(1 for r in rows if r.get("human_status") == "REJECTED")
        print(
            f"{len(rows)} candidates  |  PENDING {pending}  "
            f"APPROVED {approved}  REJECTED {rejected}"
        )
        _print_next_discovery_steps(path)
        return

    if args.export_approved:
        path = _require_candidates_file(args.candidates_file)
        rows = load_candidates(path)
        out = args.export_csv
        if not out:
            base, _ext = os.path.splitext(path)
            out = f"{base}.approved.csv"
        n = export_approved_csv(rows, out)
        print(f"Exported {n} APPROVED candidates → {out}")
        print("Look them up in Apollo, download a CSV with Email, then --import-enriched.")
        return

    if args.import_enriched:
        path = _require_candidates_file(args.candidates_file)
        if not os.path.isfile(args.import_enriched):
            print("[FAIL] --import-enriched file not found.")
            sys.exit(2)
        rows = load_candidates(path)
        leads = _load_enriched_csv(args.import_enriched)
        n = import_enriched_leads(rows, leads)
        save_candidates(path, rows)
        print(f"Attached {n} emails to APPROVED candidates. REJECTED rows were ignored.")
        print(f"Saved → {path}")
        print("Next: python main.py --process-approved --candidates-file ...")
        return

    if args.process_approved:
        path = _require_candidates_file(args.candidates_file)
        _run_process_approved(path, args, send_ok=bool(args.send))
        return

    start_idx = args.start
    question_style = args.question_style
    list_name = args.list_name
    send_ok = args.send
    test_batch = args.test_batch or "default"

    profile_key = LIST_TO_PROFILE[list_name]
    profile = PRODUCT_PROFILES[profile_key]

    os.makedirs(args.output_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_batch = test_batch.replace("/", "-")[:64] or "default"
    jsonl_path = os.path.join(
        args.output_dir,
        f"trace_{stamp}_{safe_batch}.jsonl",
    )

    print("=== Trace ===")
    print(f"    List: {list_name}  → profile: {profile_key}  |  Product: {profile['product_name']}")
    print(f"    Question style: {question_style}  |  Starting from lead #{start_idx}")
    send_note = (
        "ON — auto-send PASS only (Integrity + Alignment + score ≥ 80)"
        if send_ok
        else "OFF — JSONL only; --send sends PASS tier only"
    )
    print(f"    {send_note}")
    print(f"    Test batch: {test_batch!r}")
    print(f"    JSONL log: {jsonl_path}\n")

    csv_filename = LEAD_LISTS[list_name]
    csv_path = os.path.join(os.path.dirname(__file__) or ".", csv_filename)

    if os.path.exists(csv_path):
        print(f"[0] Loading leads from {csv_filename} …")
        leads = load_leads_from_csv(csv_path)
        if not leads:
            print("CSV loaded but no valid leads found. Exiting.")
            sys.exit(0)
        print(f"Loaded {len(leads)} leads (disqualified rows excluded):\n")
        for i, lead in enumerate(leads, 1):
            print(
                f"  {i}. {lead['name']} ({lead['title']}) @ {lead['company']} "
                f"[{lead['email']}]"
            )
        print()
    else:
        job_titles = ["VP of Sales", "Head of Sales"]
        industries = ["SaaS", "Technology"]
        employee_range = (50, 500)
        num_leads = 3
        try:
            print("[0] Fetching leads from Apollo …")
            leads = apollo_get_leads(job_titles, industries, employee_range, num_leads)
            if not leads:
                print("No leads with verified emails found. Exiting.")
                sys.exit(0)
            print(f"Found {len(leads)} leads:\n")
            for i, lead in enumerate(leads, 1):
                print(
                    f"  {i}. {lead['name']} ({lead['title']}) @ {lead['company']} "
                    f"[{lead['email']}]"
                )
            print()
        except Exception as exc:
            print(f"[FAIL] Step 0 (Apollo Search) failed: {exc}")
            sys.exit(1)

    eligible = [(i, lead) for i, lead in enumerate(leads, 1) if i >= start_idx]
    if args.limit is not None:
        eligible = eligible[: args.limit]

    counts: dict[str, int] = {}

    for idx, lead in eligible:
        result = _process_lead(
            idx,
            len(eligible),
            lead,
            profile,
            question_style,
            send_ok=send_ok,
            test_batch=test_batch,
            jsonl_path=jsonl_path,
            list_name=list_name,
            no_humanize=args.no_humanize,
        )
        counts[result] = counts.get(result, 0) + 1

    print(f"\n{'═' * 50}")
    parts = [
        f"{k}: {v}" for k, v in sorted(counts.items()) if v
    ]
    summary = "Pipeline complete: " + ", ".join(parts) + f" | leads processed cap: {len(leads)}"
    print(summary)
    print(f"JSONL: {jsonl_path}")
    print(f"{'═' * 50}")


if __name__ == "__main__":
    main()
