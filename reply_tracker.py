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


def _plain_text(value: str) -> str:
    text = re.sub(r"(?is)<br\s*/?>", "\n", value or "")
    text = re.sub(r"(?is)</p>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    return text.replace("&nbsp;", " ")


_QUOTE_CUTS = (
    re.compile(r"\nOn [^\n]{0,300} wrote:\s*", re.I),
    re.compile(r"\n-{5,}\s*Original Message\s*-{5,}", re.I),
    re.compile(r"\n_{5,}"),
    re.compile(r"\nFrom:\s", re.I),
    re.compile(r"\n보낸 사람\s*:", re.I),
)


def strip_quoted_reply(text: str) -> str:
    """Keep the new reply. Quoted Outlook and Gmail threads are not the reply."""
    plain = _plain_text(text or "")
    cut = len(plain)
    for pattern in _QUOTE_CUTS:
        match = pattern.search(plain)
        if match:
            cut = min(cut, match.start())
    return plain[:cut].strip()


def reply_excerpt(message: dict[str, Any], limit: int = 500) -> str:
    fresh = strip_quoted_reply(_body_text(message))
    if not fresh:
        fresh = strip_quoted_reply(str(message.get("bodyPreview") or ""))
    return " ".join(fresh.split())[:limit]


def _classification_text(message: dict[str, Any]) -> str:
    """Quality uses the new reply and the subject, not the quoted original."""
    return f"{reply_excerpt(message)} {message.get('subject') or ''}".lower()


def classify_reply_quality(message: dict[str, Any]) -> str:
    """positive, engaged, neutral, negative, automated, or unknown.

    Automated mail is not a human reply and is not a success.
    """
    text = _classification_text(message)
    sender = _sender_addr(message)
    automated = (
        "automatic reply",
        "out of office",
        "out of the office",
        "away from the office",
        "autoresponder",
        "auto-reply",
        "自動返信",
        "undeliverable",
        "delivery status",
        "delivery has failed",
        "mailer-daemon",
        "postmaster",
        "this is an automated",
        "this is an automatic",
        "do not reply to this",
        "thank you for applying",
        "thanks for applying",
        "application has been received",
        "we received your application",
        "your application was received",
        "calendar invitation",
        "accepted this invitation",
        "declined this invitation",
        "tentatively accepted",
        "newsletter",
        "view in browser",
    )
    if any(x in text for x in automated) or any(x in sender for x in ("mailer-daemon", "postmaster", "noreply", "no-reply")):
        return "automated"
    if not text.strip():
        return "unknown"
    negative = (
        "not interested",
        "no thanks",
        "remove me",
        "stop emailing",
        "do not contact",
        "not a fit",
    )
    if any(x in text for x in negative):
        return "negative"
    positive = (
        "sounds interesting",
        "happy to chat",
        "let's talk",
        "lets talk",
        "impressive",
        "worth a look",
        "send it over",
        "i'm interested",
        "i am interested",
        "availability",
    )
    if any(x in text for x in positive):
        return "positive"
    if "tell me more" in text or "curious how" in text:
        return "engaged"
    if "?" in text and len(text.strip()) > 20:
        return "engaged"
    return "neutral"


def classify_reply_type(message: dict[str, Any]) -> str:
    text = _classification_text(message)
    if any(
        x in text
        for x in ("out of office", "out of the office", "automatic reply", "away from the office", "auto-reply")
    ):
        return "out_of_office"
    quality = classify_reply_quality(message)
    if quality == "automated":
        return "automated"
    return quality


def is_human_reply(message: dict[str, Any]) -> bool:
    return classify_reply_quality(message) not in ("automated", "unknown")


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


def reply_folders() -> tuple[str, ...]:
    """Inbox only unless REPLY_TRACK_FOLDERS lists Graph folder names.

    Example: inbox,archive,deleteditems
    """
    raw = (os.getenv("REPLY_TRACK_FOLDERS") or "inbox").strip()
    parts = tuple(p.strip() for p in raw.split(",") if p.strip())
    return parts or ("inbox",)


def graph_messages_url(mailbox: str, folder: str) -> str:
    """Application permissions have no /me. Read a specific mailbox."""
    return (
        f"https://graph.microsoft.com/v1.0/users/{mailbox}"
        f"/mailFolders/{folder}/messages"
    )


def fetch_recent_folder_messages(
    since: datetime,
    *,
    token: str,
    mailbox: str,
    folder: str = "inbox",
    top: int = 200,
) -> list[dict[str, Any]]:
    """Messages in one Graph mail folder received on or after `since` (UTC)."""
    since_s = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.0000000Z")
    base = graph_messages_url(mailbox, folder)
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


def fetch_recent_inbox_messages(
    since: datetime,
    *,
    token: str,
    mailbox: str,
    top: int = 200,
) -> list[dict[str, Any]]:
    """Messages in Inbox received on or after `since` (UTC)."""
    return fetch_recent_folder_messages(
        since, token=token, mailbox=mailbox, folder="inbox", top=top
    )


def fetch_recent_messages(
    since: datetime,
    *,
    token: str,
    mailbox: str,
    folders: tuple[str, ...] | None = None,
    top: int = 200,
) -> list[dict[str, Any]]:
    """Scan configured folders. Default remains Inbox only."""
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for folder in folders or reply_folders():
        for msg in fetch_recent_folder_messages(
            since, token=token, mailbox=mailbox, folder=folder, top=top
        ):
            mid = str(msg.get("id") or "")
            if mid and mid in seen:
                continue
            if mid:
                seen.add(mid)
            found.append(msg)
    return found


def _domain(addr: str) -> str:
    addr = addr.strip().lower()
    if "@" in addr:
        return addr.split("@", 1)[-1]
    return ""


def _subjects_equal(a: str, b: str) -> bool:
    na, nb = normalize_subject(a), normalize_subject(b)
    return bool(na) and na == nb


def _subjects_related(a: str, b: str) -> bool:
    na, nb = normalize_subject(a), normalize_subject(b)
    if not na or not nb or na == nb:
        return False
    return na in nb or nb in na


def _quotes_original(message: dict[str, Any], outreach: dict[str, Any]) -> bool:
    body = _body_text(message).lower()
    sent = " ".join(str(outreach.get("body") or "").split()).lower()
    if len(sent) >= 40 and sent[:80] in body:
        return True
    return False


def link_reply(
    message: dict[str, Any],
    outreach: dict[str, Any],
) -> str | None:
    """Match a mailbox message to one send. Quoted text is used only for the quote check."""
    sender = _sender_addr(message)
    mailbox = (SENDER_EMAIL or "").strip().lower()
    if sender and mailbox and sender == mailbox:
        return None

    subj_m = message.get("subject") or ""
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
    same_sender = bool(sender and to_email and sender == to_email)
    same_domain = bool(
        sender and to_email and _domain(sender) and _domain(sender) == _domain(to_email)
    )
    if same_sender and _subjects_equal(ours_sub, subj_m):
        return "sender_subject"
    if same_domain and _subjects_equal(ours_sub, subj_m):
        return "domain_subject"
    if _subjects_equal(ours_sub, subj_m) and _quotes_original(message, outreach):
        return "quoted_original"
    if same_domain and _subjects_related(ours_sub, subj_m):
        return "manual_review"
    return None


def match_reply_to_outreach(
    message: dict[str, Any],
    outreach: dict[str, Any],
) -> str | None:
    """
    Match order:
    conversation_id, sender_subject, domain_subject, quoted_original, manual_review.
    Same subject alone does not match. Automated mail does not match.
    """
    if not is_human_reply(message):
        return None
    return link_reply(message, outreach)


_MATCH_RANK = {
    "conversation_id": 0,
    "sender_subject": 1,
    "domain_subject": 2,
    "quoted_original": 3,
    "manual_review": 4,
}
_HUMAN_MATCHES = ("conversation_id", "sender_subject", "domain_subject", "quoted_original")


def apply_reply_matches(
    rows: list[dict[str, Any]],
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach reply fields. Does not count automated mail as a human reply."""
    attached: dict[int, list[tuple[dict[str, Any], str]]] = {}
    for msg in messages:
        best: tuple[int, int, str] | None = None
        for i, rec in enumerate(rows):
            if rec.get("sent") is not True:
                continue
            how = match_reply_to_outreach(msg, rec)
            if not how:
                continue
            rank = _MATCH_RANK[how]
            if best is None or rank < best[0] or (rank == best[0] and i < best[1]):
                best = (rank, i, how)
        if best:
            attached.setdefault(best[1], []).append((msg, best[2]))

    for row_i, pairs in attached.items():
        pairs.sort(
            key=lambda p: _parse_iso(p[0].get("receivedDateTime"))
            or datetime.min.replace(tzinfo=timezone.utc),
        )
        human = [p for p in pairs if p[1] in _HUMAN_MATCHES]
        rec = rows[row_i]
        if not human:
            rec["reply_status"] = "manual_review"
            rec["reply_count"] = 0
            rec["reply_quality"] = None
            rec["reply_type"] = None
            rec["matched_by"] = "manual_review"
            rec["first_reply_at"] = None
            rec["last_reply_at"] = None
            rec["reply_from"] = None
            rec["reply_subject"] = None
            rec["reply_preview"] = None
            continue
        times = [
            t
            for t in (_parse_iso(p[0].get("receivedDateTime")) for p in human)
            if t
        ]
        first_msg = human[0][0]
        last_msg = human[-1][0]
        quality = classify_reply_quality(first_msg)
        rec["reply_status"] = "replied"
        rec["reply_count"] = len(human)
        rec["first_reply_at"] = min(times).isoformat() if times else None
        rec["last_reply_at"] = max(times).isoformat() if times else None
        rec["reply_from"] = _sender_addr(last_msg)
        rec["reply_subject"] = last_msg.get("subject")
        rec["reply_preview"] = (last_msg.get("bodyPreview") or "")[:500] or None
        rec["reply_type"] = classify_reply_type(first_msg)
        rec["reply_quality"] = quality
        rec["matched_by"] = human[0][1]

    for rec in rows:
        if rec.get("sent") is not True:
            continue
        if rec.get("reply_status") in ("replied", "manual_review"):
            continue
        rec.setdefault("reply_status", "none")
        rec.setdefault("reply_count", 0)
        rec.setdefault("first_reply_at", None)
        rec.setdefault("last_reply_at", None)
        rec.setdefault("reply_from", None)
        rec.setdefault("reply_subject", None)
        rec.setdefault("reply_preview", None)
        rec.setdefault("reply_type", None)
        rec.setdefault("reply_quality", None)
        rec.setdefault("matched_by", None)
    return rows


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
    inbox = fetch_recent_messages(since, token=token, mailbox=SENDER_EMAIL or "")
    apply_reply_matches(all_rows, inbox)

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
