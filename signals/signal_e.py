"""
Bot E Signal — Adaptive Momentum
Pulse signal based on 30s/60s price velocity.
Captures short-term directional trends in 5m/15m markets.
"""

from dataclasses import dataclass
from config import MIN_ODDS, MAX_ODDS

@dataclass
class BotEResult:
    market_id: str
    token_id: str
    velocity: float
    score: float         = 0.0
    direction: str       = "skip"  # "long" or "short"
    tradeable: bool      = False
    skip_reason: str     = None
    components: dict     = None

class BotESignal:
    def __init__(self, min_velocity: float = 0.015):
        self.min_velocity = min_velocity

    def evaluate(self, market_id: str, token_id: str, 
                 velocity: float, current_price: float) -> BotEResult:
        
        result = BotEResult(
            market_id=market_id,
            token_id=token_id,
            velocity=velocity,
            components={
                "velocity": velocity,
                "current_price": current_price,
                "min_threshold": self.min_velocity
            }
        )

        # Basic Check: Odds within sweet spot
        if current_price < MIN_ODDS or current_price > MAX_ODDS:
            result.skip_reason = "odds_out_of_bounds"
            return result

        # Momentum Pulse
        if abs(velocity) >= self.min_velocity:
            result.tradeable = True
            result.direction = "long" if velocity > 0 else "short"
            # Normalized Score: velocity relative to threshold
            result.score = round(abs(velocity) / self.min_velocity, 2)
        else:
            result.skip_reason = f"velocity_low:{abs(velocity):.4f}"

        return result
