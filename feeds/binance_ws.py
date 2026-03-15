"""
Binance WebSocket Feed
Real-time BTC/USDT price, momentum, RSI, volume z-score.
One shared instance — both bots read from it.
"""

import asyncio
import json
import logging
import time
from collections import deque
import websockets
from config import BINANCE_WS_URL

logger = logging.getLogger(__name__)


class BinanceFeed:

    def __init__(self):
        self.price: float  = None
        self._ticks        = deque(maxlen=500)
        self._vol_history  = deque(maxlen=100)
        self._1m_closes    = deque(maxlen=20)
        self._1m_ts: float = 0
        self._1m_vol: float = 0.0
        self._running      = False

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
            try:
                async with websockets.connect(BINANCE_WS_URL) as ws:
                    logger.info("Binance WS connected")
                    async for raw in ws:
                        if not self._running:
                            break
                        self._handle(raw)
            except Exception as e:
                logger.warning("Binance WS error: %s — reconnecting in 3s", e)
                await asyncio.sleep(3)

    def stop(self):
        self._running = False

    def _handle(self, raw: str):
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
