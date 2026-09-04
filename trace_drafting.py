"""Shared drafting policy and Trace research context for all email templates."""

from __future__ import annotations

from typing import Any

TRACE_TYPOGRAPHY_RULES = """
# TYPOGRAPHY (all templates)
- Never use em dashes (—) anywhere in the subject or body, including the sign-off. Use commas, periods, colons, or separate sentences instead.
- Vary capitalization naturally. Do not mechanically capitalize every sentence or short standalone line. Mix conventional capitalized openings with occasional lowercase openings when it feels natural and conversational. Do not force lowercase; use it selectively.
- Subject lines may use lowercase when natural (e.g. "your research on investment memos").
""".strip()

TRACE_RESEARCH_POLICY = """
TRACE RESEARCH POLICY

The Trace research package below is the sole research basis for this email.

Do not perform, infer, or invent additional company or prospect research.

Preserve the logic of WHY this person was surfaced.

Use the verified facts, research evidence, actor classification,
recommendation, and caveats together.

Do not turn observational evidence into a claim that the recipient
personally experiences the problem.

Do not treat researchers, experts, commentators, or connectors as buyers
or practitioners unless Trace evidence supports that classification.

If Trace says the person is not the likely user or economic buyer,
adapt the question and ask accordingly.

The final email must be logically consistent with Trace's recommendation.
""".strip()

TRACE_CRITIQUE_LOGIC = """
# TRACE EVALUATION LAYERS

## Integrity (hard_fail — pass/fail, not a soft score)
- Invented facts / unsupported personalization
- Wrong product or sign-off vs the active profile
- Em dash, missing closing, or other format breaks enforced here or by code

## Research alignment (hard_fail)
- Preserve why_surfaced → outreach_role → recommended_ask
- Respect evidence_level (context/workflow/friction/demand)
- Evidence distance: do not turn firm-level or context signals into personal pain
- Do not treat Expert / Researcher as Practitioner without evidence

## Copy quality (soft_scores only)
- opening_relevance, evidence_distance, question_quality, subject_fit,
  clarity, brevity, naturalness
- Subject should name the observation/question briefly — not catchy marketing
- Opening = signal + why you are asking; no hollow praise hooks

If Integrity or Alignment is violated, add a specific hard_fail.
""".strip()

OUTREACH_ROLES = (
    "Economic Buyer",
    "Practitioner",
    "Internal Champion",
    "Expert / Researcher",
    "Connector",
    "Non-target",
)

RECOMMENDED_ASKS = {
    "Economic Buyer": "explore_value_and_next_step",
    "Practitioner": "confirm_workflow_pain",
    "Internal Champion": "test_org_priority",
    "Expert / Researcher": "validate_problem_interpretation",
    "Connector": "find_workflow_owner_or_intro",
    "Non-target": "minimal_pattern_validation",
}

_OUTREACH_ASK_GUIDANCE = {
    "Economic Buyer": (
        "Email purpose: validate value and explore a concrete next step only if "
        "Trace evidence supports buyer relevance. One clear, low-friction ask."
    ),
    "Practitioner": (
        "Email purpose: confirm whether this workflow pain exists in their day-to-day work. "
        "Ask about their experience, not industry commentary."
    ),
    "Internal Champion": (
        "Email purpose: test whether the organization would care about solving this "
        "and who owns the workflow."
    ),
    "Expert / Researcher": (
        "Email purpose: validate Trace's interpretation of the research signal. "
        "Do NOT ask whether they personally struggle with the workflow. Ask whether "
        "the gap Trace identified matches what they observed."
    ),
    "Connector": (
        "Email purpose: learn who actually owns the workflow or whether an intro "
        "might make sense. Do not pitch."
    ),
    "Non-target": (
        "Email purpose: if a draft is generated anyway, keep it minimal and do not "
        "assert personal pain. Prefer validation of the industry pattern only."
    ),
}

_CLASSIFICATION_AXES = (
    "persona_fit",
    "pain_evidence",
    "behavioral_evidence",
    "workaround_evidence",
    "outcome_feedback_evidence",
    "influence_or_champion_potential",
    "economic_buyer_likelihood",
    "end_user_likelihood",
)

_EXPERT_MARKERS = (
    "commentator",
    "researcher",
    "co-author",
    "authors are",
    "researched",
    "interviewed",
    "studied",
    "authored",
    "published on",
    "industry explainer",
    "not an economic buyer",
    "not a pe underwriter",
    "not the likely user",
    "not ic workflow owner",
    "not ic user",
    "does not own",
    "without owning",
    "describes the workflow without owning",
)

_OWNERSHIP_NEGATION_MARKERS = (
    "not an economic buyer",
    "not ic workflow owner",
    "not ic user",
    "not the likely user",
    "without owning",
    "does not own",
    "describes the workflow without owning",
)

_CONNECTOR_MARKERS = ("intro via", "possible intro", "connector", "introduce")


def _axis_high(value: str | None) -> bool:
    return str(value or "").upper() in ("HIGH", "VERY_HIGH")


def _axis_low(value: str | None) -> bool:
    return str(value or "").upper() in ("LOW", "VERY_LOW")


