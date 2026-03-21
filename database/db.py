"""
Database Layer — SQLite per bot.
Each bot instance gets its own db file so logs never mix.
"""

import sqlite3
import json
import logging
from datetime import datetime, date
from contextlib import contextmanager

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                  TEXT NOT NULL,
    bot                 TEXT NOT NULL,
    market_id           TEXT,
    window_start        TEXT,
    window_end          TEXT,
    direction           TEXT,
    confidence_score    REAL,
    polymarket_odds     REAL,
    chainlink_price     REAL,
    binance_price       REAL,
    chainlink_dev_pct   REAL,
    chainlink_lag_flag  INTEGER,
    momentum_30s        REAL,
    momentum_60s        REAL,
    rsi                 REAL,
    volume_zscore       REAL,
    odds_velocity       REAL,
    skip_reason         TEXT,
    features_json       TEXT
);

CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id       INTEGER REFERENCES signals(id),
    bot             TEXT NOT NULL,
    ts_entry        TEXT NOT NULL,
    ts_exit         TEXT,
    market_id       TEXT NOT NULL,
    window_start    TEXT NOT NULL,
    window_end      TEXT NOT NULL,
    direction       TEXT NOT NULL,
    entry_odds      REAL NOT NULL,
    exit_odds       REAL,
    peak_odds       REAL,
    stake_usdc      REAL NOT NULL,
    taker_fee_bps   INTEGER DEFAULT 0,
    pnl_usdc        REAL,
    pnl_gross       REAL,
    outcome         TEXT,
    exit_reason     TEXT,
    chainlink_open  REAL,
    chainlink_close REAL,
    market_condition_id TEXT,  -- bytes32
    outcome_index       INTEGER, -- YES=0, NO=1 or similar
    redeemed            INTEGER DEFAULT 0,
    resolved        INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS skipped (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    bot         TEXT NOT NULL,
    market_id   TEXT,
    reason      TEXT NOT NULL,
    confidence  REAL,
    odds        REAL,
    cl_dev      REAL
);

CREATE TABLE IF NOT EXISTS circuit_breaker (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    consecutive_losses  INTEGER DEFAULT 0,
    daily_loss_usdc     REAL DEFAULT 0.0,
    halted              INTEGER DEFAULT 0,
    halted_reason       TEXT,
    last_reset_date     TEXT
);

