# Polymarket Dual Bot

Two independent BTC 5-minute Up/Down trading bots running in parallel.
Paper tested first, then compared, then one goes live.

---

## Architecture

```
main.py  (Orchestrator)
│
├── Shared feeds (one connection each)
│   ├── BinanceFeed     → real-time BTC price, RSI, momentum, volume (WebSocket)
│   ├── ChainlinkFeed   → onchain BTC/USD price + lag detection (Alchemy JSON-RPC)
│   └── PolymarketFeed  → live odds, order book, market discovery, orders
│
├── Bot A — Chainlink lag only
│   ├── signals/signal_a.py   → pure lag score
│   ├── data/bot_a_paper.db   → independent SQLite log
│   └── independent bankroll + circuit breaker
│
└── Bot B — Hybrid
    ├── signals/signal_b.py   → momentum + RSI + volume + odds velocity
    ├── data/bot_b_paper.db   → independent SQLite log
    └── independent bankroll + circuit breaker
```

---

## Strategy summary

### Bot A — Chainlink lag arbitrage
Chainlink's BTC/USD feed only updates onchain when price moves ≥0.5% or
on a ~1 hour heartbeat. When Binance is already 0.45%+ above Chainlink
for 10+ seconds, a Chainlink update is imminent. Polymarket odds haven't
fully priced it in yet — that's the edge.

**This is not prediction. It is information asymmetry.**

### Bot B — Hybrid
Combines four signals: momentum (40%), RSI (24%), volume z-score (18%),
odds velocity (18%). Chainlink lag amplifies (+15%) when it confirms
direction, dampens (×0.70) when it contradicts. Bot B trades without lag.

---

## Quick start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Setup folders (only needed once)
python setup_structure.py

# 3. Configure
cp .env.example .env
# Edit .env — paste your ALCHEMY_RPC_URL

# 4. Health check
python test_bot.py

# 5. Run
python main.py
```

---

## Config reference (`config.py`)

| Setting | Paper value | Live value |
|---------|-------------|------------|
| `PAPER_TRADING` | `True` | `False` |
| `CIRCUIT_BREAKER_ENABLED` | `False` | `True` |
| `BOT_A_ENABLED` | `True` | Your choice |
| `BOT_B_ENABLED` | `True` | Your choice |
| `LIVE_CONFLICT_RULE` | n/a | See below |

### Circuit breaker
- `CIRCUIT_BREAKER_ENABLED = False` — paper mode, bot keeps running through loss streaks to gather data
- `CIRCUIT_BREAKER_ENABLED = True` — live mode, halts after `MAX_CONSECUTIVE_LOSSES` or `DAILY_LOSS_LIMIT_PCT`

---

## Turning bots on/off

```python
# config.py
BOT_A_ENABLED = True    # Chainlink lag only
BOT_B_ENABLED = False   # set False to disable
```

---

## Paper testing — what to track

Run for minimum **7 days, 200+ trades per bot**.

Compare results any time:
```bash
python -m analytics.comparison
```

SQL queries on either database:
```sql
-- Overall performance
SELECT outcome, COUNT(*), ROUND(AVG(pnl_usdc),5) AS avg_pnl
FROM trades WHERE resolved=1 GROUP BY outcome;

-- By exit type
SELECT exit_reason, COUNT(*), ROUND(AVG(pnl_usdc),5)
FROM trades WHERE resolved=1 GROUP BY exit_reason;

-- Chainlink lag trades only
SELECT t.outcome, COUNT(*), ROUND(AVG(t.pnl_usdc),5)
FROM trades t JOIN signals s ON t.signal_id=s.id
WHERE s.chainlink_lag_flag=1 AND t.resolved=1
GROUP BY t.outcome;

