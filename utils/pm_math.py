def calculate_hedge_price(price_a: float) -> float:
    """
    Returns the complementary price such that price_a + price_b = 0.98.
    0.98 is the standard Polymarket payout after standard fees for binary outcomes.
    """
    if price_a is None:
        return 0.0
    return round(0.98 - price_a, 4)


def calculate_vwap(bids_or_asks: list, depth: float) -> float:
    """
    Calculates the Volume Weighted Average Price (VWAP) for a given USDC depth.
    bids_or_asks: List of dicts with 'price' and 'size' keys.
    depth: Target depth in USDC.
    """
    if not bids_or_asks or depth <= 0:
        return 0.0

    accumulated_cost = 0.0
    accumulated_shares = 0.0

    for level in bids_or_asks:
        price = float(level.get("price", 0))
        size = float(level.get("size", 0))     # Size in shares
        if price <= 0:
            continue

        level_max_cost = price * size           # Max USDC available at this level
        
        remaining_depth = depth - accumulated_cost
        
        if level_max_cost <= remaining_depth:
            # Take the full level
            accumulated_cost += level_max_cost
            accumulated_shares += size
        else:
            # Take partial level to reach exact depth
            shares_to_take = remaining_depth / price
            accumulated_cost += remaining_depth
            accumulated_shares += shares_to_take
            break

    if accumulated_shares > 0:
        return round(accumulated_cost / accumulated_shares, 4)
    return 0.0
