"""
Risk Manager
Pre-trade filters, circuit breaker, Kelly sizer.
Each bot passes its own Database so state never mixes.
Circuit breaker respects CIRCUIT_BREAKER_ENABLED from config.
"""

import logging
from datetime import date
from config import (
    MIN_ODDS, MAX_ODDS, MIN_BOOK_DEPTH,
    NO_ENTRY_LAST_SECS, MAX_CONSECUTIVE_LOSSES,
    DAILY_LOSS_LIMIT_PCT, MAX_BET_PCT, KELLY_FRACTION,
    CIRCUIT_BREAKER_ENABLED,
)

logger = logging.getLogger(__name__)


class PreTradeFilters:

    def check(self, db, confidence: float, odds: float,
              depth: float, secs_remaining: float,
              market_id: str = None, stake: float = 0.0,
              global_risk: 'GlobalRiskManager' = None) -> tuple:

        checks = [
            self._confidence(confidence),
            self._odds(odds),
            self._depth(depth),
            self._timing(secs_remaining),
            self._circuit_breaker(db),
            self._global_exposure(global_risk, stake),
        ]
        for check_result in checks:
            try:
                # Defensive check for non-tuple returns or incorrect lengths
                if isinstance(check_result, tuple) and len(check_result) >= 2:
                    passed, reason = check_result[0], check_result[1]
                else:
                    passed, reason = True, "invalid_check_return"
                
                if not passed:
                    db.log_skip(reason, confidence, odds, market_id)
                    return False, reason
            except Exception as e:
                logger.error("Filter check iteration error: %s", e)
                continue
        return True, "all_clear"

    def _confidence(self, score: float) -> tuple:
        if score == 0.0:
            return False, "zero_confidence"
        return True, ""

    def _odds(self, odds: float) -> tuple:
        if odds is None:
            return False, "no_odds_data"
        if odds < MIN_ODDS:
            return False, f"odds_too_low:{odds:.2f}"
        if odds > MAX_ODDS:
            return False, f"odds_too_high:{odds:.2f}"
        return True, ""

    def _depth(self, depth: float) -> tuple:
        if depth < MIN_BOOK_DEPTH:
            return False, f"thin_book:{depth:.1f}"
        return True, ""

    def _timing(self, secs: float) -> tuple:
        if secs < NO_ENTRY_LAST_SECS:
            return False, f"window_closing:{secs:.0f}s"
        return True, ""

    def _circuit_breaker(self, db) -> tuple:
        # If circuit breaker is disabled in config, always pass
        if not CIRCUIT_BREAKER_ENABLED:
            return True, ""

        cb = db.get_cb()
        if cb["last_reset_date"] != date.today().isoformat():
            db.reset_cb()
            return True, ""
        if cb["halted"]:
            return False, f"circuit_breaker:{cb['halted_reason']}"
        return True, ""

    def _global_exposure(self, global_risk, stake: float) -> tuple:
        if not global_risk or stake <= 0:
            return True, ""
        return global_risk.can_enter(stake)


class CircuitBreaker:

    def on_result(self, db, outcome: str, pnl: float, starting_bankroll: float):
        # If disabled, still track stats but never halt
        cb = db.get_cb()
        consecutive = cb["consecutive_losses"]
        daily_loss  = cb["daily_loss_usdc"]

        if outcome == "loss":
            consecutive += 1
            daily_loss   = abs(min(0, daily_loss + pnl))
        else:
            consecutive = 0

        halted, reason = False, None

        # Only actually halt if circuit breaker is enabled
        if CIRCUIT_BREAKER_ENABLED:
            if consecutive >= MAX_CONSECUTIVE_LOSSES:
                halted = True
                reason = f"{consecutive}_consecutive_losses"
                logger.warning("[%s] Circuit breaker: %s consecutive losses — HALTED",
                               db.bot_id, consecutive)

            if daily_loss / max(starting_bankroll, 1) >= DAILY_LOSS_LIMIT_PCT:
                halted = True
                reason = f"daily_loss_{daily_loss/starting_bankroll*100:.1f}pct"
                logger.warning("[%s] Circuit breaker: daily loss limit hit — HALTED",
                               db.bot_id)
        else:
            if consecutive >= MAX_CONSECUTIVE_LOSSES:
                logger.warning(
                    "[%s] %s consecutive losses (circuit breaker disabled — continuing)",
                    db.bot_id, consecutive
                )

        db.update_cb(consecutive, daily_loss, halted, reason)


