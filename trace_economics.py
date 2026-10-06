"""Campaign unit economics. Costs and funnel outcomes stay in separate tables.

Tracked spend is the sum of known variable provider costs. An unknown price is
null, never zero. Allocated discovery shares are stored for audit and are not
added again on top of the wave that incurred them.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

VARIABLE_CATEGORIES = {
    "research_api",
    "drafting_api",
    "contact_data",
    "email_verification",
    "mailbox",
    "other_variable",
}

DISCOVERY_STAGES = {"discovery_web", "discovery_x"}

STAGE_ORDER = (
    ("discovery_web", "Discovery Web"),
    ("discovery_x", "Discovery X"),
    ("qualification", "Qualification"),
    ("deepening", "Deepening"),
    ("resolution", "Resolution"),
    ("drafting", "Drafting"),
    ("contact_lookup", "Contact lookup"),
)

HUMAN_QUALITIES = {"positive", "engaged", "neutral", "negative"}
MEANINGFUL_QUALITIES = {"positive", "engaged"}

SCOPE_NOTE = (
    "Tracked spend: Grok research only. "
    "Apollo, drafting, mailbox, and fixed software costs are not included unless separately recorded."
)

FUNNEL_ROWS = (
    ("reviewed", "Reviewed", None),
    ("outreach_ready", "Outreach-ready", "reviewed"),
    ("approved", "Approved", "outreach_ready"),
    ("contact_found", "Contact found", "approved"),
    ("drafted", "Drafted", "contact_found"),
    ("sent", "Sent", "sendable"),
    ("human_reply", "Human reply", "sent"),
    ("meaningful_reply", "Positive/engaged reply", "sent"),
    ("meeting", "Meeting", "sent"),
    ("trial", "Trial", "sent"),
    ("customer", "Customer", "sent"),
)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def research_source_key(event: dict[str, Any], index: int) -> str:
    request_id = str(event.get("request_id") or "").strip()
    if request_id:
        return "req:" + request_id
    raw = "|".join(
        [
            str(event.get("run_id") or ""),
            str(event.get("stage") or ""),
            str(event.get("entity_key") or ""),
            str(event.get("signal_url") or ""),
            str(event.get("elapsed_sec") if event.get("elapsed_sec") is not None else ""),
            "" if event.get("cost_usd") is None else str(event.get("cost_usd")),
            str(index),
        ]
    )
    return "line:" + hashlib.sha256(raw.encode()).hexdigest()[:32]


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dumps(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False)


def _loads(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def normalize_stage(stage: str) -> str:
    raw = (stage or "").strip().lower()
    if raw in {"draft", "drafting"}:
        return "drafting"
    if raw in {"contact", "contact_lookup"}:
        return "contact_lookup"
    return raw or "other"


def stage_category(stage: str) -> str:
    normalized = normalize_stage(stage)
    if normalized == "drafting":
        return "drafting_api"
    if normalized == "contact_lookup":
        return "contact_data"
    if normalized in {"discovery_web", "discovery_x", "qualification", "deepening", "resolution", "search"}:
        return "research_api"
    return "other_variable"


def _cost_scope(stage: str, entity_key: str) -> str:
    if normalize_stage(stage) in DISCOVERY_STAGES:
        return "wave"
    if entity_key:
        return "candidate"
    return "hunt"


def _allocation_method(scope: str) -> str:
    if scope == "wave":
        return "unallocated"
    if scope == "candidate":
        return "direct"
    return "direct"


def counts_toward_total(event: dict[str, Any]) -> bool:
    if event.get("cost_usd") is None:
        return False
    if str(event.get("category") or "research_api") not in VARIABLE_CATEGORIES:
        return False
    if event.get("allocation_method") == "equal_split" and event.get("cost_scope") == "candidate":
        return False
    return True


def ratio(num: int, den: int) -> dict[str, Any]:
    if den <= 0:
        return {"num": num, "den": 0, "rate": None}
    return {"num": num, "den": den, "rate": num / den}


def unit_cost(spend: float | None, count: int) -> float | None:
    if spend is None or count <= 0:
        return None
    return spend / count


def person_key(event: dict[str, Any]) -> str:
    return str(event.get("candidate_id") or event.get("entity_key") or event.get("id") or "")


def canonical_identities(events: list[dict[str, Any]]) -> dict[str, str]:
    """Map candidate ids, final keys, and earlier name-only keys to one person."""
    aliases: dict[str, str] = {}
    left_parts: dict[str, list[str]] = {}
    for event in events:
        if event.get("event_type") != "candidate_reviewed":
            continue
        canonical = str(event.get("candidate_id") or event.get("entity_key") or "")
        if not canonical:
            continue
        for raw in (event.get("candidate_id"), event.get("entity_key")):
            if raw:
                aliases[str(raw)] = canonical
        meta = event.get("metadata")
        if not isinstance(meta, dict):
            meta = _loads(event.get("metadata_json"))
        for alias in meta.get("entity_aliases") or []:
            if alias:
                aliases[str(alias)] = canonical
        entity = str(event.get("entity_key") or "")
        if "|" in entity:
            left_parts.setdefault(entity.split("|", 1)[0], []).append(canonical)
    for left, targets in left_parts.items():
        unique = list(dict.fromkeys(targets))
        if len(unique) == 1 and left not in aliases:
            aliases[left] = unique[0]
    return aliases


def identity_of(event: dict[str, Any], aliases: dict[str, str] | None = None) -> str:
    aliases = aliases or {}
    for raw in (event.get("candidate_id"), event.get("entity_key")):
        if raw and str(raw) in aliases:
            return aliases[str(raw)]
    return person_key(event)


def allocate_wave(
    cost_usd: float | None,
    people: list[str],
    *,
    denominator: int | None = None,
) -> dict[str, Any]:
    """Equal-split a wave across the fresh people it actually reviewed.

    When some reviewed people were not stored, `denominator` keeps their share
    on the hunt instead of pushing it onto the named candidates.
    """
    unique = list(dict.fromkeys(key for key in people if key))
    if cost_usd is None:
        return {"allocation_method": "unallocated", "tracked": False, "shares": []}
    if not unique:
        return {
            "allocation_method": "unallocated",
            "tracked": True,
            "hunt_cost": cost_usd,
            "shares": [],
        }
    denom = max(len(unique), int(denominator or 0))
    share = cost_usd / denom
    return {
        "allocation_method": "equal_split",
        "tracked": True,
        "shares": [{"key": key, "allocated_usd": share} for key in unique],
    }


def forecast_hunt(history: list[dict[str, Any]], target: int) -> dict[str, Any]:
    """Estimate a hunt from completed history. Cold start does not invent a price."""
    target = max(1, int(target))
    maximum = target * 5
    usable = [
        row
        for row in history
        if int(row.get("reviewed") or 0) > 0 and row.get("cost_usd") is not None
    ]
    base = {
        "target": target,
        "maximumReviewed": maximum,
        "rangeMethod": "recent min/max cost per reviewed candidate",
    }
    if len(usable) < 2:
        return {
            **base,
            "enoughHistory": False,
            "expectedReviewed": None,
            "expectedUsd": None,
            "low": None,
            "high": None,
            "readyRate": None,
            "message": "Not enough completed hunts for a reliable estimate.",
        }
    reviewed = sum(int(row["reviewed"]) for row in usable)
    ready = sum(int(row.get("ready") or 0) for row in usable)
    ready_rate = (ready / reviewed) if reviewed else 0.0
    if ready_rate <= 0:
        expected = maximum
    else:
        expected = min(maximum, math.ceil(target / ready_rate))
    per_reviewed = [float(row["cost_usd"]) / int(row["reviewed"]) for row in usable]
    average = sum(float(row["cost_usd"]) for row in usable) / reviewed
    return {
        **base,
        "enoughHistory": True,
        "expectedReviewed": expected,
        "expectedUsd": average * expected,
        "low": min(per_reviewed) * expected,
        "high": max(per_reviewed) * expected,
        "readyRate": ready_rate,
        "message": "",
    }


def _insert_funnel(
    conn,
    *,
    profile_id: str,
    event_type: str,
    source_key: str,
    occurred_at: str | None = None,
    hunt_id: str | None = None,
    candidate_id: str | None = None,
    entity_key: str = "",
    source_channel: str = "",
    signal_family: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    meta = _dumps(metadata)
    existing = conn.execute(
        "SELECT id, metadata_json, signal_family FROM funnel_events WHERE source_key = ?",
        (source_key,),
    ).fetchone()
    if existing:
        if event_type == "human_reply":
            conn.execute(
                "UPDATE funnel_events SET metadata_json = ?, event_type = ? WHERE source_key = ?",
                (meta, event_type, source_key),
            )
        if signal_family and not (existing["signal_family"] or "").strip():
            conn.execute(
                "UPDATE funnel_events SET signal_family = ? WHERE source_key = ?",
                (signal_family, source_key),
            )
        return
    conn.execute(
        """
        INSERT INTO funnel_events (
            id, occurred_at, profile_id, hunt_id, candidate_id, entity_key,
            event_type, source_channel, signal_family, source_key, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_id("funnel"),
            occurred_at or _now(),
            profile_id,
            hunt_id,
            candidate_id,
            entity_key,
            event_type,
            source_channel or "",
            signal_family or "",
            source_key,
            meta,
        ),
    )


