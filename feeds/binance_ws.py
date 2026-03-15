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
    Price feed using Coinbase as primary, Binance as fallback.
    Class kept as BinanceFeed so nothing else needs to change.
    """

    def __init__(self):
        self.price: float   = None
        self._ticks         = deque(maxlen=500)
        self._vol_history   = deque(maxlen=100)
        self._1m_closes     = deque(maxlen=20)
        self._1m_ts: float  = 0
        self._1m_vol: float = 0.0
        self._running       = False
        self._source: str   = "none"

    @property
    def momentum_30s(self) -> float:
        return self._momentum(30)

    @property
    def momentum_60s(self) -> float:
        return self._momentum(60)

    @property
    def volume_zscore(self) -> float:
        vols = list(self._vol_history)
        if len(vols) < 5:
            return 0.0
        mean = sum(vols) / len(vols)
        std  = (sum((v - mean)**2 for v in vols) / len(vols))**0.5
        return 0.0 if std == 0 else (vols[-1] - mean) / std

    @property
    def rsi_14(self) -> float:
        closes = list(self._1m_closes)
        if len(closes) < 15:
            return 50.0
        gains  = [max(closes[i]-closes[i-1], 0) for i in range(1, len(closes))]
        losses = [max(closes[i-1]-closes[i], 0) for i in range(1, len(closes))]
        ag, al = sum(gains[-14:])/14, sum(losses[-14:])/14
        if al == 0:
            return 100.0
        return round(100 - 100/(1 + ag/al), 2)

    @property
    def rsi_signal(self) -> float:
        rsi = self.rsi_14
        if rsi < 30:
            return (30 - rsi) / 30
        elif rsi > 70:
            return -((rsi - 70) / 30)
        return (50 - rsi) / 20 * 0.3

    async def start(self):
        self._running = True
        while self._running:
            # Try Coinbase first, fall back to Binance
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
            # Subscribe to BTC-USD ticker
            await ws.send(json.dumps({
                "type":        "subscribe",
                "product_ids": ["BTC-USD"],
                "channel":     "ticker",
            }))
            logger.info("Coinbase WS connected — price feed active")
            self._source = "coinbase"
            async for raw in ws:
                if not self._running:
                    break
                self._handle_coinbase(raw)

    def _handle_coinbase(self, raw: str):
        try:
            msg = json.loads(raw)
            # Coinbase wraps events in {channel, events:[]}
            for event in msg.get("events", []):
                for ticker in event.get("tickers", []):
                    price = float(ticker.get("price", 0))
                    if price <= 0:
                        continue
                    ts = time.time()
                    self.price = price
                    self._ticks.append((ts, price))
                    # Coinbase doesn't give volume per tick — use 1.0 as placeholder
                    self._1m_vol += 1.0
                    if ts - self._1m_ts >= 60:
                        self._1m_closes.append(price)
                        self._vol_history.append(self._1m_vol)
                        self._1m_vol = 0.0
                        self._1m_ts  = ts
        except Exception:
            pass

    # ── Binance fallback ───────────────────────────────────────────────────────

    async def _connect_binance(self):
        async with websockets.connect(BINANCE_WS_URL) as ws:
            logger.info("Binance WS connected — price feed active")
            self._source = "binance"
            async for raw in ws:
                if not self._running:
                    break
                self._handle_binance(raw)

    def _handle_binance(self, raw: str):
        try:
            msg   = json.loads(raw)
            price = float(msg["p"])
            qty   = float(msg["q"])
            ts    = time.time()
            self.price = price
            self._ticks.append((ts, price))
            self._1m_vol += qty
            if ts - self._1m_ts >= 60:
                self._1m_closes.append(price)
                self._vol_history.append(self._1m_vol)
                self._1m_vol = 0.0
                self._1m_ts  = ts
        except Exception:
            pass

    def _momentum(self, seconds: int) -> float:
        cutoff  = time.time() - seconds
        history = [(t, p) for t, p in self._ticks if t >= cutoff]
        if len(history) < 2:
            return 0.0
        return (history[-1][1] - history[0][1]) / history[0][1] * 100
