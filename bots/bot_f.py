"""
Bot F — Copytrade (Market Slug Mirroring)
Monitors historically accurate market slugs and mirrors
the current crowd bias (buy what the market favours).
"""

import asyncio
import logging
import time
from datetime import datetime
from config import (
    BOT_F_BANKROLL, BOT_F_DB_PATH,
    BOT_F_ACCURACY_THRESHOLD, BOT_F_MIN_SAMPLES,
    BOT_F_MARKET_PATTERNS, NO_ENTRY_LAST_SECS
)
from bots.base_bot import BaseBot
from signals.signal_f import BotFSignal

logger = logging.getLogger("bot_f")


class BotF(BaseBot):

    BOT_ID            = "F"
    DB_PATH           = BOT_F_DB_PATH
    STARTING_BANKROLL = BOT_F_BANKROLL

    def __init__(self, binance, chainlink, poly):
        super().__init__(binance, chainlink, poly)
        self._signal = BotFSignal(
            accuracy_threshold=BOT_F_ACCURACY_THRESHOLD,
            min_samples=BOT_F_MIN_SAMPLES,
        )
        # Cache of slug -> (accuracy, samples) from DB
        self._slug_stats: dict = {}
        self.max_concurrent_trades = 4

    async def _loop(self):
        self._log.info("Bot F starting copytrade monitor...")

        while self._running:
            try:
                import config
                import importlib
                importlib.reload(config)
                import fnmatch
                
                # 1. Define the clinical copytrade filter
                target_markets = {}
                _sample_slugs = []
                for tid, m in self.poly.markets.items():
                    slug = m.get("slug", "").lower()
                    if len(_sample_slugs) < 10: _sample_slugs.append(slug)
                    
                    # A) Broad Keyword Noise Purge (Price Action, Binary Crypto)
                    if any(kw in slug for kw in getattr(config, "GLOBAL_EXCLUDE_KEYWORDS", [])):
                        continue
                    
                    # B) Bot F Specific: Pattern Match (Financial/Volume)
                    is_match = False
                    for p in getattr(config, "BOT_F_MARKET_PATTERNS", []):
                        if fnmatch.fnmatch(slug, p):
                            is_match = True
                            break
                    if is_match:
                        target_markets[tid] = m

                # 4. Heartbeat log
                self._log.info("[BotF] 🔍 Scanning %d filtered markets. Samples: %s", len(target_markets), _sample_slugs[:3])

                # 2. Write the .txt log (Title | URL) for the user dashboard
                if getattr(config, "WRITE_SCANNED_MARKETS_TXT", False):
                    entries = []
                    for m in target_markets.values():
                        s = m.get("slug", "")
                        es = m.get("event_slug", s)
                        ss = m.get("series_slug")
                        url = f"https://polymarket.com/sports/{ss}/{es}" if ss else f"https://polymarket.com/event/{es}"
                        font_title = s.replace("-", " ").title()
                        entries.append(f"{font_title} | {url}")
                    
                    entries = sorted(list(set(entries)))
                    with open("logs/bot_f_markets.txt", "w") as f:
                        f.write("\n".join(entries))

                # 3. Refresh slug accuracy stats from our DB
                self._slug_stats = self.db.get_slug_accuracies() or {}

                # 4. Heartbeat log
                self._log.info("[BotF] 🔍 Scanning %d filtered markets for Copytrade Bias...", len(target_markets))
                
                # 5. Evaluate
                for tid, m in list(target_markets.items()):
                    if len(self.executor._positions) >= self.max_concurrent_trades:
                        break
                    await self._evaluate_market(tid, m)

            except Exception as e:
                self._log.error("Bot F loop error: %s", e, exc_info=True)

            await asyncio.sleep(20)  # Slower cadence — based on resolution stats

    async def _evaluate_market(self, tid: str, m: dict):
        # Skip if already in position
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

        # Look up historical accuracy for this slug type
        # Strip timestamps from slug to get its "type", e.g. btc-updown-5m-XXXXXXX -> btc-updown-5m
        slug_type = "-".join(slug.split("-")[:-1]) if slug else slug
        slug_stat = self._slug_stats.get(slug_type, {})
        accuracy = slug_stat.get("accuracy", 0.0)
        samples  = slug_stat.get("samples", 0)

        result = self._signal.evaluate(
            market_id=market_id,
            token_id=tid,
            current_price=current_price,
            slug_accuracy=accuracy,
            slug_samples=samples,
        )

        if not result.tradeable:
            return

        # Map direction to token
        trade_token_id = tid
        trade_odds = current_price
        if result.direction == "short":
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

        self._log.info("[BotF] COPYTRADE | slug=%s acc=%.2f dir=%s score=%.2f",
                       slug_type, accuracy, result.direction, result.score)

        signal_id = self.db.log_signal({
            "ts": datetime.utcnow().isoformat(),
            "market_id": market_id,
            "direction": result.direction,
            "confidence_score": result.score,
            "polymarket_odds": trade_odds,
            "skip_reason": None,
            "features": result.components,
        })

        await self.executor.enter(
            result.direction, result.score, stake, signal_id,
            token_id=trade_token_id,
            entry_odds=trade_odds,
            market_id=market_id,
            win_end=m.get("win_end"),
        )

    def evaluate_signal(self):
        return None
