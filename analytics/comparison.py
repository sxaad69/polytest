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


def print_comparison(bot_a_balance: float = None, bot_b_balance: float = None):
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

    def dir_stats(path, direction):
        return _one(path, """
            SELECT COUNT(*) AS total,
                ROUND(SUM(pnl_usdc),4) AS pnl,
                ROUND(AVG(CASE WHEN outcome='win'
                    THEN 1.0 ELSE 0.0 END)*100,1) AS win_rate
            FROM trades
            WHERE DATE(ts_entry)=? AND direction=? AND resolved=1
        """, (today, direction))

    def lag_stats(path):
        return _one(path, """
            SELECT COUNT(t.id) AS total,
                ROUND(AVG(CASE WHEN t.outcome='win'
                    THEN 1.0 ELSE 0.0 END)*100,1) AS win_rate,
                ROUND(SUM(t.pnl_usdc),4) AS pnl
            FROM trades t
            JOIN signals s ON t.signal_id=s.id
            WHERE DATE(t.ts_entry)=? AND s.chainlink_lag_flag=1 AND t.resolved=1
        """, (today,))

    def skip_count(path):
        r = _one(path, "SELECT COUNT(*) AS cnt FROM skipped WHERE DATE(ts)=?", (today,))
        return r.get("cnt", 0)

    a       = stats(BOT_A_DB_PATH)
    b       = stats(BOT_B_DB_PATH)
    a_long  = dir_stats(BOT_A_DB_PATH, "long")
    b_long  = dir_stats(BOT_B_DB_PATH, "long")
    a_short = dir_stats(BOT_A_DB_PATH, "short")
    b_short = dir_stats(BOT_B_DB_PATH, "short")
    a_lag   = lag_stats(BOT_A_DB_PATH)
    b_lag   = lag_stats(BOT_B_DB_PATH)

    bal_a = bot_a_balance or BOT_A_BANKROLL
    bal_b = bot_b_balance or BOT_B_BANKROLL

    def v(d, k, fmt="{}"):
        val = d.get(k) or 0
        try:
            return fmt.format(val)
        except Exception:
            return str(val)

    print("\n" + "═" * 70)
    print(f"  Dual Bot Comparison — {today}")
    print("═" * 70)
    print(f"  {'Metric':<28} {'Bot A (Lag Only)':>18}  {'Bot B (Hybrid)':>18}")
    print("  " + "─" * 66)

    rows = [
        ("Strategy",          "Chainlink lag only",       "Momentum + lag boost"),
        ("Bankroll",          f"${bal_a:.2f}",             f"${bal_b:.2f}"),
        ("Trades",            v(a,"total"),                v(b,"total")),
        ("Wins",              v(a,"wins"),                 v(b,"wins")),
        ("Losses",            v(a,"losses"),               v(b,"losses")),
        ("Win rate",          v(a,"win_rate","{}%"),       v(b,"win_rate","{}%")),
        ("Total PnL",         v(a,"pnl","{:+}"),          v(b,"pnl","{:+}")),
        ("Expectancy/trade",  v(a,"expectancy","{:.5f}"),  v(b,"expectancy","{:.5f}")),
        ("TP exits",          v(a,"tp"),                   v(b,"tp")),
        ("Trailing exits",    v(a,"ts"),                   v(b,"ts")),
        ("Hard stop exits",   v(a,"hs"),                   v(b,"hs")),
        ("Skipped",           str(skip_count(BOT_A_DB_PATH)), str(skip_count(BOT_B_DB_PATH))),
        ("─"*28,              "─"*18,                     "─"*18),
        ("Long trades",       v(a_long,"total"),           v(b_long,"total")),
        ("Long win rate",     v(a_long,"win_rate","{}%"),  v(b_long,"win_rate","{}%")),
        ("Long PnL",          v(a_long,"pnl","{:+}"),     v(b_long,"pnl","{:+}")),
        ("Short trades",      v(a_short,"total"),          v(b_short,"total")),
        ("Short win rate",    v(a_short,"win_rate","{}%"), v(b_short,"win_rate","{}%")),
        ("Short PnL",         v(a_short,"pnl","{:+}"),    v(b_short,"pnl","{:+}")),
        ("─"*28,              "─"*18,                     "─"*18),
        ("Lag trades",        v(a_lag,"total"),            v(b_lag,"total")),
        ("Lag win rate",      v(a_lag,"win_rate","{}%"),   v(b_lag,"win_rate","{}%")),
        ("Lag PnL",           v(a_lag,"pnl","{:+}"),      v(b_lag,"pnl","{:+}")),
    ]

    for label, va, vb in rows:
        print(f"  {label:<28} {va:>18}  {vb:>18}")

    print("  " + "─" * 66)
    _verdict(a, b)
    print("═" * 70 + "\n")


def _verdict(a: dict, b: dict):
    print("\n  GO-LIVE VERDICT")
    print("  " + "─" * 66)
    issues_a, issues_b = [], []

    if (a.get("total") or 0) < 50:
        issues_a.append(f"sample too small ({a.get('total',0)} trades, need 50+)")
    if (b.get("total") or 0) < 50:
        issues_b.append(f"sample too small ({b.get('total',0)} trades, need 50+)")
    if (a.get("win_rate") or 0) < 52:
        issues_a.append(f"win rate {a.get('win_rate',0):.1f}% below 52%")
    if (b.get("win_rate") or 0) < 52:
        issues_b.append(f"win rate {b.get('win_rate',0):.1f}% below 52%")
    if (a.get("expectancy") or 0) < 0:
        issues_a.append(f"negative expectancy ({a.get('expectancy',0):.5f})")
    if (b.get("expectancy") or 0) < 0:
        issues_b.append(f"negative expectancy ({b.get('expectancy',0):.5f})")

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
