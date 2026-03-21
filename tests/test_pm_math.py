import pytest
from utils.pm_math import calculate_hedge_price, calculate_vwap

def test_calculate_hedge_price():
    # Standard 50/50 split
    assert calculate_hedge_price(0.50) == 0.48
    # Skewed market
    assert calculate_hedge_price(0.60) == 0.38
    # Low price
    assert calculate_hedge_price(0.10) == 0.88
    # Edge case
    assert calculate_hedge_price(None) == 0.0

def test_calculate_vwap_single_level():
    # Only one level, should return that price if depth is within size
    book = [{"price": 0.50, "size": 100}]
    assert calculate_vwap(book, 10) == 0.50
    assert calculate_vwap(book, 50) == 0.50

def test_calculate_vwap_multi_level():
    # Two levels: 5 USDC @ 0.50 (10 shares) and 5 USDC @ 0.60 (8.33 shares)
    # Total depth 8 USDC: 5 @ 0.50 + 3 @ 0.60
    book = [
        {"price": 0.50, "size": 10},
        {"price": 0.60, "size": 10}
    ]
    # Total shares for 8 USDC: 10 + (3 / 0.60) = 10 + 5 = 15
    # VWAP = 8 / 15 = 0.5333
    assert calculate_vwap(book, 8) == 0.5333

def test_calculate_vwap_insufficient_depth():
    # Total available is 5 USDC, we want 10
    book = [{"price": 0.50, "size": 10}]
    # It should return the weighted average of what it COULD get (0.50)
    assert calculate_vwap(book, 10) == 0.50

def test_calculate_vwap_empty():
    assert calculate_vwap([], 10) == 0.0
    assert calculate_vwap(None, 10) == 0.0
