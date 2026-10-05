"""Structured research on a candidate.

Facts, workarounds, inferences, and unknowns stay in separate lists.
Free-form recommendation prose is never parsed into a verified fact.
"""

from __future__ import annotations

import re
from typing import Any

GAP_STATUSES = ("confirmed_gap", "possible_gap", "covered", "unknown")
CONFIDENCE_LEVELS = ("high", "medium", "low")
_CONF_RANK = {"low": 1, "medium": 2, "high": 3}

_DECISION_LABELS = {
    "send_now": "Send now",
    "research_more": "Research more",
    "strengthen_offer": "Strengthen the offer",
    "change_recipient": "Change recipient",
    "use_different_channel": "Use a different channel",
    "no_draft": "No draft",
}

_POSSIBLE_GAP_CLAIM = "Do not claim the remaining gap as a verified fact."
_COVERED_CLAIM = "Do not claim a remaining problem from this evidence."


def empty_research() -> dict[str, Any]:
    return {
        "verified_facts": [],
        "current_workarounds": [],
        "inferences": [],
        "unknowns": [],
        "gap_assessment": {"status": "", "reason": "", "based_on": []},
        "do_not_claim": [],
    }


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split())


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def _fact(raw: Any, *, quote: bool) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    claim = str(raw.get("claim") or "").strip()
    if not claim:
        return None
    item = {
        "claim": claim,
        "source_url": str(raw.get("source_url") or "").strip(),
        "source_date": str(raw.get("source_date") or "").strip(),
    }
    if quote:
        item["quote_or_paraphrase"] = str(raw.get("quote_or_paraphrase") or "").strip()
    return item


