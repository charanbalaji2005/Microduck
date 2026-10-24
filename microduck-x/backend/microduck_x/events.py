"""Structured events: one stream for logs, the dashboard's timeline, and experiment records."""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import time
from collections import deque
from typing import Any, Callable

log = logging.getLogger("microduck_x")

_LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warn": logging.WARNING, "error": logging.ERROR}


class EventBus:
    """Append-only ring of events, fanned out to async subscribers (the dashboard WebSocket)."""

    def __init__(self, keep: int = 2000):
        self._ring: deque[dict] = deque(maxlen=keep)
        self._ids = itertools.count(1)
        self._listeners: list[Callable[[dict], Any]] = []

    def emit(self, kind: str, message: str, level: str = "info", **data: Any) -> dict:
        event = dict(id=next(self._ids), t=time.time(), kind=kind, level=level, message=message, data=data)
        self._ring.append(event)
        log.log(_LEVELS.get(level, logging.INFO), "%s %s %s", kind, message,
                json.dumps(data, default=str) if data else "")
        for listener in list(self._listeners):
            try:
                result = listener(event)
                if asyncio.iscoroutine(result):
                    asyncio.ensure_future(result)
            except Exception:  # a broken listener must not break the emitter
                log.exception("event listener failed")
        return event

    def broadcast(self, kind: str, **data: Any) -> None:
        """A transient event (a reply streaming in): fanned out live, never kept or logged."""
        event = dict(id=0, t=time.time(), kind=kind, level="debug", message="", data=data, transient=True)
        for listener in list(self._listeners):
            try:
                result = listener(event)
                if asyncio.iscoroutine(result):
                    asyncio.ensure_future(result)
            except Exception:
                log.exception("event listener failed")

    def subscribe(self, listener: Callable[[dict], Any]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    def recent(self, limit: int = 200, kind: str | None = None, level: str | None = None) -> list[dict]:
        items = list(self._ring)
        if kind:
            items = [e for e in items if e["kind"].startswith(kind)]
        if level:
            floor = _LEVELS.get(level, 0)
            items = [e for e in items if _LEVELS.get(e["level"], 0) >= floor]
        return items[-limit:]


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level,
        format='{"t":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":%(message)r}',
    )
