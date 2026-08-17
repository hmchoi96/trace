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
from trace_strategy_prompt import (
    build_trace_strategy_sender_block,
    build_trace_strategy_system_prompt,
    draft_strategy_jsonl_fields,
    normalize_trace_strategy_draft,
)

# Strategy-mode counted-body hard limit (greeting + sign-off excluded)
TRACE_STRATEGY_WORD_WARN_HI = 130

load_dotenv()

# ─── Environment ────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID")
AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID")
AZURE_CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET")
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
APOLLO_API_KEY = os.getenv("APOLLO_API_KEY")
SENDER_FIRST_NAME = os.getenv("SENDER_FIRST_NAME", "Jamie")


def build_pb_sign_off() -> str:
    return f"{SENDER_FIRST_NAME}\nbuilding Helix"


def _pb_sign_off_body_acceptable(body: str) -> bool:
    """Lenient check: last non-empty lines look like FirstName + building Helix / Helix by Wiserbond."""
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
    # First line: plausible given name (not enforcing a single name)
    first_ok = 1 <= len(line1) <= 40 and not line1.lower().startswith("http")
    return bool(second_ok and first_ok)


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
    """Deterministic length fields for JSONL (problem_validation only)."""
    if profile.get("profile_kind") != "problem_validation" or not body:
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


def strip_pb_signoff_noise_hard_fails(body: str, hard_fails: list[str]) -> list[str]:
    """Do not burn revise attempts on brittle sign-off whitespace mismatches."""
    if not _pb_sign_off_body_acceptable(body):
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
        "product_name": "Wiserbond",
        "product_context": (
            "Wiserbond solves Corporate Amnesia: past judgments, reasoning, "
            "and decision context vanish as people leave and documents "
            "scatter. We structure those lost decisions into reusable "
            "memory. We extract Condition (what was happening), Judgment "
            "(what was decided), and Reasoning (why) from internal docs "
            "and records. Local first, on prem deployable. Not a search "
            "tool or chatbot."
        ),
        "sign_off": "— Hyunmyung, Wiserbond",
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
                "We use the CRM to see why we passed and whether that was the right call",
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
            ],
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
}


LIST_TO_PROFILE = {
    "akashic": "akashic",
    "problem_validation": "problem_validation",
    "helix": "problem_validation",
}

LEAD_LISTS = {
    "akashic": "akashic_record_list.csv",
    "problem_validation": "helix_list.csv",
    "helix": "helix_list.csv",
}


# ─── Email Draft (legacy Akashic) ──────────────────────────────────────────

_DRAFTING_SYSTEM_TEMPLATE = """\
You write first-touch cold emails for B2B discovery outreach.

# PRODUCT CONTEXT (internal only, NEVER output any of this)
{product_context}

# STYLE
1. Write as if speaking out loud to a peer, pragmatic and direct.
2. Never start a sentence with a verb (e.g. "Noticed…", "Saw…").
3. Never use em dashes or dashes anywhere in the subject or body. The em
   dash inside the sign off line is the only allowed exception.
4. Never use hollow words: "impressed", "inspiring", "admire", "fascinating",
   "excited", "thrilled", "remarkable", "incredible", "love", "noticed".
5. Structure: exactly 2 sentences + 1 closing question. Nothing more.
   Keep the total body under 40 words (excluding the sign off).
6. Subject line: under 6 words, lowercase friendly.
7. Never label or reference the prospect's seniority level, career stage,
   or experience. Words like "early career", "junior", "new to", "as a
   young professional" are banned. Write as equal to equal.

# PERSONALIZATION
1. The email is about the PROSPECT and their likely pain, not about us.
2. The only inputs you have about this prospect are their name, title,
   company, and any PUBLIC SIGNAL block in the user message. Do not invent
   posts, news, achievements, or specifics beyond that block.
   Personalize through the SHAPE of their work (what someone with this
   title at this company likely deals with day to day), not made-up facts.
   If a PUBLIC SIGNAL is present, you may refer to it briefly; do not claim
   they want to buy, asked for a solution, or overquote the source.
3. Do not just reference their title or company name as personalization.

# QUESTION
{question_rules}

# WHAT NEVER APPEARS IN THE EMAIL
1. Do NOT name {product_name} in the body. Do NOT describe what
   {product_name} does or how it works. Zero product mentions in the body.
2. Do NOT request a meeting, call, or demo.
3. Do NOT pitch, sell, or explain what any company does.
4. Do NOT repeat the prospect's name after the greeting.
5. The sender signs off as "{sign_off}" which is the ONLY place
   any company name may appear. Include this sign off verbatim.

Before outputting, ask yourself: would a busy professional reply to this?
If not, rewrite it shorter and sharper.

OUTPUT FORMAT — return raw JSON only, no markdown fences:
{{"subject": "...", "body": "..."}}
"""


# ─── Problem-validation copy variation (opening + closing only) ─────────────

# Founder openings: struggle first (no product name). Helix only in the build line.
_PB_OPENING_FOUNDER: tuple[str, ...] = (
    "I have been cold calling myself and the awkward part is when",
    "I am doing founder-led outbound and keep losing the thread when",
    "While trying to sell through cold outreach, I realized",
    "I have been running my own outbound and it is tougher than I expected when",
    "I am a founder doing cold calls and the friction shows up when",
)

