"""
Run once from your project root to create the correct folder structure.
    python setup_structure.py

Works whether your files are flat in root or already partially organised.
"""

import os
import shutil

ROOT = os.path.dirname(os.path.abspath(__file__))

FOLDERS = [
    "database",
    "feeds",
    "signals",
    "bots",
    "risk",
    "execution",
    "analytics",
]

MOVES = {
    "db.py":           "database/db.py",
    "binance_ws.py":   "feeds/binance_ws.py",
    "chainlink.py":    "feeds/chainlink.py",
    "polymarket.py":   "feeds/polymarket.py",
    "signal_a.py":     "signals/signal_a.py",
    "signal_b.py":     "signals/signal_b.py",
    "base_bot.py":     "bots/base_bot.py",
    "bot_a.py":        "bots/bot_a.py",
    "bot_b.py":        "bots/bot_b.py",
    "manager.py":      "risk/manager.py",
    "trader.py":       "execution/trader.py",
    "comparison.py":   "analytics/comparison.py",
}

print("\n=== Polymarket Bot — Setup Structure ===\n")

# Create folders and __init__.py
for folder in FOLDERS:
    path = os.path.join(ROOT, folder)
    os.makedirs(path, exist_ok=True)
    init = os.path.join(path, "__init__.py")
    if not os.path.exists(init):
        open(init, "w").close()
    print(f"  ✓ {folder}/")

print()

# Move flat files into correct subfolders
for src_name, dest_rel in MOVES.items():
    src  = os.path.join(ROOT, src_name)
    dest = os.path.join(ROOT, dest_rel)
    if os.path.exists(src):
        shutil.move(src, dest)
        print(f"  ✓ {src_name:<20} → {dest_rel}")
    elif os.path.exists(dest):
        print(f"  · {dest_rel:<36} already in place")
    else:
        print(f"  ✗ {src_name:<20} not found — download it from Claude")

# Create data directory
data_dir = os.path.join(ROOT, "data")
os.makedirs(data_dir, exist_ok=True)
print(f"\n  ✓ data/  (databases will be stored here)")

print("\n=== Done ===")
print("\nNext steps:")
print("  1. cp .env.example .env  and fill in your ALCHEMY_RPC_URL")
print("  2. python test_bot.py    to verify everything works")
print("  3. python main.py        to start the bot\n")
