"""
Polymarket Feed
Live odds via WebSocket (with REST polling fallback),
order book depth, market discovery, and order placement.
One shared instance — both bots read from it.

Key fix: Gamma API returns startDateIso/endDateIso as plain dates (not datetimes).
Window start/end are extracted directly from the slug timestamp instead:
  slug = btc-updown-5m-1773543000
  window_start = 1773543000
  window_end   = 1773543000 + 300

Token IDs (needed for WS subscription and orders) are fetched separately
from the CLOB API using the market's conditionId.
"""

import asyncio
import json
import logging
import time
from collections import deque
from datetime import datetime, timezone
import aiohttp
import websockets
from config import POLYMARKET_CLOB_URL, POLYMARKET_GAMMA_URL, PAPER_TRADING

logger = logging.getLogger(__name__)

POLY_WS_URL    = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
WINDOW_SECONDS = 300   # 5-minute windows


class PolymarketFeed:

    def __init__(self):
        self.market_id: str       = None
        self.condition_id: str    = None
        self.window_start: float  = None
        self.window_end: float    = None
        self.up_token_id: str     = None
        self.down_token_id: str   = None
        self.up_odds: float       = None
        self.down_odds: float     = None
        self.book_depth: float    = 0.0
        self.odds_velocity: float = 0.0
        self._odds_history        = deque(maxlen=60)
        self._running             = False
        self._session             = None

    # ── Market discovery ───────────────────────────────────────────────────────

    async def fetch_market(self) -> bool:
        try:
            now       = time.time()
            window_ts = int(now // WINDOW_SECONDS) * WINDOW_SECONDS

            # Try current and adjacent windows via direct slug lookup
            # This works even on AWS US where restricted markets are
            # filtered out of general active scans
            for ts in [window_ts, window_ts - WINDOW_SECONDS, window_ts + WINDOW_SECONDS]:
                slug = f"btc-updown-5m-{ts}"
                m    = await self._fetch_by_slug(slug)
                if not m:
                    continue

                # Window times come from the slug timestamp directly —
                # startDateIso/endDateIso are plain dates (not datetimes)
                win_start = float(ts)
                win_end   = win_start + WINDOW_SECONDS

                if not (win_start <= now < win_end):
                    continue

                self.market_id    = m["id"]
                self.condition_id = m.get("conditionId") or m.get("condition_id")
                self.window_start = win_start
                self.window_end   = win_end

                # clobTokenIds is in the market response directly — no extra fetch needed
                clob_ids = m.get("clobTokenIds", [])
                outcomes = m.get("outcomes", [])   # e.g. ["Up", "Down"] or ["Yes", "No"]

                if clob_ids and len(clob_ids) >= 2:
                    # Match token IDs to outcomes by index
                    for i, outcome in enumerate(outcomes):
                        o = outcome.lower()
                        if o in ("up", "yes") and i < len(clob_ids):
                            self.up_token_id = clob_ids[i]
                        elif o in ("down", "no") and i < len(clob_ids):
                            self.down_token_id = clob_ids[i]

                    # Fallback: if outcomes order unknown, just assign by index
                    if not self.up_token_id and len(clob_ids) >= 2:
                        self.up_token_id   = clob_ids[0]
                        self.down_token_id = clob_ids[1]

                logger.info("Market found | slug=%s ends_in=%.0fs | up=%s... down=%s...",
                            slug, win_end - now,
                            (self.up_token_id or "?")[:10],
                            (self.down_token_id or "?")[:10])
                return True

            logger.info("No active BTC 5m market right now — will retry in 10s")
            return False

        except Exception as e:
            logger.error("fetch_market error: %s", e)
            return False

    async def _fetch_by_slug(self, slug: str) -> dict | None:
        """Fetch a single market from Gamma API by slug."""
        try:
            async with self._session.get(
                f"{POLYMARKET_GAMMA_URL}/markets",
                params={"slug": slug},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                data = await resp.json()
            markets = data if isinstance(data, list) else data.get("markets", [])
            return markets[0] if markets else None
        except Exception as e:
            logger.debug("_fetch_by_slug(%s) error: %s", slug, e)
            return None

    # ── Order book ─────────────────────────────────────────────────────────────

    async def fetch_book(self, token_id: str):
        try:
            async with self._session.get(
                f"{POLYMARKET_CLOB_URL}/book",
                params={"token_id": token_id},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                book = await resp.json()
            bids = book.get("bids", [])
            self.book_depth = sum(
                float(b["price"]) * float(b["size"]) for b in bids[:3]
            )
        except Exception as e:
            logger.debug("fetch_book error: %s", e)

    # ── WebSocket odds stream ──────────────────────────────────────────────────

    async def start_odds_stream(self):
        self._running = True
        while self._running:
            try:
                await self._ws_connect()
            except Exception as e:
                logger.warning("Polymarket WS error: %s — polling fallback", e)
                await self._poll_fallback()

    async def _ws_connect(self):
        async with websockets.connect(POLY_WS_URL) as ws:
            logger.info("Polymarket WS connected")
            if self.up_token_id and self.down_token_id:
                await ws.send(json.dumps({
                    "assets_ids": [self.up_token_id, self.down_token_id],
                    "type":       "market",
                }))
            async for raw in ws:
                if not self._running:
                    break
                self._handle(raw)

    def _handle(self, raw: str):
        try:
            msg    = json.loads(raw)
            events = msg if isinstance(msg, list) else [msg]
            for event in events:
                if not isinstance(event, dict):
                    continue
                tid   = event.get("asset_id")
                price = event.get("price")
                if not tid or price is None:
                    continue
                price = float(price)
                if tid == self.up_token_id:
                    self.up_odds   = price
                    self.down_odds = round(1.0 - price, 4)
                elif tid == self.down_token_id:
                    self.down_odds = price
                    self.up_odds   = round(1.0 - price, 4)
                self._odds_history.append((time.time(), self.up_odds or 0.5))
                self._update_velocity()
        except Exception as e:
            logger.debug("WS parse error: %s", e)

    async def _poll_fallback(self):
        while self._running:
            try:
                if self.up_token_id:
                    async with self._session.get(
                        f"{POLYMARKET_CLOB_URL}/midpoint",
                        params={"token_id": self.up_token_id},
                        timeout=aiohttp.ClientTimeout(total=10)
                    ) as resp:
                        d = await resp.json()
                    self.up_odds   = float(d.get("mid", 0.5))
                    self.down_odds = round(1.0 - self.up_odds, 4)
                    self._odds_history.append((time.time(), self.up_odds))
                    self._update_velocity()
                    logger.debug("Odds polled | up=%.3f down=%.3f",
                                 self.up_odds, self.down_odds)
            except Exception as e:
                logger.debug("Poll fallback error: %s", e)
            await asyncio.sleep(3)

    def _update_velocity(self):
        cutoff  = time.time() - 30
        history = [(t, p) for t, p in self._odds_history if t >= cutoff]
        self.odds_velocity = round(
            history[-1][1] - history[0][1], 4
        ) if len(history) >= 2 else 0.0

    # ── Order placement ────────────────────────────────────────────────────────

    async def place_order(self, direction: str, token_id: str,
                          size: float, price: float, bot_id: str) -> dict:
        if PAPER_TRADING:
            logger.info("[PAPER Bot%s] %s size=%.2f price=%.3f",
                        bot_id, direction.upper(), size, price)
            return {"status": "filled", "filled_price": price, "paper": True}
        raise NotImplementedError("Live trading requires py-clob-client. See README.")

    # ── Timing ─────────────────────────────────────────────────────────────────

    @property
    def seconds_remaining(self) -> float:
        return max(0.0, (self.window_end or 0) - time.time())

    @property
    def seconds_elapsed(self) -> float:
        return max(0.0, time.time() - (self.window_start or time.time()))

    async def __aenter__(self):
        self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *_):
        self._running = False
        if self._session:
            await self._session.close()