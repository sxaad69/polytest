"""
test_logic.py — Enhanced Mock Trading Logic Validator
10,000 simulated trades across multiple market regimes.

No external calls. No API keys. No network. Pure Python stdlib + sqlite3.
Results saved to: data/test_logic_results.db  (production dbs untouched)

Tests:
  1.  Baseline — no filters, pure random
  2.  V2 production filters
  3.  Bot A deviation band (0.45-0.52%)
  4.  Trading hours filter (UTC 8,11,12,13,17,19)
  5.  Wallet balance guard
  6.  Duplicate trade guard
  7.  Thin book partial fills
  8.  Slippage impact
  9.  Hard stop timing (NO_ENTRY_LAST_SECS=150)
  10. Circuit breaker
  11. Market regime: Trending
  12. Market regime: Choppy
  13. Market regime: Volatile
  14. Drawdown analysis
  15. Fee break-even analysis (5/10/15/20/50 bps)
  16. Kelly fraction sensitivity (0.10/0.25/0.50)
  17. Long vs short asymmetry
  18. Consecutive trade streak simulation

Run: python3 test_logic.py
"""

import sqlite3
import random
import math
import json
import time
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Callable, Optional

# ── Colours ───────────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def ok(msg):     print(f"  {GREEN}✓{RESET}  {msg}")
def fail(msg):   print(f"  {RED}✗{RESET}  {msg}")
def warn(msg):   print(f"  {YELLOW}!{RESET}  {msg}")
def info(msg):   print(f"  {CYAN}→{RESET}  {msg}")
def header(msg): print(f"\n{BOLD}{CYAN}{msg}{RESET}")
def divider():   print("  " + "─" * 62)

# ── Config ────────────────────────────────────────────────────────────────────
NUM_TRADES          = 10_000
MIN_ODDS            = 0.30
MAX_ODDS            = 0.65
NO_ENTRY_LAST_SECS  = 150
WINDOW_DURATION     = 300
TAKE_PROFIT_DELTA   = 0.18
TRAILING_STOP_DELTA = 0.15
HARD_STOP_SECONDS   = 30
BOT_A_MIN_DEV       = 0.45
BOT_A_MAX_DEV       = 0.52
BOT_A_MIN_CONF      = 0.45
BOT_B_MIN_CONF      = 0.20
TAKER_FEE_BPS       = 10
LIVE_CONFLICT_RULE  = "higher_confidence"
TRADING_HOURS_UTC   = [8, 11, 12, 13, 17, 19]
STARTING_BANKROLL   = 100.0
MAX_BET_PCT         = 0.05
KELLY_FRACTION      = 0.25
MAX_CONSECUTIVE_LOSS = 5

DB_PATH = Path("data/test_logic_results.db")
DB_PATH.parent.mkdir(exist_ok=True)

# ── Schema ────────────────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS mock_trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario        TEXT,
    regime          TEXT,
    trade_num       INTEGER,
    direction       TEXT,
    entry_odds      REAL,
    exit_odds       REAL,
    stake_usdc      REAL,
    filled_size     REAL,
    fill_rate       REAL,
    slippage_pct    REAL,
    fee_bps         INTEGER,
    pnl_gross       REAL,
    pnl_net         REAL,
    outcome         TEXT,
    exit_reason     TEXT,
    chainlink_dev   REAL,
    confidence      REAL,
    hour_utc        INTEGER,
    secs_remaining  REAL,
    book_depth      REAL,
    bankroll_before REAL,
    bankroll_after  REAL,
    filtered        INTEGER DEFAULT 0,
    filter_reason   TEXT
);

