"""Import legacy CLI JSONL runs into the Trace app database.

Usage:
  python3 -m trace_app.import_runs
  python3 -m trace_app.import_runs runs/signals_20260825T124114Z_akashic_default.jsonl
"""

from __future__ import annotations

import glob
import os
import sys
from typing import Any

from . import db, profiles, service

RUNS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "runs")

# CLI --list name → app profile id
LIST_TO_PROFILE = {
    "akashic": "akashic",
    "helix": "problem_validation",
    "problem_validation": "problem_validation",
    "myzel": "myzel",
    "myzel_pet": "myzel_pet",
    "oneaway": "oneaway",
}


def _decision(rec: dict[str, Any]) -> str:
    status = (rec.get("human_status") or "PENDING").upper()
    if status == "APPROVED":
        return "yes"
    if status == "REJECTED":
        return "no"
    return "pending"


def _found_on(rec: dict[str, Any]) -> str:
    source = str(rec.get("signal_source") or "").lower()
    if source == "x":
        return "X"
    if source == "linkedin":
        return "LinkedIn"
    return "Web"


def import_file(conn, path: str) -> tuple[str, int, int]:
    """Returns (profile_id, added, skipped)."""
    from signal_discovery import load_candidates
    from trace_app import service

    rows = load_candidates(path)
    if not rows:
        return "", 0, 0

    profile_id = LIST_TO_PROFILE.get(str(rows[0].get("list") or ""), "")
    if not profile_id:
        profile_id = str(rows[0].get("profile_key") or "")
    if not profile_id:
        return "", 0, len(rows)

    if not conn.execute("SELECT 1 FROM profiles WHERE id = ?", (profile_id,)).fetchone():
        return profile_id, 0, len(rows)

    added = skipped = 0
    for rec in rows:
        cid = str(rec.get("candidate_id") or "")
        if not cid:
            skipped += 1
            continue
        if service._find_existing_candidate(conn, profile_id, rec):
            skipped += 1
            continue
        entity = service._canonical_entity_key(rec) or str(rec.get("entity_key") or "")

        decision = _decision(rec)
        email = str(rec.get("email") or "").strip()
        conn.execute(
            """
            INSERT INTO candidates (id, hunt_id, profile_id, name, title, company,
                                    found_on, entity_key, decision, outcome,
                                    email, email_source, enrich_state,
                                    candidate_json, created_at, decided_at)
            VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cid,
                profile_id,
                str(rec.get("name") or rec.get("author_name") or ""),
                str(rec.get("title") or ""),
                str(rec.get("company") or ""),
                _found_on(rec),
                entity,
                decision,
                rec.get("file_outcome"),
                email,
                "Apollo" if email else "",
                "found" if email else "none",
                db.dumps(rec),
                rec.get("ts") or service.now_iso(),
                rec.get("human_decided_at"),
            ),
        )
        added += 1
    conn.commit()
    return profile_id, added, skipped


def import_cost_logs(conn) -> int:
    from signal_discovery import load_research_costs

    imported = 0
    for path in sorted(glob.glob(os.path.join(RUNS_DIR, "research_cost_*.jsonl"))):
        # research_cost_20260825T124114Z_akashic_default.jsonl
        base = os.path.basename(path)
        parts = base.replace("research_cost_", "").replace(".jsonl", "").split("_")
        profile_id = ""
        for key in ("akashic", "helix", "myzel_pet", "myzel", "oneaway", "problem_validation"):
            if key in parts:
                profile_id = LIST_TO_PROFILE.get(key, key)
                break
        if not profile_id:
            continue
        for event in load_research_costs(path):
            rid = str(event.get("run_id") or path)
            exists = conn.execute(
                "SELECT 1 FROM cost_events WHERE id = ?",
                (f"legacy_{rid}_{event.get('stage')}_{imported}",),
            ).fetchone()
            if exists:
                continue
            conn.execute(
                """
                INSERT INTO cost_events (id, profile_id, hunt_id, stage, cost_usd,
                                         elapsed_sec, created_at)
                VALUES (?, ?, NULL, ?, ?, ?, ?)
                """,
                (
                    profiles.new_id("cost"),
                    profile_id,
                    str(event.get("stage") or ""),
                    float(event.get("cost_usd") or 0),
                    float(event.get("elapsed_sec") or 0),
                    service.now_iso(),
                ),
            )
            imported += 1
    conn.commit()
    return imported


def main(argv: list[str] | None = None) -> int:
    paths = argv if argv is not None else sys.argv[1:]
    if not paths:
        paths = sorted(glob.glob(os.path.join(RUNS_DIR, "signals_*.jsonl")))

    conn = service.init()
    total_added = total_skipped = 0
    by_profile: dict[str, int] = {}

    for path in paths:
        if not os.path.isfile(path):
            print(f"skip (missing): {path}")
            continue
        profile_id, added, skipped = import_file(conn, path)
        if not profile_id:
            print(f"skip (unknown profile): {path}")
            continue
        total_added += added
        total_skipped += skipped
        by_profile[profile_id] = by_profile.get(profile_id, 0) + added
        print(f"{os.path.basename(path)} → {profile_id}: +{added} (skipped {skipped})")

    merged = service.dedupe_all_profiles(conn)
    merged_total = sum(merged.values())
    if merged_total:
        print(f"\nMerged {merged_total} duplicate rows after import.")

    costs = import_cost_logs(conn)
    print(f"\nImported {total_added} people, {costs} cost events.")
    for pid, n in sorted(by_profile.items()):
        print(f"  {pid}: {n}")
    if not total_added:
        print("\nNote: OneAway has no legacy signals file. Switch to Akashic or Helix to see imported data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
