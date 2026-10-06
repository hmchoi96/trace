"""Mailbox reply checks write quality onto the person, not the mail body."""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trace_app import db, guards, service  # noqa: E402


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACE_DB_PATH", str(tmp_path / "trace.db"))
    monkeypatch.setenv("SENDER_EMAIL", "sender@example.com")
    monkeypatch.setenv("AZURE_TENANT_ID", "tenant")
    monkeypatch.setenv("AZURE_CLIENT_ID", "client")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "secret")
    db.reset_connection()
    connection = service.init(str(tmp_path / "trace.db"))
    yield connection
    db.reset_connection()


def _sent_person(conn, *, conversation_id: str = "conv-1") -> None:
    conn.execute(
        """
        INSERT INTO candidates (
            id, hunt_id, profile_id, name, company, found_on, entity_key,
            email, candidate_json, created_at
        ) VALUES (?, NULL, 'oneaway', 'Troy', 'Zip', 'Web', 'troy|zip',
                  'troy@zip.com', ?, '2026-02-01T00:00:00+00:00')
        """,
        ("cand_troy", json.dumps({"entity_key": "troy|zip"})),
    )
    conn.execute(
        """
        INSERT INTO sends (
            id, candidate_id, profile_id, method, to_email, from_email,
            subject, body, conversation_id, idempotency_key, sent_at
        ) VALUES (
            'send_troy', 'cand_troy', 'oneaway', 'trace', 'troy@zip.com',
            'sender@example.com', 'BDR role', 'Outbound note.', ?, 'key-troy',
            '2026-02-01T00:00:00+00:00'
        )
        """,
        (conversation_id,),
    )
    conn.commit()


def _reply(**extra):
    message = {
        "id": "m1",
        "id": "m1",
        "subject": "RE: BDR role",
        "from": {"emailAddress": {"address": "troy@zip.com"}},
        "receivedDateTime": "2026-02-02T00:00:00Z",
        "bodyPreview": "Sounds interesting.",
        "body": {
            "content": "Sounds interesting.\n\nOn Mon, Trace wrote:\nSecret mailbox text.",
        },
        "conversationId": "conv-1",
    }
    message.update(extra)
    return message


def test_a_human_reply_is_stored_without_the_mail_body(conn):
    _sent_person(conn)
    seen = {}

    def fetch(mailbox, since, folders):
        seen["mailbox"] = mailbox
        seen["folders"] = folders
        assert since.isoformat() < "2026-02-01"
        return [_reply()]

    result = service.sync_mailbox_replies(conn, "oneaway", fetch=fetch)
    assert seen["mailbox"] == "sender@example.com"
    assert seen["folders"] == ("inbox", "archive", "deleteditems")
    assert result["humanReplies"] == 1
    rec = json.loads(
        conn.execute("SELECT candidate_json FROM candidates WHERE id = 'cand_troy'").fetchone()[0]
    )
    assert rec["reply_quality"] == "positive"
    assert "Secret mailbox text" not in json.dumps(rec)
    assert "reply_preview" not in rec
    stored = conn.execute(
        "SELECT reply_excerpt, graph_message_id FROM mailbox_replies"
    ).fetchone()
    assert stored["graph_message_id"] == "m1"
    assert "Secret" not in stored["reply_excerpt"]
    assert "Sounds interesting" in stored["reply_excerpt"]
    rows = conn.execute(
        "SELECT metadata_json FROM funnel_events WHERE event_type = 'human_reply'"
    ).fetchall()
    assert len(rows) == 1
    assert json.loads(rows[0][0]) == {"reply_quality": "positive"}
    report = service.cost_summary(conn, "oneaway")
    assert report["counts"]["sent"] == 1
    assert report["counts"]["humanReplies"] == 1
    assert report["counts"]["meaningfulReplies"] == 1


