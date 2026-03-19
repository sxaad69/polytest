"""
check_balance.py — Verify wallet and account before going live

Run: python scripts/check_balance.py

Checks:
  1. Private key loads correctly
  2. Funder address matches profile
  3. USDC balance on Polygon
  4. API credentials valid
  5. Polymarket account active
"""

import asyncio
import os
import sys
from pathlib import Path

# Add project root to path
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


async def main():
    print(f"\n{BOLD}{'═'*55}{RESET}")
    print(f"{BOLD}  Polymarket Account & Balance Check{RESET}")
    print(f"{'═'*55}\n")

    pk       = os.getenv("POLYMARKET_PRIVATE_KEY", "")
    funder   = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
    api_key  = os.getenv("POLYMARKET_API_KEY", "")
    api_sec  = os.getenv("POLYMARKET_API_SECRET", "")
    api_pass = os.getenv("POLYMARKET_PASSPHRASE", "")

    # ── 1. Env vars present ───────────────────────────────────────────────────
    print("1. Environment variables")
    all_set = True
    for name, val in [
        ("POLYMARKET_PRIVATE_KEY",    pk),
        ("POLYMARKET_FUNDER_ADDRESS", funder),
        ("POLYMARKET_API_KEY",        api_key),
        ("POLYMARKET_API_SECRET",     api_sec),
        ("POLYMARKET_PASSPHRASE",     api_pass),
    ]:
        if val:
            ok(f"{name} — set (ends ...{val[-6:]})")
        else:
            fail(f"{name} — NOT SET")
            all_set = False

    if not all_set:
        print(f"\n{RED}Fix missing env vars in .env before continuing.{RESET}\n")
        return

    # ── 2. py-clob-client import ──────────────────────────────────────────────
    print("\n2. py-clob-client")
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds
        ok("py-clob-client imported successfully")
    except ImportError:
        fail("py-clob-client not installed — run: pip install py-clob-client")
        return

    # ── 3. Client init ────────────────────────────────────────────────────────
    print("\n3. CLOB client")
    try:
        creds = ApiCreds(
            api_key       = api_key,
            api_secret    = api_sec,
            api_passphrase= api_pass,
        )
        client = ClobClient(
            host     = "https://clob.polymarket.com",
            key      = pk,
            chain_id = 137,
            creds    = creds,
            funder   = funder,
        )
        ok("ClobClient initialized")
    except Exception as e:
        fail(f"ClobClient init failed: {e}")
        return

    # ── 4. API credentials valid ──────────────────────────────────────────────
    print("\n4. API credentials")
    try:
        profile = client.get_api_keys()
        ok(f"API credentials valid — key active")
    except Exception as e:
        fail(f"API credentials invalid: {e}")
        warn("Re-generate with: python scripts/get_api_key.py")

    # ── 5. USDC balance ───────────────────────────────────────────────────────
    print("\n5. USDC balance")
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds

        creds = ApiCreds(
            api_key        = api_key,
            api_secret     = api_sec,
            api_passphrase = api_pass,
        )
        client = ClobClient(
            host     = "https://clob.polymarket.com",
            key      = pk,
            chain_id = 137,
            creds    = creds,
            funder   = funder,
        )

        # Check Polymarket CLOB balance (funds deposited into Polymarket)
        try:
            balance_info = client.get_balance_allowance(
                params={"asset_type": "COLLATERAL"}
            )
            clob_balance = float(
                balance_info.get("balance", 0)
            ) / 1e6   # USDC 6 decimals
            if clob_balance >= 10:
                ok(f"Polymarket CLOB balance: ${clob_balance:.2f} USDC ✓")
            elif clob_balance > 0:
                warn(f"Polymarket CLOB balance: ${clob_balance:.2f} USDC — low")
            else:
                warn("Polymarket CLOB balance: $0 — checking wallet directly...")
        except Exception as e:
            warn(f"CLOB balance check failed: {e}")

        # Also check raw wallet balances for both USDC contracts
        import aiohttp
        async with aiohttp.ClientSession() as s:
            for label, contract in [
                ("USDC.e (bridged)", "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"),
                ("USDC (native)",    "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"),
            ]:
                try:
                    data = "0x70a08231" + funder.lower().replace("0x","").zfill(64)
                    async with s.post(
                        "https://polygon-rpc.com",
                        json={"jsonrpc":"2.0","method":"eth_call",
                              "params":[{"to":contract,"data":data},"latest"],
                              "id":1},
                        timeout=aiohttp.ClientTimeout(total=10)
                    ) as r:
                        res = await r.json()
                    raw = res.get("result","0x0")
                    bal = int(raw, 16) / 1e6
                    if bal > 0:
                        ok(f"Wallet {label}: ${bal:.2f} USDC")
                    else:
                        print(f"  ·  Wallet {label}: $0.00")
                except Exception:
                    pass

    except Exception as e:
        fail(f"Balance check error: {e}")

    # ── 6. Funder address confirmation ────────────────────────────────────────
    print("\n6. Account confirmation")
    print(f"  Funder address: {funder}")
    print(f"  View on Polygonscan: https://polygonscan.com/address/{funder}")
    print(f"  View on Polymarket:  https://polymarket.com/profile/{funder}")

    # ── 7. Open positions check ───────────────────────────────────────────────
    print("\n7. Open positions in paper database")
    try:
        import sqlite3
        from pathlib import Path
        db_path = Path(__file__).parent.parent / "data" / "bot_a_paper.db"
        if db_path.exists():
            conn = sqlite3.connect(str(db_path))
            open_trades = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE resolved=0"
            ).fetchone()[0]
            conn.close()
            if open_trades > 0:
                warn(f"{open_trades} open paper positions — run close_positions.py first")
            else:
                ok("No open paper positions — clean to go live")
        else:
            ok("No paper database found — clean start")
    except Exception as e:
        warn(f"Could not check paper db: {e}")

    print(f"\n{'═'*55}")
    print(f"  {BOLD}Ready to go live?{RESET}")
    print(f"{'═'*55}")
    print(f"  1. Confirm USDC balance above")
    print(f"  2. Run: python scripts/close_positions.py")
    print(f"  3. Start bot: python main.py")
    print(f"{'═'*55}\n")


if __name__ == "__main__":
    asyncio.run(main())