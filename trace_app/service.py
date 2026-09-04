"""Application services. The UI talks to these, never to the engine directly."""

from __future__ import annotations

import copy
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable

from . import db, drafting, guards, profiles

RUNS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "runs", "app")

HUNT_LIMITS = (3, 5, 8, 12, 20)

STAGE_LABELS = {
    "queued": "Waiting to start",
    "starting": "Preparing this hunt",
    "search": "Searching public posts",
    "web": "Searching the web",
    "x": "Searching X",
    "linkedin": "Searching LinkedIn",
    "qualify": "Qualifying people",
    "qualification": "Qualifying people",
    "deepening": "Deepening research",
    "saving": "Saving results",
    "done": "Finished",
}

STAGE_PROGRESS = {
    "queued": 0,
    "starting": 5,
    "search": 15,
    "web": 22,
    "x": 28,
    "linkedin": 34,
    "qualify": 55,
    "qualification": 55,
    "deepening": 75,
    "saving": 90,
    "done": 100,
    "failed": 100,
}


def estimate_hunt_seconds(limit: int) -> int:
    """Rough wall-clock guess for the UI. Grok hunts are usually a few minutes."""
    return int(50 + max(1, limit) * 38)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init(path: str | None = None):
    conn = db.connect(path)
    profiles.seed_builtin_profiles(conn)
    return conn


def _runs_path(name: str) -> str:
    os.makedirs(RUNS_DIR, exist_ok=True)
    return os.path.join(RUNS_DIR, name)


# ── Hunts ───────────────────────────────────────────────────────────────────


def create_hunt(conn, profile_id: str, limit: int) -> dict[str, Any]:
    profile = profiles.get_profile(conn, profile_id)
    if not profile:
        raise guards.GuardError("no_profile", f"Unknown profile '{profile_id}'.")
    if limit not in HUNT_LIMITS:
        raise guards.GuardError(
            "bad_limit", f"Hunt size must be one of {', '.join(map(str, HUNT_LIMITS))}."
        )

    hunt_id = profiles.new_id("hunt")
    snapshot = copy.deepcopy(profile["profile"])
    estimate = estimate_hunt_seconds(limit)
    conn.execute(
        """
        INSERT INTO hunts (id, profile_id, snapshot_json, limit_n, status,
                           current_stage, estimate_sec, created_at)
        VALUES (?, ?, ?, ?, 'queued', 'queued', ?, ?)
        """,
        (hunt_id, profile_id, db.dumps(snapshot), limit, estimate, now_iso()),
    )
    append_hunt_event(conn, hunt_id, "queued", STAGE_LABELS["queued"])
    job_id = enqueue_job(
        conn, "hunt", profile_id=profile_id, hunt_id=hunt_id, payload={"limit": limit}
    )
    conn.commit()
    return {"huntId": hunt_id, "jobId": job_id}


