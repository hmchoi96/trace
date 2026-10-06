"""Sanitized reply matching. No mailbox bodies."""

from reply_tracker import (
    apply_reply_matches,
    classify_reply_quality,
    match_reply_to_outreach,
    normalize_subject,
    reply_folders,
)


def _sent(**extra):
    row = {
        "sent": True,
        "to_email": "troy@zip.com",
        "subject": "BDR role",
        "body": (
            "I saw the open BDR role and built an outbound system that booked "
            "meetings from that same motion."
        ),
        "sent_at": "2026-02-01T00:00:00Z",
        "conversation_id": "",
    }
    row.update(extra)
    return row


def _msg(**extra):
    row = {
        "id": "m1",
        "subject": "RE: BDR role",
        "from": {"emailAddress": {"address": "troy@zip.com"}},
        "receivedDateTime": "2026-02-02T00:00:00Z",
        "bodyPreview": "Sounds interesting.",
        "body": {"content": "Sounds interesting."},
        "conversationId": "",
    }
    row.update(extra)
    return row


def test_repeated_prefixes_normalize():
    assert normalize_subject("Re: Re: Fw: FWD: BDR role") == "bdr role"
    assert normalize_subject("RE:RE:Hello") == "hello"
    assert normalize_subject("Fw: Fw: Re: Hello") == "hello"


def test_same_domain_alias_matches():
    how = match_reply_to_outreach(
        _msg(
            **{
                "from": {"emailAddress": {"address": "t.christodoulou@zip.com"}},
                "bodyPreview": "Not interested.",
                "body": {"content": "Not interested."},
            }
        ),
        _sent(),
    )
    assert how == "domain_subject"


def test_unrelated_same_subject_does_not_match():
    how = match_reply_to_outreach(
        _msg(
            **{
                "from": {"emailAddress": {"address": "other@gmail.com"}},
                "bodyPreview": "Lunch tomorrow?",
                "body": {"content": "Lunch tomorrow?"},
            }
        ),
        _sent(),
    )
    assert how is None


def test_quoted_original_matches_across_domain():
    sent = _sent()
    how = match_reply_to_outreach(
        _msg(
            **{
                "from": {"emailAddress": {"address": "other@gmail.com"}},
                "body": {"content": "forwarding this: " + sent["body"]},
                "bodyPreview": sent["body"],
            }
        ),
        sent,
    )
    assert how == "quoted_original"


def test_automated_confirmation_and_ooo_are_excluded():
    sent = _sent(conversation_id="c1")
    ooo = _msg(
        conversationId="c1",
        bodyPreview="I am out of the office until Monday.",
        body={"content": "I am out of the office until Monday."},
    )
    confirm = _msg(
        id="m2",
        bodyPreview="Thank you for applying. Your application has been received.",
        body={"content": "Thank you for applying. Your application has been received."},
    )
    assert match_reply_to_outreach(ooo, sent) is None
    assert match_reply_to_outreach(confirm, sent) is None
    assert classify_reply_quality(ooo) == "automated"
    rows = apply_reply_matches([sent], [ooo, confirm])
    assert rows[0]["reply_status"] == "none"


def test_negative_human_reply_is_negative():
    msg = _msg(bodyPreview="Not interested.", body={"content": "Not interested."})
    rows = apply_reply_matches([_sent()], [msg])
    assert rows[0]["reply_status"] == "replied"
    assert rows[0]["reply_quality"] == "negative"


def test_human_replies_stay_in_order():
    first = _msg(
        id="a",
        receivedDateTime="2026-02-02T00:00:00Z",
        bodyPreview="Sounds interesting.",
        body={"content": "Sounds interesting."},
    )
    second = _msg(
        id="b",
        receivedDateTime="2026-02-03T00:00:00Z",
        bodyPreview="What time works?",
        body={"content": "What time works?"},
    )
    rows = apply_reply_matches([_sent()], [second, first])
    assert rows[0]["reply_count"] == 2
    assert rows[0]["first_reply_at"] < rows[0]["last_reply_at"]
    assert rows[0]["reply_quality"] == "positive"


def test_folder_default_stays_inbox(monkeypatch):
    monkeypatch.delenv("REPLY_TRACK_FOLDERS", raising=False)
    assert reply_folders() == ("inbox",)
    monkeypatch.setenv("REPLY_TRACK_FOLDERS", "inbox,archive,deleteditems")
    assert reply_folders() == ("inbox", "archive", "deleteditems")


def test_graph_reads_a_mailbox_user_not_me():
    from reply_tracker import graph_messages_url

    url = graph_messages_url("sender@example.com", "inbox")
    assert url.endswith("/users/sender@example.com/mailFolders/inbox/messages")
    assert "/me/" not in url


def test_quoted_original_does_not_change_the_new_reply_quality():
    from reply_tracker import classify_reply_quality

    thanks = _msg(
        bodyPreview="Thanks, Brad",
        body={"content": "Thanks, Brad\n\nOn Mon, Ada wrote:\nWhat time works?"},
    )
    refusal = _msg(
        bodyPreview="Not interested",
        body={"content": "Not interested\n\n-----Original Message-----\nSounds interesting. Are you free?"},
    )
    question = _msg(
        bodyPreview="Can you send the details?",
        body={"content": "Can you send the details?\n\nFrom: Trace\nSent: Monday\nThe old note."},
    )
    ooo = _msg(
        bodyPreview="I am out of the office until Monday.",
        body={"content": "I am out of the office until Monday.\n\nOn Mon, Ada wrote:\nNot interested?"},
    )
    assert classify_reply_quality(thanks) == "neutral"
    assert classify_reply_quality(refusal) == "negative"
    assert classify_reply_quality(question) == "engaged"
    assert classify_reply_quality(ooo) == "automated"
