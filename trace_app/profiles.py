"""Profiles are the unit that binds product, search, template, and sender.

A hunt freezes the whole profile into a snapshot. Everything downstream
(enrichment, drafting, sending) must run against that same snapshot, so a
person found for one product can never receive another product's email.
"""

from __future__ import annotations

import copy
import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from . import db

TEMPLATES = {
    "legacy": {
        "id": "legacy",
        "label": "Research-Led Discovery",
        "email_mode": "legacy_email",
        "profile_kind": "legacy",
        "description": (
            "Ask one research-grounded discovery question without pitching "
            "or explaining the product."
        ),
    },
    "strategy": {
        "id": "strategy",
        "label": "Value-First Outreach",
        "email_mode": "trace_strategy_email",
        "profile_kind": "problem_validation",
        "description": (
            "Lead with a credible value exchange, evidence, and one clear ask."
        ),
    },
    "short": {
        "id": "short",
        "label": "Short Discovery",
        "email_mode": "problem_validation_email",
        "profile_kind": "problem_validation",
        "description": (
            "Short peer-to-peer note to validate one workflow pain or interpretation."
        ),
    },
    "plain": {
        "id": "plain",
        "label": "Cautious Hypothesis",
        "email_mode": "anti_ai_email",
        "profile_kind": "problem_validation",
        "description": (
            "One careful, low-pressure guess grounded in the research package."
        ),
    },
}
DEFAULT_TEMPLATE = "strategy"

# Engine profile keys that the app seeds on first run.
BUILTIN_KEYS = ("oneaway", "problem_validation", "akashic", "myzel", "myzel_pet")