# Sales leaders at outbound-heavy orgs: ramp / team consistency (not personal founder story)
_PB_OPENING_SALES_LEADER: tuple[str, ...] = (
    "When teams ramp new reps on outbound, one thing I keep seeing is",
    "If you are trying to shorten ramp on live calls, I noticed",
    "Talking to sales leaders about outbound, a pattern that comes up is",
)

_PB_CLOSING_FOUNDER: tuple[str, ...] = (
    "Do you run into that too when you sell, and how do you handle the script mid-call?",
    "Curious if that is something you have felt as well, and what you do in the moment.",
    "Is that something you have had to figure out yourself while selling?",
    "Do you get the same thing on calls, or have you found a way that works for you?",
)

_PB_CLOSING_SALES_LEADER: tuple[str, ...] = (
    "Is cutting mid-call script search during ramp something you are working on right now?",
    "Do new reps mostly learn the script from shadowing, or is there something more structured?",
    "Do your reps still lose seconds hunting for the right line, or is that mostly solved?",
)


_ANTI_AI_OPENING: tuple[str, ...] = (
    "I might be off here, but",
    "Not sure this is actually a problem for you, but",
    "This might be a bad read, but",
    "I could be wrong here, but",
)

_ANTI_AI_CLOSING: tuple[str, ...] = (
    "Worth sending?",
    "Should I send the 3-line version?",
    "Would an example be useful, or am I off?",
    "Is that worth sending over?",
)


def _pb_slot_index(lead: dict, test_batch: str, salt: str, modulo: int) -> int:
    key = f"{lead.get('email') or ''}|{test_batch}|{salt}"
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return int(h[:12], 16) % max(modulo, 1)


def _anti_ai_copy_constraints_block(lead: dict, derived: dict, test_batch: str) -> str:
    """Deterministic anti-AI voice anchors, kept separate from PB copy pools."""
    oi = _pb_slot_index(lead, test_batch, "anti_ai_open", len(_ANTI_AI_OPENING))
    ci = _pb_slot_index(lead, test_batch, "anti_ai_close", len(_ANTI_AI_CLOSING))
    opening = _ANTI_AI_OPENING[oi]
    closing = _ANTI_AI_CLOSING[ci]
    seg = derived.get("segment") or ""
    is_sales_leader = seg == SEG_SALES_LEADER

    lines: list[str] = [
        "=== REQUIRED ANTI-AI COPY CONSTRAINTS (this lead) ===",
        "",
        "OPENING: The first sentence after the greeting must start with this exact phrase:",
        f'  "{opening}"',
        "",
        "CLOSING QUESTION: End the body with EXACTLY this question as its own paragraph",
        "(immediately before the two-line sign-off). Match wording and punctuation:",
        f'  "{closing}"',
        "",
        "ANTI-AI VOICE:",
        "- Sound like a person making a careful guess, not a polished outbound sequence.",
        "- Use plain words, mild uncertainty, and one concrete hypothesis.",
        "- Do not flatter the company. Do not use SaaS words like unlock, streamline,",
        "  transform, optimize, accelerate, elevate, empower, seamless, or game-changing.",
        "- No meeting ask, no demo ask, no calendar ask, no feature tour.",
        "- Keep the body a little imperfect but clear. Short beats impressive.",
        "",
    ]

    if is_sales_leader:
        lines.extend([
            "SALES LEADER ANGLE:",
            "- Make the guess about rep ramp, mid-call script search, or attention lost",
            "  hunting for the right line instead of listening.",
            "- Do not pretend to know their process or claim their reps struggle.",
            "",
        ])
    else:
        lines.extend([
            "FOUNDER / OPERATOR ANGLE:",
            "- Make the guess about founder-led outbound and losing attention while",
            "  scrolling a long script doc mid-call.",
            "- Mention Helix at most once as something you are building, not a finished",
            "  product they should buy.",
            "",
        ])

    lines.append("=== end anti-ai copy constraints ===")
    return "\n".join(lines)


