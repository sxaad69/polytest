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
PAPER_TRADING = True       # global flag — True keeps everything on paper

# ── Per-bot paper/live mode ────────────────────────────────────────────────────
# Allows running Bot A live while Bot B stays on paper simultaneously
BOT_A_PAPER_TRADING = True
BOT_B_PAPER_TRADING = True
BOT_C_PAPER_TRADING = True
BOT_D_PAPER_TRADING = True
BOT_E_PAPER_TRADING = True
BOT_F_PAPER_TRADING = True
BOT_G_PAPER_TRADING = True

# ── Bot enable flags ───────────────────────────────────────────────────────────
BOT_A_ENABLED = True        # Chainlink lag
BOT_B_ENABLED = True        # Hybrid
BOT_C_ENABLED   = False
BOT_D_ENABLED   = False
BOT_E_ENABLED   = False
BOT_F_ENABLED   = False
BOT_G_ENABLED = True        # Crypto (Universal)

# ── Live conflict rule ─────────────────────────────────────────────────────────
LIVE_CONFLICT_RULE = "higher_confidence"

# ── Bankroll ───────────────────────────────────────────────────────────────────
BOT_A_BANKROLL = 100000.0  # DATA VOLCANO simulation
BOT_B_BANKROLL = 100000.0  # DATA VOLCANO simulation
BOT_C_BANKROLL = 100000.0
BOT_D_BANKROLL = 100000.0
BOT_E_BANKROLL = 100000.0
BOT_F_BANKROLL = 100000.0
BOT_G_BANKROLL = 100000.0  # DATA VOLCANO simulation
MAX_BET_PCT    = 0.05
KELLY_FRACTION = 0.25

# ── Shared signal thresholds ───────────────────────────────────────────────────
MIN_ODDS            = 0.001  # PAPER: very wide
MAX_ODDS            = 0.999  # PAPER: very wide
MIN_BOOK_DEPTH      = 0.0    # LIVE: 50.0  # 0 to allow untested tokens to pass depth gate
NO_ENTRY_LAST_SECS  = 0      # LIVE: 180   # don't enter if <180s remaining
NO_ENTRY_FIRST_SECS = 0      # LIVE: 60    # don't enter in first 60s
WINDOW_DURATION     = 300
BOT_C_NO_ENTRY_LAST_SECS = 0  # LIVE: 30    # arbs lock in profit immediately

# ── Bot A thresholds (Chainlink lag) ───────────────────────────────────────────
# Data showed edge at 0.20-0.40% deviation band
# Above 0.40% = crowd already priced in, win rate drops to 0%
# Below 0.20% = noise, no directional signal
BOT_A_MIN_DEVIATION    = 0.001  # PAPER: hyper-sensitive (was 0.02)
BOT_A_MAX_DEVIATION    = 0.60   # LIVE: 0.40
BOT_A_MIN_SUSTAIN_SECS = 1      # LIVE: 5      # fast trigger — catch signal early in window
BOT_A_MIN_CONFIDENCE   = 0.02   # LIVE: 0.20   # paper mode — gather data

# ── Bot B thresholds (Hybrid) ──────────────────────────────────────────────────
BOT_B_MIN_CONFIDENCE = 0.02     # LIVE: 0.20
BOT_B_SIGNAL_WEIGHTS = {
    "momentum":      0.40,
    "rsi":           0.24,
    "volume":        0.18,
    "odds_velocity": 0.18,
}
BOT_B_LAG_BOOST  = 0.25   # strong reward when lag confirms direction
BOT_B_LAG_DAMPEN = 0.60   # strong penalty when lag contradicts

# ── Bot C thresholds (GLOB Arb) ────────────────────────────────────────────────
# Threshold is sum of YES + NO vwap. < 1.0 is a theoretical arb.
# Setting to 1.02 for PAPER testing (will trade at slight loss to test flow)
ARB_THRESHOLD     = 1.02   # Entry when (Yes_Ask + No_Ask) <= 1.05 (Guarantees negative spread triggers for testing)
BOT_C_MARKET_PATTERNS = ["*"] # Pattern list to scan for arbitrage targets

# ── Bot D thresholds (Sports Spike) ────────────────────────────────────────────
BOT_D_SPIKE_THRESHOLD   = 0.005  # PAPER: 0.5% move (was 5%) velocity spike to trigger
BOT_D_FADE_ENABLED      = True   # True = fade spikes (mean reversion)
BOT_D_MARKET_PATTERNS   = [      # Sports slug patterns to scan
    "will-*-win-*",
    "*nfl*", "*nba*", "*mlb*", "*nhl*", "*mls*",
    "*soccer*", "*epl*", "*ufc*", "*tennis*",
    "*cs2*", "*dota2*", "*hok*", "*valorant*", "*lol*", "*fc25*",
    "*val-*", "*cs2-*", "*dota2-*", "*lol-*", # short slugs
    "*cbb*", "*ncaa*", # NCAA College Basketball
]

