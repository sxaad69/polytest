"""
Polymarket Dual-Bot — Config v4
All settings derived from paper trading data analysis.

Data summary (3000+ trades across v1/v2/v3):
  ✓ Middle 3 minutes (60-240s elapsed) = 67-68% win rate
  ✓ First 60s = unstable odds, avoid
  ✓ Last 60s = odds decided, avoid
  ✓ Trailing stop = 0% win rate across ALL versions — disabled
  ✓ Hard stop at 30s too late — moved to 60s for better exit price
  ✓ Take profit delta 0.18 too small — increased to 0.22
  ✓ Bot A break-even = 48% (wide margin), Bot B break-even = 64% (tight)
  ✓ Bot A payout ratio better: +$0.71 TP vs -$0.66 HS
  ✓ Bot B needs TAKE_PROFIT_DELTA increase to improve payout ratio
  ✓ Chainlink lag edge at 0.20-0.40% deviation (confirmed)
  ✓ Above 0.40% = crowd already priced in
"""

import os
import pathlib
from dotenv import load_dotenv

load_dotenv()

# ── Mode ───────────────────────────────────────────────────────────────────────
PAPER_TRADING = False       # global flag — False enables live trading

# ── Per-bot paper/live mode ────────────────────────────────────────────────────
# Allows running Bot A live while Bot B stays on paper simultaneously
BOT_A_PAPER_TRADING = False  # Bot A live
BOT_B_PAPER_TRADING = True   # Bot B paper
BOT_C_PAPER_TRADING = True   # Bot C paper
BOT_E_PAPER_TRADING = True   # Bot E paper

# ── Bot enable flags ───────────────────────────────────────────────────────────
BOT_A_ENABLED = True        # Chainlink lag
BOT_B_ENABLED = False       # Hybrid
BOT_C_ENABLED = True        # Arbitrage
BOT_D_ENABLED = False       # Sports
BOT_E_ENABLED = True        # Momentum
BOT_F_ENABLED = False       # Copytrade
BOT_G_ENABLED = False       # Crypto

# ── Live conflict rule ─────────────────────────────────────────────────────────
LIVE_CONFLICT_RULE = "higher_confidence"

# ── Bankroll ───────────────────────────────────────────────────────────────────
BOT_A_BANKROLL = 100.0
BOT_B_BANKROLL = 100.0
BOT_C_BANKROLL = 100.0
BOT_D_BANKROLL = 100.0
BOT_E_BANKROLL = 100.0
BOT_F_BANKROLL = 100.0
BOT_G_BANKROLL = 100.0
MAX_BET_PCT    = 0.05
KELLY_FRACTION = 0.25

# ── Shared signal thresholds ───────────────────────────────────────────────────
MIN_ODDS            = 0.30
MAX_ODDS            = 0.65
MIN_BOOK_DEPTH      = 50.0
NO_ENTRY_LAST_SECS  = 180   # don't enter if <180s remaining (data confirmed)
NO_ENTRY_FIRST_SECS = 60    # don't enter in first 60s (odds not formed)
WINDOW_DURATION     = 300
BOT_C_NO_ENTRY_LAST_SECS = 30  # arbs lock in profit immediately

# ── Bot A thresholds (Chainlink lag) ───────────────────────────────────────────
# Data showed edge at 0.20-0.40% deviation band
# Above 0.40% = crowd already priced in, win rate drops to 0%
# Below 0.20% = noise, no directional signal
BOT_A_MIN_DEVIATION    = 0.20   # confirmed lower bound
BOT_A_MAX_DEVIATION    = 0.40   # confirmed upper bound — above this loses money
BOT_A_MIN_SUSTAIN_SECS = 5      # fast trigger — catch signal early in window
BOT_A_MIN_CONFIDENCE   = 0.20   # paper mode — gather data

# ── Bot B thresholds (Hybrid) ──────────────────────────────────────────────────
BOT_B_MIN_CONFIDENCE = 0.20
BOT_B_SIGNAL_WEIGHTS = {
    "momentum":      0.40,
    "rsi":           0.24,
    "volume":        0.18,
    "odds_velocity": 0.18,
}
BOT_B_LAG_BOOST  = 0.25   # strong reward when lag confirms direction
BOT_B_LAG_DAMPEN = 0.60   # strong penalty when lag contradicts

# ── Bot C thresholds (GLOB Arb) ────────────────────────────────────────────────
ARB_THRESHOLD = 0.985      # Entry when (Yes_Ask + No_Ask) <= 0.985
BOT_C_MARKET_PATTERN = "*" # Pattern to scan

# ── Circuit breaker ────────────────────────────────────────────────────────────
CIRCUIT_BREAKER_ENABLED = False   # paper mode — flip True for live
MAX_CONSECUTIVE_LOSSES  = 5
DAILY_LOSS_LIMIT_PCT    = 0.15

# ── Position management ────────────────────────────────────────────────────────
# Data-driven changes:
#   TAKE_PROFIT_DELTA: 0.18→0.22 — bigger wins improve payout ratio
#   HARD_STOP_SECONDS: 30→60 — earlier exit = better odds price on losses
#   TRAILING_STOP: disabled — 0% win rate confirmed across all versions
TAKE_PROFIT_DELTA     = 0.22    # increased from 0.18 — lowers break-even to ~57%
TRAILING_STOP_ENABLED = False   # disabled: 0% win rate in v1/v2/v3
TRAILING_STOP_DELTA   = 0.20    # kept for reference only — not active
HARD_STOP_SECONDS     = 60      # increased from 30 — better exit price
POSITION_POLL_SECS    = 3

# ── RPC Endpoints ──────────────────────────────────────────────────────────────
CHAINLINK_RPC_URL  = os.getenv("ALCHEMY_RPC_URL", "")  # Ethereum Mainnet
POLYGON_RPC_URL    = os.getenv("POLYGON_RPC_URL", "https://polygon-mainnet.g.alchemy.com/v2/YOUR_KEY")
CHAINLINK_BTC_FEED = "0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c"
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
_DATA_DIR = pathlib.Path(__file__).parent / "data"
_DATA_DIR.mkdir(exist_ok=True)

BOT_A_DB_PATH = str(_DATA_DIR / "bot_a_paper.db")
BOT_B_DB_PATH = str(_DATA_DIR / "bot_b_paper.db")
BOT_C_DB_PATH = str(_DATA_DIR / "bot_c_paper.db")
BOT_D_DB_PATH = str(_DATA_DIR / "bot_d_paper.db")
BOT_E_DB_PATH = str(_DATA_DIR / "bot_e_paper.db")
BOT_F_DB_PATH = str(_DATA_DIR / "bot_f_paper.db")
BOT_G_DB_PATH = str(_DATA_DIR / "bot_g_paper.db")

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
    # Check live credentials if any bot is going live
    if not BOT_A_PAPER_TRADING or not BOT_B_PAPER_TRADING:
        for name, val in [
            ("POLYMARKET_PRIVATE_KEY",    POLYMARKET_PRIVATE_KEY),
            ("POLYMARKET_FUNDER_ADDRESS", POLYMARKET_FUNDER_ADDRESS),
            ("POLYMARKET_API_KEY",        POLYMARKET_API_KEY),
            ("POLYMARKET_API_SECRET",     POLYMARKET_API_SECRET),
            ("POLYMARKET_PASSPHRASE",     POLYMARKET_PASSPHRASE),
        ]:
            if not val:
                errors.append(
                    f"{name} is not set in .env (required for live trading)"
                )
    if errors:
        print("\n❌ Config errors:\n")
        for e in errors:
            print(f"  • {e}")
        print()
        raise SystemExit(1)