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
    HARD_STOP_SECONDS, POSITION_POLL_SECS, STOP_LOSS_DELTA,
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
        self._load_positions()

    def _load_positions(self):
        """Reconstruct in-memory actively managed positions from DB on crash restart."""
        open_trades = self.db.open_trades()
        for t in open_trades:
            tid = t.get("id")
            stake = t.get("stake_usdc", 0.0)
            token = t.get("token_id")
            if not token: # Very old DB entry compat
                token = self.poly.up_token_id if t.get("direction") == "long" else self.poly.down_token_id
            
            self._positions[tid] = {
                "trade_id":   tid,
                "direction":  t.get("direction"),
                "token_id":   token,
                "market_id":  t.get("market_id"),
                "entry_odds": t.get("entry_odds", 0.0),
                "peak_odds":  t.get("peak_odds", 0.0),
                "stake_usdc": stake,
                "window_end": datetime.fromisoformat(t["window_end"]).timestamp() if t.get("window_end") else None,
                "confidence": 0.0,  # Legacy restored missing confidence
            }
            self.bankroll.reserve(stake)
        
        if open_trades:
            logger.info("[Bot%s] Reloaded %d active trades from database", self.bot_id, len(open_trades))

    # ── Entry ──────────────────────────────────────────────────────────────────

    async def enter(self, direction: str, confidence: float,
                    stake: float, signal_id: int,
                    token_id: str = None, entry_odds: float = None,
                    market_id: str = None, win_end: float = None,
                    win_start: float = None, condition_id: str = None,
                    asset: str = None, slug: str = None):
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

        # ── One position per market window (all bots) ──────────────────────────
        # Guards against double-entry on same condition regardless of direction.
        # Uses market_id (condition_id) as the dedup key.
        if market_id:
            for pos in self._positions.values():
                if pos.get("market_id") == market_id:
                    logger.debug("[Bot%s] Already in market %s — skipping", self.bot_id, market_id[:12])
                    return None

        # Global risk gate
        if hasattr(self, "global_risk") and self.global_risk:
            passed, reason = self.global_risk.can_enter(stake, token_id, market_id)
            if not passed:
                import logging
                logging.getLogger(f"bot_{self.bot_id.lower()}").warning(
                    "Global Risk Manager skipped trade: %s", reason
                )
                self.db.log_skip(reason, confidence, entry_odds or 0.0, market_id)
                return

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
        order_id = order.get("order_id")
        
        if self.paper_trading and not order_id:
            import uuid
            order_id = f"paper-{uuid.uuid4()}"
            
        trade_id = self.db.log_entry({
            "signal_id":           signal_id,
            "ts_entry":            datetime.utcnow().isoformat(),
            "market_id":           market_id,
            "window_start":        datetime.fromtimestamp(
                                       win_start).isoformat() if win_start else None,
            "window_end":          datetime.fromtimestamp(
                                       win_end).isoformat() if win_end else None,
            "direction":           direction,
            "entry_odds":          filled,
            "stake_usdc":          stake,
            "token_id":            token_id,
            "market_condition_id": condition_id,
            "outcome_index":       0, # Standard for binary crypto tokens
            "clob_order_id":       order_id,
            "taker_fee_bps":       self.poly.taker_fee_bps,
            "chainlink_open":      None,
            "asset":               asset,
            "slug":                slug,
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
            "asset":      asset,
            "slug":       slug,
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

        # ── 3-Tier Exit Hierarchy (applies to ALL bots) ─────────────────────

        # TIER 1: Take Profit — exit when price rises TP_DELTA above entry
        # Checked first so winning trades exit before time-based stops interfere
        if current_odds >= pos["entry_odds"] + TAKE_PROFIT_DELTA:
            await self._exit(trade_id, pos, current_odds, "take_profit")
            return

        # TIER 2: Stop Loss — exit if price drops SL_DELTA below entry
        # Price-based: fires the moment market moves against us by 8 points
        # Primary loss-limiting mechanism — replaces hard stop for most exits
        if current_odds <= pos["entry_odds"] - STOP_LOSS_DELTA:
            await self._exit(trade_id, pos, current_odds, "stop_loss")
            return

        # TIER 3: Hard Stop — last resort, force-exit before 0/1 settlement
        # If neither TP nor SL fired, force-exit for liquidity safety.
        # MANDATORY REFRESH: Before exiting via hard stop, fetch the absolute midpoint truth
        if secs_to_end <= HARD_STOP_SECONDS:
            tid = pos.get("token_id")
            if tid:
                try:
                    await self.poly.fetch_book(tid)
                    # Pull the fresh midpoint recorded by fetch_book
                    current_odds = self.poly.markets[tid].get("odds", current_odds)
                except Exception:
                    pass # Fallback to last known if REST fails 

            await self._exit(trade_id, pos, current_odds, "hard_stop")
            return

        # Trailing stop (disabled — 0% win rate in all backtested versions)
        if TRAILING_STOP_ENABLED:
            peak_gain  = pos["peak_odds"] - pos["entry_odds"]
            stop_level = pos["peak_odds"] - TRAILING_STOP_DELTA
            if peak_gain >= 0.10 and current_odds <= stop_level:
                await self._exit(trade_id, pos, current_odds, "trailing_stop")
                return

    async def _exit(self, trade_id: int, pos: dict,
                    exit_odds: float, reason: str):
        # 1. Fire the Sell order and WAIT for the true fill price
        order = await self.poly.place_order(
            "sell", pos["token_id"],
            pos["stake_usdc"], exit_odds, self.bot_id,
            paper=self.paper_trading
        )
        
        # Use the actual filled price from the exchange (or fallback to theoretical)
        true_exit_price = order.get("filled_price", exit_odds)
        clob_id = order.get("order_id", "paper_only")
        
        # 2. Log the local exit (resolved=1)
        pnl, outcome = self.db.log_exit(trade_id, {
            "ts_exit":        datetime.utcnow().isoformat(),
            "entry_odds":     pos["entry_odds"],
            "exit_odds":      true_exit_price,
            "peak_odds":      pos["peak_odds"],
            "stake_usdc":     pos["stake_usdc"],
            "exit_reason":    reason,
            "chainlink_close": None,
        })
        
        # 3. Create the TRUTH record in the settlements table
        # For paper trading, we assume 0 slippage for now.
        slippage = int((exit_odds - true_exit_price) * 10000) if exit_odds > true_exit_price else 0
        self.db.log_settlement(
            trade_id=trade_id,
            clob_order_id=clob_id,
            tx_hash=order.get("tx_hash", "paper_tx"),
            usdc_returned=pos["stake_usdc"] + pnl,
            slippage_bps=slippage
        )

        # 4. Finalize Bankroll and risk counters
        self.bankroll.settle(pos["stake_usdc"], pnl)
        self.cb.on_result(self.db, outcome, pnl, self.starting_bankroll)
        
        if trade_id in self._positions:
            del self._positions[trade_id]

        logger.info(
            "[Bot%s] EXIT | id=%s reason=%s odds=%.3f pnl=%+.4f outcome=%s | SETTLED=%s",
            self.bot_id, trade_id, reason, true_exit_price, pnl, outcome, clob_id
        )