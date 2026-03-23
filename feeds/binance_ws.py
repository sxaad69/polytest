"""
BTC Price Feed
Primary:  Coinbase Advanced Trade WebSocket — works on all AWS regions
Fallback: Binance WebSocket — works locally but blocked on AWS US (HTTP 451)

Coinbase WS: wss://advanced-trade-ws.coinbase.com/ws
Subscribes to: BTC-USD ticker channel
"""

import asyncio
import json
import logging
import time
from collections import deque
import websockets

logger = logging.getLogger(__name__)

COINBASE_WS_URL = "wss://advanced-trade-ws.coinbase.com/ws"
BINANCE_WS_URL  = "wss://stream.binance.com:9443/ws/btcusdt@trade"


class BinanceFeed:
    """
    Universal Crypto Price Feed using Coinbase as primary, Binance as fallback.
    Now tracks multiple assets (BTC, ETH, SOL, DOGE, etc.) simultaneously.
    """

    def __init__(self):
        self.prices       = {}  # symbol -> last_price
        self._tick_map    = {}  # symbol -> deque of ticks
        self._vol_map     = {}  # symbol -> aggregate 1m volume
        self._close_map   = {}  # symbol -> deque of 1m closes
        self._ts_map      = {}  # symbol -> last 1m update timestamp
        
        self.products     = ["BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "DOGE-USD", "XRP-USD", "MATIC-USD"]
        self._running     = False
        self._source      = "none"

    def get_price(self, asset: str) -> float | None:
        return self.prices.get(f"{asset.upper()}-USD") or self.prices.get(f"{asset.upper()}USDT")

    def get_momentum(self, asset: str, seconds: int = 30) -> float:
        symbol = f"{asset.upper()}-USD" if self._source == "coinbase" else f"{asset.upper()}USDT"
        ticks = self._tick_map.get(symbol)
        if not ticks: return 0.0
        
        cutoff = time.time() - seconds
        history = [(t, p) for t, p in ticks if t >= cutoff]
        if len(history) < 2: return 0.0
        return (history[-1][1] - history[0][1]) / history[0][1] * 100

    @property
    def price(self) -> float | None:
        """Legacy BTC price property for Bot A/B."""
        return self.get_price("BTC")

    @property
    def momentum_30s(self) -> float:
        """Legacy BTC momentum property."""
        return self.get_momentum("BTC", 30)

    @property
    def momentum_60s(self) -> float:
        """Legacy BTC momentum property."""
        return self.get_momentum("BTC", 60)

    @property
    def rsi_14(self) -> float:
        return 50.0 # Stub for legacy bots

    @property
    def rsi_signal(self) -> float:
        return 0.0 # Stub for legacy bots
    
    @property
    def volume_zscore(self) -> float:
        return 0.0 # Stub for legacy bots

    async def start(self):
        self._running = True
        while self._running:
            try:
                await self._connect_coinbase()
            except Exception as e:
                logger.warning("Coinbase WS error: %s — trying Binance", e)
                try:
                    await self._connect_binance()
                except Exception as e2:
                    logger.warning("Binance WS error: %s — reconnecting in 5s", e2)
                    await asyncio.sleep(5)

    def stop(self):
        self._running = False

    # ── Coinbase ───────────────────────────────────────────────────────────────

    async def _connect_coinbase(self):
        async with websockets.connect(COINBASE_WS_URL) as ws:
            await ws.send(json.dumps({
                "type":        "subscribe",
                "product_ids": self.products,
                "channel":     "ticker",
            }))
            logger.info("Coinbase WS connected | Tracking: %s", ", ".join(self.products))
            self._source = "coinbase"
            async for raw in ws:
                if not self._running: break
                self._handle_coinbase(raw)

    def _handle_coinbase(self, raw: str):
        try:
            msg = json.loads(raw)
            for event in msg.get("events", []):
                for ticker in event.get("tickers", []):
                    symbol = ticker.get("product_id")
                    price  = float(ticker.get("price", 0))
                    if not symbol or price <= 0: continue
                    
                    ts = time.time()
                    self.prices[symbol] = price
                    if symbol not in self._tick_map:
                        self._tick_map[symbol] = deque(maxlen=500)
                    self._tick_map[symbol].append((ts, price))
        except Exception:
            pass

    # ── Binance ───────────────────────────────────────────────────────────────

    async def _connect_binance(self):
        # Map product names to binance stream names (lower case, no dash, @trade)
        streams = [p.replace("-USD", "usdt").lower() + "@trade" for p in self.products]
        url = f"wss://stream.binance.com:9443/stream?streams={'/'.join(streams)}"
        
        async with websockets.connect(url) as ws:
            logger.info("Binance WS connected | Tracking: %s", ", ".join(streams))
            self._source = "binance"
            async for raw in ws:
                if not self._running: break
                self._handle_binance(raw)

    def _handle_binance(self, raw: str):
        try:
            full_msg = json.loads(raw)
            msg      = full_msg.get("data", {})
            symbol   = msg.get("s") # e.g. BTCUSDT
            price    = float(msg.get("p", 0))
            if not symbol or price <= 0: return

            ts = time.time()
            self.prices[symbol] = price
            if symbol not in self._tick_map:
                self._tick_map[symbol] = deque(maxlen=500)
            self._tick_map[symbol].append((ts, price))
        except Exception:
            pass