-- Skip reasons (filter quality)
SELECT reason, COUNT(*) FROM skipped
GROUP BY reason ORDER BY COUNT(*) DESC;
```

Minimum to go live per bot:
- Win rate > 52%
- Positive expectancy after fees
- 200+ trades
- Both long AND short win rates positive

---

## Going live

### Step 1 — Run comparison
```bash
python -m analytics.comparison
```
The report gives an automated verdict and recommends which bot to go live with.

### Step 2 — Set conflict rule (if running both live)
```python
# config.py
LIVE_CONFLICT_RULE = "higher_confidence"
# Options:
# "higher_confidence" → whichever bot has stronger score executes
# "bot_a_priority"    → Bot A always wins when both signal
# "bot_b_priority"    → Bot B always wins when both signal
# "no_trade"          → skip if both want same window (most conservative)
```

### Step 3 — Configure credentials
```python
# config.py
PAPER_TRADING              = False
CIRCUIT_BREAKER_ENABLED    = True
```
```env
# .env
POLYMARKET_PRIVATE_KEY=your_key_without_0x
POLYMARKET_FUNDER_ADDRESS=0x...your_polymarket_profile_address
POLYMARKET_API_KEY=...
POLYMARKET_API_SECRET=...
POLYMARKET_PASSPHRASE=...
```

### Step 4 — Start small
First live week: set `BOT_A_BANKROLL = 20.0` and `BOT_B_BANKROLL = 20.0`.
Scale up only after confirming live performance matches paper.

---

## AWS deployment

### First time
```bash
ssh -i your-key.pem ubuntu@your-ec2-ip
git clone https://github.com/YOUR_USERNAME/polymarket-bot.git polymarket
cd polymarket
bash setup_aws.sh
```

### Every future update
```bash
ssh ubuntu@your-ec2-ip
cd polymarket
./deploy.sh
```

### Monitoring
```bash
sudo journalctl -u polymarket-bot -f                    # live logs
sudo journalctl -u polymarket-bot --since today          # today only
sudo journalctl -u polymarket-bot -f | grep -E "ACTIVE|Waiting|ENTER|EXIT|HALTED"
sudo systemctl status polymarket-bot                     # running?
```

---

## API keys

| Key | Where | Required for |
|-----|-------|--------------|
| **Alchemy RPC URL** | dashboard.alchemy.com → Create App → Ethereum Mainnet | Paper + live |
| **Polymarket private key** | reveal.magic.link/polymarket | Live only |
| **Polymarket funder address** | Your Polymarket profile page | Live only |
| **Polymarket API key/secret/passphrase** | polymarket.com → Profile → API Keys | Live only |

---

## File structure

```
├── main.py                    Orchestrator
├── config.py                  All parameters + CIRCUIT_BREAKER_ENABLED flag
├── test_bot.py                Health check — run before main.py
├── setup_structure.py         One-time folder setup
├── requirements.txt
├── .env.example               Copy to .env and fill in
├── .gitignore
├── polymarket-bot.service     Systemd unit file for AWS
├── setup_aws.sh               First-time AWS setup script
├── deploy.sh                  Pull latest + restart on AWS
├── data/                      SQLite databases (auto-created, git-ignored)
│   ├── bot_a_paper.db
│   └── bot_b_paper.db
├── bots/
│   ├── base_bot.py            Shared loop + heartbeat logging
│   ├── bot_a.py               Bot A entry point
│   └── bot_b.py               Bot B entry point
├── signals/
│   ├── signal_a.py            Chainlink lag signal
│   └── signal_b.py            Hybrid signal
├── feeds/
│   ├── binance_ws.py          Price + RSI + momentum + volume
│   ├── chainlink.py           Onchain price + lag detection (raw JSON-RPC)
│   └── polymarket.py          Odds + order book + market discovery + orders
├── risk/
│   └── manager.py             Filters + circuit breaker + Kelly sizing
├── execution/
│   └── trader.py              Entry + trailing stop + TP + hard stop
├── analytics/
│   └── comparison.py          7-day side-by-side report + go-live verdict
└── database/
    └── db.py                  SQLite schema + all read/write operations
```