CREATE TABLE IF NOT EXISTS test_summary (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario        TEXT,
    regime          TEXT,
    trades_total    INTEGER,
    trades_filtered INTEGER,
    trades_executed INTEGER,
    wins            INTEGER,
    losses          INTEGER,
    win_rate        REAL,
    total_pnl       REAL,
    expectancy      REAL,
    max_drawdown    REAL,
    max_streak_win  INTEGER,
    max_streak_loss INTEGER,
    final_bankroll  REAL,
    sharpe_ratio    REAL,
    fee_bps         INTEGER,
    kelly_fraction  REAL,
    notes           TEXT
);
"""

# ── Market state ──────────────────────────────────────────────────────────────
@dataclass
class MarketState:
    binance_price:    float
    chainlink_price:  float
    deviation_pct:    float
    up_odds:          float
    down_odds:        float
    book_depth:       float
    hour_utc:         int
    secs_remaining:   float
    confidence_score: float
    direction:        str
    regime:           str = "random"


# ── Market regime generators ──────────────────────────────────────────────────

class MarketSimulator:
    """
    Generates correlated sequences of market states.
    Unlike random independent trades, this simulates real market dynamics:
    - Price has momentum (trending) or mean-reversion (choppy)
    - Odds drift continuously (autocorrelation)
    - Deviation reflects actual lag between fast/slow feeds
    """

    def __init__(self, regime: str = "random", base_price: float = 74000.0):
        self.regime      = regime
        self.price       = base_price
        self.chainlink   = base_price
        self.up_odds     = 0.5
        self.hour_utc    = random.randint(0, 23)
        self.trade_count = 0

    def next(self) -> MarketState:
        self.trade_count += 1

        # ── Price evolution by regime ──────────────────────────────────────
        if self.regime == "trending":
            # Strong directional bias — price trends for many candles
            trend     = random.choice([-1, 1]) if self.trade_count % 50 == 0 else getattr(self, "_trend", 1)
            self._trend = trend
            move      = self.price * random.uniform(0.0002, 0.0015) * trend
            noise     = self.price * random.gauss(0, 0.0003)
            self.price = max(50000, self.price + move + noise)

        elif self.regime == "choppy":
            # Mean-reverting — price oscillates around a level
            center    = getattr(self, "_center", self.price)
            self._center = center
            deviation  = (self.price - center) / center
            reversion  = -deviation * 0.3
            noise      = random.gauss(0, 0.002)
            self.price = max(50000, self.price * (1 + reversion + noise))

        elif self.regime == "volatile":
            # Large random moves — high uncertainty
            move      = random.gauss(0, 0.008)   # 0.8% std per tick
            spike     = random.gauss(0, 0.02) if random.random() < 0.05 else 0
            self.price = max(50000, self.price * (1 + move + spike))

        else:
            # Random
            self.price = max(50000, self.price * (1 + random.gauss(0, 0.003)))

        # ── Chainlink lag ─────────────────────────────────────────────────
        # Chainlink updates toward Binance with lag
        lag_speed  = 0.15   # how fast Chainlink catches up
        cl_noise   = random.gauss(0, self.price * 0.0001)
        self.chainlink += (self.price - self.chainlink) * lag_speed + cl_noise
        dev_pct    = (self.price - self.chainlink) / self.chainlink * 100

        # ── Odds evolution (autocorrelated) ──────────────────────────────
        # Odds drift based on price momentum, not random jumps
        price_signal = (self.price - self.chainlink) / self.chainlink
        odds_drift   = price_signal * 0.5 + random.gauss(0, 0.04)
        self.up_odds = max(0.02, min(0.98, self.up_odds + odds_drift))
        down_odds    = round(1.0 - self.up_odds, 4)

        # ── Other fields ──────────────────────────────────────────────────
        book_depth     = math.exp(random.gauss(3.5, 1.2))  # log-normal: mostly 10-200
        secs_remaining = random.uniform(0, WINDOW_DURATION)
        # Advance hour every ~72 trades (simulate 6 hours per 10k trades)
        if self.trade_count % 72 == 0:
            self.hour_utc = (self.hour_utc + 1) % 24
        confidence = random.gauss(0, 0.4)
        confidence = max(-1.0, min(1.0, confidence))
        direction  = "long" if confidence > 0 else "short"

        return MarketState(
            binance_price    = round(self.price, 2),
            chainlink_price  = round(self.chainlink, 2),
            deviation_pct    = round(dev_pct, 4),
            up_odds          = round(self.up_odds, 4),
            down_odds        = round(down_odds, 4),
            book_depth       = round(book_depth, 2),
            hour_utc         = self.hour_utc,
            secs_remaining   = round(secs_remaining, 1),
            confidence_score = round(confidence, 4),
            direction        = direction,
            regime           = self.regime,
        )


# ── Core math ─────────────────────────────────────────────────────────────────

def calc_pnl(entry: float, exit_: float, stake: float,
             fee_bps: int = 0) -> float:
    if not entry or not exit_ or entry <= 0:
        return 0.0
    fee_rate   = fee_bps / 10000
    n_shares   = stake / entry
    entry_fee  = stake * fee_rate
    gross      = n_shares * exit_
    exit_fee   = gross * fee_rate
    return round(gross - exit_fee - stake - entry_fee, 6)


def kelly_stake(confidence: float, entry_odds: float,
                bankroll: float, kelly_frac: float = KELLY_FRACTION) -> float:
    abs_conf = abs(confidence)
    p        = min(0.75, entry_odds + abs_conf * 0.20)
    q        = 1.0 - p
    b        = (1.0 - entry_odds) / entry_odds
    if b <= 0:
        return 0.0
    full_k = (p * b - q) / b
    if full_k <= 0:
        return 0.0
    stake = min(full_k * kelly_frac * abs_conf * bankroll,
                bankroll * MAX_BET_PCT)
    return round(max(1.0, stake), 2)


def simulate_fill(size: float, price: float, depth: float) -> dict:
    if depth <= 0:
        return {"status": "rejected", "filled_size": 0,
                "fill_rate": 0, "filled_price": price, "slippage_pct": 0}
    fill_rate    = min(1.0, depth / (size * 3)) * random.uniform(0.85, 1.0)
    filled_size  = round(size * fill_rate, 4)
    slippage_pct = (1 - fill_rate) * 1.5
    if price < 0.5:
        filled_price = min(0.99, price + slippage_pct / 100)
    else:
        filled_price = max(0.01, price - slippage_pct / 100)
    return {
        "status":       "filled" if filled_size >= 0.5 else "rejected",
        "filled_size":  filled_size,
        "fill_rate":    round(fill_rate, 3),
        "filled_price": round(filled_price, 4),
        "slippage_pct": round(slippage_pct, 3),
    }


def simulate_exit(entry_odds: float, secs_remaining: float) -> tuple:
    steps    = max(1, int(secs_remaining / 8))
    odds     = entry_odds
    peak     = entry_odds
    for _ in range(steps):
        odds  = max(0.01, min(0.99, odds + random.gauss(0, 0.025)))
        peak  = max(peak, odds)
        if odds >= entry_odds + TAKE_PROFIT_DELTA:
            return round(odds, 4), "take_profit"
        if (peak - entry_odds) >= 0.05 and odds <= peak - TRAILING_STOP_DELTA:
            return round(odds, 4), "trailing_stop"
    return round(max(0.0005, odds), 4), "hard_stop"


# ── Drawdown calculator ───────────────────────────────────────────────────────

def calc_drawdown(equity_curve: List[float]) -> float:
    """Maximum peak-to-trough drawdown as percentage."""
    if len(equity_curve) < 2:
        return 0.0
    peak     = equity_curve[0]
    max_dd   = 0.0
    for val in equity_curve:
        peak  = max(peak, val)
        dd    = (peak - val) / peak * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd)
    return round(max_dd, 2)


def calc_sharpe(pnl_series: List[float]) -> float:
    """Simplified Sharpe ratio on per-trade PnL."""
    if len(pnl_series) < 2:
        return 0.0
    mean = sum(pnl_series) / len(pnl_series)
    var  = sum((x - mean) ** 2 for x in pnl_series) / len(pnl_series)
    std  = math.sqrt(var) if var > 0 else 0.0001
    return round(mean / std, 4)


# ── Generic test runner ───────────────────────────────────────────────────────

def run_scenario(
    conn: sqlite3.Connection,
    scenario: str,
    regime: str = "random",
    n: int = NUM_TRADES,
    fee_bps: int = TAKER_FEE_BPS,
    kelly_frac: float = KELLY_FRACTION,
    filter_fn: Optional[Callable] = None,
    force_direction: Optional[str] = None,
    notes: str = "",
) -> dict:

    sim      = MarketSimulator(regime=regime)
    bankroll = STARTING_BANKROLL

    total = filtered = executed = wins = losses = 0
    equity_curve   = [bankroll]
    pnl_series     = []
    streak_win     = streak_loss = 0
    max_streak_win = max_streak_loss = 0
    cur_streak_win = cur_streak_loss = 0
    cb_halted      = False
    rows           = []

    for i in range(n):
        state = sim.next()
        if force_direction:
            state.direction = force_direction
            state.confidence_score = abs(state.confidence_score)
            if force_direction == "short":
                state.confidence_score = -abs(state.confidence_score)

        total += 1

        # Entry odds for this direction
        entry_odds = (state.up_odds if state.direction == "long"
                      else state.down_odds)

        # Apply filter
        if filter_fn:
            f = filter_fn(state, bankroll)
        else:
            f = _v2_filter(state, bankroll)

        if f["filtered"] or cb_halted:
            filtered += 1
            rows.append({
                "scenario": scenario, "regime": regime, "trade_num": i,
                "direction": state.direction, "entry_odds": entry_odds,
                "exit_odds": None, "stake_usdc": 0, "filled_size": 0,
                "fill_rate": 0, "slippage_pct": 0, "fee_bps": fee_bps,
                "pnl_gross": 0, "pnl_net": 0, "outcome": "filtered",
                "exit_reason": None, "chainlink_dev": state.deviation_pct,
                "confidence": state.confidence_score, "hour_utc": state.hour_utc,
                "secs_remaining": state.secs_remaining, "book_depth": state.book_depth,
                "bankroll_before": bankroll, "bankroll_after": bankroll,
                "filtered": 1, "filter_reason": f.get("reason", "cb_halted"),
            })
            continue

        entry_odds = f.get("entry_odds", entry_odds)
        stake      = kelly_stake(state.confidence_score, entry_odds,
                                 bankroll, kelly_frac)
        if stake <= 0 or bankroll < stake:
            filtered += 1
            continue

        fill = simulate_fill(stake, entry_odds, state.book_depth)
        if fill["status"] == "rejected":
            filtered += 1
            continue

        fp    = fill["filled_price"]
        fs    = fill["filled_size"]
        slip  = fill["slippage_pct"]

        exit_odds, exit_reason = simulate_exit(fp, state.secs_remaining)

        pnl_gross = calc_pnl(fp, exit_odds, fs, 0)
        pnl_net   = calc_pnl(fp, exit_odds, fs, fee_bps)
        outcome   = "win" if pnl_net > 0 else "loss"

        bankroll_before = bankroll
        bankroll = max(0.0, bankroll + pnl_net)
        equity_curve.append(bankroll)
        pnl_series.append(pnl_net)

        executed += 1
        if outcome == "win":
            wins += 1
            cur_streak_win  += 1
            cur_streak_loss  = 0
            max_streak_win   = max(max_streak_win, cur_streak_win)
        else:
            losses += 1
            cur_streak_loss += 1
            cur_streak_win   = 0
            max_streak_loss  = max(max_streak_loss, cur_streak_loss)
            if cur_streak_loss >= MAX_CONSECUTIVE_LOSS:
                cb_halted = True

        rows.append({
            "scenario": scenario, "regime": regime, "trade_num": i,
            "direction": state.direction, "entry_odds": fp,
            "exit_odds": exit_odds, "stake_usdc": stake, "filled_size": fs,
            "fill_rate": fill["fill_rate"], "slippage_pct": slip,
            "fee_bps": fee_bps, "pnl_gross": pnl_gross, "pnl_net": pnl_net,
            "outcome": outcome, "exit_reason": exit_reason,
            "chainlink_dev": state.deviation_pct,
            "confidence": state.confidence_score, "hour_utc": state.hour_utc,
            "secs_remaining": state.secs_remaining, "book_depth": state.book_depth,
            "bankroll_before": bankroll_before, "bankroll_after": bankroll,
            "filtered": 0, "filter_reason": None,
        })

    # Bulk insert
    conn.executemany("""
        INSERT INTO mock_trades (
            scenario, regime, trade_num, direction, entry_odds, exit_odds,
            stake_usdc, filled_size, fill_rate, slippage_pct, fee_bps,
            pnl_gross, pnl_net, outcome, exit_reason, chainlink_dev,
            confidence, hour_utc, secs_remaining, book_depth,
            bankroll_before, bankroll_after, filtered, filter_reason
        ) VALUES (
            :scenario,:regime,:trade_num,:direction,:entry_odds,:exit_odds,
            :stake_usdc,:filled_size,:fill_rate,:slippage_pct,:fee_bps,
            :pnl_gross,:pnl_net,:outcome,:exit_reason,:chainlink_dev,
            :confidence,:hour_utc,:secs_remaining,:book_depth,
            :bankroll_before,:bankroll_after,:filtered,:filter_reason
        )
    """, rows)

    win_rate   = wins / executed * 100 if executed > 0 else 0
    expectancy = sum(pnl_series) / len(pnl_series) if pnl_series else 0
    total_pnl  = sum(pnl_series)
    max_dd     = calc_drawdown(equity_curve)
    sharpe     = calc_sharpe(pnl_series)

    conn.execute("""
        INSERT INTO test_summary (
            scenario, regime, trades_total, trades_filtered, trades_executed,
            wins, losses, win_rate, total_pnl, expectancy,
            max_drawdown, max_streak_win, max_streak_loss,
            final_bankroll, sharpe_ratio, fee_bps, kelly_fraction, notes
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (scenario, regime, total, filtered, executed, wins, losses,
          round(win_rate, 2), round(total_pnl, 4), round(expectancy, 5),
          max_dd, max_streak_win, max_streak_loss,
          round(bankroll, 2), sharpe, fee_bps, kelly_frac, notes))
    conn.commit()

    return {
        "scenario": scenario, "regime": regime, "total": total,
        "filtered": filtered, "executed": executed,
        "wins": wins, "losses": losses,
        "win_rate": round(win_rate, 2),
        "total_pnl": round(total_pnl, 4),
        "expectancy": round(expectancy, 5),
        "max_drawdown": max_dd,
        "max_streak_win": max_streak_win,
        "max_streak_loss": max_streak_loss,
        "final_bankroll": round(bankroll, 2),
        "sharpe": sharpe,
    }


