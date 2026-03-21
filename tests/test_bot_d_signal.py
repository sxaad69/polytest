import pytest
from signals.signal_d import BotDSignal

# BOT_D_SPIKE_THRESHOLD = 0.05

def test_bot_d_fade_spike_up():
    """A sharp upward spike should trigger a SHORT (fade) entry."""
    signal = BotDSignal(spike_threshold=0.05, fade_enabled=True)
    res = signal.evaluate("m1", "tok1", velocity=0.07, current_price=0.55)
    assert res.tradeable is True
    assert res.direction == "short"     # Fade: spike was UP, we go opposite
    assert res.score == pytest.approx(1.40, abs=0.01)
    assert res.spike_magnitude == pytest.approx(0.07, abs=0.001)

def test_bot_d_fade_spike_down():
    """A sharp downward spike should trigger a LONG (fade) entry."""
    signal = BotDSignal(spike_threshold=0.05, fade_enabled=True)
    res = signal.evaluate("m1", "tok1", velocity=-0.06, current_price=0.45)
    assert res.tradeable is True
    assert res.direction == "long"      # Fade: spike was DOWN, we go opposite

def test_bot_d_ride_mode():
    """In ride mode, spike up → LONG, spike down → SHORT."""
    signal = BotDSignal(spike_threshold=0.05, fade_enabled=False)
    res = signal.evaluate("m1", "tok1", velocity=0.08, current_price=0.50)
    assert res.tradeable is True
    assert res.direction == "long"      # Ride: spike up → long

def test_bot_d_small_spike():
    """A spike below threshold should not trigger."""
    signal = BotDSignal(spike_threshold=0.05, fade_enabled=True)
    res = signal.evaluate("m1", "tok1", velocity=0.03, current_price=0.50)
    assert res.tradeable is False
    assert res.skip_reason.startswith("spike_too_small")

def test_bot_d_bounds():
    """Odds out of the allowed range should skip."""
    signal = BotDSignal(spike_threshold=0.05, fade_enabled=True)
    res = signal.evaluate("m1", "tok1", velocity=0.10, current_price=0.90)
    assert res.tradeable is False
    assert res.skip_reason == "odds_out_of_bounds"
