"""
Polymarket Dual-Bot — Config v2
Thresholds set from paper trading data analysis (v1 loose run).

Key findings from v1 data:
  - Hard stops are the main loss driver — NO_ENTRY_LAST_SECS=150 fixes this
  - Bot A edge exists only at 0.45-0.52% deviation band
  - Bot B profitable up to 0.65 odds, loses above that
  - Trailing stop too tight at 0.10 — widened to 0.15
  - Best trading hours UTC: 8,11,12,13,17,19 (added tomorrow after confirmation)
"""

import os
import pathlib
from dotenv import load_dotenv

load_dotenv()

# ── Mode ───────────────────────────────────────────────────────────────────────
PAPER_TRADING = True        # flip to False when going live

# ── Bot enable flags ───────────────────────────────────────────────────────────
BOT_A_ENABLED = True
BOT_B_ENABLED = True

# ── Live conflict rule ─────────────────────────────────────────────────────────
LIVE_CONFLICT_RULE = "higher_confidence"

# ── Bankroll ───────────────────────────────────────────────────────────────────
BOT_A_BANKROLL = 100.0
BOT_B_BANKROLL = 100.0
MAX_BET_PCT    = 0.05
KELLY_FRACTION = 0.25

# ── Shared signal thresholds ───────────────────────────────────────────────────
# v1 loose → v2 data-driven
MIN_ODDS           = 0.30    # restored (was 0.05)
MAX_ODDS           = 0.65    # tightened (was 0.95) — 0.6+ bucket underperforms
MIN_BOOK_DEPTH     = 50.0    # restored (was 0.0)
NO_ENTRY_LAST_SECS = 150     # key fix (was 0) — eliminates hard stops
WINDOW_DURATION    = 300

# ── Bot A thresholds (Chainlink lag) ───────────────────────────────────────────
# v1 data showed edge only at 0.45-0.52% deviation band
# Below 0.45% = noise (39% win rate)
# Above 0.52% = crowd already priced in (35% win rate)
# 0.45-0.50% band = 64.8% win rate, +$17.27
BOT_A_MIN_DEVIATION    = 0.45   # restored (was 0.10)
BOT_A_MIN_SUSTAIN_SECS = 10     # restored (was 2)
BOT_A_MIN_CONFIDENCE   = 0.45   # slightly loosened from original 0.50

# ── Bot B thresholds (Hybrid) ──────────────────────────────────────────────────
# v1 data: 67.8% win rate excluding hard stops, +$107.92
# Confidence at 0.20 — moderate, not too tight yet
BOT_B_MIN_CONFIDENCE = 0.20    # was 0.10 loose, was 0.62 original
BOT_B_SIGNAL_WEIGHTS = {
    "momentum":      0.40,
    "rsi":           0.24,
    "volume":        0.18,
    "odds_velocity": 0.18,
}
BOT_B_LAG_BOOST  = 0.15
BOT_B_LAG_DAMPEN = 0.70

# ── Circuit breaker ────────────────────────────────────────────────────────────
CIRCUIT_BREAKER_ENABLED = False   # paper mode — flip True for live
MAX_CONSECUTIVE_LOSSES  = 5
DAILY_LOSS_LIMIT_PCT    = 0.15

# ── Position management ────────────────────────────────────────────────────────
TAKE_PROFIT_DELTA   = 0.18   # unchanged — working well
TRAILING_STOP_DELTA = 0.15   # widened from 0.10 — give winners more room
HARD_STOP_SECONDS   = 30     # unchanged
POSITION_POLL_SECS  = 3

# ── TODO after tomorrow's data ─────────────────────────────────────────────────
# Add trading hours filter if pattern confirms:
# Best hours UTC: 8, 11, 12, 13, 17, 19
# Avoid UTC: 0, 1, 2, 15, 18, 23
# TRADING_HOURS_UTC = [8, 11, 12, 13, 17, 19]
#
# Add Bot A max deviation cap:
# BOT_A_MAX_DEVIATION = 0.52  (above this, crowd already priced it in)

# ── Chainlink ──────────────────────────────────────────────────────────────────
CHAINLINK_RPC_URL   = os.getenv("ALCHEMY_RPC_URL", "")
CHAINLINK_BTC_FEED  = "0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c"
CHAINLINK_POLL_SECS = 5

# ── Binance / Coinbase ─────────────────────────────────────────────────────────
BINANCE_WS_URL = "wss://stream.binance.com:9443/ws/btcusdt@trade"

# ── Polymarket ─────────────────────────────────────────────────────────────────
POLYMARKET_CLOB_URL  = "https://clob.polymarket.com"
POLYMARKET_GAMMA_URL = "https://gamma-api.polymarket.com"

# Live trading credentials (empty during paper testing)
POLYMARKET_PRIVATE_KEY    = os.getenv("POLYMARKET_PRIVATE_KEY", "")
POLYMARKET_FUNDER_ADDRESS = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
POLYMARKET_API_KEY        = os.getenv("POLYMARKET_API_KEY", "")
POLYMARKET_API_SECRET     = os.getenv("POLYMARKET_API_SECRET", "")
POLYMARKET_PASSPHRASE     = os.getenv("POLYMARKET_PASSPHRASE", "")

# ── Database ───────────────────────────────────────────────────────────────────
# v2 fresh databases — v1 data preserved as *_v1_loose.db
_DATA_DIR = pathlib.Path(__file__).parent / "data"
_DATA_DIR.mkdir(exist_ok=True)

BOT_A_DB_PATH = str(_DATA_DIR / "bot_a_paper.db")
BOT_B_DB_PATH = str(_DATA_DIR / "bot_b_paper.db")

# ── Logging ────────────────────────────────────────────────────────────────────
LOG_LEVEL = "INFO"


# ── Startup validation ─────────────────────────────────────────────────────────
def validate():
    errors = []
    if not CHAINLINK_RPC_URL:
        errors.append(
            "ALCHEMY_RPC_URL is not set in .env\n"
            "  → https://dashboard.alchemy.com → Create App → Ethereum Mainnet"
        )
    if not PAPER_TRADING:
        for name, val in [
            ("POLYMARKET_PRIVATE_KEY",    POLYMARKET_PRIVATE_KEY),
            ("POLYMARKET_FUNDER_ADDRESS", POLYMARKET_FUNDER_ADDRESS),
            ("POLYMARKET_API_KEY",        POLYMARKET_API_KEY),
            ("POLYMARKET_API_SECRET",     POLYMARKET_API_SECRET),
            ("POLYMARKET_PASSPHRASE",     POLYMARKET_PASSPHRASE),
        ]:
            if not val:
                errors.append(f"{name} is not set in .env (required for live trading)")
    if errors:
        print("\n❌ Config errors:\n")
        for e in errors:
            print(f"  • {e}")
        print()
        raise SystemExit(1)