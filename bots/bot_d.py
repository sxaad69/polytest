"""
Bot D — Sports Spike
Monitors live sports markets (NFL, NBA, soccer, UFC, etc.)
for sudden odds velocity spikes caused by in-game events.
Default strategy: FADE the spike (mean-reversion), as data shows
sports markets over-react to scoring events and then correct.
"""

import asyncio
import logging
import time
import fnmatch
from datetime import datetime
from config import (
    BOT_D_BANKROLL, BOT_D_DB_PATH,
    BOT_D_SPIKE_THRESHOLD, BOT_D_FADE_ENABLED,
    BOT_D_MARKET_PATTERNS, NO_ENTRY_LAST_SECS
)
from bots.base_bot import BaseBot
from signals.signal_d import BotDSignal

logger = logging.getLogger("bot_d")


class BotD(BaseBot):

    BOT_ID            = "D"
    DB_PATH           = BOT_D_DB_PATH
    STARTING_BANKROLL = BOT_D_BANKROLL

    def __init__(self, binance, chainlink, poly):
        super().__init__(binance, chainlink, poly)
        self._signal = BotDSignal(
            spike_threshold=BOT_D_SPIKE_THRESHOLD,
            fade_enabled=BOT_D_FADE_ENABLED,
        )
        self.max_concurrent_trades = 5  # Sports can have many parallel games

    async def _loop(self):
        """Custom loop: scan all sports markets every 5s for spikes."""
        self._log.info("Bot D starting sports spike monitor | mode=%s",
                       "FADE" if BOT_D_FADE_ENABLED else "RIDE")

        while self._running:
            try:
                # 1. Discover sports markets from all configured patterns
                for pattern in BOT_D_MARKET_PATTERNS:
                    await self.poly.fetch_markets_by_pattern(pattern)

                # 2. Scan every registered market for a spike
                for tid, m in list(self.poly.markets.items()):
                    if len(self.executor._positions) >= self.max_concurrent_trades:
                        break
                    await self._evaluate_market(tid, m)

            except Exception as e:
                self._log.error("Bot D loop error: %s", e, exc_info=True)

            await asyncio.sleep(5)  # Tight 5s tick to catch live spikes

    async def _evaluate_market(self, tid: str, m: dict):
        # Skip if already in this token
        for pos in self.executor._positions.values():
            if pos["token_id"] == tid:
                return

        market_id = m.get("condition_id")
        secs_remaining = m.get("win_end", 0) - time.time()
        if secs_remaining < NO_ENTRY_LAST_SECS:
            return

        current_price = m.get("odds")
        velocity = m.get("velocity", 0.0)
        if not current_price:
            return

        # Evaluate spike
        result = self._signal.evaluate(
            market_id=market_id,
            token_id=tid,
            velocity=velocity,
            current_price=current_price,
        )

        if not result.tradeable:
            return

        # For FADE mode: if spike was UP, we go SHORT (buy complementary NO token)
        # Map to a token to actually buy
        trade_token_id = tid
        trade_odds = current_price

        if result.direction == "short":
            peer_id = m.get("peer_id")
            if peer_id and peer_id in self.poly.markets:
                trade_token_id = peer_id
                trade_odds = self.poly.markets[peer_id].get("odds")
            else:
                return  # Can't fade without peer token

        if not trade_odds:
            return

        passed, reason = self.filters.check(
            db=self.db,
            confidence=result.score,
            odds=trade_odds,
            depth=m.get("depth", 0),
            secs_remaining=secs_remaining,
            market_id=market_id,
        )

        if not passed:
            return

        stake = self.sizer.calculate(result.score, trade_odds, self.bankroll.available)
        if stake <= 5.0:
            return

        mode = "FADE" if (result.direction != ("long" if velocity > 0 else "short")) else "RIDE"
        self._log.info(
            "[BotD] SPIKE %s | market=%s velocity=%.4f score=%.2f odds=%.3f stake=%.2f",
            mode, (market_id or "?")[:12], velocity, result.score, trade_odds, stake
        )

        signal_id = self.db.log_signal({
            "ts": datetime.utcnow().isoformat(),
            "market_id": market_id,
            "direction": result.direction,
            "confidence_score": result.score,
            "polymarket_odds": trade_odds,
            "odds_velocity": velocity,
            "skip_reason": None,
            "features": result.components,
        })

        await self.executor.enter(
            "long", result.score, stake, signal_id,
            token_id=trade_token_id,
            entry_odds=trade_odds,
            market_id=market_id,
            win_end=m.get("win_end"),
        )

    def evaluate_signal(self):
        return None
