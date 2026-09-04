"""HTTP contract for the Trace app, including the background job path."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fastapi_testclient = pytest.importorskip("fastapi.testclient")

from trace_app import db, service  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SENDER_EMAIL", "me@wiserbond.com")
    monkeypatch.setenv("TRACE_DB_PATH", str(tmp_path / "trace.db"))
    monkeypatch.setenv("TRACE_INLINE_WORKER", "0")
    monkeypatch.setattr(service, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", str(tmp_path / "trace.db"))
    db.reset_connection()

    from trace_app.api import app

    with fastapi_testclient.TestClient(app) as c:
        yield c
    db.reset_connection()


def test_profiles_and_templates_are_available(client):
    ids = {p["id"] for p in client.get("/api/profiles").json()}
    assert {"oneaway", "akashic"} <= ids
    templates = {t["id"]: t for t in client.get("/api/templates").json()}
    assert set(templates) == {"legacy", "strategy", "short", "plain"}
    assert templates["legacy"]["label"] == "Research-Led Discovery"
    assert templates["strategy"]["label"] == "Value-First Outreach"


def test_health_reports_the_connected_mailbox(client):
    body = client.get("/api/health").json()
    assert body["mailbox"] == "me@wiserbond.com"
    assert "hunter" in body


def test_hunt_size_outside_the_offered_list_is_rejected(client):
    res = client.post("/api/hunts", json={"profileId": "oneaway", "limit": 7})
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "bad_limit"


def test_hunt_creates_a_queued_job(client):
    res = client.post("/api/hunts", json={"profileId": "oneaway", "limit": 5})
    assert res.status_code == 200
    hunt = client.get(f"/api/hunts/{res.json()['huntId']}").json()
    assert hunt["status"] == "queued"
    assert hunt["candidates"] == []
    assert hunt["estimateSec"] > 0
    assert hunt["events"]
    job = client.get(f"/api/jobs/{res.json()['jobId']}").json()
    assert job["status"] == "queued"


def test_a_queued_hunt_can_be_cancelled_before_the_worker_starts(client):
    created = client.post("/api/hunts", json={"profileId": "oneaway", "limit": 3}).json()
    cancelled = client.post(f"/api/hunts/{created['huntId']}/cancel").json()
    assert cancelled["cancelled"] is True
    hunt = client.get(f"/api/hunts/{created['huntId']}").json()
    assert hunt["status"] == "cancelled"
    job = client.get(f"/api/jobs/{created['jobId']}").json()
    assert job["status"] == "cancelled"


def test_a_running_hunt_cannot_be_cancelled(client, monkeypatch):
    import signal_discovery

    def slow(*_a, **_k):
        return []

    monkeypatch.setattr(signal_discovery, "run_discovery", slow)
    created = client.post("/api/hunts", json={"profileId": "oneaway", "limit": 3}).json()
    conn = db.connect()
    job = service.claim_next_job(conn)
    assert job["hunt_id"] == created["huntId"]
    res = client.post(f"/api/hunts/{created['huntId']}/cancel")
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "hunt_running"
    service.run_job(conn, job)


def test_profile_hunt_history_lists_recent_runs(client):
    first = client.post("/api/hunts", json={"profileId": "oneaway", "limit": 3}).json()
    second = client.post("/api/hunts", json={"profileId": "oneaway", "limit": 5}).json()
    hunts = client.get("/api/profiles/oneaway/hunts").json()
    assert [h["id"] for h in hunts[:2]] == [second["huntId"], first["huntId"]]
    assert hunts[0]["limit"] == 5
    assert hunts[0]["status"] == "queued"


def test_worker_runs_a_hunt_and_the_people_show_up(client, monkeypatch):
    import signal_discovery

    found = {
        "record_type": "signal_candidate",
        "candidate_id": "sig_worker",
        "name": "Ken Ito",
        "title": "VP Sales",
        "company": "Northline",
        "signal_source": "x",
        "signal_text": "We are hiring our first SDR and have no playbook.",
        "human_status": "PENDING",
        "entity_key": "ken ito|northline",
    }
    monkeypatch.setattr(signal_discovery, "run_discovery", lambda *a, **k: [found])

    created = client.post("/api/hunts", json={"profileId": "oneaway", "limit": 3}).json()
    conn = db.connect()
    job = service.claim_next_job(conn)
    service.run_job(conn, job)

    hunt = client.get(f"/api/hunts/{created['huntId']}").json()
    assert hunt["status"] == "done"
    assert [c["name"] for c in hunt["candidates"]] == ["Ken Ito"]
    assert any(e["stage"] == "starting" for e in hunt["events"])
    assert hunt["progressPct"] == 100
    assert client.get(f"/api/jobs/{created['jobId']}").json()["status"] == "done"

    people = client.get("/api/profiles/oneaway/people").json()
    assert people[0]["foundOn"] == "X"
    assert people[0]["status"] == "researched"


def test_a_failing_hunt_reports_the_error_instead_of_hanging(client, monkeypatch):
    import signal_discovery

    def boom(*_a, **_k):
        raise RuntimeError("XAI_API_KEY is missing from .env")

    monkeypatch.setattr(signal_discovery, "run_discovery", boom)
    created = client.post("/api/hunts", json={"profileId": "oneaway", "limit": 3}).json()
    conn = db.connect()
    service.run_job(conn, service.claim_next_job(conn))

    hunt = client.get(f"/api/hunts/{created['huntId']}").json()
    assert hunt["status"] == "failed"
    assert "XAI_API_KEY" in hunt["error"]


def test_drafting_before_a_yes_is_refused_over_http(client):
    conn = db.connect()
    service._store_candidates(
        conn,
        None,
        "oneaway",
        [{"candidate_id": "sig_x", "name": "Dana Reed", "human_status": "PENDING"}],
    )
    res = client.post("/api/candidates/sig_x/draft", json={})
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "not_approved"


def test_notes_and_outcome_survive_a_reload(client):
    conn = db.connect()
    service._store_candidates(
        conn, None, "oneaway", [{"candidate_id": "sig_n", "name": "Dana Reed"}]
    )
    client.post("/api/candidates/sig_n/notes", json={"text": "Wait for the raise."})
    client.post("/api/candidates/sig_n/outcome", json={"outcome": "closed"})

    person = client.get("/api/candidates/sig_n").json()
    assert person["notes"][0]["text"] == "Wait for the raise."
    assert person["status"] == "closed"


def test_cost_endpoint_scales_the_estimate_with_hunt_size(client):
    small = client.get("/api/profiles/oneaway/cost?limit=3").json()
    big = client.get("/api/profiles/oneaway/cost?limit=20").json()
    assert big["nextHunt"]["low"] > small["nextHunt"]["low"]
    assert small["limits"] == [3, 5, 8, 12, 20]


def test_patch_contact_saves_manual_email(client):
    conn = db.connect()
    service._store_candidates(
        conn,
        None,
        "oneaway",
        [
            {
                "candidate_id": "sig_contact",
                "name": "Ben Cole",
                "email": "",
                "phone": "+447714635408",
                "human_status": "APPROVED",
            }
        ],
    )
    conn.execute(
        "UPDATE candidates SET phone = ?, phone_source = ?, decision = 'yes' WHERE id = ?",
        ("+447714635408", "Apollo", "sig_contact"),
    )
    conn.commit()

    res = client.patch(
        "/api/candidates/sig_contact/contact",
        json={"email": "ben.cole@fpecapital.com", "phone": "+447714635408"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["email"] == "ben.cole@fpecapital.com"
    assert body["emailSource"] == "Manual"
    assert body["draftQueued"] is True

    person = client.get("/api/candidates/sig_contact").json()
    assert person["email"] == "ben.cole@fpecapital.com"
    assert person["emailSource"] == "Manual"


def test_created_profile_is_selectable_and_isolated(client):
    created = client.post(
        "/api/profiles",
        json={
            "name": "Stockline",
            "whatItDoes": "Shows cafes what they used last week.",
            "senderName": "Sam",
            "senderCompany": "Stockline",
            "buyers": "Owner operators",
            "template": "strategy",
        },
    ).json()
    assert created["id"] == "stockline"
    assert client.get(f"/api/profiles/{created['id']}/people").json() == []