# ── Bot E thresholds (Momentum) ────────────────────────────────────────────────
BOT_E_MIN_VELOCITY = 0.0001# LIVE: 0.015 # Minimum 30s velocity delta to trigger entry
BOT_E_MARKET_PATTERNS = [
    "*president-of-*", "*prime-minister-of-*", "*ceasefire*", "*war-*", "*israel*", "*ukraine*",
    "*will-*-be-*"
]

# ── Bot F thresholds (Copytrade) ────────────────────────────────────────────────
BOT_F_ACCURACY_THRESHOLD = 0.01  # LIVE: 0.65  # Slug must resolve correctly 65%+ of time
BOT_F_MIN_SAMPLES        = 0     # LIVE: 20    # Minimum historical resolutions required
BOT_F_MARKET_PATTERNS    = [
    "*-market-cap-*", "*-fdv-*", "*-one-day-after-*", "*-launch-*",
    "*grammy*", "*oscar*", "*academy-award*", "*awards*", "*next-*-to-*"
]

# ── Bot G thresholds (Crypto) ──────────────────────────────────────────────────
BOT_G_STRIKE_ASSETS = ["btc", "eth", "sol", "bnb", "xrp", "doge"]
BOT_G_TIMEFRAMES = {
    "5m": 300,   # 5m ONLY — data shows 0% win rate on 15m/4h
}
# Signal momentum band (from 811-trade simulation, March 2026)
BOT_G_MIN_CONFIDENCE          = 0.035   # Floor — below this is noise (22% win rate)
BOT_G_MOMENTUM_CEILING        = 0.100   # Ceiling — above this is overextended (0% win rate)

# Entry filters (restored from Data Volcano mode)
BOT_G_MIN_ENTRY_ODDS          = 0.30    # Sweet spot: 0.30–0.70 (data-driven)
BOT_G_MAX_ENTRY_ODDS          = 0.70
BOT_G_MAX_ENTRY_SECS_INTO_WIN = 120     # Don't enter after 2 min into window (0% win rate after 180s)
BOT_G_MIN_SECS_REMAINING      = 60      # Don't enter if < 60s left in window

# Position management
BOT_G_MAX_CONCURRENT_TRADES   = 8       # Controlled aggression (was 999 in volcano mode)
BOT_G_MIN_STAKE               = 1.0     # Minimum stake in USDC

# ── Global Exclude patterns ────────────────────────────────────────────────────
# Noise Purge: These keywords will trigger a clinical skip for any bot scanning markets.
GLOBAL_EXCLUDE_KEYWORDS = [
    "above", "below", "up-or-down", "updown",
    "bitcoin", "ethereum", "bnb", "solana", "dogecoin", "xrp", "hype",
    "btc", "eth", "sol", "doge", # shorthand common in slugs
    "-up-", "-down-", # catch internal price action markers
]

# ── Global Portfolio Risk ──────────────────────────────────────────────────────
GLOBAL_MAX_EXPOSURE_PCT = 0.30   # Max 30% of total bankroll in flight at once
GLOBAL_DAILY_LOSS_LIMIT = 0.20   # 20% across all bots triggers global sleep mode
GLOBAL_HALT_DURATION_HOURS = 6.0 # How long to freeze the bots after max loss
GLOBAL_DAILY_PROFIT_TARGET = 0.10 # +10% target to shut down safely (Realized)
GLOBAL_UNREALIZED_PROFIT_TARGET = 0.20 # +20% spike target to panic sell & lock (Unrealized)

# ── Circuit breaker ────────────────────────────────────────────────────────────
CIRCUIT_BREAKER_ENABLED = False   # paper mode — flip True for live
MAX_CONSECUTIVE_LOSSES  = 5
DAILY_LOSS_LIMIT_PCT    = 0.15

# ── Position management ────────────────────────────────────────────────────────
GLOBAL_MIN_TRADE_SIZE = 5.0     # 0 = use pure Kelly, otherwise strict floor
GLOBAL_MAX_TRADE_SIZE = 10.0    # 0 = use pure Kelly, otherwise strict ceiling

# Data-driven changes:
# Exit parameters — tuned from live simulation data (March 23, 2026)
# At 45% win rate, need TP/SL ratio > 1.22 to be profitable
# Today: TP avg +$2,623 | SL avg -$2,346 → ratio 1.12 (slightly negative EV)
# Fix: tighten TP so more trades reach it, widen SL to reduce premature stops
TAKE_PROFIT_DELTA     = 0.08    # Tightened from 0.10 — more trades hit TP
STOP_LOSS_DELTA       = 0.12    # Widened from 0.08 — gives trade room to breathe
TRAILING_STOP_ENABLED = False   # disabled: 0% win rate in v1/v2/v3
TRAILING_STOP_DELTA   = 0.20    # kept for reference only — not active
HARD_STOP_SECONDS     = 15      # Last resort only — exit before binary settlement
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

# ── Logging & Monitoring ───────────────────────────────────────────────────────
LOG_LEVEL = "INFO"
WRITE_SCANNED_MARKETS_TXT = True   # Overwrites logs/bot_X_markets.txt with actively monitored slugs


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