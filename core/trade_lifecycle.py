from enum import Enum


class TradeStatus(str, Enum):
    SIGNAL_RECEIVED = "signal_received"
    TRADE_PREPARED = "trade_prepared"
    TRADE_CLICKED = "trade_clicked"
    TRADE_OPENED = "trade_opened"
    TRADE_CLOSED = "trade_closed"
    RESULT_WIN = "result_win"
    RESULT_LOSS = "result_loss"
    RESULT_DRAW = "result_draw"
    ERROR = "error"
