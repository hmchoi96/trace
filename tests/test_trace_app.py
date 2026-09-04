"""Trace app layer: profile binding, human gate, draft gate, send safety."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trace_app import db, drafting, guards, profiles, service  # noqa: E402


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("SENDER_EMAIL", "me@wiserbond.com")
    monkeypatch.delenv("SENDER_FULL_NAME", raising=False)
    monkeypatch.delenv("SENDER_COMPANY", raising=False)
    monkeypatch.setenv("TRACE_DB_PATH", str(tmp_path / "trace.db"))
    monkeypatch.setattr(service, "RUNS_DIR", str(tmp_path / "runs"))
    db.reset_connection()
    c = service.init(str(tmp_path / "trace.db"))
    yield c
    db.reset_connection()


def fake_candidate(cid: str, name: str = "Dana Reed", **extra):
    rec = {
        "record_type": "signal_candidate",
        "candidate_id": cid,
        "name": name,
        "title": "Head of Sales",
        "company": "Northline",
        "signal_source": "linkedin",
        "signal_url": "https://linkedin.com/posts/dana",
        "signal_text": "We still rebuild the outbound list by hand every week.",
        "why_relevant": "Owns pipeline, no outbound engine.",
        "actor_type": "PRACTITIONER",
        "recommendation": "LIKELY_PROSPECT",
        "recommendation_reason": "Runs the workflow the product replaces.",
        "human_status": "PENDING",
        "email": "",
        "entity_key": f"{name.lower()}|northline",
    }
    rec.update(extra)
    return rec


def seed_candidate(conn, profile_id="oneaway", cid="sig_1", **extra):
    rec = fake_candidate(cid, **extra)
    service._store_candidates(conn, None, profile_id, [rec])
    return cid


def stub_draft(verdict="pass"):
    def _build(engine_profile, lead, **_kwargs):
        return {
            "subject": f"note for {lead.get('first_name')}",
            "body": f"Body for {engine_profile.get('product_name')}",
            "verdict": verdict,
            "critique": {"total": 92 if verdict == "pass" else 84},
            "error": None,
        }

    return _build


def ok_sender(calls):
    def _send(to_email, subject, body):
        calls.append((to_email, subject, body))
        return True, {"conversation_id": "c1", "internet_message_id": "m1"}

    return _send


# ── Profiles ────────────────────────────────────────────────────────────────


def test_builtin_profiles_are_seeded_with_distinct_products(conn):
    rows = {p["id"]: p for p in profiles.list_profiles(conn)}
    assert "oneaway" in rows and "akashic" in rows
    assert rows["oneaway"]["productName"] != rows["akashic"]["productName"]


def test_engine_profile_binds_template_to_email_mode(conn):
    profile = profiles.get_profile(conn, "oneaway")
    strategy = profiles.engine_profile(profile["profile"], "strategy")
    plain = profiles.engine_profile(profile["profile"], "plain")
    assert strategy["email_mode"] == "trace_strategy_email"
    assert plain["email_mode"] == "anti_ai_email"
    assert strategy["product_name"] == plain["product_name"]


def test_akashic_respects_selected_template(conn):
    profile = profiles.get_profile(conn, "akashic")
    assert profile["profile"]["profile_kind"] == "legacy"
    legacy = profiles.engine_profile(profile["profile"], "legacy")
    short = profiles.engine_profile(profile["profile"], "short")
    strategy = profiles.engine_profile(profile["profile"], "strategy")
    assert legacy["profile_kind"] == "legacy"
    assert legacy["email_mode"] == "legacy_email"
    assert short["profile_kind"] == "legacy"
    assert short["email_mode"] == "problem_validation_email"
    assert strategy["email_mode"] == "trace_strategy_email"
    assert short["product_name"] == legacy["product_name"]


def test_akashic_short_template_avoids_helix_prompt(conn):
    import main as engine

    profile = profiles.get_profile(conn, "akashic")
    short = profiles.engine_profile(profile["profile"], "short")
    assert short["product_name"] != "Helix"
    prompt = engine._build_pb_system_prompt(short)
    assert "Helix" not in prompt
    assert short["product_name"] in prompt

    plain = profiles.engine_profile(profile["profile"], "plain")
    plain_prompt = engine._build_pb_system_prompt(plain)
    assert "Helix" not in plain_prompt


def test_helix_profile_keeps_helix_prompt(conn):
    import main as engine

    profile = profiles.get_profile(conn, "problem_validation")
    prompt = engine._build_pb_system_prompt(profile["profile"])
    assert "Helix" in prompt


def test_create_profile_builds_discovery_context(conn):
    created = profiles.create_profile(
        conn,
        {
            "name": "Stockline",
            "whatItDoes": "Shows cafes what they used last week.",
            "senderName": "Sam",
            "senderCompany": "Stockline",
            "buyers": "Owner operators",
            "problems": "Rebuilds the order every Sunday",
            "goodSignals": "I still do the order myself",
            "skip": "POS vendors",
            "qualify": "Do they place the order themselves?",
            "template": "strategy",
        },
    )
    disc = created["profile"]["discovery"]
    assert disc["product_name"] == "Stockline"
    assert disc["problems_it_solves"] == ["Rebuilds the order every Sunday"]
    assert created["signOff"] == "Sam\nStockline"

    from signal_discovery import discovery_context_from_profile

    ctx = discovery_context_from_profile(created["profile"])
    assert ctx["qualification_question"] == "Do they place the order themselves?"
    assert ctx["search_channels"] == ["x", "web", "linkedin"]


# ── Human gate ──────────────────────────────────────────────────────────────


def test_no_contact_lookup_before_yes(conn):
    cid = seed_candidate(conn)
    with pytest.raises(guards.GuardError) as err:
        service.prepare_candidate(conn, cid)
    assert err.value.code == "not_approved"


def test_no_draft_before_yes(conn):
    cid = seed_candidate(conn)
    with pytest.raises(guards.GuardError) as err:
        service.create_draft(conn, cid, drafter=stub_draft())
    assert err.value.code == "not_approved"


def test_records_can_draft_pending_person_with_email(conn):
    cid = seed_candidate(conn, email="dana@fund.com")
    conn.execute(
        "UPDATE candidates SET email = ? WHERE id = ?",
        ("dana@fund.com", cid),
    )
    conn.commit()
    out = service.create_draft(conn, cid, drafter=stub_draft())
    assert out["draftId"]
    row = service.get_candidate(conn, cid)
    assert row["decision"] == "yes"


def test_apollo_runs_only_after_yes_and_never_invents_an_address(conn):
    cid = seed_candidate(conn)
    calls = []

    def matcher(details, **_kwargs):
        calls.append(details)
        return {"matches": [None]}

    service.decide(conn, cid, "yes")
    out = service.prepare_candidate(conn, cid, matcher=matcher, drafter=stub_draft())
    assert len(calls) == 1
    assert out["reason"] == "no_contact"
    row = service.get_candidate(conn, cid)
    assert row["email"] == ""
    assert row["enrich_state"] == "attempted"


def test_ensure_prepare_jobs_backfills_legacy_yes_without_prepare(conn):
    cid = seed_candidate(conn, profile_id="akashic", email="dana@fund.com")
    conn.execute(
        "UPDATE candidates SET decision = 'yes', decided_at = ? WHERE id = ?",
        (service.now_iso(), cid),
    )
    conn.commit()
    assert service.ensure_prepare_jobs(conn, "akashic") == 1
    job = conn.execute(
        "SELECT * FROM jobs WHERE candidate_id = ? AND type = 'prepare'", (cid,)
    ).fetchone()
    assert job is not None


def test_ensure_prepare_jobs_skips_when_prepare_already_ran(conn):
    cid = seed_candidate(conn, email="")
    service.decide(conn, cid, "yes")
    service.prepare_candidate(
        conn, cid, matcher=lambda *args, **kwargs: {"matches": [None]}, drafter=stub_draft()
    )
    before = conn.execute(
        "SELECT COUNT(*) AS n FROM jobs WHERE candidate_id = ? AND type = 'prepare'", (cid,)
    ).fetchone()["n"]
    assert service.ensure_prepare_jobs(conn, "oneaway") == 0
    after = conn.execute(
        "SELECT COUNT(*) AS n FROM jobs WHERE candidate_id = ? AND type = 'prepare'", (cid,)
    ).fetchone()["n"]
    assert after == before == 1


def test_people_enqueues_missing_prepare_jobs(conn):
    cid = seed_candidate(conn, profile_id="akashic", email="dana@fund.com")
    conn.execute(
        "UPDATE candidates SET decision = 'yes', decided_at = ? WHERE id = ?",
        (service.now_iso(), cid),
    )
    conn.commit()
    service.people(conn, "akashic")
    job = conn.execute(
        "SELECT * FROM jobs WHERE candidate_id = ? AND type = 'prepare'", (cid,)
    ).fetchone()
    assert job is not None


def test_sync_stored_contacts_from_signal_csv(conn, tmp_path, monkeypatch):
    import csv

    monkeypatch.setattr(service, "_repo_root", lambda: str(tmp_path))
    csv_path = tmp_path / "runs"
    csv_path.mkdir(parents=True)
    out = csv_path / "akashic_signal_people.phones.csv"
    with open(out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "First Name",
                "Last Name",
                "Title",
                "Company Name",
                "Email",
                "Person Linkedin Url",
                "Mobile Phone",
                "candidate_id",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "First Name": "Kaiti",
                "Last Name": "Delaney",
                "Title": "Principal",
                "Company Name": "Ten Eleven Ventures",
                "Email": "",
                "Person Linkedin Url": "https://www.linkedin.com/in/kaitidelaney",
                "Mobile Phone": "+17202053213",
                "candidate_id": "sig_kaiti",
            }
        )
    monkeypatch.setattr(
        service,
        "_contact_csv_paths",
        lambda profile_id: [str(out)] if profile_id == "akashic" else [],
    )
    seed_candidate(
        conn,
        profile_id="akashic",
        cid="sig_kaiti",
        name="Kaiti Delaney",
        company="Ten Eleven Ventures",
        linkedin_url="https://www.linkedin.com/in/kaitidelaney",
    )
    assert service.sync_stored_contacts(conn, "akashic") == 1
    row = service.get_candidate(conn, "sig_kaiti")
    assert row["phone"] == "+17202053213"
    assert row["phone_source"] == "Apollo"


def test_pull_contact_uses_stored_csv_before_live_apollo(conn, tmp_path, monkeypatch):
    import csv

    monkeypatch.setattr(service, "_repo_root", lambda: str(tmp_path))
    out = tmp_path / "phones.csv"
    with open(out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["First Name", "Last Name", "Company Name", "Email", "candidate_id"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "First Name": "Dana",
                "Last Name": "Reed",
                "Company Name": "Northline",
                "Email": "dana@northline.io",
                "candidate_id": "sig_pull",
            }
        )
    monkeypatch.setattr(
        service,
        "_contact_csv_paths",
        lambda profile_id: [str(out)] if profile_id == "oneaway" else [],
    )

    def boom(*args, **kwargs):
        raise AssertionError("live Apollo should not run when CSV has a match")

    seed_candidate(conn, cid="sig_pull", name="Dana Reed", company="Northline")
    result = service.pull_contact(conn, "sig_pull", matcher=boom, use_apollo=True)
    assert result["found"] is True
    assert result["source"] == "Apollo"
    assert result["email"] == "dana@northline.io"


def test_pull_contact_falls_back_to_hunter_when_apollo_misses(conn, monkeypatch):
    monkeypatch.setenv("HUNTER_API_KEY", "test-hunter")
    monkeypatch.setenv("APOLLO_API_KEY", "test-apollo")

    cid = seed_candidate(conn, name="Ben Cole", company="FPE Capital")

    def apollo_empty(*_args, **_kwargs):
        return {"matches": [None]}

    def hunter_hit(_rec, **_kwargs):
        return {"email": "ben@fpe.com", "position": "Director"}

    result = service.pull_contact(
        conn,
        cid,
        matcher=apollo_empty,
        hunter_finder=hunter_hit,
    )
    assert result["found"] is True
    assert result["email"] == "ben@fpe.com"
    assert result["source"] == "Hunter.io"
    row = service.get_candidate(conn, cid)
    assert row["email_source"] == "Hunter.io"


def test_pull_contact_can_fill_email_when_phone_already_exists(conn, monkeypatch):
    monkeypatch.setenv("HUNTER_API_KEY", "test-hunter")
    monkeypatch.setenv("APOLLO_API_KEY", "test-apollo")
    cid = seed_candidate(conn, phone="+447714635408", email="")
    conn.execute(
        "UPDATE candidates SET phone = ?, phone_source = ? WHERE id = ?",
        ("+447714635408", "Apollo", cid),
    )
    conn.commit()

    def apollo_empty(*_args, **_kwargs):
        return {"matches": [None]}

    def hunter_hit(_rec, **_kwargs):
        return {"email": "ben@fpe.com"}

    result = service.pull_contact(
        conn,
        cid,
        matcher=apollo_empty,
        hunter_finder=hunter_hit,
    )
    assert result["email"] == "ben@fpe.com"
    assert result["phone"] == "+447714635408"


# ── Draft ───────────────────────────────────────────────────────────────────


def test_draft_uses_the_profile_the_person_was_found_for(conn):
    cid = seed_candidate(conn, profile_id="akashic", email="dana@fund.com")
    conn.execute("UPDATE candidates SET email = ? WHERE id = ?", ("dana@fund.com", cid))
    service.decide(conn, cid, "yes")
    seen = {}

    def spy(engine_profile, lead, **_kwargs):
        seen["product"] = engine_profile.get("product_name")
        return stub_draft()(engine_profile, lead)

    service.create_draft(conn, cid, drafter=spy)
    akashic = profiles.get_profile(conn, "akashic")
    assert seen["product"] == akashic["profile"]["product_name"]


def test_regenerating_a_draft_supersedes_the_old_one(conn):
    cid = seed_candidate(conn, email="dana@northline.io")
    service.decide(conn, cid, "yes")
    first = service.create_draft(conn, cid, drafter=stub_draft())
    second = service.create_draft(conn, cid, template_id="plain", drafter=stub_draft())
    old = service.get_draft(conn, first["draftId"])
    assert old["superseded"] == 1
    assert service.latest_draft(conn, cid)["id"] == second["draftId"]


# ── Send ────────────────────────────────────────────────────────────────────


def approved_with_draft(conn, verdict="pass", profile_id="oneaway", cid="sig_1"):
    seed_candidate(conn, profile_id=profile_id, cid=cid)
    conn.execute(
        "UPDATE candidates SET email = ?, email_source = 'Apollo' WHERE id = ?",
        ("dana@northline.io", cid),
    )
    conn.commit()
    service.decide(conn, cid, "yes")
    out = service.create_draft(conn, cid, drafter=stub_draft(verdict))
    return cid, out["draftId"]


def test_send_writes_a_record_and_marks_the_person_sent(conn):
    _cid, draft_id = approved_with_draft(conn)
    calls = []
    service.send_draft(conn, draft_id, sender=ok_sender(calls))
    assert len(calls) == 1
    row = conn.execute("SELECT * FROM sends WHERE draft_id = ?", (draft_id,)).fetchone()
    assert row["method"] == "trace"
    assert row["from_email"] == "me@wiserbond.com"


def test_review_band_draft_cannot_be_sent(conn):
    _cid, draft_id = approved_with_draft(conn, verdict="review")
    calls = []
    with pytest.raises(guards.GuardError) as err:
        service.send_draft(conn, draft_id, sender=ok_sender(calls))
    assert err.value.code == "draft_not_ready"
    assert calls == []


def test_double_click_sends_once(conn):
    _cid, draft_id = approved_with_draft(conn)
    calls = []
    first = service.send_draft(conn, draft_id, sender=ok_sender(calls))
    second = service.send_draft(conn, draft_id, sender=ok_sender(calls))
    assert first["alreadySent"] is False
    assert second["alreadySent"] is True
    assert len(calls) == 1


def test_send_is_blocked_when_the_mailbox_is_not_the_profile_sender(conn, monkeypatch):
    _cid, draft_id = approved_with_draft(conn)
    monkeypatch.setenv("SENDER_EMAIL", "someone.else@other.com")
    calls = []
    with pytest.raises(guards.GuardError) as err:
        service.send_draft(conn, draft_id, sender=ok_sender(calls))
    assert err.value.code == "sender_mismatch"
    assert calls == []


def test_send_is_blocked_when_the_draft_belongs_to_another_profile(conn):
    _cid, draft_id = approved_with_draft(conn)
    conn.execute("UPDATE drafts SET profile_id = 'akashic' WHERE id = ?", (draft_id,))
    conn.commit()
    calls = []
    with pytest.raises(guards.GuardError) as err:
        service.send_draft(conn, draft_id, sender=ok_sender(calls))
    assert err.value.code == "profile_mismatch"
    assert calls == []


def test_send_is_blocked_when_the_profile_changed_product_after_the_hunt(conn):
    cid = "sig_snap"
    hunt = service.create_hunt(conn, "oneaway", 5)
    seed_candidate(conn, cid=cid)
    conn.execute(
        "UPDATE candidates SET hunt_id = ?, email = ? WHERE id = ?",
        (hunt["huntId"], "dana@northline.io", cid),
    )
    conn.commit()
    service.decide(conn, cid, "yes")
    draft_id = service.create_draft(conn, cid, drafter=stub_draft())["draftId"]

    row = conn.execute("SELECT profile_json FROM profiles WHERE id = 'oneaway'").fetchone()
    changed = db.loads(row["profile_json"], {})
    changed["product_name"] = "Akashic"
    conn.execute(
        "UPDATE profiles SET profile_json = ? WHERE id = 'oneaway'", (db.dumps(changed),)
    )
    conn.commit()

    calls = []
    with pytest.raises(guards.GuardError) as err:
        service.send_draft(conn, draft_id, sender=ok_sender(calls))
    assert err.value.code == "snapshot_mismatch"
    assert calls == []


def test_i_sent_it_myself_records_without_touching_the_mailbox(conn):
    cid, _draft_id = approved_with_draft(conn)
    out = service.mark_sent_myself(conn, cid)
    assert out["alreadySent"] is False
    row = conn.execute("SELECT * FROM sends WHERE candidate_id = ?", (cid,)).fetchone()
    assert row["method"] == "self"
    assert row["from_email"] == ""
    assert service.mark_sent_myself(conn, cid)["alreadySent"] is True


# ── Hunt ────────────────────────────────────────────────────────────────────


def test_hunt_freezes_the_profile_snapshot(conn):
    out = service.create_hunt(conn, "oneaway", 5)
    row = conn.execute("SELECT * FROM hunts WHERE id = ?", (out["huntId"],)).fetchone()
    snap = db.loads(row["snapshot_json"], {})
    oneaway = profiles.get_profile(conn, "oneaway")
    assert snap["product_name"] == oneaway["profile"]["product_name"]
    assert row["status"] == "queued"


def test_hunt_rejects_a_size_that_is_not_offered(conn):
    with pytest.raises(guards.GuardError) as err:
        service.create_hunt(conn, "oneaway", 7)
    assert err.value.code == "bad_limit"


def test_hunt_skips_people_already_in_the_file(conn, monkeypatch):
    seed_candidate(conn, cid="sig_known", name="Dana Reed", company="Northline")
    conn.execute(
        "UPDATE candidates SET entity_key = 'dana reed|northline' WHERE id = 'sig_known'"
    )
    conn.commit()
    hunt = service.create_hunt(conn, "oneaway", 3)

    fresh = fake_candidate("sig_new", name="Ken Ito")
    fresh["entity_key"] = "ken ito|northline"
    repeat = fake_candidate("sig_dup", name="Dana Reed", company="Northline")

    import signal_discovery

    monkeypatch.setattr(
        signal_discovery, "run_discovery", lambda *a, **k: [fresh, repeat]
    )
    added = service.run_hunt(conn, hunt["huntId"])
    assert added == 1
    names = {p["name"] for p in service.people(conn, "oneaway")}
    assert names == {"Dana Reed", "Ken Ito"}


def test_hunt_skips_same_person_by_name_even_without_entity_key(conn, monkeypatch):
    seed_candidate(conn, cid="sig_known", name="Larry Cheng", company="Volition Capital")
    hunt = service.create_hunt(conn, "oneaway", 3)
    repeat = fake_candidate("sig_other", name="Larry Cheng", company="Volition Capital")
    import signal_discovery

    monkeypatch.setattr(signal_discovery, "run_discovery", lambda *a, **k: [repeat])
    added = service.run_hunt(conn, hunt["huntId"])
    assert added == 0
    assert len(service.people(conn, "oneaway")) == 1


def test_dedupe_candidates_keeps_richest_row(conn):
    rec = fake_candidate("sig_a", name="Larry Cheng", company="Volition Capital")
    service._store_candidates(conn, None, "oneaway", [rec])
    conn.execute(
        """
        INSERT INTO candidates (id, hunt_id, profile_id, name, title, company,
                                found_on, entity_key, candidate_json, created_at)
        VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "sig_b",
            "oneaway",
            "Larry Cheng",
            "",
            "Volition Capital",
            "Web",
            "",
            db.dumps(fake_candidate("sig_b", name="Larry Cheng", company="Volition Capital")),
            service.now_iso(),
        ),
    )
    conn.execute("UPDATE candidates SET email = 'larry@volitioncapital.com' WHERE id = 'sig_a'")
    conn.commit()
    removed = service.dedupe_candidates(conn, "oneaway")
    assert removed == 1
    rows = conn.execute(
        "SELECT id, email FROM candidates WHERE lower(name) = 'larry cheng'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["email"] == "larry@volitioncapital.com"


def test_hunt_stage_updates_work_from_thread_pool_workers(conn, monkeypatch):
    import signal_discovery
    from concurrent.futures import ThreadPoolExecutor

    hunt = service.create_hunt(conn, "oneaway", 3)

    def discovery_with_threads(*_a, on_stage=None, **_k):
        if on_stage:
            with ThreadPoolExecutor(max_workers=2) as pool:
                pool.submit(on_stage, "discovery_web", "").result()
                pool.submit(on_stage, "qualification", "Ada Lovelace").result()
        return []

    monkeypatch.setattr(signal_discovery, "run_discovery", discovery_with_threads)
    service.run_hunt(conn, hunt["huntId"])

    dto = service.get_hunt(conn, hunt["huntId"])
    assert dto["status"] == "done"
    stages = {event["stage"] for event in dto["events"]}
    assert "web" in stages
    assert "qualify" in stages


def test_hunt_records_cost_and_estimates_the_next_one(conn):
    hunt = service.create_hunt(conn, "oneaway", 5)
    conn.execute(
        "INSERT INTO cost_events (id, profile_id, hunt_id, stage, cost_usd, elapsed_sec, created_at)"
        " VALUES ('c1', 'oneaway', ?, 'search', 1.5, 12, ?)",
        (hunt["huntId"], service.now_iso()),
    )
    seed_candidate(conn, cid="sig_a")
    seed_candidate(conn, cid="sig_b", name="Ken Ito")
    conn.commit()
    summary = service.cost_summary(conn, "oneaway")
    assert summary["totalUsd"] == 1.5
    assert summary["hunts"] == 1
    small = service.estimate_hunt_usd(conn, "oneaway", 3)
    big = service.estimate_hunt_usd(conn, "oneaway", 20)
    assert big["low"] > small["low"]


# ── People view ─────────────────────────────────────────────────────────────


def test_people_are_separated_by_profile(conn):
    seed_candidate(conn, profile_id="oneaway", cid="sig_o")
    seed_candidate(conn, profile_id="akashic", cid="sig_a", name="Priya Shah")
    assert [p["name"] for p in service.people(conn, "oneaway")] == ["Dana Reed"]
    assert [p["name"] for p in service.people(conn, "akashic")] == ["Priya Shah"]


def test_status_moves_from_researched_to_sent(conn):
    cid = seed_candidate(conn)
    assert service.people(conn, "oneaway")[0]["status"] == "researched"
    conn.execute("UPDATE candidates SET email = ? WHERE id = ?", ("d@n.io", cid))
    service.decide(conn, cid, "yes")
    assert service.people(conn, "oneaway")[0]["status"] == "approved"
    service.create_draft(conn, cid, drafter=stub_draft())
    assert service.people(conn, "oneaway")[0]["status"] == "draft"
    service.mark_sent_myself(conn, cid)
    person = service.people(conn, "oneaway")[0]
    assert person["status"] == "sent"
    assert person["sendMethod"] == "self"


def test_disqualified_person_keeps_that_status(conn):
    cid = seed_candidate(conn)
    service.set_outcome(conn, cid, "disqualified")
    assert service.people(conn, "oneaway")[0]["status"] == "disqualified"


def test_notes_are_kept_with_the_person(conn):
    cid = seed_candidate(conn)
    service.add_note(conn, cid, "Hold until after the raise.")
    assert service.people(conn, "oneaway")[0]["notes"][0]["text"] == "Hold until after the raise."


def test_axes_are_returned_as_readable_rows(conn):
    seed_candidate(
        conn,
        pain_evidence="VERY_HIGH",
        economic_buyer_likelihood="LOW",
        supporting_evidence=["Ran the same list rebuild last quarter."],
    )
    person = service.people(conn, "oneaway")[0]
    axes = {a["label"]: a["value"] for a in person["axes"]}
    assert axes["Pain evidence"] == "Very high"
    assert axes["Economic buyer likelihood"] == "Low"
    assert person["deepened"] == ["Ran the same list rebuild last quarter."]


def test_update_contact_saves_manual_email(conn):
    cid = seed_candidate(
        conn,
        phone="+447714635408",
        email="",
    )
    conn.execute(
        "UPDATE candidates SET phone = ?, phone_source = ?, enrich_state = ?, decision = 'yes', decided_at = ? WHERE id = ?",
        ("+447714635408", "Apollo", "found", service.now_iso(), cid),
    )
    conn.commit()
    service.prepare_candidate(
        conn, cid, matcher=lambda *args, **kwargs: {"matches": [None]}, drafter=stub_draft()
    )

    out = service.update_contact(conn, cid, email="ben@volition.com", phone="+447714635408")
    assert out["email"] == "ben@volition.com"
    assert out["emailSource"] == "Manual"
    assert out["phone"] == "+447714635408"
    assert out["phoneSource"] == "Apollo"
    assert out["draftQueued"] is True

    row = conn.execute("SELECT email, email_source, phone FROM candidates WHERE id = ?", (cid,)).fetchone()
    assert row["email"] == "ben@volition.com"
    assert row["email_source"] == "Manual"

    job = conn.execute(
        "SELECT status FROM jobs WHERE candidate_id = ? AND type = 'prepare' ORDER BY created_at DESC LIMIT 1",
        (cid,),
    ).fetchone()
    assert job["status"] == "queued"


def test_failed_draft_marks_person_as_draft_failed(conn):
    cid = seed_candidate(conn, decision="yes")
    conn.execute(
        """
        INSERT INTO drafts (id, candidate_id, profile_id, template_id, subject, body,
                            verdict, sendable, critique_json, error, superseded, created_at)
        VALUES (?, ?, 'oneaway', 'legacy', '', '', 'failed', 0, NULL, ?, 0, ?)
        """,
        ("draft_testfail", cid, "draft: invalid json", service.now_iso()),
    )
    conn.commit()
    person = service.people(conn, "oneaway")[0]
    assert person["status"] == "draft_failed"
    assert person["draft"]["status"] == "failed"
    assert person["draft"]["error"]


def test_outreach_role_is_exposed_on_person(conn):
    seed_candidate(
        conn,
        recommendation_reason="commentator, not an economic buyer; not ic workflow owner",
        actor_type="OTHER",
        economic_buyer_likelihood="LOW",
        end_user_likelihood="LOW",
    )
    person = service.people(conn, "oneaway")[0]
    assert person["outreachRole"] == "Expert / Researcher"
    assert person["recommendedAsk"] == "validate_problem_interpretation"


# ── Drafting loop ───────────────────────────────────────────────────────────


def test_build_draft_revises_once_then_blocks(monkeypatch):
    import main as engine

    monkeypatch.setattr(
        engine, "claude_draft_email",
        lambda *a, **k: {"subject": "s", "body": "b"},
    )
    monkeypatch.setattr(
        engine, "claude_critique_email",
        lambda *a, **k: {"total": 70, "hard_fails": ["evidence"]},
    )
    out = drafting.build_draft({"profile_kind": "legacy", "product_name": "X"}, {"first_name": "A"})
    assert out["verdict"] == "block"
    assert out["error"] is None


def test_build_draft_reports_a_failure_instead_of_raising(monkeypatch):
    import main as engine

    def boom(*_a, **_k):
        raise RuntimeError("no key")

    monkeypatch.setattr(engine, "claude_draft_email", boom)
    out = drafting.build_draft({"profile_kind": "legacy"}, {"first_name": "A"})
    assert out["verdict"] == "failed"
    assert "no key" in out["error"]


def test_build_draft_retries_json_parse_once(monkeypatch):
    import json
    import main as engine

    calls = {"n": 0}

    def flaky(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise json.JSONDecodeError("bad", "", 0)
        return {"subject": "hi", "body": "Hello Pat,\n\nQuestion?\n\n— X"}

    monkeypatch.setattr(engine, "claude_draft_email", flaky)
    monkeypatch.setattr(
        engine, "claude_critique_email",
        lambda *a, **k: {"total": 95, "hard_fails": []},
    )
    out = drafting.build_draft(
        {"profile_kind": "legacy", "product_name": "X", "sign_off": "— X"},
        {"first_name": "Pat"},
    )
    assert calls["n"] == 2
    assert out["error"] is None
    assert out["subject"] == "hi"


def test_ensure_draft_sign_off_appends_missing_signature():
    import main as engine

    profile = {
        "email_mode": "trace_strategy_email",
        "sign_off": "Jamie Choi\nWiserbond Technologies Inc.",
    }
    email = {
        "subject": "prior deal context",
        "body": "Hi Russell,\n\nWould a brief call make sense?",
    }
    out = engine.ensure_draft_sign_off(email, profile)
    assert out["body"].endswith("Jamie Choi\nWiserbond Technologies Inc.")
    assert engine._body_has_sign_off(out["body"], profile["sign_off"])


def test_ensure_draft_sign_off_keeps_existing_signature():
    import main as engine

    profile = {"email_mode": "legacy_email", "sign_off": "Jamie Choi\nWiserbond Technologies Inc."}
    body = "Hi Pat,\n\nQuestion?\n\nJamie Choi\nWiserbond Technologies Inc."
    email = {"subject": "x", "body": body}
    out = engine.ensure_draft_sign_off(email, profile)
    assert out["body"] == body


def test_dedupe_merges_company_variants_and_email(conn):
    hunt_id = profiles.new_id("hunt")
    conn.execute(
        """
        INSERT INTO hunts (id, profile_id, snapshot_json, limit_n, status, created_at)
        VALUES (?, 'akashic', '{}', 5, 'done', '2026-01-01T00:00:00Z')
        """,
        (hunt_id,),
    )
    base = {
        "record_type": "signal_candidate",
        "signal_source": "x",
        "signal_url": "https://x.com/moseskagan/status/1",
        "author_handle": "moseskagan",
    }
    for cid, company in (
        ("sig_a", "Adaptive Realty (also Co-founder/GP, ReSeed Partners)"),
        ("sig_b", "Adaptive Realty / ReSeed Partners"),
    ):
        rec = {**base, "candidate_id": cid, "name": "Moses Kagan", "company": company}
        conn.execute(
            """
            INSERT INTO candidates (id, hunt_id, profile_id, name, title, company,
                                    found_on, entity_key, candidate_json, created_at)
            VALUES (?, ?, 'akashic', ?, '', ?, 'X', '', ?, '2026-01-01T00:00:00Z')
            """,
            (cid, hunt_id, rec["name"], company, db.dumps(rec)),
        )
    conn.commit()

    removed = service.dedupe_candidates(conn, "akashic")
    assert removed == 1
    rows = conn.execute(
        "SELECT id, company, entity_key FROM candidates WHERE profile_id = 'akashic'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["entity_key"] == "moses kagan|adaptive realty"

    hunt2 = profiles.new_id("hunt")
    conn.execute(
        """
        INSERT INTO hunts (id, profile_id, snapshot_json, limit_n, status, created_at)
        VALUES (?, 'akashic', '{}', 5, 'done', '2026-01-01T00:00:00Z')
        """,
        (hunt2,),
    )
    for cid, name in (
        ("sig_c", "Kaiti Delaney (Kaiti Delaney Krier)"),
        ("sig_d", "Kaiti Delaney"),
    ):
        rec = {
            "candidate_id": cid,
            "name": name,
            "company": "Ten Eleven Ventures",
            "email": "kkrier@1011vc.com",
            "record_type": "signal_candidate",
        }
        conn.execute(
            """
            INSERT INTO candidates (id, hunt_id, profile_id, name, title, company,
                                    found_on, entity_key, email, candidate_json, created_at)
            VALUES (?, ?, 'akashic', ?, '', 'Ten Eleven Ventures', 'Web', '', ?, ?, '2026-01-02T00:00:00Z')
            """,
            (cid, hunt2, name, "kkrier@1011vc.com", db.dumps(rec)),
        )
    conn.commit()
    removed2 = service.dedupe_candidates(conn, "akashic")
    assert removed2 == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM candidates WHERE profile_id='akashic'").fetchone()["n"] == 2
