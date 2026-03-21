"""
Live Terminal Dashboard
Real-time, flickering-free dashboard comparing all active bots and showing recent signals.
Run any time: python -m analytics.dashboard
"""

import time
import sqlite3
from datetime import date
from rich.live import Live
from rich.table import Table
from rich.layout import Layout
from rich.panel import Panel
from rich.console import Console
from rich import box

from config import (
    BOT_A_DB_PATH, BOT_B_DB_PATH, BOT_C_DB_PATH, BOT_D_DB_PATH,
    BOT_E_DB_PATH, BOT_F_DB_PATH, BOT_G_DB_PATH,
    BOT_A_BANKROLL, BOT_B_BANKROLL, BOT_C_BANKROLL, BOT_D_BANKROLL,
    BOT_E_BANKROLL, BOT_F_BANKROLL, BOT_G_BANKROLL,
    BOT_A_ENABLED, BOT_B_ENABLED, BOT_C_ENABLED, BOT_D_ENABLED,
    BOT_E_ENABLED, BOT_F_ENABLED, BOT_G_ENABLED
)

DB_PATHS = {
    "A": BOT_A_DB_PATH, "B": BOT_B_DB_PATH, "C": BOT_C_DB_PATH,
    "D": BOT_D_DB_PATH, "E": BOT_E_DB_PATH, "F": BOT_F_DB_PATH, "G": BOT_G_DB_PATH
}

BANKROLLS = {
    "A": BOT_A_BANKROLL, "B": BOT_B_BANKROLL, "C": BOT_C_BANKROLL,
    "D": BOT_D_BANKROLL, "E": BOT_E_BANKROLL, "F": BOT_F_BANKROLL, "G": BOT_G_BANKROLL
}

ENABLED = {
    "A": BOT_A_ENABLED, "B": BOT_B_ENABLED, "C": BOT_C_ENABLED,
    "D": BOT_D_ENABLED, "E": BOT_E_ENABLED, "F": BOT_F_ENABLED, "G": BOT_G_ENABLED
}


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

def get_stats():
    today = date.today().isoformat()
    data = {}
    
    for bot_id, path in DB_PATHS.items():
        if not ENABLED.get(bot_id, False): continue
        if not path: continue
        
        stats = _one(path, """
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
        
        # Also get active trades count
        active = _one(path, "SELECT COUNT(*) as active FROM trades WHERE resolved=0").get("active", 0)
        
        if not stats:
            stats = {}
            
        data[bot_id] = {
            "stats": stats,
            "active_trades": active,
            "bankroll": BANKROLLS.get(bot_id, 100.0)
        }
    return data

def get_recent_signals(limit=7):
    # Fetch top signals from all active DBs and sort them by ts desc
    all_signals = []
    for bot_id, path in DB_PATHS.items():
        if not ENABLED.get(bot_id, False): continue
        if not path: continue
        
        signals = _query(path, """
            SELECT id, ts, market_id, direction, confidence_score, polymarket_odds, skip_reason 
            FROM signals 
            ORDER BY ts DESC LIMIT 10
        """)
        for s in signals:
            s['bot'] = bot_id
            all_signals.append(s)
            
    # Sort carefully by ISO timestamp
    all_signals.sort(key=lambda x: x['ts'], reverse=True)
    return all_signals[:limit]

def generate_performance_table(data) -> Table:
    table = Table(
        title="[bold cyan]Multi-Bot Performance Matrix[/bold cyan]", 
        box=box.DOUBLE_EDGE, expand=True
    )
    
    table.add_column("Bot", style="bold magenta", justify="center")
    table.add_column("Initial Bal", justify="right")
    table.add_column("Active", justify="center", style="yellow")
    table.add_column("Trades (Res)", justify="center")
    table.add_column("Win Rate", justify="right")
    table.add_column("PnL (USDC)", justify="right")
    table.add_column("Expectancy", justify="right")
    
    for bot_id, info in data.items():
        st = info['stats']
        active = info['active_trades']
        bal = info['bankroll']
        
        total = st.get('total') or 0
        wr = st.get('win_rate') or 0.0
        pnl = st.get('pnl') or 0.0
        exp = st.get('expectancy') or 0.0
        
        wr_str = f"[green]{wr}%[/green]" if wr >= 52 else f"[red]{wr}%[/red]"
        if total == 0: wr_str = "0.0%"
        
        pnl_str = f"[green]+{pnl:.2f}[/green]" if pnl > 0 else (f"[red]{pnl:.2f}[/red]" if pnl < 0 else "0.00")
        exp_str = f"[green]{exp:.4f}[/green]" if exp > 0 else (f"[red]{exp:.4f}[/red]" if exp < 0 else "0.0000")
        
        table.add_row(
            bot_id,
            f"${bal:.2f}",
            str(active) if active > 0 else "-",
            str(total),
            wr_str,
            pnl_str,
            exp_str
        )
        
    return table

def generate_signals_table(signals) -> Table:
    table = Table(title="[bold yellow]Global Signal Feed[/bold yellow]", box=box.SIMPLE, expand=True)
    table.add_column("Time", style="dim")
    table.add_column("Bot", style="magenta")
    table.add_column("Market", style="cyan")
    table.add_column("Dir", justify="center")
    table.add_column("Conf", justify="right")
    table.add_column("Odds", justify="right")
    table.add_column("Status", justify="left")
    
    for s in signals:
        ts = s['ts'][11:19] # Just time component HH:MM:SS
        market_short = str(s['market_id'])[:12] if s['market_id'] else "unknown"
        
        direction = s.get('direction', '')
        dir_color = "green" if direction == "long" else "red" if direction == "short" else "yellow"
        dir_str = f"[{dir_color}]{direction.upper()}[/{dir_color}]"
        
        status = "EXECUTED" if not s['skip_reason'] else f"[dim yellow]SKIPPED: {s['skip_reason']}[/dim yellow]"
        
        conf = s.get('confidence_score') or 0.0
        odds = s.get('polymarket_odds') or 0.0
        
        table.add_row(
            ts,
            s['bot'],
            market_short,
            dir_str,
            f"{conf:.3f}",
            f"{odds:.3f}",
            status
        )
        
    return table

def mk_layout() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="upper", ratio=1),
        Layout(name="lower", ratio=1)
    )
    return layout

def main():
    console = Console()
    console.clear()
    
    layout = mk_layout()

    with Live(layout, refresh_per_second=2, screen=True):
        while True:
            try:
                data = get_stats()
                signals = get_recent_signals(limit=9)
                
                perf_table = generate_performance_table(data)
                sig_table = generate_signals_table(signals)
                
                layout["upper"].update(Panel(perf_table, border_style="blue"))
                layout["lower"].update(Panel(sig_table, border_style="blue"))
                
                time.sleep(2)
            except KeyboardInterrupt:
                break
            except Exception as e:
                time.sleep(5)

if __name__ == "__main__":
    main()
