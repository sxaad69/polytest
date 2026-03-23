"""
Bot G Signal — Universal Crypto (Multi-Asset Updown)
Extends Bot A's Chainlink Lag logic to all crypto updown markets
(ETH, SOL, BNB, etc.). Uses Binance momentum as the primary signal
and applies the same lag arbitrage strategy.
"""

from dataclasses import dataclass
from config import BOT_G_MOMENTUM_CEILING


@dataclass
class BotGResult:
    asset: str
    score: float      = 0.0
    direction: str    = "skip"
    tradeable: bool   = False
    skip_reason: str  = None
    components: dict  = None


class BotGSignal:
    def __init__(self, min_confidence: float = 0.035):
        # 0.035 is the minimum tradeable momentum signal threshold. 
        # Anything below this is pure noise (22% win rate on 800+ trades). 
        # By setting this to 0.035, we ensure only high-volatility 
        # momentum spikes trigger the bot, preserving our $30 capital.
        self.min_confidence = min_confidence

    def evaluate(self, asset: str, momentum: float,
                 asset_price: float | None, poly_mid: float) -> BotGResult:
        """
        Calculates signal confidence based on Binance momentum and poly lag.
        asset_price : Current spot price from Binance/Coinbase
        poly_mid    : Current mid-price (odds) from Polymarket CLOB
        poly_lag    : Distance from 0.50 — measures how much the market has
                      already priced in the move (closer to 0 = more lag edge)
        """
        poly_lag = round(0.50 - poly_mid, 4)   # + means poly is lagging (bullish)
                                                 # - means poly already priced move in
        result = BotGResult(
            asset=asset,
            components={
                "momentum":        momentum,
                "asset_price":     asset_price,
                "poly_mid":        poly_mid,
                "poly_lag":        poly_lag,
                "min_confidence":  self.min_confidence,
                "ceiling":         BOT_G_MOMENTUM_CEILING,
            }
        )

        # 1. Zero-momentum gate
        if abs(momentum) <= 0.0:
            result.skip_reason = "zero_momentum"
            return result

        raw_score = abs(momentum)

        # 2. Momentum floor — below this is noise
        if raw_score < self.min_confidence:
            result.skip_reason = f"below_floor:{raw_score:.4f}"
            return result

        # 3. Momentum ceiling — above this market is already overextended (0% win rate)
        if raw_score > BOT_G_MOMENTUM_CEILING:
            result.skip_reason = f"above_ceiling:{raw_score:.4f}"
            return result

        # 4. The Dead Zone (from data: 32-39% win rate, kills PnL)
        if 0.040 < raw_score <= 0.070:
            result.skip_reason = f"dead_zone:{raw_score:.4f}"
            return result

        result.tradeable = True
        result.score     = round(raw_score, 4)
        
        # 5. Direction logic (data-driven)
        # Low momentum zone (0.035-0.040) -> High likelihood of Mean Reversion. 
        # We short-bias these to catch the 'rubber band' snap-back.
        if raw_score <= 0.040:
            # Weak signal zone -> Mean Reversion -> ALWAYS SHORT
            result.direction = "short"
        else:
            # Strong signal zone -> Trend Continuation -> Follow Momentum
            result.direction = "long" if momentum > 0 else "short"

        return result
