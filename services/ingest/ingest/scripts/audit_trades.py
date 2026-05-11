"""
Trade audit — statistical report from the trade_journal table.

Run from the host:
  docker compose run --rm ingest python -m ingest.scripts.audit_trades --days 14

What you get (markdown to stdout):
  * Headline P&L, win rate, profit factor, drawdown
  * Per-tag breakdown (strict vs momentum)
  * Per-coin top winners + losers
  * Exit-reason histogram
  * Feature-correlation table — mean signal value at winners vs losers,
    so you can see *which gates actually predict outcomes*
  * Hourly trade frequency
  * Recent trades table
"""
import argparse
import asyncio
import json
import statistics
from datetime import datetime, timezone, timedelta

import asyncpg

from ..settings import settings


# ----------------- Queries -----------------

_HEADLINE_SQL = """
SELECT COUNT(*) AS trades,
       COUNT(*) FILTER (WHERE profit_ratio > 0)  AS wins,
       COUNT(*) FILTER (WHERE profit_ratio <= 0) AS losses,
       COALESCE(SUM(profit_abs), 0)              AS net_abs,
       COALESCE(SUM(profit_abs) FILTER (WHERE profit_abs > 0), 0)  AS gross_win,
       COALESCE(SUM(profit_abs) FILTER (WHERE profit_abs < 0), 0)  AS gross_loss,
       AVG(profit_ratio) FILTER (WHERE profit_ratio > 0)  AS avg_win_r,
       AVG(profit_ratio) FILTER (WHERE profit_ratio <= 0) AS avg_loss_r,
       AVG(duration_seconds)                              AS avg_duration_s,
       MIN(profit_ratio) AS worst_r,
       MAX(profit_ratio) AS best_r,
       MIN(min_profit_ratio) AS worst_intra
FROM trade_journal
WHERE event='exit' AND occurred_at >= $1;
"""

_TAG_SQL = """
SELECT enter_tag,
       COUNT(*) AS trades,
       COUNT(*) FILTER (WHERE profit_ratio > 0)  AS wins,
       AVG(profit_ratio) AS avg_r,
       SUM(profit_abs)   AS net_abs,
       AVG(duration_seconds) AS avg_dur_s
FROM trade_journal
WHERE event='exit' AND occurred_at >= $1
GROUP BY enter_tag
ORDER BY trades DESC;
"""

_COIN_SQL = """
SELECT coin,
       COUNT(*) AS trades,
       SUM(profit_abs) AS net_abs,
       AVG(profit_ratio) AS avg_r
FROM trade_journal
WHERE event='exit' AND occurred_at >= $1
GROUP BY coin
ORDER BY net_abs DESC;
"""

_REASON_SQL = """
SELECT exit_reason, COUNT(*) AS n,
       AVG(profit_ratio) AS avg_r
FROM trade_journal
WHERE event='exit' AND occurred_at >= $1
GROUP BY exit_reason
ORDER BY n DESC;
"""

# Mean of each numeric feature, split by win/loss.
_FEATURE_KEYS = (
    "sentiment_z", "sentiment_mean", "sentiment_count",
    "funding_rate", "oi_z", "tvl_z", "fng",
    "rsi", "rsi_1h", "price_change_1h", "volume_ratio",
)


_FEATURE_SQL = """
SELECT
    profit_ratio > 0 AS won,
    {projections}
FROM trade_journal
WHERE event='exit' AND occurred_at >= $1
"""


_RECENT_SQL = """
SELECT pair, enter_tag, exit_reason,
       ROUND((profit_ratio*100)::numeric, 2) AS pct,
       duration_seconds,
       occurred_at
FROM trade_journal
WHERE event='exit' AND occurred_at >= $1
ORDER BY occurred_at DESC
LIMIT 25;
"""


# ----------------- Helpers -----------------

def _fmt_pct(x: float | None) -> str:
    if x is None:
        return "—"
    return f"{x*100:+.2f}%"


def _fmt_dur(s: float | None) -> str:
    if s is None:
        return "—"
    s = int(s)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s//60}m"
    return f"{s//3600}h{(s%3600)//60}m"


def _profit_factor(gross_win: float, gross_loss: float) -> str:
    if gross_loss is None or gross_loss == 0:
        return "∞" if (gross_win or 0) > 0 else "—"
    return f"{(gross_win or 0) / abs(gross_loss):.2f}"


# ----------------- Report -----------------

