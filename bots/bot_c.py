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
    BOT_C_BANKROLL, BOT_C_DB_PATH, 
    MAX_BET_PCT, MIN_ODDS, MAX_ODDS, ARB_THRESHOLD,
    NO_ENTRY_LAST_SECS, BOT_C_NO_ENTRY_LAST_SECS
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
        self._signal = BotCSignal(arb_threshold=ARB_THRESHOLD)
        self.max_concurrent_trades = 4
        self.processed_markets = {} # condition_id -> win_end

    async def _loop(self):
        """Custom loop for Bot C to monitor multiple markets."""
        self._log.info("Bot C starting multi-market monitor...")
        
        while self._running:
            try:
                import config
                import importlib
                importlib.reload(config)
                import fnmatch
                
                # 1. Define the surgical clinical filter
                target_markets = {}
                for tid, m in self.poly.markets.items():
                    slug = m.get("slug", "").lower()
                    
                    # A) Broad Keyword Noise Purge (Price Action, Binary Crypto)
                    if any(kw in slug for kw in getattr(config, "GLOBAL_EXCLUDE_KEYWORDS", [])):
                        continue
                        
                    # B) Bot C Specific: EXCLUDE Sports (violent separation)
                    if any(fnmatch.fnmatch(slug, p) for p in getattr(config, "BOT_D_MARKET_PATTERNS", [])):
                        continue
                    
                    # C) Bot C Specific: Global Patterns (usually '*')
                    is_match = False
                    for p in getattr(config, "BOT_C_MARKET_PATTERNS", ["*"]):
                        if fnmatch.fnmatch(slug, p):
                            is_match = True
                            break
                    if is_match:
                        target_markets[tid] = m

                # 2. Write the .txt log (Title | URL) for the user clinical dashboard
                if getattr(config, "WRITE_SCANNED_MARKETS_TXT", False):
                    entries = []
                    for m in target_markets.values():
                        s = m.get("slug", "")
                        es = m.get("event_slug", s)
                        ss = m.get("series_slug")
                        url = f"https://polymarket.com/sports/{ss}/{es}" if ss else f"https://polymarket.com/event/{es}"
                        title = s.replace("-", " ").title()
                        entries.append(f"{title} | {url}")
                    
                    entries = sorted(list(set(entries)))
                    with open("logs/bot_c_markets.txt", "w") as f:
                        f.write("\n".join(entries))

                # 3. Cleanup and iterate evaluation
                now = time.time()
                self.processed_markets = {cid: end for cid, end in self.processed_markets.items() if end > now}

                for tid, m in list(target_markets.items()):
                    if len(self.executor._positions) >= self.max_concurrent_trades:
                        break
                    
                    peer_id = m.get("peer_id")
                    if not peer_id or tid > peer_id: continue
                    if m.get("condition_id") in self.processed_markets: continue

                    await self._evaluate_market(tid, peer_id, m)
                
                self._log.info("[BotC] 🔍 Scanning %d clinical markets (No Sports/Noise)...", len(target_markets))
            except Exception as e:
                self._log.error("Bot C loop error: %s", e, exc_info=True)
            
            await asyncio.sleep(15) # Slower poll for Arb discovery

    async def _evaluate_market(self, tid_yes: str, tid_no: str, m_yes: dict):
        m_no = self.poly.markets.get(tid_no)
        if not m_no: return

        # Timing check - use Bot C specific timing for arbs
        secs_remaining = m_yes.get("win_end", 0) - time.time()
        if secs_remaining < BOT_C_NO_ENTRY_LAST_SECS:
            return

        # 1. Fetch deep books for both
        await asyncio.gather(
            self.poly.fetch_book(tid_yes),
            self.poly.fetch_book(tid_no)
        )

        # 2. Calculate Stake FIRST
        # This fixes the depth mismatch bug
        stake = min(self.bankroll.available * 0.10, 50.0) 
        if stake <= 5.0: return # Polymarket min

        # 3. Calculate VWAP for the EXACT stake per leg
        yes_vwap = calculate_vwap(m_yes.get("asks", []), stake/2)
        no_vwap = calculate_vwap(m_no.get("asks", []), stake/2)

        # 4. Evaluate Signal
        market_id = m_yes.get("condition_id")
        result = self._signal.evaluate(
            market_id=market_id,
            token_yes=tid_yes,
            token_no=tid_no,
            yes_vwap=yes_vwap,
            no_vwap=no_vwap
        )

        # 5. Check Global Filters
        passed, reason = self.filters.check(
            db=self.db,
            confidence=result.score,
            odds=result.sum_price / 2,
            depth=m_yes.get("depth", 0) + m_no.get("depth", 0),
            secs_remaining=secs_remaining,
            market_id=market_id
        )

        if not passed or not result.tradeable:
            return

        # 6. Execute Arb
        self._log.info("[BotC] ARB FOUND | market=%s yes=%.3f no=%.3f sum=%.3f stake=%.2f",
                       market_id[:10], yes_vwap, no_vwap, result.sum_price, stake)
        
        await self._enter_arb(tid_yes, tid_no, stake/2, yes_vwap, no_vwap, result)
        self.processed_markets[market_id] = m_yes.get("win_end", 0)

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
            # PARTIAL FILL ROLLBACK (Crude): If one filled, try to sell it
            if success_yes.get("status") == "filled" or success_no.get("status") == "filled":
                self._log.critical("[BotC] PARTIAL FILL DETECTED — RISK EXPOSED")
                # In live mode we should fire off a sell order immediately
                # In paper mode we just log the imbalance
                failed_leg = "NO" if success_yes.get("status") == "filled" else "YES"
                self._log.error("[BotC] Leg 1 filled, Leg 2 (%s) failed. Trade is UNHEDGED.", failed_leg)

    def evaluate_signal(self):
        # Not used by custom loop but needed for BaseBot abstract parity
        return None
