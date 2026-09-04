"""Rules that must hold before Trace spends money or sends mail.

These exist because the failure that matters is not a crash. It is a person
found for one product receiving another product's email.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any


class GuardError(Exception):
    """Raised when an action would violate a Trace safety rule."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def apollo_ready() -> bool:
    return bool((os.getenv("APOLLO_API_KEY") or "").strip())


def hunter_ready() -> bool:
    return bool((os.getenv("HUNTER_API_KEY") or "").strip())


def contact_lookup_ready() -> bool:
    return apollo_ready() or hunter_ready()


def connected_mailbox() -> str:
    return (os.getenv("SENDER_EMAIL") or "").strip().lower()


def assert_apollo() -> None:
    if not apollo_ready():
        raise GuardError(
            "no_apollo",
            "Apollo is not connected. Add APOLLO_API_KEY to .env first.",
        )


def mailbox_ready() -> bool:
    return bool(
        connected_mailbox()
        and os.getenv("AZURE_TENANT_ID")
        and os.getenv("AZURE_CLIENT_ID")
        and os.getenv("AZURE_CLIENT_SECRET")
    )


def assert_same_profile(*, expected: str, **actual: str) -> None:
    for label, value in actual.items():
        if (value or "") != expected:
            raise GuardError(
                "profile_mismatch",
                f"{label} belongs to profile '{value}', expected '{expected}'. "
                "Trace will not mix products.",
            )


def assert_snapshot_matches(snapshot: dict[str, Any], profile_json: dict[str, Any]) -> None:
    """A hunt's frozen product must still be the product being emailed."""
    snap_product = str(snapshot.get("product_name") or "").strip()
    live_product = str(profile_json.get("product_name") or "").strip()
    if snap_product and live_product and snap_product != live_product:
        raise GuardError(
            "snapshot_mismatch",
            f"This person was found for '{snap_product}' but the profile now sells "
            f"'{live_product}'. Start a new hunt instead.",
        )


def assert_approved(candidate: dict[str, Any]) -> None:
    if candidate.get("decision") != "yes":
        raise GuardError(
            "not_approved",
            "Trace only looks up contacts and writes drafts after you say yes.",
        )


def assert_sendable(draft: dict[str, Any]) -> None:
    if draft.get("superseded"):
        raise GuardError("draft_superseded", "This draft was replaced by a newer one.")
    if not draft.get("sendable"):
        raise GuardError(
            "draft_not_ready",
            "This draft did not clear the quality check. Regenerate it first.",
        )


def assert_sender_matches(profile_from: str, mailbox: str) -> None:
    profile_from = (profile_from or "").strip().lower()
    mailbox = (mailbox or "").strip().lower()
    if not mailbox:
        raise GuardError("no_mailbox", "No mailbox is connected.")
    if profile_from and profile_from != mailbox:
        raise GuardError(
            "sender_mismatch",
            f"This profile sends as {profile_from} but the connected mailbox is "
            f"{mailbox}.",
        )


def idempotency_key(candidate_id: str, subject: str, body: str) -> str:
    digest = hashlib.sha256(
        "\x00".join([candidate_id, subject or "", body or ""]).encode("utf-8")
    ).hexdigest()
    return f"{candidate_id}:{digest[:16]}"