def _pb_copy_constraints_block(lead: dict, derived: dict, test_batch: str) -> str:
    """Deterministic opening + closing lines so consecutive sends look less templated."""
    seg = derived.get("segment") or ""
    is_sales_leader = seg == SEG_SALES_LEADER

    if is_sales_leader:
        opool = _PB_OPENING_SALES_LEADER
        pool = _PB_CLOSING_SALES_LEADER
        oi = _pb_slot_index(lead, test_batch, "pb_open_sl", len(opool))
        opening = opool[oi]
    else:
        opool = _PB_OPENING_FOUNDER
        pool = _PB_CLOSING_FOUNDER
        oi = _pb_slot_index(lead, test_batch, "pb_open_f", len(opool))
        opening = opool[oi]

    ci = _pb_slot_index(lead, test_batch, "pb_close", len(pool))
    closing = pool[ci]

    lines: list[str] = [
        "=== REQUIRED COPY CONSTRAINTS (this lead) ===",
        "",
        "OPENING: The first observational sentence after the greeting must follow this voice.",
        "Start from (continue naturally in your own words):",
        f"  «{opening} …»",
        "",
        "CLOSING QUESTION: End the body with EXACTLY this question as its own paragraph",
        "(immediately before the two-line sign-off). Match wording and punctuation:",
        f'  "{closing}"',
        "",
    ]

    if is_sales_leader:
        lines.extend([
            "SALES LEADER MODE (outbound / revenue team):",
            "- Do NOT open with your personal founder cold-calling struggle.",
            "- Frame around team ramp, new reps hunting for script lines mid-call, or",
            "  attention lost to search instead of listening.",
            "- If you mention what you are building, name it: Helix (one short line, what it helps with).",
            '  Never say "a lightweight tool" without naming Helix.',
            "- Do NOT lecture about their industry (fleet, telematics, etc.).",
            "",
        ])
    else:
        lines.extend([
            "FOUNDER PEER MODE:",
            "- Write like one founder talking to another: you cold call / do outbound yourself,",
            "  and mid-call script search steals attention from the prospect. Ask if they feel",
            "  the same and how they handle it.",
            "- HELIX NAMING (required if you mention the product): use the name Helix.",
            '  Good: "I am building Helix so the right script line is one tap away mid-call."',
            '  Bad: opening says "for Helix" then later "a lightweight tool" with no name.',
            '  Bad: "a lightweight tool" with no name at all.',
            "- Order: (1) your cold-call struggle, no product name, (2) one short line",
            "  introducing Helix and what you are building it for, (3) required closing question.",
            "- Tone: honest and curious, not a polished marketing email or industry consultant.",
            "- Do NOT open with their industry jargon (fleet data, telematics, fraud stack, etc.)",
            "  unless the FACTS block is explicit and you keep it to one light phrase.",
            "- Do NOT use: shared playbook, wing it in the moment, trust-heavy industries like…,",
            "  objections around data security or technical integration can catch you off guard.",
            "",
        ])

    lines.append("=== end copy constraints ===")
    return "\n".join(lines)


# Problem-validation / founder-led discovery (not a hard sales pitch)

_DRAFTING_PB_TEMPLATE = """\
You write first-touch cold emails for B2B *problem validation*.

# PRODUCT CONTEXT (internal only; do not dump all features into the email)
{product_context}

# EMAIL MODE: {email_mode}
- **Founder / technical founder / early team / complex-product founders:** sound like
  a founder who cold calls, loses attention hunting for the right script line mid-call,
  is building Helix because of that, and wants to know if the reader feels the same.
  Not consultant copy about the reader's vertical.
- **Sales leader / revenue leader:** sound like someone curious about ramp and whether
  new reps still burn seconds scrolling a long script doc instead of listening.
- Calm, peer-to-peer, curious, non-accusatory. At most ONE short line about what
  you are building (I started building / I am building Helix).

# BANNED (accusatory / invented pain) — never use equivalent phrases either:
Claims that the reader blanks, forgets, struggles, loses deals, repeats mistakes,
that their team handles objections badly, or that their process is broken.
Do not invent company facts. If Apollo data only suggests a context, you may
use soft hypothesis language as YOUR observation, not their failure:
"I noticed…", "one pattern I am seeing is…", "I am trying to understand whether…",
"the hard part seems to be…".

# CLOSING QUESTION (important)
- The user message gives a **REQUIRED CLOSING QUESTION** for this lead: paste it
  verbatim as the final paragraph before the sign-off (do not substitute a generic
  "pull up the right response during the call" question).
- Avoid implying the reader is under-prepared ("wishing you had better…").
- Avoid repeating the same generic subject-line theme for every lead; follow the
  REQUIRED SUBJECT for this segment from the user message.

# TYPOGRAPHY
- Do not use em dashes (—); use commas or periods instead.

# COPY GUARDRAILS (first-touch only)
- Avoid overly narrow technical jargon in the first email unless the prospect context is
  extremely reliable. Prefer broader phrasing (e.g. "technical or integration questions")
  instead of niche acronyms or product-specific terms inferred from weak signals.
- Soften harsh evaluative framing. Prefer lines like "that seems easy to overlook" or
  "that seems worth paying attention to" over blunt cost/judgment phrasing such as
  "that seems like a costly pattern."
- When mentioning what you are building, always say **Helix** by name (not "a lightweight
  tool" or "a tool" alone). Prefer "I am building Helix" / "I started building Helix"
  plus at most a few words on what it does (one-tap script line mid-call; less search,
  more listening). Never "I built".
- Do not name Helix in the opening struggle sentence and then refer to an unnamed tool later.

# APOLLO FACTS — only trust the FACTS block in the user message. No other research.

# INDUSTRY LANGUAGE
Do not write consultant-style openers about the reader's industry (e.g. "in trust-heavy
industries like fleet data and telematics…"). If you need context, keep it generic:
"on live calls", "when you need the next script line", "when the prospect pushes back".

# EVIDENCE — no unsupported claims about how THEY work at their company
Never write lines like "I noticed you're handling both the technical and sales side
at {{company}}" unless the FACTS block explicitly supports that division of labor.
Prefer **general, role-segment framing** that does not assert: their schedule, their
responsibilities, or how they split time. Safe patterns include:
"As a technical founder…", "For technical founders…", "In founder-led sales…",
"If you are involved in early sales…", "Selling {{company}}…" only when Title/role
clearly implies selling (e.g. Founder, CEO) — not for arbitrary engineers.

# STRUCTURE
- Greet with Hi and the first name only (from FACTS).
- For founders: struggle (no product name) → one line naming **Helix** and why you are
  building it → required closing question. Max 2–3 sentences before the question.
- For sales leaders: team/ramp observation, optional tool line, then required closing.
- No feature tour. No "preload responses and click" detail in the first email.

# LENGTH (the pipeline counts words deterministically: prose after “Hi {{first_name}},”
# and before the two-line signature; blank lines do not add words).
- Target about 45–65 words; never pad just to lengthen. Shorter is OK if natural.
- 66–75 = warning band; over 75 is blocked by code, not by you guessing word count.
- If the required opening and closing make the email too long, shorten the middle
  sentence(s) first. The final body must stay at or under 75 words excluding greeting
  and sign-off.

# SUBJECT
- Under 7 words, lowercase-friendly, not clickbait.
- The user message gives REQUIRED SUBJECT for this segment — use that exact
  wording (or extremely close). Do not reuse one subject line for unrelated segments.

# SIGN-OFF (end of body after the question). Two lines:
{sign_off}

OUTPUT FORMAT — return raw JSON only, no markdown fences:
{{"subject": "...", "body": "..."}}
End with the two-line sign-off above; minor whitespace differences are acceptable.
"""

