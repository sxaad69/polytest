import pytest
from signals.signal_c import BotCSignal, BotCResult

def test_bot_c_arb_found():
    signal = BotCSignal(arb_threshold=0.985)
    
    # Case 1: Clear Arb
    # Yes Ask = 0.48, No Ask = 0.48 -> Sum = 0.96 (Threshold 0.985)
    res = signal.evaluate("m1", "tok1", "tok2", 0.48, 0.48)
    assert res.tradeable is True
    assert res.direction == "arb"
    assert res.sum_price == 0.96
    assert res.score == 0.025 # threshold(0.985) - 0.96

def test_bot_c_no_arb():
    signal = BotCSignal(arb_threshold=0.985)
    
    # Case 2: No Arb (Sum > Threshold)
    res = signal.evaluate("m1", "tok1", "tok2", 0.50, 0.50)
    assert res.tradeable is False
    assert res.skip_reason.startswith("no_arb")

def test_bot_c_liquidity_check():
    signal = BotCSignal(arb_threshold=0.985)
    res = signal.evaluate("m1", "tok1", "tok2", 0.0, 0.48)
    assert res.tradeable is False
    assert res.skip_reason == "insufficient_liquidity"

def test_bot_c_bounds_check():
    signal = BotCSignal(arb_threshold=0.985)
    # MIN_ODDS is usually 0.30
    res = signal.evaluate("m1", "tok1", "tok2", 0.10, 0.80)
    assert res.tradeable is False
    assert res.skip_reason == "odds_out_of_bounds"
