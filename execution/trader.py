"""
Execution Layer
Trade entry, position monitor, trailing stop, take profit, hard stop.
One instance per bot — each has its own db and bankroll.
"""

import asyncio
import logging
import time
from datetime import datetime
from config import (
    TAKE_PROFIT_DELTA, TRAILING_STOP_DELTA,
    HARD_STOP_SECONDS, POSITION_POLL_SECS, PAPER_TRADING,
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
                 bankroll: BankrollTracker, starting_bankroll: float):
        self.bot_id            = bot_id
        self.db                = db
        self.poly              = poly_feed
        self.cb                = circuit_breaker
        self.bankroll          = bankroll
        self.starting_bankroll = starting_bankroll
        self._positions: dict  = {}

    async def enter(self, direction: str, confidence: float,
                    stake: float, signal_id: int):
        # Long = buy Up shares, Short = buy Down shares
        if direction == "long":
            token_id   = self.poly.up_token_id
            entry_odds = self.poly.up_odds
        else:
            token_id   = self.poly.down_token_id
            entry_odds = self.poly.down_odds

        if not entry_odds or not token_id:
            logger.warning("[Bot%s] No odds/token — skipping entry", self.bot_id)
            return None

        order = await self.poly.place_order(
            direction, token_id, stake, entry_odds, self.bot_id
        )
        if order.get("status") != "filled":
            return None

        filled   = order.get("filled_price", entry_odds)
        trade_id = self.db.log_entry({
            "signal_id":      signal_id,
            "ts_entry":       datetime.utcnow().isoformat(),
            "market_id":      self.poly.market_id,
            "window_start":   datetime.fromtimestamp(self.poly.window_start).isoformat(),
            "window_end":     datetime.fromtimestamp(self.poly.window_end).isoformat(),
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
            "entry_odds": filled,
            "peak_odds":  filled,
            "stake_usdc": stake,
            "window_end": self.poly.window_end,
            "confidence": confidence,
        }
        self.bankroll.reserve(stake)
        logger.info("[Bot%s][%s] ENTER | id=%s dir=%s odds=%.3f stake=%.2f",
                    self.bot_id, "PAPER" if PAPER_TRADING else "LIVE",
                    trade_id, direction, filled, stake)
        return trade_id

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
        direction    = pos["direction"]
        secs_to_end  = pos["window_end"] - time.time()

        # Track the odds for the direction we bet ON
        # Long = we bought Up shares, want Up odds to rise
        # Short = we bought Down shares, want Down odds to rise
        if direction == "long":
            current_odds = self.poly.up_odds
        else:
            current_odds = self.poly.down_odds

        if not current_odds:
            return

        # Update peak — highest odds seen for our position direction
        if current_odds > pos["peak_odds"]:
            pos["peak_odds"] = current_odds
            self.db.update_peak(trade_id, current_odds)

        # 1. Hard stop — always exit 30s before window closes
        if secs_to_end <= HARD_STOP_SECONDS:
            await self._exit(trade_id, pos, current_odds, "hard_stop")
            return

        # 2. Take profit — our odds rose TAKE_PROFIT_DELTA above entry
        if current_odds >= pos["entry_odds"] + TAKE_PROFIT_DELTA:
            await self._exit(trade_id, pos, current_odds, "take_profit")
            return

        # 3. Trailing stop — only activates after meaningful peak gain
        peak_gain  = pos["peak_odds"] - pos["entry_odds"]
        stop_level = pos["peak_odds"] - TRAILING_STOP_DELTA
        if peak_gain >= 0.05 and current_odds <= stop_level:
            await self._exit(trade_id, pos, current_odds, "trailing_stop")
            return

    async def _exit(self, trade_id: int, pos: dict,
                    exit_odds: float, reason: str):
        await self.poly.place_order(
            "sell", pos["token_id"], pos["stake_usdc"], exit_odds, self.bot_id
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
        logger.info("[Bot%s] EXIT | id=%s reason=%s odds=%.3f pnl=%+.4f outcome=%s",
                    self.bot_id, trade_id, reason, exit_odds, pnl, outcome)
