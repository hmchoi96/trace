"""Funnel unit economics. Dollars stay null when the provider price is unknown."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trace_app import db, service  # noqa: E402
from trace_economics import (  # noqa: E402
    allocate_wave,
    campaign_report,
    forecast_hunt,
    import_research_events,
    record_funnel,
    record_review_batch,
    refresh_wave_allocations,
    unit_cost,
)


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACE_DB_PATH", str(tmp_path / "trace.db"))
    db.reset_connection()
    connection = service.init(str(tmp_path / "trace.db"))
    yield connection
    db.reset_connection()


def _hunt(conn, hunt_id: str, profile_id: str = "oneaway", reviewed_n: int = 0) -> None:
    conn.execute(
        """
        INSERT INTO hunts (id, profile_id, snapshot_json, limit_n, status, created_at, reviewed_n)
        VALUES (?, ?, '{}', 5, 'done', '2026-10-01T00:00:00+00:00', ?)
        """,
        (hunt_id, profile_id, reviewed_n),
    )
    conn.commit()


def test_stage_costs_roll_up_to_hunt_and_campaign(conn):
    _hunt(conn, "h1")
    _hunt(conn, "h2")
    import_research_events(
        conn,
        [
            {"stage": "qualification", "entity_key": "a", "cost_usd": 0.4, "request_id": "q1"},
            {"stage": "discovery_web", "cost_usd": 1.0, "request_id": "w1", "wave_id": "wave-1"},
        ],
        profile_id="oneaway",
        hunt_id="h1",
    )
    import_research_events(
        conn,
        [{"stage": "discovery_x", "cost_usd": 0.5, "request_id": "x1", "wave_id": "wave-1"}],
        profile_id="oneaway",
        hunt_id="h2",
    )
    report = campaign_report(conn, "oneaway")
    assert report["totalUsd"] == pytest.approx(1.9)
    by_hunt = {row["huntId"]: row["usd"] for row in report["byHunt"]}
    assert by_hunt["h1"] == pytest.approx(1.4)
    assert by_hunt["h2"] == pytest.approx(0.5)
    stages = {row["stage"]: row["usd"] for row in report["stages"] if row["tracked"]}
    assert stages["Qualification"] == pytest.approx(0.4)
    assert stages["Discovery Web"] == pytest.approx(1.0)


def test_wave_cost_is_split_across_fresh_reviewed_people(conn):
    _hunt(conn, "h1")
    import_research_events(
        conn,
        [{"stage": "discovery_web", "cost_usd": 2.4, "request_id": "wave", "wave_id": "wave-1"}],
        profile_id="oneaway",
        hunt_id="h1",
    )
    record_review_batch(
        conn,
        "oneaway",
        "h1",
        [
            {"entity_key": f"p{i}", "source_channel": "web", "kind": "reject", "wave_id": "wave-1"}
            for i in range(4)
        ],
    )
    refresh_wave_allocations(conn, "h1")
    shares = conn.execute(
        """
        SELECT cost_usd FROM cost_events
        WHERE hunt_id = 'h1' AND allocation_method = 'equal_split' AND cost_scope = 'candidate'
        """
    ).fetchall()
    assert len(shares) == 4
    assert [row["cost_usd"] for row in shares] == pytest.approx([0.6, 0.6, 0.6, 0.6])
    report = campaign_report(conn, "oneaway")
    assert report["totalUsd"] == pytest.approx(2.4)


def test_zero_yield_discovery_stays_on_the_hunt(conn):
    _hunt(conn, "h1")
    import_research_events(
        conn,
        [{"stage": "discovery_web", "cost_usd": 1.25, "request_id": "empty", "wave_id": "wave-9"}],
        profile_id="oneaway",
        hunt_id="h1",
    )
    refresh_wave_allocations(conn, "h1")
    row = conn.execute(
        "SELECT allocation_method, cost_scope, cost_usd FROM cost_events WHERE request_id = 'empty'"
    ).fetchone()
    assert row["allocation_method"] == "unallocated"
    assert row["cost_scope"] == "wave"
    assert campaign_report(conn, "oneaway")["totalUsd"] == pytest.approx(1.25)
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM cost_events WHERE allocation_method = 'equal_split'"
    ).fetchone()["n"] == 0


def test_direct_cost_stays_on_the_candidate(conn):
    _hunt(conn, "h1")
    import_research_events(
        conn,
        [{
            "stage": "qualification",
            "entity_key": "ada",
            "candidate_id": "cand_ada",
            "cost_usd": 0.31,
            "request_id": "qual-ada",
        }],
        profile_id="oneaway",
        hunt_id="h1",
    )
    row = conn.execute("SELECT * FROM cost_events WHERE request_id = 'qual-ada'").fetchone()
    assert row["candidate_id"] == "cand_ada"
    assert row["cost_scope"] == "candidate"
    assert row["allocation_method"] == "direct"


def test_unknown_cost_is_not_a_zero(conn):
    _hunt(conn, "h1")
    import_research_events(
        conn,
        [{"stage": "drafting", "entity_key": "ada", "cost_usd": None, "request_id": "draft-1"}],
        profile_id="oneaway",
        hunt_id="h1",
    )
    stored = conn.execute("SELECT cost_usd FROM cost_events WHERE request_id = 'draft-1'").fetchone()
    assert stored["cost_usd"] is None
    report = campaign_report(conn, "oneaway")
    assert report["totalUsd"] is None
    assert report["untrackedEvents"] == 1
    drafting = next(row for row in report["stages"] if row["stage"] == "Drafting")
    assert drafting["tracked"] is False
    assert drafting["usd"] is None


def test_retry_with_the_same_request_is_not_double_counted(conn):
    _hunt(conn, "h1")
    event = {"stage": "qualification", "entity_key": "ada", "cost_usd": 0.2, "request_id": "same"}
    import_research_events(conn, [event], profile_id="oneaway", hunt_id="h1")
    import_research_events(conn, [event], profile_id="oneaway", hunt_id="h1")
    assert conn.execute("SELECT COUNT(*) AS n FROM cost_events").fetchone()["n"] == 1
    assert campaign_report(conn, "oneaway")["totalUsd"] == pytest.approx(0.2)


def test_rejected_person_counts_as_reviewed_only(conn):
    _hunt(conn, "h1")
    record_review_batch(
        conn,
        "oneaway",
        "h1",
        [{"entity_key": "nope", "source_channel": "web", "kind": "reject", "wave_id": "wave-1"}],
    )
    import_research_events(
        conn,
        [{"stage": "qualification", "entity_key": "nope", "cost_usd": 0.15, "request_id": "rej"}],
        profile_id="oneaway",
        hunt_id="h1",
    )
    report = campaign_report(conn, "oneaway")
    assert report["counts"]["reviewed"] == 1
    assert report["counts"]["outreachReady"] == 0
    assert report["totalUsd"] == pytest.approx(0.15)


def test_send_now_is_one_outreach_ready_event(conn):
    record_funnel(
        conn,
        profile_id="oneaway",
        event_type="candidate_reviewed",
        candidate_id="cand_1",
        entity_key="ada",
    )
    record_funnel(
        conn,
        profile_id="oneaway",
        event_type="outreach_ready",
        candidate_id="cand_1",
        entity_key="ada",
    )
    record_funnel(
        conn,
        profile_id="oneaway",
        event_type="outreach_ready",
        candidate_id="cand_1",
        entity_key="ada",
    )
    report = campaign_report(conn, "oneaway")
    assert report["counts"]["outreachReady"] == 1
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM funnel_events WHERE event_type = 'outreach_ready'"
    ).fetchone()["n"] == 1


def test_contact_failure_is_not_found_or_sendable(conn):
    record_funnel(conn, profile_id="oneaway", event_type="candidate_reviewed", candidate_id="cand_1", entity_key="ada")
    record_funnel(conn, profile_id="oneaway", event_type="human_approved", candidate_id="cand_1", entity_key="ada")
    record_funnel(conn, profile_id="oneaway", event_type="contact_not_found", candidate_id="cand_1", entity_key="ada")
    report = campaign_report(conn, "oneaway")
    assert report["counts"]["contactFound"] == 0
    assert report["counts"]["sent"] == 0
    sendable = next(row for row in report["funnel"] if row["key"] == "sent")
    assert sendable["conversion"]["rate"] is None


def test_automated_reply_does_not_count_as_human(conn):
    record_funnel(
        conn,
        profile_id="oneaway",
        event_type="human_reply",
        candidate_id="cand_1",
        entity_key="ada",
        metadata={"reply_quality": "automated"},
    )
    report = campaign_report(conn, "oneaway")
    assert report["counts"]["humanReplies"] == 0
    assert report["counts"]["meaningfulReplies"] == 0


def _through_sendable(conn, candidate_id: str, entity_key: str) -> None:
    for event_type in (
        "candidate_reviewed",
        "outreach_ready",
        "human_approved",
        "contact_found",
        "draft_generated",
        "sendable",
    ):
        record_funnel(
            conn,
            profile_id="oneaway",
            event_type=event_type,
            candidate_id=candidate_id,
            entity_key=entity_key,
        )


def test_positive_and_engaged_are_meaningful(conn):
    for candidate_id, entity_key, quality in (
        ("cand_1", "a", "positive"),
        ("cand_2", "b", "engaged"),
        ("cand_3", "c", "neutral"),
    ):
        _through_sendable(conn, candidate_id, entity_key)
        record_funnel(
            conn,
            profile_id="oneaway",
            event_type="email_sent",
            candidate_id=candidate_id,
            entity_key=entity_key,
            source_key=f"email_sent:{candidate_id}",
        )
        record_funnel(
            conn,
            profile_id="oneaway",
            event_type="human_reply",
            candidate_id=candidate_id,
            entity_key=entity_key,
            metadata={"reply_quality": quality},
        )
    report = campaign_report(conn, "oneaway")
    assert report["counts"]["humanReplies"] == 3
    assert report["counts"]["meaningfulReplies"] == 2


def test_followups_do_not_duplicate_the_prospect(conn):
    _through_sendable(conn, "cand_1", "ada")
    record_funnel(
        conn, profile_id="oneaway", event_type="email_sent", candidate_id="cand_1", entity_key="ada",
        source_key="email_sent:send_1",
    )
    record_funnel(
        conn, profile_id="oneaway", event_type="email_sent", candidate_id="cand_1", entity_key="ada",
        source_key="email_sent:send_2",
    )
    report = campaign_report(conn, "oneaway")
    assert report["counts"]["sent"] == 1
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM funnel_events WHERE event_type = 'email_sent'"
    ).fetchone()["n"] == 2


def test_duplicate_import_does_not_duplicate_funnel_events(conn):
    for _ in range(2):
        record_funnel(
            conn,
            profile_id="oneaway",
            event_type="candidate_reviewed",
            candidate_id="cand_1",
            entity_key="ada",
            source_channel="Web",
        )
    assert conn.execute("SELECT COUNT(*) AS n FROM funnel_events").fetchone()["n"] == 1


def test_source_spend_matches_the_campaign(conn):
    _hunt(conn, "h1")
    import_research_events(
        conn,
        [
            {"stage": "discovery_web", "cost_usd": 2.0, "request_id": "wave", "wave_id": "wave-1"},
            {"stage": "qualification", "entity_key": "web-person", "cost_usd": 0.5, "request_id": "q-web"},
            {"stage": "qualification", "entity_key": "x-person", "cost_usd": 0.5, "request_id": "q-x"},
        ],
        profile_id="oneaway",
        hunt_id="h1",
    )
    record_review_batch(
        conn,
        "oneaway",
        "h1",
        [
            {"entity_key": "web-person", "source_channel": "web", "signal_family": "hiring", "kind": "ready", "wave_id": "wave-1"},
            {"entity_key": "x-person", "source_channel": "x", "signal_family": "hiring", "kind": "reject", "wave_id": "wave-1"},
        ],
    )
    report = campaign_report(conn, "oneaway")
    assert sum(row["spend"] or 0 for row in report["bySource"]) == pytest.approx(report["totalUsd"])
    assert sum(row["spend"] or 0 for row in report["bySignal"]) == pytest.approx(report["totalUsd"])
    assert report["counts"]["outreachReady"] == 1
    assert report["counts"]["reviewed"] == 2


def test_unit_cost_is_na_when_the_denominator_is_zero():
    assert unit_cost(10.0, 0) is None
    assert unit_cost(None, 4) is None
    assert allocate_wave(2.4, ["a", "b"])["shares"][0]["allocated_usd"] == pytest.approx(1.2)


def test_forecast_uses_ready_rate_and_the_review_cap():
    history = [
        {"reviewed": 10, "ready": 5, "cost_usd": 10.0},
        {"reviewed": 10, "ready": 5, "cost_usd": 20.0},
    ]
    forecast = forecast_hunt(history, 5)
    assert forecast["enoughHistory"] is True
    assert forecast["expectedReviewed"] == 10
    assert forecast["maximumReviewed"] == 25
    assert forecast["rangeMethod"] == "recent min/max cost per reviewed candidate"
    assert forecast["low"] == pytest.approx(10.0)
    assert forecast["high"] == pytest.approx(20.0)


def test_forecast_without_history_does_not_invent_a_price():
    forecast = forecast_hunt([{"reviewed": 4, "ready": 1, "cost_usd": 3.0}], 5)
    assert forecast["enoughHistory"] is False
    assert forecast["expectedUsd"] is None
    assert forecast["maximumReviewed"] == 25
    assert "Not enough completed hunts" in forecast["message"]


def test_import_is_idempotent_and_keeps_missing_cost_unknown(conn):
    events = [
        {"stage": "qualification", "entity_key": "ada", "cost_usd": None, "elapsed_sec": 1.5},
        {"stage": "discovery_web", "cost_usd": 0.0, "request_id": "cached-zero"},
    ]
    import_research_events(conn, events, profile_id="oneaway", hunt_id="h1")
    import_research_events(conn, events, profile_id="oneaway", hunt_id="h1")
    rows = conn.execute("SELECT stage, cost_usd FROM cost_events ORDER BY stage").fetchall()
    assert len(rows) == 2
    by_stage = {row["stage"]: row["cost_usd"] for row in rows}
    assert by_stage["qualification"] is None
    assert by_stage["discovery_web"] == 0.0


def test_each_wave_is_split_by_its_own_reviewed_people(conn):
    _hunt(conn, "h1", reviewed_n=7)
    import_research_events(
        conn,
        [
            {"stage": "discovery_web", "cost_usd": 4.0, "request_id": "w1", "wave_id": "wave-1"},
            {"stage": "discovery_web", "cost_usd": 3.0, "request_id": "w2", "wave_id": "wave-2"},
        ],
        profile_id="oneaway",
        hunt_id="h1",
    )
    record_review_batch(
        conn,
        "oneaway",
        "h1",
        [{"entity_key": f"a{i}", "source_channel": "web", "wave_id": "wave-1"} for i in range(4)]
        + [{"entity_key": f"b{i}", "source_channel": "x", "wave_id": "wave-2"} for i in range(3)],
    )
    refresh_wave_allocations(conn, "h1")
    shares = {
        row["entity_key"]: row["cost_usd"]
        for row in conn.execute(
            """
            SELECT entity_key, cost_usd FROM cost_events
            WHERE allocation_method = 'equal_split' AND cost_scope = 'candidate'
            """
        )
    }
    assert shares == {**{f"a{i}": 1.0 for i in range(4)}, **{f"b{i}": 1.0 for i in range(3)}}
    report = campaign_report(conn, "oneaway")
    assert report["totalUsd"] == pytest.approx(7.0)
    unknown_spend = sum(row["spend"] or 0 for row in report["bySource"] if row["name"] == "Unknown")
    assert unknown_spend == pytest.approx(0)


def test_early_entity_key_stays_on_the_same_person(conn):
    _hunt(conn, "h1")
    record_funnel(
        conn,
        profile_id="oneaway",
        event_type="candidate_reviewed",
        candidate_id="cand_ada",
        entity_key="ada|acme",
        source_channel="Web",
        signal_family="action_trigger",
        hunt_id="h1",
        metadata={"entity_aliases": ["ada"]},
    )
    import_research_events(
        conn,
        [
            {"stage": "qualification", "entity_key": "ada", "cost_usd": 0.4, "request_id": "q"},
            {"stage": "deepening", "entity_key": "ada|acme", "cost_usd": 0.6, "request_id": "d"},
        ],
        profile_id="oneaway",
        hunt_id="h1",
    )
    report = campaign_report(conn, "oneaway")
    web = next(row for row in report["bySource"] if row["name"] == "Web")
    family = next(row for row in report["bySignal"] if row["name"] == "action_trigger")
    assert web["spend"] == pytest.approx(1.0)
    assert family["spend"] == pytest.approx(1.0)
    unknown = next((row for row in report["bySource"] if row["name"] == "Unknown"), None)
    assert unknown is None or (unknown["spend"] or 0) == pytest.approx(0)


def test_operational_states_stay_out_of_the_economics_funnel(conn):
    record_funnel(conn, profile_id="oneaway", event_type="candidate_reviewed", candidate_id="ready_person", entity_key="ready")
    record_funnel(conn, profile_id="oneaway", event_type="outreach_ready", candidate_id="ready_person", entity_key="ready")
    record_funnel(conn, profile_id="oneaway", event_type="human_approved", candidate_id="ready_person", entity_key="ready")
    record_funnel(conn, profile_id="oneaway", event_type="human_approved", candidate_id="old_person", entity_key="old")
    report = campaign_report(conn, "oneaway")
    assert [row["key"] for row in report["funnel"]] == [
        "reviewed",
        "outreach_ready",
        "sent",
        "human_reply",
        "meaningful_reply",
        "meeting",
        "customer",
    ]
    ready = next(row for row in report["funnel"] if row["key"] == "outreach_ready")
    assert ready["people"] == 1
    assert ready["conversion"]["rate"] == 1
    assert report["counts"]["outreachReady"] == 1
    assert report["legacyExcluded"]["approved"] == 1


def test_hunt_table_uses_the_start_date(conn):
    _hunt(conn, "hunt_old")
    _hunt(conn, "hunt_new")
    conn.execute(
        "UPDATE hunts SET created_at = ? WHERE id = ?",
        ("2026-10-04T02:17:00+00:00", "hunt_old"),
    )
    conn.execute(
        "UPDATE hunts SET created_at = ? WHERE id = ?",
        ("2026-10-05T21:34:00+00:00", "hunt_new"),
    )
    conn.commit()
    import_research_events(
        conn,
        [{"stage": "qualification", "entity_key": "ada", "cost_usd": 0.4, "request_id": "q-old"}],
        profile_id="oneaway",
        hunt_id="hunt_old",
    )
    import_research_events(
        conn,
        [{"stage": "qualification", "entity_key": "bea", "cost_usd": 0.6, "request_id": "q-new"}],
        profile_id="oneaway",
        hunt_id="hunt_new",
    )
    report = campaign_report(conn, "oneaway")
    names = [row["name"] for row in report["byHuntDetail"]]
    assert names[0].startswith("Oct 5")
    assert names[1].startswith("Oct 4")
    assert all(not name.startswith("hunt_") for name in names)
    labels = {row["huntId"]: row["label"] for row in report["byHunt"]}
    assert labels["hunt_new"].startswith("Oct 5")
    assert labels["hunt_old"].startswith("Oct 4")


def test_company_cost_and_held_back_people_stay_out_of_unknown(conn):
    from trace_economics import held_back_people, relink_cost_identities

    _hunt(conn, "h1")
    record_funnel(
        conn,
        profile_id="oneaway",
        event_type="candidate_reviewed",
        candidate_id="cand_zach",
        entity_key="zach bruggeman|ramp",
        source_channel="Web",
        signal_family="behavior_trigger",
        hunt_id="h1",
    )
    conn.execute(
        """
        INSERT INTO candidates (
            id, hunt_id, profile_id, name, title, company, found_on, entity_key,
            candidate_json, created_at
        ) VALUES ('cand_zach', 'h1', 'oneaway', 'Zach Bruggeman', '', 'Ramp', 'Web',
                  'zach bruggeman|ramp', '{}', '2026-10-05T00:00:00+00:00')
        """
    )
    conn.execute(
        """
        INSERT INTO cost_events (
            id, profile_id, hunt_id, stage, cost_usd, elapsed_sec, created_at,
            source_key, cost_scope, allocation_method, category
        ) VALUES ('cost_ramp', 'oneaway', 'h1', 'qualification', 0.18, 40.0,
                  '2026-10-05T00:00:00+00:00', 'cost_ramp', 'hunt', 'direct', 'research_api')
        """
    )
    conn.commit()
    relink_cost_identities(
        conn,
        "h1",
        [{"stage": "qualification", "person_name": "Ramp", "entity_key": "ramp", "cost_usd": 0.18, "elapsed_sec": 40.0}],
    )
    held = held_back_people(
        [
            {"stage": "qualification", "person_name": "Ramp", "entity_key": "ramp", "signal_url": "https://example.com"},
            {"stage": "qualification", "person_name": "Uber Engineering", "entity_key": "uber engineering", "signal_url": "https://x.com/UberEng/status/1"},
        ],
        [{"id": "cand_zach", "name": "Zach Bruggeman", "company": "Ramp", "entity_key": "zach bruggeman|ramp"}],
    )
    assert [item["name"] for item in held] == ["Uber Engineering"]
    report = campaign_report(conn, "oneaway")
    unknown = sum(row["spend"] or 0 for row in report["bySource"] if row["name"] == "Unknown")
    web = next(row for row in report["bySource"] if row["name"] == "Web")
    assert unknown == pytest.approx(0)
    assert web["spend"] == pytest.approx(0.18)


def test_signal_family_reuses_the_reply_reason_trigger():
    from trace_reply_reason import signal_family_for

    assert signal_family_for({"reply_reason": {"trigger_type": "behavior_trigger"}}) == "behavior_trigger"
    assert signal_family_for({"signal_family": "action_trigger"}) == "action_trigger"
    assert signal_family_for({}) == "unknown"


def test_tracked_spend_allocates_by_stage_and_prices_each_outcome(conn):
    """$10.32 research ledger: stage mix, unit costs, and unallocated discovery."""
    _hunt(conn, "h1", reviewed_n=11)
    people = [
        {
            "entity_key": f"p{i}",
            "source_channel": "web",
            "signal_family": "action",
            "kind": "ready" if i < 4 else "reject",
            "wave_id": "wave-web",
        }
        for i in range(11)
    ]
    record_review_batch(conn, "oneaway", "h1", people)
    import_research_events(
        conn,
        [
            {"stage": "discovery_web", "cost_usd": 2.52, "request_id": "web", "wave_id": "wave-web"},
            {"stage": "discovery_x", "cost_usd": 2.16, "request_id": "x", "wave_id": "wave-x"},
            {"stage": "qualification", "entity_key": "p0", "cost_usd": 2.54, "request_id": "qual"},
            {"stage": "deepening", "entity_key": "p0", "cost_usd": 2.00, "request_id": "deep"},
            {"stage": "resolution", "entity_key": "p0", "cost_usd": 1.10, "request_id": "resolve"},
        ],
        profile_id="oneaway",
        hunt_id="h1",
    )
    report = campaign_report(conn, "oneaway")
    assert report["totalUsd"] == pytest.approx(10.32)
    assert report["acquisitionComplete"] is False
    stages = {row["stage"]: row["usd"] for row in report["stages"] if row["tracked"]}
    assert stages["Discovery Web"] == pytest.approx(2.52)
    assert stages["Discovery X"] == pytest.approx(2.16)
    assert stages["Qualification"] == pytest.approx(2.54)
    assert stages["Deepening"] == pytest.approx(3.10)
    assert sum(stages.values()) == pytest.approx(report["totalUsd"])
    assert round(report["unitCosts"]["reviewed"], 2) == 0.94
    assert report["unitCosts"]["outreachReady"] == pytest.approx(2.58)
    assert report["counts"]["reviewed"] == 11
    assert report["counts"]["outreachReady"] == 4
    assert report["unitCosts"]["humanReply"] is None
    assert report["unitCosts"]["meaningfulReply"] is None
    assert report["unitCosts"]["customer"] is None
    assert report["counts"]["customers"] == 0
    for key in ("bySource", "bySignal", "byCampaign", "byHuntDetail"):
        assert sum(row["spend"] or 0 for row in report[key]) == pytest.approx(report["totalUsd"])
    legacy = next(row for row in report["bySource"] if row["name"] == "Legacy unallocated")
    assert legacy["spend"] == pytest.approx(2.16)
    assert legacy["ready"] == 0
    assert legacy["reviewed"] == 0
    assert conn.execute(
        """
        SELECT COUNT(*) AS n FROM cost_events
        WHERE stage = 'discovery_x' AND allocation_method = 'equal_split'
        """
    ).fetchone()["n"] == 0
    web = next(row for row in report["bySource"] if row["name"] == "Web")
    assert web["spend"] == pytest.approx(8.16)


def test_repeated_positive_replies_count_as_one_prospect(conn):
    now = datetime.now(timezone.utc)
    origin = (now - timedelta(days=5)).isoformat()
    record_funnel(
        conn,
        profile_id="oneaway",
        event_type="candidate_reviewed",
        candidate_id="cand_1",
        entity_key="ada",
        occurred_at=origin,
    )
    record_funnel(
        conn,
        profile_id="oneaway",
        event_type="human_reply",
        candidate_id="cand_1",
        entity_key="ada",
        occurred_at=(now - timedelta(days=4)).isoformat(),
        metadata={"reply_quality": "positive"},
        source_key="human_reply:ada:1",
    )
    record_funnel(
        conn,
        profile_id="oneaway",
        event_type="human_reply",
        candidate_id="cand_1",
        entity_key="ada",
        occurred_at=(now - timedelta(days=3)).isoformat(),
        metadata={"reply_quality": "positive"},
        source_key="human_reply:ada:2",
    )
    late = (now - timedelta(days=2)).isoformat()
    record_funnel(
        conn,
        profile_id="oneaway",
        event_type="candidate_reviewed",
        candidate_id="cand_2",
        entity_key="bea",
        occurred_at=late,
    )
    record_funnel(
        conn,
        profile_id="oneaway",
        event_type="human_reply",
        candidate_id="cand_2",
        entity_key="bea",
        occurred_at=(now + timedelta(days=40)).isoformat(),
        metadata={"reply_quality": "engaged"},
        source_key="human_reply:bea:late",
    )
    import_research_events(
        conn,
        [{"stage": "qualification", "entity_key": "ada", "cost_usd": 4.0, "request_id": "q-ada"}],
        profile_id="oneaway",
        hunt_id="h1",
    )
    everywhere = campaign_report(conn, "oneaway")
    assert everywhere["counts"]["meaningfulReplies"] == 2
    windowed = campaign_report(conn, "oneaway", window="30d")
    assert windowed["counts"]["meaningfulReplies"] == 1
    assert windowed["unitCosts"]["meaningfulReply"] == pytest.approx(4.0)
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM funnel_events WHERE event_type = 'human_reply' AND candidate_id = 'cand_1'"
    ).fetchone()["n"] == 2


def test_economics_module_has_no_product_name_branch():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "trace_economics.py")
    text = open(path, encoding="utf-8").read()
    for name in ("Akashic", "Helix", "Keycard", 'product_name =='):
        assert name not in text
