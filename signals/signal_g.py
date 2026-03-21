"""
Bot G Signal — Universal Crypto (Multi-Asset Updown)
Extends Bot A's Chainlink Lag logic to all crypto updown markets
(ETH, SOL, BNB, etc.). Uses Binance momentum as the primary signal
and applies the same lag arbitrage strategy.
"""

from dataclasses import dataclass


@dataclass
class BotGResult:
    asset: str
    score: float      = 0.0
    direction: str    = "skip"
    tradeable: bool   = False
    skip_reason: str  = None
    components: dict  = None


class BotGSignal:
    def __init__(self, min_confidence: float = 0.003):
        self.min_confidence = min_confidence

    def evaluate(self, asset: str, momentum: float,
                 chainlink_deviation: float) -> BotGResult:
        """
        asset: "ETH", "SOL", "BNB" etc.
        momentum: 30s price change ratio from Binance (positive = rising)
        chainlink_deviation: fractional deviation from Chainlink reference
        """
        result = BotGResult(
            asset=asset,
            components={
                "momentum":            momentum,
                "chainlink_deviation": chainlink_deviation,
                "min_confidence":      self.min_confidence,
            }
        )

        if momentum == 0.0:
            result.skip_reason = "no_momentum"
            return result

        # Combined score: momentum magnitude weighted by chainlink confirmation
        # If CL agrees with momentum → boost. If CL disagrees → dampen.
        cl_sign = 1 if (chainlink_deviation * momentum) >= 0 else -1
        raw_score = abs(momentum) * (1.0 + (cl_sign * 0.5))

        if raw_score < self.min_confidence:
            result.skip_reason = f"score_below_threshold:{raw_score:.4f}"
            return result

        result.tradeable = True
        result.score     = round(raw_score, 4)
        result.direction = "long" if momentum > 0 else "short"

        return result
