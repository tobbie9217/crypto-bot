# Project guidance for Claude Code sessions

## Read this first

Before doing anything substantive, read **`docs/work_log.md`** from top to
bottom. It is the canonical record of what has been done, what is currently
deployed vs only code-complete, and what is meant to be done next. The most
recent entry is at the top.

Use it as ground truth over recollection — if `work_log.md` and your prior
training conflict, trust `work_log.md`.

## Maintaining the work log (the only rule that's non-negotiable)

You MUST update `docs/work_log.md` whenever any of these happens:

1. **A multi-step task wraps up** (commit pushed, deploy verified, milestone
   crossed). Add a new entry above the most recent one.
2. **A significant decision is taken** (architecture, scope, "we tried X and
   chose Y because Z"). Even a one-line note in the current entry under a
   "Decisions" subhead is enough.
3. **An external action is initiated that the user will be reminded of
   later** (API application submitted, paid subscription started, manual
   action scheduled). Record the date and what's blocking us.
4. **Before ending a session** where any of the above happened.

Format conventions:

- Most recent entry at the top.
- Entry header: `## YYYY-MM-DD — <short headline>`.
- Each entry includes (as applicable): Summary, What got built (with file
  paths), Deployed and verified, Results, What's still open, Next moves,
  Project state at end of session.
- Use ✅ for done/verified, ⏳ for code-complete-not-yet-deployed, ❌ for
  blocked/skipped, ⊘ for deliberately abandoned.
- Append-only — never edit older entries; just add new ones above.

If a session is purely exploratory (read-only, no code or config change),
no work_log entry is required. The judgement call: would a fresh Claude
session in a week need this context to continue effectively? If yes, log it.

## Project context (anchors for fresh sessions)

- **What this is**: personal, non-commercial cryptocurrency sentiment +
  on-chain + derivatives trading research bot. Single user. See the
  top-level `README.md` for the architecture diagram.
- **Stack**: Python services in Docker Compose. Four containers:
  `postgres` (single source of truth), `ingest` (data collectors +
  scheduled jobs + WebSocket listeners), `sentiment` (CryptoBERT
  scoring), `freqtrade` (the trading engine with Telegram control).
- **Strategy file**: `user_data/strategies/SentimentOnchainStrategy.py`
  is the active strategy. Two entry paths: `strict` (high confluence,
  rare) and `momentum` (pullback-after-pump, more frequent).
- **Backtest config**: `user_data/config_backtest.json` uses
  `StaticPairList` with a 36-pair whitelist. The main `config.json`
  uses `CatalystPairList` which does NOT work in backtest — always
  use `config_backtest.json` for backtests.
- **Data audit reference**: `docs/deep_research.pdf` (and `.md`, `.txt`
  copies). Many work-log entries reference it by section number.

## House rules that apply to every session

1. **Don't push to `origin/main` without explicit user confirmation.** Commit
   freely, push only when asked.
2. **The repo is public** at `https://github.com/tobbie9217/crypto-bot`.
   Avoid committing anything that contains secrets. `.env` is correctly
   gitignored; verify `.env.example` is the only env file tracked before
   any commit that touches config.
3. **When making strategy changes, snapshot any new feature into the trade
   journal** by adding it to `_SNAPSHOT_COLUMNS` and (if it's a tunable
   threshold) into `_current_thresholds()`. This is how we audit which
   inputs were live at any given entry/exit.
4. **Backtests use `user_data/config_backtest.json`**:
   `docker compose exec -T freqtrade freqtrade backtesting --config user_data/config_backtest.json --strategy SentimentOnchainStrategy --timerange YYYYMMDD-YYYYMMDD`
5. **Strategy file is hot-reloaded on `docker compose restart freqtrade`** — no
   rebuild needed for strategy edits. Rebuild only when Python dependencies
   change.
6. **Ingest needs a rebuild for new collector deps** but not for code-only
   changes. Restart is enough for the latter.

## Reddit API status

As of the most recent work_log entry: Reddit Data API application was
submitted on **2026-05-12** after a prior rejection. Awaiting decision
(typical turnaround 5-10 business days). The submitted application is
based on `services/ingest/ingest/collectors/reddit.py` and emphasizes:
single-user personal research, no AI/ML training, no redistribution,
read-only on r/cryptocurrency + r/bitcoin + r/cryptomarkets, ~3 req/min.

The `reddit.py` collector auto-enables when `REDDIT_CLIENT_ID` and
`REDDIT_CLIENT_SECRET` appear in `.env`. No code change needed when
approval lands — just env vars + restart ingest.

## When in doubt

Trust `docs/work_log.md`, the `README.md`, and the commit history in that
order. Don't reconstruct from memory; verify by reading.