async def _report(pool: asyncpg.Pool, days: int) -> str:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    out: list[str] = []
    out.append(f"# Trade Audit — last {days} days  ({since.isoformat()})\n")

    async with pool.acquire() as conn:
        # Headline
        h = await conn.fetchrow(_HEADLINE_SQL, since)
        if h is None or h["trades"] == 0:
            out.append("**No trades in window.** Bot may still be in observation mode.\n")
            return "\n".join(out)

        win_rate = h["wins"] / h["trades"] if h["trades"] else 0
        out.append("## Headline\n")
        out.append(f"- Trades: **{h['trades']}**  (wins {h['wins']} / losses {h['losses']})")
        out.append(f"- Win rate: **{win_rate*100:.1f}%**")
        out.append(f"- Net P&L: **{h['net_abs']:+.2f} USDT**")
        out.append(f"- Profit factor: **{_profit_factor(h['gross_win'], h['gross_loss'])}**")
        out.append(f"- Avg win: {_fmt_pct(h['avg_win_r'])}    Avg loss: {_fmt_pct(h['avg_loss_r'])}")
        out.append(f"- Best trade: {_fmt_pct(h['best_r'])}    Worst trade: {_fmt_pct(h['worst_r'])}")
        out.append(f"- Worst intra-trade drawdown: {_fmt_pct(h['worst_intra'])}")
        out.append(f"- Avg duration: {_fmt_dur(h['avg_duration_s'])}")
        out.append("")

        # Per tag
        tags = await conn.fetch(_TAG_SQL, since)
        if tags:
            out.append("## By entry tag\n")
            out.append("| Tag | Trades | Wins | Win % | Avg ratio | Net USDT | Avg duration |")
            out.append("|---|---:|---:|---:|---:|---:|---:|")
            for t in tags:
                wr = t["wins"] / t["trades"] if t["trades"] else 0
                out.append(
                    f"| {t['enter_tag']} | {t['trades']} | {t['wins']} | "
                    f"{wr*100:.1f}% | {_fmt_pct(t['avg_r'])} | "
                    f"{(t['net_abs'] or 0):+.2f} | {_fmt_dur(t['avg_dur_s'])} |"
                )
            out.append("")

        # Exit reasons
        reasons = await conn.fetch(_REASON_SQL, since)
        if reasons:
            out.append("## Exit reasons\n")
            out.append("| Reason | Count | Avg ratio |")
            out.append("|---|---:|---:|")
            for r in reasons:
                out.append(f"| {r['exit_reason']} | {r['n']} | {_fmt_pct(r['avg_r'])} |")
            out.append("")

        # Per coin
        coins = await conn.fetch(_COIN_SQL, since)
        if coins:
            out.append("## By coin (top 5 + bottom 5 by net P&L)\n")
            out.append("| Coin | Trades | Net USDT | Avg ratio |")
            out.append("|---|---:|---:|---:|")
            top = list(coins[:5])
            bot = [c for c in coins[-5:] if c not in top]
            for c in top + bot:
                out.append(
                    f"| {c['coin']} | {c['trades']} | "
                    f"{(c['net_abs'] or 0):+.2f} | {_fmt_pct(c['avg_r'])} |"
                )
            out.append("")

        # Feature-correlation: mean of each numeric feature at winners vs losers.
        # Done in Python after pulling rows so we can handle JSONB casts safely.
        rows = await conn.fetch(
            """
            SELECT profit_ratio > 0 AS won, features
            FROM trade_journal
            WHERE event='exit' AND occurred_at >= $1
            """,
            since,
        )
        if rows:
            buckets: dict[str, dict[bool, list[float]]] = {
                k: {True: [], False: []} for k in _FEATURE_KEYS
            }
            for r in rows:
                f = r["features"] or {}
                if isinstance(f, str):
                    f = json.loads(f)
                won = bool(r["won"])
                for k in _FEATURE_KEYS:
                    v = f.get(k)
                    if isinstance(v, (int, float)):
                        buckets[k][won].append(float(v))
            out.append("## Feature distribution: winners vs losers\n")
            out.append("| Feature | Wins (mean) | Losses (mean) | Δ |")
            out.append("|---|---:|---:|---:|")
            for k in _FEATURE_KEYS:
                w = buckets[k][True]
                l = buckets[k][False]
                w_mean = statistics.mean(w) if w else None
                l_mean = statistics.mean(l) if l else None
                if w_mean is None and l_mean is None:
                    continue
                delta = (w_mean - l_mean) if (w_mean is not None and l_mean is not None) else None
                w_s = f"{w_mean:.3f}" if w_mean is not None else "—"
                l_s = f"{l_mean:.3f}" if l_mean is not None else "—"
                d_s = f"{delta:+.3f}" if delta is not None else "—"
                out.append(f"| {k} | {w_s} | {l_s} | {d_s} |")
            out.append("")
            out.append("> Δ = winner mean − loser mean. Big positive = signal predicts wins.\n")

        # Recent trades
        recent = await conn.fetch(_RECENT_SQL, since)
        if recent:
            out.append("## Recent trades (last 25)\n")
            out.append("| Pair | Tag | Exit | P&L | Duration | When |")
            out.append("|---|---|---|---:|---:|---|")
            for r in recent:
                out.append(
                    f"| {r['pair']} | {r['enter_tag']} | {r['exit_reason']} | "
                    f"{r['pct']}% | {_fmt_dur(r['duration_seconds'])} | "
                    f"{r['occurred_at'].strftime('%Y-%m-%d %H:%M')} |"
                )
            out.append("")

    return "\n".join(out)


async def main(days: int) -> None:
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=2)
    try:
        report = await _report(pool, days)
        print(report)
    finally:
        await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=14, help="Window in days")
    args = parser.parse_args()
    asyncio.run(main(args.days))
