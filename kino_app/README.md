# KINO Analysis

## Run it
```
pip install -r requirements.txt
streamlit run kino_app.py
```
Reads every `*.parquet` file in `kino_data/` — currently just January 2026
(8,928 draws). To add more months, convert each raw Allwyn export once:

```python
import kino_core
kino_core.convert_raw_kino_xlsx("kino_2026_02.xlsx", "kino_data/kino_2026_02.parquet")
```

**Never read a raw export directly at runtime** — a single month's raw
328-column file takes ~27s to parse; the same data as compact Parquet loads
in ~0.1s (287KB vs 11MB). The conversion step is meant to run once per file,
not on app startup.

## What's here
- `kino_core.py` — data pipeline (raw→Parquet conversion, multi-file
  loading) and `KinoAnalyzer` (vectorized hot/cold/overdue/repeat stats —
  built with numpy, not Python loops, since full history could be millions
  of rows; stress-tested at a simulated 2.7M rows).
- `kino_engine.py` — the number-selection engine: scoring (same
  frequency/recency/overdue blend as the Tzoker engine, rescaled for KINO's
  5-minute draw cadence), pick generation, real per-number evidence, and an
  honest backtest that compares against a pure-chance baseline rather than
  claiming an edge.
- `kino_paytable.py` — official Allwyn payout structure, transcribed with
  explicit notes on what's confirmed (cross-checked against the worked
  example and independently-derived odds) vs partial/unconfirmed (see the
  module docstring for one specific number that couldn't be reconciled
  between two sources).
- `kino_app.py` — Dashboard, Pick Generator, Backtest.

## Why KINO 7 is the Pick Generator's default
Independently verified: KINO 7's "7 of 7" pays the same €2,500 as Tzoker's
4+1, at roughly 3x better odds (1 in 40,979 vs 1 in 122,176) and half the
ticket price (€0.50 vs €1). That's a fact about the two games' fixed
structures, not a claim this engine improves anyone's odds within a game —
every combination of N numbers from 80 is exactly as likely as any other,
regardless of history. The Pick Generator organizes a consistent play using
real statistics; the Backtest tab is there specifically to show, honestly,
that the scoring tracks random chance rather than beating it.

## Known data-quality note
Allwyn's raw export has a `ΣΤΗΛΗ` (column) field that's unreliable for
~30% of rows in the January 2026 file (contains odd/even labels instead of
column identifiers, verified directly against the raw column before any of
this code touched it). It's captured but not surfaced anywhere in the app
until that's understood.

## AI Insights (Claude + ChatGPT second opinion)
The "AI Insights" tab mirrors the Tzoker app's: Claude picks numbers from
the same statistical scores the Pick Generator uses (never invents its
own), cost is always recomputed deterministically in Python (never trusted
from Claude's own arithmetic), and every claim about a number must cite the
real evidence attached independently — not Claude's restatement of it.
ChatGPT then reviews the pick against that same evidence rather than
proposing a competing one. After the pick, you can chat with Claude —
ask questions, or a one-click button asks it to reconsider based on the
cross-check table (rank over last 500/2000/all-time draws) — every chat
turn goes through the identical validation as the first pick.

Needs `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` (env vars on Railway, or
`.streamlit/secrets.toml` locally, or a one-off password field in the UI).
Never commit a key to this repo.

One thing this KINO version deliberately does NOT include, unlike the
Tzoker app: pot-avoidance reasoning (preferring less-popular numbers to
avoid splitting a prize). Tzoker's rules clearly document which categories
share a capped pot; KINO's payouts here are fixed multipliers per ticket
for the games covered, and the one exception (a per-draw payout cap on the
largest ΠΑΡΑ1 games) isn't confirmed well enough to build into the prompt
responsibly. Both prompts explicitly forbid inventing that reasoning.

## Live data & tracking (new)

**Fetching new draws** — the Dashboard has a "🔄 Fetch latest draws" expander
that calls OPAP's public draws API directly (game ID 1100 for KINO). This
endpoint pattern is confirmed by three independent open-source projects
(all agreeing exactly on the URL shape), but it was **never tested against
the live endpoint** in the environment this was built in (no network access
to api.opap.gr from that sandbox). First real click will tell you if it
still works post-Allwyn-rebrand — if it fails, the error message says why,
and everything else in the app keeps working from the existing manually-
converted monthly files regardless.

New draws get persisted to `kino_data/kino_live.parquet` (merged with the
monthly historical files automatically — `load_all_kino_draws()` reads
every `*.parquet` in the folder) and the app's cache is cleared so stats
recalculate against the fresh data immediately.

**Track Record** — a new tab, distinct from Backtest. Backtest replays
history; Track Record logs real picks (a "💾 Track this pick" button on
both Pick Generator and AI Insights) and resolves them against whatever
real draw happens next, once you fetch new draws. Same honesty rule as
everywhere else: this shows what happened, compared against the pure-
chance baseline — it's a record, not a promise about future picks.
