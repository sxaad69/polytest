import pytest
from signals.signal_f import BotFSignal
from signals.signal_g import BotGSignal


# ── Bot F Tests ────────────────────────────────────────────────────────────────

def test_bot_f_copytrade_long():
    signal = BotFSignal(accuracy_threshold=0.65, min_samples=20)
    # High accuracy slug, price > 0.55 → long
    res = signal.evaluate("m1", "tok1", current_price=0.60,
                          slug_accuracy=0.72, slug_samples=50)
    assert res.tradeable is True
    assert res.direction == "long"
    assert res.score == pytest.approx(0.70, abs=0.01)  # (0.72 - 0.65) * 10

def test_bot_f_copytrade_short():
    signal = BotFSignal(accuracy_threshold=0.65, min_samples=20)
    # Price <= 0.45 → follow market to NO side (direction = short)
    res = signal.evaluate("m1", "tok1", current_price=0.40,
                          slug_accuracy=0.70, slug_samples=30)
    assert res.tradeable is True
    assert res.direction == "short"

def test_bot_f_no_bias():
    signal = BotFSignal(accuracy_threshold=0.65, min_samples=20)
    # Price is too balanced (0.46-0.54) → no clear bias
    res = signal.evaluate("m1", "tok1", current_price=0.50,
                          slug_accuracy=0.70, slug_samples=30)
    assert res.tradeable is False
    assert res.skip_reason.startswith("no_clear_bias")

def test_bot_f_low_accuracy():
    signal = BotFSignal(accuracy_threshold=0.65, min_samples=20)
    res = signal.evaluate("m1", "tok1", current_price=0.60,
                          slug_accuracy=0.55, slug_samples=30)
    assert res.tradeable is False
    assert res.skip_reason.startswith("low_accuracy")

def test_bot_f_insufficient_samples():
    signal = BotFSignal(accuracy_threshold=0.65, min_samples=20)
    res = signal.evaluate("m1", "tok1", current_price=0.60,
                          slug_accuracy=0.80, slug_samples=5)
    assert res.tradeable is False
    assert res.skip_reason.startswith("insufficient_samples")


# ── Bot G Tests ────────────────────────────────────────────────────────────────

def test_bot_g_long_confirmed():
    signal = BotGSignal(min_confidence=0.003)
    # Positive momentum, CL agrees (+ve dev) → score boosted
    res = signal.evaluate("ETH", momentum=0.005, chainlink_deviation=0.002)
    assert res.tradeable is True
    assert res.direction == "long"
    assert res.score > 0.003

def test_bot_g_short_confirmed():
    signal = BotGSignal(min_confidence=0.003)
    # Negative momentum, CL agrees (-ve dev)
    res = signal.evaluate("SOL", momentum=-0.005, chainlink_deviation=-0.002)
    assert res.tradeable is True
    assert res.direction == "short"

def test_bot_g_contradicted():
    signal = BotGSignal(min_confidence=0.003)
    # Momentum positive but CL disagrees (negative dev) → dampened score
    res = signal.evaluate("BNB", momentum=0.003, chainlink_deviation=-0.002)
    # Score = 0.003 * (1 - 0.5) = 0.0015 → below threshold
    assert res.tradeable is False

def test_bot_g_no_momentum():
    signal = BotGSignal(min_confidence=0.003)
    res = signal.evaluate("ETH", momentum=0.0, chainlink_deviation=0.001)
    assert res.tradeable is False
    assert res.skip_reason == "no_momentum"
