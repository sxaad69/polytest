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
        for passed, reason in checks:
            if not passed:
                db.log_skip(reason, confidence, odds, market_id)
                return False, reason
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

        stake = min(
            full_k * KELLY_FRACTION * abs_conf * bankroll,
            bankroll * MAX_BET_PCT
        )
        return round(max(5.0, stake), 2)   # Polymarket minimum = 5 shares


class GlobalRiskManager:
    """
    Portfolio-level risk control.
    """
    def __init__(self, bots_dict: dict):
        self.bots = bots_dict
        import config
        self.max_exposure_pct = config.GLOBAL_MAX_EXPOSURE_PCT
        self.daily_loss_limit = config.GLOBAL_DAILY_LOSS_LIMIT
        self.initial_bankrolls = {
            "A": config.BOT_A_BANKROLL, "B": config.BOT_B_BANKROLL, 
            "C": config.BOT_C_BANKROLL, "D": config.BOT_D_BANKROLL,
            "E": config.BOT_E_BANKROLL, "F": config.BOT_F_BANKROLL, 
            "G": config.BOT_G_BANKROLL
        }
        self._total_bankroll = sum(self.initial_bankrolls.values())

    def can_enter(self, stake: float) -> tuple:
        """Checks if a new trade would exceed global exposure limits."""
        current_exposure = 0.0
        for bot in self.bots.values():
            if hasattr(bot, "executor") and bot.executor:
                for pos in bot.executor._positions.values():
                    current_exposure += pos.get("stake_usdc", 0.0)
        
        limit = self._total_bankroll * self.max_exposure_pct
        if (current_exposure + stake) > limit:
            return False, f"global_exposure_limit:{current_exposure+stake:.1f}/{limit:.1f}"
        
        return True, ""

    def check_health(self) -> bool:
        """Aggregates and enforces global circuit breakers."""
        total_daily_loss = 0.0
        for bid, bot in self.bots.items():
            cb = bot.db.get_cb()
            total_daily_loss += cb.get('daily_loss_usdc', 0.0)
            
        loss_pct = total_daily_loss / max(self._total_bankroll, 1)
        if loss_pct >= self.daily_loss_limit:
            logger.critical("GLOBAL CIRCUIT BREAKER: Total loss %.1f%% | HALTING ALL", 
                          loss_pct*100)
            for bot in self.bots.values():
                b_cb = bot.db.get_cb()
                bot.db.update_cb(b_cb.get('consecutive_losses', 0), 
                                 b_cb.get('daily_loss_usdc', 0),
                                 halted=True, reason="global_loss_limit")
            return False
        
        return True