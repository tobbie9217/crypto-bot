"""
Audit bot — Telegram interface for querying the trade journal.

Uses a SECOND Telegram bot (not freqtrade's) because Telegram only
allows one polling client per bot at a time. Reads the AUDIT_BOT_TOKEN
env var; routes replies back to the chat_id of whoever DMs `/start`.
Only the configured FREQTRADE__TELEGRAM__CHAT_ID is authorized — every
other chat is ignored silently.

Commands: /audit /why /recent /vetoes /stats /help
"""
import asyncio
import json
import logging
import os
import signal
import statistics
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import asyncpg
import httpx
import structlog


def _configure_logging() -> None:
    logging.basicConfig(level="INFO")
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ]
    )


log = structlog.get_logger()


HEARTBEAT_PATH = "/tmp/healthz"
HEARTBEAT_INTERVAL_S = 30


async def _heartbeat() -> None:
    """Touch HEARTBEAT_PATH every HEARTBEAT_INTERVAL_S seconds for the
    docker healthcheck. Same pattern as ingest/sentiment."""
    while True:
        try:
            with open(HEARTBEAT_PATH, "w") as f:
                f.write(str(time.time()))
        except OSError as e:
            log.warning("heartbeat_write_failed", error=str(e))
        await asyncio.sleep(HEARTBEAT_INTERVAL_S)


# ----------------- Telegram I/O -----------------

# Slash-menu (the one that appears when you type `/` in the chat).
# Registered with Telegram once at startup via setMyCommands.
SLASH_COMMANDS = [
    {"command": "audit",  "description": "Full audit report (default last 14 days)"},
    {"command": "stats",  "description": "Quick headline numbers (default 14 days)"},
    {"command": "why",    "description": "Last trades for a pair, e.g. /why BTC/USDT"},
    {"command": "recent", "description": "Last N events (default 10)"},
    {"command": "vetoes", "description": "Soft-veto reason tally (default 14 days)"},
    {"command": "help",   "description": "Show available commands"},
]

