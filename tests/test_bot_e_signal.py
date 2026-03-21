import pytest
from signals.signal_e import BotESignal

def test_bot_e_momentum_long():
    # min_velocity = 0.015
    signal = BotESignal(min_velocity=0.015)
    
    # Case 1: Strong Positive Velocity
    res = signal.evaluate("m1", "tok1", 0.02, 0.50)
    assert res.tradeable is True
    assert res.direction == "long"
    assert res.score == 1.33 # (0.02 / 0.015)

def test_bot_e_momentum_short():
    signal = BotESignal(min_velocity=0.015)
    
    # Case 2: Strong Negative Velocity
    res = signal.evaluate("m1", "tok1", -0.02, 0.50)
    assert res.tradeable is True
    assert res.direction == "short"
    assert res.score == 1.33

def test_bot_e_low_velocity():
    signal = BotESignal(min_velocity=0.015)
    
    # Case 3: Velocity below threshold
    res = signal.evaluate("m1", "tok1", 0.01, 0.50)
    assert res.tradeable is False
    assert res.skip_reason.startswith("velocity_low")

def test_bot_e_bounds():
    signal = BotESignal(min_velocity=0.015)
    
    # Case 4: Odds out of bounds (Sweet spot is 0.30 - 0.65)
    res = signal.evaluate("m1", "tok1", 0.05, 0.80)
    assert res.tradeable is False
    assert res.skip_reason == "odds_out_of_bounds"
