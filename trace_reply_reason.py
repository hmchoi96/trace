"""Deterministic reply-reason gate.

Decides whether a first touch should be drafted. Prompt text does not override
this module.

# ponytail: phrase scan on the research package. Upgrade when discovery stores
# structured trigger fields and stops relying on prose.
"""

from __future__ import annotations

import re
from typing import Any, Literal

ReplyReasonLevel = Literal["active", "credible", "topic_only", "none"]
TriggerType = Literal[
    "action_trigger",
    "behavior_trigger",
    "relationship_trigger",
    "topic_trigger",
    "none",
]
DraftDecision = Literal[
    "send_now",
    "research_more",
    "strengthen_offer",
    "change_recipient",
    "use_different_channel",
    "no_draft",
]
OutreachMotion = Literal[
    "cold_product",
    "direct_application",
    "warm_intro",
    "existing_relationship",
    "expert_research",
    "connector",
]

MOTIONS = (
    "cold_product",
    "direct_application",
    "warm_intro",
    "existing_relationship",
    "expert_research",
    "connector",
)
TRIGGERS = (
    "action_trigger",
    "behavior_trigger",
    "relationship_trigger",
    "topic_trigger",
    "none",
)
DECISIONS = (
    "send_now",
    "research_more",
    "strengthen_offer",
    "change_recipient",
    "use_different_channel",
    "no_draft",
)
ASSET_STATUSES = ("verified", "planned", "unavailable")

HELD_DECISIONS = tuple(d for d in DECISIONS if d != "send_now")

REPLY_REASON_HARD_FAILS = (
    "topic_signal_used_as_action_trigger",
    "no_verified_workflow_owner",
    "question_without_reply_reason",
    "meeting_before_value",
    "sender_asset_not_verified",
    "motion_structure_mismatch",
    "draft_generated_despite_no_draft",
)

_ALIGNMENTS = ("direct", "adjacent", "unrelated", "unknown")

# ponytail: token overlap against the active profile's problem, personas,
# and assets. Replace when discovery stores trigger_offer_alignment itself.
_GENERIC_TERMS = frozenset(
    """
    a an the and or of to for in on with from by at as is are was were be been
    we our they their you your this that it its not no do does did into over
    under new old team company people person time work product market deal
    deals private investment professional history similar current previous
    later when what which who how than then just still also more most other
    about after before again same first their them will can has have had
    decision decisions search tool tools
    """.split()
)
_SEEKING_PHRASES = (
    "looking for a solution",
    "looking for a tool",
    "looking for a system",
    "looking for vendors",
    "looking for a decision",
    "evaluating vendors",
    "evaluating tools",
    "evaluating a solution",
    "need a solution",
    "asked for help",
    "asked for a recommendation",
    "need help with",
)
_PROBLEM_NOW = (
    "struggle",
    "struggling",
    "hard to",
    "can't find",
    "cannot find",
    "right now",
    "currently",
    "comparing",
    "reopen",
    "reopening",
)
_HIRE_STEMS = {"hiring": "hire", "hired": "hire", "hires": "hire"}
_EVENT_STEMS = {
    "hiring": frozenset({"hire"}),
    "acquisition": frozenset({"acquisition", "acquire", "acquired"}),
    "fundraising": frozenset({"fundraising", "raised", "series"}),
    "implementing": frozenset({"implementing", "implement"}),
}

_ACTION_PHRASES = (
    "is hiring",
    "are hiring",
    "now hiring",
    "actively hiring",
    "open role",
    "hiring for",
    "hiring our",
    "hiring a ",
    "hiring the ",
    "i applied",
    "applied today",
    "signup error",
    "sign-up error",
    "asked me to follow up",
    "asked us to follow up",
    "looking for vendors",
    "evaluating vendors",
    "evaluating tools",
    "implementing a system",
    "procurement",
)
_BEHAVIOR_PHRASES = (
    "workaround",
    "reopening",
    "reopens",
    "reopen prior",
    "every deal",
    "every ic",
    "every week",
    "recurring",
    "repeatedly",
    "manual comparison",
    "handoff",
    "by hand",
    "owns the workflow",
    "performs the workflow",
    "day-to-day",
    "day to day",
)
_WARM_PHRASES = (
    "intro via",
    "introduced me",
    "introduced us",
    "suggested i contact",
    "suggested the connection",
    "warm intro",
    "warm introduction",
)
_EXISTING_PHRASES = (
    "prior conversation",
    "previously gave feedback",
    "we spoke",
    "existing relationship",
    "follow-up is expected",
)
_LINKEDIN_ONLY = ("linkedin connection", "connected on linkedin")
_ASSET_CLAIM = (
    "teardown",
    "replay",
    "one-page",
    "one page",
    "prototype",
    "i can send the",
    "i prepared a",
    "i built a sample",
)
_MEETING = ("meeting", "jump on a call", "15 minutes", "15-minute", "calendar", "hop on a call")
_PERSONAL_PAIN = (
    "you struggle",
    "you're struggling",
    "you are struggling",
    "you probably struggle",
    "your pain",
    "you personally",
    "your day-to-day",
    "in your day-to-day",
)

