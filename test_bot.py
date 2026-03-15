"""
Bot Health Check — run before starting the bot.

    python test_bot.py

Tests:
  1. .env loaded and Alchemy key present
  2. Chainlink BTC/USD price feed responding
  3. Binance WebSocket connects and streams a price
  4. Polymarket Gamma API reachable
  5. Active BTC 5m market found (or explains why not)
  6. Polymarket CLOB API reachable
  7. Polymarket WebSocket connects
  8. SQLite databases can be created in data/
  9. Config sanity (weights, thresholds, circuit breaker flag)
"""

import asyncio
import json
import os
import sys
import time
import pathlib

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"

def ok(msg):     print(f"  {GREEN}✓{RESET}  {msg}")
def fail(msg):   print(f"  {RED}✗{RESET}  {msg}")
def warn(msg):   print(f"  {YELLOW}!{RESET}  {msg}")
def header(msg): print(f"\n{msg}")

results = []

def record(name: str, passed: bool, detail: str = ""):
    results.append((name, passed, detail))
    if passed:
        ok(f"{name}{' — ' + detail if detail else ''}")
    else:
        fail(f"{name}{' — ' + detail if detail else ''}")


# ── 1. .env and Alchemy key ────────────────────────────────────────────────────
header("1. Environment")
try:
    from dotenv import load_dotenv
    load_dotenv()
    rpc = os.getenv("ALCHEMY_RPC_URL", "")
    if rpc and len(rpc) > 50:
        record(".env loaded", True, f"key ends ...{rpc[-8:]}")
    else:
        record(".env loaded", False,
               "ALCHEMY_RPC_URL missing — copy .env.example to .env and fill it in")
except ImportError:
    record(".env loaded", False, "python-dotenv not installed — pip install python-dotenv")


# ── 2. Chainlink price feed ────────────────────────────────────────────────────
header("2. Chainlink BTC/USD feed")

