"""
Lightweight tone humanization for problem-validation emails (Python + Claude).
This is NOT the .NET Humanizr library; see README for rationale.
"""

from __future__ import annotations

import json
import os
import re

import anthropic

from segmentation import PB_WORD_WARN_HI, pb_body_length_analysis

LIGHT_HUMANIZE_SYSTEM = """\
You polish the **body** of a cold email so it reads slightly more like a real founder
typed it: less stiff, less “generated,” still professional. **Intensity: light** —
phrase-level fixes only; do not rewrite into a new structure.

**Do not change the subject line** (it is not part of this task; the pipeline keeps
the original subject).

Immutable (must appear **verbatim** in the body):
- First line: exactly this greeting line (including comma if present):
{greeting_line}
- Last lines (signature), exactly:
{sign_off}

Rules:
- Edit only the lines between greeting and signature.
- Keep the same core problem, segment angle, and intent of the question.
- Do not add facts, metrics, or claims about the prospect or company.
- Do not sound hypey or salesy. Avoid em dashes in new text.
- Shorten wordy phrases; replace stiff AI-ish wording with plain English.

OUTPUT — raw JSON only, no markdown:
{{"body": "...", "changes_summary": "one short line"}}
"""


# Post-humanize deterministic gate (accusatory / pipeline bans)
_POST_BANNED = (
    "you blank",
    "you forget",
    "your team",
    "your reps",
    "losing deals",
    "you struggle",
    "you lose",
    "you repeat",
    "repeats mistakes",
    "badly at objections",
    "process is broken",
)


def _first_line_greeting(body: str) -> str | None:
    lines = body.replace("\r\n", "\n").strip().split("\n")
    if not lines:
        return None
    return lines[0].strip()


def body_envelope_matches(body: str, greeting_line: str, sign_off: str) -> bool:
    """True if first line and last signature lines match exactly (whitespace-trimmed per line)."""
    raw = (body or "").replace("\r\n", "\n")
    lines = raw.split("\n")
    exp_g = greeting_line.strip()
    sign_lines = sign_off.replace("\r\n", "\n").strip().split("\n")
    if not lines or lines[0].strip() != exp_g:
        return False
    if len(lines) < 1 + len(sign_lines):
        return False
    tail = [x.strip() for x in lines[-len(sign_lines) :]]
    exp_tail = [x.strip() for x in sign_lines]
    return tail == exp_tail


def deterministic_post_humanize_violations(
    body: str,
    first_name: str,
    *,
    greeting_line: str | None = None,
    sign_off: str | None = None,
    warn_hi: int = PB_WORD_WARN_HI,
) -> list[str]:
    """Return human-readable violation messages, or empty if OK."""
    bad: list[str] = []
    t = body.lower()
    for p in _POST_BANNED:
        if p in t:
            bad.append(f"banned_substring:{p}")
    meta = pb_body_length_analysis(body, first_name or "", warn_hi=warn_hi)
    if meta["length_status"] == "hard_fail":
        bad.append(f"length_exceeds_{warn_hi}:{meta['body_word_count']}")
    if greeting_line is not None and sign_off is not None:
        if not body_envelope_matches(body, greeting_line, sign_off):
            bad.append("greeting_or_sign_off_changed")
    else:
        g = _first_line_greeting(body)
        if first_name and g:
            if not re.match(
                r"^Hi\s+" + re.escape(first_name.strip()) + r"\s*,?\s*$",
                g,
                re.I,
            ):
                bad.append("greeting_first_line_changed")
    return bad


def claude_light_humanize(
    email: dict[str, str],
    *,
    greeting_line: str,
    sign_off: str,
    api_key: str | None,
) -> tuple[str, str]:
    """Returns (body, changes_summary). Subject is never modified by this call."""
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY is missing")
    client = anthropic.Anthropic(api_key=api_key)
    user = (
        "CURRENT EMAIL:\n"
        f"Subject: {email.get('subject', '')}\n"
        f"Body:\n{email.get('body', '')}\n"
    )
    prompt = LIGHT_HUMANIZE_SYSTEM.format(
        greeting_line=greeting_line,
        sign_off=sign_off,
    )
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=prompt,
        messages=[{"role": "user", "content": user}],
    )
    raw = resp.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    data = json.loads(raw)
    return (
        (data.get("body") or "").strip(),
        (data.get("changes_summary") or "").strip(),
    )


def build_greeting_line(first_name: str) -> str:
    return f"Hi {first_name.strip()}," if first_name else "Hi,"


def infer_first_name_from_body(body: str) -> str:
    m = re.match(r"^Hi\s+([^,\n]+),", (body or "").strip(), re.I)
    return (m.group(1) or "").strip() if m else ""


def run_humanize_jsonl_batch(
    input_path: str,
    output_path: str,
    api_key: str | None,
) -> int:
    """
    Re-read a JSONL run and apply light humanize + deterministic checks only
    (no second full critique). Returns number of lines written.
    """
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY is missing")

    n_out = 0
    with open(input_path, encoding="utf-8") as inf, open(
        output_path, "w", encoding="utf-8"
    ) as outf:
        for line in inf:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            body = rec.get("body")
            subj = rec.get("subject")
            if not body or not subj:
                outf.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n_out += 1
                continue
            fn = rec.get("first_name") or infer_first_name_from_body(body)
            greet = _first_line(body)
            sign = _default_sign_off()

            email = {"subject": subj, "body": body}
            try:
                hb, summ = claude_light_humanize(
                    email,
                    greeting_line=greet,
                    sign_off=sign,
                    api_key=api_key,
                )
            except Exception as exc:
                rec["humanize_batch_error"] = str(exc)
                rec["humanize_applied"] = False
                rec["humanize_reason"] = "fallback_to_original"
                outf.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n_out += 1
                continue

            viol = deterministic_post_humanize_violations(
                hb, fn, greeting_line=greet, sign_off=sign,
            )
            rec["original_body_batch"] = body
            rec["humanized_body"] = hb
            rec["humanize_batch_violations"] = viol
            if viol:
                rec["humanize_applied"] = False
                rec["humanize_reason"] = "fallback_to_original"
            else:
                rec["humanize_applied"] = True
                rec["humanize_reason"] = summ or "applied"
                rec["body"] = hb
            outf.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n_out += 1
    return n_out


def _first_line(body: str) -> str:
    lines = body.replace("\r\n", "\n").split("\n")
    return lines[0].strip() if lines else ""


def _default_sign_off() -> str:
    name = os.environ.get("SENDER_FIRST_NAME", "Jamie")
    return f"{name}\nbuilding Helix"