MOTION_STRUCTURES = {
    "direct_application": (
        "Structure: live opportunity, then directly relevant proof, then the "
        "exact role or process next step. Name the open role and one proof "
        "that matches it."
    ),
    "warm_intro": (
        "Structure: verified introducer, then the specific overlap, then one "
        "narrow conversation ask. Do not widen the ask past the introduction."
    ),
    "existing_relationship": (
        "Structure: the existing context, then why this follow-up is timely, "
        "then one proportionate ask."
    ),
    "cold_product": (
        "Structure: verified workflow or action, then a concrete asset already "
        "prepared, then offer that asset before any meeting, then a conditional "
        "next step. Never state that an asset exists unless Verified sender "
        "asset names it. Generic benefits are not enough."
    ),
    "expert_research": (
        "Structure: specific observed evidence, then one narrow interpretation, "
        "then one distinction-checking question. Do not assert that the "
        "recipient personally feels the workflow pain. Do not add a product "
        "pitch unless needed to explain why you are asking."
    ),
    "connector": (
        "Ask who owns the workflow or whether one introduction makes sense. "
        "Do not disguise a product pitch as a connector question."
    ),
}


class DraftBlocked(Exception):
    """Raised when drafting is attempted after a non-send decision."""

    def __init__(self, assessment: dict[str, Any]) -> None:
        super().__init__(assessment.get("reason") or assessment.get("draft_decision") or "no_draft")
        self.assessment = assessment


def _text(value: Any) -> str:
    return str(value or "").strip()


def _blob(rec: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "signal_text",
        "why_relevant",
        "recommendation_reason",
        "latent_behavior",
        "active_occasion",
        "relationship_path",
        "overlap",
        "title",
        "relevant_proof",
        "next_action",
    ):
        parts.append(_text(rec.get(key)))
    for ev in rec.get("supporting_evidence") or []:
        if isinstance(ev, dict):
            parts.append(_text(ev.get("quote_or_paraphrase")))
    for sig in rec.get("additional_signals") or rec.get("signals") or []:
        if isinstance(sig, dict):
            parts.append(_text(sig.get("signal_text") or sig.get("text")))
    return " ".join(p for p in parts if p).lower()


def _has_any(blob: str, phrases: tuple[str, ...]) -> bool:
    return any(p in blob for p in phrases)


def _role(rec: dict[str, Any]) -> str:
    role = _text(rec.get("outreach_role"))
    if role:
        return role
    from trace_drafting import classify_outreach_role

    return classify_outreach_role(rec)