def _reason(rec: dict[str, Any]) -> str:
    return str(rec.get("recommendation_reason") or "").lower()


def _has_expert_researcher_signals(reason: str) -> bool:
    return any(m in reason for m in _EXPERT_MARKERS)


def _does_not_own_workflow(reason: str) -> bool:
    return any(m in reason for m in _OWNERSHIP_NEGATION_MARKERS)


def _has_connector_opportunity(reason: str) -> bool:
    return any(m in reason for m in _CONNECTOR_MARKERS)


def _is_expert_researcher(rec: dict[str, Any], reason: str) -> bool:
    actor = str(rec.get("actor_type") or "UNKNOWN").upper()
    recommendation = str(rec.get("recommendation") or "").upper()
    if recommendation == "HIGH_VALUE_DISCOVERY":
        return True
    if _has_expert_researcher_signals(reason) and _does_not_own_workflow(reason):
        return True
    if actor == "OTHER" and _has_expert_researcher_signals(reason):
        return True
    if (
        recommendation == "CHAMPION_CANDIDATE"
        and _axis_low(rec.get("end_user_likelihood"))
        and _has_expert_researcher_signals(reason)
    ):
        return True
    if (
        _axis_low(rec.get("economic_buyer_likelihood"))
        and _axis_low(rec.get("end_user_likelihood"))
        and _has_expert_researcher_signals(reason)
    ):
        return True
    return False


def _is_connector_primary(rec: dict[str, Any], reason: str) -> bool:
    if not _has_connector_opportunity(reason):
        return False
    if _is_expert_researcher(rec, reason):
        return False
    actor = str(rec.get("actor_type") or "UNKNOWN").upper()
    if actor == "CONNECTOR":
        return True
    return _has_connector_opportunity(reason) and not _has_expert_researcher_signals(reason)


def classify_outreach(rec: dict[str, Any]) -> dict[str, Any]:
    """Primary relationship to the problem, with optional secondary roles."""
    actor = str(rec.get("actor_type") or "UNKNOWN").upper()
    reason = _reason(rec)
    recommendation = str(rec.get("recommendation") or "").upper()
    secondary_roles: list[str] = []

    if actor == "BUILDER_OR_VENDOR":
        role = "Non-target"
    elif recommendation in ("LIKELY_NOT_RELEVANT", "LIKELY_NOT_PROSPECT"):
        role = "Non-target"
    elif _is_expert_researcher(rec, reason):
        role = "Expert / Researcher"
        if _has_connector_opportunity(reason):
            secondary_roles.append("Connector")
    elif _axis_high(rec.get("economic_buyer_likelihood")):
        role = "Economic Buyer"
    elif _axis_high(rec.get("end_user_likelihood")) or actor == "PRACTITIONER":
        role = "Practitioner"
    elif _axis_high(rec.get("influence_or_champion_potential")) or recommendation == "CHAMPION_CANDIDATE":
        role = "Internal Champion"
    elif recommendation in ("PRIMARY_PROSPECT", "LIKELY_PROSPECT", "ADJACENT_PRACTITIONER"):
        role = "Practitioner"
    elif _is_connector_primary(rec, reason):
        role = "Connector"
    else:
        role = "Practitioner"

    return {
        "outreach_role": role,
        "secondary_roles": secondary_roles,
        "recommended_ask": RECOMMENDED_ASKS.get(role, RECOMMENDED_ASKS["Practitioner"]),
    }


def classify_outreach_role(rec: dict[str, Any]) -> str:
    return classify_outreach(rec)["outreach_role"]


def outreach_ask_guidance(role: str) -> str:
    return _OUTREACH_ASK_GUIDANCE.get(role, _OUTREACH_ASK_GUIDANCE["Practitioner"])


def legacy_outreach_angle(rec: dict[str, Any], profile: dict[str, Any], *, is_senior: bool) -> str:
    """Actor-first drafting angle for Akashic legacy; senior/early is secondary nuance."""
    outreach = classify_outreach(rec)
    role = outreach["outreach_role"]
    lines = [
        "=== OUTREACH ROLE (Trace classification — primary) ===",
        f"Role: {role}",
        f"Recommended ask: {outreach['recommended_ask']}",
        outreach_ask_guidance(role),
    ]
    if outreach["secondary_roles"]:
        lines.append(f"Secondary roles (do not override primary): {', '.join(outreach['secondary_roles'])}")
    lines.append("")
    angles = profile.get("angles") or {}
    nuance_key = "senior" if is_senior else "early"
    nuance = str(angles.get(nuance_key) or "").strip()
    if nuance:
        lines.extend([
            "=== ROLE NUANCE (secondary; do not override outreach role) ===",
            nuance,
            "",
        ])
    lines.append("=== end outreach guidance ===")
    return "\n".join(lines)


