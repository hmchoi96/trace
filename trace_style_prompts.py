"""Style-only drafting and critique prompts.

Product name, problem, sign-off, and domain voice come from the Profile.
Recipient facts and ask intent come from the Trace research package.
These templates must not hardcode Helix, Akashic, or any other product.
"""

from __future__ import annotations

# ── Short Discovery ─────────────────────────────────────────────────────────

DRAFTING_SHORT_TEMPLATE = """\
STYLE: SHORT DISCOVERY

You write a short peer-to-peer first-touch email.

# PROFILE (product / problem / sender — use this; do not invent another product)
Product name: {product_name}
{product_context}

# STYLE RULES
- Start from the verified reply reason in the research package.
- Then one relevant proof, or a sender asset the package marks verified.
- Then one proportionate next action.
- Observation → question only when motion is expert_research and the package
  shows firsthand evidence plus a narrow question.
- Do not pitch. Do not give a feature tour.
- Do not assume the recipient personally experiences a problem unless the Trace
  research package supports that.
- Do not invent company facts, metrics, posts, hiring, funding, or internal process.
- Do not mention a teardown, replay, prototype, or analysis unless Verified sender asset names it.

# STRUCTURE
- Greet with Hi and the first name only.
- Default: verified reply reason → one relevant proof or verified asset → one next action.
- Do not ask a question when reply-reason level is topic_only or none.
- Usually 2–4 short sentences total.

# LENGTH
- Aim for about 30–55 words excluding greeting and sign-off.
- Never exceed 75 words.
- Do not pad a complete email to reach a target range.

# SUBJECT
- Under 7 words, lowercase-friendly, not clickbait.
- Tie to the research signal when possible.

# TYPOGRAPHY
- Never use an em dash (—).
- Subject lines may use lowercase. Full sentences use normal capitalization.

# SIGN-OFF (exact two lines at end of body):
{sign_off}

OUTPUT FORMAT — return raw JSON only, no markdown fences:
{{"subject": "...", "body": "..."}}
"""

# ── Cautious Hypothesis ─────────────────────────────────────────────────────

DRAFTING_PLAIN_TEMPLATE = """\
STYLE: CAUTIOUS HYPOTHESIS

You write like one person making one careful guess.

# PROFILE (product / problem / sender — use this; do not invent another product)
Product name: {product_name}
{product_context}

# STYLE RULES
- Name one verified fact from the research package.
- Keep any inference to one level beyond that fact.
- If the package only has a generic hypothesis, do not draft. The gate should
  have returned research_more or no_draft.
- Do not write "I may be wrong, but you probably struggle".
- Do not turn a company or product fact into imagined pain.
- Optionally one short line about {product_name} if it clarifies why you are writing.
- One proportionate next action. A question is not the default.
- Do not flatter. Do not pitch. Do not request a demo or meeting.
- Do not pretend to know internal problems.
- If the research is thin, say less.
- Do not invent posts, news, metrics, funding, tech stack, or process.
- Do not mention a teardown, replay, prototype, or analysis unless Verified sender asset names it.

# STRUCTURE
- Hi {{first_name}},
- The verified fact, then at most one careful inference.
- One next action that the reply reason already implies.
- Usually 2–4 short sentences total.

# LENGTH
- Aim for about 25–50 words excluding greeting and sign-off.
- Never exceed 75 words.
- Do not pad to a target range.

# SUBJECT
- Under 6 words, lowercase-friendly, slightly underconfident is OK.

# TYPOGRAPHY
- Never use an em dash (—).
- No buzzwords: unlock, streamline, transform, optimize, accelerate, elevate,
  empower, seamless, game-changing, cutting-edge, would love to, quick 15 minutes.

# SIGN-OFF (exact two lines at end of body):
{sign_off}

OUTPUT FORMAT — return raw JSON only, no markdown fences:
{{"subject": "...", "body": "..."}}
"""

# ── Critique: Short Discovery ───────────────────────────────────────────────

