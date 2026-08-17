# Trace

A tool for people who do not want to write every cold email by hand.

You describe the product once. Trace finds **people who are living with that problem right now** and drafts the email. You always decide whether it actually sends.

You do not need to be a developer. Paste the commands below into the terminal. Change only the file names to yours.

---

## What Trace does / what you do

| Trace | You |
|-------|-----|
| Finds people on the web and on X | Opens LinkedIn and marks “yes / no” |
| Writes the email draft | Pulls email addresses from Apollo |
| Scores whether the draft is good enough | Decides whether to send |

**Nothing sends until you add `--send`.** Without that flag, Trace only writes drafts and stops.

What the scores mean:

- **90 or above** — good enough to send (with `--send`, only these go out)
- **80–89** — you read it and send it yourself. It will not auto-send
- **Below 80** — do not send

---

## One-time setup

Python needs to be on the machine. If it is not, ask someone technical to open this repo and run the two lines below.

```bash
cp .env.example .env
pip install -r requirements.txt
```

Then open `.env`. It is a plain text file. Put your keys in.

| Field | When you need it |
|-------|------------------|
| `ANTHROPIC_API_KEY` | Writing drafts (Claude) |
| `XAI_API_KEY` | Finding people (Grok) |
| `APOLLO_API_KEY` | Attaching email addresses |
| Azure fields + `SENDER_EMAIL` | **Only when you actually send.** Leave blank if you are only reviewing drafts |

If you do not know where the keys are, they live in the settings page of that AI or Apollo account. This file does not go up to GitHub.

---

## Tell Trace what you sell (this is the important part)

Trace does not know your product. One JSON **profile** is that briefing.

1. Copy `examples/product.example.json`.
2. Rename it something like `my_product.json`. (The example is a fake coffee-shop inventory product. Replace all of it with yours.)
3. Fill it in as if you were briefing a new salesperson. Emails go out in English, so write the file in English.

How to think about each part:

**Who to find**

- `product_name` — product name
- `what_it_does` — one or two sentences, in the buyer’s workflow, of what it takes off their plate
- `target_users_or_buyers` — the person who actually does the work. “SaaS companies” is worse than “coffee-shop owners who still place the weekly order themselves”
- `problems_it_solves` — the annoying job they already do every week
- `examples_of_problem_signals` — a complaint they might post. Not a slogan
- `obvious_non_targets_or_adjacent_vendors` — competitors who sell the same thing, consultants who only comment, companies too big to be a first customer
- `search_guidance` — “do not search for our product name; find people who are doing X”

**How the email should sound**

- `product_context` — your internal brief. It does not get dumped into the email
- `sign_off` — last two lines of the email. Line 1 is the name, line 2 is the company
- `sender_block` — who is writing, and what conversation you want

Leave these two lines **exactly as in the example.** Changing them changes the email format.

- `"profile_kind": "problem_validation"`
- `"email_mode": "trace_strategy_email"`

The point: **do not ask Trace to find people looking for your product.** Ask it to find people who are still doing the job by hand. They do not need to know your product’s name.

---

## How to run it

The output file name is different every time. The terminal prints the path at the end. **Copy that path** into the `runs/signals_....jsonl` slot in the next command.

### 1) Find people

No email goes out. This only builds a candidate list. `--limit 5` means “stop around 5 people per channel.” Start small.

```bash
python main.py --discover-signals --product-config my_product.json --limit 5
```

When it finishes, a file appears in the `runs/` folder.

### 2) Read the list

```bash
python main.py --review-candidates --candidates-file runs/signals_....jsonl
```

You get the name, why they were picked, and whether Trace thinks they are a fit, a maybe, or a no. **You open LinkedIn.** Trace does not check it for you.

### 3) Mark the people who are right

One person at a time. Use the `sig_...` id the terminal printed.

```bash
python main.py --set-human-status APPROVED --candidate-id sig_... --candidates-file runs/signals_....jsonl
```

Or:

```bash
python main.py --set-human-status REJECTED --reject-reason vendor --candidate-id sig_... --candidates-file runs/signals_....jsonl
```

### 4) Attach email addresses (Apollo)

Trace does not invent email addresses. It exports only the people you approved, as a CSV for Apollo.

```bash
python main.py --export-approved --candidates-file runs/signals_....jsonl
```

Load that list into Apollo, export a CSV that has emails filled in, then:

```bash
python main.py --import-enriched apollo.csv --candidates-file runs/signals_....jsonl
```

Name, company, email, and title should be in the Apollo export. If `Company Description` is there, Trace uses that too.

### 5) Write drafts (still does not send)

```bash
python main.py --process-approved --candidates-file runs/signals_....jsonl
```

Drafts and scores land in `runs/`. Read them. If a draft is off, do not send it.

### 6) Send only when you mean it

```bash
python main.py --process-approved --candidates-file runs/signals_....jsonl --send
```

**Only scores of 90 or above go out.** 80–89 stays in the file. Copy those yourself if you still want to send them.

---

## When something feels stuck

**Nothing is sending.**  
You did not add `--send`, the score is under 90, or Azure / `SENDER_EMAIL` in `.env` is empty. If you are only reviewing drafts, that is expected.

**The people it finds are wrong.**  
The complaint sentences and the “skip these people” list are too thin. Do not rewrite the product pitch. Write **the job they actually do** in more concrete terms.

**It looks like it searched for our product name.**  
`search_guidance` says not to, but `examples_of_problem_signals` reads like a slogan. Change those to how a customer would actually complain.

**I do not know what JSON is.**  
Open `my_product.json` in a text editor. Keep the quotes and commas exactly as in the example. Change only the English words. If you drop a comma, Trace cannot read the file. If you get stuck, show that file to someone technical.

---

If you want to change scoring or the code, look at `main.py`. For day-to-day sales use, the steps above are enough.