def _inference(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    claim = str(raw.get("claim") or "").strip()
    if not claim:
        return None
    confidence = str(raw.get("confidence") or "").strip().lower()
    if confidence not in CONFIDENCE_LEVELS:
        confidence = "low"
    return {"claim": claim, "confidence": confidence, "based_on": _strings(raw.get("based_on"))}


def _gap(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"status": "", "reason": "", "based_on": []}
    status = str(raw.get("status") or "").strip()
    if status not in GAP_STATUSES:
        status = ""
    return {
        "status": status,
        "reason": str(raw.get("reason") or "").strip(),
        "based_on": _strings(raw.get("based_on")),
    }


def parse_research(payload: Any) -> dict[str, Any]:
    """Read structured fields only. A recommendation paragraph is not a fact."""
    if not isinstance(payload, dict):
        return empty_research()
    block = payload.get("research") if isinstance(payload.get("research"), dict) else payload
    if not isinstance(block, dict):
        return empty_research()
    facts = [item for item in (_fact(row, quote=True) for row in block.get("verified_facts") or []) if item]
    workarounds = [
        item for item in (_fact(row, quote=False) for row in block.get("current_workarounds") or []) if item
    ]
    inferences = [item for item in (_inference(row) for row in block.get("inferences") or []) if item]
    unknowns = _strings(block.get("unknowns"))
    gap = _gap(block.get("gap_assessment"))
    return {
        "verified_facts": facts,
        "current_workarounds": workarounds,
        "inferences": inferences,
        "unknowns": unknowns,
        "gap_assessment": gap,
        "do_not_claim": _strings(block.get("do_not_claim")),
    }


def _fact_score(item: dict[str, str]) -> tuple[int, int, int, int, int]:
    quote = item.get("quote_or_paraphrase") or ""
    return (
        1 if item.get("source_url") else 0,
        1 if item.get("source_date") else 0,
        1 if quote else 0,
        len(quote),
        len(item.get("claim") or ""),
    )


def _collapse_claims(items: list[dict[str, str]]) -> list[dict[str, str]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for item in items:
        grouped.setdefault(_norm(item["claim"]), []).append(item)
    out: list[dict[str, str]] = []
    for group in grouped.values():
        sourced = [item for item in group if item.get("source_url")]
        pool = sourced or group
        by_url: dict[str, dict[str, str]] = {}
        for item in pool:
            key = _norm(item.get("source_url") or "") or "_"
            prev = by_url.get(key)
            if prev is None or _fact_score(item) > _fact_score(prev):
                by_url[key] = item
        out.extend(by_url.values())
    return out


def _better_inference(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    current_has = bool(current.get("based_on"))
    incoming_has = bool(incoming.get("based_on"))
    if incoming_has and not current_has:
        chosen = dict(incoming)
    elif current_has and not incoming_has:
        chosen = dict(current)
    elif incoming_has and current_has and _CONF_RANK[incoming["confidence"]] > _CONF_RANK[current["confidence"]]:
        chosen = dict(incoming)
    else:
        chosen = dict(current)
    based: list[str] = []
    for item in list(current.get("based_on") or []) + list(incoming.get("based_on") or []):
        if item not in based:
            based.append(item)
    chosen["based_on"] = based
    if not chosen["based_on"]:
        # Confidence only rises when the inference cites evidence.
        chosen["confidence"] = current["confidence"] if not incoming_has else incoming["confidence"]
    return chosen


def _collapse_inferences(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for item in items:
        key = _norm(item["claim"])
        prev = grouped.get(key)
        grouped[key] = item if prev is None else _better_inference(prev, item)
    return list(grouped.values())


def _collapse_lines(items: list[str]) -> list[str]:
    best: dict[str, str] = {}
    for text in items:
        key = _norm(text)
        if not key:
            continue
        prev = best.get(key)
        if prev is None or len(text) > len(prev):
            best[key] = text
    return list(best.values())


def dedupe_research(research: dict[str, Any]) -> dict[str, Any]:
    parsed = parse_research(research)
    return {
        "verified_facts": _collapse_claims(parsed["verified_facts"]),
        "current_workarounds": _collapse_claims(parsed["current_workarounds"]),
        "inferences": _collapse_inferences(parsed["inferences"]),
        "unknowns": _collapse_lines(parsed["unknowns"]),
        "gap_assessment": parsed["gap_assessment"],
        "do_not_claim": _collapse_lines(parsed["do_not_claim"]),
    }


def merge_research(*parts: Any) -> dict[str, Any]:
    combined = empty_research()
    gap = combined["gap_assessment"]
    for part in parts:
        parsed = parse_research(part)
        combined["verified_facts"].extend(parsed["verified_facts"])
        combined["current_workarounds"].extend(parsed["current_workarounds"])
        combined["inferences"].extend(parsed["inferences"])
        combined["unknowns"].extend(parsed["unknowns"])
        combined["do_not_claim"].extend(parsed["do_not_claim"])
        if parsed["gap_assessment"]["status"]:
            gap = parsed["gap_assessment"]
    combined["gap_assessment"] = gap
    return dedupe_research(combined)


def research_from_record(rec: dict[str, Any] | None) -> dict[str, Any]:
    """Empty sections when a stored record has no structured research."""
    rec = rec or {}
    raw = rec.get("research") if isinstance(rec.get("research"), dict) else None
    if raw is None and isinstance(rec.get("gap_assessment"), dict):
        raw = {"gap_assessment": rec.get("gap_assessment")}
    if raw is None:
        return empty_research()
    return dedupe_research(raw)


def gap_status(rec: dict[str, Any] | None) -> str:
    return str(research_from_record(rec)["gap_assessment"]["status"] or "")


def do_not_claim_lines(rec: dict[str, Any] | None) -> list[str]:
    research = research_from_record(rec)
    lines = list(research["do_not_claim"])
    gap = research["gap_assessment"]["status"]
    extra = ""
    if gap == "possible_gap":
        extra = _POSSIBLE_GAP_CLAIM
    elif gap == "covered":
        extra = _COVERED_CLAIM
    if extra and extra not in lines:
        lines.append(extra)
    return lines


def apply_gap_to_decision(
    status: str,
    trigger: str,
    decision: str,
    reason: str,
    missing: list[str],
) -> tuple[str, str, list[str]]:
    """Cold-product precedence. An empty status leaves the decision unchanged.

    covered: never send_now.
    unknown: send_now only for an independent action trigger.
    possible_gap: send_now may stand, and the reason must not state the gap as fact.
    confirmed_gap: keep the decision the rest of the gate already made.
    """
    if status not in GAP_STATUSES:
        return decision, reason, missing
    if status == "covered":
        return (
            "no_draft",
            "Available evidence shows the existing workaround covers the problem. No demonstrated remaining gap.",
            ["remaining_gap"],
        )
    if status == "unknown" and decision == "send_now" and trigger != "action_trigger":
        return (
            "research_more",
            "Public evidence does not show whether a problem remains.",
            ["gap_assessment"],
        )
    if status == "possible_gap" and decision == "send_now":
        return (
            "send_now",
            "Current behavior matches the offer. The remaining gap is inferred. Ask; do not state it as fact.",
            missing,
        )
    return decision, reason, missing


def decision_summary(rec: dict[str, Any] | None, assessment: dict[str, Any] | None) -> dict[str, str]:
    rec = rec or {}
    assessment = assessment or {}
    research = research_from_record(rec)
    facts = research["verified_facts"]
    decision = str(assessment.get("draft_decision") or "")
    why_now = str(assessment.get("active_occasion") or "").strip()
    if not why_now and facts:
        why_now = facts[0]["claim"]
    ownership = assessment.get("workflow_ownership_evidence") or []
    why_person = "; ".join(str(item).strip() for item in ownership if str(item).strip())
    claims = do_not_claim_lines(rec)
    return {
        "decision": _DECISION_LABELS.get(decision, decision),
        "whyNow": why_now,
        "whyThisPerson": why_person,
        "replyReason": str(assessment.get("reason") or "").strip(),
        "doNotClaim": " ".join(claims),
    }


def research_dto(rec: dict[str, Any] | None) -> dict[str, Any]:
    research = research_from_record(rec)
    gap = research["gap_assessment"]

    def fact_dto(item: dict[str, str], *, quote: bool) -> dict[str, str]:
        out = {
            "claim": item["claim"],
            "sourceUrl": item.get("source_url") or "",
            "sourceDate": item.get("source_date") or "",
        }
        if quote:
            out["quoteOrParaphrase"] = item.get("quote_or_paraphrase") or ""
        return out

    return {
        "verifiedFacts": [fact_dto(item, quote=True) for item in research["verified_facts"]],
        "currentWorkarounds": [fact_dto(item, quote=False) for item in research["current_workarounds"]],
        "inferences": [
            {
                "claim": item["claim"],
                "confidence": item["confidence"],
                "basedOn": list(item["based_on"]),
            }
            for item in research["inferences"]
        ],
        "unknowns": list(research["unknowns"]),
        "gapAssessment": {
            "status": gap["status"],
            "reason": gap["reason"],
            "basedOn": list(gap["based_on"]),
        },
        "doNotClaim": do_not_claim_lines(rec),
    }
