import inspect
from collections import defaultdict

from logger import get_logger

logger = get_logger("axim.lifecycle", filename="lifecycle.log")


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
