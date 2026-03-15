"""
Polymarket Dual-Bot — Config
All tunable parameters in one place.

Bot A — Pure Chainlink lag arbitrage
Bot B — Hybrid: momentum + odds velocity + lag as boost
"""

import os
import pathlib
from dotenv import load_dotenv

load_dotenv()   # reads .env from project root

# ── Mode ───────────────────────────────────────────────────────────────────────
PAPER_TRADING = True        # Always True until paper test passes. See README.

# ── Bot enable flags ───────────────────────────────────────────────────────────
BOT_A_ENABLED = True        # Chainlink lag only
BOT_B_ENABLED = True        # Hybrid strategy

# ── Live conflict rule (only applies when PAPER_TRADING = False) ───────────────
# "higher_confidence" → whichever bot has stronger score executes
# "bot_a_priority"    → Bot A always wins when both signal
# "bot_b_priority"    → Bot B always wins when both signal
# "no_trade"          → skip if both bots want the same window
LIVE_CONFLICT_RULE = "higher_confidence"

# ── Bankroll ───────────────────────────────────────────────────────────────────
BOT_A_BANKROLL = 100.0
BOT_B_BANKROLL = 100.0
MAX_BET_PCT    = 0.05       # Max 5% of bankroll per trade
KELLY_FRACTION = 0.25       # Quarter Kelly

# ── Shared signal thresholds ───────────────────────────────────────────────────
MIN_ODDS           = 0.30
MAX_ODDS           = 0.70
MIN_BOOK_DEPTH     = 50.0
NO_ENTRY_LAST_SECS = 60
WINDOW_DURATION    = 300

# ── Bot A thresholds (Chainlink lag only) ──────────────────────────────────────
BOT_A_MIN_DEVIATION    = 0.45
BOT_A_MIN_SUSTAIN_SECS = 10
BOT_A_MIN_CONFIDENCE   = 0.50

# ── Bot B thresholds (Hybrid) ──────────────────────────────────────────────────
BOT_B_MIN_CONFIDENCE = 0.62
BOT_B_SIGNAL_WEIGHTS = {
    "momentum":      0.40,
    "rsi":           0.24,
    "volume":        0.18,
    "odds_velocity": 0.18,
}
BOT_B_LAG_BOOST  = 0.15
BOT_B_LAG_DAMPEN = 0.70

# ── Circuit breaker ────────────────────────────────────────────────────────────
# Set False during paper testing if you want the bot to keep running
# regardless of loss streaks — useful for gathering data without interruption.
# Set True for live trading — this is your financial safety net.
CIRCUIT_BREAKER_ENABLED = False      # False = paper testing, True = live
MAX_CONSECUTIVE_LOSSES  = 5          # halt after this many losses in a row
DAILY_LOSS_LIMIT_PCT    = 0.15       # halt if daily loss exceeds 15% of bankroll

# ── Position management ────────────────────────────────────────────────────────
TAKE_PROFIT_DELTA   = 0.18   # exit when odds rise 18¢ above entry
TRAILING_STOP_DELTA = 0.10   # trail 10¢ below peak odds
HARD_STOP_SECONDS   = 30     # always exit 30s before window closes
POSITION_POLL_SECS  = 3      # position monitor interval

# ── Chainlink ──────────────────────────────────────────────────────────────────
CHAINLINK_RPC_URL   = os.getenv("ALCHEMY_RPC_URL", "")
CHAINLINK_BTC_FEED  = "0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c"
CHAINLINK_POLL_SECS = 5

# ── Binance ────────────────────────────────────────────────────────────────────
BINANCE_WS_URL = "wss://stream.binance.com:9443/ws/btcusdt@trade"

# ── Polymarket ─────────────────────────────────────────────────────────────────
POLYMARKET_CLOB_URL  = "https://clob.polymarket.com"
POLYMARKET_GAMMA_URL = "https://gamma-api.polymarket.com"

# Live trading credentials (all empty during paper testing)
POLYMARKET_PRIVATE_KEY    = os.getenv("POLYMARKET_PRIVATE_KEY", "")
POLYMARKET_FUNDER_ADDRESS = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
POLYMARKET_API_KEY        = os.getenv("POLYMARKET_API_KEY", "")
POLYMARKET_API_SECRET     = os.getenv("POLYMARKET_API_SECRET", "")
POLYMARKET_PASSPHRASE     = os.getenv("POLYMARKET_PASSPHRASE", "")

# ── Database — stored in ./data/ (created automatically) ──────────────────────
_DATA_DIR = pathlib.Path(__file__).parent / "data"
_DATA_DIR.mkdir(exist_ok=True)

BOT_A_DB_PATH = str(_DATA_DIR / "bot_a_paper.db")
BOT_B_DB_PATH = str(_DATA_DIR / "bot_b_paper.db")

# ── Logging ────────────────────────────────────────────────────────────────────
LOG_LEVEL = "INFO"
# Logs go to stdout → captured by systemd journald on AWS
# View with: sudo journalctl -u polymarket-bot -f


# ── Startup validation ─────────────────────────────────────────────────────────
def validate():
    errors = []
    if not CHAINLINK_RPC_URL:
        errors.append(
            "ALCHEMY_RPC_URL is not set in .env\n"
            "  → Get it at: https://dashboard.alchemy.com\n"
            "  → Create App → Ethereum Mainnet → Copy HTTPS URL"
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
        print("\n❌ Config errors — please fix before running:\n")
        for e in errors:
            print(f"  • {e}")
        print()
        raise SystemExit(1)