def cancel_hunt(conn, hunt_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM hunts WHERE id = ?", (hunt_id,)).fetchone()
    if not row:
        raise guards.GuardError("no_hunt", f"Unknown hunt '{hunt_id}'.")
    hunt = dict(row)
    status = str(hunt["status"])
    if status == "cancelled":
        return {"huntId": hunt_id, "cancelled": True}
    running_job = conn.execute(
        "SELECT id FROM jobs WHERE hunt_id = ? AND status = 'running' LIMIT 1",
        (hunt_id,),
    ).fetchone()
    if status == "running" or running_job:
        raise guards.GuardError(
            "hunt_running",
            "This hunt is already running. Trace cannot stop a search in progress.",
        )
    if status in ("done", "failed"):
        return {"huntId": hunt_id, "cancelled": False, "alreadyFinished": True}
    finished = now_iso()
    conn.execute(
        """
        UPDATE hunts SET status = 'cancelled', error = ?, finished_at = ?, current_stage = 'cancelled'
        WHERE id = ?
        """,
        ("Cancelled before it started.", finished, hunt_id),
    )
    conn.execute(
        """
        UPDATE jobs SET status = 'cancelled', error = ?, finished_at = ?
        WHERE hunt_id = ? AND status = 'queued'
        """,
        ("Cancelled.", finished, hunt_id),
    )
    append_hunt_event(conn, hunt_id, "cancelled", "Cancelled before it started")
    conn.commit()
    return {"huntId": hunt_id, "cancelled": True}


def append_hunt_event(conn, hunt_id: str, stage: str, message: str) -> None:
    conn.execute(
        """
        INSERT INTO hunt_events (id, hunt_id, stage, message, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (profiles.new_id("evt"), hunt_id, stage, message, now_iso()),
    )
    conn.execute(
        "UPDATE hunts SET current_stage = ? WHERE id = ?",
        (stage, hunt_id),
    )
    conn.commit()


def _job_id_for_hunt(conn, hunt_id: str) -> str | None:
    row = conn.execute(
        "SELECT id FROM jobs WHERE hunt_id = ? ORDER BY created_at DESC LIMIT 1",
        (hunt_id,),
    ).fetchone()
    return row["id"] if row else None


def _normalize_stage(stage: str) -> str:
    if stage.startswith("discovery_"):
        channel = stage.removeprefix("discovery_")
        if channel in STAGE_LABELS:
            return channel
        return "search"
    if stage == "qualification":
        return "qualify"
    return stage


def _stage_message(stage: str, person_name: str = "") -> str:
    normalized = _normalize_stage(stage)
    label = STAGE_LABELS.get(normalized, stage.replace("_", " ").capitalize())
    person = person_name.strip()
    if person and normalized in ("qualify", "deepening"):
        return f"{label}: {person}"
    return label


def _hunt_progress_pct(stage: str) -> int:
    normalized = _normalize_stage(stage)
    return STAGE_PROGRESS.get(normalized, STAGE_PROGRESS.get(stage, 0))


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _hunt_timing(hunt: dict[str, Any]) -> dict[str, int]:
    estimate = int(hunt.get("estimate_sec") or 0)
    anchor = _parse_iso(hunt.get("started_at")) or _parse_iso(hunt.get("created_at"))
    elapsed = 0
    if anchor:
        elapsed = max(0, int((datetime.now(timezone.utc) - anchor).total_seconds()))
    status = str(hunt.get("status") or "")
    remaining = max(0, estimate - elapsed) if status in ("queued", "running") else 0
    return {"estimateSec": estimate, "elapsedSec": elapsed, "remainingSec": remaining}


def _on_hunt_stage(hunt_id: str, job_id: str | None, stage: str, person_name: str = "") -> None:
    # run_discovery calls this from thread-pool workers; each thread needs its own connection.
    conn = db.connect()
    normalized = _normalize_stage(stage)
    message = _stage_message(stage, person_name)
    append_hunt_event(conn, hunt_id, normalized, message)
    if job_id:
        set_job_progress(conn, job_id, message)


def run_hunt(conn, hunt_id: str, researcher: Callable[..., Any] | None = None) -> int:
    row = conn.execute("SELECT * FROM hunts WHERE id = ?", (hunt_id,)).fetchone()
    if not row:
        raise guards.GuardError("no_hunt", f"Unknown hunt '{hunt_id}'.")
    hunt = dict(row)
    profile_id = hunt["profile_id"]
    snapshot = db.loads(hunt["snapshot_json"], {})
    job_id = _job_id_for_hunt(conn, hunt_id)

    from signal_discovery import run_discovery

    started = now_iso()
    conn.execute(
        "UPDATE hunts SET status = 'running', started_at = ? WHERE id = ?",
        (started, hunt_id),
    )
    append_hunt_event(conn, hunt_id, "starting", STAGE_LABELS["starting"])
    if job_id:
        set_job_progress(conn, job_id, STAGE_LABELS["starting"])

    seed_path = _runs_path(f"{profile_id}.candidates.jsonl")
    cache_path = _runs_path(f"research_cache_{profile_id}.jsonl")
    cost_path = _runs_path(f"cost_{hunt_id}.jsonl")

    _export_candidates_jsonl(conn, profile_id, seed_path)

    engine_key = str(snapshot.get("app_engine_key") or profile_id)

    def on_stage(stage: str, person_name: str = "") -> None:
        _on_hunt_stage(hunt_id, job_id, stage, person_name)

    candidates = run_discovery(
        snapshot,
        list_name=profile_id,
        profile_key=engine_key,
        limit=int(hunt["limit_n"]),
        researcher=researcher,
        cost_log_path=cost_path,
        run_id=hunt_id,
        cache_path=cache_path,
        seed_candidate_paths=[seed_path] if os.path.isfile(seed_path) else None,
        on_stage=on_stage,
    )

    append_hunt_event(conn, hunt_id, "saving", STAGE_LABELS["saving"])
    if job_id:
        set_job_progress(conn, job_id, STAGE_LABELS["saving"])

    added = _store_candidates(conn, hunt_id, profile_id, candidates)
    dedupe_candidates(conn, profile_id)
    _import_cost_events(conn, profile_id, hunt_id, cost_path)
    append_hunt_event(conn, hunt_id, "done", f"Found {added} new people")
    conn.execute(
        "UPDATE hunts SET status = 'done', finished_at = ?, current_stage = 'done' WHERE id = ?",
        (now_iso(), hunt_id),
    )
    conn.commit()
    _export_candidates_jsonl(conn, profile_id, seed_path)
    return added


def _normalize_person_name(name: str) -> str:
    """Match key: strip parenthetical aliases, lowercase, collapse spaces."""
    s = re.sub(r"\s*\([^)]*\)", "", str(name or "").strip())
    return " ".join(s.lower().split())


def _normalize_company_for_dedupe(company: str) -> str:
    """Primary org label before slash or parenthetical qualifiers."""
    s = str(company or "").strip()
    s = re.split(r"\s*[\(/]", s)[0].strip()
    return " ".join(s.lower().split())


def _record_email(row: dict[str, Any], rec: dict[str, Any] | None = None) -> str:
    rec = rec or {}
    return str(row.get("email") or rec.get("email") or "").strip().lower()


def _record_handle(rec: dict[str, Any]) -> str:
    from signal_discovery import normalize_handle

    return normalize_handle(
        str(rec.get("author_handle") or ""),
        str(rec.get("signal_url") or rec.get("linkedin_url") or ""),
    )


def _canonical_entity_key(rec: dict[str, Any]) -> str:
    """Stable person key for dedupe: normalized name|company, else handle."""
    from signal_discovery import entity_key_for

    stored = str(rec.get("entity_key") or "").strip()
    if stored.startswith("h:"):
        return stored

    name = _normalize_person_name(str(rec.get("name") or rec.get("author_name") or ""))
    company = _normalize_company_for_dedupe(str(rec.get("company") or ""))
    if name and company:
        return entity_key_for(name, company)
    handle = _record_handle(rec)
    if handle:
        return f"h:{handle}"
    if stored:
        return stored
    if name:
        return entity_key_for(name, "")
    return ""


def _find_existing_candidate(conn, profile_id: str, rec: dict[str, Any]) -> str | None:
    cid = str(rec.get("candidate_id") or "")
    if cid:
        row = conn.execute("SELECT id FROM candidates WHERE id = ?", (cid,)).fetchone()
        if row:
            return row["id"]

    entity = _canonical_entity_key(rec)
    if entity:
        row = conn.execute(
            """
            SELECT id FROM candidates
             WHERE profile_id = ? AND entity_key != '' AND entity_key = ?
            """,
            (profile_id, entity),
        ).fetchone()
        if row:
            return row["id"]

    email = _record_email(rec, rec)
    if email:
        row = conn.execute(
            """
            SELECT id FROM candidates
             WHERE profile_id = ? AND lower(trim(coalesce(email, ''))) = ?
             LIMIT 1
            """,
            (profile_id, email),
        ).fetchone()
        if row:
            return row["id"]

    handle = _record_handle(rec)
    if handle:
        row = conn.execute(
            """
            SELECT id FROM candidates
             WHERE profile_id = ? AND entity_key = ?
             LIMIT 1
            """,
            (profile_id, f"h:{handle}"),
        ).fetchone()
        if row:
            return row["id"]

    nname = _normalize_person_name(str(rec.get("name") or rec.get("author_name") or ""))
    ncompany = _normalize_company_for_dedupe(str(rec.get("company") or ""))
    if nname and ncompany:
        for row in conn.execute(
            "SELECT id, name, company FROM candidates WHERE profile_id = ?",
            (profile_id,),
        ):
            if (
                _normalize_person_name(row["name"]) == nname
                and _normalize_company_for_dedupe(row["company"]) == ncompany
            ):
                return row["id"]

    name = str(rec.get("name") or rec.get("author_name") or "").strip().lower()
    company = str(rec.get("company") or "").strip().lower()
    if name and company:
        row = conn.execute(
            """
            SELECT id FROM candidates
             WHERE profile_id = ?
               AND lower(trim(name)) = ?
               AND lower(trim(company)) = ?
             LIMIT 1
            """,
            (profile_id, name, company),
        ).fetchone()
        if row:
            return row["id"]
    return None


def _candidate_richness(conn, row: dict[str, Any]) -> tuple:
    rec = db.loads(row.get("candidate_json") or "{}", {})
    has_draft = conn.execute(
        """
        SELECT 1 FROM drafts
         WHERE candidate_id = ? AND superseded = 0 AND trim(coalesce(body, '')) != ''
        """,
        (row["id"],),
    ).fetchone()
    has_sent = conn.execute(
        "SELECT 1 FROM sends WHERE candidate_id = ? LIMIT 1", (row["id"],)
    ).fetchone()
    email = str(row.get("email") or rec.get("email") or "").strip()
    phone = str(row.get("phone") or "").strip()
    return (
        1 if has_draft else 0,
        1 if has_sent else 0,
        1 if email else 0,
        1 if phone else 0,
        1 if row.get("decision") == "yes" else 0,
        str(row.get("created_at") or ""),
    )


def _merge_duplicate_into(conn, keep_id: str, dup: dict[str, Any]) -> None:
    keep = get_candidate(conn, keep_id)
    if not keep:
        return
    keep_rec = db.loads(keep.get("candidate_json") or "{}", {})
    dup_rec = db.loads(dup.get("candidate_json") or "{}", {})
    email = str(keep.get("email") or keep_rec.get("email") or dup.get("email") or dup_rec.get("email") or "").strip()
    phone = str(keep.get("phone") or keep_rec.get("phone") or dup.get("phone") or dup_rec.get("phone") or "").strip()
    email_source = str(keep.get("email_source") or dup.get("email_source") or "")
    phone_source = str(keep.get("phone_source") or dup.get("phone_source") or "")
    entity = str(keep.get("entity_key") or _canonical_entity_key(keep_rec) or _canonical_entity_key(dup_rec) or "")
    keep_rec.setdefault("additional_signals", [])
    dup_signals = dup_rec.get("additional_signals") or dup_rec.get("signals") or []
    if isinstance(dup_signals, list):
        keep_rec["additional_signals"] = (keep_rec.get("additional_signals") or []) + dup_signals
    conn.execute(
        """
        UPDATE candidates
           SET email = ?, email_source = ?, phone = ?, phone_source = ?,
               entity_key = ?, candidate_json = ?
         WHERE id = ?
        """,
        (email, email_source, phone, phone_source, entity, db.dumps(keep_rec), keep_id),
    )
    dup_id = dup["id"]
    for table in ("drafts", "sends", "jobs"):
        conn.execute(
            f"UPDATE {table} SET candidate_id = ? WHERE candidate_id = ?",
            (keep_id, dup_id),
        )
    conn.execute("DELETE FROM candidates WHERE id = ?", (dup_id,))


def _backfill_entity_keys(conn, profile_id: str) -> int:
    updated = 0
    rows = conn.execute(
        "SELECT id, name, company, entity_key, candidate_json FROM candidates WHERE profile_id = ?",
        (profile_id,),
    ).fetchall()
    for row in rows:
        rec = db.loads(row["candidate_json"] or "{}", {})
        merged = {**rec, "name": row["name"], "company": row["company"]}
        key = _canonical_entity_key(merged)
        if key and key != str(row["entity_key"] or ""):
            conn.execute(
                "UPDATE candidates SET entity_key = ? WHERE id = ?",
                (key, row["id"]),
            )
            updated += 1
    return updated


def dedupe_candidates(conn, profile_id: str) -> int:
    """Merge duplicate people within one profile (name/company variants, email, handle)."""
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM candidates WHERE profile_id = ? ORDER BY created_at ASC",
        (profile_id,),
    ).fetchall()]
    if not rows:
        return 0

    parent = {r["id"]: r["id"] for r in rows}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    by_name_co: dict[tuple[str, str], list[str]] = defaultdict(list)
    by_email: dict[str, list[str]] = defaultdict(list)
    by_handle: dict[str, list[str]] = defaultdict(list)
    by_entity: dict[str, list[str]] = defaultdict(list)

    for row in rows:
        rec = db.loads(row.get("candidate_json") or "{}", {})
        rid = row["id"]
        nname = _normalize_person_name(row["name"])
        nco = _normalize_company_for_dedupe(row["company"])
        if nname and nco:
            by_name_co[(nname, nco)].append(rid)
        email = _record_email(row, rec)
        if email:
            by_email[email].append(rid)
        handle = _record_handle(rec)
        if handle:
            by_handle[handle].append(rid)
        entity = _canonical_entity_key({**rec, **row})
        if entity:
            by_entity[entity].append(rid)

    for group in (
        *by_name_co.values(),
        *by_email.values(),
        *by_handle.values(),
        *by_entity.values(),
    ):
        if len(group) < 2:
            continue
        anchor = group[0]
        for other in group[1:]:
            union(anchor, other)

    clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        clusters[find(row["id"])].append(row)

    removed = 0
    for cluster in clusters.values():
        if len(cluster) < 2:
            continue
        cluster.sort(key=lambda r: _candidate_richness(conn, r), reverse=True)
        keep = cluster[0]
        for dup in cluster[1:]:
            _merge_duplicate_into(conn, keep["id"], dup)
            removed += 1

    backfilled = _backfill_entity_keys(conn, profile_id)
    if removed or backfilled:
        conn.commit()
    return removed


def dedupe_all_profiles(conn) -> dict[str, int]:
    """Run dedupe + entity_key backfill for every profile."""
    out: dict[str, int] = {}
    for row in conn.execute("SELECT id FROM profiles ORDER BY id"):
        out[row["id"]] = dedupe_candidates(conn, row["id"])
    return out


def _store_candidates(conn, hunt_id: str, profile_id: str, rows: list[dict[str, Any]]) -> int:
    added = 0
    for rec in rows:
        cid = str(rec.get("candidate_id") or "")
        if not cid:
            continue
        if _find_existing_candidate(conn, profile_id, rec):
            continue
        entity = _canonical_entity_key(rec) or str(rec.get("entity_key") or "")
        conn.execute(
            """
            INSERT INTO candidates (id, hunt_id, profile_id, name, title, company,
                                    found_on, entity_key, candidate_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cid,
                hunt_id,
                profile_id,
                str(rec.get("name") or rec.get("author_name") or ""),
                str(rec.get("title") or ""),
                str(rec.get("company") or ""),
                _found_on(rec),
                entity,
                db.dumps(rec),
                now_iso(),
            ),
        )
        added += 1
    conn.commit()
    return added


def _found_on(rec: dict[str, Any]) -> str:
    source = str(rec.get("signal_source") or "").lower()
    if source == "x":
        return "X"
    if source == "linkedin":
        return "LinkedIn"
    return "Web"


def _export_candidates_jsonl(conn, profile_id: str, path: str) -> None:
    from signal_discovery import save_candidates

    rows = conn.execute(
        "SELECT candidate_json, decision, outcome FROM candidates WHERE profile_id = ?",
        (profile_id,),
    ).fetchall()
    out = []
    for row in rows:
        rec = db.loads(row["candidate_json"], {})
        if not rec:
            continue
        rec["human_status"] = {
            "yes": "APPROVED",
            "no": "REJECTED",
        }.get(row["decision"], "PENDING")
        if row["outcome"]:
            rec["file_outcome"] = row["outcome"]
        out.append(rec)
    if out:
        save_candidates(path, out)


def _import_cost_events(conn, profile_id: str, hunt_id: str, path: str) -> None:
    from signal_discovery import load_research_costs

    if not os.path.isfile(path):
        return
    for event in load_research_costs(path):
        conn.execute(
            """
            INSERT INTO cost_events (id, profile_id, hunt_id, stage, cost_usd,
                                     elapsed_sec, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profiles.new_id("cost"),
                profile_id,
                hunt_id,
                str(event.get("stage") or ""),
                float(event.get("cost_usd") or 0),
                float(event.get("elapsed_sec") or 0),
                now_iso(),
            ),
        )
    conn.commit()


# ── Decisions ───────────────────────────────────────────────────────────────


def decide(conn, candidate_id: str, decision: str, reason: str | None = None) -> dict[str, Any]:
    if decision not in ("yes", "no"):
        raise guards.GuardError("bad_decision", "Decision must be yes or no.")
    cand = get_candidate(conn, candidate_id)
    if not cand:
        raise guards.GuardError("no_candidate", f"Unknown person '{candidate_id}'.")
    rec = db.loads(cand["candidate_json"], {})
    rec["human_status"] = "APPROVED" if decision == "yes" else "REJECTED"
    rec["human_decided_at"] = now_iso()
    if decision == "no" and reason:
        rec["human_reject_reason"] = reason
    conn.execute(
        "UPDATE candidates SET decision = ?, decided_at = ?, candidate_json = ? WHERE id = ?",
        (decision, now_iso(), db.dumps(rec), candidate_id),
    )
    job_id = None
    if decision == "yes":
        job_id = enqueue_job(
            conn,
            "prepare",
            profile_id=cand["profile_id"],
            candidate_id=candidate_id,
            payload={},
        )
    conn.commit()
    return {"candidateId": candidate_id, "decision": decision, "jobId": job_id}


def set_outcome(conn, candidate_id: str, outcome: str | None) -> None:
    if outcome not in (None, "closed", "disqualified"):
        raise guards.GuardError("bad_outcome", "Outcome must be closed or disqualified.")
    conn.execute(
        "UPDATE candidates SET outcome = ? WHERE id = ?", (outcome, candidate_id)
    )
    conn.commit()


def add_note(conn, candidate_id: str, text: str) -> None:
    text = (text or "").strip()
    if not text:
        return
    conn.execute(
        "INSERT INTO notes (id, candidate_id, text, created_at) VALUES (?, ?, ?, ?)",
        (profiles.new_id("note"), candidate_id, text, now_iso()),
    )
    conn.commit()


# ── Contact + draft ─────────────────────────────────────────────────────────


def prepare_candidate(
    conn,
    candidate_id: str,
    *,
    template_id: str | None = None,
    matcher: Callable[..., Any] | None = None,
    drafter: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Apollo lookup then Claude draft. Only after a yes."""
    cand = get_candidate(conn, candidate_id)
    if not cand:
        raise guards.GuardError("no_candidate", f"Unknown person '{candidate_id}'.")
    guards.assert_approved(cand)

    profile = profiles.get_profile(conn, cand["profile_id"])
    if not profile:
        raise guards.GuardError("no_profile", "This person's profile is gone.")
    guards.assert_same_profile(
        expected=profile["id"], candidate=cand["profile_id"]
    )

    rec = db.loads(cand["candidate_json"], {})
    if not (rec.get("email") or "").strip():
        _run_apollo(conn, cand, rec, matcher=matcher)
        cand = get_candidate(conn, candidate_id) or cand
        rec = db.loads(cand["candidate_json"], {})

    if not (rec.get("email") or "").strip():
        return {"candidateId": candidate_id, "draftId": None, "reason": "no_contact"}

    return create_draft(
        conn, candidate_id, template_id=template_id, drafter=drafter
    )


def _run_apollo(conn, cand: dict[str, Any], rec: dict[str, Any], *, matcher=None) -> None:
    if _needs_contact(cand):
        _pull_stored_csv(rec, cand["profile_id"])
    if guards.apollo_ready():
        need_apollo = (
            not str(rec.get("email") or "").strip()
            or not str(rec.get("phone") or "").strip()
        )
        if need_apollo:
            try:
                _pull_apollo_live(rec, matcher=matcher)
            except Exception:
                rec["enrichment_attempted"] = True
    if guards.hunter_ready() and not str(rec.get("email") or "").strip():
        try:
            _pull_hunter_live(rec)
        except Exception:
            rec["enrichment_attempted"] = True
    _persist_contact(conn, cand, rec)


def _contact_sources(
    rec: dict[str, Any], cand: dict[str, Any], email: str, phone: str
) -> tuple[str, str]:
    email_source = str(rec.get("email_source") or cand.get("email_source") or "")
    phone_source = str(rec.get("phone_source") or cand.get("phone_source") or "")
    if email and not email_source:
        email_source = "Apollo"
    if phone and not phone_source:
        phone_source = "Apollo"
    return email_source, phone_source


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _contact_csv_paths(profile_id: str) -> list[str]:
    root = _repo_root()
    paths: list[str] = []
    try:
        from main import LEAD_LISTS

        base = LEAD_LISTS.get(profile_id)
        if base:
            paths.append(os.path.join(root, base))
            if base.endswith(".csv"):
                paths.append(os.path.join(root, base[:-4] + ".phones.csv"))
    except ImportError:
        pass
    for rel in {
        "akashic": [
            "runs/akashic_signal_people.csv",
            "runs/akashic_signal_people.phones.csv",
        ],
    }.get(profile_id, []):
        paths.append(os.path.join(root, rel))
    seen: set[str] = set()
    out: list[str] = []
    for path in paths:
        if path not in seen and os.path.isfile(path):
            seen.add(path)
            out.append(path)
    return out


def _load_contact_csv_rows(profile_id: str) -> list[dict[str, Any]]:
    import csv

    rows: list[dict[str, Any]] = []
    for path in _contact_csv_paths(profile_id):
        with open(path, newline="", encoding="utf-8") as fh:
            rows.extend(list(csv.DictReader(fh)))
    return rows


def _persist_contact(conn, cand: dict[str, Any], rec: dict[str, Any]) -> None:
    email = str(rec.get("email") or "").strip()
    phone = str(rec.get("phone") or "").strip()
    email_source, phone_source = _contact_sources(rec, cand, email, phone)
    conn.execute(
        """
        UPDATE candidates
           SET candidate_json = ?, email = ?, email_source = ?,
               phone = ?, phone_source = ?, enrich_state = ?
         WHERE id = ?
        """,
        (
            db.dumps(rec),
            email,
            email_source,
            phone,
            phone_source,
            "found" if email or phone else "attempted",
            cand["id"],
        ),
    )
    conn.commit()


def update_contact(
    conn,
    candidate_id: str,
    *,
    email: str | None = None,
    phone: str | None = None,
) -> dict[str, Any]:
    """Save email/phone entered manually in Records."""
    cand = get_candidate(conn, candidate_id)
    if not cand:
        raise guards.GuardError("no_candidate", f"Unknown person '{candidate_id}'.")

    rec = db.loads(cand["candidate_json"], {})
    email_source = str(cand.get("email_source") or "")
    phone_source = str(cand.get("phone_source") or "")
    prev_email = str(cand.get("email") or rec.get("email") or "").strip()
    prev_phone = str(cand.get("phone") or rec.get("phone") or "").strip()

    if email is not None:
        cleaned = email.strip()
        rec["email"] = cleaned
        if cleaned:
            if cleaned != prev_email:
                email_source = "Manual"
        else:
            email_source = ""

    if phone is not None:
        cleaned_phone = phone.strip()
        rec["phone"] = cleaned_phone
        if cleaned_phone:
            if cleaned_phone != prev_phone:
                phone_source = "Manual"
        else:
            phone_source = ""

    final_email = str(rec.get("email") or "").strip()
    final_phone = str(rec.get("phone") or "").strip()
    if not final_email and not final_phone:
        raise guards.GuardError("no_contact", "Enter an email or phone to save.")

    decision = str(cand.get("decision") or "pending")
    decided_at = cand.get("decided_at")
    # Records: saving an email means this person is ready for a draft.
    if final_email and decision != "yes":
        decision = "yes"
        decided_at = now_iso()
        rec["human_status"] = "APPROVED"
        rec["human_decided_at"] = decided_at

    conn.execute(
        """
        UPDATE candidates
           SET candidate_json = ?, email = ?, email_source = ?,
               phone = ?, phone_source = ?, enrich_state = ?,
               decision = ?, decided_at = ?
         WHERE id = ?
        """,
        (
            db.dumps(rec),
            str(rec.get("email") or "").strip(),
            email_source,
            str(rec.get("phone") or "").strip(),
            phone_source,
            "found" if (rec.get("email") or rec.get("phone")) else "none",
            decision,
            decided_at,
            candidate_id,
        ),
    )
    conn.commit()
    draft_queued = _enqueue_prepare_if_needed(conn, candidate_id)
    return {
        "candidateId": candidate_id,
        "email": str(rec.get("email") or "") or None,
        "phone": str(rec.get("phone") or "") or None,
        "emailSource": email_source or None,
        "phoneSource": phone_source or None,
        "draftQueued": draft_queued,
    }


def _needs_contact(cand: dict[str, Any]) -> bool:
    return not str(cand.get("email") or "").strip() and not str(cand.get("phone") or "").strip()


def _pull_stored_csv(rec: dict[str, Any], profile_id: str) -> bool:
    from signal_discovery import sync_contact_from_csv_rows

    return sync_contact_from_csv_rows(rec, _load_contact_csv_rows(profile_id))


def _pull_apollo_live(rec: dict[str, Any], *, matcher=None) -> bool:
    from apollo_enrich import bulk_match_people, emails_from_matches
    from signal_discovery import candidate_to_apollo_details, _apply_apollo_person

    if str(rec.get("email") or "").strip() and str(rec.get("phone") or "").strip():
        return False
    details = [candidate_to_apollo_details(rec)]
    fn = matcher or bulk_match_people
    need_phone = not str(rec.get("phone") or "").strip()
    payload = fn(
        details,
        reveal_phone_number=need_phone,
        reveal_personal_emails=True,
    )
    emails = emails_from_matches(payload if isinstance(payload, dict) else {})
    matches = payload.get("matches") if isinstance(payload, dict) else []
    if not isinstance(matches, list):
        matches = []
    person = matches[0] if matches else None
    email = emails[0] if emails else ""
    before_email = str(rec.get("email") or "").strip()
    before_phone = str(rec.get("phone") or "").strip()
    if isinstance(person, dict):
        _apply_apollo_person(rec, person, email)
    else:
        rec["enrichment_attempted"] = True
    after_email = str(rec.get("email") or "").strip()
    after_phone = str(rec.get("phone") or "").strip()
    return after_email != before_email or after_phone != before_phone


def _pull_hunter_live(rec: dict[str, Any], *, finder=None) -> bool:
    from hunter_enrich import apply_hunter_email, find_email

    if str(rec.get("email") or "").strip():
        return False
    fn = finder or find_email
    before = str(rec.get("email") or "").strip()
    try:
        data = fn(rec)
    except Exception:
        rec["enrichment_attempted"] = True
        return False
    if data:
        apply_hunter_email(rec, data)
    else:
        rec["enrichment_attempted"] = True
    return str(rec.get("email") or "").strip() != before


def _contact_lookup_source(rec: dict[str, Any]) -> str | None:
    email_source = str(rec.get("email_source") or "").strip()
    if email_source:
        return email_source
    phone_source = str(rec.get("phone_source") or "").strip()
    if phone_source:
        return phone_source
    return None


def pull_contact(
    conn,
    candidate_id: str,
    *,
    matcher: Callable[..., Any] | None = None,
    hunter_finder: Callable[..., Any] | None = None,
    use_apollo: bool = True,
) -> dict[str, Any]:
    """Fill email/phone: stored CSV, then Apollo, then Hunter.io only if email still missing."""
    cand = get_candidate(conn, candidate_id)
    if not cand:
        raise guards.GuardError("no_candidate", f"Unknown person '{candidate_id}'.")
    has_email = bool(str(cand.get("email") or "").strip())
    has_phone = bool(str(cand.get("phone") or "").strip())
    if has_email and has_phone:
        return {
            "candidateId": candidate_id,
            "found": False,
            "source": None,
            "reason": "already_has_contact",
        }

    rec = db.loads(cand["candidate_json"], {})
    _pull_stored_csv(rec, cand["profile_id"])

    if use_apollo and guards.apollo_ready():
        need_apollo = (
            not str(rec.get("email") or "").strip()
            or not str(rec.get("phone") or "").strip()
        )
        if need_apollo:
            try:
                _pull_apollo_live(rec, matcher=matcher)
            except Exception:
                rec["enrichment_attempted"] = True

    if guards.hunter_ready() and not str(rec.get("email") or "").strip():
        try:
            _pull_hunter_live(rec, finder=hunter_finder)
        except Exception:
            rec["enrichment_attempted"] = True

    _persist_contact(conn, cand, rec)
    email = str(rec.get("email") or "").strip()
    phone = str(rec.get("phone") or "").strip()
    source = _contact_lookup_source(rec)
    return {
        "candidateId": candidate_id,
        "found": bool(email or phone),
        "source": source,
        "email": email or None,
        "phone": phone or None,
        "reason": None if email or phone else "not_found",
    }


def sync_stored_contacts(conn, profile_id: str) -> int:
    """Backfill email/phone from on-disk Apollo CSV exports for this profile."""
    from signal_discovery import sync_contact_from_csv_rows

    rows = conn.execute(
        """
        SELECT * FROM candidates
         WHERE profile_id = ?
           AND (email IS NULL OR email = '')
           AND (phone IS NULL OR phone = '')
        """,
        (profile_id,),
    ).fetchall()
    if not rows:
        return 0
    csv_rows = _load_contact_csv_rows(profile_id)
    if not csv_rows:
        return 0
    updated = 0
    for row in rows:
        cand = dict(row)
        rec = db.loads(cand["candidate_json"], {})
        if sync_contact_from_csv_rows(rec, csv_rows):
            _persist_contact(conn, cand, rec)
            updated += 1
    return updated


def _ensure_draftable(conn, cand: dict[str, Any]) -> dict[str, Any]:
    """Hunt uses explicit yes; Records can draft once an email is on file."""
    if cand.get("decision") == "yes":
        return cand
    rec = db.loads(cand["candidate_json"], {})
    email = str(cand.get("email") or rec.get("email") or "").strip()
    if not email:
        guards.assert_approved(cand)
    decided_at = now_iso()
    rec["human_status"] = "APPROVED"
    rec["human_decided_at"] = decided_at
    conn.execute(
        "UPDATE candidates SET decision = 'yes', decided_at = ?, candidate_json = ? WHERE id = ?",
        (decided_at, db.dumps(rec), cand["id"]),
    )
    conn.commit()
    return get_candidate(conn, cand["id"]) or cand


def create_draft(
    conn,
    candidate_id: str,
    *,
    template_id: str | None = None,
    drafter: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    from trace_drafting import enrich_lead_from_candidate

    cand = get_candidate(conn, candidate_id)
    if not cand:
        raise guards.GuardError("no_candidate", f"Unknown person '{candidate_id}'.")
    cand = _ensure_draftable(conn, cand)

    profile = profiles.get_profile(conn, cand["profile_id"])
    if not profile:
        raise guards.GuardError("no_profile", "This person's profile is gone.")

    rec = db.loads(cand["candidate_json"], {})
    snapshot = _snapshot_for(conn, cand, profile)
    guards.assert_snapshot_matches(snapshot, profile["profile"])

    tid = template_id or profile["defaultTemplate"]
    if tid not in profiles.TEMPLATES:
        raise guards.GuardError("bad_template", f"Unknown template '{tid}'.")

    engine_profile = profiles.engine_profile(snapshot, tid)
    lead = enrich_lead_from_candidate(rec)
    build = drafter or drafting.build_draft
    out = build(engine_profile, lead)

    draft_id = profiles.new_id("draft")
    conn.execute(
        "UPDATE drafts SET superseded = 1 WHERE candidate_id = ?", (candidate_id,)
    )
    conn.execute(
        """
        INSERT INTO drafts (id, candidate_id, profile_id, template_id, subject, body,
                            verdict, sendable, critique_json, error, superseded, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
        """,
        (
            draft_id,
            candidate_id,
            cand["profile_id"],
            tid,
            out.get("subject") or "",
            out.get("body") or "",
            out.get("verdict") or "failed",
            1 if out.get("verdict") == drafting.VERDICT_SENDABLE else 0,
            db.dumps(out.get("critique")) if out.get("critique") else None,
            out.get("error"),
            now_iso(),
        ),
    )
    conn.commit()
    return {"candidateId": candidate_id, "draftId": draft_id, "verdict": out.get("verdict")}


def _snapshot_for(conn, cand: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    if cand.get("hunt_id"):
        row = conn.execute(
            "SELECT snapshot_json FROM hunts WHERE id = ?", (cand["hunt_id"],)
        ).fetchone()
        if row:
            snap = db.loads(row["snapshot_json"], {})
            if snap:
                return snap
    return profile["profile"]


def edit_draft(conn, draft_id: str, subject: str | None, body: str | None) -> None:
    draft = get_draft(conn, draft_id)
    if not draft:
        raise guards.GuardError("no_draft", f"Unknown draft '{draft_id}'.")
    conn.execute(
        "UPDATE drafts SET subject = ?, body = ? WHERE id = ?",
        (
            subject if subject is not None else draft["subject"],
            body if body is not None else draft["body"],
            draft_id,
        ),
    )
    conn.commit()


# ── Sending ─────────────────────────────────────────────────────────────────


def send_draft(
    conn,
    draft_id: str,
    *,
    sender: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    draft = get_draft(conn, draft_id)
    if not draft:
        raise guards.GuardError("no_draft", f"Unknown draft '{draft_id}'.")
    cand = get_candidate(conn, draft["candidate_id"])
    if not cand:
        raise guards.GuardError("no_candidate", "This draft has no person.")
    profile = profiles.get_profile(conn, cand["profile_id"])
    if not profile:
        raise guards.GuardError("no_profile", "This person's profile is gone.")

    guards.assert_same_profile(
        expected=profile["id"],
        candidate=cand["profile_id"],
        draft=draft["profile_id"],
    )
    guards.assert_approved(cand)
    guards.assert_sendable(draft)
    snapshot = _snapshot_for(conn, cand, profile)
    guards.assert_snapshot_matches(snapshot, profile["profile"])

    to_email = str(cand.get("email") or "").strip()
    if not to_email:
        raise guards.GuardError("no_email", "No address for this person yet.")

    mailbox = guards.connected_mailbox()
    guards.assert_sender_matches(profile["fromEmail"], mailbox)

    key = guards.idempotency_key(cand["id"], draft["subject"], draft["body"])
    existing = conn.execute(
        "SELECT * FROM sends WHERE idempotency_key = ?", (key,)
    ).fetchone()
    if existing:
        return {"sendId": existing["id"], "alreadySent": True}

    send = sender
    if send is None:
        import main as engine

        send = engine.outlook_send_with_meta

    ok, meta = send(to_email, draft["subject"], draft["body"])
    if not ok:
        raise guards.GuardError("send_failed", "The mailbox rejected the send.")

    send_id = profiles.new_id("send")
    conn.execute(
        """
        INSERT INTO sends (id, draft_id, candidate_id, profile_id, method, to_email,
                           from_email, subject, body, graph_message_id, conversation_id,
                           internet_message_id, idempotency_key, sent_at)
        VALUES (?, ?, ?, ?, 'trace', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            send_id,
            draft_id,
            cand["id"],
            cand["profile_id"],
            to_email,
            mailbox,
            draft["subject"],
            draft["body"],
            (meta or {}).get("graph_sent_message_id"),
            (meta or {}).get("conversation_id"),
            (meta or {}).get("internet_message_id"),
            key,
            now_iso(),
        ),
    )
    conn.commit()
    return {"sendId": send_id, "alreadySent": False}


def mark_sent_myself(conn, candidate_id: str) -> dict[str, Any]:
    cand = get_candidate(conn, candidate_id)
    if not cand:
        raise guards.GuardError("no_candidate", f"Unknown person '{candidate_id}'.")
    guards.assert_approved(cand)
    draft = latest_draft(conn, candidate_id)
    key = guards.idempotency_key(candidate_id, "self", cand.get("email") or "")
    existing = conn.execute(
        "SELECT * FROM sends WHERE idempotency_key = ?", (key,)
    ).fetchone()
    if existing:
        return {"sendId": existing["id"], "alreadySent": True}
    send_id = profiles.new_id("send")
    conn.execute(
        """
        INSERT INTO sends (id, draft_id, candidate_id, profile_id, method, to_email,
                           from_email, subject, body, idempotency_key, sent_at)
        VALUES (?, ?, ?, ?, 'self', ?, '', ?, ?, ?, ?)
        """,
        (
            send_id,
            draft["id"] if draft else None,
            candidate_id,
            cand["profile_id"],
            str(cand.get("email") or ""),
            draft["subject"] if draft else "",
            draft["body"] if draft else "",
            key,
            now_iso(),
        ),
    )
    conn.commit()
    return {"sendId": send_id, "alreadySent": False}


# ── Reads ───────────────────────────────────────────────────────────────────


def get_candidate(conn, candidate_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
    return dict(row) if row else None


def get_draft(conn, draft_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    return dict(row) if row else None


def latest_draft(conn, candidate_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM drafts WHERE candidate_id = ? ORDER BY created_at DESC LIMIT 1",
        (candidate_id,),
    ).fetchone()
    return dict(row) if row else None


def list_hunts(conn, profile_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT h.*,
               (SELECT COUNT(*) FROM candidates c WHERE c.hunt_id = h.id) AS candidate_count
        FROM hunts h
        WHERE h.profile_id = ?
        ORDER BY h.created_at DESC
        LIMIT ?
        """,
        (profile_id, limit),
    ).fetchall()
    return [_hunt_summary(conn, dict(row)) for row in rows]


def _hunt_summary(conn, hunt: dict[str, Any]) -> dict[str, Any]:
    timing = _hunt_timing(hunt)
    last = conn.execute(
        "SELECT message FROM hunt_events WHERE hunt_id = ? ORDER BY created_at DESC LIMIT 1",
        (hunt["id"],),
    ).fetchone()
    return {
        "id": hunt["id"],
        "profileId": hunt["profile_id"],
        "limit": hunt["limit_n"],
        "status": hunt["status"],
        "currentStage": hunt.get("current_stage") or "",
        "candidateCount": int(hunt.get("candidate_count") or 0),
        "error": hunt.get("error"),
        "createdAt": hunt["created_at"],
        "startedAt": hunt.get("started_at"),
        "finishedAt": hunt.get("finished_at"),
        "progress": last["message"] if last else "",
        "progressPct": _hunt_progress_pct(hunt.get("current_stage") or ""),
        **timing,
    }


def get_hunt(conn, hunt_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM hunts WHERE id = ?", (hunt_id,)).fetchone()
    if not row:
        return None
    hunt = dict(row)
    job = conn.execute(
        "SELECT * FROM jobs WHERE hunt_id = ? ORDER BY created_at DESC LIMIT 1",
        (hunt_id,),
    ).fetchone()
    timing = _hunt_timing(hunt)
    events = conn.execute(
        """
        SELECT stage, message, created_at
        FROM hunt_events
        WHERE hunt_id = ?
        ORDER BY created_at ASC
        """,
        (hunt_id,),
    ).fetchall()
    return {
        "id": hunt["id"],
        "profileId": hunt["profile_id"],
        "limit": hunt["limit_n"],
        "status": hunt["status"],
        "error": hunt["error"],
        "createdAt": hunt["created_at"],
        "startedAt": hunt.get("started_at"),
        "finishedAt": hunt["finished_at"],
        "currentStage": hunt.get("current_stage") or "",
        "progress": job["progress"] if job else "",
        "jobStatus": job["status"] if job else None,
        "progressPct": _hunt_progress_pct(hunt.get("current_stage") or ""),
        **timing,
        "events": [
            {"stage": e["stage"], "message": e["message"], "at": e["created_at"]}
            for e in events
        ],
        "candidates": [
            candidate_dto(conn, dict(r))
            for r in conn.execute(
                "SELECT * FROM candidates WHERE hunt_id = ? ORDER BY created_at ASC",
                (hunt_id,),
            ).fetchall()
        ],
    }


def candidate_dto(conn, row: dict[str, Any]) -> dict[str, Any]:
    from trace_drafting import classify_outreach

    rec = db.loads(row["candidate_json"], {})
    outreach = classify_outreach(rec)
    draft = latest_draft(conn, row["id"])
    send = conn.execute(
        "SELECT * FROM sends WHERE candidate_id = ? ORDER BY sent_at DESC LIMIT 1",
        (row["id"],),
    ).fetchone()
    notes = conn.execute(
        "SELECT text, created_at FROM notes WHERE candidate_id = ? ORDER BY created_at ASC",
        (row["id"],),
    ).fetchall()
    return {
        "id": row["id"],
        "profileId": row["profile_id"],
        "huntId": row["hunt_id"],
        "name": row["name"],
        "title": row["title"],
        "company": row["company"],
        "foundOn": row["found_on"],
        "decision": row["decision"],
        "outcome": row["outcome"],
        "status": _status_of(row, draft, send),
        "email": row["email"],
        "emailSource": row["email_source"],
        "phone": row["phone"],
        "phoneSource": row["phone_source"],
        "enrichState": row["enrich_state"],
        "createdAt": row["created_at"],
        "decidedAt": row["decided_at"],
        "signal": {
            "text": rec.get("signal_text") or "",
            "url": rec.get("signal_url") or "",
            "date": rec.get("signal_date") or "",
            "why": rec.get("why_relevant") or "",
        },
        "actorType": rec.get("actor_type") or "",
        "outreachRole": outreach["outreach_role"],
        "recommendedAsk": outreach["recommended_ask"],
        "secondaryRoles": outreach["secondary_roles"],
        "recommendation": rec.get("recommendation") or "",
        "recommendationReason": rec.get("recommendation_reason") or "",
        "linkedinUrl": rec.get("linkedin_url") or "",
        "axes": _axes_dto(rec),
        "deepened": [str(e) for e in (rec.get("supporting_evidence") or [])],
        "additionalSignals": _extra_signals_dto(rec),
        "draft": _draft_dto(draft),
        "sentAt": send["sent_at"] if send else None,
        "sendMethod": send["method"] if send else None,
        "notes": [{"text": n["text"], "at": n["created_at"]} for n in notes],
    }


def _axes_dto(rec: dict[str, Any]) -> list[dict[str, str]]:
    """The engine stores axes as flat ALL_CAPS fields. The UI wants readable rows."""
    from signal_discovery import CLASSIFICATION_AXES

    out: list[dict[str, str]] = []
    nested = rec.get("axes") if isinstance(rec.get("axes"), dict) else {}
    for key in CLASSIFICATION_AXES:
        raw = rec.get(key) or nested.get(key)
        if not raw:
            continue
        out.append({"key": key, "label": _humanize(key), "value": _humanize(str(raw))})
    return out


def _humanize(value: str) -> str:
    text = str(value or "").replace("_", " ").strip().lower()
    return text[:1].upper() + text[1:] if text else ""


def _extra_signals_dto(rec: dict[str, Any]) -> list[dict[str, str]]:
    items = rec.get("additional_signals") or rec.get("signals") or []
    out: list[dict[str, str]] = []
    for item in items:
        if isinstance(item, dict):
            out.append(
                {
                    "text": str(item.get("signal_text") or item.get("text") or ""),
                    "url": str(item.get("source_url") or item.get("url") or ""),
                    "date": str(item.get("published_at") or item.get("date") or ""),
                }
            )
        elif str(item).strip():
            out.append({"text": str(item), "url": "", "date": ""})
    return out


def _draft_status(draft: dict[str, Any]) -> str:
    if draft.get("superseded"):
        return "superseded"
    err = str(draft.get("error") or "").strip()
    body = str(draft.get("body") or "").strip()
    if err and not body:
        return "failed"
    if body:
        return "ready"
    return "pending"


def _draft_dto(draft: dict[str, Any] | None) -> dict[str, Any] | None:
    if not draft:
        return None
    return {
        "id": draft["id"],
        "templateId": draft["template_id"],
        "subject": draft["subject"],
        "body": draft["body"],
        "verdict": draft["verdict"],
        "sendable": bool(draft["sendable"]),
        "error": draft["error"],
        "status": _draft_status(draft),
        "createdAt": draft["created_at"],
    }


def _status_of(row: dict[str, Any], draft: dict[str, Any] | None, send: Any) -> str:
    if row.get("outcome"):
        return row["outcome"]
    if send:
        return "sent"
    if row["decision"] == "no":
        return "passed"
    if draft:
        ds = _draft_status(draft)
        if ds == "failed":
            return "draft_failed"
        if ds == "ready":
            return "draft"
    if row["decision"] == "yes":
        return "approved"
    return "researched"


def ensure_prepare_jobs(conn, profile_id: str) -> int:
    """Queue draft prep for approved people who still need a draft.

    Covers legacy yes-without-job imports and the common hunt path where Apollo
    only found phone: after manual email entry, a finished prepare job must not
    block a new one.
    """
    rows = conn.execute(
        """
        SELECT c.id
          FROM candidates c
         WHERE c.profile_id = ?
           AND c.decision = 'yes'
           AND (
               trim(coalesce(c.email, '')) != ''
               OR trim(coalesce(json_extract(c.candidate_json, '$.email'), '')) != ''
           )
           AND NOT EXISTS (
               SELECT 1 FROM drafts d
                WHERE d.candidate_id = c.id AND d.superseded = 0
                  AND trim(coalesce(d.body, '')) != ''
           )
           AND NOT EXISTS (
               SELECT 1 FROM jobs j
                WHERE j.candidate_id = c.id AND j.type = 'prepare'
                  AND j.status IN ('queued', 'running')
           )
        """,
        (profile_id,),
    ).fetchall()
    enqueued = 0
    for row in rows:
        enqueue_job(
            conn,
            "prepare",
            profile_id=profile_id,
            candidate_id=row["id"],
            payload={},
        )
        enqueued += 1
    if enqueued:
        conn.commit()
    return enqueued


def _enqueue_prepare_if_needed(conn, candidate_id: str) -> bool:
    row = conn.execute(
        """
        SELECT c.id, c.profile_id
          FROM candidates c
         WHERE c.id = ?
           AND c.decision = 'yes'
           AND (
               trim(coalesce(c.email, '')) != ''
               OR trim(coalesce(json_extract(c.candidate_json, '$.email'), '')) != ''
           )
           AND NOT EXISTS (
               SELECT 1 FROM drafts d
                WHERE d.candidate_id = c.id AND d.superseded = 0
                  AND trim(coalesce(d.body, '')) != ''
           )
           AND NOT EXISTS (
               SELECT 1 FROM jobs j
                WHERE j.candidate_id = c.id AND j.type = 'prepare'
                  AND j.status IN ('queued', 'running')
           )
        """,
        (candidate_id,),
    ).fetchone()
    if not row:
        return False
    enqueue_job(
        conn,
        "prepare",
        profile_id=row["profile_id"],
        candidate_id=candidate_id,
        payload={},
    )
    conn.commit()
    return True


def people(conn, profile_id: str) -> list[dict[str, Any]]:
    sync_stored_contacts(conn, profile_id)
    ensure_prepare_jobs(conn, profile_id)
    rows = conn.execute(
        "SELECT * FROM candidates WHERE profile_id = ? ORDER BY created_at DESC",
        (profile_id,),
    ).fetchall()
    return [candidate_dto(conn, dict(r)) for r in rows]


def cost_summary(conn, profile_id: str) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT hunt_id, stage, SUM(cost_usd) AS cost FROM cost_events "
        "WHERE profile_id = ? GROUP BY hunt_id, stage",
        (profile_id,),
    ).fetchall()
    total = 0.0
    by_stage: dict[str, float] = {}
    by_hunt: dict[str, float] = {}
    for r in rows:
        cost = float(r["cost"] or 0)
        total += cost
        by_stage[r["stage"] or "other"] = by_stage.get(r["stage"] or "other", 0) + cost
        by_hunt[r["hunt_id"] or ""] = by_hunt.get(r["hunt_id"] or "", 0) + cost
    return {
        "profileId": profile_id,
        "totalUsd": round(total, 4),
        "hunts": len(by_hunt),
        "byStage": [{"stage": k, "usd": round(v, 4)} for k, v in sorted(by_stage.items())],
        "byHunt": [{"huntId": k, "usd": round(v, 4)} for k, v in by_hunt.items()],
    }


def estimate_hunt_usd(conn, profile_id: str, limit: int) -> dict[str, float]:
    """Per-person average from this profile's own history, with a safe default."""
    row = conn.execute(
        "SELECT SUM(cost_usd) AS total FROM cost_events WHERE profile_id = ?",
        (profile_id,),
    ).fetchone()
    hunts = conn.execute(
        "SELECT COUNT(DISTINCT hunt_id) AS n FROM cost_events WHERE profile_id = ?",
        (profile_id,),
    ).fetchone()
    people_found = conn.execute(
        "SELECT COUNT(*) AS n FROM candidates WHERE profile_id = ?", (profile_id,)
    ).fetchone()
    total = float(row["total"] or 0)
    found = int(people_found["n"] or 0)
    per_person = (total / found) if (total and found) else 0.55
    low = per_person * limit * 0.8
    high = per_person * limit * 1.35
    return {"low": round(low, 2), "high": round(high, 2)}


# ── Jobs ────────────────────────────────────────────────────────────────────


def enqueue_job(
    conn,
    job_type: str,
    *,
    profile_id: str | None = None,
    hunt_id: str | None = None,
    candidate_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> str:
    job_id = profiles.new_id("job")
    conn.execute(
        """
        INSERT INTO jobs (id, type, profile_id, hunt_id, candidate_id, payload_json,
                          status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'queued', ?)
        """,
        (
            job_id,
            job_type,
            profile_id,
            hunt_id,
            candidate_id,
            db.dumps(payload or {}),
            now_iso(),
        ),
    )
    return job_id


def get_job(conn, job_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        return None
    job = dict(row)
    return {
        "id": job["id"],
        "type": job["type"],
        "status": job["status"],
        "progress": job["progress"],
        "error": job["error"],
        "huntId": job["hunt_id"],
        "candidateId": job["candidate_id"],
    }


def claim_next_job(conn) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    conn.execute(
        "UPDATE jobs SET status = 'running', started_at = ? WHERE id = ? AND status = 'queued'",
        (now_iso(), row["id"]),
    )
    conn.commit()
    return dict(row)


def finish_job(conn, job_id: str, *, error: str | None = None) -> None:
    conn.execute(
        "UPDATE jobs SET status = ?, error = ?, finished_at = ? WHERE id = ?",
        ("failed" if error else "done", error, now_iso(), job_id),
    )
    conn.commit()


def set_job_progress(conn, job_id: str, message: str) -> None:
    conn.execute("UPDATE jobs SET progress = ? WHERE id = ?", (message, job_id))
    conn.commit()


def run_job(conn, job: dict[str, Any]) -> None:
    job_id = job["id"]
    try:
        if job["type"] == "hunt":
            set_job_progress(conn, job_id, "Searching")
            added = run_hunt(conn, job["hunt_id"])
            set_job_progress(conn, job_id, f"Found {added}")
        elif job["type"] == "prepare":
            set_job_progress(conn, job_id, "Looking up contact")
            payload = db.loads(job["payload_json"], {})
            prepare_candidate(
                conn, job["candidate_id"], template_id=payload.get("templateId")
            )
            set_job_progress(conn, job_id, "Draft ready")
        else:
            raise guards.GuardError("bad_job", f"Unknown job type '{job['type']}'.")
        finish_job(conn, job_id)
    except Exception as exc:
        if job["type"] == "hunt" and job.get("hunt_id"):
            conn.execute(
                "UPDATE hunts SET status = 'failed', error = ?, finished_at = ? WHERE id = ?",
                (str(exc), now_iso(), job["hunt_id"]),
            )
            conn.commit()
        finish_job(conn, job_id, error=str(exc))
