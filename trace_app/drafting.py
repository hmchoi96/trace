"""Draft one email with the existing Claude engine. No printing, no sending.

Mirrors the CLI's draft → critique → one revision → humanize loop, but returns
the result instead of writing JSONL and calling Graph.
"""

from __future__ import annotations

import json
from typing import Any

VERDICT_SENDABLE = "pass"


def _draft_email_with_retry(engine, engine_profile, lead, derived):
    """Retry once on JSON parse / malformed draft output."""
    last_error: Exception | None = None
    for _attempt in range(2):
        try:
            return engine.claude_draft_email(
                engine_profile, lead, "yesno", revision=None, derived=derived,
            )
        except json.JSONDecodeError as exc:
            last_error = exc
        except ValueError as exc:
            msg = str(exc).lower()
            if "subject" in msg or "body" in msg or "json" in msg:
                last_error = exc
            else:
                raise
        except Exception:
            raise
    raise ValueError(f"draft: {last_error}")


def build_draft(
    engine_profile: dict[str, Any],
    lead: dict[str, Any],
    *,
    no_humanize: bool = False,
) -> dict[str, Any]:
    """Returns subject, body, verdict, critique, error."""
    import main as engine
    from trace_eval import (
        annotate_critique,
        evidence_level_for,
        has_draftable_signal,
    )

    result: dict[str, Any] = {
        "subject": "",
        "body": "",
        "verdict": "failed",
        "critique": None,
        "error": None,
    }

    if not has_draftable_signal(lead):
        critique = annotate_critique(
            {
                "hard_fails": [
                    "NO SIGNAL: research package has no supported trigger; "
                    "do not fabricate personalization."
                ],
                "soft_scores": {},
                "total": 0,
                "issues": [
                    "Choose research_more or skip drafting until a real signal exists."
                ],
            },
            integrity_fails=[
                "NO SIGNAL: research package has no supported trigger; "
                "do not fabricate personalization."
            ],
            alignment_fails=[],
        )
        result["verdict"] = "block"
        result["critique"] = critique
        result["error"] = "no_signal"
        return result

    # Ensure evidence_level is always present for critique gates.
    lead = dict(lead)
    lead["evidence_level"] = evidence_level_for(lead)

    derived = None
    # Optional campaign hints may be precomputed by callers; style templates stay
    # product-agnostic and do not branch on Helix vs other products.

    try:
        email = _draft_email_with_retry(engine, engine_profile, lead, derived)
    except Exception as exc:
        result["error"] = str(exc) if str(exc).startswith("draft:") else f"draft: {exc}"
        return result

    critique: dict[str, Any] | None = None
    verdict = "failed"
    attempts = 0
    while True:
        try:
            critique = engine.claude_critique_email(
                email, engine_profile, lead, derived=derived,
            )
        except Exception as exc:
            result["error"] = f"critique: {exc}"
            result["subject"] = email.get("subject") or ""
            result["body"] = email.get("body") or ""
            return result

        verdict = engine._decide_verdict(critique, attempts, engine_profile)
        if verdict in ("pass", "review", "block"):
            break

        attempts += 1
        try:
            email = engine.claude_draft_email(
                engine_profile,
                lead,
                "yesno",
                revision={"previous": email, "critique": critique},
                derived=derived,
            )
        except Exception as exc:
            result["error"] = f"revision: {exc}"
            result["subject"] = email.get("subject") or ""
            result["body"] = email.get("body") or ""
            return result

    if (
        engine._uses_template_draft_path(engine_profile)
        and verdict in ("pass", "review")
    ):
        email, critique, verdict, _meta = engine._finalize_pb_with_humanize(
            email, critique, verdict, lead, derived, engine_profile,
            no_humanize=no_humanize,
        )

    result["subject"] = email.get("subject") or ""
    result["body"] = email.get("body") or ""
    result["verdict"] = verdict
    result["critique"] = critique
    return result
