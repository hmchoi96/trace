"""Draft evaluation architecture: Integrity → Research alignment → Copy quality.

Profile owns product/sender/sign-off. Template owns writing style.
Research package owns recipient evidence. Critique must not couple style to product.
"""

from __future__ import annotations

import re
from typing import Any

# Evidence strength on the research package (not a copy score).
EVIDENCE_NONE = "none"
EVIDENCE_CONTEXT = "context"
EVIDENCE_WORKFLOW = "workflow"
EVIDENCE_FRICTION = "friction"
EVIDENCE_DEMAND = "demand"

EVIDENCE_LEVELS = (
    EVIDENCE_NONE,
    EVIDENCE_CONTEXT,
    EVIDENCE_WORKFLOW,
    EVIDENCE_FRICTION,
    EVIDENCE_DEMAND,
)

# Unified copy-quality bands (same for every style).
QUALITY_STRONG = 90
QUALITY_SENDABLE = 80
QUALITY_REVISE = 70

MAX_REVISE_ATTEMPTS = 1

# Soft scores shared across styles (sum = 100).
COPY_SOFT_KEYS = (
    "opening_relevance",  # was "hook" — reason to keep reading from the signal
    "evidence_distance",  # how far the email moves past supported signal
    "question_quality",
    "subject_fit",
    "clarity",
    "brevity",
    "naturalness",
)

_PAIN_CLAIM = re.compile(
    r"\b(hard to find|can'?t find|cannot find|lost|struggle|broken archive|"
    r"nothing to go back|vanishes|vanish|disappear)\b",
    re.I,
)


def normalize_evidence_level(raw: str | None, *, signal_strength: str | None = None) -> str:
    """Map stored fields onto a canonical evidence_level."""
    text = str(raw or "").strip().lower()
    aliases = {
        "none": EVIDENCE_NONE,
        "unknown": EVIDENCE_NONE,
        "context": EVIDENCE_CONTEXT,
        "l1": EVIDENCE_CONTEXT,
        "level_1": EVIDENCE_CONTEXT,
        "workflow": EVIDENCE_WORKFLOW,
        "l2": EVIDENCE_WORKFLOW,
        "level_2": EVIDENCE_WORKFLOW,
        "friction": EVIDENCE_FRICTION,
        "l3": EVIDENCE_FRICTION,
        "level_3": EVIDENCE_FRICTION,
        "pain": EVIDENCE_FRICTION,
        "demand": EVIDENCE_DEMAND,
        "l4": EVIDENCE_DEMAND,
        "level_4": EVIDENCE_DEMAND,
        "strong": EVIDENCE_FRICTION,
        "medium": EVIDENCE_CONTEXT,
    }
    if text in aliases:
        return aliases[text]
    strength = str(signal_strength or "").strip().lower()
    if strength in aliases:
        return aliases[strength]
    return EVIDENCE_NONE


def evidence_level_for(rec: dict[str, Any]) -> str:
    return normalize_evidence_level(
        rec.get("evidence_level"),
        signal_strength=str(rec.get("signal_strength") or ""),
    )


def has_draftable_signal(rec: dict[str, Any]) -> bool:
    """False only when there is nobody to address and no research trigger at all."""
    if str(rec.get("signal_text") or "").strip():
        return True
    if str(rec.get("why_relevant") or "").strip():
        return True
    if str(rec.get("linkedin_url") or "").strip():
        return True
    if evidence_level_for(rec) in (
        EVIDENCE_CONTEXT,
        EVIDENCE_WORKFLOW,
        EVIDENCE_FRICTION,
        EVIDENCE_DEMAND,
    ):
        return True
    name = str(rec.get("name") or rec.get("author_name") or "").strip()
    first = str(rec.get("first_name") or "").strip()
    # Identity alone may draft a minimal role-level note (evidence_level=none).
    return bool(name or first)