def record_funnel(
    conn,
    *,
    profile_id: str,
    event_type: str,
    candidate_id: str | None = None,
    entity_key: str = "",
    hunt_id: str | None = None,
    source_channel: str = "",
    signal_family: str = "",
    occurred_at: str | None = None,
    metadata: dict[str, Any] | None = None,
    source_key: str | None = None,
) -> None:
    identity = candidate_id or entity_key
    if not identity:
        return
    key = source_key or f"{event_type}:{profile_id}:{identity}"
    _insert_funnel(
        conn,
        profile_id=profile_id,
        event_type=event_type,
        source_key=key,
        occurred_at=occurred_at,
        hunt_id=hunt_id,
        candidate_id=candidate_id,
        entity_key=entity_key,
        source_channel=source_channel,
        signal_family=signal_family,
        metadata=metadata,
    )


def _channel_label(found_on: str) -> str:
    raw = (found_on or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    if lowered in {"x", "twitter"}:
        return "X"
    if lowered == "linkedin":
        return "LinkedIn"
    if lowered == "web":
        return "Web"
    if lowered == "manual":
        return "Manual"
    return raw


def sync_profile(conn, profile_id: str) -> None:
    """Derive funnel events from stored people, drafts, and sends. Idempotent."""
    rows = conn.execute(
        "SELECT * FROM candidates WHERE profile_id = ?",
        (profile_id,),
    ).fetchall()
    for row in rows:
        rec = _loads(row["candidate_json"])
        from trace_reply_reason import signal_family_for

        family = signal_family_for(rec)
        if str(rec.get("signal_family") or "") != family:
            rec["signal_family"] = family
            conn.execute(
                "UPDATE candidates SET candidate_json = ? WHERE id = ?",
                (_dumps(rec), row["id"]),
            )
        entity = str(row["entity_key"] or rec.get("entity_key") or "")
        identity = entity or row["id"]
        channel = _channel_label(str(row["found_on"] or ""))
        family = str(rec.get("signal_family") or "")
        hunt_id = row["hunt_id"]
        occurred = row["created_at"]
        record_funnel(
            conn,
            profile_id=profile_id,
            event_type="candidate_reviewed",
            candidate_id=row["id"],
            entity_key=entity,
            hunt_id=hunt_id,
            source_channel=channel,
            signal_family=family,
            occurred_at=occurred,
            source_key=f"candidate_reviewed:{profile_id}:{identity}",
        )
        decision = str(rec.get("draft_decision") or "")
        reply = rec.get("reply_reason") if isinstance(rec.get("reply_reason"), dict) else {}
        if not decision:
            decision = str(reply.get("draft_decision") or "")
        if decision == "send_now":
            record_funnel(
                conn,
                profile_id=profile_id,
                event_type="outreach_ready",
                candidate_id=row["id"],
                entity_key=entity,
                hunt_id=hunt_id,
                source_channel=channel,
                signal_family=family,
                occurred_at=occurred,
                source_key=f"outreach_ready:{profile_id}:{identity}",
            )
        if row["decision"] == "yes":
            record_funnel(
                conn,
                profile_id=profile_id,
                event_type="human_approved",
                candidate_id=row["id"],
                entity_key=entity,
                hunt_id=hunt_id,
                source_channel=channel,
                signal_family=family,
                occurred_at=row["decided_at"] or occurred,
                source_key=f"human_approved:{profile_id}:{identity}",
            )
        elif row["decision"] == "no":
            record_funnel(
                conn,
                profile_id=profile_id,
                event_type="human_rejected",
                candidate_id=row["id"],
                entity_key=entity,
                hunt_id=hunt_id,
                source_channel=channel,
                signal_family=family,
                occurred_at=row["decided_at"] or occurred,
                source_key=f"human_rejected:{profile_id}:{identity}",
            )
        email = str(row["email"] or "").strip()
        if email:
            record_funnel(
                conn,
                profile_id=profile_id,
                event_type="contact_found",
                candidate_id=row["id"],
                entity_key=entity,
                hunt_id=hunt_id,
                source_channel=channel,
                signal_family=family,
                occurred_at=occurred,
                source_key=f"contact_found:{profile_id}:{identity}",
            )
        elif row["decision"] == "yes" and row["enrich_state"] == "attempted":
            record_funnel(
                conn,
                profile_id=profile_id,
                event_type="contact_not_found",
                candidate_id=row["id"],
                entity_key=entity,
                hunt_id=hunt_id,
                source_channel=channel,
                signal_family=family,
                occurred_at=occurred,
                source_key=f"contact_not_found:{profile_id}:{identity}",
            )
        draft = conn.execute(
            "SELECT id, sendable, created_at FROM drafts WHERE candidate_id = ? AND superseded = 0 ORDER BY created_at ASC LIMIT 1",
            (row["id"],),
        ).fetchone()
        if draft:
            record_funnel(
                conn,
                profile_id=profile_id,
                event_type="draft_generated",
                candidate_id=row["id"],
                entity_key=entity,
                hunt_id=hunt_id,
                source_channel=channel,
                signal_family=family,
                occurred_at=draft["created_at"],
                source_key=f"draft_generated:{profile_id}:{identity}",
            )
            if int(draft["sendable"] or 0) == 1 and email:
                record_funnel(
                    conn,
                    profile_id=profile_id,
                    event_type="sendable",
                    candidate_id=row["id"],
                    entity_key=entity,
                    hunt_id=hunt_id,
                    source_channel=channel,
                    signal_family=family,
                    occurred_at=draft["created_at"],
                    source_key=f"sendable:{profile_id}:{identity}",
                )
        for send in conn.execute(
            "SELECT id, sent_at FROM sends WHERE candidate_id = ?",
            (row["id"],),
        ):
            record_funnel(
                conn,
                profile_id=profile_id,
                event_type="email_sent",
                candidate_id=row["id"],
                entity_key=entity,
                hunt_id=hunt_id,
                source_channel=channel,
                signal_family=family,
                occurred_at=send["sent_at"],
                source_key=f"email_sent:{send['id']}",
            )
        quality = str(rec.get("reply_quality") or "").lower()
        if quality in HUMAN_QUALITIES:
            record_funnel(
                conn,
                profile_id=profile_id,
                event_type="human_reply",
                candidate_id=row["id"],
                entity_key=entity,
                hunt_id=hunt_id,
                source_channel=channel,
                signal_family=family,
                occurred_at=str(rec.get("first_reply_at") or occurred),
                metadata={"reply_quality": quality},
                source_key=f"human_reply:{profile_id}:{identity}",
            )
    conn.commit()


def record_review_batch(conn, profile_id: str, hunt_id: str, people: list[dict[str, Any]]) -> None:
    """Reviewed people who may never be saved, including rejects."""
    saved = {
        str(row["entity_key"]): row["id"]
        for row in conn.execute(
            "SELECT id, entity_key FROM candidates WHERE hunt_id = ?",
            (hunt_id,),
        )
        if row["entity_key"]
    }
    for person in people:
        entity = str(person.get("entity_key") or "")
        if not entity:
            continue
        channel = _channel_label(str(person.get("source_channel") or ""))
        family = str(person.get("signal_family") or "")
        meta = {"wave_id": str(person.get("wave_id") or "")}
        aliases = [str(item) for item in (person.get("entity_aliases") or []) if item]
        if aliases:
            meta["entity_aliases"] = aliases
        record_funnel(
            conn,
            profile_id=profile_id,
            event_type="candidate_reviewed",
            candidate_id=saved.get(entity),
            entity_key=entity,
            hunt_id=hunt_id,
            source_channel=channel,
            signal_family=family,
            metadata=meta,
            source_key=f"candidate_reviewed:{profile_id}:{entity}",
        )
        if person.get("kind") == "ready" or person.get("draft_decision") == "send_now":
            record_funnel(
                conn,
                profile_id=profile_id,
                event_type="outreach_ready",
                candidate_id=saved.get(entity),
                entity_key=entity,
                hunt_id=hunt_id,
                source_channel=channel,
                signal_family=family,
                metadata=meta,
                source_key=f"outreach_ready:{profile_id}:{entity}",
            )
        if person.get("kind") == "reject":
            record_funnel(
                conn,
                profile_id=profile_id,
                event_type="candidate_rejected",
                candidate_id=saved.get(entity),
                entity_key=entity,
                hunt_id=hunt_id,
                source_channel=channel,
                signal_family=family,
                metadata=meta,
                source_key=f"candidate_rejected:{profile_id}:{entity}",
            )
    conn.commit()


def import_research_events(
    conn,
    events: list[dict[str, Any]],
    *,
    profile_id: str,
    hunt_id: str | None,
    occurred_at: str | None = None,
) -> int:
    """Insert research-cost log rows. The same request is not billed twice."""
    written = 0
    for index, event in enumerate(events):
        key = research_source_key(event, index)
        stage = normalize_stage(str(event.get("stage") or ""))
        entity = str(event.get("entity_key") or "")
        cost = event.get("cost_usd")
        cost_value = None if cost is None or cost == "" else float(cost)
        scope = _cost_scope(stage, entity)
        metadata = {"wave_id": str(event.get("wave_id") or "")}
        payload = (
            profile_id,
            hunt_id,
            event.get("candidate_id"),
            entity,
            stage,
            str(event.get("provider") or "xai"),
            str(event.get("model") or ""),
            str(event.get("source_channel") or ""),
            str(event.get("signal_family") or ""),
            str(event.get("signal_url") or ""),
            str(event.get("request_id") or ""),
            event.get("prompt_tokens"),
            event.get("cached_prompt_tokens"),
            event.get("reasoning_tokens"),
            event.get("completion_tokens"),
            event.get("web_calls_attempted"),
            event.get("web_calls_billable"),
            event.get("x_calls_attempted"),
            event.get("x_calls_billable"),
            cost_value,
            float(event.get("elapsed_sec") or 0),
            scope,
            _allocation_method(scope),
            stage_category(stage),
            key,
            _dumps(metadata),
            occurred_at or event.get("occurred_at") or _now(),
        )
        before = conn.execute("SELECT id, cost_usd FROM cost_events WHERE source_key = ?", (key,)).fetchone()
        if before:
            if before["cost_usd"] is None and cost_value is not None:
                conn.execute(
                    "UPDATE cost_events SET cost_usd = ? WHERE source_key = ?",
                    (cost_value, key),
                )
            continue
        conn.execute(
            """
            INSERT INTO cost_events (
                id, profile_id, hunt_id, candidate_id, entity_key, stage, provider, model,
                source_channel, signal_family, signal_url, request_id,
                prompt_tokens, cached_prompt_tokens, reasoning_tokens, completion_tokens,
                web_calls_attempted, web_calls_billable, x_calls_attempted, x_calls_billable,
                cost_usd, elapsed_sec, cost_scope, allocation_method, category,
                source_key, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (new_id("cost"), *payload),
        )
        written += 1
    conn.commit()
    return written


def refresh_wave_allocations(conn, hunt_id: str) -> None:
    conn.execute(
        """
        UPDATE cost_events
        SET cost_scope = 'wave'
        WHERE hunt_id = ?
          AND stage IN ('discovery_web', 'discovery_x')
          AND cost_scope != 'candidate'
        """,
        (hunt_id,),
    )
    conn.execute(
        """
        DELETE FROM cost_events
        WHERE hunt_id = ? AND cost_scope = 'candidate' AND allocation_method = 'equal_split'
        """,
        (hunt_id,),
    )
    waves = conn.execute(
        """
        SELECT * FROM cost_events
        WHERE hunt_id = ? AND cost_scope = 'wave'
        """,
        (hunt_id,),
    ).fetchall()
    reviewed = conn.execute(
        """
        SELECT * FROM funnel_events
        WHERE hunt_id = ? AND event_type = 'candidate_reviewed'
        """,
        (hunt_id,),
    ).fetchall()
    by_wave: dict[str, list[Any]] = {}
    for row in reviewed:
        wave = str(_loads(row["metadata_json"]).get("wave_id") or "")
        by_wave.setdefault(wave, []).append(row)
    for wave in waves:
        meta = _loads(wave["metadata_json"])
        wave_id = str(meta.get("wave_id") or "")
        people = by_wave.get(wave_id) or ([] if wave_id else list(reviewed))
        if wave_id and not people:
            people = []
        keys = []
        seen = set()
        aliases = canonical_identities([dict(row) for row in reviewed])
        for person in people:
            key = identity_of(dict(person), aliases)
            if key and key not in seen:
                seen.add(key)
                keys.append(person)
        stored = int(meta.get("reviewed_count") or 0)
        meta["reviewed_count"] = max(stored, len(keys))
        plan = allocate_wave(
            wave["cost_usd"],
            [identity_of(dict(person), aliases) for person in keys],
            denominator=meta["reviewed_count"] if meta["reviewed_count"] > len(keys) else None,
        )
        method = plan["allocation_method"] if plan["tracked"] else "unallocated"
        conn.execute(
            "UPDATE cost_events SET allocation_method = ?, metadata_json = ? WHERE id = ?",
            (method, _dumps(meta), wave["id"]),
        )
        if method != "equal_split":
            continue
        share_by_key = {item["key"]: item["allocated_usd"] for item in plan["shares"]}
        for person in keys:
            key = identity_of(dict(person), aliases)
            conn.execute(
                """
                INSERT OR IGNORE INTO cost_events (
                    id, profile_id, hunt_id, candidate_id, entity_key, stage, provider,
                    source_channel, signal_family, signal_url, cost_usd, elapsed_sec,
                    cost_scope, allocation_method, category, source_key, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'candidate', 'equal_split', ?, ?, ?, ?)
                """,
                (
                    new_id("alloc"),
                    wave["profile_id"],
                    hunt_id,
                    person["candidate_id"],
                    person["entity_key"] or "",
                    wave["stage"],
                    wave["provider"] or "",
                    person["source_channel"] or "",
                    person["signal_family"] or "",
                    "",
                    share_by_key.get(key),
                    wave["category"] or "research_api",
                    f"alloc:{wave['source_key'] or wave['id']}:{key}",
                    _dumps({"wave_id": wave_id, "parent_id": wave["id"], "allocated": True}),
                    wave["created_at"],
                ),
            )
    conn.commit()


def _rows(conn, sql: str, args: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, args)]


def _quality(event: dict[str, Any]) -> str:
    meta = event.get("metadata")
    if not isinstance(meta, dict):
        meta = _loads(event.get("metadata_json"))
    return str(meta.get("reply_quality") or event.get("event_type") or "").lower()


def funnel_counts(events: list[dict[str, Any]]) -> tuple[dict[str, int], dict[str, int]]:
    """Strict funnel counts, plus outcomes that skipped an earlier stage.

    A later stage only counts people who also passed every stage before it.
    Conversion therefore cannot exceed 100%. The skipped outcomes stay in
    legacyExcluded and are not deleted.
    """
    aliases = canonical_identities(events)

    def ids(types: set[str], *, quality: set[str] | None = None) -> set[str]:
        found = set()
        for event in events:
            if event.get("event_type") not in types:
                continue
            if quality is not None and _quality(event) not in quality:
                continue
            key = identity_of(event, aliases)
            if key:
                found.add(key)
        return found

    reviewed = ids({"candidate_reviewed"})
    raw_ready = ids({"outreach_ready"})
    raw_approved = ids({"human_approved"})
    raw_contact = ids({"contact_found"})
    raw_drafted = ids({"draft_generated"})
    raw_sendable = ids({"sendable"})
    raw_sent = ids({"email_sent"})
    raw_human = ids({"human_reply"}, quality=HUMAN_QUALITIES)
    raw_meaningful = ids({"human_reply"}, quality=MEANINGFUL_QUALITIES)
    raw_meeting = ids({"meeting_booked"})
    ready = reviewed & raw_ready
    approved = ready & raw_approved
    contact = approved & raw_contact
    drafted = contact & raw_drafted
    sendable = drafted & raw_sendable
    sent = sendable & raw_sent
    human = sent & raw_human
    meaningful = sent & raw_meaningful
    meeting = sent & raw_meeting
    trial = sent & ids({"trial_started"})
    customer = sent & ids({"customer_won"})
    counts = {
        "reviewed": len(reviewed),
        "outreach_ready": len(ready),
        "approved": len(approved),
        "contact_found": len(contact),
        "drafted": len(drafted),
        "sendable": len(sendable),
        "sent": len(sent),
        "human_reply": len(human),
        "meaningful_reply": len(meaningful),
        "meeting": len(meeting),
        "trial": len(trial),
        "customer": len(customer),
    }
    legacy = {
        "outreachReady": len(raw_ready - ready),
        "approved": len(raw_approved - approved),
        "contactFound": len(raw_contact - contact),
        "drafted": len(raw_drafted - drafted),
        "sent": len(raw_sent - sent),
        "humanReplies": len(raw_human - human),
        "meaningfulReplies": len(raw_meaningful - meaningful),
        "meetings": len(raw_meeting - meeting),
    }
    return counts, legacy


def _window_bounds(window: str, start: str | None, end: str | None) -> tuple[datetime | None, datetime | None]:
    now = datetime.now(timezone.utc)
    if window == "7d":
        return now - timedelta(days=7), now
    if window == "30d":
        return now - timedelta(days=30), now
    if window == "custom":
        return _parse_time(start), _parse_time(end)
    return None, None


def _in_cohort(
    events: list[dict[str, Any]],
    *,
    hunt_id: str | None,
    start: datetime | None,
    end: datetime | None,
    attribution_days: int | None,
    aliases: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    if not hunt_id and start is None and end is None:
        return events
    aliases = aliases or {}
    reviewed = [event for event in events if event.get("event_type") == "candidate_reviewed"]
    if hunt_id:
        reviewed = [event for event in reviewed if event.get("hunt_id") == hunt_id]
    kept_review = []
    for event in reviewed:
        at = _parse_time(event.get("occurred_at"))
        if start and (at is None or at < start):
            continue
        if end and (at is None or at > end):
            continue
        kept_review.append(event)
    origins = {identity_of(event, aliases): _parse_time(event.get("occurred_at")) for event in kept_review}
    keys = set(origins)
    selected = []
    for event in events:
        key = identity_of(event, aliases)
        if key not in keys:
            continue
        if attribution_days is None:
            selected.append(event)
            continue
        origin = origins.get(key)
        at = _parse_time(event.get("occurred_at"))
        if origin is None or at is None or at <= origin + timedelta(days=attribution_days):
            selected.append(event)
    return selected


def _tracked_spend(costs: list[dict[str, Any]]) -> float | None:
    priced = [float(row["cost_usd"]) for row in costs if counts_toward_total(row)]
    if not priced and any(row.get("cost_usd") is None for row in costs):
        return None
    if not priced:
        return None
    return sum(priced)


def _stage_rollup(costs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    labels = dict(STAGE_ORDER)
    for row in costs:
        if row.get("allocation_method") == "equal_split" and row.get("cost_scope") == "candidate":
            continue
        stage = normalize_stage(str(row.get("stage") or ""))
        label = labels.get(stage, "Other")
        bucket = buckets.setdefault(label, {"tracked": 0.0, "priced": 0, "calls": 0})
        bucket["calls"] += 1
        if row.get("cost_usd") is not None and counts_toward_total(row):
            bucket["tracked"] += float(row["cost_usd"])
            bucket["priced"] += 1
    order = [label for _, label in STAGE_ORDER] + ["Other"]
    out = []
    for label in order:
        bucket = buckets.get(label)
        if not bucket:
            continue
        tracked = bucket["priced"] > 0
        out.append(
            {
                "stage": label,
                "usd": bucket["tracked"] if tracked else None,
                "tracked": tracked,
                "calls": bucket["calls"],
            }
        )
    return out


def _source_of(
    event: dict[str, Any],
    people: dict[str, dict[str, str]],
    aliases: dict[str, str] | None = None,
) -> tuple[str, str]:
    person = people.get(identity_of(event, aliases)) or {}
    channel = person.get("source") or event.get("source_channel") or ""
    family = person.get("family") or event.get("signal_family") or ""
    return channel or "Unknown", family or "Unknown"


def _attribute(costs: list[dict[str, Any]], funnel: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    aliases = canonical_identities(funnel)
    people: dict[str, dict[str, str]] = {}
    for event in funnel:
        if event.get("event_type") != "candidate_reviewed":
            continue
        key = identity_of(event, aliases)
        people[key] = {
            "source": event.get("source_channel") or "Unknown",
            "family": event.get("signal_family") or "Unknown",
            "hunt": event.get("hunt_id") or "Unknown",
        }
    def bump(table: dict[str, dict[str, Any]], name: str, usd: float | None, person: str, stage: str) -> None:
        row = table.setdefault(
            name,
            {
                "name": name,
                "spend": 0.0,
                "priced": False,
                "stages": {},
            },
        )
        if usd is not None:
            row["spend"] += usd
            row["priced"] = True
        if person:
            row["stages"].setdefault(stage, set()).add(person)

    def empty_tables():
        return {}, {}, {}

    sources, families, hunts = empty_tables()
    for event in funnel:
        if event.get("event_type") != "candidate_reviewed":
            continue
        key = identity_of(event, aliases)
        channel, family = _source_of(event, people, aliases)
        hunt = event.get("hunt_id") or "Unknown"
        bump(sources, channel, None, key, "reviewed")
        bump(families, family, None, key, "reviewed")
        bump(hunts, hunt, None, key, "reviewed")
    outcome_stage = {
        "outreach_ready": "ready",
        "email_sent": "sent",
        "meeting_booked": "meeting",
    }
    for event in funnel:
        stage = outcome_stage.get(str(event.get("event_type")))
        if event.get("event_type") == "human_reply":
            quality = _quality(event)
            if quality in HUMAN_QUALITIES:
                stage = "human"
            if quality in MEANINGFUL_QUALITIES:
                channel, family = _source_of(event, people, aliases)
                hunt = people.get(identity_of(event, aliases), {}).get("hunt") or event.get("hunt_id") or "Unknown"
                key = identity_of(event, aliases)
                bump(sources, channel, None, key, "meaningful")
                bump(families, family, None, key, "meaningful")
                bump(hunts, hunt, None, key, "meaningful")
        if not stage:
            continue
        channel, family = _source_of(event, people, aliases)
        hunt = people.get(identity_of(event, aliases), {}).get("hunt") or event.get("hunt_id") or "Unknown"
        key = identity_of(event, aliases)
        bump(sources, channel, None, key, stage)
        bump(families, family, None, key, stage)
        bump(hunts, hunt, None, key, stage)
    for cost in costs:
        if not counts_toward_total(cost):
            continue
        amount = float(cost["cost_usd"])
        if cost.get("cost_scope") == "wave" and cost.get("allocation_method") == "equal_split":
            continue
        if cost.get("cost_scope") == "candidate" and cost.get("allocation_method") == "direct":
            channel, family = _source_of(cost, people, aliases)
            hunt = cost.get("hunt_id") or "Unknown"
        elif cost.get("cost_scope") == "wave":
            channel, family, hunt = "Unknown", "Unknown", cost.get("hunt_id") or "Unknown"
        else:
            channel, family, hunt = "Unknown", "Unknown", cost.get("hunt_id") or "Unknown"
        bump(sources, channel, amount, "", "spend")
        bump(families, family, amount, "", "spend")
        bump(hunts, hunt, amount, "", "spend")
    allocated_by_parent: dict[str, float] = {}
    for cost in costs:
        if cost.get("allocation_method") != "equal_split" or cost.get("cost_scope") != "candidate":
            continue
        if cost.get("cost_usd") is None:
            continue
        parent = str((cost.get("metadata") or {}).get("parent_id") or "")
        amount = float(cost["cost_usd"])
        if parent:
            allocated_by_parent[parent] = allocated_by_parent.get(parent, 0.0) + amount
        channel, family = _source_of(cost, people, aliases)
        hunt = cost.get("hunt_id") or "Unknown"
        bump(sources, channel, amount, "", "spend")
        bump(families, family, amount, "", "spend")
        bump(hunts, hunt, amount, "", "spend")
    for cost in costs:
        if not (cost.get("cost_scope") == "wave" and cost.get("allocation_method") == "equal_split"):
            continue
        if cost.get("cost_usd") is None:
            continue
        remainder = float(cost["cost_usd"]) - allocated_by_parent.get(str(cost.get("id") or ""), 0.0)
        if remainder <= 1e-9:
            continue
        hunt = cost.get("hunt_id") or "Unknown"
        bump(sources, "Unknown", remainder, "", "spend")
        bump(families, "Unknown", remainder, "", "spend")
        bump(hunts, hunt, remainder, "", "spend")

    def finish(table: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        rows = []
        for name, row in sorted(table.items()):
            stages = row.get("stages") or {}
            reviewed_n = len(stages.get("reviewed", set()))
            ready_n = len(stages.get("ready", set()))
            sent_n = len(stages.get("sent", set()))
            human_n = len(stages.get("human", set()))
            meaningful_n = len(stages.get("meaningful", set()))
            meeting_n = len(stages.get("meeting", set()))
            spend = row["spend"] if row["priced"] else None
            rows.append(
                {
                    "name": name,
                    "spend": spend,
                    "reviewed": reviewed_n,
                    "ready": ready_n,
                    "sent": sent_n,
                    "humanReplies": human_n,
                    "meaningfulReplies": meaningful_n,
                    "meetings": meeting_n,
                    "costPerReady": unit_cost(spend, ready_n),
                    "costPerMeaningful": unit_cost(spend, meaningful_n),
                }
            )
        return rows

    return finish(sources), finish(families), finish(hunts)


def _decorate(event: dict[str, Any]) -> dict[str, Any]:
    row = dict(event)
    if "metadata" not in row:
        row["metadata"] = _loads(row.get("metadata_json"))
    return row


def campaign_report(
    conn,
    profile_id: str,
    *,
    window: str = "all",
    hunt_id: str | None = None,
    start: str | None = None,
    end: str | None = None,
    attribution_days: int = 30,
) -> dict[str, Any]:
    sync_profile(conn, profile_id)
    hunt_ids = [
        row["id"]
        for row in conn.execute("SELECT id FROM hunts WHERE profile_id = ?", (profile_id,))
    ]
    for one in hunt_ids:
        refresh_wave_allocations(conn, one)
    costs = [_decorate(row) for row in _rows(conn, "SELECT * FROM cost_events WHERE profile_id = ?", (profile_id,))]
    funnel = [_decorate(row) for row in _rows(conn, "SELECT * FROM funnel_events WHERE profile_id = ?", (profile_id,))]
    start_at, end_at = _window_bounds(window, start, end)
    filtered = window != "all" or bool(hunt_id)
    aliases = canonical_identities(funnel)
    if filtered:
        funnel = _in_cohort(
            funnel,
            hunt_id=hunt_id,
            start=start_at,
            end=end_at,
            attribution_days=attribution_days if window != "all" else None,
            aliases=aliases,
        )
        people = {identity_of(event, aliases) for event in funnel}
        costs = [
            row
            for row in costs
            if (hunt_id and row.get("hunt_id") == hunt_id)
            or identity_of(row, aliases) in people
            or (row.get("cost_scope") == "wave" and row.get("hunt_id") in {event.get("hunt_id") for event in funnel})
        ]
    counts, legacy = funnel_counts(funnel)
    named_reviewed = counts["reviewed"]
    if window == "all":
        for hunt in conn.execute(
            "SELECT id, reviewed_n FROM hunts WHERE profile_id = ?",
            (profile_id,),
        ):
            if hunt_id and hunt["id"] != hunt_id:
                continue
            named = {
                identity_of(_decorate(row), aliases)
                for row in conn.execute(
                    "SELECT * FROM funnel_events WHERE hunt_id = ? AND event_type = 'candidate_reviewed'",
                    (hunt["id"],),
                )
            }
            extra = max(0, int(hunt["reviewed_n"] or 0) - len(named))
            counts["reviewed"] += extra
    spend = _tracked_spend(costs)
    untracked = sum(
        1
        for row in costs
        if row.get("cost_usd") is None
        and not (
            row.get("allocation_method") == "equal_split" and row.get("cost_scope") == "candidate"
        )
    )
    excluded = [
        row
        for row in costs
        if row.get("cost_usd") is not None and str(row.get("category") or "") not in VARIABLE_CATEGORIES
    ]
    stages = _stage_rollup(costs)
    by_source, by_signal, by_hunt = _attribute(costs, funnel)
    attributed = sum(row["reviewed"] for row in by_source)
    if counts["reviewed"] > attributed:
        unknown = next((row for row in by_source if row["name"] == "Unknown"), None)
        if unknown is None:
            unknown = {
                "name": "Unknown",
                "spend": None,
                "reviewed": 0,
                "ready": 0,
                "sent": 0,
                "humanReplies": 0,
                "meaningfulReplies": 0,
                "meetings": 0,
                "costPerReady": None,
                "costPerMeaningful": None,
            }
            by_source.append(unknown)
        unknown["reviewed"] += counts["reviewed"] - attributed
    funnel_rows = []
    for key, label, parent in FUNNEL_ROWS:
        parent_count = counts[parent] if parent else 0
        conversion = ratio(counts[key], parent_count) if parent else {"num": counts[key], "den": None, "rate": None}
        funnel_rows.append(
            {
                "stage": label,
                "key": key,
                "people": counts[key],
                "conversion": conversion,
                "unitCost": unit_cost(spend, counts[key]),
            }
        )
    unit = {
        "reviewed": unit_cost(spend, counts["reviewed"]),
        "outreachReady": unit_cost(spend, counts["outreach_ready"]),
        "approved": unit_cost(spend, counts["approved"]),
        "contactFound": unit_cost(spend, counts["contact_found"]),
        "sent": unit_cost(spend, counts["sent"]),
        "humanReply": unit_cost(spend, counts["human_reply"]),
        "meaningfulReply": unit_cost(spend, counts["meaningful_reply"]),
        "meeting": unit_cost(spend, counts["meeting"]),
        "trial": unit_cost(spend, counts["trial"]),
        "customer": unit_cost(spend, counts["customer"]),
    }
    if window == "all" and not hunt_id:
        window_label = "All time. Outcomes are the campaign lifetime."
    else:
        window_label = (
            f"Cohort filter: {window}. "
            f"Outcomes stay inside {attribution_days} days of first review."
        )
    hunt_totals: dict[str, float] = {}
    for row in costs:
        if not counts_toward_total(row):
            continue
        if row.get("cost_scope") == "wave" and row.get("allocation_method") == "equal_split":
            hunt_totals[row.get("hunt_id") or ""] = hunt_totals.get(row.get("hunt_id") or "", 0.0) + float(row["cost_usd"])
        elif not (row.get("allocation_method") == "equal_split" and row.get("cost_scope") == "candidate"):
            hunt_totals[row.get("hunt_id") or ""] = hunt_totals.get(row.get("hunt_id") or "", 0.0) + float(row["cost_usd"])
    return {
        "profileId": profile_id,
        "scopeNote": SCOPE_NOTE,
        "windowLabel": window_label,
        "attributionDays": attribution_days,
        "totalUsd": spend,
        "untrackedEvents": untracked,
        "excludedUsd": sum(float(row["cost_usd"]) for row in excluded) if excluded else None,
        "hunts": len({row.get("hunt_id") for row in costs if row.get("hunt_id")}),
        "counts": {
            "reviewed": counts["reviewed"],
            "saved": named_reviewed,
            "outreachReady": counts["outreach_ready"],
            "contactFound": counts["contact_found"],
            "sent": counts["sent"],
            "humanReplies": counts["human_reply"],
            "meaningfulReplies": counts["meaningful_reply"],
            "meetings": counts["meeting"],
        },
        "funnel": funnel_rows,
        "unitCosts": unit,
        "byStage": [{"stage": row["stage"], "usd": row["usd"]} for row in stages if row["usd"] is not None],
        "stages": stages,
        "byHunt": [{"huntId": key, "usd": value} for key, value in hunt_totals.items()],
        "bySource": by_source,
        "bySignal": by_signal,
        "byHuntDetail": by_hunt,
        "namedReviewed": named_reviewed,
        "legacyExcluded": legacy,
    }


def forecast_profile(conn, profile_id: str, target: int) -> dict[str, Any]:
    history = []
    for hunt in conn.execute(
        "SELECT id, reviewed_n, status FROM hunts WHERE profile_id = ? AND status = 'done'",
        (profile_id,),
    ):
        reviewed_events = conn.execute(
            "SELECT COUNT(*) AS n FROM funnel_events WHERE hunt_id = ? AND event_type = 'candidate_reviewed'",
            (hunt["id"],),
        ).fetchone()["n"]
        ready_events = conn.execute(
            "SELECT COUNT(*) AS n FROM funnel_events WHERE hunt_id = ? AND event_type = 'outreach_ready'",
            (hunt["id"],),
        ).fetchone()["n"]
        reviewed = max(int(hunt["reviewed_n"] or 0), int(reviewed_events or 0))
        cost_rows = _rows(
            conn,
            "SELECT * FROM cost_events WHERE hunt_id = ?",
            (hunt["id"],),
        )
        priced = [float(row["cost_usd"]) for row in cost_rows if counts_toward_total(row) and not (
            row.get("allocation_method") == "equal_split" and row.get("cost_scope") == "candidate"
        )]
        # Wave totals already included; allocated copies excluded by counts_toward_total.
        history.append(
            {
                "reviewed": reviewed,
                "ready": int(ready_events or 0),
                "cost_usd": sum(priced) if priced else None,
            }
        )
    return forecast_hunt(history, target)


def candidate_trace(conn, row: dict[str, Any], profile_name: str = "") -> dict[str, Any]:
    rec = _loads(row.get("candidate_json"))
    entity = str(row.get("entity_key") or "")
    costs = _rows(
        conn,
        """
        SELECT * FROM cost_events
        WHERE profile_id = ? AND (candidate_id = ? OR (entity_key != '' AND entity_key = ?))
        """,
        (row["profile_id"], row["id"], entity),
    )
    hunt = None
    if row.get("hunt_id"):
        hunt = conn.execute("SELECT created_at FROM hunts WHERE id = ?", (row["hunt_id"],)).fetchone()
    labels = {
        "discovery_web": "Allocated discovery" if True else "Discovery Web",
        "discovery_x": "Allocated discovery",
        "qualification": "Qualification",
        "resolution": "Resolution",
        "deepening": "Deepening",
        "drafting": "Drafting",
        "contact_lookup": "Contact lookup",
    }
    lines = []
    total = 0.0
    priced = False
    for key, label in (
        ("discovery_web", "Allocated discovery, Web"),
        ("discovery_x", "Allocated discovery, X"),
        ("qualification", "Qualification"),
        ("resolution", "Resolution"),
        ("deepening", "Deepening"),
        ("drafting", "Drafting"),
        ("contact_lookup", "Contact lookup"),
    ):
        matched = [item for item in costs if normalize_stage(str(item.get("stage") or "")) == key]
        if key in DISCOVERY_STAGES:
            matched = [item for item in matched if item.get("allocation_method") == "equal_split"]
        else:
            matched = [item for item in matched if item.get("allocation_method") != "equal_split"]
        if not matched and key in DISCOVERY_STAGES:
            continue
        known = [item for item in matched if item.get("cost_usd") is not None]
        if not matched:
            lines.append({"label": label, "usd": None, "tracked": False, "allocated": False})
            continue
        if not known:
            lines.append({"label": label, "usd": None, "tracked": False, "allocated": key in DISCOVERY_STAGES})
            continue
        amount = sum(float(item["cost_usd"]) for item in known)
        total += amount
        priced = True
        lines.append(
            {
                "label": label,
                "usd": amount,
                "tracked": True,
                "allocated": key in DISCOVERY_STAGES,
            }
        )
    created = hunt["created_at"] if hunt else row.get("created_at")
    parsed = _parse_time(created)
    when = parsed.strftime("%b %-d") if parsed else ""
    hunt_label = " ".join(part for part in (when, profile_name, "hunt") if part).strip()
    family = str(rec.get("signal_family") or "")
    return {
        "sourceChannel": _channel_label(str(row.get("found_on") or "")) or "Unknown",
        "signalFamily": family or "Unknown",
        "huntLabel": hunt_label,
        "lines": lines,
        "totalUsd": total if priced else None,
    }
