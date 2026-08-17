"""
Match Inbox replies to outbound JSONL records via Microsoft Graph (read-only on Inbox).

Does not send mail. Requires same Azure app credentials as main.py plus Mail.Read.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID")
AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID")
AZURE_CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET")
SENDER_EMAIL = os.getenv("SENDER_EMAIL")


def _get_graph_token() -> str:
    url = f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/oauth2/v2.0/token"
    response = requests.post(
        url,
        data={
            "client_id": AZURE_CLIENT_ID,
            "client_secret": AZURE_CLIENT_SECRET,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def normalize_subject(subject: str) -> str:
    s = (subject or "").strip()
    while True:
        low = s.lower()
        m = re.match(
            r"^(re|fwd|fw|aw|안내|回复)\s*:\s*",
            low,
            flags=re.I,
        )
        if m:
            s = s[m.end() :].strip()
            continue
        break
    return " ".join(s.split()).strip().lower()


def _sender_addr(message: dict[str, Any]) -> str:
    fr = message.get("from") or {}
    em = (fr.get("emailAddress") or {}).get("address") or ""
    return (em or "").strip().lower()


def _body_text(message: dict[str, Any]) -> str:
    b = message.get("body") or {}
    return (b.get("content") or message.get("bodyPreview") or "") or ""


def classify_reply_type(message: dict[str, Any]) -> str:
    subj = (message.get("subject") or "").lower()
    text = (
        f"{message.get('bodyPreview', '')} {_body_text(message)} {subj}"
    ).lower()

    ooo = (
        "automatic reply",
        "out of office",
        "away from the office",
        "autoresponder",
        "auto-reply",
        "自動返信",
    )
    if any(x in text for x in ooo):
        return "out_of_office"

    neg = (
        "unsubscribe",
        "remove me",
        "not interested",
        "stop emailing",
        "do not contact",
    )
    if any(x in text for x in neg):
        return "negative"

    pos = (
        "interested",
        "happy to chat",
        "tell me more",
        "worth a look",
        "send it over",
        "let's talk",
        "lets talk",
        "book a",
        " schedule a",
        " demo",
    )
    if any(x in text for x in pos):
        return "positive"

    return "neutral"


def _parse_iso(dt: str | None) -> datetime | None:
    if not dt:
        return None
    s = dt.replace("Z", "+00:00")
    try:
        x = datetime.fromisoformat(s)
        if x.tzinfo is None:
            x = x.replace(tzinfo=timezone.utc)
        return x
    except ValueError:
        return None


def _sent_time(record: dict[str, Any]) -> datetime | None:
    t = record.get("sent_at") or record.get("ts")
    return _parse_iso(t if isinstance(t, str) else None)


def _ensure_outreach_id(rec: dict[str, Any], line_idx: int) -> str:
    if rec.get("outreach_id"):
        return str(rec["outreach_id"])
    lst = str(rec.get("list") or "run")
    batch = str(rec.get("test_batch") or "default").replace("/", "-")[:48]
    idx = rec.get("lead_index", line_idx)
    return f"{lst}_{batch}_{int(idx):04d}"


def load_sent_outreach(jsonl_path: str) -> list[dict[str, Any]]:
    """Records with sent true and a prospect email (optionally enrich outreach_id)."""
    out: list[dict[str, Any]] = []
    with open(jsonl_path, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("error") and not rec.get("sent"):
                continue
            if rec.get("sent") is not True:
                continue
            if not (rec.get("to_email") or "").strip():
                continue
            rec = dict(rec)
            rec["_line"] = i
            rec.setdefault("outreach_id", _ensure_outreach_id(rec, i))
            out.append(rec)
    return out


def fetch_recent_inbox_messages(
    since: datetime,
    *,
    token: str,
    mailbox: str,
    top: int = 200,
) -> list[dict[str, Any]]:
    """Messages in Inbox received on or after `since` (UTC)."""
    since_s = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.0000000Z")
    base = f"https://graph.microsoft.com/v1.0/users/{mailbox}/mailFolders/inbox/messages"
    params = {
        "$top": str(top),
        "$orderby": "receivedDateTime desc",
        "$select": (
            "id,subject,bodyPreview,body,from,receivedDateTime,"
            "conversationId,toRecipients,replyTo"
        ),
        "$filter": f"receivedDateTime ge {since_s}",
    }
    messages: list[dict[str, Any]] = []
    url = base
    first = True
    while url:
        r = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params=params if first else None,
            timeout=60,
        )
        first = False
        r.raise_for_status()
        data = r.json()
        messages.extend(data.get("value") or [])
        url = data.get("@odata.nextLink")
    return messages


def _domain(addr: str) -> str:
    addr = addr.strip().lower()
    if "@" in addr:
        return addr.split("@", 1)[-1]
    return ""


def _subject_tokens_match(a: str, b: str) -> bool:
    na, nb = normalize_subject(a), normalize_subject(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    return na in nb or nb in na


def match_reply_to_outreach(
    message: dict[str, Any],
    outreach: dict[str, Any],
) -> str | None:
    """
    Return matched_by label if this Inbox message is a reply to the outreach; else None.
    Order: conversation_id, sender_subject, domain_subject.
    """
    sender = _sender_addr(message)
    mailbox = (SENDER_EMAIL or "").strip().lower()
    if sender and mailbox and sender == mailbox:
        return None

    if "mailer-daemon" in sender or "postmaster" in sender:
        return None
    subj_m = message.get("subject") or ""
    low = subj_m.lower()
    if any(
        x in low
        for x in (
            "undeliverable",
            "delivery status",
            "failure notice",
            "returned mail",
        )
    ):
        return None

    rec_time = _parse_iso(message.get("receivedDateTime"))
    sent_time = _sent_time(outreach)
    if rec_time and sent_time and rec_time < sent_time:
        return None

    ours_sub = outreach.get("subject") or ""
    conv_o = outreach.get("conversation_id")
    conv_m = message.get("conversationId")
    if conv_o and conv_m and conv_o == conv_m:
        return "conversation_id"

    to_email = (outreach.get("to_email") or "").strip().lower()
    if sender and to_email and sender == to_email:
        if _subject_tokens_match(ours_sub, subj_m):
            return "sender_subject"

    if to_email and sender:
        if _domain(sender) and _domain(sender) == _domain(to_email):
            if _subject_tokens_match(ours_sub, subj_m):
                return "domain_subject"

    return None


def update_outreach_records_with_replies(
    input_jsonl: str,
    output_jsonl: str,
    *,
    since_days: int = 14,
) -> dict[str, Any]:
    """
    Read full JSONL, attach reply fields to sent rows, write merged JSONL.
    """
    for var, label in (
        (AZURE_TENANT_ID, "AZURE_TENANT_ID"),
        (AZURE_CLIENT_ID, "AZURE_CLIENT_ID"),
        (AZURE_CLIENT_SECRET, "AZURE_CLIENT_SECRET"),
        (SENDER_EMAIL, "SENDER_EMAIL"),
    ):
        if not var:
            raise EnvironmentError(f"Missing env var: {label}")

    token = _get_graph_token()
    all_rows: list[dict[str, Any]] = []
    with open(input_jsonl, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            all_rows.append(json.loads(line))

    sent_rows = sum(
        1
        for r in all_rows
        if r.get("sent") is True and (r.get("to_email") or "").strip()
    )

    since = datetime.now(timezone.utc) - timedelta(days=max(1, since_days))
    inbox = fetch_recent_inbox_messages(since, token=token, mailbox=SENDER_EMAIL or "")

    # Collect matches: list of (row_index, message, matched_by)
    attached: dict[int, list[tuple[dict[str, Any], str]]] = {}

    rank_map = {"conversation_id": 0, "sender_subject": 1, "domain_subject": 2}

    for msg in inbox:
        best: tuple[int, int, str] | None = None  # rank, row_i, how
        for i, rec in enumerate(all_rows):
            if rec.get("sent") is not True:
                continue
            how = match_reply_to_outreach(msg, rec)
            if not how:
                continue
            rank = rank_map[how]
            if best is None:
                best = (rank, i, how)
            elif rank < best[0]:
                best = (rank, i, how)
            elif rank == best[0] and i < best[1]:
                best = (rank, i, how)
        if best:
            row_i = best[1]
            how = best[2]
            attached.setdefault(row_i, []).append((msg, how))

    # Aggregate per row
    for row_i, pairs in attached.items():
        pairs.sort(
            key=lambda p: _parse_iso(p[0].get("receivedDateTime"))
            or datetime.min.replace(tzinfo=timezone.utc),
        )
        times = [
            _parse_iso(p[0].get("receivedDateTime"))
            for p in pairs
            if _parse_iso(p[0].get("receivedDateTime"))
        ]
        times = [t for t in times if t]
        last_msg = pairs[-1][0]
        first_at = min(times).isoformat() if times else None
        last_at = max(times).isoformat() if times else None
        rtype = classify_reply_type(last_msg)
        matched_by = pairs[0][1]

        rec = all_rows[row_i]
        rec["reply_status"] = "replied"
        rec["reply_count"] = len(pairs)
        rec["first_reply_at"] = first_at
        rec["last_reply_at"] = last_at
        rec["reply_from"] = _sender_addr(last_msg)
        rec["reply_subject"] = last_msg.get("subject")
        rec["reply_preview"] = (last_msg.get("bodyPreview") or "")[:500] or None
        rec["reply_type"] = rtype
        rec["matched_by"] = matched_by

    # Rows that were sent but still no match
    for rec in all_rows:
        if rec.get("sent") is not True:
            continue
        if rec.get("reply_status") == "replied":
            continue
        rec.setdefault("reply_status", "none")
        rec.setdefault("reply_count", 0)
        rec.setdefault("first_reply_at", None)
        rec.setdefault("last_reply_at", None)
        rec.setdefault("reply_from", None)
        rec.setdefault("reply_subject", None)
        rec.setdefault("reply_preview", None)
        rec.setdefault("reply_type", None)
        rec.setdefault("matched_by", None)

    with open(output_jsonl, "w", encoding="utf-8") as out:
        for rec in all_rows:
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")

    replied = sum(1 for r in all_rows if r.get("reply_status") == "replied")
    return {
        "output": output_jsonl,
        "sent_rows": sent_rows,
        "inbox_messages_scanned": len(inbox),
        "rows_marked_replied": replied,
    }


def run_cli(
    input_path: str,
    output_path: str | None,
    since_days: int,
) -> None:
    out = output_path or (input_path.replace(".jsonl", "") + ".with_replies.jsonl")
    meta = update_outreach_records_with_replies(
        input_path,
        out,
        since_days=since_days,
    )
    print(
        f"Reply tracking complete → {meta['output']}\n"
        f"  sent rows: {meta['sent_rows']}, "
        f"inbox messages (window): {meta['inbox_messages_scanned']}, "
        f"replied: {meta['rows_marked_replied']}",
    )