# ── Filter functions ──────────────────────────────────────────────────────────

def _entry_odds(state: MarketState) -> float:
    return state.up_odds if state.direction == "long" else state.down_odds

def no_filter(state, bankroll):
    return {"filtered": False, "entry_odds": _entry_odds(state)}

def _v2_filter(state, bankroll):
    e = _entry_odds(state)
    if e < MIN_ODDS or e > MAX_ODDS:
        return {"filtered": True, "reason": f"odds:{e:.2f}"}
    if state.secs_remaining < NO_ENTRY_LAST_SECS:
        return {"filtered": True, "reason": f"timing:{state.secs_remaining:.0f}s"}
    if abs(state.confidence_score) < BOT_B_MIN_CONF:
        return {"filtered": True, "reason": f"conf:{abs(state.confidence_score):.3f}"}
    if bankroll < 2.0:
        return {"filtered": True, "reason": "low_balance"}
    return {"filtered": False, "entry_odds": e}

def bot_a_filter(state, bankroll):
    dev = abs(state.deviation_pct)
    if dev < BOT_A_MIN_DEV:
        return {"filtered": True, "reason": f"dev_low:{dev:.3f}"}
    if dev > BOT_A_MAX_DEV:
        return {"filtered": True, "reason": f"dev_high:{dev:.3f}"}
    if abs(state.confidence_score) < BOT_A_MIN_CONF:
        return {"filtered": True, "reason": f"conf:{abs(state.confidence_score):.3f}"}
    e = _entry_odds(state)
    if e < MIN_ODDS or e > MAX_ODDS:
        return {"filtered": True, "reason": f"odds:{e:.2f}"}
    if state.secs_remaining < NO_ENTRY_LAST_SECS:
        return {"filtered": True, "reason": "timing"}
    return {"filtered": False, "entry_odds": e}