def evidence_drafting_guidance(level: str) -> str:
    level = normalize_evidence_level(level)
    if level == EVIDENCE_DEMAND:
        return (
            "Evidence level: demand. Workaround or active search is supported. "
            "You may discuss value carefully. Still one question; no feature tour."
        )
    if level == EVIDENCE_FRICTION:
        return (
            "Evidence level: friction. Pain evidence exists. You may name the friction "
            "only as far as the research package supports. Do not invent severity."
        )
    if level == EVIDENCE_WORKFLOW:
        return (
            "Evidence level: workflow. Behavior is visible (e.g. revisiting prior work). "
            "Validate the workflow. Do not claim they cannot find past thinking."
        )
    if level == EVIDENCE_CONTEXT:
        return (
            "Evidence level: context. Process intensity or role context only — not pain. "
            "Ask whether the observed process creates the hypothesized behavior. "
            "Do not describe a pain. Do not say they lose or cannot find past reasoning."
        )
    return (
        "Evidence level: none. No supported personalization trigger. "
        "Do not fabricate posts, hiring, funding, or internal process. "
        "Prefer not drafting; if forced, ask a minimal role-level question only."
    )


def quality_band(total: int) -> str:
    if total >= QUALITY_STRONG:
        return "strong"
    if total >= QUALITY_SENDABLE:
        return "usable"
    if total >= QUALITY_REVISE:
        return "revise"
    return "rewrite"


def _norm_line(s: str) -> str:
    return " ".join((s or "").lower().replace("—", "-").replace("–", "-").split())


def sign_off_lines(sign_off: str) -> list[str]:
    return [ln.strip() for ln in (sign_off or "").replace("\r\n", "\n").split("\n") if ln.strip()]


def body_matches_sign_off(body: str, sign_off: str) -> bool:
    """Last non-empty body lines match the profile sign-off (lenient whitespace)."""
    required = sign_off_lines(sign_off)
    if len(required) < 2:
        return False
    lines = [ln.strip() for ln in (body or "").replace("\r\n", "\n").split("\n") if ln.strip()]
    if len(lines) < 2:
        return False
    return (
        _norm_line(lines[-2]) == _norm_line(required[-2])
        and _norm_line(lines[-1]) == _norm_line(required[-1])
    )


def strip_foreign_signoff_hard_fails(
    hard_fails: list[str],
    *,
    required_sign_off: str,
) -> list[str]:
    """Drop sign-off complaints about products/lines not in the active profile sign-off."""
    required_l = _norm_line(required_sign_off)
    out: list[str] = []
    for h in hard_fails:
        hl = str(h).lower()
        mentions_sign = any(
            k in hl
            for k in (
                "sign-off",
                "sign off",
                "signoff",
                "signature",
                "closing",
                "second line",
                "building helix",
                "helix by wiserbond",
            )
        )
        if not mentions_sign:
            out.append(str(h))
            continue
        # Keep fails that reference a mismatch with the required sign-off text.
        if "invent" in hl and "product" in hl:
            out.append(str(h))
            continue
        # Helix-only complaints when Helix is not in this profile's sign-off.
        if ("helix" in hl) and ("helix" not in required_l):
            continue
        # Generic "missing building Helix" when not required.
        if "building helix" in hl and "building helix" not in required_l:
            continue
        out.append(str(h))
    return out