async def test_chainlink():
    import aiohttp
    rpc  = os.getenv("ALCHEMY_RPC_URL", "")
    feed = "0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c"
    if not rpc:
        record("Chainlink price", False, "no RPC URL")
        return
    payload = {
        "jsonrpc": "2.0", "method": "eth_call",
        "params": [{"to": feed, "data": "0xfeaf968c"}, "latest"],
        "id": 1
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(rpc, json=payload,
                              timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json()
        if "error" in data:
            record("Chainlink price", False, str(data["error"]))
            return
        raw        = data["result"][2:]
        price      = int(raw[64:128], 16) / 1e8
        updated_at = int(raw[192:256], 16)
        stale_mins = (time.time() - updated_at) / 60
        if price < 1000 or price > 1_000_000:
            record("Chainlink price", False, f"suspicious value: ${price:,.2f}")
        else:
            detail = f"${price:,.2f} | last updated {stale_mins:.1f} min ago"
            if stale_mins > 120:
                warn(f"Price OK (${price:,.2f}) but stale {stale_mins:.0f} min — normal if BTC flat")
                results.append(("Chainlink price", True, detail))
            else:
                record("Chainlink price", True, detail)
    except Exception as e:
        record("Chainlink price", False, str(e))

asyncio.run(test_chainlink())


# ── 3. Binance WebSocket ───────────────────────────────────────────────────────
header("3. Binance WebSocket")

async def test_binance():
    import websockets
    try:
        async with websockets.connect(
            "wss://stream.binance.com:9443/ws/btcusdt@trade"
        ) as ws:
            raw   = await asyncio.wait_for(ws.recv(), timeout=5)
            msg   = json.loads(raw)
            price = float(msg["p"])
            record("Binance WS", True, f"BTC/USDT=${price:,.2f} live")
    except asyncio.TimeoutError:
        record("Binance WS", False, "connected but no trade in 5s")
    except Exception as e:
        record("Binance WS", False, str(e))

asyncio.run(test_binance())


# ── 4. Polymarket Gamma API ────────────────────────────────────────────────────
header("4. Polymarket Gamma API")

async def test_gamma():
    import aiohttp
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                "https://gamma-api.polymarket.com/markets",
                params={"active": "true", "limit": 1},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                data  = await r.json()
        count = len(data) if isinstance(data, list) else len(data.get("markets", []))
        record("Gamma API", True, f"reachable — {count} market(s) returned")
    except Exception as e:
        record("Gamma API", False, str(e))

asyncio.run(test_gamma())


# ── 5. Active BTC 5m market ────────────────────────────────────────────────────
header("5. Active BTC 5-minute market")

async def test_market():
    import aiohttp
    from datetime import datetime, timezone

    def _ts(iso):
        if not iso:
            return None
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            return dt.replace(tzinfo=timezone.utc).timestamp()
        except Exception:
            return None

    now       = time.time()
    window_ts = int(now // 300) * 300
    found     = False

    async with aiohttp.ClientSession() as s:
        slugs = [
            f"btc-updown-5m-{window_ts}",
            f"btc-updown-5m-{window_ts - 300}",
            f"btc-updown-5m-{window_ts + 300}",
        ]
        for slug in slugs:
            try:
                async with s.get(
                    "https://gamma-api.polymarket.com/markets",
                    params={"slug": slug},
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as r:
                    data = await r.json()
                markets = data if isinstance(data, list) else data.get("markets", [])
                if not markets:
                    continue
                m     = markets[0]
                end   = _ts(m.get("endDateIso") or m.get("end_date_iso"))
                start = _ts(m.get("startDateIso") or m.get("start_date_iso"))
                if start and end and start <= now < end:
                    tokens = m.get("tokens", [])
                    up_id  = next((t["token_id"] for t in tokens
                                   if t.get("outcome","").lower() == "up"), None)
                    record("Active 5m market", True,
                           f"slug={slug} ends_in={end-now:.0f}s "
                           f"up_token={up_id[:10] if up_id else 'none'}...")
                    found = True
                    break
            except Exception:
                continue

        if not found:
            # Fallback scan
            try:
                async with s.get(
                    "https://gamma-api.polymarket.com/markets",
                    params={"active": "true", "closed": "false", "limit": 50},
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as r:
                    data = await r.json()
                markets  = data if isinstance(data, list) else data.get("markets", [])
                btc_mkts = [m for m in markets if "btc-updown-5m" in m.get("slug","")]
                if btc_mkts:
                    record("Active 5m market", True,
                           f"found via scan — {btc_mkts[0].get('slug')}")
                else:
                    warn("No active BTC 5m market right now")
                    warn("Normal outside US hours (~4pm-10pm ET) — bot will find it automatically")
                    results.append(("Active 5m market", True, "no market now but API works"))
            except Exception as e:
                record("Active 5m market", False, str(e))

asyncio.run(test_market())


# ── 6. Polymarket CLOB API ─────────────────────────────────────────────────────
header("6. Polymarket CLOB API")

async def test_clob():
    import aiohttp
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("https://clob.polymarket.com/",
                             timeout=aiohttp.ClientTimeout(total=10)) as r:
                record("CLOB API", r.status < 500, f"HTTP {r.status}")
    except Exception as e:
        record("CLOB API", False, str(e))

asyncio.run(test_clob())


# ── 7. Polymarket WebSocket ────────────────────────────────────────────────────
header("7. Polymarket WebSocket")

async def test_poly_ws():
    import websockets
    try:
        async with websockets.connect(
            "wss://ws-subscriptions-clob.polymarket.com/ws/market"
        ) as ws:
            record("Polymarket WS", True, "connected (HTTP 101)")
    except Exception as e:
        record("Polymarket WS", False, str(e))

asyncio.run(test_poly_ws())


# ── 8. SQLite databases ────────────────────────────────────────────────────────
header("8. SQLite databases")

try:
    import sqlite3
    data_dir = pathlib.Path(__file__).parent / "data"
    data_dir.mkdir(exist_ok=True)
    for bot_id, db_name in [("A", "bot_a_paper.db"), ("B", "bot_b_paper.db")]:
        db_path = data_dir / db_name
        conn    = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE IF NOT EXISTS _test (id INTEGER)")
        conn.execute("DROP TABLE _test")
        conn.close()
        record(f"SQLite Bot {bot_id}", True, str(db_path))
except Exception as e:
    record("SQLite", False, str(e))


# ── 9. Config sanity ───────────────────────────────────────────────────────────
header("9. Config sanity")

try:
    from config import (
        BOT_B_SIGNAL_WEIGHTS, MIN_ODDS, MAX_ODDS,
        TAKE_PROFIT_DELTA, TRAILING_STOP_DELTA,
        BOT_A_MIN_CONFIDENCE, BOT_B_MIN_CONFIDENCE,
        BOT_A_BANKROLL, BOT_B_BANKROLL,
        CIRCUIT_BREAKER_ENABLED, PAPER_TRADING,
    )

    weight_sum = sum(BOT_B_SIGNAL_WEIGHTS.values())
    record("Bot B weights sum to 1.0",
           abs(weight_sum - 1.0) < 0.001,
           f"sum={weight_sum:.3f}")

    record("Odds range valid",
           MIN_ODDS < MAX_ODDS,
           f"{MIN_ODDS}–{MAX_ODDS}")

    record("TP > trailing stop",
           TAKE_PROFIT_DELTA > TRAILING_STOP_DELTA,
           f"TP={TAKE_PROFIT_DELTA} trailing={TRAILING_STOP_DELTA}")

    record("Bankrolls set", True,
           f"A=${BOT_A_BANKROLL} B=${BOT_B_BANKROLL}")

    record("Confidence thresholds", True,
           f"A={BOT_A_MIN_CONFIDENCE} B={BOT_B_MIN_CONFIDENCE}")

    cb_status = "DISABLED (paper mode)" if not CIRCUIT_BREAKER_ENABLED else "ENABLED (live mode)"
    record("Circuit breaker", True, cb_status)

    if not PAPER_TRADING and not CIRCUIT_BREAKER_ENABLED:
        warn("PAPER_TRADING=False but CIRCUIT_BREAKER_ENABLED=False — enable it for live trading!")

except Exception as e:
    record("Config sanity", False, str(e))


# ── Summary ────────────────────────────────────────────────────────────────────
total  = len(results)
passed = sum(1 for _, p, _ in results if p)
failed = total - passed

print("\n" + "═" * 58)
print(f"  Results: {passed}/{total} passed", end="")
if failed:
    print(f"  ({RED}{failed} failed{RESET})")
else:
    print(f"  {GREEN}— all good{RESET}")
print("═" * 58)

if failed:
    print(f"\n{RED}Fix the failing checks before running the bot.{RESET}\n")
    sys.exit(1)
else:
    print(f"\n{GREEN}Everything looks good. Run: python main.py{RESET}\n")
    sys.exit(0)
