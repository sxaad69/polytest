"""
Bot D Signal — Sports Spike Detector
Fires when a sports market experiences a sudden volume/odds spike
(e.g. a team scores a goal, makes a basket, scores a TD).
These spikes are often mean-reverting — the true probability
was partially priced in before the event, so the spike
overshoots and corrects.
Strategy: Fade the spike (counter-trend) OR ride momentum
depending on spike magnitude and timing in game.
"""

from dataclasses import dataclass
from config import MIN_ODDS, MAX_ODDS, BOT_D_SPIKE_THRESHOLD, BOT_D_FADE_ENABLED


@dataclass
class BotDResult:
    market_id: str
    token_id: str
    velocity: float
    spike_magnitude: float = 0.0
    score: float           = 0.0
    direction: str         = "skip"
    tradeable: bool        = False
    skip_reason: str       = None
    components: dict       = None


class BotDSignal:
    def __init__(self, spike_threshold: float = 0.05,
                 fade_enabled: bool = True):
        self.spike_threshold = spike_threshold
        self.fade_enabled    = fade_enabled

    def evaluate(self, market_id: str, token_id: str,
                 velocity: float, current_price: float) -> BotDResult:

        result = BotDResult(
            market_id=market_id,
            token_id=token_id,
            velocity=velocity,
            components={
                "velocity":        velocity,
                "current_price":   current_price,
                "spike_threshold": self.spike_threshold,
                "fade_mode":       self.fade_enabled,
            }
        )

        # Sanity: price in playable range
        if current_price < MIN_ODDS or current_price > MAX_ODDS:
            result.skip_reason = "odds_out_of_bounds"
            return result

        spike = abs(velocity)
        result.spike_magnitude = spike

        if spike < self.spike_threshold:
            result.skip_reason = f"spike_too_small:{spike:.4f}"
            return result

        # Strategy:
        # FADE mode:  bet against the spike (mean reversion)
        # RIDE mode:  bet with the spike (momentum)
        if self.fade_enabled:
            # Bet opposite of the velocity direction
            result.direction = "short" if velocity > 0 else "long"
        else:
            result.direction = "long" if velocity > 0 else "short"

        result.tradeable = True
        # Score = how much larger the spike is vs threshold
        result.score = round(spike / self.spike_threshold, 2)

        return result
