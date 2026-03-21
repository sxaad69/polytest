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


import fnmatch
from utils.pm_math import calculate_vwap

class PolymarketFeed:

    def __init__(self):
        # Multi-market state: token_id -> market_data
        self.markets = {}
        
        # Compatibility trackers for legacy Bots A and B (BTC-UPDOWN)
        self._default_up_id = None
        self._default_down_id = None
        self._default_window = {"start": None, "end": None}
        
        self.taker_fee_bps = 0
        self.maker_fee_bps = 0
        self._running      = False
        self._session      = None
        self._ws           = None

    # ── Compatibility Layer (to avoid breaking Bot A & B) ──────────────────────

    @property
    def up_token_id(self): return self._default_up_id
    
    @property
    def down_token_id(self): return self._default_down_id

    @property
    def up_odds(self): 
        return self.markets.get(self._default_up_id, {}).get("odds")

    @property
    def down_odds(self):
        return self.markets.get(self._default_down_id, {}).get("odds")

    @property
    def window_start(self): return self._default_window["start"]

    @property
    def window_end(self): return self._default_window["end"]

    @property
    def book_depth(self):
        return self.markets.get(self._default_up_id, {}).get("depth", 0.0)

    @property
    def odds_velocity(self):
        return self.markets.get(self._default_up_id, {}).get("velocity", 0.0)

    # ── Market discovery ───────────────────────────────────────────────────────

    async def fetch_market(self) -> bool:
        """Legacy method for Bot A/B backward compatibility."""
        return await self.fetch_markets_by_pattern("btc-updown-5m-*")

    async def fetch_markets_by_pattern(self, pattern: str) -> bool:
        """Fetches all active markets matching a slug pattern and registers them."""
        try:
            now = time.time()
            async with self._session.get(
                f"{POLYMARKET_GAMMA_URL}/markets",
                params={"active": "true"},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                all_markets = await resp.json()
            
            markets = all_markets if isinstance(all_markets, list) else all_markets.get("markets", [])
            matches = [m for m in markets if fnmatch.fnmatch(m.get("slug", ""), pattern)]
            
            if not matches:
                logger.debug("No markets matching pattern: %s", pattern)
                return False

            count = 0
            for m in matches:
                # Basic registration
                tid_up, tid_down = self._parse_clob_ids(m)
                if not tid_up or not tid_down:
                    continue
                
                # Check window (for updown markets)
                slug = m.get("slug", "")
                win_ts = self._extract_ts_from_slug(slug)
                win_start = float(win_ts) if win_ts else now
                win_end = win_start + WINDOW_SECONDS
                
                # Register market state
                for tid, peer in [(tid_up, tid_down), (tid_down, tid_up)]:
                    if tid not in self.markets:
                        self.markets[tid] = {
                            "odds": None,
                            "history": deque(maxlen=60),
                            "velocity": 0.0,
                            "bids": [],
                            "asks": [],
                            "depth": 0.0,
                            "win_start": win_start,
                            "win_end": win_end,
                            "slug": slug,
                            "peer_id": peer,   # Store complementary token for auto-hedge logic
                            "condition_id": self._extract_condition_id(m)
                        }
                
                # Compatibility seeding for BTC-UPDOWN
                if "btc-updown-5m-" in slug and win_start <= now < win_end:
                    self._default_up_id = tid_up
                    self._default_down_id = tid_down
                    self._default_window = {"start": win_start, "end": win_end}
                    self.taker_fee_bps = int(m.get("takerBaseFee", 0)) // 100
                
                count += 1

            if count > 0:
                logger.info("Registered %d markets for pattern: %s", count, pattern)
                await self.resubscribe()
                return True
            return False

        except Exception as e:
            logger.error("fetch_markets_by_pattern error: %s", e)
            return False

    def _extract_condition_id(self, m: dict) -> str | None:
        return m.get("conditionId") or m.get("condition_id")

    def _parse_clob_ids(self, market_data: dict) -> tuple:
        clob_ids = market_data.get("clobTokenIds", [])
        if isinstance(clob_ids, str):
            try: clob_ids = json.loads(clob_ids)
            except: clob_ids = []
        
        outcomes = market_data.get("outcomes", [])
        up_id = down_id = None
        
        if clob_ids and len(clob_ids) >= 2:
            for i, o in enumerate(outcomes):
                name = o.lower()
                if name in ("up", "yes") and i < len(clob_ids):
                    up_id = clob_ids[i]
                elif name in ("down", "no") and i < len(clob_ids):
                    down_id = clob_ids[i]
            
            if not up_id: # fallback
                up_id, down_id = clob_ids[0], clob_ids[1]
                
        return up_id, down_id

    def _extract_ts_from_slug(self, slug: str) -> int | None:
        try:
            return int(slug.split("-")[-1])
        except: return None

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
        """Fetches Orderbook snapshot from CLOB REST API."""
        try:
            async with self._session.get(
                f"{POLYMARKET_CLOB_URL}/book",
                params={"token_id": token_id},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                book = await resp.json()
            
            bids = book.get("bids", [])
            asks = book.get("asks", [])
            
            if token_id in self.markets:
                self.markets[token_id]["bids"] = bids
                self.markets[token_id]["asks"] = asks
                # Update legacy book_depth if this is the default market
                if token_id == self._default_up_id:
                    self.markets[token_id]["depth"] = sum(
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
            self._ws = ws
            logger.info("Polymarket WS connected")
            # Subscribe immediately if we already have tokens
            if self.up_token_id and self.down_token_id:
                await self._subscribe(ws)
            async for raw in ws:
                if not self._running:
                    break
                self._handle(raw)
        self._ws = None

    async def _subscribe(self, ws):
        """Subscribe to price and book updates for all registered markets."""
        tids = list(self.markets.keys())
        if not tids: return
        
        await ws.send(json.dumps({
            "assets_ids": tids,
            "type":       "market",
        }))
        logger.info("WS subscribed | %d tokens", len(tids))
        await self._seed_odds()

    async def _seed_odds(self):
        """Fetch current odds via REST to seed initial state."""
        try:
            for tid, m in list(self.markets.items()):
                if m["odds"] is not None: continue
                
                async with self._session.get(
                    f"{POLYMARKET_CLOB_URL}/midpoint",
                    params={"token_id": tid},
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    d = await resp.json()
                
                mid = float(d.get("mid", 0))
                if mid > 0:
                    m["odds"] = mid
                    m["history"].append((time.time(), mid))
                    self._update_velocity(tid)
                    
                    # Complementary seed
                    peer_id = m.get("peer_id")
                    if peer_id and peer_id in self.markets:
                        comp_price = calculate_hedge_price(mid)
                        self.markets[peer_id]["odds"] = comp_price
            
            logger.info("Odds seeded via REST for %d markets", len(self.markets))
        except Exception as e:
            logger.debug("Odds seed error: %s", e)

    async def resubscribe(self):
        """
        Called after fetch_market loads new tokens.
        Sends a fresh subscription on the existing WS connection.
        """
        if self._ws and self.up_token_id and self.down_token_id:
            try:
                await self._subscribe(self._ws)
            except Exception as e:
                logger.warning("Resubscribe error: %s", e)

    def _handle(self, raw: str):
        try:
            msg = json.loads(raw)
            events = msg if isinstance(msg, list) else [msg]
            
            for event in events:
                if not isinstance(event, dict): continue
                tid = event.get("asset_id")
                if not tid or tid not in self.markets: continue

                # Price Change Update
                price = event.get("price")
                if price is not None:
                    price = float(price)
                    self.markets[tid]["odds"] = price
                    self.markets[tid]["history"].append((time.time(), price))
                    self._update_velocity(tid)
                    
                    # Complementary odds for binary markets (auto-hedge logic)
                    peer_id = self.markets[tid].get("peer_id")
                    if peer_id and peer_id in self.markets:
                        self.markets[peer_id]["odds"] = calculate_hedge_price(price)

                # L2 Book Update
                book = event.get("book")
                if book:
                    self.markets[tid]["bids"] = book.get("bids", [])
                    self.markets[tid]["asks"] = book.get("asks", [])
                    # calculate_vwap use could go here if needed per tick

        except Exception as e:
            logger.debug("WS parse error: %s", e)

    def _update_velocity(self, tid: str):
        m = self.markets.get(tid)
        if not m: return
        cutoff = time.time() - 30
        history = list(m["history"])
        history = [(t, p) for t, p in history if t >= cutoff]
        m["velocity"] = round(
            history[-1][1] - history[0][1], 4
        ) if len(history) >= 2 else 0.0

    async def _poll_fallback(self):
        while self._running:
            try:
                if self._default_up_id:
                    async with self._session.get(
                        f"{POLYMARKET_CLOB_URL}/midpoint",
                        params={"token_id": self._default_up_id},
                        timeout=aiohttp.ClientTimeout(total=10)
                    ) as resp:
                        d = await resp.json()
                    mid = float(d.get("mid", 0.5))
                    self.markets[self._default_up_id]["odds"] = mid
                    self.markets[self._default_up_id]["history"].append((time.time(), mid))
                    self._update_velocity(self._default_up_id)
                    
                    if self._default_down_id:
                        self.markets[self._default_down_id]["odds"] = calculate_hedge_price(mid)

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
                          size: float, price: float, bot_id: str,
                          paper: bool = True) -> dict:
        if paper:
            logger.info("[PAPER Bot%s] %s size=%.2f price=%.3f",
                        bot_id, direction.upper(), size, price)
            return {"status": "filled", "filled_price": price, "paper": True}

        # ── Live order via py-clob-client ──────────────────────────────────
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
            from py_clob_client.constants import POLYGON
            from config import (
                POLYMARKET_PRIVATE_KEY, POLYMARKET_FUNDER_ADDRESS,
                POLYMARKET_API_KEY, POLYMARKET_API_SECRET, POLYMARKET_PASSPHRASE,
            )

            creds = ApiCreds(
                api_key        = POLYMARKET_API_KEY,
                api_secret     = POLYMARKET_API_SECRET,
                api_passphrase = POLYMARKET_PASSPHRASE,
            )
            client = ClobClient(
                host           = POLYMARKET_CLOB_URL,
                key            = POLYMARKET_PRIVATE_KEY,
                chain_id       = POLYGON,
                creds          = creds,
                funder         = POLYMARKET_FUNDER_ADDRESS,
                signature_type = 1,   # EOA — required for Magic/Gmail wallet
            )

            # Round price to valid tick (0.01 increments)
            rounded_price = round(round(price / 0.01) * 0.01, 4)

            # Polymarket orders are in shares, not USDC
            shares = round(size / rounded_price, 2)

            order_args = OrderArgs(
                token_id = token_id,
                price    = rounded_price,
                size     = shares,
                side     = "BUY" if direction != "sell" else "SELL",
            )

            signed_order = client.create_order(order_args)
            resp         = client.post_order(signed_order, OrderType.GTC)

            if resp and resp.get("success"):
                filled_price = float(resp.get("price", rounded_price))
                filled_size  = float(resp.get("size", shares))
                logger.info(
                    "[LIVE Bot%s] %s FILLED | size=%.2f price=%.3f order_id=%s",
                    bot_id, direction.upper(), filled_size, filled_price,
                    resp.get("orderID", "?")
                )
                return {
                    "status":       "filled",
                    "filled_price": filled_price,
                    "filled_size":  filled_size,
                    "order_id":     resp.get("orderID"),
                    "paper":        False,
                }
            else:
                logger.error("[LIVE Bot%s] Order rejected: %s", bot_id, resp)
                return {"status": "failed", "reason": str(resp)}

        except ImportError:
            logger.error("py-clob-client not installed — pip install py-clob-client")
            return {"status": "failed", "reason": "missing_dependency"}
        except Exception as e:
            logger.error("[LIVE Bot%s] Order error: %s", bot_id, e)
            return {"status": "failed", "reason": str(e)}

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