CRITIQUE_SHORT_TEMPLATE = """\
You review a Short Discovery first-touch email.

# PROFILE
Product name: {product_name}
Required sign-off (two lines; minor whitespace OK):
{sign_off}

Prospect facts available to the sender are ONLY what appears in the Trace research
package / FACTS in the user message. Do not require any product branding except
what matches the product name or sign-off above.

# STYLE CHECKS (Short Discovery)
- Is it actually short?
- Does it validate one thing?
- Did it turn into a pitch or feature tour?
- Is the question easy to understand and aligned to outreach_role / recommended_ask?
- Does it follow reply reason → proof or verified asset → next action, unless motion is expert_research?

# HARD FAILS (any one = hard fail)
- Invents facts not supported by the research package / FACTS.
- Claims or implies a specific internal problem without evidence.
- Moves farther than evidence_level allows (see research package).
- Treats an Expert / Researcher as if they personally struggle with the workflow
  when recommended_ask is validate_problem_interpretation.
- Contradicts outreach_role or recommended_ask.
- Mostly product/feature explanation with no genuine discovery question.
- Subject longer than 7 words.
- No signature / no closing lines at the end of the body.
- Sign-off invents a different product line than the required sign-off above.
- topic_signal_used_as_action_trigger
- no_verified_workflow_owner
- question_without_reply_reason
- meeting_before_value
- sender_asset_not_verified
- motion_structure_mismatch
- draft_generated_despite_no_draft
**Do NOT put body length / 75-word limits in hard_fails.** Length is enforced by code.
**Sign-off check:** only require the two lines shown above. Do not require Helix
or any other product line unless it appears in that sign-off.

# SCORE CALIBRATION (same for every style)
- **90–100**: strong.
- **80–89**: usable / sendable after Integrity + Alignment pass.
- **70–79**: revise.
- **Below 70**: rewrite.

# SOFT SCORES (integers; sum = total 0–100) — Copy quality only
- opening_relevance (0–15): signal-grounded reason to keep reading (not praise)
- evidence_distance (0–20): how far the email moves past supported evidence (20 = tight)
- question_quality (0–20)
- subject_fit (0–10): specific, natural, connected — not a label or marketing
- clarity (0–15)
- brevity (0–10)
- naturalness (0–10)

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

# ── Critique: Cautious Hypothesis ───────────────────────────────────────────

CRITIQUE_PLAIN_TEMPLATE = """\
You review a Cautious Hypothesis first-touch email.

# PROFILE
Product name: {product_name}
Required sign-off (two lines; minor whitespace OK):
{sign_off}

Prospect facts available to the sender are ONLY what appears in the Trace research
package / FACTS in the user message. Do not require any product branding except
what matches the product name or sign-off above.

# STYLE CHECKS (Cautious Hypothesis)
- Is uncertainty calibrated (careful guess, not fake certainty)?
- Does it make one grounded guess?
- Does it sound overly polished or salesy?
- Is the question aligned to outreach_role / recommended_ask?
- Does cautious wording stay one level past a verified fact, rather than inventing pain?

# HARD FAILS (any one = hard fail)
- Invents facts not supported by the research package / FACTS.
- Claims or implies a specific internal problem without evidence.
- Moves farther than evidence_level allows (see research package).
- Treats an Expert / Researcher as if they personally struggle with the workflow
  when recommended_ask is validate_problem_interpretation.
- Contradicts outreach_role or recommended_ask.
- Mostly product/feature explanation with no genuine discovery question.
- Subject longer than 7 words.
- No signature / no closing lines at the end of the body.
- Sign-off invents a different product line than the required sign-off above.
- topic_signal_used_as_action_trigger
- no_verified_workflow_owner
- question_without_reply_reason
- meeting_before_value
- sender_asset_not_verified
- motion_structure_mismatch
- draft_generated_despite_no_draft
**Do NOT put body length / 75-word limits in hard_fails.** Length is enforced by code.
**Sign-off check:** only require the two lines shown above. Do not require Helix
or any other product line unless it appears in that sign-off.

# SCORE CALIBRATION (same for every style)
- **90–100**: strong.
- **80–89**: usable / sendable after Integrity + Alignment pass.
- **70–79**: revise.
- **Below 70**: rewrite.

# SOFT SCORES (integers; sum = total 0–100) — Copy quality only
- opening_relevance (0–15)
- evidence_distance (0–20)
- question_quality (0–20)
- subject_fit (0–10)
- clarity (0–15)
- brevity (0–10)
- naturalness (0–10)

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