class KellySizer:

    def calculate(self, confidence: float, entry_odds: float,
                  bankroll: float) -> float:
        abs_conf = abs(confidence)
        p = min(0.75, entry_odds + (abs_conf * 0.20))
        q = 1.0 - p
        b = (1.0 - entry_odds) / entry_odds

        if b <= 0:
            return 0.0

        full_k = (p * b - q) / b
        if full_k <= 0:
            return 0.0

        # Calculate pure Kelly stake
        raw_stake = bankroll * (full_k / 2.0) # Half-kelly for safety
        
        # Apply strict bounded rules
        import config
        min_trade = getattr(config, "GLOBAL_MIN_TRADE_SIZE", 0.0)
        max_trade = getattr(config, "GLOBAL_MAX_TRADE_SIZE", 0.0)
        
        final_stake = raw_stake
        
        if min_trade > 0:
            final_stake = max(final_stake, min_trade)
        if max_trade > 0:
            final_stake = min(final_stake, max_trade)
            
        return round(final_stake, 2)


class GlobalRiskManager:
    """
    Portfolio-level risk control.
    """
    def __init__(self, bots_dict: dict):
        self.bots = bots_dict
        import config
        self.max_exposure_pct = config.GLOBAL_MAX_EXPOSURE_PCT
        self.daily_loss_limit = config.GLOBAL_DAILY_LOSS_LIMIT
        self.daily_profit_target = getattr(config, "GLOBAL_DAILY_PROFIT_TARGET", 0.10)
        self.initial_bankrolls = {
            "A": config.BOT_A_BANKROLL, "B": config.BOT_B_BANKROLL, 
            "C": config.BOT_C_BANKROLL, "D": config.BOT_D_BANKROLL,
            "E": config.BOT_E_BANKROLL, "F": config.BOT_F_BANKROLL, 
            "G": config.BOT_G_BANKROLL
        }
        # Only sum bankrolls for BOTS THAT ARE ACTUALLY ACTIVE
        active_ids = {bot.db.bot_id if hasattr(bot, 'db') else k for k, bot in bots_dict.items()}
        self._total_bankroll = sum(v for k, v in self.initial_bankrolls.items() if k in active_ids)

    def can_enter(self, stake: float, token_id: str = None, market_id: str = None) -> tuple:
        """Checks if a new trade would exceed global limits or create a token conflict."""
        current_exposure = 0.0
        for bot in self.bots.values():
            if hasattr(bot, "executor") and bot.executor:
                for pos in bot.executor._positions.values():
                    # Cross-bot token conflict — same token = same direction, no value in doubling
                    if token_id and pos.get("token_id") == token_id:
                        return False, f"cross_bot_conflict:token:{token_id[:12]}"
                    current_exposure += pos.get("stake_usdc", 0.0)
        
        limit = self._total_bankroll * self.max_exposure_pct
        if (current_exposure + stake) > limit:
            return False, f"global_exposure_limit:{current_exposure+stake:.1f}/{limit:.1f}"
        
        return True, ""

    def check_health(self) -> bool:
        """Aggregates and enforces global circuit breakers."""
        import time
        import config
        
        # 1. Pre-check: Are we currently inside a 6-hour penalty box?
        for bid, bot in self.bots.items():
            cb = bot.db.get_cb()
            resume_ts = cb.get("resume_time_ts", 0.0)
            if resume_ts > time.time():
                hours_left = (resume_ts - time.time()) / 3600.0
                logger.warning("GLOBAL CIRCUIT BREAKER ACTIVE: Sleeping for %.1f more hours.", hours_left)
                return False

        # 2. Portfolio Valuation
        total_daily_loss = 0.0
        current_total_bankroll = 0.0
        unrealized_pnl = 0.0
        self.needs_liquidation = False
        self.liquidation_reason = ""
        
        for bid, bot in self.bots.items():
            # a) Use the SETTLED daily loss from the DB (The Truth)
            cb_data = bot.db.get_cb()
            total_daily_loss += cb_data.get('daily_loss_usdc', 0.0)
            
            # b) Balance Check
            if hasattr(bot, "bankroll") and bot.bankroll:
                current_total_bankroll += getattr(bot.bankroll, "balance", 0.0)
                
            # c) Floating PnL of persistent positions (Mark-to-Market using BID odds)
            if hasattr(bot, "executor") and bot.executor:
                for tid, pos in bot.executor._positions.items():
                    cost = pos.get("stake_usdc", 0.0)
                    m = bot.poly.markets.get(pos.get("token_id")) if hasattr(bot, "poly") else None
                    
                    # SAFETY CHECK: If we have no price data yet (Startup Phase),
                    # value the position at COST to prevent a false Panic Sell.
                    if not m or not m.get("odds") or pos.get("entry_odds", 0) <= 0:
                        unrealized_pnl += 0.0 # Valued at cost (no gain, no loss)
                        continue

                    # Use BID odds for conservative "What can I sell for NOW" value
                    # If orderbook is empty, fall back to midpoint
                    current_bid = m.get("bid", m["odds"]) 
                    shares = cost / pos["entry_odds"]
                    
                    # Apply 0.5% buffer for slippage + 2% Taker Fee
                    current_val = (shares * current_bid) * 0.975
                    unrealized_pnl += (current_val - cost)

        loss_pct = total_daily_loss / max(self._total_bankroll, 1)
        
        # True Floating Equity (Realized + Unrealized)
        total_equity = current_total_bankroll + unrealized_pnl
        equity_pct = (total_equity - self._total_bankroll) / max(self._total_bankroll, 1)
        
        # 3. Maximum Loss Trigger (The 6-Hour Rule)
        if loss_pct >= self.daily_loss_limit:
            lock_hours = getattr(config, "GLOBAL_HALT_DURATION_HOURS", 6.0)
            resume_ts = time.time() + (lock_hours * 3600)
            
            logger.critical("🚨 GLOBAL CIRCUIT BREAKER: Settled loss %.1f%% | INITIATING %.1f-HOUR DATABASE LOCK", 
                          loss_pct*100, lock_hours)
                          
            for bot in self.bots.values():
                b_cb = bot.db.get_cb()
                bot.db.update_cb(b_cb.get('consecutive_losses', 0), 
                                 b_cb.get('daily_loss_usdc', 0),
                                 halted=True, reason=f"settled_loss_limit_{lock_hours}h_lock",
                                 resume_time_ts=resume_ts)
            return False
            
        # 4. Profit Ratchet (Floating Equity Spike +20%) -> capture gains
        unreal_target = getattr(config, "GLOBAL_UNREALIZED_PROFIT_TARGET", 0.20)
        if unreal_target > 0 and equity_pct >= unreal_target:
            self.needs_liquidation = True
            self.liquidation_reason = f"profit_ratchet_{equity_pct*100:.1f}pct"
            logger.critical("🚀 PROFIT RATCHET TRIGGERED: +%.1f%% Floating Equity | SECURING ALL BAGS", 
                          equity_pct*100)
            return False

        # 5. Panic Sell (Floating Equity Crash -25%) -> stop systemic bleed
        panic_floor = -0.25
        if equity_pct <= panic_floor:
            self.needs_liquidation = True
            self.liquidation_reason = f"panic_exit_{equity_pct*100:.1f}pct"
            logger.critical("⚠️ PANIC SELL TRIGGERED: %.1f%% Floating Equity Crash | LIQUIDATING PORTFOLIO", 
                          equity_pct*100)
            return False
            
        # 6. Smooth Bankroll Halt (Realized +10%) -> peaceful stop
        realized_profit_pct = (current_total_bankroll - self._total_bankroll) / max(self._total_bankroll, 1)
        if self.daily_profit_target > 0 and realized_profit_pct >= self.daily_profit_target:
            logger.critical("☕ DAILY CASH TARGET HIT: +%.1f%% | FINISHING CURRENT TRADES THEN HALTING", 
                          realized_profit_pct*100)
            for bot in self.bots.values():
                b_cb = bot.db.get_cb()
                bot.db.update_cb(b_cb.get('consecutive_losses', 0), 
                                 b_cb.get('daily_loss_usdc', 0),
                                 halted=True, reason=f"daily_profit_{realized_profit_pct*100:.1f}pct")
            return False
        
        return True