_DRAFTING_ANTI_AI_TEMPLATE = """\
You write first-touch cold emails in the opposite style of generic AI outbound.

# PRODUCT CONTEXT (internal only; do not dump all features into the email)
{product_context}

# EMAIL MODE: {email_mode}
The goal is not to sound polished. The goal is to sound like one real person
making a specific, low-pressure guess.

# CORE SHAPE
- Hi {{first_name}},
- Start with the exact required uncertainty phrase from the user message.
- One grounded observation/hypothesis based only on FACTS and DERIVED context.
- One short line about Helix only if it helps the reader understand why you are writing.
- End with the exact required closing question from the user message.
- Sign off with the two-line signature below.

# WHAT THIS IS NOT
- Not a sales pitch.
- Not consultant copy about their industry.
- Not a compliment about their growth, mission, platform, team, funding, or innovation.
- Not a meeting request.
- Not a demo request.
- Not a product tour.

# PERSONALIZATION RULES
- Use only the FACTS block and the DERIVED context in the user message.
- Do not invent posts, news, initiatives, metrics, funding, tech stack usage, or internal process.
- If the facts are thin, say less. A cautious guess is better than fake specificity.
- You may use role-level context such as "founder-led outbound" or "rep ramp" when the segment supports it.

# STYLE RULES
- Plain English. Short sentences. No buzzwords.
- Mild uncertainty is required: it should feel like "I might be wrong", not "we know your pain".
- No em dashes. Use commas or periods.
- No "I hope you are well", "unlock", "streamline", "transform", "optimize",
  "accelerate", "elevate", "empower", "seamless", "game-changing", "cutting-edge",
  "quick 15 minutes", or "would love to".
- Do not start every sentence with "I". Vary the rhythm naturally.

# LENGTH
- Target 35-60 counted words excluding greeting and sign-off.
- Never exceed 75 counted words excluding greeting and sign-off.

# SUBJECT
- Under 6 words.
- Lowercase-friendly.
- Human and slightly underconfident is OK.
- Do not blindly copy the derived subject hint if it sounds like a marketing category.

# SIGN-OFF (end of body after the question). Two lines:
{sign_off}

OUTPUT FORMAT - return raw JSON only, no markdown fences:
{{"subject": "...", "body": "..."}}
End with the two-line sign-off above; minor whitespace differences are acceptable.
"""

_PB_REVISION_EXTRAS = """\

# REVISION RULES (only when rewriting after critique)
- Fix ONLY the critique issues and hard-fail causes. Do **not** rewrite the whole
  email into a generic template.
- **Keep** the segment angle (technical founder vs sales leader vs trust-heavy, etc.),
  subject_line_hint, and voice of the first draft unless a cited issue requires a change.
- Do not replace specific angle with bland "any founder" wording.
- If the issue is evidence safety, remove the unsupported line; replace with one of the
  safe patterns above, not with over-generic filler.
"""