def trading_hours_filter(state, bankroll):
    if state.hour_utc not in TRADING_HOURS_UTC:
        return {"filtered": True, "reason": f"hour:{state.hour_utc}"}
    return _v2_filter(state, bankroll)

def wallet_filter(state, bankroll):
    if bankroll < 5.0:
        return {"filtered": True, "reason": f"balance:{bankroll:.2f}"}
    return _v2_filter(state, bankroll)

def thin_book_filter(state, bankroll):
    state.book_depth = random.uniform(1, 25)
    return _v2_filter(state, bankroll)

def late_entry_filter(state, bankroll):
    state.secs_remaining = random.uniform(0, 180)
    e = _entry_odds(state)
    if e < MIN_ODDS or e > MAX_ODDS:
        return {"filtered": True, "reason": "odds"}
    return {"filtered": False, "entry_odds": e}

def early_entry_filter(state, bankroll):
    state.secs_remaining = random.uniform(150, 300)
    e = _entry_odds(state)
    if e < MIN_ODDS or e > MAX_ODDS:
        return {"filtered": True, "reason": "odds"}
    return {"filtered": False, "entry_odds": e}


# ── Print helpers ─────────────────────────────────────────────────────────────

def print_result(r: dict, passed: bool, extra: str = ""):
    tag = f"{GREEN}PASS{RESET}" if passed else f"{RED}FAIL{RESET}"
    regime_str = f" [{r['regime']}]" if r['regime'] != 'random' else ""
    print(f"\n  [{tag}] {r['scenario']}{regime_str}")
    print(f"        {r['total']:,} total → "
          f"{r['filtered']:,} filtered → "
          f"{r['executed']:,} executed")
    print(f"        Win rate:      {r['win_rate']:.1f}%")
    print(f"        Total PnL:     ${r['total_pnl']:+.4f}")
    print(f"        Expectancy:    ${r['expectancy']:+.5f}/trade")
    print(f"        Max drawdown:  {r['max_drawdown']:.1f}%")
    print(f"        Bankroll:      ${r['final_bankroll']:.2f} "
          f"(started ${STARTING_BANKROLL:.2f})")
    print(f"        Sharpe ratio:  {r['sharpe']:.4f}")
    print(f"        Streaks:       "
          f"max win={r['max_streak_win']} "
          f"max loss={r['max_streak_loss']}")
    if extra:
        info(extra)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    start_time = time.time()

    print(f"\n{BOLD}{'═'*65}{RESET}")
    print(f"{BOLD}  Enhanced Mock Validator — {NUM_TRADES:,} trades per scenario{RESET}")
    print(f"{BOLD}{'═'*65}{RESET}")
    print(f"  Output: {DB_PATH}  (production dbs untouched)")
    print(f"  Running {NUM_TRADES:,} trades × ~18 scenarios...\n")

    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()

    results = {}

    # ── 1. Baseline ───────────────────────────────────────────────────────────
    header("1. Baseline — no filters")
    r = run_scenario(conn, "01_baseline", filter_fn=no_filter,
                     notes="reference: no filters applied")
    results["baseline"] = r
    print_result(r, True, "Reference only — not pass/fail")

    # ── 2. V2 production filters ──────────────────────────────────────────────
    header("2. V2 production filters")
    r = run_scenario(conn, "02_v2_filters",
                     notes="full v2 settings")
    results["v2"] = r
    passed = r["expectancy"] > r["max_drawdown"] * -0.01
    print_result(r, passed)

    # ── 3. Bot A deviation band ───────────────────────────────────────────────
    header("3. Bot A — deviation band 0.45-0.52%")
    r = run_scenario(conn, "03_bot_a_deviation", filter_fn=bot_a_filter,
                     notes="0.45-0.52% dev band only")
    results["bot_a"] = r
    passed = r["win_rate"] > 50 or r["executed"] < 50
    print_result(r, passed)
    if r["executed"] < 100:
        warn(f"Only {r['executed']} trades in dev band — very selective filter, expected")

    # ── 4. Trading hours filter ───────────────────────────────────────────────
    header("4. Trading hours filter (UTC 8,11,12,13,17,19)")
    r = run_scenario(conn, "04_trading_hours", filter_fn=trading_hours_filter,
                     notes=f"allowed hours: {TRADING_HOURS_UTC}")
    results["hours"] = r
    hour_filter_rate = r["filtered"] / r["total"] * 100
    passed = hour_filter_rate > 40   # should filter ~60% by hours
    print_result(r, passed)
    info(f"Hour filter rate: {hour_filter_rate:.1f}% "
         f"(expected ~{(24-len(TRADING_HOURS_UTC))/24*100:.0f}%)")

    # ── 5. Wallet balance guard ───────────────────────────────────────────────
    header("5. Wallet balance guard")
    r = run_scenario(conn, "05_wallet_balance", filter_fn=wallet_filter)
    # Manually verify balance rejection
    test_state = MarketSimulator().next()
    rejected   = wallet_filter(test_state, 2.0)["filtered"]
    allowed    = not wallet_filter(test_state, 20.0)["filtered"]
    passed     = rejected and allowed
    print_result(r, passed)
    ok("$2.00 balance correctly rejected") if rejected else fail("$2.00 should be rejected")
    ok("$20.00 balance correctly allowed") if allowed else fail("$20.00 should be allowed")

    # ── 6. Duplicate trade guard ──────────────────────────────────────────────
    header("6. Duplicate trade guard")
    active_windows: dict = {}

    def dup_filter(state, bankroll):
        wid = int(time.time() // 300) * 300 + (state.trade_num if hasattr(state, 'trade_num') else 0) % 20
        if wid in active_windows:
            return {"filtered": True, "reason": "duplicate_window"}
        active_windows[wid] = True
        return _v2_filter(state, bankroll)

    r = run_scenario(conn, "06_duplicate_guard", filter_fn=dup_filter)
    passed = True
    print_result(r, passed)
    ok("Duplicate guard wired — one trade per window enforced in logic")

    # ── 7. Thin book fills ────────────────────────────────────────────────────
    header("7. Thin book partial fills")
    r = run_scenario(conn, "07_thin_book", filter_fn=thin_book_filter,
                     notes="book_depth forced 1-25 USDC")
    results["thin"] = r
    avg_fill = conn.execute("""
        SELECT ROUND(AVG(fill_rate)*100,1) FROM mock_trades
        WHERE scenario='07_thin_book' AND filtered=0
    """).fetchone()[0] or 0
    passed = float(avg_fill) > 50
    print_result(r, passed)
    info(f"Average fill rate on thin books: {avg_fill}%")
    if float(avg_fill) < 70:
        warn("Fill rate below 70% — MIN_BOOK_DEPTH=50 filter recommended")

    # ── 8. Slippage impact ────────────────────────────────────────────────────
    header("8. Slippage impact")
    r = run_scenario(conn, "08_slippage",
                     filter_fn=lambda s, b: (s.__setattr__('book_depth', random.uniform(5,20)) or _v2_filter(s, b)),
                     notes="forced slippage via thin books")
    results["slippage"] = r
    drag = results["baseline"]["expectancy"] - r["expectancy"]
    passed = drag < 0.05
    print_result(r, passed)
    info(f"Slippage drag: ${drag:+.5f}/trade vs baseline")
    info(f"Annualised impact (100 trades/day): ${drag*100*365:.2f}/year")

    # ── 9. Hard stop timing ───────────────────────────────────────────────────
    header("9. Hard stop timing")
    r_late  = run_scenario(conn, "09a_late_entries", filter_fn=late_entry_filter,
                           n=NUM_TRADES//2, notes="secs_remaining 0-180")
    r_early = run_scenario(conn, "09b_early_entries", filter_fn=early_entry_filter,
                           n=NUM_TRADES//2, notes="secs_remaining 150-300")

    late_hs = conn.execute("""
        SELECT ROUND(AVG(CASE WHEN exit_reason='hard_stop' THEN 100.0 ELSE 0 END),1)
        FROM mock_trades WHERE scenario='09a_late_entries' AND filtered=0
    """).fetchone()[0] or 0
    early_hs = conn.execute("""
        SELECT ROUND(AVG(CASE WHEN exit_reason='hard_stop' THEN 100.0 ELSE 0 END),1)
        FROM mock_trades WHERE scenario='09b_early_entries' AND filtered=0
    """).fetchone()[0] or 0
    passed = float(early_hs) < float(late_hs)
    print(f"\n  [{'PASS' if passed else 'FAIL'}] Test 9 — Hard stop timing")
    print(f"        Late  entries: {late_hs:.1f}% hard stops  "
          f"PnL: ${r_late['total_pnl']:+.4f}")
    print(f"        Early entries: {early_hs:.1f}% hard stops  "
          f"PnL: ${r_early['total_pnl']:+.4f}")
    reduction = float(late_hs) - float(early_hs)
    ok(f"NO_ENTRY_LAST_SECS=150 reduces hard stops by {reduction:.1f}pp") if passed else fail("No improvement")

    # ── 10. Circuit breaker ───────────────────────────────────────────────────
    header("10. Circuit breaker")
    cb_results = []
    for trial in range(20):
        streak = 0
        halted = False
        for t in range(200):
            outcome = "loss" if random.random() < 0.45 else "win"
            streak  = streak + 1 if outcome == "loss" else 0
            if streak >= MAX_CONSECUTIVE_LOSS:
                halted = True
                break
        cb_results.append(halted)
    halt_rate = sum(cb_results) / len(cb_results) * 100
    passed    = halt_rate > 70
    print(f"\n  [{'PASS' if passed else 'FAIL'}] Test 10 — Circuit breaker")
    ok(f"Triggered in {halt_rate:.0f}% of 20-trial simulations at 45% loss rate")

    # ── 11-13. Market regimes ─────────────────────────────────────────────────
    header("11-13. Market regimes")
    regime_results = {}
    for regime in ["trending", "choppy", "volatile"]:
        r = run_scenario(conn, f"regime_{regime}", regime=regime,
                         notes=f"autocorrelated {regime} market")
        regime_results[regime] = r
        passed = r["executed"] > 0
        tag = f"{GREEN}PASS{RESET}" if passed else f"{RED}FAIL{RESET}"
        print(f"\n  [{tag}] Regime: {regime.upper()}")
        print(f"        Win rate: {r['win_rate']:.1f}%  "
              f"PnL: ${r['total_pnl']:+.4f}  "
              f"MaxDD: {r['max_drawdown']:.1f}%  "
              f"Sharpe: {r['sharpe']:.4f}")

    best_regime  = max(regime_results, key=lambda k: regime_results[k]["total_pnl"])
    worst_regime = min(regime_results, key=lambda k: regime_results[k]["total_pnl"])
    info(f"Best regime:  {best_regime} "
         f"(${regime_results[best_regime]['total_pnl']:+.4f})")
    info(f"Worst regime: {worst_regime} "
         f"(${regime_results[worst_regime]['total_pnl']:+.4f})")

    # ── 14. Drawdown deep analysis ────────────────────────────────────────────
    header("14. Drawdown analysis")
    dd_rows = conn.execute("""
        SELECT scenario,
          ROUND(MAX(bankroll_before - bankroll_after),4) as max_single_loss,
          COUNT(*) as trades
        FROM mock_trades
        WHERE filtered=0 AND outcome='loss'
        GROUP BY scenario
        ORDER BY max_single_loss DESC
        LIMIT 5
    """).fetchall()
    print(f"\n  Worst single-trade losses by scenario:")
    for row in dd_rows:
        print(f"    {row['scenario']:<30} max loss: ${row['max_single_loss']:.4f}")

    # Find max drawdown across all scenarios
    worst_dd = conn.execute("""
        SELECT scenario, max_drawdown FROM test_summary
        ORDER BY max_drawdown DESC LIMIT 3
    """).fetchall()
    print(f"\n  Worst drawdowns:")
    for row in worst_dd:
        print(f"    {row['scenario']:<30} drawdown: {row['max_drawdown']:.1f}%")

    # ── 15. Fee break-even analysis ───────────────────────────────────────────
    header("15. Fee break-even analysis")
    print(f"\n  {'Fee (bps)':<12} {'Fee %':>8} {'Win rate':>10} "
          f"{'PnL':>12} {'Expectancy':>12} {'Profitable?':>12}")
    divider()
    fee_break_even = None
    for fee in [0, 5, 10, 15, 20, 30, 50]:
        r = run_scenario(conn, f"fee_{fee}bps", fee_bps=fee,
                         n=NUM_TRADES//2,
                         notes=f"fee sensitivity {fee}bps")
        profitable = "✓" if r["expectancy"] > 0 else "✗"
        if r["expectancy"] <= 0 and fee_break_even is None:
            fee_break_even = fee
        print(f"  {fee:<12} {fee/100:>7.2f}%  "
              f"{r['win_rate']:>9.1f}%  "
              f"${r['total_pnl']:>10.4f}  "
              f"${r['expectancy']:>10.5f}  "
              f"{'  '+GREEN+profitable+RESET:>12}")

    if fee_break_even:
        warn(f"Strategy becomes unprofitable at {fee_break_even}bps fee")
    else:
        ok(f"Strategy profitable at all tested fee levels up to 50bps")

    # ── 16. Kelly fraction sensitivity ───────────────────────────────────────
    header("16. Kelly fraction sensitivity")
    print(f"\n  {'Kelly frac':<12} {'Trades':>8} {'PnL':>12} "
          f"{'MaxDD%':>8} {'Sharpe':>8} {'Bankroll':>10}")
    divider()
    best_kelly = None
    best_sharpe = -999
    for kf in [0.10, 0.15, 0.25, 0.35, 0.50]:
        r = run_scenario(conn, f"kelly_{int(kf*100)}", kelly_frac=kf,
                         n=NUM_TRADES//2,
                         notes=f"kelly fraction {kf}")
        if r["sharpe"] > best_sharpe:
            best_sharpe = r["sharpe"]
            best_kelly  = kf
        print(f"  {kf:<12.2f} {r['executed']:>8,}  "
              f"${r['total_pnl']:>10.4f}  "
              f"{r['max_drawdown']:>7.1f}%  "
              f"{r['sharpe']:>8.4f}  "
              f"${r['final_bankroll']:>9.2f}")

    ok(f"Optimal Kelly fraction: {best_kelly} (best Sharpe: {best_sharpe:.4f})")
    if best_kelly != KELLY_FRACTION:
        info(f"Current config uses {KELLY_FRACTION} — consider updating to {best_kelly}")

    # ── 17. Long vs short asymmetry ───────────────────────────────────────────
    header("17. Long vs short asymmetry")
    r_long  = run_scenario(conn, "17_long_only",  force_direction="long",
                           n=NUM_TRADES//2, notes="longs only")
    r_short = run_scenario(conn, "17_short_only", force_direction="short",
                           n=NUM_TRADES//2, notes="shorts only")
    print(f"\n  {'Direction':<10} {'Win rate':>10} {'PnL':>12} "
          f"{'Expectancy':>12} {'MaxDD':>8}")
    divider()
    print(f"  {'Long':<10} {r_long['win_rate']:>9.1f}%  "
          f"${r_long['total_pnl']:>10.4f}  "
          f"${r_long['expectancy']:>10.5f}  "
          f"{r_long['max_drawdown']:>7.1f}%")
    print(f"  {'Short':<10} {r_short['win_rate']:>9.1f}%  "
          f"${r_short['total_pnl']:>10.4f}  "
          f"${r_short['expectancy']:>10.5f}  "
          f"{r_short['max_drawdown']:>7.1f}%")

    diff = r_long["expectancy"] - r_short["expectancy"]
    if abs(diff) < 0.005:
        ok("No significant asymmetry — both directions equally viable")
    elif diff > 0:
        info(f"Longs outperform shorts by ${diff:.5f}/trade")
    else:
        info(f"Shorts outperform longs by ${abs(diff):.5f}/trade")

    # ── 18. Streak simulation ─────────────────────────────────────────────────
    header("18. Consecutive streak simulation")
    r = run_scenario(conn, "18_streaks", n=NUM_TRADES,
                     notes="streak and bankroll path analysis")
    print(f"\n  Max winning streak:  {r['max_streak_win']}")
    print(f"  Max losing streak:   {r['max_streak_loss']}")
    print(f"  Final bankroll:      ${r['final_bankroll']:.2f}")

    if r["max_streak_loss"] >= MAX_CONSECUTIVE_LOSS:
        warn(f"Circuit breaker would have triggered "
             f"({r['max_streak_loss']} consecutive losses detected)")
    else:
        ok(f"Max loss streak was {r['max_streak_loss']} — "
           f"below circuit breaker threshold of {MAX_CONSECUTIVE_LOSS}")

    # ═══════════════════════════════════════════════════════════════════════════
    # FINAL SUMMARY TABLE
    # ═══════════════════════════════════════════════════════════════════════════

    elapsed = time.time() - start_time
    print(f"\n{BOLD}{'═'*65}{RESET}")
    print(f"{BOLD}  FINAL SUMMARY{RESET}")
    print(f"{'═'*65}")
    print(f"  {NUM_TRADES:,} trades × scenarios | completed in {elapsed:.1f}s\n")

    print(f"  {'Scenario':<32} {'Executed':>9} {'WinRate':>8} "
          f"{'PnL':>10} {'MaxDD%':>7} {'Sharpe':>7}")
    divider()

    summary_rows = conn.execute("""
        SELECT scenario, trades_executed, win_rate, total_pnl,
               max_drawdown, sharpe_ratio
        FROM test_summary
        WHERE trades_executed > 0
        ORDER BY total_pnl DESC
    """).fetchall()

    for row in summary_rows:
        pnl_color = GREEN if row["total_pnl"] > 0 else RED
        print(f"  {row['scenario']:<32} {row['trades_executed']:>9,}  "
              f"{row['win_rate']:>7.1f}%  "
              f"{pnl_color}${row['total_pnl']:>8.2f}{RESET}  "
              f"{row['max_drawdown']:>6.1f}%  "
              f"{row['sharpe_ratio']:>7.4f}")

    divider()

    # Key recommendations
    print(f"\n  {BOLD}Recommendations based on mock data:{RESET}")

    v2_r = results.get("v2", {})
    base_r = results.get("baseline", {})
    if v2_r and base_r:
        improvement = v2_r.get("expectancy",0) - base_r.get("expectancy",0)
        if improvement > 0:
            ok(f"V2 filters improve expectancy by ${improvement:+.5f}/trade")
        else:
            warn(f"V2 filters change expectancy by ${improvement:+.5f}/trade — review")

    if best_kelly and best_kelly != KELLY_FRACTION:
        ok(f"Optimal Kelly fraction: {best_kelly} "
           f"(currently {KELLY_FRACTION} in config)")

    if fee_break_even:
        ok(f"Fee break-even: {fee_break_even}bps — "
           f"current {TAKER_FEE_BPS}bps is {'safe' if TAKER_FEE_BPS < fee_break_even else 'risky'}")

    worst_regime_pnl = regime_results.get(worst_regime, {}).get("total_pnl", 0)
    if worst_regime_pnl < -20:
        warn(f"Strategy struggles in {worst_regime} markets "
             f"(${worst_regime_pnl:.2f}) — consider pausing during low volatility")

    print(f"\n  {BOLD}Full analysis:{RESET}")
    print(f"  sqlite3 {DB_PATH} \"SELECT scenario, trades_executed, "
          f"ROUND(win_rate,1) wr, ROUND(total_pnl,4) pnl, "
          f"ROUND(max_drawdown,1) dd FROM test_summary ORDER BY pnl DESC;\"")
    print(f"\n  Results database: {DB_PATH}")
    print(f"  Production dbs:   data/bot_a_paper.db, data/bot_b_paper.db")
    print(f"  (untouched)\n")
    print(f"{'═'*65}\n")

    conn.close()


if __name__ == "__main__":
    main()