# Persistent quick-tap keyboard at the bottom of the chat. Attached
# to every outbound message so it never disappears. Only the
# parameter-less commands get buttons — /why needs a pair argument
# and is left to typing or the slash menu.
QUICK_KEYBOARD = {
    "keyboard": [
        [{"text": "/audit"},  {"text": "/stats"}],
        [{"text": "/recent"}, {"text": "/vetoes"}],
        [{"text": "/help"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}


class TelegramClient:
    def __init__(self, token: str) -> None:
        self.token = token
        self.base = f"https://api.telegram.org/bot{token}"
        self.session = httpx.AsyncClient(timeout=60)
        self._offset = 0

    async def close(self) -> None:
        await self.session.aclose()

    async def register_commands(self) -> None:
        """Tell Telegram about our slash commands so the `/` autocomplete
        menu shows them with descriptions. One-shot at startup."""
        try:
            resp = await self.session.post(
                f"{self.base}/setMyCommands",
                json={"commands": SLASH_COMMANDS},
            )
            if resp.status_code >= 400:
                log.warning("telegram_setmycommands_failed",
                            status=resp.status_code, body=resp.text[:300])
        except Exception as e:  # noqa: BLE001
            log.warning("telegram_setmycommands_exception", error=str(e))

    async def get_updates(self) -> list[dict]:
        try:
            resp = await self.session.get(
                f"{self.base}/getUpdates",
                params={"offset": self._offset, "timeout": 30, "allowed_updates": '["message"]'},
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:  # noqa: BLE001
            log.warning("telegram_getupdates_failed", error=str(e))
            await asyncio.sleep(5)
            return []
        if not payload.get("ok"):
            log.warning("telegram_getupdates_not_ok", payload=payload)
            return []
        results = payload.get("result", [])
        if results:
            self._offset = max(r["update_id"] for r in results) + 1
        return results

    async def send(self, chat_id: int, text: str) -> None:
        # Telegram max message length is 4096; chunk long replies.
        # The quick-tap keyboard is attached to the LAST chunk only —
        # Telegram replaces the keyboard with whatever was on the most
        # recent message, so attaching to every chunk wastes payload
        # without changing behaviour.
        chunks = _chunk(text, 3800)
        last_idx = len(chunks) - 1
        for i, chunk in enumerate(chunks):
            payload = {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            }
            if i == last_idx:
                payload["reply_markup"] = QUICK_KEYBOARD
            try:
                resp = await self.session.post(
                    f"{self.base}/sendMessage",
                    json=payload,
                )
            except Exception as e:  # noqa: BLE001
                log.warning("telegram_send_failed", error=str(e), chunk_idx=i)
                continue
            if resp.status_code == 400:
                # Markdown parse failed — retry as plain text so the user sees something.
                log.warning("telegram_send_markdown_rejected",
                            chunk_idx=i, body=resp.text[:300])
                fallback_payload = {
                    "chat_id": chat_id,
                    "text": chunk,
                    "disable_web_page_preview": True,
                }
                if i == last_idx:
                    fallback_payload["reply_markup"] = QUICK_KEYBOARD
                try:
                    fallback = await self.session.post(
                        f"{self.base}/sendMessage",
                        json=fallback_payload,
                    )
                    if fallback.status_code >= 400:
                        log.error("telegram_send_fallback_failed",
                                  status=fallback.status_code,
                                  body=fallback.text[:300])
                except Exception as e:  # noqa: BLE001
                    log.error("telegram_send_fallback_exception", error=str(e))
            elif resp.status_code >= 400:
                log.error("telegram_send_failed_status",
                          status=resp.status_code, body=resp.text[:300])


def _chunk(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    out: list[str] = []
    while text:
        if len(text) <= limit:
            out.append(text)
            break
        # Try to break on newline near the limit.
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        out.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return out


# ----------------- Formatting helpers -----------------

def _fmt_pct(x: Optional[float]) -> str:
    if x is None:
        return "—"
    return f"{x*100:+.2f}%"


def _fmt_dur(seconds: Optional[Any]) -> str:
    if seconds is None:
        return "—"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    return f"{s // 3600}h{(s % 3600) // 60}m"


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _coerce_features(raw: Any) -> dict:
    if not raw:
        return {}
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw


# ----------------- Command handlers -----------------

HELP_TEXT = (
    "*Crypto-bot audit*\n"
    "Available commands:\n"
    "• `/audit [days]` — full audit report (default 14)\n"
    "• `/stats [days]` — quick headline numbers only\n"
    "• `/why <pair>` — last trade reasoning, e.g. `/why BTC/USDT`\n"
    "• `/recent [n]` — last N events (default 10)\n"
    "• `/vetoes [days]` — soft-veto reason tally (default 14)\n"
    "• `/help` — this menu\n"
    "\n"
    "Bot fires trade alerts to your other (@Seifawa\\_bot) chat. Use this "
    "bot for on-demand queries."
)


async def cmd_help(_pool: asyncpg.Pool, _args: list[str]) -> str:
    return HELP_TEXT


async def cmd_start(_pool: asyncpg.Pool, _args: list[str]) -> str:
    return (
        "👋 *Audit bot ready.*\n\nSee `/help` for queries you can ask.\n"
        "Trade alerts go to your freqtrade-bot chat in real time."
    )


def _parse_days(args: list[str], default: int) -> int:
    if not args:
        return default
    try:
        n = int(args[0])
        return max(1, min(365, n))
    except ValueError:
        return default


async def cmd_stats(pool: asyncpg.Pool, args: list[str]) -> str:
    days = _parse_days(args, 14)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT COUNT(*) AS trades,
                   COUNT(*) FILTER (WHERE profit_ratio > 0)  AS wins,
                   COALESCE(SUM(profit_abs), 0)              AS net_abs,
                   COALESCE(SUM(profit_abs) FILTER (WHERE profit_abs > 0), 0)  AS gw,
                   COALESCE(SUM(profit_abs) FILTER (WHERE profit_abs < 0), 0)  AS gl,
                   AVG(duration_seconds)                              AS avg_dur
            FROM trade_journal
            WHERE event='exit' AND occurred_at >= $1
            """, since,
        )
    if not row or row["trades"] == 0:
        return f"📊 *Stats — last {days} days*\nNo closed trades yet."
    trades = row["trades"]
    wins = row["wins"]
    win_rate = wins / trades * 100
    pf = (row["gw"] / abs(row["gl"])) if row["gl"] != 0 else None
    pf_s = f"{pf:.2f}" if pf is not None else "∞"
    return (
        f"📊 *Stats — last {days} days*\n"
        f"Trades: *{trades}*  ({wins} win / {trades - wins} loss)\n"
        f"Win rate: *{win_rate:.1f}%*\n"
        f"Net P&L: *{row['net_abs']:+.2f} USDT*\n"
        f"Profit factor: *{pf_s}*\n"
        f"Avg duration: {_fmt_dur(row['avg_dur'])}"
    )


async def cmd_audit(pool: asyncpg.Pool, args: list[str]) -> str:
    days = _parse_days(args, 14)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    async with pool.acquire() as conn:
        head = await conn.fetchrow(
            """
            SELECT COUNT(*) AS trades,
                   COUNT(*) FILTER (WHERE profit_ratio > 0) AS wins,
                   COUNT(*) FILTER (WHERE profit_ratio <= 0) AS losses,
                   COALESCE(SUM(profit_abs), 0) AS net,
                   COALESCE(SUM(profit_abs) FILTER (WHERE profit_abs > 0), 0) AS gw,
                   COALESCE(SUM(profit_abs) FILTER (WHERE profit_abs < 0), 0) AS gl,
                   AVG(profit_ratio) FILTER (WHERE profit_ratio > 0) AS avgwin,
                   AVG(profit_ratio) FILTER (WHERE profit_ratio <= 0) AS avgloss,
                   MIN(profit_ratio) AS worst, MAX(profit_ratio) AS best,
                   AVG(duration_seconds) AS avg_dur
            FROM trade_journal
            WHERE event='exit' AND occurred_at >= $1
            """, since,
        )
        tags = await conn.fetch(
            """
            SELECT enter_tag, COUNT(*) AS n,
                   COUNT(*) FILTER (WHERE profit_ratio > 0) AS wins,
                   COALESCE(SUM(profit_abs), 0) AS net,
                   AVG(profit_ratio) AS avg_r
            FROM trade_journal
            WHERE event='exit' AND occurred_at >= $1
            GROUP BY enter_tag ORDER BY n DESC
            """, since,
        )
        reasons = await conn.fetch(
            """
            SELECT exit_reason, COUNT(*) AS n, AVG(profit_ratio) AS avg_r
            FROM trade_journal
            WHERE event='exit' AND occurred_at >= $1
            GROUP BY exit_reason ORDER BY n DESC
            """, since,
        )

    if not head or head["trades"] == 0:
        return f"📋 *Audit — last {days} days*\nNo closed trades yet."

    win_rate = head["wins"] / head["trades"] * 100
    pf = (head["gw"] / abs(head["gl"])) if head["gl"] != 0 else None
    pf_s = f"{pf:.2f}" if pf is not None else "∞"
    out = [f"📋 *Audit — last {days} days*\n"]
    out.append(
        f"*Headline*\n"
        f"Trades: {head['trades']}  (W/L: {head['wins']}/{head['losses']})\n"
        f"Win rate: {win_rate:.1f}%   PF: {pf_s}\n"
        f"Net: {head['net']:+.2f} USDT\n"
        f"Avg win: {_fmt_pct(head['avgwin'])}   "
        f"Avg loss: {_fmt_pct(head['avgloss'])}\n"
        f"Best: {_fmt_pct(head['best'])}   "
        f"Worst: {_fmt_pct(head['worst'])}\n"
        f"Avg duration: {_fmt_dur(head['avg_dur'])}\n"
    )
    if tags:
        out.append("*By tag*")
        for t in tags:
            wr = t["wins"] / t["n"] * 100 if t["n"] else 0
            out.append(
                f"  • {t['enter_tag']}: {t['n']} trades, {wr:.0f}% wins, "
                f"avg {_fmt_pct(t['avg_r'])}, net {t['net']:+.2f}"
            )
        out.append("")
    if reasons:
        out.append("*Exit reasons*")
        for r in reasons:
            out.append(f"  • `{r['exit_reason']}`: {r['n']}  ({_fmt_pct(r['avg_r'])})")
    return "\n".join(out)


async def cmd_why(pool: asyncpg.Pool, args: list[str]) -> str:
    if not args:
        return "Usage: `/why BTC/USDT`"
    pair = args[0].upper()
    if "/" not in pair:
        pair = f"{pair}/USDT"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT event, enter_tag, exit_reason, rate, profit_ratio,
                   duration_seconds, max_profit_ratio, min_profit_ratio,
                   features, occurred_at
            FROM trade_journal
            WHERE pair = $1
            ORDER BY occurred_at DESC LIMIT 6
            """, pair,
        )
    if not rows:
        return f"No journal entries for `{pair}` yet."

    out = [f"🔎 *Recent activity — `{pair}`*\n"]
    for r in rows:
        ev = r["event"]
        ts = _ts(r["occurred_at"])
        emoji = {"entry": "🎯", "exit": "🚪", "vetoed": "🛑",
                 "attempted_entry": "👀"}.get(ev, "•")
        if ev == "exit":
            out.append(
                f"{emoji} {ts}  *EXIT*  ({r['enter_tag']})  "
                f"{_fmt_pct(r['profit_ratio'])}  in {_fmt_dur(r['duration_seconds'])}\n"
                f"   reason: `{r['exit_reason']}`   "
                f"peak {_fmt_pct(r['max_profit_ratio'])}  "
                f"trough {_fmt_pct(r['min_profit_ratio'])}"
            )
        elif ev == "entry":
            f = _coerce_features(r["features"])
            rsi_5m = f.get("rsi")
            rsi_1h = f.get("rsi_1h")
            pc1h = f.get("price_change_1h")
            vr = f.get("volume_ratio")
            extras: list[str] = []
            if rsi_5m is not None:
                extras.append(f"RSI {rsi_5m:.1f} (1h {rsi_1h:.1f})" if rsi_1h is not None else f"RSI {rsi_5m:.1f}")
            if pc1h is not None and vr is not None:
                extras.append(f"+{pc1h:.1f}% 1h, {vr:.1f}× vol")
            out.append(
                f"{emoji} {ts}  *ENTRY*  ({r['enter_tag']})  @ `{r['rate']:.6g}`\n"
                f"   {'  '.join(extras) if extras else ''}"
            )
        elif ev == "vetoed":
            out.append(
                f"{emoji} {ts}  *VETO*  ({r['enter_tag']})  "
                f"@ `{r['rate']:.6g}`\n"
                f"   reason: `{r['exit_reason']}`"
            )
        elif ev == "attempted_entry":
            out.append(
                f"{emoji} {ts}  attempt  ({r['enter_tag']})  @ `{r['rate']:.6g}`"
            )
    return "\n".join(out)


async def cmd_recent(pool: asyncpg.Pool, args: list[str]) -> str:
    try:
        n = int(args[0]) if args else 10
    except ValueError:
        n = 10
    n = max(1, min(50, n))
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT event, pair, enter_tag, exit_reason,
                   profit_ratio, duration_seconds, occurred_at
            FROM trade_journal
            WHERE event IN ('entry','exit','vetoed')
            ORDER BY occurred_at DESC LIMIT $1
            """, n,
        )
    if not rows:
        return "No journal events yet."
    out = [f"🕐 *Last {len(rows)} events*\n"]
    for r in rows:
        ev = r["event"]
        ts = r["occurred_at"].strftime("%m-%d %H:%M")
        emoji = {"entry": "🎯", "exit": "🚪", "vetoed": "🛑"}.get(ev, "•")
        if ev == "exit":
            out.append(
                f"{emoji} {ts} `{r['pair']}` {r['enter_tag']} "
                f"{_fmt_pct(r['profit_ratio'])} ({_fmt_dur(r['duration_seconds'])})  "
                f"`{r['exit_reason']}`"
            )
        elif ev == "vetoed":
            out.append(
                f"{emoji} {ts} `{r['pair']}` {r['enter_tag']}  veto: `{r['exit_reason']}`"
            )
        else:
            out.append(f"{emoji} {ts} `{r['pair']}` {r['enter_tag']}")
    return "\n".join(out)


async def cmd_vetoes(pool: asyncpg.Pool, args: list[str]) -> str:
    days = _parse_days(args, 14)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT exit_reason, COUNT(*) AS n,
                   ARRAY_AGG(pair ORDER BY occurred_at DESC) AS pairs
            FROM trade_journal
            WHERE event='vetoed' AND occurred_at >= $1
            GROUP BY exit_reason ORDER BY n DESC
            """, since,
        )
    if not rows:
        return f"🛑 *Vetoes — last {days} days*\nNo trades vetoed."
    out = [f"🛑 *Vetoes — last {days} days*\n"]
    for r in rows:
        pairs = r["pairs"][:5]
        more = f" (+{len(r['pairs']) - 5} more)" if len(r["pairs"]) > 5 else ""
        out.append(f"*{r['n']}× `{r['exit_reason']}`*")
        out.append(f"   pairs: {', '.join(pairs)}{more}\n")
    return "\n".join(out)


COMMANDS = {
    "/start":   cmd_start,
    "/help":    cmd_help,
    "/audit":   cmd_audit,
    "/stats":   cmd_stats,
    "/why":     cmd_why,
    "/recent":  cmd_recent,
    "/vetoes":  cmd_vetoes,
}


# ----------------- Main loop -----------------

async def handle_update(
    update: dict, allowed_chat_id: int, tg: TelegramClient, pool: asyncpg.Pool
) -> None:
    msg = update.get("message")
    if not msg:
        return
    chat_id = msg.get("chat", {}).get("id")
    text = (msg.get("text") or "").strip()
    if not text:
        return
    if chat_id != allowed_chat_id:
        log.info("unauthorized_chat", chat_id=chat_id, text=text[:40])
        return

    # Split command and args. Telegram appends "@botname" sometimes.
    parts = text.split()
    cmd = parts[0].split("@", 1)[0].lower()
    args = parts[1:]

    handler = COMMANDS.get(cmd)
    if handler is None:
        await tg.send(chat_id, f"Unknown command `{cmd}`. Try `/help`.")
        return
    try:
        reply = await handler(pool, args)
    except Exception as e:  # noqa: BLE001
        log.error("handler_failed", cmd=cmd, error=str(e))
        reply = f"⚠️ Error running `{cmd}`: `{e}`"
    await tg.send(chat_id, reply)


async def main() -> None:
    _configure_logging()
    token = os.environ.get("AUDIT_BOT_TOKEN")
    chat_id_raw = os.environ.get("FREQTRADE__TELEGRAM__CHAT_ID")
    db_url = os.environ.get("DATABASE_URL")
    if not token or not chat_id_raw or not db_url:
        log.error("missing_config",
                  have_token=bool(token), have_chat_id=bool(chat_id_raw),
                  have_db=bool(db_url))
        return
    allowed_chat_id = int(chat_id_raw)

    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
    tg = TelegramClient(token)
    log.info("audit_bot_starting", chat_id=allowed_chat_id)

    # Register slash commands with Telegram so the `/` autocomplete
    # menu in the chat shows all available commands with descriptions.
    # One-shot at startup — Telegram remembers the list per-bot.
    await tg.register_commands()

    # SIGTERM/SIGINT → set the stop event so the poll loop can exit
    # within a heartbeat instead of waiting up to 30s for the current
    # Telegram long-poll to time out.
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    stop_wait_task = asyncio.create_task(stop_event.wait())
    heartbeat_task = asyncio.create_task(_heartbeat())

    try:
        while not stop_event.is_set():
            poll_task = asyncio.create_task(tg.get_updates())
            await asyncio.wait(
                {poll_task, stop_wait_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stop_event.is_set():
                poll_task.cancel()
                try:
                    await poll_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
                break
            try:
                updates = poll_task.result()
            except Exception as e:  # noqa: BLE001
                log.warning("telegram_poll_failed", error=str(e))
                updates = []
            for u in updates:
                await handle_update(u, allowed_chat_id, tg, pool)
    finally:
        log.info("audit_bot_shutdown")
        for task in (stop_wait_task, heartbeat_task):
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        await tg.close()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