QUESTION_RULES = {
    "yesno": (
        "1. End with a simple yes/no question the reader can answer in 5 seconds.\n"
        "2. The question should trigger a quick 'yes, actually...' reaction.\n"
        "3. Keep it under 15 words.\n"
        "4. NEVER use 'How do you currently...' as a question opener.\n"
        "5. Do NOT ask generic questions like 'what\\'s your biggest challenge'."
    ),
    "open": (
        "1. End with one bold, specific question that makes the reader pause.\n"
        "2. The question must be under 15 words.\n"
        "3. The question must be hard to answer with a simple yes or no.\n"
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
    return _DRAFTING_SYSTEM_TEMPLATE.format(
        question_rules=rules,
        product_context=profile["product_context"],
        product_name=profile["product_name"],
        sign_off=profile["sign_off"],
    )


def _build_pb_system_prompt(profile: dict) -> str:
    mode = profile.get("email_mode", "problem_validation_email")
    if mode == "sales_pitch_email":
        return _DRAFTING_SALES_PITCH_TEMPLATE.format(
            product_context=profile["product_context"],
            sign_off=build_pb_sign_off(),
        )
    if mode == "trace_strategy_email":
        return build_trace_strategy_system_prompt(
            product_context=profile["product_context"],
            sign_off=build_pb_sign_off(),
        )
    if mode == "anti_ai_email":
        return _DRAFTING_ANTI_AI_TEMPLATE.format(
            product_context=profile["product_context"],
            email_mode=mode,
            sign_off=build_pb_sign_off(),
        )
    return _DRAFTING_PB_TEMPLATE.format(
        product_context=profile["product_context"],
        email_mode=mode,
        sign_off=build_pb_sign_off(),
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
    kind = profile.get("profile_kind", "legacy")

    if kind == "problem_validation":
        system_prompt = _build_pb_system_prompt(profile)
        mode = profile.get("email_mode", "problem_validation_email")
        anti_ai_mode = mode == "anti_ai_email"
        strategy_mode = mode == "trace_strategy_email"
        if derived is None:
            derived = derive_campaign_fields(lead, test_batch)

        ctx = format_apollo_context_block(lead)
        signal_blk = format_signal_evidence_block(lead)
        if signal_blk:
            ctx = f"{ctx}\n\n{signal_blk}"
        subject_line_note = (
            "subject_line_hint (context only; do not copy if it sounds like a "
            "marketing category)"
            if (anti_ai_mode or strategy_mode)
            else "subject_line_hint (REQUIRED subject line — use this exact text)"
        )
        angle_note = (
            f"DERIVED (for tone only; do not assert as fact about the prospect):\n"
            f"- segment: {derived['segment']}\n"
            f"- {subject_line_note}: {derived['subject_line_hint']}\n"
            f"- email_angle: {derived['email_angle']}\n"
            f"- buyer_angle: {derived['buyer_angle']}\n"
            f"- likely_objection_context (internal, do not quote verbatim if it "
            f"sounds like fake personalization): {derived['likely_objection_context']}\n"
            f"- research_basis: {derived['research_basis']}\n"
        )
        if strategy_mode:
            copy_blk = (
                build_trace_strategy_sender_block()
                + "\n\n"
                + "=== CAMPAIGN NOTES ===\n"
                + f"- email_mode: {mode}\n"
                + f"- test_batch: {test_batch or '(none)'}\n"
                + f"- counted-body hard limit: {TRACE_STRATEGY_WORD_WARN_HI} words "
                + "(greeting + two-line sign-off excluded)\n"
                + "=== end campaign notes ===\n"
            )
        elif anti_ai_mode:
            copy_blk = _anti_ai_copy_constraints_block(lead, derived, test_batch)
        else:
            copy_blk = _pb_copy_constraints_block(lead, derived, test_batch)
        base_message = f"{ctx}\n\n{angle_note}\n{copy_blk}\n"
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
        company = lead.get("company", "")
        name = lead.get("name", "")
        angle_key = "senior" if _is_senior_title(title) else "early"
        angle = profile["angles"][angle_key]
        signal_blk = format_signal_evidence_block(lead)
        extra = f"\n\n{signal_blk}" if signal_blk else ""
        base_message = (
            f"Prospect: {name}\n"
            f"Title: {title}\n"
            f"Company: {company}{extra}\n\n"
            f"{angle}"
        )
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
        kind == "problem_validation"
        and profile.get("email_mode") == "trace_strategy_email"
    ) else 768

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    raw = _strip_json_fences(response.content[0].text)
    email = json.loads(raw)

    if (
        kind == "problem_validation"
        and profile.get("email_mode") == "trace_strategy_email"
    ):
        return normalize_trace_strategy_draft(email)

    if "subject" not in email or "body" not in email:
        raise ValueError("Claude response missing 'subject' or 'body' key")

    return email


# ─── Email Critique (legacy) ───────────────────────────────────────────────

_CRITIQUE_SYSTEM_TEMPLATE = """\
You are a brutal cold-email reviewer for first-touch B2B discovery outreach.
Apply the rubric strictly. Default to lower scores. If unsure, deduct.

This email is being sent to promote {product_name}. The body must NOT
mention {product_name} or describe what it does. The sign-off line
"{sign_off}" is the ONLY place {product_name} may appear, and the em
dash inside that sign-off line is allowed. Em dashes anywhere else
(subject or body) are a hard fail.

# HARD FAILS (each one independently is a hard fail)
- Banned hollow words appear in subject or body: "impressed", "inspiring",
  "admire", "fascinating", "excited", "thrilled", "remarkable",
  "incredible", "love", "noticed".
- Em dashes or dashes anywhere except inside the sign-off line.
- Any sentence in the body starts with a verb (e.g. "Noticed", "Saw",
  "Hope", "Wanted").
- Body mentions {product_name} or describes what it does or how it works.
- Body requests a meeting, call, demo, time on calendar, or "15 minutes".
- Body is not exactly 2 declarative/observational sentences plus 1
  closing question.
- Body word count exceeds 40 words (counted excluding the sign-off line).
- The closing question opens with "How do you currently".
- The sign-off line is missing or altered from "{sign_off}".
- Subject line exceeds 6 words.
- Prospect's first name appears more than once in subject + body
  combined (the greeting is the only place it should appear).
- The email invents specific facts about the prospect or their company
  that could not be derived just from their title and company name
  (fake quotes, fake news, fake achievements, fake metrics).

# SOFT RUBRIC (each 0-25, total 0-100)
- personalization: how naturally the email speaks to this specific
  prospect's likely reality vs. swap-the-name boilerplate. 25 = clearly
  written for them. 0 = generic.
- question_quality: is the closing question sharp, specific, easy to
  answer in seconds, and provoking a quick reaction? Generic questions
  like "what's your biggest challenge" score near 0.
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


_CRITIQUE_PB_TEMPLATE = """\
You review a *problem-validation* cold email (founder-led discovery).

Prospect facts available to the sender are ONLY what appears in the user
message FACTS block. The email may briefly mention building a lightweight tool.

# SIGN-OFF — NEVER a hard fail for whitespace or exact name spelling alone
The body should end with two lines: a first name, then either "building Helix" or
"Helix by Wiserbond" (minor variations OK). Accept Jamie, Hyunmyung, or other
reasonable first names. Ignore extra blank lines and trivial spacing.
**Do NOT output any hard_fail that only complains about sign-off formatting,
exact line breaks, or benign name choice.** Missing a closing entirely is still a fail.

# PRIORITY (when scoring)
1. Problem relevance
2. Non-aggressive tone
3. Segment fit (soft — no accusations)
4. Reply likelihood
5. Clarity
6. Evidence safety (no invented company facts)
7. Feature density — penalize if it reads like a product pitch instead of problem discovery

# SCORE CALIBRATION — be strict at the top; 80–89 is still useful for humans
- **95–100**: exceptional — subject, body, question, segment fit, evidence safety
  all clearly strong; ready to auto-send without hesitation.
- **90–94**: strong — appropriate for pass / auto-send band.
- **80–89**: **manual review queue** — not a failure; decent for problem-validation
  experiments if issues are minor. Score honestly; do not force 90+ for adequate drafts.
- **Below 80**: weak / should block unless only hard-fail fixes would rescue it.
Most good-but-not-perfect drafts should land **80–89**. Reserve **95+** for rare excellence.
When scoring 80–89, list 1–2 concrete improvements in issues — do not recommend
rewriting into a blander generic email; preserve segment fit.

# HARD FAILS (any one = hard fail) — NOT sign-off trivia
- Invents facts not supported by the FACTS block (metrics, news, funding details, etc.).
- Claims or implies the prospect has a specific internal problem without evidence
  (e.g. they blank, forget, lose deals, repeat mistakes, team is bad at objections).
- Overly negative framing about the reader or their org.
- Fake personalization (over-specific claims from thin air).
- Mostly product/feature explanation with no genuine problem-discovery question.
- feature_density HARD FAIL: body lists **three or more** distinct concrete product
  capabilities/behaviors (e.g. preload AND in-call click AND post-call AI refine all
  enumerated as separate pitches) *when* the email no longer reads like discovery; OR
  the closing question is missing / not about whether they relate to the problem.
**Do NOT put body length / 75-word limits in hard_fails.** Length is enforced by
deterministic code (greeting + sign-off excluded). You may mention length only in
`issues` as optional feedback.
- Subject longer than 7 words.
- No signature / no closing lines at all at the end of the body.

# SOFT SCORES (each sub-score as allocated below; integers; sum = total 0–100)
Assign:
- problem_relevance (0–20)
- evidence_safety (0–20)
- non_aggressive_tone (0–15)
- segment_fit (0–12)
- reply_likelihood (0–12)
- clarity (0–11)
- feature_density (0–10) — HIGH = appropriately light product mention, LOW = pitch-heavy

"total" MUST equal the sum of these seven.

# OUTPUT FORMAT — raw JSON only.
{{
  "hard_fails": [],
  "soft_scores": {{
    "problem_relevance": 0,
    "evidence_safety": 0,
    "non_aggressive_tone": 0,
    "segment_fit": 0,
    "reply_likelihood": 0,
    "clarity": 0,
    "feature_density": 0
  }},
  "total": 0,
  "issues": []
}}
"""


_CRITIQUE_TRACE_STRATEGY_TEMPLATE = """\
You review a Trace *strategy* cold email (value-exchange first, not generic AI outbound).

Prospect facts available to the sender are ONLY what appears in the FACTS block.
Derived segment context is for tone only — not proven internal problems.

# WHAT GOOD LOOKS LIKE
- Clear why this recipient, why now, why this sender
- One concrete recipient benefit
- One clear low-friction ask
- Competency shown via execution evidence, not adjectives
- Transparent sender motive without making the email about the sender
- No fabricated research, product usage, customers, or metrics

# SIGN-OFF — NEVER a hard fail for whitespace or exact name spelling alone
Body should end with two lines: a first name, then "building Helix" or
"Helix by Wiserbond" (minor variations OK). Missing a closing entirely is a fail.

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

# SCORE CALIBRATION
- **95–100**: exceptional value exchange; ready to auto-send.
- **90–94**: strong; pass / auto-send band.
- **80–89**: manual review queue — useful but imperfect.
- **Below 80**: block.
Most good-but-not-perfect drafts should land **80–89**.

# SOFT SCORES (integers; sum = total 0–100)
- problem_relevance (0–20): trigger + hypothesis fit this recipient
- evidence_safety (0–20): no invented facts; calibrated language
- non_aggressive_tone (0–15): calm, peer, non-accusatory
- segment_fit (0–12): founder vs sales-leader angle makes sense
- reply_likelihood (0–12): easy to answer; clear reason to reply
- clarity (0–11): why you / why me / why now / what ask
- feature_density (0–10): HIGH = light product mention, LOW = feature tour

"total" MUST equal the sum of these seven.

# OUTPUT FORMAT — raw JSON only.
{{
  "hard_fails": [],
  "soft_scores": {{
    "problem_relevance": 0,
    "evidence_safety": 0,
    "non_aggressive_tone": 0,
    "segment_fit": 0,
    "reply_likelihood": 0,
    "clarity": 0,
    "feature_density": 0
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

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    kind = profile.get("profile_kind", "legacy")

    if kind == "problem_validation":
        if profile.get("email_mode") == "trace_strategy_email":
            system_prompt = _CRITIQUE_TRACE_STRATEGY_TEMPLATE
        else:
            system_prompt = _CRITIQUE_PB_TEMPLATE
        ctx = format_apollo_context_block(lead)
        d_blob = ""
        if derived:
            d_blob = (
                f"\nDerived segment: {derived.get('segment')}\n"
                f"segment_reason: {derived.get('segment_reason')}\n"
                f"Expected subject hint: {derived.get('subject_line_hint')}\n"
            )
        user_message = (
            f"{ctx}{d_blob}\n\n"
            f"DRAFT TO REVIEW:\nSubject: {email.get('subject', '')}\n"
            f"Body:\n{email.get('body', '')}\n"
        )
    else:
        system_prompt = _CRITIQUE_SYSTEM_TEMPLATE.format(
            product_name=profile["product_name"],
            sign_off=profile["sign_off"],
        )
        user_message = (
            f"Prospect: {lead.get('name', '')}\n"
            f"Title: {lead.get('title', '')}\n"
            f"Company: {lead.get('company', '')}\n\n"
            f"DRAFT TO REVIEW:\n"
            f"Subject: {email.get('subject', '')}\n"
            f"Body:\n{email.get('body', '')}"
        )

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=768,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    raw = _strip_json_fences(response.content[0].text)
    critique = json.loads(raw)

    hard_fails = critique.get("hard_fails") or []
    soft = critique.get("soft_scores") or {}
    issues = critique.get("issues") or []

    total = critique.get("total")
    if not isinstance(total, int):
        if kind == "problem_validation":
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
    if kind == "problem_validation":
        merged, meta = merge_pb_hard_fails_with_local_length(
            email.get("body") or "",
            lead.get("first_name") or "",
            out["hard_fails"],
            warn_hi=_pb_word_warn_hi(profile),
        )
        out["hard_fails"] = merged
        out["length_analysis"] = meta
    return out


PASS_THRESHOLD = 80
# Problem-validation: auto-pass / auto-send band; 80–89 = manual REVIEW queue
PASS_THRESHOLD_PB = 90
REVIEW_THRESHOLD_PB_MIN = 80
REVISE_THRESHOLD = 60
MAX_REVISE_ATTEMPTS = 1


def _decide_verdict(
    critique: dict,
    attempts_used: int,
    profile: dict,
) -> str:
    kind = profile.get("profile_kind", "legacy")
    hard = critique.get("hard_fails") or []
    total = critique.get("total", 0)

    if kind == "problem_validation":
        if hard:
            if attempts_used >= MAX_REVISE_ATTEMPTS:
                return "block"
            return "revise"
        if total >= PASS_THRESHOLD_PB:
            return "pass"
        if total >= REVIEW_THRESHOLD_PB_MIN:
            return "review"
        return "block"

    pt = PASS_THRESHOLD
    rt = REVISE_THRESHOLD
    if not hard and total >= pt:
        return "pass"
    if attempts_used >= MAX_REVISE_ATTEMPTS:
        return "block"
    if hard or total >= rt:
        return "revise"
    return "block"


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
        or profile.get("profile_kind") != "problem_validation"
        or verdict not in ("pass", "review")
    ):
        return email, critique, verdict, meta

    sig = build_pb_sign_off()
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
    "problem_relevance",
    "evidence_safety",
    "non_aggressive_tone",
    "segment_fit",
    "reply_likelihood",
    "clarity",
    "feature_density",
)

LEGACY_SOFT_KEYS = ("personalization", "question_quality", "voice", "hook")


def _format_critique_log(critique: dict, profile: dict) -> list[str]:
    soft = critique.get("soft_scores") or {}
    keys = PB_SOFT_KEYS if profile.get("profile_kind") == "problem_validation" else LEGACY_SOFT_KEYS
    soft_str = ", ".join(f"{k}={soft.get(k, 0)}" for k in keys)
    lines = [f"  total={critique.get('total', 0)}/100  ({soft_str})"]
    if profile.get("profile_kind") == "problem_validation":
        la = critique.get("length_analysis") or {}
        if la:
            lines.append(
                f"  deterministic words (no greeting/sign-off): "
                f"{la.get('body_word_count')} ({la.get('length_status')})"
            )
    for h in critique.get("hard_fails") or []:
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
        help="Lead list: akashic | problem_validation | helix (alias for problem_validation).",
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
            "Research only through step 6: Grok scan + qualification + review queue. "
            "You check LinkedIn. No Apollo, no email, no send. Alias: --research-only."
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
        help="With --review-candidates: prompt APPROVED/REJECTED for each PENDING row.",
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
            "After you APPROVED people and attached emails: draft with the existing "
            "Trace engine (steps 9-10). Without --send, you still decide whether to send."
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
    if profile.get("profile_kind") == "problem_validation":
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

    if profile.get("profile_kind") == "problem_validation":
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
            if profile.get("profile_kind") == "problem_validation":
                print(
                    f"  → PASS (≥ {PASS_THRESHOLD_PB}, no hard fails — "
                    f"auto-send only with --send)."
                )
            else:
                print(f"  → PASS (>= {PASS_THRESHOLD} and no hard fails).")
            break
        if verdict == "review":
            print(
                f"  → REVIEW (score {total_s} in {REVIEW_THRESHOLD_PB_MIN}–"
                f"{PASS_THRESHOLD_PB - 1}, no hard fails). "
                f"Manual approval; not auto-sent even with --send."
            )
            break
        if verdict == "block":
            hf = critique.get("hard_fails") or []
            if profile.get("profile_kind") == "problem_validation":
                if attempts_used >= MAX_REVISE_ATTEMPTS and hf:
                    reason = "hard fail unresolved after revision"
                elif total_s < REVIEW_THRESHOLD_PB_MIN:
                    reason = f"score {total_s} below {REVIEW_THRESHOLD_PB_MIN} (block)"
                else:
                    reason = "blocked"
            else:
                if attempts_used >= MAX_REVISE_ATTEMPTS and hf:
                    reason = "hard fail unresolved after revision"
                elif attempts_used >= MAX_REVISE_ATTEMPTS:
                    reason = f"score below threshold (< {PASS_THRESHOLD})"
                else:
                    reason = f"score below revise floor (< {REVISE_THRESHOLD})"
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
        profile.get("profile_kind") == "problem_validation"
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
    print("\n--- Step 6 (you): LinkedIn check. Research path stops here. ---")
    print(f"  python main.py --review-candidates --candidates-file {path}")
    print("\n--- Email path (only after you confirm people) ---")
    print("  7. Approve / reject (AI recommendation is kept):")
    print(
        f"     python main.py --set-human-status APPROVED "
        f"--candidate-id sig_... --candidates-file {path}"
    )
    print(
        f"     python main.py --set-human-status REJECTED --reject-reason vendor "
        f"--candidate-id sig_... --candidates-file {path}"
    )
    print("  8. Apollo CSV (manual) then import emails:")
    print(f"     python main.py --export-approved --candidates-file {path}")
    print(
        f"     python main.py --import-enriched apollo.csv --candidates-file {path}"
    )
    print("  9-10. Draft emails. Add --send only when you actually want PASS drafts sent:")
    print(f"     python main.py --process-approved --candidates-file {path}")
    print(f"     python main.py --process-approved --candidates-file {path} --send")


def _run_interactive_review(path: str) -> None:
    rows = load_candidates(path)
    pending = [r for r in rows if r.get("human_status") == "PENDING"]
    if not pending:
        print("No PENDING candidates.")
        return
    for rec in pending:
        print(format_review_card(rec))
        raw = input("Approve / Reject / Skip [a/r/s]: ").strip().lower()
        if raw in ("a", "approve", "approved"):
            apply_human_decision(rows, rec["candidate_id"], "APPROVED")
        elif raw in ("r", "reject", "rejected"):
            reason = input(
                "Optional reason (vendor/wrong_role/not_real_pain/"
                "wrong_company/other/blank): "
            ).strip().lower() or None
            apply_human_decision(rows, rec["candidate_id"], "REJECTED", reason)
        else:
            print("Skipped.")
    save_candidates(path, rows)
    print(f"Saved decisions → {path}")


def main():
    args = _parse_args()
    if args.track_replies:
        if not args.replies_input or not os.path.isfile(args.replies_input):
            print("[FAIL] --track-replies requires an existing --replies-input JSONL path.")
            sys.exit(2)
        from reply_tracker import run_cli

        run_cli(args.replies_input, args.replies_output, int(args.since_days))
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
        print("    Stops so you can check LinkedIn. No enrichment. No email. No send.")
        print(f"    Candidates: {jsonl_path}\n")
        cost_path = os.path.join(
            args.output_dir,
            f"research_cost_{stamp}_{list_name}_{safe_batch}.jsonl",
        )
        print(f"    Cost log: {cost_path}")
        cache_path = os.path.join(args.output_dir, "research_cache.jsonl")
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
            print("Approved. Export for Apollo, then --import-enriched, then --process-approved.")
        else:
            print("Rejected. No enrichment or outbound.")
        return

    if args.review_candidates:
        path = _require_candidates_file(args.candidates_file)
        if args.interactive_review:
            _run_interactive_review(path)
            return
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
        rows = load_candidates(path)
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
        print("    Gate 2 unchanged: without --send, drafts are JSONL only.")
        print(f"    JSONL: {jsonl_path}\n")
        counts: dict[str, int] = {}
        for i, rec in enumerate(ready, 1):
            list_name, profile = _profile_for_candidate(rec)
            lead = candidate_to_lead(rec)
            result = _process_lead(
                i,
                len(ready),
                lead,
                profile,
                args.question_style,
                send_ok=args.send,
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
        "ON — auto-send PASS ≥90 only (REVIEW 80–89 never sent)"
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