def integrity_hard_fails(
    *,
    body: str,
    subject: str,
    sign_off: str,
    word_count: int | None = None,
    word_limit: int = 75,
    llm_hard_fails: list[str] | None = None,
) -> list[str]:
    """Deterministic integrity gate. Score is irrelevant if this fails."""
    fails: list[str] = []
    for h in llm_hard_fails or []:
        hl = str(h).lower()
        if any(
            k in hl
            for k in (
                "invent",
                "fabricat",
                "not supported",
                "facts block",
                "research package",
            )
        ):
            fails.append(str(h))
    if re.search(r"[—]", f"{subject or ''}\n{body or ''}"):
        fails.append("Em dash present in subject or body.")
    if word_count is not None and word_count > word_limit:
        fails.append(
            f"Body exceeds {word_limit} words (deterministic count: {word_count})"
        )
    if sign_off and not body_matches_sign_off(body, sign_off):
        lines = [ln.strip() for ln in (body or "").replace("\r\n", "\n").split("\n") if ln.strip()]
        if len(lines) < 2:
            fails.append("Missing required sign-off lines from the active profile.")
        else:
            req_l = _norm_line(sign_off)
            last = _norm_line(lines[-1])
            # Wrong product line in the closing (e.g. Helix on an Akashic profile).
            if "helix" in last and "helix" not in req_l:
                fails.append(
                    "Sign-off uses a different product line than the active profile."
                )
            elif req_l and last and last not in req_l and _norm_line(sign_off_lines(sign_off)[-1]) not in last:
                # Company / second line mismatch only when clearly divergent.
                req_second = _norm_line(sign_off_lines(sign_off)[-1])
                if req_second and req_second != last:
                    fails.append(
                        "Sign-off does not match the active profile sign-off."
                    )
    # Dedupe while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for f in fails:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def alignment_hard_fails(
    *,
    body: str,
    evidence_level: str,
    llm_hard_fails: list[str] | None = None,
) -> list[str]:
    """Research alignment gate: role/ask/evidence distance."""
    fails: list[str] = []
    level = normalize_evidence_level(evidence_level)
    for h in llm_hard_fails or []:
        hl = str(h).lower()
        if any(
            k in hl
            for k in (
                "outreach_role",
                "recommended_ask",
                "contradict",
                "evidence distance",
                "firm-level",
                "personal workflow",
                "personal pain",
                "trace logic",
                "expert / researcher",
                "researcher as",
            )
        ):
            fails.append(str(h))
    if level in (EVIDENCE_NONE, EVIDENCE_CONTEXT) and _PAIN_CLAIM.search(body or ""):
        fails.append(
            "Evidence distance: pain/loss language is not supported at "
            f"evidence_level={level}."
        )
    seen: set[str] = set()
    out: list[str] = []
    for f in fails:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def decide_verdict(
    critique: dict[str, Any],
    attempts_used: int,
    *,
    integrity_fails: list[str] | None = None,
    alignment_fails: list[str] | None = None,
) -> str:
    """Unified verdict across styles.

    sendable (pass) = no integrity/alignment fails AND quality >= 80
    revise = fixable fails or quality 70–79
    block = exhausted attempts, or quality < 70 with no path
    """
    integ = [x for x in (integrity_fails or []) if x]
    align = [x for x in (alignment_fails or []) if x]
    # Prefer explicit gate fails; fall back to critique hard_fails for callers
    # that have not split layers yet.
    hard = integ + align
    if not hard:
        hard = [str(x) for x in (critique.get("hard_fails") or []) if x]
    total = int(critique.get("total") or 0)

    if hard:
        if attempts_used >= MAX_REVISE_ATTEMPTS:
            return "block"
        return "revise"
    if total >= QUALITY_SENDABLE:
        return "pass"
    if total >= QUALITY_REVISE:
        if attempts_used >= MAX_REVISE_ATTEMPTS:
            return "block"
        return "revise"
    return "block"


def annotate_critique(
    critique: dict[str, Any],
    *,
    integrity_fails: list[str],
    alignment_fails: list[str],
) -> dict[str, Any]:
    out = dict(critique)
    total = int(out.get("total") or 0)
    out["integrity_fails"] = list(integrity_fails)
    out["alignment_fails"] = list(alignment_fails)
    out["hard_fails"] = list(integrity_fails) + list(alignment_fails)
    out["quality_band"] = quality_band(total)
    out["layers"] = {
        "integrity": "fail" if integrity_fails else "pass",
        "alignment": "fail" if alignment_fails else "pass",
        "copy": out["quality_band"],
    }
    return out
