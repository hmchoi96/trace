"""HTTP surface for the Trace UI. Thin: every rule lives in the service layer."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import db, guards, profiles, service, worker

_worker = worker.Worker()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    service.init()
    if os.environ.get("TRACE_INLINE_WORKER", "1") == "1":
        _worker.start()
    yield
    _worker.stop()


app = FastAPI(title="Trace", version="1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        os.environ.get("TRACE_WEB_ORIGIN", "http://localhost:3000"),
        "http://127.0.0.1:3000",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

def conn():
    return db.connect()


def _guarded(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except guards.GuardError as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": exc.message})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "invalid", "message": str(exc)})


# ── Models ──────────────────────────────────────────────────────────────────


class HuntRequest(BaseModel):
    profileId: str
    limit: int = 5


class DecisionRequest(BaseModel):
    decision: str
    reason: Optional[str] = None


class DraftRequest(BaseModel):
    templateId: Optional[str] = None


class DraftEdit(BaseModel):
    subject: Optional[str] = None
    body: Optional[str] = None


class OutcomeRequest(BaseModel):
    outcome: Optional[str] = None


class NoteRequest(BaseModel):
    text: str


class ContactUpdate(BaseModel):
    email: Optional[str] = None
    phone: Optional[str] = None


class ProfileRequest(BaseModel):
    name: str
    whatItDoes: str
    senderName: str = ""
    senderCompany: str = ""
    senderWork: str = ""
    signOff: str = ""
    desiredOutcome: str = ""
    buyers: str = ""
    problems: str = ""
    goodSignals: str = ""
    skip: str = ""
    qualify: str = ""
    searchGuidance: str = ""
    productContext: str = ""
    fromEmail: str = ""
    template: str = profiles.DEFAULT_TEMPLATE
    searchWeb: bool = True
    searchX: bool = True
    searchLinkedin: bool = True
    preferWeb: bool = False


# ── Routes ──────────────────────────────────────────────────────────────────


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "mailbox": guards.connected_mailbox(),
        "mailboxReady": guards.mailbox_ready(),
        "anthropic": bool(os.getenv("ANTHROPIC_API_KEY")),
        "grok": bool(os.getenv("XAI_API_KEY")),
        "apollo": bool(os.getenv("APOLLO_API_KEY")),
        "hunter": bool(os.getenv("HUNTER_API_KEY")),
    }


@app.get("/api/templates")
def templates() -> list[dict[str, Any]]:
    return list(profiles.TEMPLATES.values())


@app.get("/api/profiles")
def list_profiles() -> list[dict[str, Any]]:
    return profiles.list_profiles(conn())


@app.post("/api/profiles")
def create_profile(payload: ProfileRequest) -> dict[str, Any]:
    return _guarded(profiles.create_profile, conn(), payload.model_dump())


@app.get("/api/profiles/{profile_id}/people")
def people(profile_id: str) -> list[dict[str, Any]]:
    return service.people(conn(), profile_id)


@app.get("/api/profiles/{profile_id}/hunts")
def hunts(profile_id: str, limit: int = 20) -> list[dict[str, Any]]:
    return service.list_hunts(conn(), profile_id, limit=limit)


@app.get("/api/profiles/{profile_id}/cost")
def cost(profile_id: str, limit: int = 5) -> dict[str, Any]:
    c = conn()
    out = service.cost_summary(c, profile_id)
    out["nextHunt"] = service.estimate_hunt_usd(c, profile_id, limit)
    out["limits"] = list(service.HUNT_LIMITS)
    return out


@app.post("/api/hunts")
def create_hunt(payload: HuntRequest) -> dict[str, Any]:
    return _guarded(service.create_hunt, conn(), payload.profileId, payload.limit)


@app.post("/api/hunts/{hunt_id}/cancel")
def cancel_hunt(hunt_id: str) -> dict[str, Any]:
    return _guarded(service.cancel_hunt, conn(), hunt_id)


@app.get("/api/hunts/{hunt_id}")
def get_hunt(hunt_id: str) -> dict[str, Any]:
    out = service.get_hunt(conn(), hunt_id)
    if not out:
        raise HTTPException(status_code=404, detail="Unknown hunt")
    return out


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    out = service.get_job(conn(), job_id)
    if not out:
        raise HTTPException(status_code=404, detail="Unknown job")
    return out


@app.get("/api/candidates/{candidate_id}")
def get_candidate(candidate_id: str) -> dict[str, Any]:
    c = conn()
    row = service.get_candidate(c, candidate_id)
    if not row:
        raise HTTPException(status_code=404, detail="Unknown person")
    return service.candidate_dto(c, row)


@app.post("/api/candidates/{candidate_id}/decision")
def decide(candidate_id: str, payload: DecisionRequest) -> dict[str, Any]:
    return _guarded(service.decide, conn(), candidate_id, payload.decision, payload.reason)


@app.post("/api/candidates/{candidate_id}/pull-contact")
def pull_contact(candidate_id: str) -> dict[str, Any]:
    return _guarded(service.pull_contact, conn(), candidate_id)


@app.patch("/api/candidates/{candidate_id}/contact")
def patch_contact(candidate_id: str, payload: ContactUpdate) -> dict[str, Any]:
    return _guarded(
        service.update_contact,
        conn(),
        candidate_id,
        email=payload.email,
        phone=payload.phone,
    )


@app.post("/api/candidates/{candidate_id}/draft")
def draft(candidate_id: str, payload: DraftRequest) -> dict[str, Any]:
    return _guarded(service.create_draft, conn(), candidate_id, template_id=payload.templateId)


@app.post("/api/candidates/{candidate_id}/outcome")
def outcome(candidate_id: str, payload: OutcomeRequest) -> dict[str, Any]:
    _guarded(service.set_outcome, conn(), candidate_id, payload.outcome)
    return {"ok": True}


@app.post("/api/candidates/{candidate_id}/notes")
def note(candidate_id: str, payload: NoteRequest) -> dict[str, Any]:
    _guarded(service.add_note, conn(), candidate_id, payload.text)
    return {"ok": True}


@app.post("/api/candidates/{candidate_id}/sent-myself")
def sent_myself(candidate_id: str) -> dict[str, Any]:
    return _guarded(service.mark_sent_myself, conn(), candidate_id)


@app.patch("/api/drafts/{draft_id}")
def patch_draft(draft_id: str, payload: DraftEdit) -> dict[str, Any]:
    _guarded(service.edit_draft, conn(), draft_id, payload.subject, payload.body)
    return {"ok": True}


@app.post("/api/drafts/{draft_id}/send")
def send(draft_id: str) -> dict[str, Any]:
    return _guarded(service.send_draft, conn(), draft_id)
