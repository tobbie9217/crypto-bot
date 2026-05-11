"""
Threshold proposer — reads the trade journal and recommends threshold
adjustments based on what would have improved historical trades.

For each tunable threshold, sweeps candidate values, filters past trades
to only those that would have passed, and reports the candidate that
maximizes net profit (subject to a minimum sample size).

LIMITATION: Can only recommend TIGHTENING (raising "min" thresholds,
lowering "max" thresholds, or dropping gates). We have outcome data
only for trades we actually took. Recommending LOOSER thresholds
requires phantom-trade data captured at every candle whether we entered
or not — a separate addition.

Output: markdown report + a Python code block with the proposed changes
that you can paste straight into SentimentOnchainStrategy.

Run from the host:
  docker compose run --rm ingest python -m ingest.scripts.propose_thresholds
  docker compose run --rm ingest python -m ingest.scripts.propose_thresholds --days 30 --min-trades 10
"""
import argparse
import asyncio
import json
import statistics
from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable, Optional

import asyncpg

from ..settings import settings


# Feature key in journal.features  ->  (strategy variable, gate type)
# gate type:
#   'min' = "feature >= threshold" — to tighten, raise the threshold
#   'max' = "feature <= threshold" — to tighten, lower the threshold
TUNABLES: tuple[tuple[str, str, str], ...] = (
    ("sentiment_z",     "SENTIMENT_Z_MIN",     "min"),
    ("sentiment_mean",  "SENTIMENT_MEAN_MIN",  "min"),
    ("sentiment_count", "SENTIMENT_COUNT_MIN", "min"),
    ("funding_rate",    "FUNDING_MAX_ENTER",   "max"),
    ("oi_z",            "OI_Z_MIN",            "min"),
    ("tvl_z",           "TVL_Z_MIN",           "min"),
    ("fng",             "FNG_MAX_ENTER",       "max"),
)


@dataclass
class Trade:
    feature: dict
    profit_abs: float
    profit_ratio: float


@dataclass
class Candidate:
    value: float
    trades_kept: int
    wins: int
    losses: int
    net_abs: float
    gross_win: float
    gross_loss: float

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades_kept if self.trades_kept else 0.0

    @property
    def profit_factor(self) -> Optional[float]:
        if self.gross_loss == 0:
            return None
        return self.gross_win / abs(self.gross_loss)


def _evaluate(trades: list[Trade], feature: str, gate: str, threshold: float) -> Candidate:
    kept: list[Trade] = []
    for t in trades:
        v = t.feature.get(feature)
        if v is None:
            continue
        if gate == "min" and v >= threshold:
            kept.append(t)
        elif gate == "max" and v <= threshold:
            kept.append(t)
    wins = sum(1 for t in kept if t.profit_abs > 0)
    losses = len(kept) - wins
    gross_win = sum(t.profit_abs for t in kept if t.profit_abs > 0)
    gross_loss = sum(t.profit_abs for t in kept if t.profit_abs < 0)
    return Candidate(
        value=threshold,
        trades_kept=len(kept),
        wins=wins,
        losses=losses,
        net_abs=gross_win + gross_loss,
        gross_win=gross_win,
        gross_loss=gross_loss,
    )


def _sweep_values(values: list[float], gate: str, percentiles: Iterable[int]) -> list[float]:
    """Return candidate threshold values at given percentiles of the feature
    distribution. Sweeping toward the "tighter" direction only."""
    if not values:
        return []
    sorted_v = sorted(values)
    n = len(sorted_v)
    out: list[float] = []
    for p in percentiles:
        idx = max(0, min(n - 1, int(round(p / 100 * (n - 1)))))
        out.append(sorted_v[idx])
    # Keep unique, ordered for the gate direction.
    out = sorted(set(out), reverse=(gate == "max"))
    return out


async def _load_trades(pool: asyncpg.Pool, days: int, enter_tag: Optional[str]) -> list[Trade]:
    where = "event='exit' AND occurred_at >= NOW() - $1::interval"
    params: list = [timedelta(days=days)]
    if enter_tag:
        where += " AND enter_tag = $2"
        params.append(enter_tag)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT features, profit_abs, profit_ratio FROM trade_journal WHERE {where}",
            *params,
        )
    out: list[Trade] = []
    for r in rows:
        f = r["features"]
        if isinstance(f, str):
            f = json.loads(f)
        if r["profit_abs"] is None:
            continue
        out.append(Trade(feature=f or {}, profit_abs=float(r["profit_abs"]),
                         profit_ratio=float(r["profit_ratio"] or 0)))
    return out


