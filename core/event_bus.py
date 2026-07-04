import inspect
import logging
from collections import defaultdict
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"

logger = logging.getLogger("axim.lifecycle")
if not logger.handlers:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_DIR / "lifecycle.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.INFO)


class EventBus:
    def __init__(self):
        self._subscribers = defaultdict(list)

    def subscribe(self, event_name, callback):
        self._subscribers[event_name].append(callback)

    async def publish(self, event_name, payload=None):
        payload = payload or {}
        logger.info("EVENT event=%s payload=%s", event_name, payload)
        for callback in list(self._subscribers.get(event_name, [])):
            try:
                result = callback(payload)
                if inspect.isawaitable(result):
                    await result
            except Exception as e:
                logger.error("event_bus: subscriber for event=%s raised %s", event_name, e)


_default_bus = EventBus()


def get_event_bus():
    return _default_bus
