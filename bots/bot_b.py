"""Bot B — Hybrid Strategy"""

import logging
from config import BOT_B_BANKROLL, BOT_B_DB_PATH
from bots.base_bot import BaseBot
from signals.signal_b import BotBSignal

logger = logging.getLogger("bot_b")


class BotB(BaseBot):

    BOT_ID            = "B"
    DB_PATH           = BOT_B_DB_PATH
    STARTING_BANKROLL = BOT_B_BANKROLL

    def __init__(self, binance, chainlink, poly):
        super().__init__(binance, chainlink, poly)
        self._signal = BotBSignal()

    def evaluate_signal(self):
        return self._signal.evaluate(
            momentum_30s  = self.binance.momentum_30s,
            momentum_60s  = self.binance.momentum_60s,
            rsi_signal    = self.binance.rsi_signal,
            volume_zscore = self.binance.volume_zscore,
            odds_velocity = self.poly.odds_velocity,
            lag_signal    = self.chainlink.lag_signal,
        )
