"""
Polymarket Dual Bot — Main Orchestrator
Launches Bot A and Bot B as independent parallel async tasks.
Shared feeds, independent decisions, independent databases.
"""

import asyncio
import logging
import signal
import sys
from config import (
    PAPER_TRADING, BOT_A_ENABLED, BOT_B_ENABLED,
    LIVE_CONFLICT_RULE, LOG_LEVEL,
    BOT_A_BANKROLL, BOT_B_BANKROLL, validate,
)
from feeds.binance_ws import BinanceFeed
from feeds.chainlink import ChainlinkFeed
from feeds.polymarket import PolymarketFeed
from bots.bot_a import BotA
from bots.bot_b import BotB
from analytics.comparison import print_comparison

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


class Orchestrator:

    def __init__(self):
        self.binance   = BinanceFeed()
        self.chainlink = None
        self.poly      = PolymarketFeed()
        self.bot_a     = None
        self.bot_b     = None
        self._running  = False

    async def run(self):
        logger.info("=" * 60)
        logger.info("Polymarket Dual Bot | mode=%s",
                    "PAPER" if PAPER_TRADING else "LIVE")
        logger.info("Bot A (Lag): %s | Bot B (Hybrid): %s",
                    "ON" if BOT_A_ENABLED else "OFF",
                    "ON" if BOT_B_ENABLED else "OFF")
        if not PAPER_TRADING:
            logger.info("Live conflict rule: %s", LIVE_CONFLICT_RULE)
        logger.info("=" * 60)

        if not BOT_A_ENABLED and not BOT_B_ENABLED:
            logger.error("Both bots disabled in config. Enable at least one.")
            return

        self.chainlink = ChainlinkFeed(self.binance)

        async with self.poly:
            if BOT_A_ENABLED:
                self.bot_a = BotA(self.binance, self.chainlink, self.poly)
            if BOT_B_ENABLED:
                self.bot_b = BotB(self.binance, self.chainlink, self.poly)

            tasks = [
                asyncio.create_task(self.binance.start(),              name="binance_ws"),
                asyncio.create_task(self.chainlink.start(),            name="chainlink"),
                asyncio.create_task(self.poly.start_odds_stream(),     name="poly_ws"),
            ]
            if BOT_A_ENABLED:
                tasks.append(asyncio.create_task(self.bot_a.run(), name="bot_a"))
            if BOT_B_ENABLED:
                tasks.append(asyncio.create_task(self.bot_b.run(), name="bot_b"))

            if not PAPER_TRADING and BOT_A_ENABLED and BOT_B_ENABLED:
                tasks.append(asyncio.create_task(
                    self._conflict_monitor(), name="conflict_monitor"
                ))

            self._running = True
            try:
                await asyncio.gather(*tasks)
            except asyncio.CancelledError:
                logger.info("Shutdown signal received")
            except Exception as e:
                logger.error("Fatal: %s", e, exc_info=True)
            finally:
                await self._shutdown(tasks)

    async def _conflict_monitor(self):
        """Live mode only: monitors for both bots signalling same window."""
        logger.info("Live conflict monitor active | rule=%s", LIVE_CONFLICT_RULE)
        while self._running:
            await asyncio.sleep(1)

    def resolve_conflict(self, score_a: float, score_b: float) -> str:
        if LIVE_CONFLICT_RULE == "higher_confidence":
            return "A" if abs(score_a) >= abs(score_b) else "B"
        elif LIVE_CONFLICT_RULE == "bot_a_priority":
            return "A"
        elif LIVE_CONFLICT_RULE == "bot_b_priority":
            return "B"
        elif LIVE_CONFLICT_RULE == "no_trade":
            return "none"
        return "A"

    async def _shutdown(self, tasks):
        logger.info("Shutting down...")
        self._running = False
        self.binance.stop()
        self.chainlink.stop()
        if self.bot_a:
            self.bot_a.stop()
        if self.bot_b:
            self.bot_b.stop()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        bal_a = self.bot_a.bankroll.balance if self.bot_a else BOT_A_BANKROLL
        bal_b = self.bot_b.bankroll.balance if self.bot_b else BOT_B_BANKROLL
        print_comparison(bal_a, bal_b)
        logger.info("Shutdown complete")


def main():
    validate()
    orch = Orchestrator()

    def _handle(sig, frame):
        logger.info("Interrupt received")
        sys.exit(0)

    signal.signal(signal.SIGINT,  _handle)
    signal.signal(signal.SIGTERM, _handle)
    asyncio.run(orch.run())


if __name__ == "__main__":
    main()