async def _current_thresholds(pool: asyncpg.Pool) -> dict:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT thresholds FROM trade_journal
            WHERE event='entry' AND thresholds IS NOT NULL
            ORDER BY occurred_at DESC LIMIT 1
            """
        )
    if row is None or row["thresholds"] is None:
        return {}
    t = row["thresholds"]
    return json.loads(t) if isinstance(t, str) else t


def _format_candidate(c: Candidate) -> str:
    pf = c.profit_factor
    pf_s = f"{pf:.2f}" if pf is not None else "∞"
    return (f"thr={c.value:.4g}  trades={c.trades_kept}  "
            f"wins={c.wins} losses={c.losses}  "
            f"win%={c.win_rate*100:.1f}  pf={pf_s}  net={c.net_abs:+.2f}")


def _propose_one(
    feature: str, var: str, gate: str,
    trades: list[Trade], min_trades: int, current: float | None,
) -> tuple[Candidate, list[Candidate]]:
    """Return (best candidate, all sweep candidates including baseline)."""
    values = [t.feature[feature] for t in trades if t.feature.get(feature) is not None]
    sweep = _sweep_values(values, gate, percentiles=(0, 10, 25, 40, 50, 60, 75, 90))
    if current is not None and current not in sweep:
        sweep.append(current)
        sweep = sorted(set(sweep), reverse=(gate == "max"))
    candidates = [_evaluate(trades, feature, gate, v) for v in sweep]
    valid = [c for c in candidates if c.trades_kept >= min_trades]
    # Best = highest net_abs amongst those with enough samples.
    best = max(valid, key=lambda c: c.net_abs) if valid else max(
        candidates, key=lambda c: c.net_abs
    )
    return best, candidates


async def main(days: int, min_trades: int, tag: Optional[str]) -> None:
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=2)
    try:
        trades = await _load_trades(pool, days, tag)
        current = await _current_thresholds(pool)
    finally:
        await pool.close()

    title_tag = f" (tag={tag})" if tag else ""
    out: list[str] = [f"# Threshold Proposer — last {days} days{title_tag}\n"]

    if not trades:
        out.append("**No closed trades in window.** Bot may still be in observation mode.\n")
        out.append("> Re-run after at least 10 trades have closed for a meaningful proposal.\n")
        print("\n".join(out))
        return

    out.append(f"Sample size: **{len(trades)} closed trades**.")
    out.append(f"Minimum sample per candidate: {min_trades}.\n")

    if len(trades) < min_trades * 2:
        out.append(
            f"> ⚠️ Sample size ({len(trades)}) is small. Recommendations may overfit; "
            f"prefer to wait until 30+ trades have closed before applying changes.\n"
        )

    out.append("## Per-threshold sweep\n")
    out.append("Each block shows: current value, best candidate (max net), full sweep.\n")

    proposed_changes: list[tuple[str, float | int, float | int]] = []

    for feature, var, gate in TUNABLES:
        cur = current.get(var)
        best, all_c = _propose_one(feature, var, gate, trades, min_trades, cur)
        out.append(f"### `{var}`  ({gate} gate on `{feature}`)")
        if cur is not None:
            cur_eval = _evaluate(trades, feature, gate, cur)
            out.append(f"- **Current** ({cur:.4g}): {_format_candidate(cur_eval)}")
        out.append(f"- **Proposed** ({best.value:.4g}): {_format_candidate(best)}")
        if cur is not None and best.value != cur:
            cur_net = _evaluate(trades, feature, gate, cur).net_abs
            delta = best.net_abs - cur_net
            if abs(delta) > 1e-6:
                out.append(f"- ΔNet: {delta:+.2f} USDT vs current")
                proposed_changes.append((var, cur, best.value))
        out.append("")
        out.append("Full sweep:")
        for c in all_c:
            marker = " ← best" if c is best else ""
            out.append(f"  - {_format_candidate(c)}{marker}")
        out.append("")

    if proposed_changes:
        out.append("## Suggested edits")
        out.append("Paste these into `SentimentOnchainStrategy.py` (or update the .env-driven values you've extracted):\n")
        out.append("```python")
        for var, cur, new in proposed_changes:
            out.append(f"{var} = {new:.4g}   # was {cur:.4g}")
        out.append("```\n")
        out.append(
            "> ⚠️ **Sanity-check before applying.** "
            "Tightening that improves *past* P&L can also reduce trade frequency too far. "
            "If a proposed change cuts the trade count by more than 50%, reject it.\n"
        )
    else:
        out.append("## No changes suggested\n")
        out.append("Either the current thresholds are already optimal for the historical sample, or the sample is too small to recommend confident changes.\n")

    out.append("---")
    out.append("*Limitations:*")
    out.append("- Only proposes tighter thresholds. Loosening recommendations require phantom-trade capture (planned).")
    out.append("- Optimizes against past P&L. Past performance ≠ future results, especially across regime changes.")
    out.append("- Treats each threshold independently. Joint optimization (e.g., `sentiment_z + funding_rate` together) is not modeled — apply changes one at a time.")
    print("\n".join(out))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30, help="Window in days")
    parser.add_argument("--min-trades", type=int, default=10,
                        help="Minimum trade samples for a candidate to be valid")
    parser.add_argument("--tag", choices=["strict", "momentum"], default=None,
                        help="Restrict analysis to a single entry tag")
    args = parser.parse_args()
    asyncio.run(main(args.days, args.min_trades, args.tag))