BUILTIN_LABELS = {
    "oneaway": "OneAway",
    "problem_validation": "Helix",
    "akashic": "Akashic",
    "myzel": "Myzel Organics",
    "myzel_pet": "Myzel Organics (pet)",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def slugify(value: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower()).strip("_")
    return out or "profile"


def template_for(profile_json: dict[str, Any], template_id: str | None) -> dict[str, Any]:
    tid = (template_id or "").strip() or profile_json.get("app_template") or DEFAULT_TEMPLATE
    return TEMPLATES.get(tid, TEMPLATES[DEFAULT_TEMPLATE])


def sign_off_for(profile_json: dict[str, Any]) -> str:
    import os

    full = os.getenv("SENDER_FULL_NAME", "").strip()
    if full:
        company = os.getenv("SENDER_COMPANY", "Wiserbond Technologies Inc.").strip()
        return f"{full}\n{company}"
    explicit = str(profile_json.get("sign_off") or "").strip()
    if explicit:
        raw = explicit.lstrip("—–-").strip()
        if "\n" in raw:
            return raw
        if "," in raw:
            parts = [p.strip() for p in raw.split(",", 1)]
            if len(parts) == 2:
                return f"{parts[0]}\n{parts[1]}"
        return raw
    name = str(profile_json.get("app_sender_name") or "").strip()
    company = str(profile_json.get("app_sender_company") or "").strip()
    return "\n".join([p for p in (name, company) if p])


def engine_profile(
    profile_json: dict[str, Any],
    template_id: str | None = None,
) -> dict[str, Any]:
    """Bind a template style to the profile without swapping the underlying product."""
    out = copy.deepcopy(profile_json)
    tpl = template_for(profile_json, template_id)
    out["template_id"] = tpl["id"]
    out["email_mode"] = tpl["email_mode"]
    out["sign_off"] = sign_off_for(profile_json)
    out.setdefault("product_context", "")
    out.setdefault("product_name", "")
    return out


def sender_identity(profile_json: dict[str, Any]) -> dict[str, str]:
    return {
        "name": str(profile_json.get("app_sender_name") or "").strip(),
        "company": str(profile_json.get("app_sender_company") or "").strip(),
        "from_email": str(profile_json.get("app_from_email") or "").strip().lower(),
        "sign_off": sign_off_for(profile_json),
    }


def _builtin_rows() -> list[dict[str, Any]]:
    """Read the engine's built-in profiles without importing the CLI at module load."""
    import main as engine

    rows: list[dict[str, Any]] = []
    for key in BUILTIN_KEYS:
        src = engine.PRODUCT_PROFILES.get(key)
        if not src:
            continue
        pj = copy.deepcopy(src)
        discovery = pj.get("discovery") or {}
        sign_off = str(pj.get("sign_off") or "").strip()
        parts = [p.strip() for p in sign_off.replace("—", "").split("\n") if p.strip()]
        if len(parts) == 1 and "," in parts[0]:
            parts = [p.strip() for p in parts[0].split(",")]
        sender_name = parts[0] if parts else ""
        sender_company = parts[1] if len(parts) > 1 else str(pj.get("product_name") or "")
        pj["app_sender_name"] = sender_name
        pj["app_sender_company"] = sender_company
        pj["app_from_email"] = (os.getenv("SENDER_EMAIL") or "").strip().lower()
        if pj.get("profile_kind") == "legacy":
            pj["app_template"] = "legacy"
        elif pj.get("email_mode") == "trace_strategy_email":
            pj["app_template"] = DEFAULT_TEMPLATE
        else:
            pj["app_template"] = "short"
        pj["app_engine_key"] = key
        rows.append(
            {
                "id": key,
                "name": BUILTIN_LABELS.get(key, key),
                "product_name": str(discovery.get("product_name") or pj.get("product_name") or key),
                "hunt_description": str(discovery.get("target_users_or_buyers") or ""),
                "sender_name": sender_name,
                "sender_company": sender_company,
                "from_email": pj["app_from_email"],
                "default_template": pj["app_template"],
                "profile_json": pj,
                "builtin": 1,
            }
        )
    return rows


def seed_builtin_profiles(conn) -> None:
    for row in _builtin_rows():
        existing = conn.execute(
            "SELECT id FROM profiles WHERE id = ?", (row["id"],)
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE profiles
                   SET name = ?, product_name = ?, hunt_description = ?,
                       sender_name = ?, sender_company = ?, from_email = ?,
                       default_template = ?, profile_json = ?
                 WHERE id = ? AND builtin = 1
                """,
                (
                    row["name"],
                    row["product_name"],
                    row["hunt_description"],
                    row["sender_name"],
                    row["sender_company"],
                    row["from_email"],
                    row["default_template"],
                    db.dumps(row["profile_json"]),
                    row["id"],
                ),
            )
            continue
        conn.execute(
            """
            INSERT INTO profiles (id, name, product_name, hunt_description,
                                  sender_name, sender_company, from_email,
                                  default_template, profile_json, builtin, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                row["name"],
                row["product_name"],
                row["hunt_description"],
                row["sender_name"],
                row["sender_company"],
                row["from_email"],
                row["default_template"],
                db.dumps(row["profile_json"]),
                row["builtin"],
                now_iso(),
            ),
        )
    conn.commit()


def list_profiles(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM profiles ORDER BY builtin DESC, created_at ASC"
    ).fetchall()
    return [_profile_dto(dict(r)) for r in rows]


def get_profile(conn, profile_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
    if not row:
        return None
    return _profile_dto(dict(row))


def _profile_dto(row: dict[str, Any]) -> dict[str, Any]:
    pj = db.loads(row.get("profile_json"), {})
    return {
        "id": row["id"],
        "name": row["name"],
        "productName": row["product_name"],
        "huntDescription": row["hunt_description"],
        "senderName": row["sender_name"],
        "senderCompany": row["sender_company"],
        "fromEmail": row["from_email"],
        "defaultTemplate": row["default_template"],
        "builtin": bool(row["builtin"]),
        "signOff": sign_off_for(pj),
        "profile": pj,
    }


def create_profile(conn, payload: dict[str, Any]) -> dict[str, Any]:
    """Build an engine-shaped profile from the Add profile form."""
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("name is required")
    what = str(payload.get("whatItDoes") or "").strip()
    if not what:
        raise ValueError("whatItDoes is required")

    sender_name = str(payload.get("senderName") or "").strip()
    sender_company = str(payload.get("senderCompany") or "").strip()
    template = str(payload.get("template") or DEFAULT_TEMPLATE)
    if template not in TEMPLATES:
        raise ValueError(f"template must be one of {', '.join(TEMPLATES)}")

    channels: list[str] = []
    if payload.get("searchX", True):
        channels.append("x")
    if payload.get("searchWeb", True):
        channels.append("web")
    if payload.get("searchLinkedin", True):
        channels.append("linkedin")
    if not channels:
        channels = ["web"]

    sign_off = str(payload.get("signOff") or "").strip() or "\n".join(
        [p for p in (sender_name, sender_company) if p]
    )
    sender_work = str(payload.get("senderWork") or "").strip()
    outcome = str(payload.get("desiredOutcome") or "").strip()

    profile_json: dict[str, Any] = {
        "profile_kind": TEMPLATES[template]["profile_kind"],
        "email_mode": TEMPLATES[template]["email_mode"],
        "product_name": name,
        "product_context": str(payload.get("productContext") or what),
        "sign_off": sign_off,
        "sender_block": (
            "=== SENDER (verified for this campaign) ===\n"
            f"- Name: {sender_name}\n"
            f"- Company: {sender_company}\n"
            f"- Current work: {sender_work or what}\n"
            f"- Desired outcome: {outcome}\n"
            "- Constraints: no fabricated customers or metrics; research facts only\n"
            "=== end sender ==="
        ),
        "discovery": {
            "product_name": name,
            "what_it_does": what,
            "target_users_or_buyers": str(payload.get("buyers") or ""),
            "problems_it_solves": _lines(payload.get("problems")),
            "examples_of_problem_signals": _lines(payload.get("goodSignals")),
            "obvious_non_targets_or_adjacent_vendors": _lines(payload.get("skip")),
            "search_guidance": str(payload.get("searchGuidance") or ""),
            "search_channels": channels,
            "prefer_web": bool(payload.get("preferWeb")),
            "qualification_question": str(payload.get("qualify") or ""),
        },
        "app_sender_name": sender_name,
        "app_sender_company": sender_company,
        "app_from_email": str(payload.get("fromEmail") or os.getenv("SENDER_EMAIL") or "")
        .strip()
        .lower(),
        "app_template": template,
    }

    base = slugify(name)
    pid = base
    n = 2
    while conn.execute("SELECT 1 FROM profiles WHERE id = ?", (pid,)).fetchone():
        pid = f"{base}_{n}"
        n += 1

    conn.execute(
        """
        INSERT INTO profiles (id, name, product_name, hunt_description,
                              sender_name, sender_company, from_email,
                              default_template, profile_json, builtin, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
        """,
        (
            pid,
            name,
            name,
            str(payload.get("buyers") or ""),
            sender_name,
            sender_company,
            profile_json["app_from_email"],
            template,
            db.dumps(profile_json),
            now_iso(),
        ),
    )
    conn.commit()
    return get_profile(conn, pid)  # type: ignore[return-value]


def _lines(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value or "")
    return [ln.strip(" -•\t") for ln in text.splitlines() if ln.strip(" -•\t")]


def load_profile_file(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
