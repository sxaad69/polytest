"""
Bot B Signal — Hybrid Strategy
Momentum + RSI + volume + odds velocity.
Chainlink lag boosts or dampens but is NOT required to trade.
"""

from dataclasses import dataclass, field
from config import BOT_B_MIN_CONFIDENCE, BOT_B_SIGNAL_WEIGHTS, BOT_B_LAG_BOOST, BOT_B_LAG_DAMPEN


@dataclass
class BotBResult:
    score: float             = 0.0
    direction: str           = "skip"
    tradeable: bool          = False
    lag_boost_applied: bool  = False
    lag_dampen_applied: bool = False
    components: dict         = field(default_factory=dict)
    skip_reason: str         = None


class BotBSignal:

    def evaluate(self, momentum_30s: float, momentum_60s: float,
                 rsi_signal: float, volume_zscore: float,
                 odds_velocity: float, lag_signal: float) -> BotBResult:

        result = BotBResult()
        w      = BOT_B_SIGNAL_WEIGHTS

        s_momentum = self._norm_momentum(momentum_30s, momentum_60s)
        s_rsi      = rsi_signal
        s_volume   = self._norm_volume(volume_zscore, s_momentum)
        s_odds_vel = self._norm_odds_velocity(odds_velocity)

        score = (
            s_momentum * w["momentum"]      +
            s_rsi      * w["rsi"]           +
            s_volume   * w["volume"]        +
            s_odds_vel * w["odds_velocity"]
        )
        score = max(-1.0, min(1.0, score))

        # Chainlink lag amplifier
        if abs(lag_signal) > 0.01:
            if (lag_signal > 0) == (score > 0):
                score = score * BOT_B_LAG_BOOST
                result.lag_boost_applied = True
            else:
                score = score * BOT_B_LAG_DAMPEN
                result.lag_dampen_applied = True
            score = max(-1.0, min(1.0, score))

        result.score     = round(score, 4)
        result.direction = "long" if score > 0 else "short"
        result.tradeable = abs(score) >= BOT_B_MIN_CONFIDENCE

        if not result.tradeable:
            result.skip_reason = f"confidence_too_low:{abs(score):.3f}"

        result.components = {
            "momentum":   round(s_momentum * w["momentum"], 4),
            "rsi":        round(s_rsi      * w["rsi"],      4),
            "volume":     round(s_volume   * w["volume"],   4),
            "odds_vel":   round(s_odds_vel * w["odds_velocity"], 4),
            "lag_signal": lag_signal,
        }
        return result

    def _norm_momentum(self, m30: float, m60: float) -> float:
        blended = m30 * 0.6 + m60 * 0.4
        return max(-1.0, min(1.0, blended / 0.3))

    def _norm_volume(self, zscore: float, momentum_signal: float) -> float:
        if zscore > 1.5:
            return momentum_signal * 0.5
        elif zscore < -0.5:
            return momentum_signal * -0.2
        return 0.0

    def _norm_odds_velocity(self, velocity: float) -> float:
        return max(-1.0, min(1.0, velocity / 0.10))