def test_an_automated_reply_does_not_count(conn):
    _sent_person(conn)

    def fetch(mailbox, since, folders):
        return [
            _reply(
                bodyPreview="I am out of the office until Monday.",
                body={"content": "I am out of the office until Monday."},
            )
        ]

    result = service.sync_mailbox_replies(conn, "oneaway", fetch=fetch)
    assert result["humanReplies"] == 0
    rec = json.loads(
        conn.execute("SELECT candidate_json FROM candidates WHERE id = 'cand_troy'").fetchone()[0]
    )
    assert "reply_quality" not in rec


def test_checking_twice_does_not_duplicate_the_person(conn):
    _sent_person(conn)

    def fetch(mailbox, since, folders):
        return [_reply()]

    service.sync_mailbox_replies(conn, "oneaway", fetch=fetch)
    service.sync_mailbox_replies(conn, "oneaway", fetch=fetch)
    count = conn.execute(
        "SELECT COUNT(*) FROM funnel_events WHERE event_type = 'human_reply'"
    ).fetchone()[0]
    assert count == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM mailbox_replies").fetchone()["n"] == 1
    report = service.cost_summary(conn, "oneaway")
    assert report["counts"]["sent"] == 1
    assert report["counts"]["humanReplies"] == 1


def test_a_later_classification_updates_the_same_reply(conn):
    _sent_person(conn)
    calls = {"n": 0}

    def fetch(mailbox, since, folders):
        calls["n"] += 1
        if calls["n"] == 1:
            return [_reply()]
        return [
            _reply(
                bodyPreview="Not interested.",
                body={"content": "Not interested."},
            )
        ]

    service.sync_mailbox_replies(conn, "oneaway", fetch=fetch)
    service.sync_mailbox_replies(conn, "oneaway", fetch=fetch)
    rec = json.loads(
        conn.execute("SELECT candidate_json FROM candidates WHERE id = 'cand_troy'").fetchone()[0]
    )
    assert rec["reply_quality"] == "negative"
    rows = conn.execute(
        "SELECT metadata_json FROM funnel_events WHERE event_type = 'human_reply'"
    ).fetchall()
    assert len(rows) == 1
    assert json.loads(rows[0][0])["reply_quality"] == "negative"
    report = service.cost_summary(conn, "oneaway")
    assert report["counts"]["humanReplies"] == 1
    assert report["counts"]["meaningfulReplies"] == 0


def test_two_replies_in_one_thread_stay_one_person(conn):
    _sent_person(conn)
    calls = {"n": 0}

    def fetch(mailbox, since, folders):
        calls["n"] += 1
        return [
            _reply(id="m1", receivedDateTime="2026-02-02T00:00:00Z", bodyPreview="Sounds interesting.", body={"content": "Sounds interesting."}),
            _reply(
                id="m2",
                receivedDateTime="2026-02-03T00:00:00Z",
                bodyPreview="Not interested.",
                body={"content": "Not interested."},
            ),
        ]

    first = service.sync_mailbox_replies(conn, "oneaway", fetch=fetch)
    second = service.sync_mailbox_replies(conn, "oneaway", fetch=fetch)
    assert first["newlyMatched"] == 2
    assert second["newlyMatched"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM mailbox_replies").fetchone()["n"] == 2
    report = service.cost_summary(conn, "oneaway")
    rec = json.loads(
        conn.execute("SELECT candidate_json FROM candidates WHERE id = 'cand_troy'").fetchone()[0]
    )
    assert rec["reply_quality"] == "positive"
    assert report["counts"]["humanReplies"] == 1
    assert report["counts"]["meaningfulReplies"] == 1
    assert report["counts"]["sent"] == 1
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM funnel_events WHERE event_type = 'human_reply'"
    ).fetchone()["n"] == 2


def test_reply_check_requires_a_mailbox(conn, monkeypatch):
    monkeypatch.delenv("SENDER_EMAIL", raising=False)
    with pytest.raises(guards.GuardError) as caught:
        service.sync_mailbox_replies(conn, "oneaway", fetch=lambda *args: [])
    assert caught.value.code == "no_mailbox"
