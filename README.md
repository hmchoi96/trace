# Trace

B2B cold-email automation: **Claude draft → self-critique (rubric) → optional send.**

- **`akashic`:** legacy Wiserbond discovery emails (2 sentences + question, strict rubric).
- **`problem_validation` / `helix`:** same CSV; **Helix problem-validation** flow (Apollo whitelist, deterministic segment, JSONL on every row). **`helix` is an alias** for `problem_validation`.

## Default safety

**By default nothing is sent.** Draft + critique + JSONL always run for each lead.

For **`problem_validation`**, **`--send`** only auto-sends emails that **PASS** (score ≥ **90**, no hard fails). Scores **80–89** go to a **REVIEW** verdict (saved in JSONL with `requires_manual_review`, never auto-sent). Below **80** (or hard fails after revision) → **BLOCK**.

**Body length:** The 75-word cap uses **deterministic** fields in JSONL: `body_word_count`, `counted_text`, `length_status` (greeting line and two-line sign-off excluded). Claude does not author length hard fails; the pipeline strips bad ones and adds one if the local count exceeds 75.

## Setup

### 1. Create `.env`

```bash
cp .env.example .env
```

Fill in API keys and Azure credentials. Optional: **`SENDER_FIRST_NAME`** (first line of the problem-validation sign-off; default **`Jamie`**). For `--discover-signals`, set **`XAI_API_KEY`**.

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Run

```bash
# Akashic (legacy profile; send still requires --send)
python main.py --list akashic

# Problem validation (Helix use case) — default: no send, writes JSONL under runs/
python main.py --list problem_validation --limit 5 --test-batch pilot-a

# Same list, alias flag
python main.py --list helix --limit 3

# Research only (steps 1-6). You check LinkedIn. No email.
python main.py --research-only --list akashic --limit 3
python main.py --discover-signals --list helix
python main.py --review-candidates --candidates-file runs/signals_....jsonl

# Email path after you confirm people (steps 7-10)
python main.py --set-human-status APPROVED --candidate-id sig_... --candidates-file runs/signals_....jsonl
python main.py --export-approved --candidates-file runs/signals_....jsonl
python main.py --import-enriched apollo.csv --candidates-file runs/signals_....jsonl
python main.py --process-approved --candidates-file runs/signals_....jsonl
# --send only when you actually want PASS drafts sent
python main.py --process-approved --candidates-file runs/signals_....jsonl --send
# Custom product JSON: product_name, what_it_does, target_users_or_buyers, problems_it_solves, examples_of_problem_signals, obvious_non_targets_or_adjacent_vendors
python main.py --discover-signals --product-config my_product.json

# Actually send when rubric passes (use only after reviewing JSONL)
python main.py --list problem_validation --send

# Akashic starting at lead 5, open question style
python main.py --list akashic --question-style open --start 5
```

## How it works

### `akashic`

1. **Draft.** Claude writes using the legacy template (no product in body, etc.).
2. **Critique.** Soft scores: `personalization`, `question_quality`, `voice`, `hook`.
3. **Verdict / revise / block** (same thresholds as before).
4. **Send** only with `--send`.

### `problem_validation` (and `--list helix`)

1. **Load** Apollo CSV into a whitelist field map (no invented facts).
2. **Classify** segment deterministically (`segment_reason` saved for audit).
3. **Draft** founder-led problem-discovery email (`problem_validation_email` mode; `sales_pitch_email` is separated but not the default).
4. **Critique** with problem-validation rubric (`problem_relevance`, `evidence_safety`, …). Sign-off trivia is **not** a hard fail when the ending is clearly a two-line signature.
5. **Verdicts:** **PASS** (≥90) → eligible for `--send` | **REVIEW** (80–89, no hard fails) → manual approval, JSONL only | **BLOCK** (&lt;80 or hard fails unresolved).
6. **Subject line** hint is fixed per **segment** to reduce repetition.
7. **Append one JSON line per lead** under `runs/` (PASS / REVIEW / BLOCK / failures).
8. **Auto-send** with `--send` applies only to **PASS** (≥90), not REVIEW.

## Lists

| Flag                     | CSV file                 | Profile              |
|--------------------------|--------------------------|----------------------|
| `--list akashic`         | `akashic_record_list.csv` | `akashic` (Wiserbond) |
| `--list problem_validation` | `helix_list.csv`      | `problem_validation` |
| `--list helix`           | `helix_list.csv` (alias) | `problem_validation` |

Apollo export: include the usual contact/org columns; optional `Company Description` is used when present.

## CLI reference

| Flag | Meaning |
|------|---------|
| `--send` | Auto-send **only** PASS (≥90) problem-validation emails. REVIEW (80–89) is never auto-sent. |
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
