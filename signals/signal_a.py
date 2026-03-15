"""
Bot A Signal — Pure Chainlink Lag Arbitrage
Only trades when Binance has a sustained deviation from Chainlink.
No prediction. No momentum. Structural edge only.
"""

from dataclasses import dataclass
from config import BOT_A_MIN_CONFIDENCE


@dataclass
class BotAResult:
    score: float         = 0.0
    direction: str       = "skip"
    tradeable: bool      = False
    lag_magnitude: float = 0.0
    lag_sustained: float = 0.0
    skip_reason: str     = None
    components: dict     = None


class BotASignal:

    def evaluate(self, lag_signal: float, lag_sustained: float,
                 lag_detected: bool) -> BotAResult:
        result = BotAResult(
            lag_magnitude = abs(lag_signal),
            lag_sustained = lag_sustained,
            components    = {"lag_signal": lag_signal, "lag_sustained": lag_sustained},
        )
        if not lag_detected or lag_signal == 0.0:
            result.skip_reason = "no_lag_detected"
            return result

        result.score     = round(lag_signal, 4)
        result.direction = "long" if lag_signal > 0 else "short"
        result.tradeable = abs(lag_signal) >= BOT_A_MIN_CONFIDENCE

        if not result.tradeable:
            result.skip_reason = f"lag_below_threshold:{abs(lag_signal):.3f}"

        return result
