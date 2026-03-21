"""
Bot F Signal — Copytrade (Market Slug Mirroring)
Follows "smart money" markets where historical resolution accuracy
indicates the crowd consistently prices the correct outcome.
If a market slug has resolved > ACCURACY_THRESHOLD correctly in the
past, we mirror the current market bias (buy whichever side is > 0.55).
"""

from dataclasses import dataclass
from config import MIN_ODDS, MAX_ODDS, BOT_F_ACCURACY_THRESHOLD, BOT_F_MIN_SAMPLES


@dataclass
class BotFResult:
    market_id: str
    token_id: str
    score: float     = 0.0
    direction: str   = "skip"
    tradeable: bool  = False
    skip_reason: str = None
    components: dict = None


class BotFSignal:
    def __init__(self, accuracy_threshold: float = 0.65,
                 min_samples: int = 20):
        self.accuracy_threshold = accuracy_threshold
        self.min_samples        = min_samples

    def evaluate(self, market_id: str, token_id: str,
                 current_price: float,
                 slug_accuracy: float, slug_samples: int) -> BotFResult:

        result = BotFResult(
            market_id=market_id,
            token_id=token_id,
            components={
                "current_price":    current_price,
                "slug_accuracy":    slug_accuracy,
                "slug_samples":     slug_samples,
                "accuracy_thresh":  self.accuracy_threshold,
            }
        )

        # Need enough historical samples to trust the slug
        if slug_samples < self.min_samples:
            result.skip_reason = f"insufficient_samples:{slug_samples}"
            return result

        # Slug must have proven accuracy
        if slug_accuracy < self.accuracy_threshold:
            result.skip_reason = f"low_accuracy:{slug_accuracy:.2f}"
            return result

        # Price within playable range
        if current_price < MIN_ODDS or current_price > MAX_ODDS:
            result.skip_reason = "odds_out_of_bounds"
            return result

        # Mirror the market bias: buy whichever side is favourite
        # (>0.55 = market is pricing a clear favourite)
        if current_price >= 0.55:
            result.direction = "long"   # Market favours YES — follow it
        elif current_price <= 0.45:
            result.direction = "short"  # Market favours NO — buy NO token
        else:
            result.skip_reason = f"no_clear_bias:{current_price:.3f}"
            return result

        result.tradeable = True
        # Score = accuracy excess above threshold, scaled
        result.score = round((slug_accuracy - self.accuracy_threshold) * 10, 2)

        return result
