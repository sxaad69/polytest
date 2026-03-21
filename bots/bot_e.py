"""
Bot E — Adaptive Momentum
Monitors momentum across all discovered markets.
Trades on price velocity pulse (5m/15m).
"""

import asyncio
import logging
import time
from datetime import datetime
from config import (
    BOT_E_BANKROLL, BOT_E_DB_PATH, 
    MAX_BET_PCT, MIN_ODDS, MAX_ODDS, NO_ENTRY_LAST_SECS,
    BOT_E_MIN_VELOCITY
)
from bots.base_bot import BaseBot
from signals.signal_e import BotESignal

logger = logging.getLogger("bot_e")

class BotE(BaseBot):

    BOT_ID            = "E"
    DB_PATH           = BOT_E_DB_PATH
    STARTING_BANKROLL = BOT_E_BANKROLL

    def __init__(self, binance, chainlink, poly):
        super().__init__(binance, chainlink, poly)
        self._signal = BotESignal(min_velocity=BOT_E_MIN_VELOCITY)
        self.max_concurrent_trades = 3

    async def _loop(self):
        """Custom loop for Bot E to monitor multiple markets."""
        self._log.info("Bot E starting momentum monitor...")
        
        while self._running:
            try:
                # 1. Discover all active markets
                pattern = "*" 
                await self.poly.fetch_markets_by_pattern(pattern)
                
                # 2. Iterate and evaluate
                for tid, m in list(self.poly.markets.items()):
                    # Limit number of active positions to avoid over-trading
                    if len(self.executor._positions) >= self.max_concurrent_trades:
                        break
                        
                    await self._evaluate_market(tid, m)
                    
            except Exception as e:
                self._log.error("Bot E loop error: %s", e, exc_info=True)
            
            await asyncio.sleep(10) # 10s tick for capturing trends

    async def _evaluate_market(self, tid: str, m: dict):
        # Skip if already in this exact token
        for pos in self.executor._positions.values():
            if pos["token_id"] == tid:
                return

        # Condition / Timing check
        market_id = m.get("condition_id")
        secs_remaining = m.get("win_end", 0) - time.time()
        if secs_remaining < NO_ENTRY_LAST_SECS:
            return

        # 1. Evaluate Signal
        current_price = m.get("odds")
        velocity = m.get("velocity", 0.0)
        
        if not current_price: return

        result = self._signal.evaluate(
            market_id=market_id,
            token_id=tid,
            velocity=velocity,
            current_price=current_price
        )

        # 2. Check Global Filters
        # Note: direction 'short' for momentum on UP token = 'long' on DOWN token
        trade_token_id = tid
        trade_odds = current_price
        
        if result.direction == "short":
            # Map momentum short-sell to a buy on the complementary token
            peer_id = m.get("peer_id")
            if peer_id and peer_id in self.poly.markets:
                trade_token_id = peer_id
                trade_odds = self.poly.markets[peer_id].get("odds")
            else:
                return # Can't trade short without peer token

        if not trade_odds: return

        passed, reason = self.filters.check(
            db             = self.db,
            confidence     = result.score,
            odds           = trade_odds,
            depth          = m.get("depth", 0),
            secs_remaining = secs_remaining,
            market_id      = market_id
        )

        # 3. Execution
        if not passed or not result.tradeable:
            # We don't log ALL skips to avoid excessive noise in multi-bot logs,
            # but we log to the DB via Filters.check()
            return

        stake = self.sizer.calculate(result.score, trade_odds, self.bankroll.available)
        if stake <= 5.0: return # Polymarket min

        self._log.info("[BotE] MOMENTUM PULSE | market=%s dir=%s velocity=%.4f score=%.2f",
                       market_id[:10], result.direction, velocity, result.score)
        
        # Log signal to db then enter
        signal_id = self.db.log_signal({
            "ts": datetime.utcnow().isoformat(),
            "market_id": market_id,
            "direction": "long", # Buying the trend token (even if it's the 'short' side)
            "confidence_score": result.score,
            "polymarket_odds": trade_odds,
            "odds_velocity": velocity,
            "skip_reason": None,
            "features": result.components
        })
        
        await self.executor.enter("long", result.score, stake, signal_id, 
                                  token_id=trade_token_id, 
                                  entry_odds=trade_odds,
                                  market_id=market_id,
                                  win_end=m.get("win_end"))

    def evaluate_signal(self):
        return None
