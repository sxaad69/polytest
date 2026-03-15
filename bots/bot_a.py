"""Bot A — Pure Chainlink Lag Arbitrage"""

import logging
from config import BOT_A_BANKROLL, BOT_A_DB_PATH
from bots.base_bot import BaseBot
from signals.signal_a import BotASignal

logger = logging.getLogger("bot_a")


class BotA(BaseBot):

    BOT_ID            = "A"
    DB_PATH           = BOT_A_DB_PATH
    STARTING_BANKROLL = BOT_A_BANKROLL

    def __init__(self, binance, chainlink, poly):
        super().__init__(binance, chainlink, poly)
        self._signal = BotASignal()

    def evaluate_signal(self):
        return self._signal.evaluate(
            lag_signal    = self.chainlink.lag_signal,
            lag_sustained = self.chainlink.lag_sustained,
            lag_detected  = self.chainlink.lag_detected,
        )
