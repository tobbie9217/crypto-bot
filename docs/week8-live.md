# Week 8 — Live trading

**Read this whole document before you flip the switch.**

You're about to put real money behind code you wrote. Drawdowns hurt
more, bugs cost more, and the emotional pressure to override the bot
will be real. Setting up correctly is half the battle.

## Pre-flight checklist

- [ ] 30+ days of testnet paper-trade with profit factor >= 1.5 and
      drawdown < 10%
- [ ] You can articulate what your strategy does **and why** in one
      paragraph, without looking at the code
- [ ] You've watched at least one full red-day cycle in paper-trade
      and the protections kicked in correctly
- [ ] You have funds you can afford to lose entirely
- [ ] You're emotionally prepared for a -20% week

If any box is unchecked, go back to week 6.

## Create the Binance API key

1. Sign in to https://www.binance.com (real account).
2. **Account -> API Management -> Create API**.
3. Choose **System generated** -> name it `crypto-bot-trade`.
4. **Restrictions** — set ALL of these:
   - [x] Enable **Spot & Margin Trading**
   - [ ] Enable Withdrawals — **OFF** (critical)
   - [ ] Enable Internal Transfer — OFF
   - [ ] Permits Universal Transfer — OFF
5. **IP access restrictions** — set **Restrict access to trusted IPs only**
   and add your VPS / dev machine's public IP.
6. Save the API Key + Secret to a password manager. **You will not see
   the secret again.**

## Configure for live

`.env`:
```
FREQTRADE__EXCHANGE__KEY=your_real_api_key
FREQTRADE__EXCHANGE__SECRET=your_real_secret
```

`user_data/config.json` — three changes:

```json
"max_open_trades": 2,           // start with 2, not 3
"dry_run": false,               // <-- this is the live switch
"dry_run_wallet": 1000,         // ignored when dry_run: false, but keep set
"exchange": {
    "name": "binance",
    "sandbox": false,           // <-- and this
    ...
}
```

**Start with $100-500 of real funds.** Transfer USDT from spot to spot —
do not deposit fresh fiat with the bot running.

Tighten protections one more notch for live:
```json
"protections": [
    {"method": "MaxDrawdown", "max_allowed_drawdown": 0.03, ...},   // 3% not 5%
    {"method": "StoplossGuard", "trade_limit": 2, ...},              // 2 not 4
    ...
]
```

## Deploy to a VPS

Local Docker Desktop is fine for dev but not for live:
- Your laptop sleeps -> bot misses entries/exits
- Power outage -> open positions with no manager
- Latency to Binance via residential ISP is 100-300ms

Move to a VPS in week 8 day 1:

| Provider | Recommended tier | Notes |
|----------|------------------|-------|
| Hetzner | CX22 (~€5/mo) | EU latency to Binance is good |
| DigitalOcean | Basic Droplet $12/mo | More flexible regions |
| AWS Lightsail | $10/mo | Worth it if you're already in AWS |

Steps:
1. Spin up Ubuntu 24.04 LTS, 2GB RAM minimum.
2. Install Docker + docker compose plugin.
3. `git clone` your repo (push it to a private GitHub first).
4. Copy `.env` over via `scp` — never commit it.
5. `docker compose up -d`.
6. **Whitelist this VPS's IP on Binance API** (above).
7. Verify Telegram /status responds from the VPS.

## Hardening

- 2FA on: Binance, Telegram, GitHub, VPS provider.
- SSH key auth only on the VPS — disable password login.
- `ufw` allow 22 (SSH) only. No 8080 — keep FreqUI internal.
- Use Tailscale or an SSH tunnel to reach FreqUI from your laptop.
- `unattended-upgrades` for security patches.
- Postgres data on a separate volume so you can snapshot it.

## Day 1-7 live

- Check `/status` in Telegram twice a day. That's it.
- Do **NOT** override trades manually. The whole point of the bot is
  emotional discipline you don't have at 3am.
- If you feel the urge to intervene, that's data: log what you wanted to
  do and what happened. Compare with the bot's outcome at end of week.

## When to pull the plug

Stop the bot (`/stop` or `docker compose stop freqtrade`) immediately if:
- Drawdown exceeds 10% in any rolling 7-day window.
- Any trade behaves unexpectedly (wrong direction, wrong size, stuck open).
- You see API errors in `docker compose logs freqtrade` you don't understand.
- Your Binance account flags anything (security alert, geo-restriction).

A pause + post-mortem is always cheaper than letting an unknown bug eat
through capital.

## Scaling (months 3-6)

Only scale if month 1-2 shows positive expectancy with tolerable
drawdown. The right way to scale is more **capital**, not more pairs or
more leverage. Doubling stake from $500 to $1000 is one knob.
Adding 20 more pairs is twenty knobs that all need re-validation.

## Legal note

Trading for yourself is fine in most jurisdictions. Trading for friends
or charging fees triggers regulatory requirements (investment-advisor
registration). Stay personal-use unless you're ready for that overhead.