CREATE TABLE IF NOT EXISTS chainlink_lag_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    binance_price   REAL,
    chainlink_price REAL,
    deviation_pct   REAL,
    direction       TEXT,
    sustained_secs  REAL,
    trade_taken     INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS daily_summary (
    date            TEXT PRIMARY KEY,
    bot             TEXT NOT NULL,
    total_trades    INTEGER DEFAULT 0,
    wins            INTEGER DEFAULT 0,
    losses          INTEGER DEFAULT 0,
    win_rate        REAL,
    total_pnl       REAL DEFAULT 0.0,
    tp_exits        INTEGER DEFAULT 0,
    ts_exits        INTEGER DEFAULT 0,
    hs_exits        INTEGER DEFAULT 0,
    bankroll_end    REAL
);
"""


class Database:

    def __init__(self, db_path: str, bot_id: str):
        self.db_path = db_path
        self.bot_id  = bot_id
        self._init()

    def _init(self):
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            
            # Migration check for new columns (if table existed without them)
            existing_cols = [r["name"] for r in conn.execute("PRAGMA table_info(trades)").fetchall()]
            if "market_condition_id" not in existing_cols:
                conn.execute("ALTER TABLE trades ADD COLUMN market_condition_id TEXT")
            if "outcome_index" not in existing_cols:
                conn.execute("ALTER TABLE trades ADD COLUMN outcome_index INTEGER")
            if "redeemed" not in existing_cols:
                conn.execute("ALTER TABLE trades ADD COLUMN redeemed INTEGER DEFAULT 0")

            conn.execute("""
                INSERT OR IGNORE INTO circuit_breaker (id, last_reset_date)
                VALUES (1, ?)
            """, (date.today().isoformat(),))
        logger.info("[Bot %s] Database ready at %s", self.bot_id, self.db_path)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def log_signal(self, s: dict) -> int:
        with self._conn() as conn:
            cur = conn.execute("""
                INSERT INTO signals (
                    ts, bot, market_id, window_start, window_end,
                    direction, confidence_score, polymarket_odds,
                    chainlink_price, binance_price, chainlink_dev_pct,
                    chainlink_lag_flag, momentum_30s, momentum_60s,
                    rsi, volume_zscore, odds_velocity,
                    skip_reason, features_json
                ) VALUES (
                    :ts, :bot, :market_id, :window_start, :window_end,
                    :direction, :confidence_score, :polymarket_odds,
                    :chainlink_price, :binance_price, :chainlink_dev_pct,
                    :chainlink_lag_flag, :momentum_30s, :momentum_60s,
                    :rsi, :volume_zscore, :odds_velocity,
                    :skip_reason, :features_json
                )
            """, {**s, "bot": self.bot_id,
                  "features_json": json.dumps(s.get("features", {}))})
            return cur.lastrowid

    def log_entry(self, t: dict) -> int:
        with self._conn() as conn:
            cur = conn.execute("""
                INSERT INTO trades (
                    signal_id, bot, ts_entry, market_id,
                    window_start, window_end, direction,
                    entry_odds, peak_odds, stake_usdc,
                    taker_fee_bps, chainlink_open,
                    market_condition_id, outcome_index
                ) VALUES (
                    :signal_id, :bot, :ts_entry, :market_id,
                    :window_start, :window_end, :direction,
                    :entry_odds, :entry_odds, :stake_usdc,
                    :taker_fee_bps, :chainlink_open,
                    :market_condition_id, :outcome_index
                )
            """, {**t, "bot": self.bot_id,
                  "taker_fee_bps": t.get("taker_fee_bps", 0),
                  "market_condition_id": t.get("market_condition_id"),
                  "outcome_index": t.get("outcome_index")})
            return cur.lastrowid

    def log_exit(self, trade_id: int, e: dict) -> tuple:
        # Fetch fee rate stored at entry time
        with self._conn() as conn:
            row = conn.execute(
                "SELECT taker_fee_bps FROM trades WHERE id=?", (trade_id,)
            ).fetchone()
        fee_bps = dict(row).get("taker_fee_bps", 0) if row else 0

        # Gross PnL (no fees) and net PnL (after fees)
        gross_pnl = self._calc_pnl(e["entry_odds"], e["exit_odds"], e["stake_usdc"], 0)
        net_pnl   = self._calc_pnl(e["entry_odds"], e["exit_odds"], e["stake_usdc"], fee_bps)
        outcome   = "win" if net_pnl > 0 else ("loss" if net_pnl < 0 else "breakeven")

        with self._conn() as conn:
            conn.execute("""
                UPDATE trades SET
                    ts_exit         = :ts_exit,
                    exit_odds       = :exit_odds,
                    peak_odds       = :peak_odds,
                    pnl_usdc        = :net_pnl,
                    pnl_gross       = :gross_pnl,
                    outcome         = :outcome,
                    exit_reason     = :exit_reason,
                    chainlink_close = :chainlink_close,
                    resolved        = 1
                WHERE id = :trade_id
            """, {**e, "net_pnl": net_pnl, "gross_pnl": gross_pnl,
                  "outcome": outcome, "trade_id": trade_id})
        return net_pnl, outcome

    def update_peak(self, trade_id: int, peak: float):
        with self._conn() as conn:
            conn.execute(
                "UPDATE trades SET peak_odds=? WHERE id=? AND peak_odds<?",
                (peak, trade_id, peak)
            )

    def open_trades(self) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE resolved=0"
            ).fetchall()
        return [dict(r) for r in rows]

    def log_skip(self, reason: str, confidence=None, odds=None,
                 market_id=None, cl_dev=None):
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO skipped (ts, bot, market_id, reason, confidence, odds, cl_dev)
                VALUES (?,?,?,?,?,?,?)
            """, (datetime.utcnow().isoformat(), self.bot_id,
                  market_id, reason, confidence, odds, cl_dev))

    def get_unredeemed_wins(self) -> list:
        """Fetch all winning trades that haven't been redeemed yet."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT market_condition_id, outcome_index, id
                FROM trades
                WHERE outcome='win' AND redeemed=0 AND market_condition_id IS NOT NULL
            """).fetchall()
        return [dict(r) for r in rows]

    def mark_redeemed(self, trade_id: int):
        with self._conn() as conn:
            conn.execute("UPDATE trades SET redeemed=1 WHERE id=?", (trade_id,))

    def get_cb(self) -> dict:
        with self._conn() as conn:
            return dict(conn.execute(
                "SELECT * FROM circuit_breaker WHERE id=1"
            ).fetchone())

    def update_cb(self, losses: int, daily_loss: float,
                  halted=False, reason=None):
        with self._conn() as conn:
            conn.execute("""
                UPDATE circuit_breaker SET
                    consecutive_losses=?, daily_loss_usdc=?,
                    halted=?, halted_reason=?
                WHERE id=1
            """, (losses, daily_loss, int(halted), reason))

    def reset_cb(self):
        with self._conn() as conn:
            conn.execute("""
                UPDATE circuit_breaker SET
                    consecutive_losses=0, daily_loss_usdc=0.0,
                    halted=0, halted_reason=NULL, last_reset_date=?
                WHERE id=1
            """, (date.today().isoformat(),))

    def log_lag_event(self, e: dict):
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO chainlink_lag_events (
                    ts, binance_price, chainlink_price,
                    deviation_pct, direction, sustained_secs, trade_taken
                ) VALUES (
                    :ts, :binance_price, :chainlink_price,
                    :deviation_pct, :direction, :sustained_secs, :trade_taken
                )
            """, e)

    def daily_stats(self) -> dict:
        today = date.today().isoformat()
        with self._conn() as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*)                                        AS total,
                    SUM(CASE WHEN outcome='win'  THEN 1 END)       AS wins,
                    SUM(CASE WHEN outcome='loss' THEN 1 END)       AS losses,
                    ROUND(SUM(pnl_usdc),4)                         AS pnl,
                    ROUND(AVG(CASE WHEN outcome='win'
                        THEN 1.0 ELSE 0.0 END)*100,1)              AS win_rate,
                    SUM(CASE WHEN exit_reason='take_profit'   THEN 1 END) AS tp,
                    SUM(CASE WHEN exit_reason='trailing_stop' THEN 1 END) AS ts,
                    SUM(CASE WHEN exit_reason='hard_stop'     THEN 1 END) AS hs
                FROM trades
                WHERE DATE(ts_entry)=? AND resolved=1
            """, (today,)).fetchone()
        r = dict(row)
        for k in r:
            if r[k] is None:
                r[k] = 0
        return r

    def skip_stats(self) -> list:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT reason, COUNT(*) AS cnt
                FROM skipped GROUP BY reason ORDER BY cnt DESC
            """).fetchall()
        return [dict(r) for r in rows]

    def direction_stats(self, direction: str) -> dict:
        today = date.today().isoformat()
        with self._conn() as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*)                                        AS total,
                    ROUND(SUM(pnl_usdc),4)                         AS pnl,
                    ROUND(AVG(CASE WHEN outcome='win'
                        THEN 1.0 ELSE 0.0 END)*100,1)              AS win_rate
                FROM trades
                WHERE DATE(ts_entry)=? AND direction=? AND resolved=1
            """, (today, direction)).fetchone()
        r = dict(row)
        for k in r:
            if r[k] is None:
                r[k] = 0
        return r

    def lag_trade_stats(self) -> dict:
        today = date.today().isoformat()
        with self._conn() as conn:
            row = conn.execute("""
                SELECT
                    COUNT(t.id)                                     AS total,
                    ROUND(AVG(CASE WHEN t.outcome='win'
                        THEN 1.0 ELSE 0.0 END)*100,1)              AS win_rate,
                    ROUND(SUM(t.pnl_usdc),4)                       AS pnl
                FROM trades t
                JOIN signals s ON t.signal_id=s.id
                WHERE DATE(t.ts_entry)=?
                  AND s.chainlink_lag_flag=1
                  AND t.resolved=1
            """, (today,)).fetchone()
        r = dict(row)
        for k in r:
            if r[k] is None:
                r[k] = 0
        return r

    @staticmethod
    def _calc_pnl(entry: float, exit_: float, stake: float,
                  taker_fee_bps: int = 0) -> float:
        """
        Polymarket PnL with taker fees applied on both legs.

        Entry: you buy N shares at entry_odds
               fee = stake * (taker_fee_bps / 10000)
               total cost = stake + entry_fee

        Exit: you sell N shares at exit_odds
              gross proceeds = N * exit_odds
              fee = gross_proceeds * (taker_fee_bps / 10000)
              net proceeds = gross_proceeds - exit_fee

        PnL = net_proceeds - total_cost
        """
        if not entry or not exit_:
            return 0.0

        fee_rate     = taker_fee_bps / 10000
        n_shares     = stake / entry

        entry_fee    = stake * fee_rate
        gross_exit   = n_shares * exit_
        exit_fee     = gross_exit * fee_rate
        net_exit     = gross_exit - exit_fee
        total_cost   = stake + entry_fee

        return round(net_exit - total_cost, 6)
