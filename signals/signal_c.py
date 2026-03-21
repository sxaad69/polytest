"""
Bot C Signal — GLOB Arbitrage
Identifies mispriced outcome pairs where Price_Yes + Price_No < Threshold.
Uses L2 Orderbook VWAP for accurate entry pricing.
"""

from dataclasses import dataclass
from config import MIN_ODDS, MAX_ODDS

@dataclass
class BotCResult:
    market_id: str
    token_yes: str
    token_no: str
    yes_price: float
    no_price: float
    sum_price: float
    score: float         = 0.0
    direction: str       = "skip"  # "arb" if valid
    tradeable: bool      = False
    skip_reason: str     = None
    components: dict     = None

class BotCSignal:
    def __init__(self, arb_threshold: float = 0.985):
        self.arb_threshold = arb_threshold

    def evaluate(self, market_id: str, 
                 token_yes: str, token_no: str,
                 yes_vwap: float, no_vwap: float) -> BotCResult:
        
        sum_price = yes_vwap + no_vwap
        
        result = BotCResult(
            market_id=market_id,
            token_yes=token_yes,
            token_no=token_no,
            yes_price=yes_vwap,
            no_price=no_vwap,
            sum_price=sum_price,
            components={
                "yes_price": yes_vwap,
                "no_price": no_vwap,
                "sum_price": sum_price,
                "threshold": self.arb_threshold
            }
        )

        # Basic Sanity Checks
        if yes_vwap <= 0 or no_vwap <= 0:
            result.skip_reason = "insufficient_liquidity"
            return result
        
        if yes_vwap < MIN_ODDS or yes_vwap > MAX_ODDS or \
           no_vwap < MIN_ODDS or no_vwap > MAX_ODDS:
            result.skip_reason = "odds_out_of_bounds"
            return result

        # Arb Calculation
        if sum_price < self.arb_threshold:
            result.tradeable = True
            result.direction = "arb"
            # Score is the "discount" from the threshold, normalized
            result.score = round(max(0.01, self.arb_threshold - sum_price), 4)
        else:
            result.skip_reason = f"no_arb_opportunity:{sum_price:.3f}"

        return result
