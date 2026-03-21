"""
Execution Layer
Trade entry, position monitor, take profit, hard stop.
Trailing stop disabled — 0% win rate confirmed across all paper test versions.

Changes from data analysis:
  - HARD_STOP_SECONDS: 30 → 60  (earlier exit = better price on losses)
  - TRAILING_STOP: disabled via TRAILING_STOP_ENABLED=False in config
  - peak_gain threshold: 0.05 → 0.10 (kept for when trailing re-enabled)
"""

import asyncio
import logging
import time
from datetime import datetime
from config import (
    TAKE_PROFIT_DELTA, TRAILING_STOP_DELTA, TRAILING_STOP_ENABLED,
    HARD_STOP_SECONDS, POSITION_POLL_SECS,
)

logger = logging.getLogger(__name__)


class BankrollTracker:

    def __init__(self, balance: float):
        self.balance   = balance
        self._reserved = 0.0

    @property
    def available(self) -> float:
        return self.balance - self._reserved

    def reserve(self, amount: float):
        self._reserved = min(self._reserved + amount, self.balance)

    def settle(self, stake: float, pnl: float):
        self._reserved = max(0.0, self._reserved - stake)
        self.balance   = round(self.balance + pnl, 4)


class ExecutionLayer:

    def __init__(self, bot_id: str, db, poly_feed, circuit_breaker,
                 bankroll: BankrollTracker, starting_bankroll: float,
                 paper_trading: bool = True):
        self.bot_id            = bot_id
        self.db                = db
        self.poly              = poly_feed
        self.cb                = circuit_breaker
        self.bankroll          = bankroll
        self.starting_bankroll = starting_bankroll
        self.paper_trading     = paper_trading
        self._positions: dict  = {}

    # ── Entry ──────────────────────────────────────────────────────────────────

    async def enter(self, direction: str, confidence: float,
                    stake: float, signal_id: int, 
                    token_id: str = None, entry_odds: float = None,
                    market_id: str = None, win_end: float = None,
                    win_start: float = None):
        
        # Backward compatibility for legacy bots (A/B)
        if not token_id:
            if direction == "long":
                token_id   = self.poly.up_token_id
                entry_odds = self.poly.up_odds
                market_id  = self.poly.market_id
                win_end    = self.poly.window_end
                win_start  = self.poly.window_start
            else:
                token_id   = self.poly.down_token_id
                entry_odds = self.poly.down_odds
                market_id  = self.poly.market_id
                win_end    = self.poly.window_end
                win_start  = self.poly.window_start

        if not entry_odds or not token_id:
            logger.warning("[Bot%s] No odds/token — skipping entry", self.bot_id)
            return None

        order = await self.poly.place_order(
            direction, token_id, stake, entry_odds, self.bot_id,
            paper=self.paper_trading
        )
        if order.get("status") != "filled":
            return None

        filled   = order.get("filled_price", entry_odds)
        trade_id = self.db.log_entry({
            "signal_id":      signal_id,
            "ts_entry":       datetime.utcnow().isoformat(),
            "market_id":      market_id,
            "window_start":   datetime.fromtimestamp(
                                  win_start).isoformat() if win_start else None,
            "window_end":     datetime.fromtimestamp(
                                  win_end).isoformat() if win_end else None,
            "direction":      direction,
            "entry_odds":     filled,
            "stake_usdc":     stake,
            "taker_fee_bps":  self.poly.taker_fee_bps,
            "chainlink_open": None,
        })

        self._positions[trade_id] = {
            "trade_id":   trade_id,
            "direction":  direction,
            "token_id":   token_id,
            "market_id":  market_id,
            "entry_odds": filled,
            "peak_odds":  filled,
            "stake_usdc": stake,
            "window_end": win_end,
            "confidence": confidence,
        }
        self.bankroll.reserve(stake)
        logger.info("[Bot%s][%s] ENTER | id=%s dir=%s odds=%.3f stake=%.2f",
                    self.bot_id,
                    "PAPER" if self.paper_trading else "LIVE",
                    trade_id, direction, filled, stake)
        return trade_id

    # ── Position monitor ───────────────────────────────────────────────────────

    async def start_monitor(self):
        while True:
            if self._positions:
                await self._check_all()
            await asyncio.sleep(POSITION_POLL_SECS)

    async def on_odds_update(self):
        if self._positions:
            await self._check_all()

    async def _check_all(self):
        for tid, pos in list(self._positions.items()):
            await self._evaluate(tid, pos)

    async def _evaluate(self, trade_id: int, pos: dict):
        direction = pos.get("direction")
        win_end = pos.get("window_end")
        secs_to_end = (win_end - time.time()) if win_end else 999999
        
        # Use the specific market data for this token
        market = self.poly.markets.get(pos["token_id"])
        if market:
            current_odds = market.get("odds")
        else:
            # Fallback for legacy UP/DOWN logic
            current_odds = self.poly.up_odds if direction == "long" else self.poly.down_odds

        if not current_odds:
            return

        # Update peak odds
        if current_odds > pos["peak_odds"]:
            pos["peak_odds"] = current_odds
            self.db.update_peak(trade_id, current_odds)

        # ── 1. Hard stop — exit 60s before window closes ───────────────────
        # At 60s exit price is still reasonable
        # At 30s odds often already collapsed — worse fill
        if secs_to_end <= HARD_STOP_SECONDS:
            await self._exit(trade_id, pos, current_odds, "hard_stop")
            return

        # ── 2. Take profit ─────────────────────────────────────────────────
        # Exit when odds rise TAKE_PROFIT_DELTA above entry
        if current_odds >= pos["entry_odds"] + TAKE_PROFIT_DELTA:
            await self._exit(trade_id, pos, current_odds, "take_profit")
            return

        # ── 3. Trailing stop (disabled by default) ─────────────────────────
        # Data showed 0% win rate across all versions
        # Re-enable via TRAILING_STOP_ENABLED=True in config if re-testing
        if TRAILING_STOP_ENABLED:
            peak_gain  = pos["peak_odds"] - pos["entry_odds"]
            stop_level = pos["peak_odds"] - TRAILING_STOP_DELTA
            if peak_gain >= 0.10 and current_odds <= stop_level:
                await self._exit(trade_id, pos, current_odds, "trailing_stop")
                return

    async def _exit(self, trade_id: int, pos: dict,
                    exit_odds: float, reason: str):
        await self.poly.place_order(
            "sell", pos["token_id"],
            pos["stake_usdc"], exit_odds, self.bot_id,
            paper=self.paper_trading
        )
        pnl, outcome = self.db.log_exit(trade_id, {
            "ts_exit":        datetime.utcnow().isoformat(),
            "entry_odds":     pos["entry_odds"],
            "exit_odds":      exit_odds,
            "peak_odds":      pos["peak_odds"],
            "stake_usdc":     pos["stake_usdc"],
            "exit_reason":    reason,
            "chainlink_close": None,
        })
        self.bankroll.settle(pos["stake_usdc"], pnl)
        self.cb.on_result(self.db, outcome, pnl, self.starting_bankroll)
        del self._positions[trade_id]
        logger.info(
            "[Bot%s] EXIT | id=%s reason=%s odds=%.3f pnl=%+.4f outcome=%s",
            self.bot_id, trade_id, reason, exit_odds, pnl, outcome
        )