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
    BOT_G_BANKROLL, BOT_G_DB_PATH, BOT_G_MIN_CONFIDENCE,
    BOT_G_MAX_CONCURRENT_TRADES, BOT_G_MIN_STAKE,
    BOT_G_MIN_ENTRY_ODDS, BOT_G_MAX_ENTRY_ODDS,
    BOT_G_MAX_ENTRY_SECS_INTO_WIN, BOT_G_MIN_SECS_REMAINING,
    NO_ENTRY_LAST_SECS,
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
        self._signal = BotGSignal(min_confidence=BOT_G_MIN_CONFIDENCE)
        self.max_concurrent_trades = BOT_G_MAX_CONCURRENT_TRADES

    async def _loop(self):
        self._log.info("Bot G starting multi-crypto monitor...")

        while self._running:
            try:
                import config
                import importlib
                importlib.reload(config)
                import fnmatch
                # 1. Surgical Strike List Engine (Direct Mathematical Slug Generation)
                await self.poly.fetch_strike_list_markets()
                
                # 2. Strict Isolation: Only evaluate the current active slugs
                # we mathematically calculated. Ignore background discovery junk.
                target_markets = {}
                now = time.time()
                active_slugs = set()
                import config
                assets = getattr(config, "BOT_G_STRIKE_ASSETS", [])
                tfs    = getattr(config, "BOT_G_TIMEFRAMES", {})
                for asset in assets:
                    for tf_name, tf_secs in tfs.items():
                        ts = int(now // tf_secs) * tf_secs
                        active_slugs.add(f"{asset}-updown-{tf_name}-{ts}")

                # Pull strictly from the shared registry ONLY if it matches our active surgical slugs
                for tid, m in self.poly.markets.items():
                    if m.get("slug") in active_slugs:
                        target_markets[tid] = m

                # 2. Write the .txt log (Title | URL) for the user dashboard
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
                    with open("logs/bot_g_markets.txt", "w") as f:
                        f.write("\n".join(entries))

                # 3. Heartbeat log with clinical diagnostic
                best_asset, max_mom = "NONE", 0.0
                for tid, m in target_markets.items():
                    asset = m.get("slug", "").split("-")[0].upper()
                    mom = abs(self.binance.get_momentum(asset, 30))
                    if mom > max_mom: best_asset, max_mom = asset, mom

                self._log.info("[BotG] 🔍 Scanning %d filtered crypto markets | Best: %s (%.4f momentum)", 
                               len(target_markets), best_asset, max_mom)
                
                # 4. Evaluate each market
                for tid, m in list(target_markets.items()):
                    if len(self.executor._positions) >= self.max_concurrent_trades:
                        break
                    await self._evaluate_market(tid, m)

            except Exception as e:
                self._log.error("Bot G loop error: %s", e, exc_info=True)

            await asyncio.sleep(10)

    async def _evaluate_market(self, tid: str, m: dict):
        # Extract market identity first so we can gate on it
        market_id = m.get("condition_id")
        slug = m.get("slug", "")
        # Parse win_start/win_end from registry, or fall back to slug timestamp
        # Slug format: "btc-updown-5m-1774134000" — last segment is the epoch start
        win_start = m.get("win_start")
        win_end   = m.get("win_end")
        if not win_start or not win_end:
            parts = slug.rsplit("-", 1)
            if len(parts) == 2 and parts[1].isdigit():
                ts = int(parts[1])
                # Infer duration from timeframe in slug (e.g. "5m" -> 300s)
                tf_map = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
                tf = next((k for k in tf_map if f"-{k}-" in slug), None)
                duration = tf_map.get(tf, 300)
                win_start = float(ts)
                win_end   = float(ts + duration)
                m["win_start"] = win_start  # cache back so executor can use it
                m["win_end"]   = win_end
            else:
                return  # Can't determine window — skip

        secs_remaining = win_end - time.time()

        current_price = m.get("odds")
        if current_price is None:
            return

        # One position per market window (checked by condition_id, not token_id)
        # This guards against both Long+Long and Long+Short in same window
        for pos in self.executor._positions.values():
            if pos.get("market_id") == market_id:
                return

        # ── Entry filters (all config-driven) ────────────────────────────────
        # 1. Minimum time remaining in window
        if secs_remaining < BOT_G_MIN_SECS_REMAINING:
            return

        # 2. Entry timing — don't enter after 2 min into window (data: 0% win after 180s)
        secs_into_window = time.time() - win_start
        if secs_into_window > BOT_G_MAX_ENTRY_SECS_INTO_WIN:
            return

        # 3. Odds range — sweet spot 0.30–0.70 (data-driven)
        if current_price < BOT_G_MIN_ENTRY_ODDS or current_price > BOT_G_MAX_ENTRY_ODDS:
            return

        # Infer asset from slug (e.g. "eth-updown-5m-XXX" -> "ETH")
        asset = slug.split("-")[0].upper() if slug else "CRYPTO"

        # High-Fidelity Multi-Asset Feed
        momentum    = self.binance.get_momentum(asset, 30)
        asset_price = self.binance.get_price(asset)
        poly_mid    = current_price

        result = self._signal.evaluate(
            asset=asset,
            momentum=momentum,
            asset_price=asset_price,
            poly_mid=poly_mid,
        )

        if not result.tradeable:
            return

        # 4. Direction-specific odds filters (from deep winner analysis)
        if result.direction == "long" and (current_price > 0.60 or current_price < 0.45):
            return  # Longs only survive near fair-value
        if result.direction == "short" and (0.40 < current_price < 0.50):
            return  # Shorts fail in the ambiguous middle zone

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
        if stake <= BOT_G_MIN_STAKE:
            return

        self._log.info("[BotG] CRYPTO SIGNAL | asset=%s dir=%s score=%.4f odds=%.3f lag=%.3f",
                       asset, result.direction, result.score, trade_odds,
                       result.components.get("poly_lag", 0.0))

        signal_id = self.db.log_signal({
            "ts":                  datetime.utcnow().isoformat(),
            "market_id":           market_id,
            "window_start":        datetime.fromtimestamp(win_start).isoformat() if win_start else None,
            "window_end":          datetime.fromtimestamp(win_end).isoformat() if win_end else None,
            "direction":           result.direction,
            "confidence_score":    result.score,
            "polymarket_odds":     trade_odds,
            "chainlink_price":     None,
            "binance_price":       result.components.get("asset_price"),
            "chainlink_dev_pct":   None,
            "chainlink_lag_flag":  None,
            "momentum_30s":        momentum,          # ← was None, now stores actual value
            "momentum_60s":        None,
            "rsi":                 None,
            "volume_zscore":       None,
            "odds_velocity":       m.get("velocity", 0.0),
            "skip_reason":         None,
            "features":            {
                **result.components,
                "asset":            asset,
                "poly_lag":         result.components.get("poly_lag"),
                "secs_into_window": round(secs_into_window, 1),
                "secs_remaining":   round(secs_remaining, 1),
            },
        })

        await self.executor.enter(
            direction=result.direction,
            confidence=result.score,
            stake=stake,
            signal_id=signal_id,
            token_id=trade_token_id,
            entry_odds=trade_odds,
            market_id=market_id,
            win_start=win_start,
            win_end=win_end,
            condition_id=market_id,
            asset=asset,
            slug=slug,
        )

    def evaluate_signal(self):
        return None
