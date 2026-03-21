"""
Bot C — GLOB Arbitrage
Monitors all discovered markets for YES + NO < 0.985 opportunities.
Executes simultaneous buy-both orders and holds until settlement.
"""

import asyncio
import logging
import time
from datetime import datetime
from config import (
    BOT_C_BANKROLL, BOT_C_DB_PATH, BOT_C_ENABLED, 
    MAX_BET_PCT, MIN_ODDS, MAX_ODDS, ARB_THRESHOLD,
    NO_ENTRY_LAST_SECS
)
from bots.base_bot import BaseBot
from signals.signal_c import BotCSignal
from utils.pm_math import calculate_vwap

logger = logging.getLogger("bot_c")

class BotC(BaseBot):

    BOT_ID            = "C"
    DB_PATH           = BOT_C_DB_PATH
    STARTING_BANKROLL = BOT_C_BANKROLL

    def __init__(self, binance, chainlink, poly):
        super().__init__(binance, chainlink, poly)
        self._signal = BotCSignal(arb_threshold=getattr(self, 'ARB_THRESHOLD', 0.985))
        self.processed_markets = set()

    async def _loop(self):
        """Custom loop for Bot C to monitor multiple markets."""
        self._log.info("Bot C starting multi-market monitor...")
        
        while self._running:
            try:
                # 1. Discover/Update markets (pattern based)
                # In a real environment, we'd use a specific pattern like 'btc-updown-*' or '*'
                pattern = "*" # Monitor all discovered
                await self.poly.fetch_markets_by_pattern(pattern)
                
                # 2. Iterate and evaluate
                for tid, m in list(self.poly.markets.items()):
                    # Only check if this is the 'primary' side of a pair to avoid double checking
                    # (Each pair has two tokens, we only need to check the pair once)
                    peer_id = m.get("peer_id")
                    if not peer_id or tid > peer_id: 
                        continue
                        
                    await self._evaluate_market(tid, peer_id, m)
                    
            except Exception as e:
                self._log.error("Bot C loop error: %s", e, exc_info=True)
            
            await asyncio.sleep(15) # Slower poll for Arb discovery

    async def _evaluate_market(self, tid_yes: str, tid_no: str, m_yes: dict):
        m_no = self.poly.markets.get(tid_no)
        if not m_no: return

        # Skip if already traded in this window/market
        market_id = m_yes.get("condition_id")
        if market_id in self.processed_markets: 
            return

        # Timing check - don't enter near close
        secs_remaining = m_yes.get("win_end", 0) - time.time()
        if secs_remaining < NO_ENTRY_LAST_SECS:
            return

        # 1. Fetch deep books for both
        await asyncio.gather(
            self.poly.fetch_book(tid_yes),
            self.poly.fetch_book(tid_no)
        )

        # 2. Calculate VWAP for a test stake (e.g. 10.0)
        target_stake = 10.0
        yes_vwap = calculate_vwap(m_yes.get("asks", []), target_stake/2)
        no_vwap = calculate_vwap(m_no.get("asks", []), target_stake/2)

        # 3. Evaluate Signal
        result = self._signal.evaluate(
            market_id=market_id,
            token_yes=tid_yes,
            token_no=tid_no,
            yes_vwap=yes_vwap,
            no_vwap=no_vwap
        )

        # 4. Check Global Filters
        passed, reason = self.filters.check(
            db=self.db,
            confidence=result.score,
            odds=result.sum_price / 2, # Average odds for filtering
            depth=m_yes.get("depth", 0) + m_no.get("depth", 0),
            secs_remaining=secs_remaining,
            market_id=market_id
        )

        if not passed or not result.tradeable:
            return

        # 5. Kelly Sizing (simplified for Arb)
        # For Arb, we can use a higher fixed percentage as it's "risk-free"
        stake = min(self.bankroll.available * 0.10, 50.0) # Cap at 50 USDC for safety
        
        # 6. Execute Execute Arb (Two market orders)
        self._log.info("[BotC] ARB FOUND | market=%s yes=%.3f no=%.3f sum=%.3f stake=%.2f",
                       market_id[:10], yes_vwap, no_vwap, result.sum_price, stake)
        
        await self._enter_arb(tid_yes, tid_no, stake/2, yes_vwap, no_vwap, result)
        self.processed_markets.add(market_id)

    async def _enter_arb(self, tid_yes, tid_no, stake_per_leg, price_yes, price_no, result):
        # Place both orders
        success_yes = await self.poly.place_order("long", tid_yes, stake_per_leg, price_yes, "C", paper=self.executor.paper_trading)
        success_no = await self.poly.place_order("long", tid_no, stake_per_leg, price_no, "C", paper=self.executor.paper_trading)

        if success_yes.get("status") == "filled" and success_no.get("status") == "filled":
            # Log as a single "arb" trade
            self.db.log_entry({
                "ts_entry": datetime.utcnow().isoformat(),
                "market_id": result.market_id,
                "direction": "arb",
                "entry_odds": result.sum_price,
                "stake_usdc": stake_per_leg * 2,
                "confidence_score": result.score,
            })
            self.bankroll.reserve(stake_per_leg * 2)
            self._log.info("[BotC] ARB EXECUTED | Locked in %.2f%% profit", (1.0 - result.sum_price)*100)
        else:
            self._log.error("[BotC] ARB FAILED | Yes:%s No:%s", 
                            success_yes.get("status"), success_no.get("status"))

    def evaluate_signal(self):
        # Not used by custom loop but needed for BaseBot abstract parity
        return None
