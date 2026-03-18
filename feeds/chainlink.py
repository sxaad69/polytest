"""
Chainlink Feed + Lag Detector

Uses raw JSON-RPC eth_call via aiohttp — no web3.py threading issues.
Polls every 5 seconds with exponential backoff retry for Alchemy 429s.

Chainlink BTC/USD only updates onchain when:
  (a) price moves ≥ 0.5%  OR  (b) heartbeat timeout (~1 hour)
This creates lag vs Binance spot — which is the edge Bot A exploits.
"""

import asyncio
import logging
import time
import aiohttp
from config import (
    CHAINLINK_RPC_URL, CHAINLINK_BTC_FEED,
    BOT_A_MIN_DEVIATION, BOT_A_MAX_DEVIATION, BOT_A_MIN_SUSTAIN_SECS,
    CHAINLINK_POLL_SECS,
)

logger = logging.getLogger(__name__)

# keccak256("latestRoundData()") first 4 bytes
LATEST_ROUND_SELECTOR = "0xfeaf968c"


class ChainlinkFeed:

    def __init__(self, binance_feed):
        self.binance              = binance_feed
        self.price: float         = None
        self.updated_at: int      = None
        self.deviation_pct: float = 0.0
        self.lag_signal: float    = 0.0
        self.lag_direction: str   = None
        self.lag_sustained: float = 0.0

        self._dev_start: float    = None
        self._dev_dir: str        = None
        self._running             = False
        self._session             = None
        self._first_fetch_done    = False

        if not CHAINLINK_RPC_URL:
            logger.error(
                "ALCHEMY_RPC_URL not set in .env\n"
                "  → https://dashboard.alchemy.com → Create App → Ethereum Mainnet"
            )

    async def start(self):
        self._running = True
        self._session = aiohttp.ClientSession()
        logger.info("Chainlink feed starting | rpc=%s...", CHAINLINK_RPC_URL[:45])
        try:
            while self._running:
                try:
                    await self._fetch_with_retry()
                    self._update_lag()
                except Exception as e:
                    logger.warning("Chainlink poll failed: %s", e)
                await asyncio.sleep(CHAINLINK_POLL_SECS)
        finally:
            await self._session.close()

    def stop(self):
        self._running = False

    async def _fetch_with_retry(self):
        """Exponential backoff: 0s, 1s, 2s, 4s — handles Alchemy 429s."""
        delays   = [1, 2, 4]
        last_err = None
        for attempt, delay in enumerate([0] + delays, 1):
            if delay:
                await asyncio.sleep(delay)
            try:
                await self._fetch()
                if attempt > 1:
                    logger.info("Chainlink fetch OK on attempt %d", attempt)
                return
            except Exception as e:
                last_err = e
                logger.debug("Chainlink attempt %d failed: %s", attempt, e)
        raise last_err

    async def _fetch(self):
        """Raw eth_call JSON-RPC — returns BTC/USD price in 8 decimals."""
        payload = {
            "jsonrpc": "2.0",
            "method":  "eth_call",
            "params":  [
                {"to": CHAINLINK_BTC_FEED, "data": LATEST_ROUND_SELECTOR},
                "latest"
            ],
            "id": 1,
        }
        async with self._session.post(
            CHAINLINK_RPC_URL, json=payload,
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            data = await resp.json()

        if "error" in data:
            raise RuntimeError(f"RPC error: {data['error']}")

        result = data.get("result", "")
        if not result or result == "0x":
            raise RuntimeError("Empty response from Chainlink contract")

        raw = result[2:]
        if len(raw) < 64 * 5:
            raise RuntimeError(f"Response too short: {len(raw)} chars")

        # ABI decode: answer = slot 1 (index 64:128), updatedAt = slot 3 (192:256)
        self.price      = int(raw[64:128], 16) / 1e8
        self.updated_at = int(raw[192:256], 16)

        if not self._first_fetch_done:
            logger.info("Chainlink BTC/USD=%.2f ✓ feed working", self.price)
            self._first_fetch_done = True
        else:
            logger.debug("Chainlink BTC/USD=%.2f staleness=%.0fs",
                         self.price, self.staleness_secs)

    def _update_lag(self):
        if not self.price or not self.binance.price:
            return
        dev               = (self.binance.price - self.price) / self.price * 100
        self.deviation_pct = dev
        direction         = "up" if dev > 0 else "down"
        now               = time.time()

        if abs(dev) >= BOT_A_MIN_DEVIATION and abs(dev) <= BOT_A_MAX_DEVIATION:
            if self._dev_dir != direction:
                self._dev_start = now
                self._dev_dir   = direction
            self.lag_sustained = now - (self._dev_start or now)

            if self.lag_sustained >= BOT_A_MIN_SUSTAIN_SECS:
                magnitude          = min(abs(dev) / BOT_A_MIN_DEVIATION * 0.3, 1.0)
                self.lag_signal    = magnitude if direction == "up" else -magnitude
                self.lag_direction = direction
                logger.debug(
                    "Lag ACTIVE dev=%.3f%% dir=%s sustained=%.1fs signal=%.2f",
                    dev, direction, self.lag_sustained, self.lag_signal
                )
            else:
                self.lag_signal = 0.0
        else:
            self._dev_start    = None
            self._dev_dir      = None
            self.lag_sustained = 0.0
            self.lag_signal    = 0.0
            self.lag_direction = None

    @property
    def lag_detected(self) -> bool:
        return abs(self.lag_signal) > 0.01

    @property
    def staleness_secs(self) -> float:
        if not self.updated_at:
            return 999.0
        return time.time() - self.updated_at