# Trace

Outbound email tool: find people who live with a problem, then **Claude draft → self-critique (rubric) → optional send.**

A **product profile** is what you sell, who to look for, and how to write. This repo is the tool. Campaigns (who you email for) live in a profile, not in the product name.

## Default safety

**By default nothing is sent.** Draft + critique + JSONL always run.

`--send` only auto-sends emails that **PASS** (score ≥ **90**, no hard fails). Scores **80–89** go to **REVIEW** (saved in JSONL with `requires_manual_review`, never auto-sent). Below **80**, or unresolved hard fails → **BLOCK**.

**Body length:** The 75-word cap is counted locally: `body_word_count`, `counted_text`, `length_status` (greeting line and two-line sign-off excluded).

## Setup

### 1. Create `.env`

```bash
cp .env.example .env
```

Fill in API keys and Azure credentials. Optional: **`SENDER_FIRST_NAME`** (used only if the profile has no `sign_off`). For `--discover-signals`, set **`XAI_API_KEY`**.

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

## Add a product profile

Copy `examples/product.example.json` and fill it in. That file is the whole campaign: discovery search + email voice.

**Discovery** (required):

| Field | What to put |
|-------|-------------|
| `product_name` | Name used in research and, if mentioned, in the email |
| `what_it_does` | One or two sentences in the buyer's workflow |
| `target_users_or_buyers` | Who actually does the work or buys |
| `problems_it_solves` | Recurring jobs they already do the hard way |
| `examples_of_problem_signals` | First-person sentences they might post or say |
| `obvious_non_targets_or_adjacent_vendors` | Same-category vendors, commentators, unrealistic logos |

**Email** (same JSON; skip these and Trace falls back to the legacy 2-sentence template):

| Field | What to put |
|-------|-------------|
| `profile_kind` | `problem_validation` |
| `email_mode` | `trace_strategy_email` |
| `product_context` | Internal brief. Never dumped into the email. |
| `sign_off` | Two lines: name, then company or "building X" |
| `sender_block` | Who is writing, desired outcome, constraints |
| `search_guidance` | Optional. How to hunt (usually: not by product name) |
| `list_name` | Optional. Used in output filenames (default `custom`) |

Then run research against that file:

```bash
python main.py --discover-signals --product-config your_product.json --limit 5
```

To make `--list your_name` work without passing JSON every time, copy the same fields into `PRODUCT_PROFILES`, `LIST_TO_PROFILE`, and `LEAD_LISTS` in `main.py`. Point `LEAD_LISTS` at a local CSV (CSV files are gitignored).

## Run

```bash
# Research only (steps 1-6). You check LinkedIn. No email.
python main.py --discover-signals --product-config your_product.json --limit 5
python main.py --review-candidates --candidates-file runs/signals_....jsonl

# Email path after you confirm people (steps 7-10)
python main.py --set-human-status APPROVED --candidate-id sig_... --candidates-file runs/signals_....jsonl
python main.py --export-approved --candidates-file runs/signals_....jsonl
python main.py --import-enriched apollo.csv --candidates-file runs/signals_....jsonl
python main.py --process-approved --candidates-file runs/signals_....jsonl
# --send only when you actually want PASS drafts sent
python main.py --process-approved --candidates-file runs/signals_....jsonl --send
```

If a profile is already registered in `main.py`, `--list NAME` is a shortcut for that campaign. Prefer `--product-config` for anything new.

Apollo export: include the usual contact/org columns; optional `Company Description` is used when present.

## How it works

### Research (`--discover-signals`)

1. Grok searches X and the web from the profile (problem behavior, not product-name shopping).
2. Signals are merged, ranked, and grouped by person.
3. Identifiable people get a qualification pass; a few get a deeper pass.
4. JSONL under `runs/` is the review queue. You check LinkedIn. Nothing is emailed.

### Email (`--process-approved` or `--list` on a CSV)

1. **Load** Apollo fields as a whitelist (no invented facts).
2. **Classify** segment deterministically (`segment_reason` saved for audit).
3. **Draft** from the profile (`trace_strategy_email` by default for new profiles).
4. **Critique** with the problem-validation rubric. Sign-off trivia is not a hard fail when the ending is clearly a two-line signature.
5. **Verdicts:** **PASS** (≥90) → eligible for `--send` | **REVIEW** (80–89, no hard fails) → JSONL only | **BLOCK** (<80 or hard fails unresolved).
6. **Append one JSON line per lead** under `runs/`.
7. **Auto-send** with `--send` applies only to **PASS**.

## CLI reference

| Flag | Meaning |
|------|---------|
| `--product-config PATH` | JSON product profile (see `examples/product.example.json`). |
| `--list NAME` | Shortcut for a profile already registered in `main.py`. |
| `--send` | Auto-send **only** PASS (≥90). REVIEW is never auto-sent. |
| `--limit N` | Process at most N leads after `--start`. |
| `--start K` | 1-based lead index to start from. |
| `--test-batch NAME` | Stored on each JSONL record. |
| `--output-dir DIR` | JSONL directory (default `runs`). |
| `--dry-run` | Explicit label; behavior matches default unless `--send` is set. |

## Tests

```bash
pytest tests/
```

## Tuning

In `main.py`: `PASS_THRESHOLD` (legacy 80), **`PASS_THRESHOLD_PB` (90)**,
**`REVIEW_THRESHOLD_PB_MIN` (80)** for the manual-review band,
`REVISE_THRESHOLD` (legacy 60), `MAX_REVISE_ATTEMPTS`, `PRODUCT_PROFILES`.
