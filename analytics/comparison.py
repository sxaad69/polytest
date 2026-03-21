"""
Comparison Analytics
Side-by-side report for Bot A vs Bot B.
Run any time: python -m analytics.comparison
"""

import sqlite3
from datetime import date
from config import BOT_A_DB_PATH, BOT_B_DB_PATH, BOT_A_BANKROLL, BOT_B_BANKROLL


def _one(db_path: str, sql: str, params=()) -> dict:
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row  = conn.execute(sql, params).fetchone()
        conn.close()
        return dict(row) if row else {}
    except Exception:
        return {}


def _query(db_path: str, sql: str, params=()) -> list:
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def print_comparison(bot_balances: dict = None):
    """Prints a comparison table for all bots in the given dict {name: balance}."""
    from config import BOT_A_DB_PATH, BOT_B_DB_PATH, BOT_C_DB_PATH, BOT_D_DB_PATH, \
                       BOT_E_DB_PATH, BOT_F_DB_PATH, BOT_G_DB_PATH, \
                       BOT_A_BANKROLL, BOT_B_BANKROLL, BOT_C_BANKROLL, BOT_D_BANKROLL, \
                       BOT_E_BANKROLL, BOT_F_BANKROLL, BOT_G_BANKROLL
    
    db_paths = {
        "A": BOT_A_DB_PATH, "B": BOT_B_DB_PATH, "C": BOT_C_DB_PATH,
        "D": BOT_D_DB_PATH, "E": BOT_E_DB_PATH, "F": BOT_F_DB_PATH, "G": BOT_G_DB_PATH
    }
    initial_bankrolls = {
        "A": BOT_A_BANKROLL, "B": BOT_B_BANKROLL, "C": BOT_C_BANKROLL,
        "D": BOT_D_BANKROLL, "E": BOT_E_BANKROLL, "F": BOT_F_BANKROLL, "G": BOT_G_BANKROLL
    }
    
    if not bot_balances:
        bot_balances = {"A": BOT_A_BANKROLL, "B": BOT_B_BANKROLL}
    
    today = date.today().isoformat()

    def stats(path):
        return _one(path, """
            SELECT COUNT(*) AS total,
                SUM(CASE WHEN outcome='win'  THEN 1 END)    AS wins,
                SUM(CASE WHEN outcome='loss' THEN 1 END)    AS losses,
                ROUND(SUM(pnl_usdc),4)                      AS pnl,
                ROUND(AVG(CASE WHEN outcome='win'
                    THEN 1.0 ELSE 0.0 END)*100,2)           AS win_rate,
                ROUND(AVG(pnl_usdc),6)                      AS expectancy,
                SUM(CASE WHEN exit_reason='take_profit'   THEN 1 END) AS tp,
                SUM(CASE WHEN exit_reason='trailing_stop' THEN 1 END) AS ts,
                SUM(CASE WHEN exit_reason='hard_stop'     THEN 1 END) AS hs
            FROM trades WHERE DATE(ts_entry)=? AND resolved=1
        """, (today,))

    # Data collection for enabled bots
    data = {}
    for bot_id, bal in bot_balances.items():
        path = db_paths.get(bot_id)
        if not path: continue
        data[bot_id] = {
            "stats": stats(path),
            "balance": bal,
            "initial": initial_bankrolls.get(bot_id, 100.0)
        }

    if not data: return

    def v(d, k, fmt="{}"):
        val = d.get(k)
        val = 0 if val is None else val
        try: return fmt.format(val)
        except: return str(val)

    print("\n" + "═" * (30 + 15 * len(data)))
    print(f"  Bot Comparison — {today}")
    print("═" * (30 + 15 * len(data)))
    
    headers = "  " + f"{'Metric':<25}"
    for bid in data.keys(): headers += f"{f'Bot {bid}':>15}"
    print(headers)
    print("  " + "─" * (25 + 15 * len(data)))

    metrics = [
        ("Bankroll", lambda d: f"${d['balance']:.2f}"),
        ("Trades", lambda d: v(d['stats'], "total")),
        ("Wins", lambda d: v(d['stats'], "wins")),
        ("Losses", lambda d: v(d['stats'], "losses")),
        ("Win rate", lambda d: v(d['stats'], "win_rate", "{}%")),
        ("Total PnL", lambda d: v(d['stats'], "pnl", "{:+}")),
        ("TP exits", lambda d: v(d['stats'], "tp")),
        ("Hard stop exits", lambda d: v(d['stats'], "hs")),
    ]

    for label, fetcher in metrics:
        row = f"  {label:<25}"
        for d in data.values():
            row += f"{fetcher(d):>15}"
        print(row)

    print("  " + "─" * (25 + 15 * len(data)))
    print("═" * 70 + "\n")


def _verdict(a: dict, b: dict):
    print("\n  GO-LIVE VERDICT")
    print("  " + "─" * 66)
    issues_a, issues_b = [], []

    total_a  = a.get("total") or 0
    total_b  = b.get("total") or 0
    wr_a     = float(a.get("win_rate") or 0)
    wr_b     = float(b.get("win_rate") or 0)
    exp_a    = float(a.get("expectancy") or 0)
    exp_b    = float(b.get("expectancy") or 0)

    if total_a < 50:
        issues_a.append(f"sample too small ({total_a} trades, need 50+)")
    if total_b < 50:
        issues_b.append(f"sample too small ({total_b} trades, need 50+)")
    if wr_a < 52:
        issues_a.append(f"win rate {wr_a:.1f}% below 52%")
    if wr_b < 52:
        issues_b.append(f"win rate {wr_b:.1f}% below 52%")
    if exp_a < 0:
        issues_a.append(f"negative expectancy ({exp_a:.5f})")
    if exp_b < 0:
        issues_b.append(f"negative expectancy ({exp_b:.5f})")

    def show(label, issues, exp, wr):
        if issues:
            print(f"\n  Bot {label}: ✗ NOT READY")
            for i in issues:
                print(f"    - {i}")
        else:
            print(f"\n  Bot {label}: ✓ READY — exp={exp:.5f} wr={wr:.1f}%")

    show("A", issues_a, a.get("expectancy",0), a.get("win_rate",0))
    show("B", issues_b, b.get("expectancy",0), b.get("win_rate",0))

    exp_a = a.get("expectancy") or 0
    exp_b = b.get("expectancy") or 0
    if not issues_a and not issues_b:
        winner = "A" if exp_a >= exp_b else "B"
        print(f"\n  RECOMMENDATION: Go live with Bot {winner} (higher expectancy)")
    elif not issues_a:
        print("\n  RECOMMENDATION: Go live with Bot A only. Bot B not ready.")
    elif not issues_b:
        print("\n  RECOMMENDATION: Go live with Bot B only. Bot A not ready.")
    else:
        print("\n  RECOMMENDATION: Neither ready. Continue paper testing.")


if __name__ == "__main__":
    print_comparison()