# Trace web UI

A Next.js (App Router) front end for Trace, the outbound email tool. It talks to the local
Trace FastAPI server and holds no data of its own: every list, draft, and cost figure on
screen comes from the API, so a reload never loses anything.

Trace is the outbound tool. It is not Helix — Helix is the cold-calling product, and Trace is
one of the tools that emails people who might need it.

## Requirements

- Node 22 and npm 10 (or newer)
- Python 3 with the Trace API dependencies installed, in the repository root

## Run it

Two processes. Start the API first.

**1. The API** (from the repository root, not from `web/`):

```bash
cd /Users/hyunmyungchoi/Trace
python3 -m uvicorn trace_app.api:app --port 8000
```

It seeds the built-in profiles on startup and stores everything in a local SQLite database.

**2. The web app** (in a second terminal):

```bash
cd /Users/hyunmyungchoi/Trace/web
npm install     # first time only
npm run dev
```

Open http://localhost:3000.

## Configuration

The API base URL is read from `NEXT_PUBLIC_TRACE_API` and defaults to `http://localhost:8000`.
To point at a different host, copy `.env.example` to `.env.local` and edit it:

```bash
cp .env.example .env.local
```

The API's CORS policy allows `http://localhost:3000` by default; set `TRACE_WEB_ORIGIN` on the
API process if you serve the web app from somewhere else.

## Other commands

```bash
npm run build      # production build, fails on any TypeScript error
npm run start      # serve the production build on port 3000
npm run typecheck  # tsc --noEmit
```

## What the screens do

- **New hunt** — five steps. Pick a hunt size, start a hunt, watch it poll, decide on each
  person one at a time, review and edit the drafts Trace wrote, then send. Finding people
  never sends mail and approving never sends mail; only step 5 does.
- **People and history** — every person in the selected profile, with a sortable table, a
  found-on filter, and a detail panel holding the evidence, contact sources, draft, notes, and
  the Close / Disqualify actions.
- **Cost** — total research spend, hunts run, spend by stage, and the estimate for the next
  hunt at the currently selected size.
- **Add profile** — the four-part brief a hunt runs from. Saving switches to the new profile.

Each profile keeps its own people, hunts, and spend. Switching profiles reloads all three.

## Notes on honesty in the UI

- Nothing is fabricated. If the API returns an empty list, the screen says so.
- Opens, clicks, and replies are not tracked. The Replied stat is labelled as untracked and
  the sent chart is a count of sends, not a funnel.
- If `mailboxReady` is false in `/api/health`, Trace sending is disabled and the reason is
  shown. Recording mail you sent yourself still works.
- Guard violations from the API (HTTP 409) surface their message in a callout rather than
  being swallowed.
