"""
Bot G — Universal Crypto (Multi-Asset Updown)
Scans all crypto updown markets (ETH, SOL, BNB, etc.)
and applies Binance momentum + Chainlink deviation logic.
Same core edge as Bot A but generalised to all crypto assets.
"""

import asyncio
import logging
import time
from datetime import datetime
from config import (
    BOT_G_BANKROLL, BOT_G_DB_PATH,
    BOT_G_MARKET_PATTERN, NO_ENTRY_LAST_SECS
)
from bots.base_bot import BaseBot
from signals.signal_g import BotGSignal

logger = logging.getLogger("bot_g")


class BotG(BaseBot):

    BOT_ID            = "G"
    DB_PATH           = BOT_G_DB_PATH
    STARTING_BANKROLL = BOT_G_BANKROLL

    def __init__(self, binance, chainlink, poly):
        super().__init__(binance, chainlink, poly)
        self._signal = BotGSignal()
        self.max_concurrent_trades = 4

    async def _loop(self):
        self._log.info("Bot G starting multi-crypto monitor...")

        while self._running:
            try:
                # 1. Discover all crypto updown markets
                await self.poly.fetch_markets_by_pattern(BOT_G_MARKET_PATTERN)

                # 2. Evaluate each market
                for tid, m in list(self.poly.markets.items()):
                    if len(self.executor._positions) >= self.max_concurrent_trades:
                        break
                    await self._evaluate_market(tid, m)

            except Exception as e:
                self._log.error("Bot G loop error: %s", e, exc_info=True)

            await asyncio.sleep(10)

    async def _evaluate_market(self, tid: str, m: dict):
        # Skip if already in this token
        for pos in self.executor._positions.values():
            if pos["token_id"] == tid:
                return

        market_id = m.get("condition_id")
        slug = m.get("slug", "")
        secs_remaining = m.get("win_end", 0) - time.time()
        if secs_remaining < NO_ENTRY_LAST_SECS:
            return

        current_price = m.get("odds")
        if not current_price:
            return

        # Infer asset from slug (e.g. "eth-updown-5m-XXX" -> "ETH")
        asset = slug.split("-")[0].upper() if slug else "CRYPTO"

        # Use BTC momentum/chainlink as proxy for correlated crypto assets
        # TODO: wire per-asset feeds when ETH/SOL Binance streams are added
        momentum  = self.binance.momentum_30s or 0.0
        cl_dev    = self.chainlink.deviation_pct / 100.0  # fractional

        result = self._signal.evaluate(
            asset=asset,
            momentum=momentum,
            chainlink_deviation=cl_dev,
        )

        if not result.tradeable:
            return

        # Map direction → token
        if result.direction == "long":
            trade_token_id = tid
            trade_odds = current_price
        else:
            peer_id = m.get("peer_id")
            if peer_id and peer_id in self.poly.markets:
                trade_token_id = peer_id
                trade_odds = self.poly.markets[peer_id].get("odds")
            else:
                return

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

        self._log.info("[BotG] CRYPTO SIGNAL | asset=%s dir=%s score=%.4f odds=%.3f",
                       asset, result.direction, result.score, trade_odds)

        signal_id = self.db.log_signal({
            "ts": datetime.utcnow().isoformat(),
            "market_id": market_id,
            "direction": result.direction,
            "confidence_score": result.score,
            "polymarket_odds": trade_odds,
            "odds_velocity": m.get("velocity", 0.0),
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