def _format_evidence_items(rec: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for ev in rec.get("supporting_evidence") or []:
        if not isinstance(ev, dict):
            continue
        quote = str(ev.get("quote_or_paraphrase") or "").strip()
        if not quote:
            continue
        excerpt = quote if len(quote) <= 280 else quote[:277] + "…"
        bits = [ev.get("evidence_type") or "EVIDENCE", excerpt]
        if ev.get("source_date"):
            bits.append(f"({ev['source_date']})")
        lines.append("- " + " ".join(str(b) for b in bits if b))
    extra = rec.get("additional_signals") or rec.get("signals") or []
    for sig in extra:
        if not isinstance(sig, dict):
            continue
        text = str(sig.get("signal_text") or sig.get("text") or "").strip()
        if not text:
            continue
        excerpt = text if len(text) <= 220 else text[:217] + "…"
        lines.append(
            f"- Additional signal ({sig.get('source') or sig.get('published_at') or 'web'}): {excerpt}"
        )
    return lines


def format_drafting_context_package(rec: dict[str, Any]) -> str:
    """Build the sole research input for drafting from Trace's existing research."""
    from trace_eval import evidence_drafting_guidance, evidence_level_for

    outreach = classify_outreach(rec)
    role = outreach["outreach_role"]
    sections: list[str] = [
        TRACE_RESEARCH_POLICY,
        "",
        "=== DRAFTING CONTEXT PACKAGE ===",
        "",
        "## A. Verified Facts",
        f"- Name: {rec.get('name') or rec.get('author_name') or ''}",
        f"- Title: {rec.get('title') or ''}",
        f"- Company: {rec.get('company') or ''}",
        f"- Company type: {rec.get('company_type') or ''}",
        f"- LinkedIn: {rec.get('linkedin_url') or ''}",
        f"- Email (if known): {rec.get('email') or ''}",
        "",
        "## B. Research Evidence",
        f"- Primary signal source: {rec.get('signal_source') or ''}",
        f"- Primary signal URL: {rec.get('signal_url') or ''}",
        f"- Primary signal date: {rec.get('signal_date') or ''}",
    ]
    signal_text = str(rec.get("signal_text") or "").strip()
    if signal_text:
        excerpt = signal_text if len(signal_text) <= 500 else signal_text[:497] + "…"
        sections.append(f"- Primary signal text: {excerpt}")
    why = str(rec.get("why_relevant") or "").strip()
    if why:
        sections.append(f"- Why surfaced: {why}")
    latent = str(rec.get("latent_behavior") or "").strip()
    if latent:
        sections.append(f"- Latent behavior: {latent}")
    evidence_lines = _format_evidence_items(rec)
    if evidence_lines:
        sections.append("- Deepened / additional evidence:")
        sections.extend(f"  {ln}" for ln in evidence_lines)
    sections.extend([
        "",
        "## C. Trace Interpretation",
        f"- Evidence level: {evidence_level_for(rec)}",
        f"- Actor type (raw): {rec.get('actor_type') or 'UNKNOWN'}",
        f"- Outreach role (primary): {role}",
        f"- Recommended ask: {outreach['recommended_ask']}",
    ])
    if outreach["secondary_roles"]:
        sections.append(f"- Secondary roles: {', '.join(outreach['secondary_roles'])}")
    sections.extend([
        f"- Recommendation: {rec.get('recommendation') or ''}",
        f"- Recommendation reason: {rec.get('recommendation_reason') or ''}",
        f"- Evidence drafting rule: {evidence_drafting_guidance(evidence_level_for(rec))}",
    ])
    axis_bits = [
        f"{k}={rec[k]}"
        for k in _CLASSIFICATION_AXES
        if rec.get(k) and str(rec[k]).upper() != "UNKNOWN"
    ]
    if axis_bits:
        sections.append("- Classification axes: " + "; ".join(axis_bits))
    sections.extend([
        "",
        "=== OUTREACH ASK GUIDANCE ===",
        outreach_ask_guidance(role),
        "",
        "=== end drafting context package ===",
    ])
    return "\n".join(sections)


def format_trace_critique_context(rec: dict[str, Any]) -> str:
    """Same research package as drafting, plus logic checks for critique."""
    return f"{format_drafting_context_package(rec)}\n\n{TRACE_CRITIQUE_LOGIC}"


def enrich_lead_from_candidate(rec: dict[str, Any]) -> dict[str, Any]:
    """Copy Trace research fields onto the lead dict used by the draft engine."""
    from signal_discovery import candidate_to_lead, split_name

    lead = candidate_to_lead(rec)
    outreach = classify_outreach(rec)
    for key in _CLASSIFICATION_AXES:
        if rec.get(key):
            lead[key] = rec[key]
    for key in (
        "why_relevant",
        "latent_behavior",
        "company_type",
        "supporting_evidence",
        "additional_signals",
        "signals",
        "recommendation_reason",
        "evidence_level",
        "signal_strength",
    ):
        if rec.get(key) not in (None, "", []):
            lead[key] = rec[key]
    from trace_eval import evidence_level_for

    lead["evidence_level"] = evidence_level_for(rec)
    lead["outreach_role"] = outreach["outreach_role"]
    lead["secondary_roles"] = outreach["secondary_roles"]
    lead["recommended_ask"] = outreach["recommended_ask"]
    first, _last = split_name(rec.get("name") or rec.get("author_name") or "")
    if first:
        lead["first_name"] = first
    return lead
