"""
Base Bot — shared loop logic.
Both Bot A and Bot B inherit from this.
Subclasses only implement evaluate_signal().
"""

import asyncio
import logging
from datetime import datetime
from config import PAPER_TRADING, NO_ENTRY_FIRST_SECS, BOT_A_PAPER_TRADING, BOT_B_PAPER_TRADING
from database.db import Database
from feeds.polymarket import PolymarketFeed
from feeds.binance_ws import BinanceFeed
from feeds.chainlink import ChainlinkFeed
from risk.manager import PreTradeFilters, CircuitBreaker, KellySizer
from execution.trader import ExecutionLayer, BankrollTracker

logger = logging.getLogger(__name__)


class BaseBot:

    BOT_ID            = "BASE"
    DB_PATH           = None
    STARTING_BANKROLL = 100.0

    def __init__(self, binance: BinanceFeed, chainlink: ChainlinkFeed,
                 poly: PolymarketFeed):
        self.binance   = binance
        self.chainlink = chainlink
        self.poly      = poly
        self.db        = Database(self.DB_PATH, self.BOT_ID)
        self.filters   = PreTradeFilters()
        self.cb        = CircuitBreaker()
        self.sizer     = KellySizer()
        self.bankroll  = BankrollTracker(self.STARTING_BANKROLL)
        # Determine paper mode for this specific bot
        import config
        paper_map = {
            "A": config.BOT_A_PAPER_TRADING,
            "B": config.BOT_B_PAPER_TRADING,
            "C": config.BOT_C_PAPER_TRADING,
            "D": config.BOT_D_PAPER_TRADING,
            "E": config.BOT_E_PAPER_TRADING,
            "F": config.BOT_F_PAPER_TRADING,
            "G": config.BOT_G_PAPER_TRADING,
        }
        paper = paper_map.get(self.BOT_ID, True)

        self.executor  = ExecutionLayer(
            self.BOT_ID, self.db, self.poly,
            self.cb, self.bankroll, self.STARTING_BANKROLL,
            paper_trading=paper
        )
        self._running  = False
        self._log      = logging.getLogger(f"bot_{self.BOT_ID.lower()}")

    async def run(self):
        self._running = True
        self._log.info("Bot %s starting | mode=%s bankroll=%.2f",
                       self.BOT_ID,
                       "PAPER" if self.executor.paper_trading else "LIVE",
                       self.STARTING_BANKROLL)
        tasks = [
            asyncio.create_task(self.executor.start_monitor(),
                                name=f"bot_{self.BOT_ID}_monitor"),
            asyncio.create_task(self._loop(),
                                name=f"bot_{self.BOT_ID}_loop"),
        ]
        await asyncio.gather(*tasks)

    async def _loop(self):
        await asyncio.sleep(12)
        _tick_count = 0
        while self._running:
            try:
                import config
                import importlib
                importlib.reload(config)
                
                # Clinical Report: Write target market to .txt (Title | URL)
                if getattr(config, "WRITE_SCANNED_MARKETS_TXT", False):
                    mid = getattr(self.poly, "market_id", None)
                    if mid:
                        s = getattr(self.poly, "slug", str(mid))
                        es = getattr(self.poly, "event_slug", s)
                        ss = getattr(self.poly, "series_slug", None)
                        
                        url = f"https://polymarket.com/sports/{ss}/{es}" if ss else f"https://polymarket.com/event/{es}"
                        title = s.replace("-", " ").title()
                        
                        with open(f"logs/bot_{self.BOT_ID.lower()}_markets.txt", "w") as f:
                            f.write(f"{title} | {url}")

                await self._tick()
                _tick_count += 1
                # Heartbeat every 60 seconds (every 6 ticks at 10s interval)
                if _tick_count % 6 == 0:
                    self._log_heartbeat()
            except Exception as e:
                self._log.error("Loop error: %s", e, exc_info=True)
            await asyncio.sleep(10)

    def _log_heartbeat(self):
        """Print bot state every 60s so you always know what's happening."""
        cb     = self.db.get_cb()
        halted = cb.get("halted", 0)

        if halted:
            self._log.warning(
                "[Bot%s] ⚠ HALTED | reason=%s | bankroll=%.2f",
                self.BOT_ID, cb.get("halted_reason"), self.bankroll.balance
            )
            return

        if not self.poly.market_id:
            self._log.info(
                "[Bot%s] ⏳ Waiting for market | BTC=%.2f | bankroll=%.2f",
                self.BOT_ID, self.binance.price or 0, self.bankroll.balance
            )
            return

        if not self.poly.up_odds:
            self._log.info(
                "[Bot%s] ⏳ Market found, waiting for odds | ends_in=%.0fs",
                self.BOT_ID, self.poly.seconds_remaining
            )
            return

        self._log.info(
            "[Bot%s] ✓ ACTIVE | mode=%s | BTC=%.2f | CL=%.2f | dev=%.3f%% "
            "| up=%.2f down=%.2f | ends_in=%.0fs | positions=%d | bankroll=%.2f",
            self.BOT_ID,
            "PAPER" if self.executor.paper_trading else "LIVE",
            self.binance.price or 0,
            self.chainlink.price or 0,
            self.chainlink.deviation_pct,
            self.poly.up_odds,
            self.poly.down_odds,
            self.poly.seconds_remaining,
            len(self.executor._positions),
            self.bankroll.balance
        )
        self._log.info(
            "[Bot%s] strategy=%s",
            self.BOT_ID,
            "Chainlink lag only" if self.BOT_ID == "A" else "Hybrid momentum+lag"
        )

    async def _tick(self):
        # Fetch new market when none loaded or current window is expiring
        if not self.poly.market_id or self.poly.seconds_remaining < 15:
            await self.poly.fetch_market()
            return

        # Have market but no odds yet — wait, don't re-fetch
        if not self.poly.up_odds:
            return

        # Don't enter in first 60s — odds not yet formed
        if self.poly.seconds_elapsed < NO_ENTRY_FIRST_SECS:
            return

        token = (self.poly.up_token_id if (self.binance.momentum_30s or 0) >= 0
                 else self.poly.down_token_id)
        if token:
            await self.poly.fetch_book(token)

        result = self.evaluate_signal()
        if result is None:
            return

        trade_odds = (self.poly.up_odds if result.direction == "long"
                      else self.poly.down_odds)

        # 1. Calculate stake BEFORE filter check (required for GlobalRiskManager exposure limits)
        stake = self.sizer.calculate(result.score, trade_odds, self.bankroll.available)
        if stake <= 0:
            return

        # 2. Run filters (now with STAKE and GLOBAL_RISK context)
        passed, reason = self.filters.check(
            db             = self.db,
            confidence     = result.score,
            odds           = trade_odds,
            depth          = self.poly.book_depth,
            secs_remaining = self.poly.seconds_remaining,
            market_id      = self.poly.market_id,
            stake          = stake,
            global_risk    = getattr(self.executor, "global_risk", None)
        )

        signal_id = self._log_signal(result, trade_odds, passed, reason)

        if not passed or not result.tradeable:
            self._log.debug("[Bot%s] skip | reason=%s score=%.3f",
                            self.BOT_ID, reason, result.score)
            return

        self._log.info("[Bot%s] SIGNAL | dir=%s score=%.3f odds=%.3f stake=%.2f",
                       self.BOT_ID, result.direction, result.score, trade_odds, stake)

        await self.executor.enter(result.direction, result.score, stake, signal_id)

    def stop(self):
        self._running = False

    def _log_signal(self, result, trade_odds: float,
                    passed: bool, reason: str) -> int:
        return self.db.log_signal({
            "ts":                datetime.utcnow().isoformat(),
            "market_id":         self.poly.market_id,
            "window_start":      datetime.fromtimestamp(
                                     self.poly.window_start
                                 ).isoformat() if self.poly.window_start else None,
            "window_end":        datetime.fromtimestamp(
                                     self.poly.window_end
                                 ).isoformat() if self.poly.window_end else None,
            "direction":         result.direction if passed else "skip",
            "confidence_score":  result.score,
            "polymarket_odds":   trade_odds,
            "chainlink_price":   self.chainlink.price,
            "binance_price":     self.binance.price,
            "chainlink_dev_pct": self.chainlink.deviation_pct,
            "chainlink_lag_flag":int(self.chainlink.lag_detected),
            "momentum_30s":      self.binance.momentum_30s,
            "momentum_60s":      self.binance.momentum_60s,
            "rsi":               self.binance.rsi_14,
            "volume_zscore":     self.binance.volume_zscore,
            "odds_velocity":     self.poly.odds_velocity,
            "skip_reason":       None if passed else reason,
            "features":          getattr(result, "components", {}) or {},
        })

    def evaluate_signal(self):
        raise NotImplementedError

    def daily_report(self) -> dict:
        return {
            "bot":       self.BOT_ID,
            "bankroll":  self.bankroll.balance,
            "stats":     self.db.daily_stats(),
            "direction": {
                "long":  self.db.direction_stats("long"),
                "short": self.db.direction_stats("short"),
            },
            "lag_trades": self.db.lag_trade_stats(),
            "skips":      self.db.skip_stats(),
        }