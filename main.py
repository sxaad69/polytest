"""
Polymarket Multi-Bot — Main Orchestrator
Launches up to 7 bots as independent parallel async tasks.
Shared feeds, independent decisions, independent databases.
"""

import asyncio
import logging
import signal
import sys
from config import (
    PAPER_TRADING, LIVE_CONFLICT_RULE, LOG_LEVEL, 
    BOT_A_ENABLED, BOT_B_ENABLED, BOT_C_ENABLED, BOT_D_ENABLED, 
    BOT_E_ENABLED, BOT_F_ENABLED, BOT_G_ENABLED,
    BOT_A_BANKROLL, BOT_B_BANKROLL, BOT_C_BANKROLL, BOT_D_BANKROLL,
    BOT_E_BANKROLL, BOT_F_BANKROLL, BOT_G_BANKROLL, validate,
)
from feeds.binance_ws import BinanceFeed
from feeds.chainlink import ChainlinkFeed
from feeds.polymarket import PolymarketFeed
from bots.bot_a import BotA
from bots.bot_b import BotB
from bots.bot_c import BotC
from bots.bot_d import BotD
from bots.bot_e import BotE
from execution.redeemer import Redeemer
from risk.manager import GlobalRiskManager
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
        self.bots      = {}      # bot_id -> instance
        self.bot_tasks = []      # list of asyncio tasks
        self._running  = False

    async def run(self):
        logger.info("=" * 60)
        logger.info("Polymarket Orchestrator | mode=%s",
                    "PAPER" if PAPER_TRADING else "LIVE")
        
        # Bot Registry: (BotClass, enabled_flag, bot_id)
        # Note: Bots C-G will be added here as they are implemented
        registry = [
            (BotA, BOT_A_ENABLED, "A"),
            (BotB, BOT_B_ENABLED, "B"),
            (BotC, BOT_C_ENABLED, "C"),
            (BotD, BOT_D_ENABLED, "D"),
            (BotE, BOT_E_ENABLED, "E"),
        ]
        
        active_registry = [r for r in registry if r[1]]
        if not active_registry:
            logger.error("No bots enabled in config. Enable at least one.")
            return

        enabled_str = ", ".join([f"Bot {r[2]}" for r in active_registry])
        logger.info("Active Bots: %s", enabled_str)
        if not PAPER_TRADING:
            logger.info("Live conflict rule: %s", LIVE_CONFLICT_RULE)
        logger.info("=" * 60)

        self.chainlink = ChainlinkFeed(self.binance)

        async with self.poly:
            # Instantiate active bots
            for bot_class, _, bot_id in active_registry:
                self.bots[bot_id] = bot_class(self.binance, self.chainlink, self.poly)

            tasks = [
                asyncio.create_task(self.binance.start(),          name="binance_ws"),
                asyncio.create_task(self.chainlink.start(),        name="chainlink"),
                asyncio.create_task(self.poly.start_odds_stream(), name="poly_ws"),
            ]
            
            # Start bot main loops
            for bot_id, bot_instance in self.bots.items():
                t = asyncio.create_task(bot_instance.run(), name=f"bot_{bot_id.lower()}")
                self.bot_tasks.append(t)
                tasks.append(t)

            if not PAPER_TRADING and len(self.bots) >= 2:
                tasks.append(asyncio.create_task(
                    self._conflict_monitor(), name="conflict_monitor"
                ))
            
            # Global Health Monitor
            tasks.append(asyncio.create_task(self._health_monitor(), name="health_monitor"))

            self._running = True
            try:
                await asyncio.gather(*tasks)
            except asyncio.CancelledError:
                logger.info("Shutdown signal received")
            except Exception as e:
                logger.error("Fatal: %s", e, exc_info=True)
            finally:
                await self._shutdown(tasks)

    async def _health_monitor(self):
        """Monitors global circuit breaker across all bots."""
        logger.info("Global risk monitor active")
        while self._running:
            if not GlobalRiskManager.check_health(self.bots):
                logger.critical("GLOBAL HALT TRIGGERED — SHUTTING DOWN")
                self._running = False
                # Trigger clean shutdown by throwing a custom exception or just stopping
                break
            await asyncio.sleep(10)

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
        
        # 1. STOP FEEDS
        if self.binance:
            self.binance.stop()
        if self.chainlink:
            self.chainlink.stop()
        
        # 2. STOP BOTS
        for bot in self.bots.values():
            bot.stop()
            
        # 3. CANCEL TASKS
        for t in tasks:
            if not t.done():
                t.cancel()
        
        await asyncio.gather(*tasks, return_exceptions=True)
        
        # 4. REDEEM WINNINGS (Live only)
        if not PAPER_TRADING:
            logger.info("Starting post-session redemption...")
            redeemer = Redeemer()
            for bid, bot in self.bots.items():
                unredeemed = bot.db.get_unredeemed_wins()
                if unredeemed:
                    logger.info("[Bot %s] Found %d unredeemed wins", bid, len(unredeemed))
                    for trade in unredeemed:
                        success = redeemer.redeem(
                            trade["market_condition_id"], 
                            [trade["outcome_index"]]
                        )
                        if success:
                            bot.db.mark_redeemed(trade["id"])
                    
        # 5. FINAL REPORT
        balances = {bid: b.bankroll.balance for bid, b in self.bots.items()}
        print_comparison(balances)
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
