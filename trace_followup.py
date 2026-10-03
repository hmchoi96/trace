"""Follow-up bump after a first send. Not a second first-touch pitch."""

from __future__ import annotations

from typing import Any

SYSTEM_ADDENDUM = """
# FOLLOW-UP (overrides first-touch "this is the first email")

This person already received a first email from this sender.
Write a short bump, not a new cold pitch.
Do not restart the pitch. Do not recap the product.
Do not apologize. Do not make "just checking in" the whole message.
Keep the counted body under 50 words, excluding greeting and sign-off.
Subject: reuse the original subject with Re: if it does not already start with Re:.
Keep the required two-line sign-off.
""".strip()

CRITIQUE_ADDENDUM = """
This is a follow-up, not a first-touch email.
Do not fail it for being a second email or for sounding like a bump.
Still fail product pitches in the body, em dashes, meeting asks, and a missing ask.
A complete email under 50 words is preferable to a padded one.
""".strip()


def context_block(previous_send: dict[str, Any]) -> str:
    subject = str(previous_send.get("subject") or "").strip()
    body = str(previous_send.get("body") or "").strip()
    sent_at = str(previous_send.get("sent_at") or "").strip()
    return (
        "=== FOLLOW-UP CONTEXT ===\n"
        f"Previous subject: {subject}\n"
        f"Previous body:\n{body}\n"
        f"Sent at: {sent_at}\n"
        "Write the next email in this thread. Output the same JSON as usual.\n"
        "=== end follow-up context ===\n"
    )


def apply_to_draft_prompts(
    profile: dict[str, Any] | None,
    system_prompt: str,
    user_message: str,
) -> tuple[str, str]:
    prev = (profile or {}).get("previous_send")
    if not prev:
        return system_prompt, user_message
    return (
        system_prompt + "\n\n" + SYSTEM_ADDENDUM,
        context_block(prev) + "\n" + user_message,
    )


def apply_to_critique_prompts(
    profile: dict[str, Any] | None,
    system_prompt: str,
    user_message: str,
) -> tuple[str, str]:
    prev = (profile or {}).get("previous_send")
    if not prev:
        return system_prompt, user_message
    return (
        system_prompt + "\n\n" + CRITIQUE_ADDENDUM,
        context_block(prev) + "\n" + user_message,
    )
