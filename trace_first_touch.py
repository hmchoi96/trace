"""Shared first-touch drafting philosophy for all Trace email templates.

Keep template-specific prompts thin: they should differ in angle and ask,
not restate the same length / tone / compression rules.
"""

from __future__ import annotations

# Hard ceiling for counted body (greeting + two-line sign-off excluded).
FIRST_TOUCH_WORD_MAX = 75

TRACE_FIRST_TOUCH_PHILOSOPHY = """
# FIRST-TOUCH PHILOSOPHY

Do the research and reasoning before writing.

The email is not a summary of the research.
Use the research to decide what deserves to be said.

One email should carry one main idea and one main ask.

Write the complete thought, then compress it once.
Remove anything that does not earn the reply.

Shorter is better when meaning survives.
Never pad to reach a target length.

Keep the body under 75 words.
Aim for plain, concrete English at roughly an 8th-grade reading level.
Keep necessary industry language.

Prefer 2–4 short thought blocks.
Do not enforce a fixed sentence count.

Never use an em dash.

Subject lines may be lowercase.
Use normal capitalization for full sentences, with occasional lowercase fragments only when natural.
Do not mechanically alternate capitalization.
""".strip()

TRACE_FIRST_TOUCH_STYLE_RULES = """
# FIRST-TOUCH WRITING RULES

- Never use an em dash anywhere in the subject, body, or sign-off.
- Keep the email body at or below 75 words, excluding greeting and sign-off.
- Shorter is better when the message remains clear.
- Aim for roughly an 8th-grade reading level.
- Prefer short, concrete words and sentences.
- Keep most sentences under 20 words.
- Avoid nested clauses and unnecessary setup.
- One email should carry one main idea and one main ask.
- Draft for meaning first, then compress once before output.
- Delete any sentence or phrase that does not improve relevance, credibility, understanding, or the ask.
- Do not add detail merely because research is available.
- Prefer 2–4 short thought blocks. Do not treat line count as a hard rule.
- Subject lines may naturally use lowercase.
- Full grammatical sentences should normally use standard capitalization.
- Short fragments or standalone conversational lines may occasionally begin lowercase when natural.
- Do not force capitalization variation mechanically.
""".strip()

TRACE_FIRST_TOUCH_LENGTH_CHECK = """
# FIRST-TOUCH LENGTH CHECK

- Body must be <=75 words excluding greeting and sign-off.
- Do not fail an email merely because it is below a target range.
- A complete 30-word email is preferable to a padded 55-word email.
- If the email exceeds 60 words, check whether it can be compressed.
- Never add filler during revision.
""".strip()

TRACE_FIRST_TOUCH_READABILITY_CHECK = """
# READABILITY CHECK

Fail or revise when:
- sentences are needlessly long,
- clauses are stacked,
- formal words replace simpler ones without reason,
- the email reads like a memo rather than a cold email.

Do not penalize necessary industry terminology.
""".strip()


def with_first_touch_rules(prompt: str) -> str:
    """Append shared style + philosophy to a drafting system prompt."""
    base = (prompt or "").rstrip()
    return (
        f"{base}\n\n---\n\n{TRACE_FIRST_TOUCH_PHILOSOPHY}"
        f"\n\n{TRACE_FIRST_TOUCH_STYLE_RULES}\n"
    )


def with_first_touch_critique_checks(prompt: str) -> str:
    """Append shared length + readability checks to a critique system prompt."""
    base = (prompt or "").rstrip()
    return (
        f"{base}\n\n---\n\n{TRACE_FIRST_TOUCH_LENGTH_CHECK}"
        f"\n\n{TRACE_FIRST_TOUCH_READABILITY_CHECK}\n"
    )
