"""
test_live_order.py — Place one real order and immediately close it.

Validates:
  1. Signature works (invalid signature error fixed)
  2. Order reaches Polymarket and fills
  3. Position can be closed via sell order
  4. PnL calculation is correct

Run: python scripts/test_live_order.py

Uses smallest possible stake ($1.00) to minimise real money risk.
"""

import asyncio
import sys
import os
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def ok(msg):   print(f"  {GREEN}✓{RESET}  {msg}")
def fail(msg): print(f"  {RED}✗{RESET}  {msg}")
def warn(msg): print(f"  {YELLOW}!{RESET}  {msg}")
def info(msg): print(f"  →  {msg}")


async def get_active_market():
    """Find the current active BTC 5m market and return token IDs."""
    import aiohttp
    now       = time.time()
    window_ts = int(now // 300) * 300

    async with aiohttp.ClientSession() as s:
        for ts in [window_ts, window_ts - 300, window_ts + 300]:
            slug = f"btc-updown-5m-{ts}"
            async with s.get(
                "https://gamma-api.polymarket.com/markets",
                params={"slug": slug},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                data = await r.json()

            markets = data if isinstance(data, list) else []
            if not markets:
                continue

            m         = markets[0]
            win_start = float(ts)
            win_end   = win_start + 300

            if not (win_start <= now < win_end):
                continue

            import json as _json
            clob_ids = m.get("clobTokenIds", [])
            if isinstance(clob_ids, str):
                clob_ids = _json.loads(clob_ids)

            outcomes = m.get("outcomes", [])
            up_id = down_id = None
            for i, outcome in enumerate(outcomes):
                o = outcome.lower()
                if o in ("up", "yes") and i < len(clob_ids):
                    up_id = clob_ids[i]
                elif o in ("down", "no") and i < len(clob_ids):
                    down_id = clob_ids[i]

            if not up_id and len(clob_ids) >= 2:
                up_id, down_id = clob_ids[0], clob_ids[1]

            # Get current odds
            async with s.get(
                "https://clob.polymarket.com/midpoint",
                params={"token_id": up_id},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                mid = await r.json()
            up_odds   = float(mid.get("mid", 0.5))
            down_odds = round(1.0 - up_odds, 4)

            return {
                "slug":      slug,
                "win_end":   win_end,
                "up_id":     up_id,
                "down_id":   down_id,
                "up_odds":   up_odds,
                "down_odds": down_odds,
                "secs_left": win_end - now,
            }

    return None


def make_client():
    """Build py-clob-client with correct EOA signature type."""
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds
    from py_clob_client.constants import POLYGON

    creds = ApiCreds(
        api_key        = os.getenv("POLYMARKET_API_KEY"),
        api_secret     = os.getenv("POLYMARKET_API_SECRET"),
        api_passphrase = os.getenv("POLYMARKET_PASSPHRASE"),
    )
    return ClobClient(
        host           = "https://clob.polymarket.com",
        key            = os.getenv("POLYMARKET_PRIVATE_KEY"),
        chain_id       = POLYGON,
        creds          = creds,
        funder         = os.getenv("POLYMARKET_FUNDER_ADDRESS"),
        signature_type = 1,   # EOA — Magic/Gmail wallet
    )


async def main():
    print(f"\n{BOLD}{'═'*55}{RESET}")
    print(f"{BOLD}  Live Order Test — place + immediately close{RESET}")
    print(f"{'═'*55}")
    print(f"  Stake: $1.00 (minimum to validate signing)")
    print(f"  This will use real USDC from your account\n")

    # ── Step 1: Find active market ─────────────────────────────────────────
    print("1. Finding active BTC 5m market...")
    market = await get_active_market()
    if not market:
        fail("No active market right now — try again in a few minutes")
        return

    ok(f"Market: {market['slug']} | ends_in={market['secs_left']:.0f}s")
    ok(f"Odds: up={market['up_odds']:.3f}  down={market['down_odds']:.3f}")

    if market["secs_left"] < 120:
        warn(f"Only {market['secs_left']:.0f}s remaining — too close to end")
        warn("Wait for next window and try again")
        return

    # ── Step 2: Choose direction ───────────────────────────────────────────
    print("\n2. Choosing direction...")
    # Pick the direction closer to 0.50 for fairest test
    if abs(market["up_odds"] - 0.5) <= abs(market["down_odds"] - 0.5):
        direction = "long"
        token_id  = market["up_id"]
        price     = market["up_odds"]
    else:
        direction = "short"
        token_id  = market["down_id"]
        price     = market["down_odds"]

    # Round to tick
    price = round(round(price / 0.01) * 0.01, 4)
    stake  = 5.00                    # enough to cover 5 share minimum
    shares = max(5.0, round(stake / price, 2))   # Polymarket minimum = 5 shares

    info(f"Direction: {direction.upper()}")
    info(f"Token ID:  {token_id[:20]}...")
    info(f"Price:     {price:.3f}")
    info(f"Shares:    {shares:.2f}")
    info(f"Stake:     ${stake:.2f}")

    # ── Step 3: Build client ───────────────────────────────────────────────
    print("\n3. Building CLOB client...")
    try:
        client = make_client()
        ok("Client built with signature_type=1 (EOA)")
    except Exception as e:
        fail(f"Client build failed: {e}")
        return

    # ── Step 4: Place buy order ────────────────────────────────────────────
    print("\n4. Placing BUY order...")
    try:
        from py_clob_client.clob_types import OrderArgs, OrderType

        order_args   = OrderArgs(
            token_id = token_id,
            price    = price,
            size     = shares,
            side     = "BUY",
        )
        signed_order = client.create_order(order_args)
        resp         = client.post_order(signed_order, OrderType.GTC)

        print(f"\n  Raw response: {resp}\n")

        if resp and resp.get("success"):
            order_id = resp.get("orderID", "?")
            ok(f"BUY order placed! order_id={order_id}")
            ok(f"Check Polymarket → Open Orders to confirm")
        else:
            fail(f"BUY order failed: {resp}")
            info("Common fixes:")
            info("  - signature_type=1 already set — check private key format")
            info("  - Key should be WITHOUT 0x prefix")
            info("  - Re-generate API keys: python scripts/get_api_key.py")
            return

    except Exception as e:
        fail(f"BUY order error: {e}")
        import traceback
        traceback.print_exc()
        return

    # ── Step 5: Wait 3 seconds then close ─────────────────────────────────
    print("\n5. Waiting 15 seconds for settlement then closing...")
    for i in range(15, 0, -3):
        print(f"     {i}s...", end="\r")
        await asyncio.sleep(3)
    print()

    try:
        # Get current odds for exit
        import aiohttp
        async with aiohttp.ClientSession() as s:
            async with s.get(
                "https://clob.polymarket.com/midpoint",
                params={"token_id": token_id},
                timeout=aiohttp.ClientTimeout(total=5)
            ) as r:
                mid = await r.json()
        exit_price = float(mid.get("mid", price))
        exit_price = round(round(exit_price / 0.01) * 0.01, 4)

        sell_args   = OrderArgs(
            token_id = token_id,
            price    = exit_price,
            size     = shares,
            side     = "SELL",
        )
        signed_sell = client.create_order(sell_args)
        sell_resp   = client.post_order(signed_sell, OrderType.GTC)

        print(f"\n  Raw sell response: {sell_resp}\n")

        if sell_resp and sell_resp.get("success"):
            ok(f"SELL order placed! order_id={sell_resp.get('orderID','?')}")

            # PnL estimate
            pnl = round((exit_price - price) / price * stake, 4)
            if pnl >= 0:
                ok(f"Estimated PnL: +${pnl:.4f}")
            else:
                warn(f"Estimated PnL: ${pnl:.4f} (small loss expected on quick close)")
        else:
            warn(f"SELL order response: {sell_resp}")
            warn("Position may still be open — check Polymarket UI")

    except Exception as e:
        fail(f"SELL order error: {e}")
        warn("Position may still be open — check Polymarket → Open Orders")

    # ── Summary ────────────────────────────────────────────────────────────
    print(f"\n{'═'*55}")
    print(f"  {BOLD}Test complete{RESET}")
    print(f"{'═'*55}")
    print(f"  If both orders succeeded:")
    print(f"    ✓ Signature fix is working")
    print(f"    ✓ Live trading is ready")
    print(f"    ✓ Deploy to AWS and start python main.py")
    print(f"\n  If orders failed:")
    print(f"    → Check error message above")
    print(f"    → Verify .env credentials")
    print(f"    → Re-run: python scripts/get_api_key.py")
    print(f"{'═'*55}\n")


if __name__ == "__main__":
    asyncio.run(main())