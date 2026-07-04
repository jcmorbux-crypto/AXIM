from dataclasses import dataclass


@dataclass
class Signal:

    message_id: int

    channel: str

    sender: str

    asset: str

    direction: str

    timeframe: str

    payout: int

    trade_amount: float

    message: str

    received_at: str

    executed: bool = False

    execution_time: str = ""

    result: str = ""

    profit: float = 0.0