def sender_assets(profile: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Profiles without sender_assets load as an empty inventory."""
    out: list[dict[str, Any]] = []
    for item in (profile or {}).get("sender_assets") or []:
        if not isinstance(item, dict):
            continue
        status = _text(item.get("status")).lower()
        if status not in ASSET_STATUSES:
            status = "unavailable"
        motions = item.get("applicable_motions") or []
        out.append(
            {
                "id": _text(item.get("id")),
                "name": _text(item.get("name") or item.get("id")),
                "status": status,
                "description": _text(item.get("description")),
                "applicable_motions": [str(m) for m in motions if str(m).strip()],
                "proof": _text(item.get("proof")),
            }
        )
    return out


def _pick_asset(
    rec: dict[str, Any],
    profile: dict[str, Any] | None,
    motion: str,
) -> dict[str, Any] | None:
    assets = sender_assets(profile)
    wanted = _text(rec.get("sender_asset") or rec.get("sender_asset_id"))
    if wanted:
        for asset in assets:
            if asset["id"] == wanted or asset["name"].lower() == wanted.lower():
                return asset
        status = _text(rec.get("sender_asset_status")).lower()
        if status not in ASSET_STATUSES:
            status = "unavailable"
        return {
            "id": wanted,
            "name": wanted,
            "status": status,
            "description": "",
            "applicable_motions": [],
            "proof": "",
        }
    status_only = _text(rec.get("sender_asset_status")).lower()
    if status_only in ASSET_STATUSES:
        return {
            "id": "",
            "name": _text(rec.get("sender_asset_name")),
            "status": status_only,
            "description": "",
            "applicable_motions": [],
            "proof": "",
        }

    def _fits(asset: dict[str, Any]) -> bool:
        motions = asset.get("applicable_motions") or []
        return not motions or motion in motions

    for status in ("verified", "planned"):
        for asset in assets:
            if asset["status"] == status and _fits(asset):
                return asset
    return None


def infer_motion(rec: dict[str, Any], blob: str, role: str) -> str:
    explicit = _text(rec.get("outreach_motion"))
    if explicit in MOTIONS:
        return explicit
    # A company hiring is an action trigger for cold product, not an application.
    # direct_application is only when the sender is the one applying.
    if rec.get("role_open") is True or _has_any(
        blob, ("i applied", "applied today", "my application", "i am applying", "i'm applying")
    ):
        return "direct_application"
    if rec.get("relationship_path") or _has_any(blob, _WARM_PHRASES):
        return "warm_intro"
    if _has_any(blob, _EXISTING_PHRASES):
        return "existing_relationship"
    if role == "Expert / Researcher":
        return "expert_research"
    if role == "Connector":
        return "connector"
    return "cold_product"


def _bits(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _profile_text(profile: dict[str, Any] | None, *keys: str) -> str:
    profile = profile or {}
    disc = profile.get("discovery") or {}
    parts: list[str] = []
    for key in keys:
        parts.extend(_bits(profile.get(key)))
        parts.extend(_bits(disc.get(key)))
    return " ".join(parts)


def _asset_text(profile: dict[str, Any] | None) -> str:
    parts: list[str] = []
    for asset in sender_assets(profile):
        parts.extend(_bits(asset.get("name")))
        parts.extend(_bits(asset.get("description")))
    return " ".join(parts)


def _terms(text: str) -> set[str]:
    found = set(re.findall(r"[a-z0-9][a-z0-9'+-]{2,}", (text or "").lower()))
    out: set[str] = set()
    for tok in found:
        tok = _HIRE_STEMS.get(tok, tok)
        if tok in _GENERIC_TERMS:
            continue
        if tok.endswith("s") and not tok.endswith("ss") and len(tok) > 3 and tok[:-1] not in _GENERIC_TERMS:
            tok = tok[:-1]
        if len(tok) < 3 or tok in _GENERIC_TERMS:
            continue
        out.add(tok)
    return out


def _company_event(blob: str) -> str:
    if _has_any(
        blob,
        (
            "is hiring",
            "are hiring",
            "hiring a ",
            "hiring our",
            "hiring the ",
            "now hiring",
            "actively hiring",
            "open role",
        ),
    ):
        return "hiring"
    if _has_any(blob, ("acquisition", "acquired", "acquires")):
        return "acquisition"
    if _has_any(blob, ("fundraising", "raised $", "series a", "series b", "closed a round")):
        return "fundraising"
    if "implementing " in blob and not _has_any(blob, _SEEKING_PHRASES):
        return "implementing"
    return ""


def infer_alignment(
    rec: dict[str, Any],
    blob: str,
    profile: dict[str, Any] | None,
) -> str:
    """Compare the signal to the active profile. The product name is not an input."""
    explicit = _text(rec.get("trigger_offer_alignment")).lower()
    if explicit in _ALIGNMENTS:
        return explicit
    problem_terms = _terms(
        _profile_text(
            profile,
            "problem_definition",
            "problems_it_solves",
            "what_it_does",
            "offer",
        )
    )
    workflow_terms = _terms(
        _profile_text(profile, "target_workflow", "examples_of_problem_signals")
    )
    persona_terms = _terms(_profile_text(profile, "target_personas", "target_users_or_buyers"))
    context_terms = _terms(_profile_text(profile, "product_context"))
    asset_terms = _terms(_asset_text(profile))
    offer_terms = problem_terms | workflow_terms | context_terms | asset_terms
    if not offer_terms and not persona_terms:
        return "unknown"
    signal_terms = _terms(blob)
    shared_offer = signal_terms & offer_terms
    shared_problem = signal_terms & problem_terms
    near_terms = persona_terms | context_terms | asset_terms
    seeking = _has_any(blob, _SEEKING_PHRASES)
    problem_now = _has_any(blob, _PROBLEM_NOW)
    event = _company_event(blob)
    if event:
        stems = _EVENT_STEMS.get(event, frozenset())
        if stems & problem_terms and (shared_problem - stems):
            return "direct"
        if (seeking or problem_now) and shared_offer:
            return "direct"
        # Hiring can sit next to the persona. Other company events do not.
        if event == "hiring" and (signal_terms & near_terms):
            return "adjacent"
        return "unrelated"
    if (seeking or problem_now) and shared_offer:
        return "direct"
    if len(signal_terms & (problem_terms | workflow_terms | asset_terms)) >= 2:
        return "direct"
    if shared_offer or (signal_terms & persona_terms):
        return "adjacent"
    return "unknown"


def infer_trigger(rec: dict[str, Any], blob: str, motion: str, alignment: str = "unknown") -> str:
    explicit = _text(rec.get("trigger_type"))
    if explicit in TRIGGERS:
        return explicit
    relationship = bool(_text(rec.get("relationship_path"))) or _has_any(
        blob, _WARM_PHRASES + _EXISTING_PHRASES
    )
    if relationship and _has_any(blob, _LINKEDIN_ONLY) and not _has_any(
        blob, _WARM_PHRASES + _EXISTING_PHRASES
    ) and not _text(rec.get("relationship_path")):
        relationship = False
    action = rec.get("role_open") is True or _has_any(blob, _ACTION_PHRASES)
    if alignment == "direct" and _has_any(blob, _SEEKING_PHRASES):
        action = True
    behavior = bool(rec.get("workflow_ownership_evidence")) or _has_any(blob, _BEHAVIOR_PHRASES)
    if alignment == "direct" and _has_any(blob, _PROBLEM_NOW) and not behavior:
        action = True
    if motion in ("warm_intro", "existing_relationship") and relationship:
        return "relationship_trigger"
    if action:
        return "action_trigger"
    if behavior:
        return "behavior_trigger"
    if relationship:
        return "relationship_trigger"
    if blob.strip() or _text(rec.get("signal_text")):
        return "topic_trigger"
    return "none"


def _level(trigger: str) -> str:
    if trigger == "none":
        return "none"
    if trigger == "topic_trigger":
        return "topic_only"
    if trigger == "behavior_trigger":
        return "credible"
    return "active"


def _ownership(rec: dict[str, Any], blob: str, trigger: str) -> list[str]:
    raw = rec.get("workflow_ownership_evidence") or []
    lines = [str(x).strip() for x in raw if str(x).strip()]
    if lines:
        return lines
    if trigger == "behavior_trigger":
        return ["verified workflow behavior in the research package"]
    if "owns the workflow" in blob or "performs the workflow" in blob:
        return ["research package says they own or perform the workflow"]
    return []


def _proof(rec: dict[str, Any]) -> str:
    return _text(rec.get("relevant_proof") or rec.get("competency_proof"))


def _firsthand(rec: dict[str, Any], blob: str) -> bool:
    if "firsthand_evidence" in rec:
        return bool(rec.get("firsthand_evidence"))
    return any(p in blob for p in ("you observed", "cases you studied", "interviewed", "firsthand"))


def _novel(rec: dict[str, Any]) -> bool:
    if "novel_question" in rec:
        return bool(rec.get("novel_question"))
    return False


def _overlap(rec: dict[str, Any], blob: str) -> str:
    explicit = _text(rec.get("overlap"))
    if explicit:
        return explicit
    if "overlap" in blob:
        return "overlap named in the research package"
    return ""


def _offer_block(alignment: str) -> tuple[str, str, list[str]] | None:
    """Cold product cannot send on a company action that is not this offer."""
    if alignment == "direct":
        return None
    if alignment == "unrelated":
        return (
            "no_draft",
            "The company action is not related to this product's problem.",
            ["trigger_offer_alignment"],
        )
    return (
        "research_more",
        "The trigger is not directly tied to this product's offer.",
        ["trigger_offer_alignment"],
    )


def _decide(
    rec: dict[str, Any],
    *,
    motion: str,
    trigger: str,
    alignment: str,
    asset: dict[str, Any] | None,
    blob: str,
) -> tuple[str, str, list[str]]:
    asset_status = asset["status"] if asset else ""
    verified = asset_status == "verified"
    missing: list[str] = []

    if rec.get("owns_or_influences") is False and motion in ("cold_product", "direct_application"):
        return "change_recipient", "Recipient does not own this process.", ["process_owner"]

    if motion == "direct_application":
        live = trigger == "action_trigger" or rec.get("role_open") is True
        proof = bool(_proof(rec))
        if live and proof:
            return "send_now", "Live role with directly relevant proof.", []
        if not live:
            missing.append("live_role")
        if not proof:
            missing.append("relevant_proof")
        if live and not proof:
            return "strengthen_offer", "Open role, no directly relevant proof yet.", missing
        return "research_more", "A direct application needs a live role and proof.", missing

    if motion == "warm_intro":
        named = bool(_text(rec.get("relationship_path")) or _text(rec.get("introducer"))) or _has_any(
            blob, _WARM_PHRASES
        )
        overlap = _overlap(rec, blob)
        if named and overlap:
            return "send_now", "Named introducer and a specific overlap.", []
        if not named:
            missing.append("introducer")
        if not overlap:
            missing.append("overlap")
        return "research_more", "Warm intro needs a named introducer and a specific overlap.", missing

    if motion == "existing_relationship":
        if trigger == "relationship_trigger":
            return "send_now", "Existing relationship. A proportionate follow-up is allowed.", []
        return "research_more", "No verified existing relationship.", ["relationship"]

    if motion == "expert_research":
        if _firsthand(rec, blob) and _novel(rec):
            return (
                "send_now",
                "Firsthand evidence and a narrow question.",
                [],
            )
        if not _firsthand(rec, blob):
            return (
                "use_different_channel",
                "Broad source without firsthand evidence. Prefer a public reply.",
                ["firsthand_evidence"],
            )
        return "research_more", "The question is not narrow enough to email.", ["novel_question"]

    if motion == "connector":
        if trigger == "relationship_trigger":
            return "send_now", "Credible relationship for a connector ask.", []
        return "no_draft", "Connector ask needs a credible relationship.", ["relationship"]

    # cold_product. A company action is not enough unless it matches the offer.
    if trigger == "action_trigger":
        blocked = _offer_block(alignment)
        if blocked:
            return blocked
        return "send_now", "Action is directly tied to this product's offer.", []
    if trigger == "relationship_trigger" and _overlap(rec, blob):
        return "send_now", "Warm path and a specific overlap.", []
    if trigger == "relationship_trigger":
        return "research_more", "Warm path needs a specific overlap.", ["overlap"]
    if trigger == "behavior_trigger":
        blocked = _offer_block(alignment)
        if blocked:
            return blocked
        if verified:
            return "send_now", "Verified workflow, directly tied to the offer, and a verified asset.", []
        return (
            "send_now",
            "Workflow matches the offer. Draft from the campaign offer, and do not name an asset that is not verified.",
            [],
        )
    if trigger == "topic_trigger":
        if alignment == "unrelated":
            return (
                "no_draft",
                "That company fact is not related to this product's problem.",
                ["trigger_offer_alignment"],
            )
        return (
            "research_more",
            "Topic relevance only. That is not a reason to draft a cold-product email.",
            ["action_or_ownership", "sender_asset"],
        )
    return "no_draft", "No reply reason.", ["reply_reason"]


def assess_reply_reason(
    rec: dict[str, Any] | None,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the gate result. Does not mutate rec."""
    rec = rec or {}
    blob = _blob(rec)
    role = _role(rec)
    motion = infer_motion(rec, blob, role)
    alignment = infer_alignment(rec, blob, profile)
    trigger = infer_trigger(rec, blob, motion, alignment)
    asset = _pick_asset(rec, profile, motion)
    decision, reason, missing = _decide(
        rec, motion=motion, trigger=trigger, alignment=alignment, asset=asset, blob=blob
    )
    from trace_research import apply_gap_to_decision, do_not_claim_lines, gap_status

    gap = gap_status(rec)
    if motion == "cold_product":
        decision, reason, missing = apply_gap_to_decision(gap, trigger, decision, reason, missing)
    asset_name = ""
    asset_status = ""
    gets = ""
    if asset:
        asset_status = asset["status"]
        asset_name = asset["name"]
        if asset_status == "verified":
            gets = asset["description"] or asset_name
    occasion = _text(rec.get("active_occasion"))
    if not occasion and trigger == "action_trigger":
        occasion = _text(rec.get("signal_text"))[:180]
    path = _text(rec.get("relationship_path") or rec.get("introducer"))
    nxt = _text(rec.get("next_action"))
    if decision == "send_now" and motion == "cold_product" and gap == "possible_gap":
        nxt = "Ask about the inferred gap. Do not state it as fact."
    elif decision == "send_now" and not nxt:
        nxt = {
            "direct_application": "conversation about the open role",
            "warm_intro": "narrow conversation implied by the introduction",
            "existing_relationship": "proportionate follow-up",
            "cold_product": "offer the verified asset before a meeting",
            "expert_research": "one distinction-checking question",
            "connector": "ask for the workflow owner",
        }.get(motion, "")
    return {
        "reply_reason_level": _level(trigger),
        "trigger_type": trigger,
        "trigger_offer_alignment": alignment,
        "motion": motion,
        "active_occasion": occasion,
        "relationship_path": path,
        "workflow_ownership_evidence": _ownership(rec, blob, trigger),
        "sender_asset": asset_name if asset_status == "verified" else "",
        "sender_asset_status": asset_status,
        "recipient_gets_before_meeting": gets,
        "next_action": nxt,
        "missing_evidence": missing,
        "draft_decision": decision,
        "reason": reason,
        "gap_status": gap,
        "do_not_claim": do_not_claim_lines(rec),
    }


def reply_reason_payload(assessment: dict[str, Any]) -> dict[str, Any]:
    return {
        "motion": assessment.get("motion") or "",
        "trigger_type": assessment.get("trigger_type") or "",
        "trigger_offer_alignment": assessment.get("trigger_offer_alignment") or "",
        "reply_reason_level": assessment.get("reply_reason_level") or "",
        "active_occasion": assessment.get("active_occasion") or "",
        "workflow_ownership_evidence": list(assessment.get("workflow_ownership_evidence") or []),
        "verified_sender_asset": assessment.get("sender_asset") or "",
        "recipient_gets_before_meeting": assessment.get("recipient_gets_before_meeting") or "",
        "next_action": assessment.get("next_action") or "",
        "missing_evidence": list(assessment.get("missing_evidence") or []),
    }


def authoritative_decision(assessment: dict[str, Any]) -> dict[str, str]:
    """Single status field. send_decision and draft_decision stay the same."""
    return {
        "status": str(assessment.get("draft_decision") or "no_draft"),
        "reason": str(assessment.get("reason") or ""),
    }


def format_reply_reason_section(assessment: dict[str, Any]) -> str:
    evidence = assessment.get("workflow_ownership_evidence") or []
    missing = assessment.get("missing_evidence") or []
    motion = str(assessment.get("motion") or "cold_product")
    lines = [
        "## D. Reply-Reason Assessment",
        f"- Trigger type: {assessment.get('trigger_type') or ''}",
        f"- Trigger/offer alignment: {assessment.get('trigger_offer_alignment') or ''}",
        f"- Reply-reason level: {assessment.get('reply_reason_level') or ''}",
        f"- Motion: {motion}",
        f"- Active occasion: {assessment.get('active_occasion') or ''}",
        f"- Relationship path: {assessment.get('relationship_path') or ''}",
        "- Workflow ownership evidence: " + ("; ".join(evidence) if evidence else ""),
        f"- Verified sender asset: {assessment.get('sender_asset') or ''}",
        f"- Recipient gets before meeting: {assessment.get('recipient_gets_before_meeting') or ''}",
        f"- Next action: {assessment.get('next_action') or ''}",
        f"- Gap assessment: {assessment.get('gap_status') or ''}",
        "- Do not claim: " + ("; ".join(assessment.get("do_not_claim") or [])),
        f"- Draft decision: {assessment.get('draft_decision') or ''}",
        "- Missing evidence: " + (", ".join(missing) if missing else ""),
        f"- Reason: {assessment.get('reason') or ''}",
        (
            "If Gap assessment is not confirmed_gap, do not state an unconfirmed problem as fact. "
            "A narrow question about the unknown is allowed."
            if assessment.get("gap_status") in ("possible_gap", "unknown", "covered")
            else ""
        ),
        "",
        MOTION_STRUCTURES.get(motion, MOTION_STRUCTURES["cold_product"]),
        "",
        "The deterministic draft decision is authoritative.",
        "Do not draft when Draft decision is not send_now.",
        "Do not mention a teardown, replay, prototype, or analysis unless Verified sender asset names it.",
    ]
    return "\n".join(lines)


def followup_assessment() -> dict[str, Any]:
    return assess_reply_reason(
        {
            "outreach_motion": "existing_relationship",
            "trigger_type": "relationship_trigger",
            "relationship_path": "existing email thread",
            "active_occasion": "follow-up on a sent email",
        }
    )


def ensure_may_draft(
    rec: dict[str, Any] | None,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Block the drafting model when the gate did not return send_now."""
    if (profile or {}).get("previous_send") or (rec or {}).get("followup"):
        return followup_assessment()
    assessment = assess_reply_reason(rec, profile)
    if assessment["draft_decision"] != "send_now":
        raise DraftBlocked(assessment)
    return assessment


def stamp_authoritative_decision(email: dict[str, Any], assessment: dict[str, Any]) -> dict[str, Any]:
    out = dict(email)
    decision = authoritative_decision(assessment)
    out["reply_reason"] = reply_reason_payload(assessment)
    out["send_decision"] = decision
    out["draft_decision"] = decision
    return out


def held_draft_result(assessment: dict[str, Any]) -> dict[str, Any]:
    decision = authoritative_decision(assessment)
    return {
        "subject": "",
        "body": "",
        "verdict": assessment["draft_decision"],
        "critique": {
            "hard_fails": [],
            "soft_scores": {},
            "total": 0,
            "issues": [assessment["reason"]],
            "reply_reason": reply_reason_payload(assessment),
            "send_decision": decision,
            "draft_decision": decision,
        },
        "error": None,
        "reply_reason": reply_reason_payload(assessment),
        "send_decision": decision,
    }


def _body_has(body: str, phrases: tuple[str, ...]) -> bool:
    low = (body or "").lower()
    return any(p in low for p in phrases)


def reply_reason_hard_fails(
    body: str,
    assessment: dict[str, Any] | None,
    profile: dict[str, Any] | None = None,
) -> list[str]:
    """Integrity failures. These are not copy-quality penalties."""
    assessment = assessment or {}
    fails: list[str] = []
    trigger = assessment.get("trigger_type") or ""
    level = assessment.get("reply_reason_level") or ""
    motion = assessment.get("motion") or ""
    decision = assessment.get("draft_decision") or ""
    text = body or ""
    if text.strip() and decision and decision != "send_now":
        fails.append("draft_generated_despite_no_draft")
    if trigger == "topic_trigger" and _body_has(text, _PERSONAL_PAIN):
        fails.append("topic_signal_used_as_action_trigger")
    ownership = assessment.get("workflow_ownership_evidence") or []
    if _body_has(text, _PERSONAL_PAIN) and not ownership and motion in (
        "cold_product",
        "expert_research",
        "",
    ):
        fails.append("no_verified_workflow_owner")
    if "?" in text and level in ("topic_only", "none") and motion in ("cold_product", ""):
        fails.append("question_without_reply_reason")
    verified = bool(assessment.get("sender_asset")) or (
        assessment.get("sender_asset_status") == "verified"
    )
    if (
        motion == "cold_product"
        and _body_has(text, _MEETING)
        and not verified
    ):
        fails.append("meeting_before_value")
    if _body_has(text, _ASSET_CLAIM) and not verified:
        fails.append("sender_asset_not_verified")
    from trace_research import states_unconfirmed_problem

    if assessment.get("gap_status") in ("possible_gap", "unknown", "covered") and states_unconfirmed_problem(text):
        fails.append("inferred_gap_claimed_as_fact")
    if motion == "expert_research" and _body_has(text, _PERSONAL_PAIN):
        fails.append("motion_structure_mismatch")
    if motion == "connector" and _body_has(text, ("demo", "pilot", "our product")):
        fails.append("motion_structure_mismatch")
    # profile is accepted so callers can pass it; asset truth lives on the assessment
    del profile
    seen: set[str] = set()
    out: list[str] = []
    for item in fails:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
